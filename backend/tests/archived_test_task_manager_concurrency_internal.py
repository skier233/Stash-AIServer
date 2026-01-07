import asyncio
import pathlib
import sys
import time
import uuid

import pytest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from stash_ai_server.actions.models import ContextInput
from stash_ai_server.services.registry import ServiceBase, services
from stash_ai_server.tasks.helpers import task_handler
from stash_ai_server.tasks.manager import SERVICE_CONFIG, manager
from stash_ai_server.tasks.models import TaskPriority, TaskStatus, TaskSpec


@pytest.mark.asyncio
async def test_pending_service_configured_when_manager_ready():
    unique_name = f"svc_{uuid.uuid4().hex}"

    class PendingService(ServiceBase):
        name = unique_name
        max_concurrency = 4

    svc = PendingService()

    original_task_manager = getattr(services, "_task_manager", None)
    original_pending = set(getattr(services, "_pending_task_configs", set()))

    try:
        services._services.pop(unique_name, None)
        services._pending_task_configs = set()

        class _FailingManager:
            def configure_service(self, *args, **kwargs):
                raise RuntimeError("manager not ready")

        services._task_manager = _FailingManager()

        services.register(svc)
        assert unique_name in services._pending_task_configs

        services.set_task_manager(manager)
        assert SERVICE_CONFIG[unique_name]["max_concurrent"] == svc.max_concurrency
    finally:
        services._services.pop(unique_name, None)
        SERVICE_CONFIG.pop(unique_name, None)
        services._pending_task_configs = set(original_pending)
        services._task_manager = original_task_manager
        services.set_task_manager(manager)


@pytest.mark.asyncio
async def test_task_manager_honors_service_max_concurrency():
    await manager.start()

    unique_name = f"svc_{uuid.uuid4().hex}"

    class ConcurrentService(ServiceBase):
        name = unique_name
        max_concurrency = 3

    svc = ConcurrentService()
    services._services.pop(unique_name, None)
    services.register(svc)
    services.set_task_manager(manager)
    assert SERVICE_CONFIG[unique_name]["max_concurrent"] == svc.max_concurrency

    counts = {"active": 0, "peak": 0}
    lock = asyncio.Lock()

    @task_handler(id=f"{unique_name}.child", service=unique_name)
    async def child_handler(ctx: ContextInput, params: dict, task):
        async with lock:
            counts["active"] += 1
            counts["peak"] = max(counts["peak"], counts["active"])
        await asyncio.sleep(0.2)
        async with lock:
            counts["active"] -= 1
        return {"done": True}

    ctx = ContextInput(page="tests", entity_id=None, is_detail_view=False, selected_ids=[])
    spec = TaskSpec(id=f"{unique_name}.child", service=unique_name)

    tasks = [manager.submit(spec, child_handler, ctx, {}, TaskPriority.high) for _ in range(9)]

    ramp_deadline = time.time() + 5
    while time.time() < ramp_deadline and counts["peak"] < svc.max_concurrency:
        await asyncio.sleep(0.01)

    deadline = time.time() + 5
    while time.time() < deadline:
        statuses = [t.status for t in tasks]
        if all(status == TaskStatus.completed for status in statuses):
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("Tasks did not complete within timeout")

    assert counts["peak"] >= svc.max_concurrency
    assert counts["peak"] <= svc.max_concurrency
    assert all(t.status == TaskStatus.completed for t in tasks)
    assert counts["active"] == 0

    try:
        for t in tasks:
            manager.tasks.pop(t.id, None)
            manager.cancel_tokens.pop(t.id, None)
            manager._handlers.pop(t.id, None)
            manager._task_specs.pop(t.id, None)

        manager.queues.pop(unique_name, None)
        manager.running_counts.pop(unique_name, None)
        services._services.pop(unique_name, None)
        SERVICE_CONFIG.pop(unique_name, None)
    finally:
        counts["active"] = 0
