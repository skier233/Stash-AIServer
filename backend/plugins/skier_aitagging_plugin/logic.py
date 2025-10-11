from __future__ import annotations
import logging
import time
from stash_ai_server.actions.models import ContextInput
from stash_ai_server.tasks.models import TaskRecord

from .models import AIModelInfo
from .stash_handler import add_error_tag_to_images, get_ai_tag_ids_from_names, is_vr_scene, remove_ai_tags_from_images
from .http_handler import call_images_api, call_scene_api, get_active_scene_models
from .utils import extract_tags_from_response, get_selected_items
from stash_ai_server.services.base import RemoteServiceBase
from stash_ai_server.tasks.helpers import spawn_chunked_tasks, task_handler
from stash_ai_server.tasks.models import TaskPriority
from stash_ai_server.utils.stash_api_real import stash_api

_log = logging.getLogger(__name__)

MAX_IMAGES_PER_REQUEST = 288

current_server_models_cache: list[AIModelInfo] = []

MODELS_CACHE_REFRESH_INTERVAL = 600  # seconds

next_cache_refresh_time = 0.0

async def update_model_cache(service: RemoteServiceBase) -> None:
    """Update the cache of models from the remote service."""
    global current_server_models_cache
    global next_cache_refresh_time
    try:
        current_server_models_cache = await get_active_scene_models(service)
        next_cache_refresh_time = time.monotonic() + MODELS_CACHE_REFRESH_INTERVAL
    except Exception as e:
        _log.error("Failed to update model cache: %s", e)

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
    scene_id = ctx.entity_id
    scene_path, scene_tags = stash_api.get_scene_path_and_tags(int(scene_id))

    service = params["service"]
    

    #TODO: Check if already tagged with same models logic

    #TODO: path mutation logic

    # TODO: Check what services don't need processing again (or possibly all)
    global next_cache_refresh_time
    if service.was_disconnected:
        await update_model_cache(service)
        service.was_disconnected = False
    elif time.monotonic() > next_cache_refresh_time:
        await update_model_cache(service)

    # Get models used for tagging this scene in the past if exist

    # Compare models used for tagging this scene in the past with current models and determine which categories need to be retagged if any

    # TODO: VR Scene handling
    vr_scene = is_vr_scene(scene_tags)
    result = await call_scene_api(service, scene_path, 2.0, vr_scene)
    _log.debug(f"Scene API result: {result}")

    # TODO
    return {
        "scene_id": None,
        "tags": [],
        "summary": "No scene available for this request",
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
