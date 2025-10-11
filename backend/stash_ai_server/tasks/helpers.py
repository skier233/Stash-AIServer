from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, Callable, Iterable, Sequence, TypeVar

from stash_ai_server.actions.models import ContextInput
from stash_ai_server.tasks.models import TaskPriority, TaskSpec, TaskStatus, TaskRecord
from stash_ai_server.tasks.manager import manager as task_manager

T = TypeVar("T")


def task_handler(*, id: str, service: str | None = None):
    """Decorator that attaches a TaskSpec template to a coroutine handler.

    The decorated function can then be passed directly to TaskManager.submit
    (or helper functions in this module) without manually creating a TaskSpec
    for each invocation.
    """

    def decorator(fn: Callable[..., Any]):
        spec = TaskSpec(id=id, service=service or '')
        setattr(fn, "_task_spec", spec)
        return fn

    return decorator


def _make_child_context(chunk: Sequence[str], parent_context: ContextInput) -> ContextInput:
    if len(chunk) == 1:
        return ContextInput(page=parent_context.page, entity_id=chunk[0], is_detail_view=parent_context.is_detail_view, selected_ids=[])
    return ContextInput(page=parent_context.page, entity_id=None, is_detail_view=parent_context.is_detail_view, selected_ids=list(chunk))

def _chunk_items(items: Sequence[T], chunk_size: int) -> Iterable[list[T]]:
    size = max(1, int(chunk_size))
    for idx in range(0, len(items), size):
        chunk = list(items[idx : idx + size])
        if chunk:
            yield chunk


async def spawn_chunked_tasks(
    *,
    parent_task: TaskRecord,
    parent_context: ContextInput,
    handler: Callable[[ContextInput, dict, TaskRecord | None], Any],
    items: Sequence[T],
    chunk_size: int,
    params: dict | None = None,
    priority: TaskPriority = TaskPriority.high,
    hold_children: bool = True,
    task_spec: TaskSpec | None = None,
    context_factory: Callable[[Sequence[T], ContextInput], ContextInput] | None = None,
    mark_parent_controller: bool = True,
) -> dict:
    """Submit child tasks for the provided items in evenly sized chunks.

    Args:
        parent_task: The controller/parent TaskRecord.
        handler: Async callable for each chunk (must accept (ctx, params, task_record?) signature).
        items: Sequence of items to process.
        chunk_size: Maximum number of items per chunk.
        params: Optional parameters forwarded to each child task.
        priority: Priority for child tasks (defaults to high).
        hold_children: If True, wait until all children finish (or parent cancelled).
        task_spec: Optional TaskSpec template; inferred from handler when omitted.
        context_factory: Builds a ContextInput for each chunk.
        mark_parent_controller: When True (default), mark parent_task as a controller so it no longer
            counts against service concurrency once child work begins.

    Returns:
        Dict with spawned task ids and control metadata for the caller.
    """

    if not items:
        return {"spawned": [], "count": 0, "held": bool(hold_children)}

    params = dict(params or {})
    spec_template = task_spec or getattr(handler, "_task_spec", None)
    if spec_template is None:
        raise ValueError("Handler is missing task metadata. Decorate with @task_handler or pass task_spec explicitly.")

    if not spec_template.service:
        spec_template = replace(spec_template, service=parent_task.service)

    if mark_parent_controller and not parent_task.skip_concurrency:
        task_manager.mark_controller(parent_task)

    spawned: list[str] = []
    for chunk in _chunk_items(items, chunk_size):
        if context_factory is None:
            context_factory = _make_child_context
        chunk_ctx = context_factory(chunk, parent_context)
        child = task_manager.submit(
            spec_template,
            handler,
            chunk_ctx,
            params,
            priority,
            group_id=parent_task.id,
        )
        spawned.append(child.id)
        task_manager.emit_progress(parent_task, {"queued": len(spawned), "total": len(spawned)})

    if not hold_children:
        return {"spawned": spawned, "count": len(spawned), "held": False, "min_hold": None}

    while True:
        children = [t for t in task_manager.tasks.values() if t.group_id == parent_task.id]
        pending = [c for c in children if c.status not in (TaskStatus.completed, TaskStatus.failed, TaskStatus.cancelled)]
        completed = len(children) - len(pending)
        if children:
            task_manager.emit_progress(parent_task, {"completed": completed, "total": len(children), "pending": len(pending)})
        if not pending:
            break
        if getattr(parent_task, "cancel_requested", False):
            break
        await asyncio.sleep(0.1)

    return {
        "spawned": spawned,
        "count": len(spawned),
        "held": True,
    }
