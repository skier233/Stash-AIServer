"""Unit tests for task handler metadata and chunked child task submission."""

from types import SimpleNamespace

import pytest

from stash_ai_server.actions.models import ContextInput
from stash_ai_server.tasks import helpers
from stash_ai_server.tasks.models import (
    TaskPriority,
    TaskSpec,
    TaskStatus,
    new_task,
)


def _parent(*, service: str = "service", cancel_requested: bool = False):
    task = new_task(
        "parent",
        service,
        TaskPriority.normal,
        ContextInput(page="scenes", selected_ids=["1", "2"]),
        {},
    )
    task.cancel_requested = cancel_requested
    return task


class FakeTaskManager:
    def __init__(self, *, child_status=TaskStatus.completed):
        self.child_status = child_status
        self.tasks = {}
        self.controllers = []
        self.submissions = []
        self.progress = []

    def mark_controller(self, task):
        self.controllers.append(task.id)
        task.skip_concurrency = True

    def submit(
        self,
        spec,
        handler,
        context,
        params,
        priority,
        *,
        group_id,
    ):
        child_id = f"child-{len(self.submissions) + 1}"
        child = SimpleNamespace(
            id=child_id,
            group_id=group_id,
            status=self.child_status,
        )
        self.tasks[child_id] = child
        self.submissions.append(
            {
                "spec": spec,
                "handler": handler,
                "context": context,
                "params": params,
                "priority": priority,
                "group_id": group_id,
            }
        )
        return child

    def emit_progress(self, task, payload):
        self.progress.append((task.id, payload))


def test_task_handler_attaches_task_spec():
    @helpers.task_handler(id="child", service="declared")
    async def handler(*_args):
        return None

    assert handler._task_spec == TaskSpec(id="child", service="declared")


def test_chunk_helpers_build_expected_contexts_and_chunks():
    parent_context = ContextInput(
        page="scenes",
        entity_id="parent",
        is_detail_view=True,
        selected_ids=["1", "2"],
    )

    single = helpers._make_child_context(["1"], parent_context)
    multiple = helpers._make_child_context(["1", "2"], parent_context)

    assert single.entity_id == "1"
    assert single.selected_ids == []
    assert single.is_detail_view is True
    assert multiple.entity_id is None
    assert multiple.selected_ids == ["1", "2"]
    assert list(helpers._chunk_items([1, 2, 3], 2)) == [[1, 2], [3]]
    assert list(helpers._chunk_items([1, 2], 0)) == [[1], [2]]
    assert list(helpers._chunk_items([], 2)) == []


@pytest.mark.asyncio
async def test_spawn_chunked_tasks_returns_early_for_empty_input(monkeypatch):
    manager = FakeTaskManager()
    monkeypatch.setattr(helpers, "task_manager", manager)

    result = await helpers.spawn_chunked_tasks(
        parent_task=_parent(),
        parent_context=ContextInput(page="scenes"),
        handler=lambda *_: None,
        items=[],
        chunk_size=2,
        hold_children=False,
    )

    assert result == {"spawned": [], "count": 0, "held": False}
    assert manager.submissions == []


@pytest.mark.asyncio
async def test_spawn_chunked_tasks_requires_handler_metadata(monkeypatch):
    monkeypatch.setattr(helpers, "task_manager", FakeTaskManager())

    with pytest.raises(ValueError, match="missing task metadata"):
        await helpers.spawn_chunked_tasks(
            parent_task=_parent(),
            parent_context=ContextInput(page="scenes"),
            handler=lambda *_: None,
            items=["1"],
            chunk_size=1,
        )


@pytest.mark.asyncio
async def test_spawn_chunked_tasks_infers_service_and_returns_without_holding(
    monkeypatch,
):
    manager = FakeTaskManager(child_status=TaskStatus.queued)
    monkeypatch.setattr(helpers, "task_manager", manager)

    @helpers.task_handler(id="child")
    async def handler(*_args):
        return None

    parent = _parent(service="parent-service")
    result = await helpers.spawn_chunked_tasks(
        parent_task=parent,
        parent_context=ContextInput(page="scenes"),
        handler=handler,
        items=["1", "2", "3"],
        chunk_size=2,
        params={"threshold": 0.5},
        priority=TaskPriority.low,
        hold_children=False,
    )

    assert result == {
        "spawned": ["child-1", "child-2"],
        "count": 2,
        "held": False,
        "min_hold": None,
    }
    assert manager.controllers == [parent.id]
    assert [entry["spec"].service for entry in manager.submissions] == [
        "parent-service",
        "parent-service",
    ]
    assert manager.submissions[0]["context"].selected_ids == ["1", "2"]
    assert manager.submissions[1]["context"].entity_id == "3"
    assert all(
        entry["params"] == {"threshold": 0.5}
        for entry in manager.submissions
    )
    assert all(
        entry["priority"] is TaskPriority.low for entry in manager.submissions
    )


@pytest.mark.asyncio
async def test_spawn_chunked_tasks_holds_completed_children(monkeypatch):
    manager = FakeTaskManager(child_status=TaskStatus.completed)
    monkeypatch.setattr(helpers, "task_manager", manager)
    custom_contexts = []

    def context_factory(chunk, parent_context):
        custom_contexts.append((list(chunk), parent_context.page))
        return ContextInput(page="images", selected_ids=list(chunk))

    async def handler(*_args):
        return None

    parent = _parent()
    result = await helpers.spawn_chunked_tasks(
        parent_task=parent,
        parent_context=ContextInput(page="scenes"),
        handler=handler,
        items=["1", "2"],
        chunk_size=1,
        task_spec=TaskSpec(id="explicit", service="explicit-service"),
        context_factory=context_factory,
    )

    assert result == {
        "spawned": ["child-1", "child-2"],
        "count": 2,
        "held": True,
    }
    assert custom_contexts == [(["1"], "scenes"), (["2"], "scenes")]
    assert manager.progress[-1] == (
        parent.id,
        {"completed": 2, "total": 2, "pending": 0},
    )


@pytest.mark.asyncio
async def test_spawn_chunked_tasks_stops_holding_when_parent_is_cancelled(
    monkeypatch,
):
    manager = FakeTaskManager(child_status=TaskStatus.running)
    monkeypatch.setattr(helpers, "task_manager", manager)
    parent = _parent(cancel_requested=True)
    parent.skip_concurrency = True

    result = await helpers.spawn_chunked_tasks(
        parent_task=parent,
        parent_context=ContextInput(page="scenes"),
        handler=lambda *_: None,
        items=["1"],
        chunk_size=1,
        task_spec=TaskSpec(id="child", service="service"),
        mark_parent_controller=False,
    )

    assert result["held"] is True
    assert manager.controllers == []
    assert manager.progress[-1][1] == {
        "completed": 0,
        "total": 1,
        "pending": 1,
    }
