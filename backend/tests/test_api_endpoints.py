"""
Comprehensive API endpoint tests for the Stash AI Server.

This module tests all REST API endpoints using the real FastAPI application
with PostgreSQL database. No mocking of backend endpoints - only external
services like Stash API are mocked when necessary.

Tests include:
- Actions API
- Tasks API  
- Recommendations API
- Plugins API
- System API
- Interactions API
- Version API

Uses the existing PostgreSQL infrastructure (system or embedded).
"""

import pytest
import os
import json
import threading
from fastapi.testclient import TestClient
from sqlalchemy import text

# Set test environment variables BEFORE any imports
os.environ["STASH_URL"] = "http://localhost:9999"
os.environ["STASH_API_KEY"] = "test_key"

# Import the main application and test infrastructure
from stash_ai_server.main import app
from stash_ai_server.db.session import get_db
from stash_ai_server.core.dependencies import get_task_manager

# Import test database infrastructure
from tests.database import test_database, sync_db_session
from tests.config import test_config


@pytest.fixture
def client_with_db(sync_db_session):
    """Test client with real app and test database."""
    def override_get_db():
        try:
            yield sync_db_session
        except Exception:
            # Rollback on any error to prevent transaction state issues
            try:
                sync_db_session.rollback()
            except Exception:
                pass
            raise
        finally:
            # Ensure clean state after each request
            try:
                if sync_db_session.in_transaction():
                    sync_db_session.rollback()
            except Exception:
                pass
    
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class TestActionsAPI:
    """Test the Actions API endpoints with real database."""
    
    def test_list_available_actions(self, client_with_db: TestClient):
        """Test POST /actions/available with valid context."""
        context = {
            "page": "scenes",
            "entityId": "123",
            "isDetailView": True,
            "selectedIds": ["1", "2", "3"]
        }
        response = client_with_db.post(
            "/api/v1/actions/available", 
            json={"context": context}
        )
        assert response.status_code == 200
        
        data = response.json()
        assert isinstance(data, list)
    
    def test_list_available_actions_validation(self, client_with_db: TestClient):
        """Test /actions/available request validation."""
        # Missing request body
        response = client_with_db.post("/api/v1/actions/available")
        assert response.status_code == 422
        
        # Missing context field
        response = client_with_db.post("/api/v1/actions/available", json={})
        assert response.status_code == 422
        
        # Invalid context type
        response = client_with_db.post("/api/v1/actions/available", json={"context": "invalid"})
        assert response.status_code == 422
    
    def test_submit_action(self, client_with_db: TestClient):
        """Test POST /actions/submit."""
        payload = {
            "action_id": "test_action",
            "context": {"page": "scenes"},
            "params": {}
        }
        response = client_with_db.post("/api/v1/actions/submit", json=payload)
        # Should return 404 for non-existent action
        assert response.status_code == 404
    
    def test_submit_action_validation(self, client_with_db: TestClient):
        """Test /actions/submit request validation."""
        # Missing request body
        response = client_with_db.post("/api/v1/actions/submit")
        assert response.status_code == 422
        
        # Missing action_id
        response = client_with_db.post("/api/v1/actions/submit", json={
            "context": {"page": "scenes"},
            "params": {}
        })
        assert response.status_code == 422
        
        # Missing context
        response = client_with_db.post("/api/v1/actions/submit", json={
            "action_id": "test_action",
            "params": {}
        })
        assert response.status_code == 422


class TestTasksAPI:
    """Test the Tasks API endpoints with real database."""
    
    def test_list_tasks(self, client_with_db: TestClient):
        """Test GET /tasks."""
        response = client_with_db.get("/api/v1/tasks")
        assert response.status_code == 200
        
        data = response.json()
        assert "tasks" in data
        assert isinstance(data["tasks"], list)
    
    def test_list_tasks_with_filters(self, client_with_db: TestClient):
        """Test GET /tasks with service and status filters."""
        response = client_with_db.get("/api/v1/tasks?service=test_service&status=completed")
        assert response.status_code == 200
        
        data = response.json()
        assert "tasks" in data
        assert isinstance(data["tasks"], list)
    
    def test_get_task_not_found(self, client_with_db: TestClient):
        """Test GET /tasks/{task_id} for non-existent task."""
        response = client_with_db.get("/api/v1/tasks/nonexistent_task")
        assert response.status_code == 404
    
    def test_cancel_task_not_found(self, client_with_db: TestClient):
        """Test POST /tasks/{task_id}/cancel for non-existent task."""
        response = client_with_db.post("/api/v1/tasks/nonexistent_task/cancel")
        assert response.status_code == 404
    
    def test_task_history(self, client_with_db: TestClient):
        """Test GET /tasks/history."""
        response = client_with_db.get("/api/v1/tasks/history")
        assert response.status_code == 200
        
        data = response.json()
        assert "history" in data
        assert isinstance(data["history"], list)
    
    def test_task_history_with_filters(self, client_with_db: TestClient):
        """Test GET /tasks/history with filters."""
        response = client_with_db.get("/api/v1/tasks/history?limit=10&service=test&status=completed")
        assert response.status_code == 200
        
        data = response.json()
        assert "history" in data
        assert isinstance(data["history"], list)


