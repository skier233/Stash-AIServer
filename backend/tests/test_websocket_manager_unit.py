"""Unit tests for WebSocket connection and task-event handling."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect

from stash_ai_server.api import ws as ws_api


class FakeWebSocket:
    def __init__(self, *, fail_send=False, receive_error=None):
        self.fail_send = fail_send
        self.receive_error = receive_error
        self.accepted = 0
        self.sent = []

    async def accept(self):
        self.accepted += 1

    async def send_text(self, data):
        if self.fail_send:
            raise RuntimeError("send failed")
        self.sent.append(data)

    async def receive_text(self):
        if self.receive_error is not None:
            raise self.receive_error
        return "ping"


class FakeTask:
    def __init__(self, identifier):
        self.identifier = identifier

    def summary(self):
        return {"id": self.identifier}


class FakeTaskManager:
    def __init__(self, tasks=(), *, registration_error=None):
        self.tasks = list(tasks)
        self.registration_error = registration_error
        self.listeners = []

    def on_event(self, listener):
        if self.registration_error is not None:
            raise self.registration_error
        self.listeners.append(listener)

    def list(self):
        return list(self.tasks)


@pytest.fixture(autouse=True)
def reset_websocket_globals(monkeypatch):
    manager = ws_api.ConnectionManager()
    monkeypatch.setattr(ws_api, "ws_manager", manager)
    monkeypatch.setattr(ws_api, "_listener_registered", False)
    monkeypatch.setattr(ws_api, "_task_manager_ref", None)
    return manager


@pytest.mark.asyncio
async def test_connection_manager_connects_removes_and_broadcasts():
    manager = ws_api.ConnectionManager()
    healthy = FakeWebSocket()
    stale = FakeWebSocket(fail_send=True)

    await manager.connect(healthy)
    await manager.connect(stale)
    await manager.broadcast({"type": "update", "value": 1})

    assert healthy.accepted == 1
    assert stale.accepted == 1
    assert [json.loads(payload) for payload in healthy.sent] == [
        {"type": "update", "value": 1}
    ]
    assert manager.active == [healthy]


@pytest.mark.asyncio
async def test_task_event_listener_schedules_broadcast_on_running_loop(monkeypatch):
    payloads = []

    async def broadcast(payload):
        payloads.append(payload)

    monkeypatch.setattr(ws_api.ws_manager, "broadcast", broadcast)

    ws_api._task_event_listener("completed", FakeTask("task-1"), None)
    await asyncio.sleep(0)

    assert payloads == [
        {"type": "task.completed", "task": {"id": "task-1"}}
    ]


def test_task_event_listener_falls_back_to_asyncio_run(monkeypatch):
    payloads = []

    async def broadcast(payload):
        payloads.append(payload)

    def no_loop():
        raise RuntimeError("no loop")

    monkeypatch.setattr(ws_api.ws_manager, "broadcast", broadcast)
    monkeypatch.setattr(
        ws_api,
        "asyncio",
        SimpleNamespace(
            get_running_loop=no_loop,
            get_event_loop=no_loop,
            run=asyncio.run,
        ),
    )

    ws_api._task_event_listener("failed", FakeTask("task-2"), None)

    assert payloads == [
        {"type": "task.failed", "task": {"id": "task-2"}}
    ]


def test_listener_registration_is_idempotent_and_tracks_manager_changes():
    first = FakeTaskManager()
    second = FakeTaskManager()

    ws_api._ensure_listener_registered(first)
    ws_api._ensure_listener_registered(first)
    ws_api._ensure_listener_registered(second)

    assert first.listeners == [ws_api._task_event_listener]
    assert second.listeners == [ws_api._task_event_listener]
    assert ws_api._task_manager_ref is second


def test_listener_registration_failure_does_not_mark_registered():
    manager = FakeTaskManager(registration_error=RuntimeError("failed"))

    ws_api._ensure_listener_registered(manager)

    assert ws_api._listener_registered is False
    assert ws_api._task_manager_ref is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "receive_error_type",
    [WebSocketDisconnect, RuntimeError],
)
async def test_tasks_websocket_authenticates_sends_snapshots_and_cleans_up(
    monkeypatch,
    receive_error_type,
):
    socket = FakeWebSocket(receive_error=receive_error_type())
    task_manager = FakeTaskManager([FakeTask("one"), FakeTask("two")])
    auth_calls = []

    async def enforce_auth(ws):
        auth_calls.append(ws)

    monkeypatch.setattr(ws_api, "enforce_shared_key_websocket", enforce_auth)

    await ws_api.tasks_ws(socket, task_manager)

    assert auth_calls == [socket]
    assert socket.accepted == 1
    assert [json.loads(payload) for payload in socket.sent] == [
        {"type": "task.snapshot", "task": {"id": "one"}},
        {"type": "task.snapshot", "task": {"id": "two"}},
    ]
    assert ws_api.ws_manager.active == []
    assert task_manager.listeners == [ws_api._task_event_listener]


@pytest.mark.asyncio
async def test_tasks_websocket_ignores_snapshot_send_failures(monkeypatch):
    socket = FakeWebSocket(
        fail_send=True,
        receive_error=WebSocketDisconnect(),
    )
    task_manager = FakeTaskManager([FakeTask("one")])

    async def enforce_auth(_ws):
        return None

    monkeypatch.setattr(ws_api, "enforce_shared_key_websocket", enforce_auth)

    await ws_api.tasks_ws(socket, task_manager)

    assert socket.accepted == 1
    assert socket.sent == []
    assert ws_api.ws_manager.active == []
