"""Test configuration and fixtures for comprehensive test suite."""

import time
import sys
import pathlib
import os
import asyncio
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from typing import AsyncGenerator, Generator

# Ensure backend root (containing 'app' package) is on sys.path
BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Import test configuration and database fixtures
# Handle both backend/ and root workspace directory contexts
try:
    # When running from backend/ directory
    from tests.config import test_config
    from tests.database import test_database, db_session, async_db_session, clean_database
except ImportError:
    # When running from root workspace directory
    from backend.tests.config import test_config
    from backend.tests.database import test_database, db_session, async_db_session, clean_database


# Configure pytest for async testing
def pytest_configure(config):
    """Configure pytest with async support and test environment."""
    # Apply test configuration environment overrides
    test_config.apply_environment_overrides()


def pytest_unconfigure(config):
    """Clean up test environment after all tests complete."""
    # Clean up test configuration
    test_config.cleanup_environment()


@pytest_asyncio.fixture(scope="session")
async def test_app(test_database):
    """Session-scoped test application with isolated configuration."""
    # Import after database is set up and environment is configured
    from stash_ai_server.main import app
    
    yield app


@pytest_asyncio.fixture(scope="session") 
async def client(test_app):
    """Session-scoped test client with isolated database and configuration."""
    with TestClient(test_app) as c:
        # Force debug & faster loop inside tests
        os.environ.setdefault('TASK_DEBUG', '1')
        os.environ.setdefault('TASK_LOOP_INTERVAL', '0.01')
        
        # Import task manager after environment is set up
        from stash_ai_server.tasks.manager import manager
        
        # Ensure task manager started (startup event sometimes races under TestClient)
        if not manager._runner_started:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(manager.start())
        
        # Small delay to ensure everything is initialized
        time.sleep(0.05)
        yield c


def submit_task(client, action_id: str, context: dict, params: dict | None = None, priority: str | None = None):
    """Helper function to submit tasks via API."""
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
    """Fixture providing task submission helper."""
    def _call(action_id: str, context: dict, params: dict | None = None, priority: str | None = None):
        return submit_task(client, action_id, context, params, priority)
    return _call


@pytest_asyncio.fixture
async def isolated_plugin_dir(tmp_path):
    """Provide isolated plugin directory for plugin tests."""
    plugin_dir = tmp_path / "test_plugins"
    plugin_dir.mkdir()
    
    # Set environment variable for plugin directory
    original_plugin_dir = os.environ.get('AI_SERVER_PLUGINS_DIR')
    os.environ['AI_SERVER_PLUGINS_DIR'] = str(plugin_dir)
    
    yield plugin_dir
    
    # Restore original plugin directory
    if original_plugin_dir:
        os.environ['AI_SERVER_PLUGINS_DIR'] = original_plugin_dir
    else:
        os.environ.pop('AI_SERVER_PLUGINS_DIR', None)