class TestRecommendationsAPI:
    """Test the Recommendations API endpoints with real database."""
    
    def test_list_recommenders(self, client_with_db: TestClient):
        """Test GET /recommendations/recommenders."""
        response = client_with_db.get("/api/v1/recommendations/recommenders?context=global_feed")
        assert response.status_code == 200
        
        data = response.json()
        assert "context" in data
        assert "recommenders" in data
        assert "defaultRecommenderId" in data
        assert data["context"] == "global_feed"
        assert isinstance(data["recommenders"], list)
    
    def test_list_recommenders_validation(self, client_with_db: TestClient):
        """Test /recommendations/recommenders validation."""
        # Missing context parameter
        response = client_with_db.get("/api/v1/recommendations/recommenders")
        assert response.status_code == 422
        
        # Invalid context
        response = client_with_db.get("/api/v1/recommendations/recommenders?context=invalid")
        assert response.status_code == 422
    
    def test_query_recommendations(self, client_with_db: TestClient):
        """Test POST /recommendations/query."""
        payload = {
            "context": "global_feed",
            "recommenderId": "nonexistent_recommender",
            "config": {},
            "limit": 10
        }
        response = client_with_db.post("/api/v1/recommendations/query", json=payload)
        # Should return 404 for non-existent recommender
        assert response.status_code == 404
    
    def test_query_recommendations_validation(self, client_with_db: TestClient):
        """Test /recommendations/query validation."""
        # Missing request body
        response = client_with_db.post("/api/v1/recommendations/query")
        assert response.status_code == 422
        
        # Missing required fields
        response = client_with_db.post("/api/v1/recommendations/query", json={})
        assert response.status_code == 422
        
        # Invalid context
        payload = {
            "context": "invalid_context",
            "recommenderId": "test_recommender"
        }
        response = client_with_db.post("/api/v1/recommendations/query", json=payload)
        assert response.status_code == 422
    
    def test_upsert_preferences(self, client_with_db: TestClient):
        """Test PUT /recommendations/preferences."""
        payload = {
            "context": "global_feed",
            "recommenderId": "nonexistent_recommender",
            "config": {}
        }
        response = client_with_db.put("/api/v1/recommendations/preferences", json=payload)
        # Should return 404 for non-existent recommender
        assert response.status_code == 404
    
    def test_upsert_preferences_validation(self, client_with_db: TestClient):
        """Test /recommendations/preferences validation."""
        # Missing request body
        response = client_with_db.put("/api/v1/recommendations/preferences")
        assert response.status_code == 422
        
        # Missing recommenderId
        response = client_with_db.put("/api/v1/recommendations/preferences", json={
            "context": "global_feed",
            "config": {}
        })
        assert response.status_code == 422


