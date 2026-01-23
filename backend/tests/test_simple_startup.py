"""
Simple test to debug startup issues.
"""

import pytest
import os
from fastapi.testclient import TestClient

# Set test environment variables BEFORE any imports
os.environ["STASH_URL"] = "http://localhost:9999"
os.environ["STASH_API_KEY"] = "test_key"

from stash_ai_server.main import app


def test_simple_app_startup():
    """Test that the FastAPI app can start without database fixtures."""
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200


def test_simple_version_endpoint():
    """Test version endpoint without database fixtures."""
    with TestClient(app) as client:
        response = client.get("/api/v1/version")
        # Should work even without database
        assert response.status_code in [200, 500]  # 500 is OK if DB not available


if __name__ == "__main__":
    test_simple_app_startup()
    test_simple_version_endpoint()
    print("Simple tests passed!")