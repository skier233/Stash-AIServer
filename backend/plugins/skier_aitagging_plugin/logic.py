from __future__ import annotations

import logging
import time
from typing import Any
from stash_ai_server.actions.models import ContextInput
from stash_ai_server.tasks.models import TaskRecord

from .models import AIModelInfo
from .stash_handler import (
    add_error_tag_to_images,
    get_ai_tag_ids_from_names,
    is_vr_scene,
    remove_ai_tags_from_images,
    resolve_ai_tag_reference,
)
from .http_handler import call_images_api, call_scene_api, get_active_scene_models
from .utils import extract_tags_from_response, get_selected_items
from .reprocessing import determine_model_plan
from .marker_handling import apply_scene_markers
from stash_ai_server.services.base import RemoteServiceBase
from stash_ai_server.tasks.helpers import spawn_chunked_tasks, task_handler
from stash_ai_server.tasks.models import TaskPriority
from stash_ai_server.utils.stash_api_real import stash_api
from .tag_config import get_tag_configuration
from stash_ai_server.db.ai_results_store import (
    get_latest_scene_run_async,
    get_scene_model_history_async,
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
    image_to_process = get_selected_items(ctx)
    service = params["service"]

    image_paths = stash_api.get_image_paths(image_to_process)
    # Try remote API first

    return_status = "Success"
    #TODO actually use id: path and remove any images with no paths
    images_without_paths = [(tid, path) for tid, path in image_paths.items() if path is None]
    if images_without_paths:
        _log.warning(f"Some images have no paths: {images_without_paths}")
        return_status = "Partial Success"
        # Remove images without paths from the request
        for tid, _ in images_without_paths:
            image_paths.pop(tid, None)
            # TODO: do something with these images

    if not image_paths:
        return {"message": "No valid images to process"}

    result = None
    try:
        result = await call_images_api(service, list(image_paths.values()))
        if result is None:
            return_status = "Failed"
        else:
            result = result.result
            _log.warning("Remote API response: %s", result)

            remove_ai_tags_from_images(list(image_paths.keys()))

            saw_success = False
            for image, id in zip(result, image_paths.keys()):
                if 'error' in image:
                    return_status = "Partial Success"
                    add_error_tag_to_images([id])
                    continue
                saw_success = True
                tags_list = extract_tags_from_response(image)
                tag_ids = get_ai_tag_ids_from_names(tags_list)
                stash_api.add_tags_to_images([id], tag_ids)

            if not saw_success:
                return_status = "Failed"
    
    except Exception:
        return_status = "Failed"
        _log.error("Failed to call images API", exc_info=True)

    return "dummy", return_status

# ==============================================================================
# Scene tagging
# ==============================================================================


@task_handler(id="skier.ai_tag.scene.task")
async def tag_scene_task(ctx: ContextInput, params: dict, task_record: TaskRecord) -> dict:
    scene_id_raw = ctx.entity_id
    if scene_id_raw is None:
        raise ValueError("Context missing scene entity_id")
    try:
        scene_id = int(scene_id_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid scene_id: {scene_id_raw}") from exc

    scene_path, scene_tags = stash_api.get_scene_path_and_tags(scene_id)

    service = params["service"]

    # Retrieve the latest stored run so we can determine future skip conditions.
    historical_models = await get_scene_model_history_async(service=service.name, scene_id=scene_id)
    if historical_models:
        _log.debug(
            "Scene %s historical models: %s",
            scene_id,
            [m.model_name for m in historical_models],
        )

    #TODO: path mutation logic

    await update_model_cache(service)

    skip_categories, should_reprocess = determine_model_plan(
        current_models=current_server_models_cache,
        previous_models=historical_models,
        current_frame_interval=SCENE_FRAME_INTERVAL,
        current_threshold=SCENE_THRESHOLD,
    )

    if not should_reprocess:
        _log.info("Skipping remote tagging for scene_id=%s; existing data considered current", scene_id)
        markers_by_tag = await apply_scene_markers(
            scene_id=scene_id,
            service_name=service.name,
        )
        return {
            "scene_id": scene_id,
            "markers": markers_by_tag,
            "summary": f"Retrieved {sum(len(spans) for spans in markers_by_tag.values())} markers from storage",
        }

    # TODO: VR Scene handling
    vr_scene = is_vr_scene(scene_tags)
    _log.debug(
        "Running scene tagging for scene_id=%s; skipping categories=%s",
        scene_id,
        skip_categories,
    )
    result = await call_scene_api(
        service,
        scene_path,
        SCENE_FRAME_INTERVAL,
        vr_scene,
        threshold=SCENE_THRESHOLD,
        skip_categories=skip_categories,
    )

    _log.debug("Scene API result: %s", result)

    if result is None:
        return {
            "scene_id": scene_id,
            "tags": [],
            "summary": "Remote tagging service returned no data",
        }

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
        # Create a resolve function that uses tag config to map backend tags to stash tags
        
        tag_config = get_tag_configuration()
        
        def resolve_backend_to_stash_tag_id(backend_label: str, category: str | None) -> int | None:
            """Resolve backend tag name to Stash tag ID using configuration."""
            settings = tag_config.resolve(backend_label)
            stash_name = settings.stash_name or backend_label
            if not stash_name:
                return None
            return resolve_ai_tag_reference(stash_name)
        
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
            resolve_reference=resolve_backend_to_stash_tag_id,
        )
    except Exception:
        _log.exception("Failed to persist AI scene run for scene_id=%s", scene_id)

    #TODO: this logic is kinda sketchy
    if run_id is not None and processed_categories:
        purge_scene_categories(
            service=service.name,
            scene_id=scene_id,
            categories=processed_categories,
            exclude_run_id=run_id,
        )

    markers_by_tag = await apply_scene_markers(
        scene_id=scene_id,
        service_name=service.name,
    )
    
    return {
        "scene_id": scene_id,
        "markers": markers_by_tag,
        "summary": f"Processed scene with {sum(len(spans) for spans in markers_by_tag.values())} markers",
    }


async def tag_scenes(service: RemoteServiceBase, ctx: ContextInput, params: dict, task_record: TaskRecord):
    selected_items = get_selected_items(ctx)
    params["service"] = service
    if not selected_items:
        # TODO: standardize empty responses
        return {"message": "No scenes to process"}
    if len(selected_items) == 1:
        await tag_scene_task(ctx, params, task_record)
        # TODO: Standardize output
        return {"message": "Single scene processed directly"}

    task_priority = TaskPriority.low
    if ctx.is_detail_view:
        task_priority = TaskPriority.high
    elif ctx.selected_ids and len(ctx.selected_ids) >= 1:
        task_priority = TaskPriority.normal
    elif ctx.visible_ids and len(ctx.visible_ids) >= 1:
        task_priority = TaskPriority.normal

    result = await spawn_chunked_tasks(
        parent_task=task_record,
        parent_context=ctx,
        handler=tag_scene_task,
        items=selected_items,
        chunk_size=1,
        params=params,
        priority=task_priority,
        hold_children=True,
    )

    return result

async def tag_images(service: RemoteServiceBase, ctx: ContextInput, params: dict, task_record: TaskRecord):
    selected_items = get_selected_items(ctx)
    params["service"] = service
    if not selected_items:
        # TODO: standardize empty responses
        return {"message": "No images to process"}
    if len(selected_items) <= MAX_IMAGES_PER_REQUEST:
        await tag_images_task(ctx, params)
        # TODO: Standardize output
        return {"message": "Single scene processed directly"}

    task_priority = TaskPriority.low
    if ctx.is_detail_view:
        task_priority = TaskPriority.high
    elif ctx.selected_ids and len(ctx.selected_ids) >= 1:
        task_priority = TaskPriority.normal
    elif ctx.visible_ids and len(ctx.visible_ids) >= 1:
        task_priority = TaskPriority.normal

    result = await spawn_chunked_tasks(
        parent_task=task_record,
        parent_context=ctx,
        handler=tag_images_task,
        items=selected_items,
        chunk_size=MAX_IMAGES_PER_REQUEST,
        params=params,
        priority=task_priority,
        hold_children=True,
    )

    return result