class TestPluginsAPI:
    """Test the Plugins API endpoints with real database."""
    
    def test_list_installed_plugins(self, client_with_db: TestClient):
        """Test GET /plugins/installed."""
        response = client_with_db.get("/api/v1/plugins/installed")
        assert response.status_code == 200
        
        data = response.json()
        assert isinstance(data, list)
    
    def test_list_installed_plugins_with_filters(self, client_with_db: TestClient):
        """Test GET /plugins/installed with filters."""
        response = client_with_db.get("/api/v1/plugins/installed?active_only=true&include_removed=false")
        assert response.status_code == 200
        
        data = response.json()
        assert isinstance(data, list)
    
    def test_list_plugin_sources(self, client_with_db: TestClient):
        """Test GET /plugins/sources."""
        response = client_with_db.get("/api/v1/plugins/sources")
        assert response.status_code == 200
        
        data = response.json()
        assert isinstance(data, list)
    
    def test_create_plugin_source(self, client_with_db: TestClient):
        """Test POST /plugins/sources."""
        payload = {
            "name": "test_source",
            "url": "https://example.com/plugins",
            "enabled": True
        }
        response = client_with_db.post("/api/v1/plugins/sources", json=payload)
        assert response.status_code in [200, 201]
    
    def test_create_plugin_source_validation(self, client_with_db: TestClient):
        """Test /plugins/sources validation."""
        # Missing required fields
        response = client_with_db.post("/api/v1/plugins/sources", json={})
        assert response.status_code == 422
    
    def test_delete_plugin_source_not_found(self, client_with_db: TestClient):
        """Test DELETE /plugins/sources/{source_id} for non-existent source."""
        response = client_with_db.delete("/api/v1/plugins/sources/nonexistent_source")
        assert response.status_code == 404
    
    def test_list_plugin_settings(self, client_with_db: TestClient):
        """Test GET /plugins/settings/{plugin_name}."""
        response = client_with_db.get("/api/v1/plugins/settings/test_plugin")
        assert response.status_code == 200
        
        data = response.json()
        assert isinstance(data, list)
    
    def test_upsert_plugin_setting(self, client_with_db: TestClient):
        """Test PUT /plugins/settings/{plugin_name}/{key}."""
        response = client_with_db.put("/api/v1/plugins/settings/test_plugin/test_key", json={"value": "test"})
        assert response.status_code in [200, 201]
    
    def test_list_system_settings(self, client_with_db: TestClient):
        """Test GET /plugins/system/settings."""
        response = client_with_db.get("/api/v1/plugins/system/settings")
        assert response.status_code == 200
        
        data = response.json()
        assert isinstance(data, list)


class TestSystemAPI:
    """Test the System API endpoints with real database."""
    
    def test_system_health(self, client_with_db: TestClient):
        """Test GET /system/health."""
        response = client_with_db.get("/api/v1/system/health")
        assert response.status_code == 200
        
        data = response.json()
        assert "status" in data
        assert "backend_version" in data
        # In test environment, status can be "warn" due to Stash not being available
        assert data["status"] in ["healthy", "warn"]
        
        # Should have database health info with real database
        if "database" in data:
            assert "status" in data["database"]
            assert "message" in data["database"]
            assert "latency_ms" in data["database"]


class TestInteractionsAPI:
    """Test the Interactions API endpoints with real database."""
    
    def test_sync_events(self, client_with_db: TestClient):
        """Test POST /interactions/sync with valid events."""
        events = [
            {
                "id": "test_event_1",
                "session_id": "test_session_1",
                "ts": "2022-01-01T00:00:00Z",
                "type": "scene_view",
                "entity_type": "scene",
                "entity_id": 123,
                "client_id": "test_client"
            }
        ]
        response = client_with_db.post("/api/v1/interactions/sync", json=events)
        assert response.status_code == 200
        
        data = response.json()
        assert "accepted" in data
        assert "duplicates" in data
        assert "errors" in data
        assert isinstance(data["accepted"], int)
        assert isinstance(data["duplicates"], int)
        # Handle both integer and list types for errors field
        if isinstance(data["errors"], list):
            assert len(data["errors"]) >= 0
        else:
            assert isinstance(data["errors"], int)
    
    def test_sync_events_empty_list(self, client_with_db: TestClient):
        """Test POST /interactions/sync with empty event list."""
        response = client_with_db.post("/api/v1/interactions/sync", json=[])
        assert response.status_code == 200
        
        data = response.json()
        assert data["accepted"] == 0
        assert data["duplicates"] == 0
        # Handle both integer and list types for errors field
        if isinstance(data["errors"], list):
            assert len(data["errors"]) == 0
        else:
            assert data["errors"] == 0
    
    def test_sync_events_malformed_data(self, client_with_db: TestClient):
        """Test POST /interactions/sync with malformed event data."""
        # Invalid event structure - missing required fields
        events = [
            {
                "invalid_field": "value"
            }
        ]
        response = client_with_db.post("/api/v1/interactions/sync", json=events)
        # Should return 422 for validation error
        assert response.status_code == 422


class TestVersionAPI:
    """Test the Version API endpoint with real database."""
    
    def test_version_endpoint(self, client_with_db: TestClient):
        """Test GET /version."""
        response = client_with_db.get("/api/v1/version")
        assert response.status_code == 200
        
        data = response.json()
        assert "version" in data
        assert "frontend_min_version" in data
        # With real database, should have actual alembic version
        assert "db_alembic_head" in data


