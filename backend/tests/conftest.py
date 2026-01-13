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
    # Only apply test configuration if we're running database-related tests
    # Check if any of the selected tests require database setup
    if hasattr(config, 'getoption'):
        # Don't automatically apply environment overrides
        # Let individual fixtures handle this when needed
        pass


def pytest_unconfigure(config):
    """Clean up test environment after all tests complete."""
    # Clean up test configuration
    test_config.cleanup_environment()
    
    # Ensure all TaskManager instances are properly shut down
    try:
        import asyncio
        from stash_ai_server.core.dependencies import get_task_manager, set_test_task_manager_override
        
        # Create a new event loop for cleanup if needed
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        # Get any remaining TaskManager instance
        try:
            manager = get_task_manager()
            if manager and manager._runner_started:
                loop.run_until_complete(manager.shutdown())
        except Exception:
            pass
        
        # Cancel all remaining asyncio tasks
        try:
            current_task = asyncio.current_task()
            for task in asyncio.all_tasks():
                if task != current_task and not task.done():
                    task_name = getattr(task, '_name', '')
                    if 'task_manager' in task_name.lower():
                        task.cancel()
        except Exception:
            pass
        
        # Clear any test overrides
        try:
            set_test_task_manager_override(None)
        except Exception:
            pass
        
        # Close the event loop if we created it
        try:
            if loop.is_running():
                loop.stop()
            loop.close()
        except Exception:
            pass
            
    except Exception:
        # Ignore cleanup errors
        pass


@pytest_asyncio.fixture(scope="session")
async def test_app(test_database):
    """Session-scoped test application with isolated configuration."""
    # Import after database is set up and environment is configured
    from stash_ai_server.main import app
    
    yield app


@pytest_asyncio.fixture(scope="session") 
async def client_with_database(test_app):
    """Session-scoped test client with isolated database and configuration."""
    # Create a test-specific app without the problematic lifespan
    from fastapi import FastAPI
    from stash_ai_server.core.config import settings
    from stash_ai_server.api import interactions as interactions_router
    from stash_ai_server.api import actions as actions_router
    from stash_ai_server.api import tasks as tasks_router
    from stash_ai_server.api import ws as ws_router
    from stash_ai_server.api import recommendations as recommendations_router
    from stash_ai_server.api import plugins as plugins_router
    from stash_ai_server.api import system as system_router
    from stash_ai_server.api import version as version_router
    
    # Create a minimal test app without lifespan
    test_app_minimal = FastAPI(title=f"{settings.app_name}_test")
    
    # Add routers
    test_app_minimal.include_router(actions_router.router, prefix=settings.api_v1_prefix)
    test_app_minimal.include_router(tasks_router.router, prefix=settings.api_v1_prefix)
    test_app_minimal.include_router(ws_router.router, prefix=settings.api_v1_prefix)
    test_app_minimal.include_router(recommendations_router.router, prefix=settings.api_v1_prefix)
    test_app_minimal.include_router(plugins_router.router, prefix=settings.api_v1_prefix)
    test_app_minimal.include_router(interactions_router.router, prefix=settings.api_v1_prefix)
    test_app_minimal.include_router(system_router.router, prefix=settings.api_v1_prefix)
    test_app_minimal.include_router(version_router.router, prefix=settings.api_v1_prefix)
    
    # Add root endpoint
    @test_app_minimal.get("/")
    def root():
        return {"status": "ok", "app": settings.app_name}
    
    with TestClient(test_app_minimal) as c:
        # Force debug & faster loop inside tests
        os.environ.setdefault('TASK_DEBUG', '1')
        os.environ.setdefault('TASK_LOOP_INTERVAL', '0.01')
        
        # Import and start task manager manually in a controlled way
        manager = None
        try:
            from stash_ai_server.core.dependencies import get_task_manager
            
            # Get the task manager instance through dependency injection
            manager = get_task_manager()
            
            # Configure task manager for testing but DO NOT START IT
            # Let individual tests control when to start it
            manager._loop_interval = 0.01
            manager._debug = True
            
            # DO NOT START the task manager here - let individual tests control this
            # This prevents conflicts with isolated_task_manager fixtures
            
            # Small delay to ensure everything is initialized
            time.sleep(0.05)
            
        except Exception as e:
            # If task manager fails to start, log but continue
            # Tests can still run without it for basic functionality
            print(f"Warning: Task manager setup failed in test: {e}")
        
        try:
            yield c
        finally:
            # Cleanup: shutdown the task manager after the session
            if manager:
                try:
                    # Only shutdown if it was actually started
                    if manager._runner_started:
                        loop = asyncio.get_event_loop()
                        loop.run_until_complete(manager.shutdown())
                except Exception as e:
                    print(f"Warning: Task manager failed to shutdown cleanly: {e}")
            
            # Clear the test override to prevent interference with other tests
            try:
                from stash_ai_server.core.dependencies import set_test_task_manager_override
                set_test_task_manager_override(None)
            except Exception:
                pass


@pytest.fixture(scope="session")
def client_with_db(client_with_database):
    """Alias for client_with_database for backward compatibility."""
    return client_with_database


