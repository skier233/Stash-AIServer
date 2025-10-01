from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import List
from stash_ai_server.tasks.manager import manager
import json
import asyncio

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


# Register listener
try:
    manager.on_event(_task_event_listener)
except Exception:
    pass


@router.websocket('/ws/tasks')
async def tasks_ws(ws: WebSocket):
    """Websocket endpoint that sends a task snapshot then streams updates."""
    await ws_manager.connect(ws)
    for t in manager.list():
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
