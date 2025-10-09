from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from stash_ai_server.actions.models import ContextInput
from backend.stash_ai_server.services.base import RemoteServiceBase
from stash_ai_server.tasks.helpers import spawn_chunked_tasks, task_handler
from stash_ai_server.tasks.models import TaskPriority

_log = logging.getLogger(__name__)

# Remote API endpoints
IMAGES_ENDPOINT = "/images"  # Batch endpoint - accepts multiple image paths
SCENE_ENDPOINT = "/scene"    # Single scene endpoint - processes one scene at a time

Scope = Literal["detail", "selected", "page", "all"]

# Response models
class ImageTaggingResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    
    images: list[dict[str, Any]] | None = None
    summary: str | None = None


class SceneTaggingResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    
    scene_id: str | None = None
    tags: list[dict[str, Any]] | None = None
    summary: str | None = None


# Fallback stub data

_IMAGE_TAGS = [
    {"name": "outdoor", "confidence": 0.92},
    {"name": "portrait", "confidence": 0.81},
    {"name": "studio-light", "confidence": 0.77},
]

_SCENE_TAGS = [
    {"name": "hard-light", "confidence": 0.74},
    {"name": "dialogue-heavy", "confidence": 0.63},
    {"name": "dynamic-camera", "confidence": 0.58},
]

_PRIORITY_MAP = {
    "high": TaskPriority.high,
    "normal": TaskPriority.normal,
    "low": TaskPriority.low,
}

_EXCLUDED_CHILD_PARAM_KEYS = {
    "hold",
    "hold_children",
    "holdChildren",
    "hold_parent_seconds",
    "holdParentSeconds",
    "chunk_size",
    "chunkSize",
}


def _collect_targets(scope: Scope, ctx: ContextInput) -> list[str]:
    """Collect target IDs based on scope."""
    selected = list(ctx.selected_ids or [])
    visible = list(ctx.visible_ids or [])
    entity = [ctx.entity_id] if ctx.entity_id else []

    if scope == "detail":
        return entity or selected
    if scope == "selected":
        return selected or entity
    if scope == "page":
        return visible
    if scope == "all":
        return []
    raise ValueError(f"Unknown scope '{scope}'")


async def _simulate_latency(entity_count: int):
    """Simulate processing delay for stub responses."""
    delay = min(0.05 * max(entity_count, 1), 0.4)
    await asyncio.sleep(delay)


# ==============================================================================
# Image tagging - batch endpoint that accepts multiple image paths
# ==============================================================================

async def _call_images_api(service: Any, image_paths: list[str], params: dict) -> ImageTaggingResponse | None:
    """Call the /images endpoint with a batch of image paths."""
    if not service.server_url:
        return None
    
    try:
        payload = {
            "image_paths": image_paths,
            "params": params or {},
        }
        return await service.http.post(
            IMAGES_ENDPOINT,
            json=payload,
            response_model=ImageTaggingResponse,
        )
    except asyncio.CancelledError:  # pragma: no cover
        raise
    except Exception as exc:  # noqa: BLE001
        _log.warning("images API call failed: %s", exc)
        return None


def _stub_image_results(image_ids: list[str]) -> dict:
    """Generate stub results for images when API is unavailable."""
    return {
        "images": [
            {
                "image_id": iid,
                "suggested_tags": _IMAGE_TAGS,
                "notes": "Mock image tagging data",
            }
            for iid in image_ids
        ],
        "summary": f"Processed {len(image_ids)} image(s) (stub)",
    }


async def tag_images(service: Any, scope: Scope, ctx: ContextInput, params: dict) -> dict:
    """
    Tag images using batch /images endpoint.
    All images in the scope are sent in a single request.
    """
    targets = _collect_targets(scope, ctx)
    
    if scope == "all":
        total = params.get("totalImages") or "ALL"
        return {
            "targets": "ALL",
            "summary": f"Requested tagging for {total} images",
        }
    
    if not targets:
        return {
            "targets": [],
            "images": [],
            "summary": "No images available for this request",
        }
    
    # Try remote API first
    remote = await _call_images_api(service, targets, params)
    if remote:
        data = remote.model_dump(exclude_none=True)
        data["targets"] = targets
        return data
    
    # Fallback to stub
    await _simulate_latency(len(targets))
    result = _stub_image_results(targets)
    result["targets"] = targets
    return result


# ==============================================================================
# Scene tagging - single scene endpoint, must spawn child tasks for multiple
# ==============================================================================

async def _call_scene_api(service: RemoteServiceBase, scene_id: str, params: dict) -> SceneTaggingResponse | None:
    """Call the /scene endpoint for a single scene."""   
    try:
        payload = {
            "scene_id": scene_id,
            "params": params or {},
        }
        return await service.http.post(
            SCENE_ENDPOINT,
            json=payload,
            response_model=SceneTaggingResponse,
        )
    except asyncio.CancelledError:  # pragma: no cover
        raise
    except Exception as exc:  # noqa: BLE001
        _log.warning("scene API call failed for scene_id=%s: %s", scene_id, exc)
        return None


def _stub_scene_result(scene_id: str) -> dict:
    """Generate stub result for a single scene when API is unavailable."""
    return {
        "scene_id": scene_id,
        "tags": _SCENE_TAGS,
        "summary": f"Processed scene {scene_id} (stub)",
    }


async def tag_scene_single(service: RemoteServiceBase, ctx: ContextInput, params: dict) -> dict:
    """
    Tag a single scene using the /scene endpoint.
    Used for detail view or when only one scene is selected.
    """
    targets = _collect_targets("detail", ctx)
    
    if not targets:
        return {
            "scene_id": None,
            "tags": [],
            "summary": "No scene available for this request",
        }
    
    scene_id = targets[0]
    
    # Try remote API first
    remote = await _call_scene_api(service, scene_id, params)
    if remote:
        return remote.model_dump(exclude_none=True)
    
    # Fallback to stub
    await _simulate_latency(1)
    return _stub_scene_result(scene_id)


async def tag_scene_all(service: Any, ctx: ContextInput, params: dict) -> dict:
    """Handle 'all scenes' request - just acknowledge, don't process."""
    total = params.get("totalScenes") or "ALL"
    return {
        "targets": "ALL",
        "summary": f"Requested tagging for {total} scenes",
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


def _child_priority(params: dict) -> TaskPriority:
    hint = str(params.get("child_priority") or params.get("priority") or "high").lower()
    return _PRIORITY_MAP.get(hint, TaskPriority.high)


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
    
    targets = _collect_targets(scope, ctx)
    
    if not targets:
        return {"message": "No scenes to process"}

    chunk_size = _sanitize_chunk_size(params.get("chunk_size") or params.get("chunkSize") or 1)
    child_priority = _child_priority(params)
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
