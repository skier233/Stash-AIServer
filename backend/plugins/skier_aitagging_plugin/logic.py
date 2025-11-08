from __future__ import annotations

import asyncio
import logging
import time
from typing import Sequence
from stash_ai_server.actions.models import ContextInput
from stash_ai_server.tasks.models import TaskRecord

from .models import AIModelInfo, TagTimeFrame
from .stash_handler import (
    add_error_tag_to_images,
    has_ai_tagged,
    is_vr_scene,
    resolve_ai_tag_reference,
)
from .http_handler import call_images_api, call_scene_api, get_active_scene_models
from .utils import (
    collect_image_tag_records,
    extract_tags_from_response,
    filter_enabled_tag_ids,
    get_selected_items,
)
from .reprocessing import determine_model_plan
from .marker_handling import apply_scene_markers
from .scene_tagging import apply_scene_tags
from stash_ai_server.services.base import RemoteServiceBase
from stash_ai_server.tasks.helpers import spawn_chunked_tasks, task_handler
from stash_ai_server.tasks.models import TaskPriority, TaskStatus
from stash_ai_server.tasks.manager import manager as task_manager
from stash_ai_server.utils.stash_api import stash_api
from stash_ai_server.utils.path_mutation import mutate_path_for_plugin
from .legacy_ai_video_result import LegacyAIVideoResult
from .tag_config import get_tag_configuration, resolve_backend_to_stash_tag_id
from stash_ai_server.db.ai_results_store import (
    get_image_model_history_async,
    get_image_tag_ids_async,
    get_scene_model_history_async,
    store_image_run_async,
    store_scene_run_async,
    purge_scene_categories,
)

_log = logging.getLogger(__name__)

MAX_IMAGES_PER_REQUEST = 288

SCENE_FRAME_INTERVAL = 2.0
SCENE_THRESHOLD = 0.5

current_server_models_cache: list[AIModelInfo] = []

MODELS_CACHE_REFRESH_INTERVAL = 600  # seconds

next_cache_refresh_time = 0.0


def _short_error(message: str, *, limit: int = 120) -> str:
    return message if len(message) <= limit else message[: limit - 3] + "..."


def _format_scene_message(scene_id: int, applied: int, removed: int, markers: int) -> str:
    return (
        f"Scene #{scene_id}: applied {applied} tag(s), "
        f"removed {removed} tag(s), added {markers} marker span(s)."
    )


def _format_multi_summary(kind: str, success: int, failed: int) -> str:
    if failed == 0:
        return f"Processed {success} {kind} successfully."
    return (
        f"{kind.capitalize()} processing finished: {success} succeeded, "
        f"{failed} failed. See AI Tasks for details."
    )


async def _apply_scene_markers_and_tags(
    *,
    scene_id: int,
    service_name: str,
    scene_duration: float,
    existing_scene_tag_ids: Sequence[int] | None,
):
    """Reload stored markers and tags for a scene and provide basic counts."""

    markers_by_tag = await apply_scene_markers(
        scene_id=scene_id,
        service_name=service_name,
    )
    tag_changes = await apply_scene_tags(
        scene_id=scene_id,
        service_name=service_name,
        scene_duration=scene_duration,
        existing_scene_tag_ids=existing_scene_tag_ids,
    )
    marker_count = sum(len(spans) for spans in markers_by_tag.values())
    applied_tags = len(tag_changes.get("applied", []))
    removed_tags = len(tag_changes.get("removed", []))
    return markers_by_tag, tag_changes, marker_count, applied_tags, removed_tags


async def update_model_cache(service: RemoteServiceBase, *, force: bool = False) -> None:
    """Update the cache of models from the remote service."""
    global current_server_models_cache
    global next_cache_refresh_time

    if service.was_disconnected:
        force = True
    now = time.monotonic()
    if not force and now < next_cache_refresh_time:
        return
    try:
        models = await get_active_scene_models(service)
        current_server_models_cache = models
        next_cache_refresh_time = now + MODELS_CACHE_REFRESH_INTERVAL
    except Exception as exc:
        _log.error("Failed to update model cache: %s", exc)

