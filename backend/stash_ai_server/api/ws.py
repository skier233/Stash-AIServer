from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from typing import List
from stash_ai_server.core.dependencies import TaskManagerDep
import json
import asyncio
from stash_ai_server.core.api_key import enforce_shared_key_websocket

router = APIRouter()


class ConnectionManager:
    """Lightweight websocket connection manager."""
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def remove(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict):
        data = json.dumps(message)
        stale = []
        for ws in self.active:
            try:
                await ws.send_text(data)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.remove(ws)


ws_manager = ConnectionManager()


def _task_event_listener(event: str, task, extra):
    """Forward task events to connected websockets asynchronously."""
    payload = {'type': f'task.{event}', 'task': task.summary()}

    async def _do_send():
        await ws_manager.broadcast(payload)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        loop.create_task(_do_send())
    else:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(_do_send(), loop)
            else:
                loop.run_until_complete(_do_send())
        except Exception:
            asyncio.run(_do_send())


# Task event listener registration - will be done when WebSocket connects
_listener_registered = False
_task_manager_ref = None

def _task_event_listener(event: str, task, extra):
    """Forward task events to connected websockets asynchronously."""
    payload = {'type': f'task.{event}', 'task': task.summary()}

    async def _do_send():
        await ws_manager.broadcast(payload)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        loop.create_task(_do_send())
    else:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(_do_send(), loop)
            else:
                loop.run_until_complete(_do_send())
        except Exception:
            asyncio.run(_do_send())


def _ensure_listener_registered(task_manager):
    """Ensure the task event listener is registered."""
    global _listener_registered, _task_manager_ref
    if not _listener_registered or _task_manager_ref != task_manager:
        try:
            task_manager.on_event(_task_event_listener)
            _listener_registered = True
            _task_manager_ref = task_manager
        except Exception:
            pass


@router.websocket('/ws/tasks')
async def tasks_ws(ws: WebSocket, task_manager: TaskManagerDep):
    """Websocket endpoint that sends a task snapshot then streams updates."""
    _ensure_listener_registered(task_manager)  # Register listener when first WebSocket connects
    await enforce_shared_key_websocket(ws)
    await ws_manager.connect(ws)
    for t in task_manager.list():
        try:
            await ws.send_text(json.dumps({'type': 'task.snapshot', 'task': t.summary()}))
        except Exception:
            pass
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_manager.remove(ws)
    except Exception:
        ws_manager.remove(ws)
