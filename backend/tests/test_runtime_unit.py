"""Unit tests for in-process backend refresh coordination."""

import asyncio
from types import SimpleNamespace

import pytest

from stash_ai_server.core import runtime


@pytest.fixture(autouse=True)
def reset_runtime_state():
    original_handlers = dict(runtime._REFRESH_HANDLERS)
    original_handler_counter = runtime._handler_counter
    original_refresh_task = runtime._refresh_task
    original_refresh_pending = runtime._refresh_pending
    original_refresh_running = runtime._refresh_running
    original_refresh_again = runtime._refresh_again

    runtime._REFRESH_HANDLERS.clear()
    runtime._handler_counter = 0
    runtime._refresh_task = None
    runtime._refresh_pending = False
    runtime._refresh_running = False
    runtime._refresh_again = False
    try:
        yield
    finally:
        task = runtime._refresh_task
        if task is not None and not task.done():
            task.cancel()
        runtime._REFRESH_HANDLERS.clear()
        runtime._REFRESH_HANDLERS.update(original_handlers)
        runtime._handler_counter = original_handler_counter
        runtime._refresh_task = original_refresh_task
        runtime._refresh_pending = original_refresh_pending
        runtime._refresh_running = original_refresh_running
        runtime._refresh_again = original_refresh_again


def test_register_refresh_handler_validates_inputs_and_replaces_names():
    first = lambda: None
    second = lambda: None

    with pytest.raises(ValueError, match="name is required"):
        runtime.register_backend_refresh_handler("", first)
    with pytest.raises(TypeError, match="must be callable"):
        runtime.register_backend_refresh_handler("invalid", None)

    runtime.register_backend_refresh_handler("same", first, priority=5)
    runtime.register_backend_refresh_handler("same", second, priority=-1)

    priority, order, callback = runtime._REFRESH_HANDLERS["same"]
    assert priority == -1
    assert order == 2
    assert callback is second


@pytest.mark.asyncio
async def test_execute_refresh_orders_sync_and_async_handlers():
    calls = []

    def later():
        calls.append("later")

    async def first():
        await asyncio.sleep(0)
        calls.append("first")

    runtime.register_backend_refresh_handler("later", later, priority=5)
    runtime.register_backend_refresh_handler("first", first, priority=-1)
    runtime._refresh_task = object()

    await runtime._execute_refresh(0)

    assert calls == ["first", "later"]
    assert runtime._refresh_running is False
    assert runtime._refresh_task is None


@pytest.mark.asyncio
async def test_execute_refresh_continues_after_handler_failure(caplog):
    calls = []

    def broken():
        raise RuntimeError("refresh failed")

    runtime.register_backend_refresh_handler("broken", broken)
    runtime.register_backend_refresh_handler("working", lambda: calls.append("working"))

    await runtime._execute_refresh(0)

    assert calls == ["working"]
    assert "backend refresh handler broken failed" in caplog.text


@pytest.mark.asyncio
async def test_execute_refresh_handles_empty_registry_and_delay(monkeypatch, caplog):
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(
        runtime,
        "asyncio",
        SimpleNamespace(sleep=fake_sleep),
    )
    caplog.set_level("INFO", logger=runtime.__name__)

    await runtime._execute_refresh(0.25)

    assert delays == [0.25]
    assert "no handlers registered" in caplog.text


def test_schedule_backend_restart_runs_synchronously_without_event_loop():
    calls = []
    runtime.register_backend_refresh_handler("sync", lambda: calls.append("sync"))

    runtime.schedule_backend_restart(0)

    assert calls == ["sync"]
    assert runtime._refresh_pending is False
    assert runtime._refresh_running is False


@pytest.mark.asyncio
async def test_schedule_backend_restart_requeues_during_active_refresh():
    calls = []

    def handler():
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            runtime.schedule_backend_restart(0)

    runtime.register_backend_refresh_handler("handler", handler)
    runtime.schedule_backend_restart(0)
    first_task = runtime._refresh_task
    assert first_task is not None

    await first_task
    for _ in range(10):
        if len(calls) == 2:
            break
        await asyncio.sleep(0)
    second_task = runtime._refresh_task
    if second_task is not None:
        await second_task

    assert calls == [1, 2]
    assert runtime._refresh_again is False


def test_schedule_backend_restart_coalesces_pending_requests():
    runtime._refresh_pending = True

    runtime.schedule_backend_restart(0)

    assert runtime._refresh_pending is True
    assert runtime._refresh_running is False


@pytest.mark.asyncio
async def test_start_refresh_task_clamps_negative_delay(monkeypatch):
    delays = []

    async def fake_execute(delay):
        delays.append(delay)

    monkeypatch.setattr(runtime, "_execute_refresh", fake_execute)
    loop = asyncio.get_running_loop()

    runtime._start_refresh_task(loop, -5)
    task = runtime._refresh_task
    assert task is not None
    await task

    assert delays == [0.0]
    assert runtime._refresh_running is True