@pytest.fixture(scope="session")
def client_with_auth(client_with_database):
    """Test client with authentication enabled."""
    # Set up authentication for this test session
    from stash_ai_server.core.system_settings import set_value
    
    # Configure a test API key
    test_api_key = "test_api_key_12345"
    set_value('UI_SHARED_API_KEY', test_api_key)
    
    yield client_with_database
    
    # Clean up - disable authentication
    set_value('UI_SHARED_API_KEY', '')


@pytest.fixture(scope="session")
def client_no_auth():
    """Lightweight test client without authentication or database dependencies."""
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    import os
    
    # Set up test environment to prevent database connections
    os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
    os.environ['STASH_URL'] = 'http://localhost:9999'
    os.environ['STASH_API_KEY'] = 'test_key'
    
    # Create a minimal test app with mocked routes that mirror the real API
    test_app = FastAPI(title="test_app_no_auth")
    
    # Add basic endpoints that mirror the real API structure
    @test_app.get("/")
    def root():
        return {"status": "ok", "app": "test_app_no_auth"}
    
    @test_app.post("/api/v1/actions/available")
    def actions_available():
        # This will be mocked in individual tests to return auth errors
        return {"actions": []}
    
    @test_app.post("/api/v1/actions/submit")
    def actions_submit():
        return {"task_id": "test"}
    
    # Create test client
    with TestClient(test_app) as client:
        yield client


@pytest.fixture(scope="session")
def client():
    """Lightweight test client without database dependencies for basic API tests."""
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from unittest.mock import patch, MagicMock
    import os
    
    # Set up test environment to prevent database connections
    os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
    os.environ['STASH_URL'] = 'http://localhost:9999'
    os.environ['STASH_API_KEY'] = 'test_key'
    
    # Create a minimal test app with mocked routes
    test_app = FastAPI(title="test_app_minimal")
    
    # Add basic endpoints that mirror the real API structure
    @test_app.get("/")
    def root():
        return {"status": "ok", "app": "test_app_minimal"}
    
    @test_app.get("/api/v1/version")
    def version():
        return {"version": "test", "db_alembic_head": "test", "frontend_min_version": "test"}
    
    @test_app.get("/api/v1/tasks")
    def list_tasks():
        # Mock authentication check
        raise HTTPException(status_code=401, detail="Authentication required")
    
    @test_app.get("/api/v1/tasks/history")
    def task_history():
        raise HTTPException(status_code=401, detail="Authentication required")
    
    @test_app.post("/api/v1/tasks/{task_id}/cancel")
    def cancel_task(task_id: str):
        raise HTTPException(status_code=401, detail="Authentication required")
    
    @test_app.get("/api/v1/tasks/{task_id}")
    def get_task(task_id: str):
        raise HTTPException(status_code=401, detail="Authentication required")
    
    @test_app.get("/api/v1/recommendations/recommenders")
    def list_recommenders():
        raise HTTPException(status_code=401, detail="Authentication required")
    
    @test_app.post("/api/v1/recommendations/query")
    def query_recommendations():
        raise HTTPException(status_code=401, detail="Authentication required")
    
    @test_app.put("/api/v1/recommendations/preferences")
    def upsert_preferences():
        raise HTTPException(status_code=401, detail="Authentication required")
    
    @test_app.get("/api/v1/plugins/installed")
    def list_installed_plugins():
        raise HTTPException(status_code=401, detail="Authentication required")
    
    @test_app.get("/api/v1/plugins/sources")
    def list_plugin_sources():
        raise HTTPException(status_code=401, detail="Authentication required")
    
    @test_app.post("/api/v1/plugins/sources")
    def create_plugin_source():
        raise HTTPException(status_code=401, detail="Authentication required")
    
    @test_app.delete("/api/v1/plugins/sources/{name}")
    def delete_plugin_source(name: str):
        raise HTTPException(status_code=401, detail="Authentication required")
    
    @test_app.get("/api/v1/plugins/settings/{plugin_name}")
    def list_plugin_settings(plugin_name: str):
        raise HTTPException(status_code=401, detail="Authentication required")
    
    @test_app.put("/api/v1/plugins/settings/{plugin_name}/{key}")
    def upsert_plugin_setting(plugin_name: str, key: str):
        raise HTTPException(status_code=401, detail="Authentication required")
    
    @test_app.get("/api/v1/plugins/system/settings")
    def list_system_settings():
        raise HTTPException(status_code=401, detail="Authentication required")
    
    @test_app.get("/api/v1/system/health")
    def system_health():
        raise HTTPException(status_code=401, detail="Authentication required")
    
    @test_app.post("/api/v1/interactions/sync")
    def sync_events():
        raise HTTPException(status_code=401, detail="Authentication required")
    
    # Create test client
    with TestClient(test_app) as client:
        yield client


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
def submit_task_helper(client_with_database):
    """Fixture providing task submission helper."""
    def _call(action_id: str, context: dict, params: dict | None = None, priority: str | None = None):
        return submit_task(client_with_database, action_id, context, params, priority)
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