# ==============================================================================
# Image tagging - batch endpoint that accepts multiple image paths
# ==============================================================================


@task_handler(id="skier.ai_tag.image.task")
async def tag_images_task(ctx: ContextInput, params: dict) -> dict:
    """
    Tag images using batch /images endpoint.
    """
    raw_image_ids = get_selected_items(ctx)
    service: RemoteServiceBase = params["service"]

    image_ids: list[int] = []
    for raw in raw_image_ids:
        try:
            image_ids.append(int(raw))
        except (TypeError, ValueError):
            _log.warning("Skipping invalid image id: %s", raw)

    if not image_ids:
        return {
            "status": "noop",
            "message": "No images to process.",
            "processed_ids": [],
            "failed_ids": [],
            "skipped_ids": [],
            "tags_added": {},
        }

    try:
        image_paths = await asyncio.to_thread(stash_api.get_image_paths, image_ids)
    except Exception as exc:
        _log.exception("Failed to fetch image paths for ids=%s", image_ids)
        detail = _short_error(str(exc) or exc.__class__.__name__)
        return {
            "status": "failed",
            "message": f"Image tagging failed while fetching paths ({detail}).",
            "processed_ids": image_ids,
            "failed_ids": image_ids,
            "skipped_ids": [],
            "tags_added": {},
        }

    failure_reasons: dict[int, str] = {}
    failed_images: set[int] = set()
    skipped_images: set[int] = set()

    valid_paths: dict[int, str] = {}
    for image_id in image_ids:
        path = (image_paths or {}).get(image_id)
        if not path:
            failure_reasons[image_id] = "file path unavailable"
            failed_images.add(image_id)
            continue
        valid_paths[image_id] = path

    if not valid_paths:
        return {
            "status": "failed",
            "message": "No valid images to process.",
            "processed_ids": image_ids,
            "failed_ids": image_ids,
            "skipped_ids": [],
            "tags_added": {},
        }

    await update_model_cache(service)

    config = get_tag_configuration()
    active_models = list(current_server_models_cache)
    requested_models_payload = [model.model_dump(exclude_none=True) for model in active_models]

    remote_targets: dict[int, str] = {}
    determine_errors: list[int] = []

    for image_id, path in valid_paths.items():
        try:
            historical_models = await get_image_model_history_async(service=service.name, image_id=image_id)
        except Exception:
            _log.exception("Failed to load image model history for image_id=%s", image_id)
            historical_models = ()
            determine_errors.append(image_id)

        _, should_reprocess = determine_model_plan(
            current_models=active_models,
            previous_models=historical_models,
            current_frame_interval=SCENE_FRAME_INTERVAL,
            current_threshold=SCENE_THRESHOLD,
        )

        if should_reprocess:
            remote_targets[image_id] = mutate_path_for_plugin(path, service.plugin_name)
        else:
            skipped_images.add(image_id)

    if remote_targets:
        remote_image_ids = list(remote_targets.keys())
        remote_paths = [remote_targets[iid] for iid in remote_image_ids]
        try:
            response = await call_images_api(service, remote_paths)
            _log.debug("Images API metrics: %s", getattr(response, "metrics", None))
        except Exception:
            _log.exception("Remote image tagging failed for %d images", len(remote_image_ids))
            await asyncio.to_thread(add_error_tag_to_images, remote_image_ids)
            for iid in remote_image_ids:
                failure_reasons[iid] = "remote service request failed"
            failed_images.update(remote_image_ids)
            response = None

        if response is not None:
            result_payload = response.result if isinstance(response.result, list) else []
            models_used = response.models if getattr(response, "models", None) else []
            for idx, image_id in enumerate(remote_image_ids):
                payload = result_payload[idx] if idx < len(result_payload) else {}
                if isinstance(payload, dict) and payload.get("error"):
                    failure_reasons[image_id] = _short_error(str(payload.get("error")))
                    await asyncio.to_thread(add_error_tag_to_images, [image_id])
                    failed_images.add(image_id)
                    continue

                tags_by_category = extract_tags_from_response(payload if isinstance(payload, dict) else {})
                resolved_records = collect_image_tag_records(tags_by_category, config)
                try:
                    await store_image_run_async(
                        service=service.name,
                        plugin_name=service.plugin_name,
                        image_id=image_id,
                        tag_records=resolved_records,
                        input_params=None,
                        requested_models=models_used if models_used else requested_models_payload,
                    )
                except Exception:
                    _log.exception("Failed to persist image tagging run for image_id=%s", image_id)

    tags_added_counts: dict[int, int] = {}

    for image_id in image_ids:
        tags_added_counts[image_id] = 0
        if image_id in failed_images:
            continue
        try:
            stored_tag_ids = await get_image_tag_ids_async(service=service.name, image_id=image_id)
        except Exception:
            _log.exception("Failed to load stored image tags for image_id=%s", image_id)
            failure_reasons[image_id] = "failed to load stored tags"
            failed_images.add(image_id)
            continue

        normalized_ids = filter_enabled_tag_ids(stored_tag_ids, config)
        tags_added_counts[image_id] = len(normalized_ids)

        if not normalized_ids and not stored_tag_ids:
            continue

        try:
            if stored_tag_ids:
                await asyncio.to_thread(stash_api.remove_tags_from_images, [image_id], stored_tag_ids)
            if normalized_ids:
                await asyncio.to_thread(stash_api.add_tags_to_images, [image_id], normalized_ids)
        except Exception:
            _log.exception("Failed to refresh tags for image_id=%s", image_id)
            failure_reasons[image_id] = "failed to sync tags with Stash"
            failed_images.add(image_id)

    processed_ids = list(dict.fromkeys(image_ids))
    failed_ids = sorted(failed_images)
    skipped_ids = sorted(skipped_images)
    success_count = len(processed_ids) - len(failed_ids)

    status = "success"
    if failed_ids:
        status = "failed" if success_count == 0 else "partial"

    if len(processed_ids) == 1:
        image_id = processed_ids[0]
        if image_id in failed_images:
            reason = failure_reasons.get(image_id, "unknown error")
            message = f"Image #{image_id}: tagging failed ({reason})."
        else:
            added = tags_added_counts.get(image_id, 0)
            message = f"Image #{image_id}: added {added} tag(s)."
    else:
        message = _format_multi_summary("images", success_count, len(failed_ids))

    return {
        "status": status,
        "message": message,
        "processed_ids": processed_ids,
        "failed_ids": failed_ids,
        "skipped_ids": skipped_ids,
        "tags_added": tags_added_counts,
        "failure_reasons": {iid: failure_reasons[iid] for iid in failed_ids},
    }


