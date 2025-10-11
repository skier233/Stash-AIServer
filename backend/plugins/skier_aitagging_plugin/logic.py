from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from stash_ai_server.actions.models import ContextInput

from stash_ai_server.tasks.models import TaskRecord

from .stash_handler import add_error_tag_to_images, get_ai_tag_ids_from_names, remove_ai_tags_from_images

from .http_handler import call_images_api

from .utils import extract_tags_from_response, get_selected_items
from stash_ai_server.services.base import RemoteServiceBase
from stash_ai_server.tasks.helpers import spawn_chunked_tasks, task_handler
from stash_ai_server.tasks.models import TaskPriority
from stash_ai_server.utils.stash_api_real import stash_api
from stash_ai_server.services.registry import services

_log = logging.getLogger(__name__)

MAX_IMAGES_PER_REQUEST = 288

# ==============================================================================
# Image tagging - batch endpoint that accepts multiple image paths
# ==============================================================================


@task_handler(id="skier.ai_tag.image.task")
async def tag_images_task(ctx: ContextInput, params: dict, taskRecord: TaskRecord) -> dict:
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

    result = None
    try:
        result = await call_images_api(service, list(image_paths.values()), params)
        if result is None:
            return_status = "Failed"
        else:
            result = result.result
            _log.warning("Remote API response: %s", result)

            remove_ai_tags_from_images(list(image_paths.keys()))

            for image, id in zip(result, image_paths.keys()):
                if 'error' in image:
                    return_status = "Partial Success"
                    add_error_tag_to_images([id])
                    continue
                tags_list = extract_tags_from_response(image)
                tag_ids = get_ai_tag_ids_from_names(tags_list)
                stash_api.add_tags_to_images([id], tag_ids)
    
    except Exception:
        return_status = "Failed"
        _log.error("Failed to call images API", exc_info=True)

    return "dummy", return_status


async def tag_images():
    pass

# ==============================================================================
# Scene tagging - single scene endpoint, must spawn child tasks for multiple
# ==============================================================================

# TODO: make scenes api work
# async def _call_scene_api(service: RemoteServiceBase, scene_id: str, params: dict) -> SceneTaggingResponse | None:
#     """Call the /scene endpoint for a single scene."""   
#     try:
#         payload = {
#             "scene_id": scene_id,
#             "params": params or {},
#         }
#         return await service.http.post(
#             SCENE_ENDPOINT,
#             json=payload,
#             response_model=SceneTaggingResponse,
#         )
#     except asyncio.CancelledError:  # pragma: no cover
#         raise
#     except Exception as exc:  # noqa: BLE001
#         _log.warning("scene API call failed for scene_id=%s: %s", scene_id, exc)
#         return None


async def tag_scene_single(service: RemoteServiceBase, ctx: ContextInput, params: dict) -> dict:
    """
    Tag a single scene using the /scene endpoint.
    Used for detail view or when only one scene is selected.
    """
    return {
        "scene_id": None,
        "tags": [],
        "summary": "No scene available for this request",
    }

async def tag_scene_all(service: Any, ctx: ContextInput, params: dict) -> dict:
    """Handle 'all scenes' request - just acknowledge, don't process."""
    return {
        "scene_id": None,
        "tags": [],
        "summary": "No scene available for this request",
    }

@task_handler(id="skier.ai_tag.scene.task")
async def tag_scene_task(ctx: ContextInput, params: dict, task_record: TaskRecord) -> dict:
    service = services.get(task_record.service)

    # TODO
    return {
        "scene_id": None,
        "tags": [],
        "summary": "No scene available for this request",
    }


async def tag_scenes(service: RemoteServiceBase, ctx: ContextInput, params: dict, task_record: TaskRecord):
    selected_items = get_selected_items(ctx)
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
        await tag_images_task(ctx, params, task_record)
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
