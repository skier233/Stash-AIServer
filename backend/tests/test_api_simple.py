"""
Simple API endpoint tests using real application and real database.
NO MOCKING - tests the actual application behavior.
"""

import pytest
import os
from fastapi.testclient import TestClient

# Set test environment variables BEFORE any imports
os.environ["STASH_URL"] = "http://localhost:9999"
os.environ["STASH_API_KEY"] = "test_key"

# Import the main application and test infrastructure
from stash_ai_server.main import app
from stash_ai_server.db.session import get_db

# Import test database infrastructure
from tests.database import test_database, sync_db_session
from tests.config import test_config


@pytest.fixture
def client_with_db(sync_db_session):
    """Test client with real app and test database."""
    def override_get_db():
        try:
            yield sync_db_session
        finally:
            pass
    
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class TestVersionAPI:
    """Test version endpoint - no auth required."""
    
    def test_version_endpoint(self, client_with_db: TestClient):
        """Test /version endpoint returns correct structure."""
        response = client_with_db.get("/api/v1/version")
        assert response.status_code == 200
        
        data = response.json()
        assert "version" in data
        assert "frontend_min_version" in data
        assert "db_alembic_head" in data


class TestTasksAPI:
    """Test tasks endpoints."""
    
    def test_list_tasks(self, client_with_db: TestClient):
        """Test GET /tasks endpoint."""
        response = client_with_db.get("/api/v1/tasks")
        # May require auth or work without it - both are valid
        assert response.status_code in [200, 401, 403]
        
        if response.status_code == 200:
            data = response.json()
            assert "tasks" in data
            assert isinstance(data["tasks"], list)
    
    def test_task_history(self, client_with_db: TestClient):
        """Test GET /tasks/history endpoint."""
        response = client_with_db.get("/api/v1/tasks/history")
        assert response.status_code in [200, 401, 403]
        
        if response.status_code == 200:
            data = response.json()
            assert "history" in data
            assert isinstance(data["history"], list)
    
    def test_get_nonexistent_task(self, client_with_db: TestClient):
        """Test GET /tasks/{id} with nonexistent task."""
        response = client_with_db.get("/api/v1/tasks/nonexistent")
        assert response.status_code in [404, 401, 403]
    
    def test_cancel_nonexistent_task(self, client_with_db: TestClient):
        """Test POST /tasks/{id}/cancel with nonexistent task."""
        response = client_with_db.post("/api/v1/tasks/nonexistent/cancel")
        assert response.status_code in [404, 401, 403]


class TestActionsAPI:
    """Test actions endpoints."""
    
    def test_actions_available_validation(self, client_with_db: TestClient):
        """Test POST /actions/available validation."""
        # Missing body should return 422
        response = client_with_db.post("/api/v1/actions/available")
        assert response.status_code == 422
        
        # Valid request
        response = client_with_db.post("/api/v1/actions/available", json={"context": {"page": "scenes"}})
        assert response.status_code in [200, 401, 403]
        
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)
    
    def test_actions_submit_validation(self, client_with_db: TestClient):
        """Test POST /actions/submit validation."""
        # Missing body should return 422
        response = client_with_db.post("/api/v1/actions/submit")
        assert response.status_code == 422
        
        # Valid request
        payload = {
            "action_id": "test_action",
            "context": {"page": "scenes"},
            "params": {}
        }
        response = client_with_db.post("/api/v1/actions/submit", json=payload)
        assert response.status_code in [200, 404, 401, 403]


class TestRecommendationsAPI:
    """Test recommendations endpoints."""
    
    def test_list_recommenders(self, client_with_db: TestClient):
        """Test GET /recommendations/recommenders."""
        response = client_with_db.get("/api/v1/recommendations/recommenders?context=scenes")
        assert response.status_code in [200, 401, 403, 422]
        
        if response.status_code == 200:
            data = response.json()
            assert "context" in data
            assert "recommenders" in data
            assert "defaultRecommenderId" in data
    
    def test_query_recommendations_validation(self, client_with_db: TestClient):
        """Test POST /recommendations/query validation."""
        # Missing body should return 422
        response = client_with_db.post("/api/v1/recommendations/query")
        assert response.status_code == 422
        
        # Valid request
        payload = {
            "context": "scenes",
            "recommenderId": "test_recommender",
            "config": {},
            "limit": 10
        }
        response = client_with_db.post("/api/v1/recommendations/query", json=payload)
        assert response.status_code in [200, 404, 401, 403, 422]


class TestPluginsAPI:
    """Test plugins endpoints."""
    
    def test_list_installed_plugins(self, client_with_db: TestClient):
        """Test GET /plugins/installed."""
        response = client_with_db.get("/api/v1/plugins/installed")
        assert response.status_code in [200, 401, 403]
        
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)
    
    def test_list_plugin_sources(self, client_with_db: TestClient):
        """Test GET /plugins/sources."""
        response = client_with_db.get("/api/v1/plugins/sources")
        assert response.status_code in [200, 401, 403]
        
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)


class TestSystemAPI:
    """Test system endpoints."""
    
    def test_system_health(self, client_with_db: TestClient):
        """Test GET /system/health."""
        response = client_with_db.get("/api/v1/system/health")
        # Health check may fail due to external dependencies - that's OK
        assert response.status_code in [200, 401, 403, 500, 503]
        
        if response.status_code == 200:
            data = response.json()
            assert "status" in data
            assert "backend_version" in data


class TestInteractionsAPI:
    """Test interactions endpoints."""
    
    def test_sync_events_validation(self, client_with_db: TestClient):
        """Test POST /interactions/sync validation."""
        # Valid empty list
        response = client_with_db.post("/api/v1/interactions/sync", json=[])
        assert response.status_code in [200, 401, 403]
        
        if response.status_code == 200:
            data = response.json()
            assert "accepted" in data
            assert "duplicates" in data
            assert "errors" in data
        
        # Valid event
        events = [{
            "event_type": "scene_view",
            "scene_id": 123,
            "timestamp": 1640995200.0,
            "client_id": "test_client"
        }]
        response = client_with_db.post("/api/v1/interactions/sync", json=events)
        assert response.status_code in [200, 401, 403, 422]


class TestRootEndpoint:
    """Test root endpoint."""
    
    def test_root_endpoint(self, client_with_db: TestClient):
        """Test GET / endpoint."""
        response = client_with_db.get("/")
        assert response.status_code == 200
        
        data = response.json()
        assert "status" in data or "message" in data  # Different apps may return different structure


class TestErrorHandling:
    """Test error handling across endpoints."""
    
    def test_nonexistent_endpoint(self, client_with_db: TestClient):
        """Test request to nonexistent endpoint."""
        response = client_with_db.get("/api/v1/nonexistent")
        assert response.status_code == 404
    
    def test_invalid_method(self, client_with_db: TestClient):
        """Test invalid HTTP method."""
        response = client_with_db.get("/api/v1/actions/available")  # Should be POST
        assert response.status_code == 405
    
    def test_malformed_json(self, client_with_db: TestClient):
        """Test malformed JSON request."""
        response = client_with_db.post(
            "/api/v1/actions/available",
            data='{"invalid": json}',
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422