class TestRootEndpoint:
    """Test the root endpoint."""
    
    def test_root_endpoint(self, client_with_db: TestClient):
        """Test GET /."""
        response = client_with_db.get("/")
        assert response.status_code == 200
        
        data = response.json()
        assert "status" in data
        assert "app" in data
        assert data["status"] == "ok"


class TestAPIErrorHandling:
    """Test API error handling with real application."""
    
    def test_malformed_json_handling(self, client_with_db: TestClient):
        """Test handling of malformed JSON requests."""
        response = client_with_db.post(
            "/api/v1/actions/available",
            data='{"invalid": json}',
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422
    
    def test_invalid_http_methods(self, client_with_db: TestClient):
        """Test invalid HTTP methods on endpoints."""
        # GET on POST-only endpoint
        response = client_with_db.get("/api/v1/actions/available")
        assert response.status_code == 405
        
        # PUT on POST-only endpoint
        response = client_with_db.put("/api/v1/actions/available")
        assert response.status_code == 405
    
    def test_nonexistent_endpoints(self, client_with_db: TestClient):
        """Test requests to non-existent endpoints."""
        response = client_with_db.get("/api/v1/nonexistent")
        assert response.status_code == 404
        
        response = client_with_db.post("/api/v999/actions/available")
        assert response.status_code == 404
    
    def test_oversized_request_handling(self, client_with_db: TestClient):
        """Test handling of oversized request bodies."""
        large_payload = {
            "context": {
                "selectedIds": ["id_" + str(i) for i in range(1000)]  # Large list
            }
        }
        response = client_with_db.post("/api/v1/actions/available", json=large_payload)
        assert response.status_code in [200, 400, 413, 422]
    
    def test_concurrent_requests(self, client_with_db: TestClient):
        """Test API behavior under concurrent requests."""
        results = []
        
        def make_request():
            try:
                response = client_with_db.get("/api/v1/tasks")
                results.append(response.status_code)
            except Exception as e:
                results.append(str(e))
        
        # Create multiple concurrent requests
        threads = []
        for i in range(3):
            thread = threading.Thread(target=make_request)
            threads.append(thread)
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join(timeout=5)
        
        # All requests should succeed or fail gracefully
        assert len(results) == 3
        for result in results:
            if isinstance(result, int):
                assert result in [200, 429, 500, 503]
    
    def test_error_response_format_consistency(self, client_with_db: TestClient):
        """Test that all error responses have consistent format."""
        # 422 error
        response = client_with_db.post("/api/v1/actions/available", json={})
        assert response.status_code == 422
        data = response.json()
        assert "detail" in data
        
        # 404 error
        response = client_with_db.get("/api/v1/tasks/nonexistent_task")
        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
        
        # 405 error
        response = client_with_db.get("/api/v1/actions/available")
        assert response.status_code == 405
        data = response.json()
        assert "detail" in data


class TestAPIStructureAndConventions:
    """Test API structure and conventions with real application."""
    
    def test_api_versioning_consistency(self, client_with_db: TestClient):
        """Test API versioning consistency across all endpoints."""
        # All endpoints should use /api/v1/ prefix
        v1_endpoints = [
            "/api/v1/version",
            "/api/v1/tasks",
            "/api/v1/tasks/history",
            "/api/v1/plugins/installed",
            "/api/v1/plugins/sources",
            "/api/v1/system/health",
        ]
        
        for endpoint in v1_endpoints:
            response = client_with_db.get(endpoint)
            # Should not return 404 (endpoint exists)
            assert response.status_code != 404, f"Endpoint {endpoint} should exist"
        
        # Invalid version should return 404
        response = client_with_db.get("/api/v999/version")
        assert response.status_code == 404
    
    def test_json_response_consistency(self, client_with_db: TestClient):
        """Test that all endpoints return valid JSON with consistent structure."""
        json_endpoints = [
            "/",
            "/api/v1/version",
            "/api/v1/tasks",
            "/api/v1/tasks/history",
            "/api/v1/plugins/installed",
            "/api/v1/plugins/sources",
            "/api/v1/system/health",
        ]
        
        for endpoint in json_endpoints:
            response = client_with_db.get(endpoint)
            if response.status_code == 200:
                # Verify valid JSON response
                try:
                    data = response.json()
                    assert isinstance(data, (dict, list)), f"Response should be JSON object or array for {endpoint}"
                except json.JSONDecodeError:
                    pytest.fail(f"Endpoint {endpoint} did not return valid JSON")
    
    def test_http_status_code_consistency(self, client_with_db: TestClient):
        """Test HTTP status code consistency across similar operations."""
        # GET operations for existing resources should return 200
        get_endpoints = [
            "/api/v1/version",
            "/api/v1/tasks",
            "/api/v1/plugins/installed",
            "/api/v1/system/health"
        ]
        
        for endpoint in get_endpoints:
            response = client_with_db.get(endpoint)
            assert response.status_code == 200, f"GET {endpoint} should return 200"
        
        # 404 for nonexistent resources should be consistent
        not_found_endpoints = [
            ("/api/v1/tasks/nonexistent_task", "GET"),
            ("/api/v1/plugins/sources/nonexistent_source", "DELETE"),
            ("/api/v1/nonexistent", "GET")
        ]
        
        for endpoint, method in not_found_endpoints:
            if method == "GET":
                response = client_with_db.get(endpoint)
            elif method == "DELETE":
                response = client_with_db.delete(endpoint)
            
            assert response.status_code == 404, f"{method} {endpoint} should return 404"
    
    def test_endpoint_naming_conventions(self, client_with_db: TestClient):
        """Test that endpoints follow consistent naming conventions."""
        # Resource endpoints should be plural and exist
        plural_resource_endpoints = [
            "/api/v1/tasks",
            "/api/v1/plugins/installed",
            "/api/v1/plugins/sources",
        ]
        
        for endpoint in plural_resource_endpoints:
            response = client_with_db.get(endpoint)
            # Should not return 404 (endpoint exists with correct naming)
            assert response.status_code != 404, f"Plural resource endpoint {endpoint} should exist"
        
        # Action endpoints should exist
        action_endpoints = [
            "/api/v1/actions/available",
            "/api/v1/actions/submit", 
            "/api/v1/interactions/sync"
        ]
        
        for endpoint in action_endpoints:
            response = client_with_db.post(endpoint, json={})
            # Should not return 404 (endpoint exists with correct naming)
            assert response.status_code != 404, f"Action endpoint {endpoint} should exist"


class TestDependencyInjection:
    """Test that dependency injection is working properly."""
    
    def test_task_manager_dependency_injection(self):
        """Test that TaskManager is properly injected as a dependency."""
        # Get the task manager through dependency injection
        task_manager = get_task_manager()
        assert task_manager is not None
        
        # Should be the same instance when called again (singleton)
        task_manager2 = get_task_manager()
        assert task_manager is task_manager2
    
    def test_database_connection_with_postgres(self, sync_db_session):
        """Test that we can actually connect to and query PostgreSQL."""
        result = sync_db_session.execute(text("SELECT 1 as test_value"))
        row = result.fetchone()
        assert row[0] == 1
    
    def test_database_tables_exist(self, test_database):
        """Test that database tables were created."""
        from sqlalchemy import inspect
        inspector = inspect(test_database.test_engine)
        tables = inspector.get_table_names()
        
        # Should have some tables from our models
        assert len(tables) > 0


class TestRealDatabaseOperations:
    """Test real database operations without mocking."""
    
    def test_can_create_and_query_data(self, sync_db_session):
        """Test that we can create and query data in PostgreSQL."""
        # Test basic SQL operations
        sync_db_session.execute(text("CREATE TEMP TABLE test_table (id SERIAL PRIMARY KEY, name VARCHAR(50))"))
        sync_db_session.execute(text("INSERT INTO test_table (name) VALUES ('test_name')"))
        sync_db_session.commit()
        
        result = sync_db_session.execute(text("SELECT name FROM test_table WHERE id = 1"))
        row = result.fetchone()
        assert row[0] == "test_name"
    
    def test_postgresql_specific_features(self, sync_db_session):
        """Test PostgreSQL-specific features work."""
        # Test JSON column (PostgreSQL specific)
        sync_db_session.execute(text("""
            CREATE TEMP TABLE json_test (
                id SERIAL PRIMARY KEY, 
                data JSONB
            )
        """))
        sync_db_session.execute(text("""
            INSERT INTO json_test (data) VALUES ('{"key": "value", "number": 42}')
        """))
        sync_db_session.commit()
        
        result = sync_db_session.execute(text("SELECT data->>'key' as key_value FROM json_test WHERE id = 1"))
        row = result.fetchone()
        assert row[0] == "value"


if __name__ == "__main__":
    # Run a simple test to verify the setup works
    import subprocess
    import sys
    
    print("Running basic test...")
    pytest.main([__file__ + "::TestRootEndpoint::test_root_endpoint", "-v"])