# ==============================================================================
# Scene tagging
# ==============================================================================


@task_handler(id="skier.ai_tag.scene.task")
async def tag_scene_task(ctx: ContextInput, params: dict, task_record: TaskRecord) -> dict:
    scene_id_raw = ctx.entity_id
    _log.debug("ASYNC debug scene id: %s", scene_id_raw)
    if scene_id_raw is None:
        raise ValueError("Context missing scene entity_id. ctx: %s" % ctx)
    try:
        scene_id = int(scene_id_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid scene_id: {scene_id_raw}") from exc

    service = params["service"]
    try:
        scene_path, scene_tags, scene_duration = await asyncio.to_thread(
            stash_api.get_scene_path_and_tags_and_duration,
            scene_id,
        )
    except Exception as exc:
        _log.exception("Failed to load scene metadata for scene_id=%s", scene_id)
        detail = _short_error(str(exc) or exc.__class__.__name__)
        return {
            "scene_id": scene_id,
            "status": "failed",
            "message": f"Scene #{scene_id}: tagging failed ({detail}).",
            "processed_ids": [scene_id],
            "failed_ids": [scene_id],
        }

    remote_scene_path = mutate_path_for_plugin(scene_path or "", service.plugin_name)

    try:
        historical_models = await get_scene_model_history_async(service=service.name, scene_id=scene_id)
    except Exception:
        _log.exception("Failed to load scene model history for scene_id=%s", scene_id)
        historical_models = ()

    if has_ai_tagged(scene_tags) and not historical_models:
        legacy_result = await LegacyAIVideoResult.try_load_from_scene_path(scene_path)
        if legacy_result is None:
            _log.info("No legacy AI json found for scene_id=%s", scene_id)
        else:
            imported = await legacy_result.save_to_db(scene_id=scene_id, service=service)
            if imported:
                historical_models = await get_scene_model_history_async(service=service.name, scene_id=scene_id)

    if historical_models:
        _log.debug(
            "Scene %s historical models: %s",
            scene_id,
            [m.model_name for m in historical_models],
        )

    await update_model_cache(service)

    skip_categories, should_reprocess = determine_model_plan(
        current_models=current_server_models_cache,
        previous_models=historical_models,
        current_frame_interval=SCENE_FRAME_INTERVAL,
        current_threshold=SCENE_THRESHOLD,
    )

    try:
        if not should_reprocess:
            _log.info("Skipping remote tagging for scene_id=%s; existing data considered current", scene_id)
            (
                markers_by_tag,
                tag_changes,
                marker_count,
                applied_tags,
                removed_tags,
            ) = await _apply_scene_markers_and_tags(
                scene_id=scene_id,
                service_name=service.name,
                scene_duration=scene_duration,
                existing_scene_tag_ids=scene_tags,
            )
            message = _format_scene_message(scene_id, applied_tags, removed_tags, marker_count)
            summary_parts = [f"Retrieved {marker_count} marker span(s) from storage"]
            if applied_tags:
                summary_parts.append(f"applied {applied_tags} scene tag(s)")
            if removed_tags:
                summary_parts.append(f"removed {removed_tags} scene tag(s)")
            return {
                "scene_id": scene_id,
                "status": "success",
                "message": message,
                "scene_tags": tag_changes,
                "summary": "; ".join(summary_parts),
                "markers_applied": marker_count,
                "tags_applied": applied_tags,
                "tags_removed": removed_tags,
                "processed_ids": [scene_id],
                "failed_ids": [],
            }

        vr_scene = is_vr_scene(scene_tags)
        _log.debug(
            "Running scene tagging for scene_id=%s; skipping categories=%s",
            scene_id,
            skip_categories,
        )
        response = await call_scene_api(
            service,
            remote_scene_path,
            SCENE_FRAME_INTERVAL,
            vr_scene,
            threshold=SCENE_THRESHOLD,
            skip_categories=skip_categories,
        )
        if response is not None:
            _log.debug("Scene API metrics: %s", response.metrics)

        if response is None or response.result is None:
            _log.warning("Remote scene tagging returned no data for scene_id=%s", scene_id)
            (
                markers_by_tag,
                tag_changes,
                marker_count,
                applied_tags,
                removed_tags,
            ) = await _apply_scene_markers_and_tags(
                scene_id=scene_id,
                service_name=service.name,
                scene_duration=scene_duration,
                existing_scene_tag_ids=scene_tags,
            )
            message = (
                f"Scene #{scene_id}: remote service returned no data. "
                f"Applied {applied_tags} tag(s), removed {removed_tags}, added {marker_count} marker span(s) from storage."
            )
            summary_parts = ["Remote tagging service returned no data"]
            if marker_count:
                summary_parts.append(f"reapplied {marker_count} marker span(s) from storage")
            if applied_tags:
                summary_parts.append(f"applied {applied_tags} scene tag(s)")
            if removed_tags:
                summary_parts.append(f"removed {removed_tags} scene tag(s)")
            return {
                "scene_id": scene_id,
                "status": "failed",
                "message": message,
                "scene_tags": tag_changes,
                "summary": "; ".join(summary_parts),
                "markers_applied": marker_count,
                "tags_applied": applied_tags,
                "tags_removed": removed_tags,
                "processed_ids": [scene_id],
                "failed_ids": [scene_id],
            }

        result = response.result
        processed_categories = {
            str(category)
            for category in (result.timespans.keys() if result.timespans else [])
            if category is not None
        }

        result_models_payload = [model.model_dump(exclude_none=True) for model in result.models]

        missing_from_cache = [m for m in result.models if m not in current_server_models_cache]
        if missing_from_cache:
            _log.debug(
                "Discovered %d models not present in cache; triggering refresh", len(missing_from_cache)
            )
            await update_model_cache(service, force=True)

        run_id: int | None = None
        try:
            tag_config = get_tag_configuration()

            run_id = await store_scene_run_async(
                service=service.name,
                plugin_name=service.plugin_name,
                scene_id=scene_id,
                input_params={
                    "frame_interval": SCENE_FRAME_INTERVAL,
                    "vr_video": vr_scene,
                    "threshold": SCENE_THRESHOLD,
                },
                result_payload=result.model_dump(exclude_none=True),
                requested_models=result_models_payload,
                resolve_reference=lambda backend_label, category: resolve_backend_to_stash_tag_id(backend_label, tag_config, category),
            )
        except Exception:
            _log.exception("Failed to persist AI scene run for scene_id=%s", scene_id)

        if run_id is not None and processed_categories:
            purge_scene_categories(
                service=service.name,
                scene_id=scene_id,
                categories=processed_categories,
                exclude_run_id=run_id,
            )

        (
            markers_by_tag,
            tag_changes,
            marker_count,
            applied_tags,
            removed_tags,
        ) = await _apply_scene_markers_and_tags(
            scene_id=scene_id,
            service_name=service.name,
            scene_duration=scene_duration,
            existing_scene_tag_ids=scene_tags,
        )
        message = _format_scene_message(scene_id, applied_tags, removed_tags, marker_count)
        summary_parts = [f"Processed scene with {marker_count} marker span(s)"]
        if applied_tags:
            summary_parts.append(f"applied {applied_tags} scene tag(s)")
        if removed_tags:
            summary_parts.append(f"removed {removed_tags} scene tag(s)")

        return {
            "scene_id": scene_id,
            "status": "success",
            "message": message,
            "scene_tags": tag_changes,
            "summary": "; ".join(summary_parts),
            "markers_applied": marker_count,
            "tags_applied": applied_tags,
            "tags_removed": removed_tags,
            "processed_ids": [scene_id],
            "failed_ids": [],
        }
    except Exception as exc:
        _log.exception("Scene tagging failed for scene_id=%s", scene_id)
        detail = _short_error(str(exc) or exc.__class__.__name__)
        return {
            "scene_id": scene_id,
            "status": "failed",
            "message": f"Scene #{scene_id}: tagging failed ({detail}).",
            "processed_ids": [scene_id],
            "failed_ids": [scene_id],
        }


async def tag_scenes(service: RemoteServiceBase, ctx: ContextInput, params: dict, task_record: TaskRecord):
    selected_items = get_selected_items(ctx)
    params["service"] = service
    if not selected_items:
        return {
            "status": "noop",
            "message": "No scenes to process.",
            "scenes_requested": 0,
            "scenes_completed": 0,
            "scenes_failed": 0,
        }
    if len(selected_items) == 1:
        if not ctx.entity_id:
            ctx.entity_id = str(selected_items[0])
        result = await tag_scene_task(ctx, params, task_record)
        return result

    task_priority = TaskPriority.low
    if ctx.is_detail_view:
        task_priority = TaskPriority.high
    elif ctx.selected_ids and len(ctx.selected_ids) >= 1:
        task_priority = TaskPriority.normal
    elif ctx.visible_ids and len(ctx.visible_ids) >= 1:
        task_priority = TaskPriority.normal

    spawn_result = await spawn_chunked_tasks(
        parent_task=task_record,
        parent_context=ctx,
        handler=tag_scene_task,
        items=selected_items,
        chunk_size=1,
        params=params,
        priority=task_priority,
        hold_children=True,
    )
    child_ids = spawn_result.get("spawned", [])
    success = 0
    failed = 0
    for child_id in child_ids:
        child = task_manager.get(child_id)
        if child is None:
            continue
        if child.status == TaskStatus.failed:
            failed += 1
            continue
        child_result = getattr(child, "result", None)
        if isinstance(child_result, dict) and child_result.get("status") == "failed":
            failed += 1
        else:
            success += 1

    total_requested = len(selected_items)
    accounted = success + failed
    if accounted < len(child_ids):
        failed += len(child_ids) - accounted
        accounted = success + failed
    if accounted < total_requested:
        # Treat any unaccounted requested scenes as failures to be safe.
        failed += total_requested - accounted

    failed = min(failed, total_requested)
    success = max(total_requested - failed, 0)

    status = "success"
    if failed:
        status = "failed" if success == 0 else "partial"
    message = _format_multi_summary("scenes", success, failed)

    return {
        "status": status,
        "message": message,
        "scenes_requested": total_requested,
        "scenes_completed": success,
        "scenes_failed": failed,
        "spawned": child_ids,
        "count": len(child_ids),
        "held": spawn_result.get("held", False),
    }

async def tag_images(service: RemoteServiceBase, ctx: ContextInput, params: dict, task_record: TaskRecord):
    selected_items = get_selected_items(ctx)
    params["service"] = service
    if not selected_items:
        return {
            "status": "noop",
            "message": "No images to process.",
            "images_requested": 0,
            "images_completed": 0,
            "images_failed": 0,
        }
    if len(selected_items) <= MAX_IMAGES_PER_REQUEST:
        return await tag_images_task(ctx, params)

    task_priority = TaskPriority.low
    if ctx.is_detail_view:
        task_priority = TaskPriority.high
    elif ctx.selected_ids and len(ctx.selected_ids) >= 1:
        task_priority = TaskPriority.normal
    elif ctx.visible_ids and len(ctx.visible_ids) >= 1:
        task_priority = TaskPriority.normal

    spawn_result = await spawn_chunked_tasks(
        parent_task=task_record,
        parent_context=ctx,
        handler=tag_images_task,
        items=selected_items,
        chunk_size=MAX_IMAGES_PER_REQUEST,
        params=params,
        priority=task_priority,
        hold_children=True,
    )
    child_ids = spawn_result.get("spawned", [])
    total_requested = len(selected_items)
    success = 0
    failed = 0

    for child_id in child_ids:
        child = task_manager.get(child_id)
        if child is None:
            continue

        chunk_total = 0
        try:
            if child.context.selected_ids:
                chunk_total = len(child.context.selected_ids)
            elif child.context.entity_id:
                chunk_total = 1
        except Exception:
            chunk_total = 0

        if child.status == TaskStatus.failed:
            failed += chunk_total or 1
            continue

        child_result = getattr(child, "result", None)
        if isinstance(child_result, dict):
            processed = child_result.get("processed_ids") or []
            failed_ids = child_result.get("failed_ids") or []
            success += max(len(processed) - len(failed_ids), 0)
            failed += len(failed_ids)
        else:
            failed += chunk_total or 1

    failed = min(failed, total_requested)
    success = max(total_requested - failed, 0)

    status = "success"
    if failed:
        status = "failed" if success == 0 else "partial"
    message = _format_multi_summary("images", success, failed)

    return {
        "status": status,
        "message": message,
        "images_requested": total_requested,
        "images_completed": success,
        "images_failed": failed,
        "spawned": child_ids,
        "count": len(child_ids),
        "held": spawn_result.get("held", False),
    }
