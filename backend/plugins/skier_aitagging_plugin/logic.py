from __future__ import annotations

import asyncio
from typing import Literal, Sequence

from stash_ai_server.actions.models import ContextInput
from stash_ai_server.tasks.helpers import spawn_chunked_tasks, task_handler
from stash_ai_server.tasks.models import TaskPriority

Scope = Literal["detail", "selected", "page", "all"]
EntityKind = Literal["image", "scene"]

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


def _collect_targets(kind: EntityKind, scope: Scope, ctx: ContextInput) -> list[str]:
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
    raise ValueError(f"Unknown scope '{scope}' for {kind}")


def _summary(kind: EntityKind, scope: Scope, count: int) -> str:
    noun = f"{kind}s"
    if scope == "all":
        return f"Requested tagging for all {noun} in library (stub)"
    scope_label = {
        "detail": "detail",
        "selected": "selected",
        "page": "page",
    }[scope]
    plural = "s" if count != 1 else ""
    return f"Processed {count} {kind}{plural} from {scope_label} scope (stub)"


def _entity_payload(kind: EntityKind, entity_id: str) -> dict:
    tags = _IMAGE_TAGS if kind == "image" else _SCENE_TAGS
    key = f"{kind}_id"
    return {
        key: entity_id,
        "suggested_tags": tags,
        "notes": f"Mock {kind} tagging data",
    }


async def _simulate_latency(entity_count: int):
    delay = min(0.05 * max(entity_count, 1), 0.4)
    await asyncio.sleep(delay)


async def tag_images(scope: Scope, ctx: ContextInput, params: dict) -> dict:
    targets = _collect_targets("image", scope, ctx)
    if scope == "all":
        total = params.get("totalImages") or "ALL"
        return {
            "targets": "ALL",
            "summary": f"Requested tagging for {total} images (stub)",
        }
    if not targets:
        return {
            "targets": [],
            "images": [],
            "summary": "No images available for this request",
        }
    await _simulate_latency(len(targets))
    return {
        "targets": targets,
        "images": [_entity_payload("image", iid) for iid in targets],
        "summary": _summary("image", scope, len(targets)),
    }


async def tag_scenes(scope: Scope, ctx: ContextInput, params: dict) -> dict:
    targets = _collect_targets("scene", scope, ctx)
    if scope == "all":
        total = params.get("totalScenes") or "ALL"
        return {
            "targets": "ALL",
            "summary": f"Requested tagging for {total} scenes (stub)",
        }
    if not targets:
        return {
            "targets": [],
            "scenes": [],
            "summary": "No scenes available for this request",
        }
    await _simulate_latency(len(targets))
    return {
        "targets": targets,
        "scenes": [_entity_payload("scene", sid) for sid in targets],
        "summary": _summary("scene", scope, len(targets)),
    }


def _format_preview(ids: Sequence[str]) -> str:
    if not ids:
        return ""
    if len(ids) <= 3:
        return ", ".join(ids)
    return f"{', '.join(ids[:3])} +{len(ids) - 3}"


def _make_scene_context(chunk: Sequence[str]) -> ContextInput:
    if len(chunk) == 1:
        return ContextInput(page="scenes", entity_id=chunk[0], is_detail_view=True, selected_ids=[])
    return ContextInput(page="scenes", entity_id=None, is_detail_view=False, selected_ids=list(chunk))

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


@task_handler(id="skier.ai_tag.scene.chunk", service="ai")
async def _run_scene_chunk(ctx: ContextInput, params: dict, task_record=None):
    scope = "detail" if ctx.is_detail_view else "selected"
    return await tag_scenes(scope, ctx, params)


async def spawn_scene_batch(
    ctx: ContextInput,
    params: dict,
    task_record,
) -> dict:
    selected = _collect_targets("scene", "selected", ctx)
    if not selected:
        return {"message": "No scenes selected"}

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

    min_hold_raw = params.get("hold_parent_seconds", params.get("holdParentSeconds", 0))
    try:
        min_hold = float(min_hold_raw or 0)
    except (TypeError, ValueError):
        min_hold = 0.0

    result = await spawn_chunked_tasks(
        parent_task=task_record,
        handler=_run_scene_chunk,
        items=selected,
        chunk_size=chunk_size,
        context_factory=_make_scene_context,
        params=child_params,
        priority=child_priority,
        hold_children=hold,
        min_hold_seconds=min_hold,
    )

    return result
