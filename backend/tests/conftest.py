import time
import sys
import pathlib
import os
import asyncio
import pytest
from fastapi.testclient import TestClient

# Ensure backend root (containing 'app' package) is on sys.path
BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from stash_ai_server.main import app

# Register the slow test service only for tests. Prefer the plugin implementation
# if the plugin is present, otherwise fall back to the local shim at
# stash_ai_server.services.slow.service.
try:
    from stash_ai_server.plugins.slow_service_plugin.service import register as register_slow_service
except Exception:
    from stash_ai_server.services.slow.service import register as register_slow_service
from stash_ai_server.tasks.manager import manager

try:
    register_slow_service()
except Exception:
    # Ignore double registration in watch mode
    pass


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        # Force debug & faster loop inside tests
        os.environ.setdefault('TASK_DEBUG', '1')
        os.environ.setdefault('TASK_LOOP_INTERVAL', '0.01')
        # Ensure task manager started (startup event sometimes races under TestClient)
        if not manager._runner_started:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(manager.start())
        time.sleep(0.05)
        yield c


def submit_task(client, action_id: str, context: dict, params: dict | None = None, priority: str | None = None):
    payload = {
        'action_id': action_id,
        'context': context,
        'params': params or {},
    }
    if priority:
        payload['priority'] = priority
    r = client.post('/api/v1/tasks/submit', json=payload)
    assert r.status_code == 200, r.text
    return r.json()['task_id']


@pytest.fixture
def submit_task_helper(client):
    def _call(action_id: str, context: dict, params: dict | None = None, priority: str | None = None):
        return submit_task(client, action_id, context, params, priority)
    return _call
