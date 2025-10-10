from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from stash_ai_server.actions.models import ContextInput

from .stash_handler import add_error_tag_to_images, get_ai_tag_ids_from_names, remove_ai_tags_from_images

from .http_handler import call_images_api

from .utils import extract_tags_from_response, get_selected_items
from .models import Scope
from stash_ai_server.services.base import RemoteServiceBase
from stash_ai_server.tasks.helpers import spawn_chunked_tasks, task_handler
from stash_ai_server.tasks.models import TaskPriority
from stash_ai_server.utils.stash_api_real import stash_api

_log = logging.getLogger(__name__)



_EXCLUDED_CHILD_PARAM_KEYS = {
    "hold",
    "hold_children",
    "holdChildren",
    "hold_parent_seconds",
    "holdParentSeconds",
    "chunk_size",
    "chunkSize",
}

# ==============================================================================
# Image tagging - batch endpoint that accepts multiple image paths
# ==============================================================================


async def tag_images(service: RemoteServiceBase, scope: Scope, ctx: ContextInput, params: dict) -> dict:
    """
    Tag images using batch /images endpoint.
    All images in the scope are sent in a single request.
    """
    targets = get_selected_items(scope, ctx)
    
    image_paths = stash_api.get_image_paths(targets)
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


# ==============================================================================
# Child task handler for scene batches
# ==============================================================================

def _get_service() -> Any:
    """Retrieve the skier AI tagging service from registry."""
    from stash_ai_server.services.registry import services

    service = services.get("skier.ai_tagging")
    if service is None:
        raise RuntimeError("Skier AI tagging service is not registered")
    return service


@task_handler(id="skier.ai_tag.scene.chunk", service="ai")
async def _run_scene_chunk(ctx: ContextInput, params: dict, task_record=None):
    """
    Process a single scene as a child task.
    The /scene endpoint only handles one scene at a time.
    """
    service = _get_service()
    return await tag_scene_single(service, ctx, params)


# ==============================================================================
# Batch orchestration for multiple scenes
# ==============================================================================

def _sanitize_chunk_size(raw_value: object) -> int:
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return 1
    return max(1, value)


def _child_params(params: dict) -> dict:
    return {k: v for k, v in params.items() if k not in _EXCLUDED_CHILD_PARAM_KEYS}


async def spawn_scene_batch(
    service: Any,
    ctx: ContextInput,
    params: dict,
    task_record,
) -> dict:
    """
    Spawn child tasks for each scene in the selection.
    Since /scene endpoint only handles one scene at a time, we must create
    individual child tasks for each scene.
    """
    # Determine scope based on context
    if ctx.is_detail_view:
        scope = "detail"
    elif ctx.selected_ids:
        scope = "selected"
    elif ctx.visible_ids:
        scope = "page"
    else:
        scope = "all"
    
    targets = get_selected_items(scope, ctx)
    
    if not targets:
        return {"message": "No scenes to process"}

    chunk_size = _sanitize_chunk_size(params.get("chunk_size") or params.get("chunkSize") or 1)
    child_priority = TaskPriority.from_str(params.get("child_priority") or params.get("priority"))
    child_params = _child_params(params)
    
    hold_value = params.get("hold_children")
    if hold_value is None:
        hold_value = params.get("holdChildren")
    if isinstance(hold_value, str):
        hold = hold_value.strip().lower() not in {"false", "0", "no", ""}
    elif hold_value is None:
        hold = True
    else:
        hold = bool(hold_value)

    result = await spawn_chunked_tasks(
        parent_task=task_record,
        parent_context=ctx,
        handler=_run_scene_chunk,
        items=targets,
        chunk_size=chunk_size,
        params=child_params,
        priority=child_priority,
        hold_children=hold,
    )

    return result
