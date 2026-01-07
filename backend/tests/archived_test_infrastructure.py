"""Test infrastructure validation."""

import pytest
import pytest_asyncio
from pathlib import Path

from tests.config import test_config
from tests.database import db_manager
from tests.async_utils import AsyncTestClient, TaskManagerTestUtils


class TestInfrastructure:
    """Test the test infrastructure itself."""
    
    def test_config_creation(self):
        """Test that test configuration is created correctly."""
        assert test_config.database_url.startswith("postgresql+psycopg://")
        assert "test" in test_config.database_name
        assert test_config.plugin_directory.exists()
        assert test_config.task_debug is True
        assert test_config.task_loop_interval == 0.01
    
    def test_plugin_directory_structure(self):
        """Test that test plugin directory structure is correct."""
        plugin_dir = test_config.plugin_directory
        assert plugin_dir.exists()
        assert (plugin_dir / "__init__.py").exists()
        
        # Check base test plugin exists
        base_plugin_dir = plugin_dir / "base_test_plugin"
        assert base_plugin_dir.exists()
        assert (base_plugin_dir / "plugin.yml").exists()
        assert (base_plugin_dir / "plugin.py").exists()
    
    @pytest_asyncio.fixture
    async def test_database_fixture(self, test_database):
        """Test that database fixture works correctly."""
        assert test_database is not None
        assert test_database.test_engine is not None
        return test_database
    
    async def test_database_isolation(self, test_database_fixture):
        """Test that database isolation works correctly."""
        # Test that we can create and use database connections
        async with test_database_fixture.get_test_session() as session:
            assert session is not None
            # Test basic database operation
            result = session.execute("SELECT 1 as test_value")
            row = result.fetchone()
            assert row[0] == 1
    
    async def test_async_database_session(self, async_db_session):
        """Test async database session fixture."""
        assert async_db_session is not None
        # Test basic async database operation
        result = await async_db_session.execute("SELECT 1 as test_value")
        row = result.fetchone()
        assert row[0] == 1
    
    async def test_clean_database_fixture(self, clean_database):
        """Test clean database fixture."""
        # This test just verifies the fixture runs without error
        # The actual cleaning is tested by ensuring tests don't interfere with each other
        pass
    
    async def test_async_test_client(self, client):
        """Test async test client functionality."""
        async_client = AsyncTestClient(client.app)
        
        # Test basic HTTP request
        response = await async_client.async_request("GET", "/api/v1/health")
        assert response.status_code == 200
    
    def test_task_manager_utils(self):
        """Test task manager utilities."""
        utils = TaskManagerTestUtils()
        
        # Test that utilities are available
        assert hasattr(utils, 'wait_for_task_completion')
        assert hasattr(utils, 'assert_task_status')
        assert hasattr(utils, 'wait_for_task_count')
        assert hasattr(utils, 'get_task_history')
        assert hasattr(utils, 'clear_task_history')
    
    def test_environment_overrides(self):
        """Test that environment overrides are applied correctly."""
        import os
        
        # Test that test environment variables are set
        assert os.getenv('TASK_DEBUG') == '1'
        assert os.getenv('TASK_LOOP_INTERVAL') == '0.01'
        assert os.getenv('AI_SERVER_LOG_LEVEL') == 'DEBUG'
        assert os.getenv('AI_SERVER_PLUGINS_DIR') == str(test_config.plugin_directory)
    
    async def test_isolated_plugin_dir(self, isolated_plugin_dir):
        """Test isolated plugin directory fixture."""
        assert isolated_plugin_dir.exists()
        assert isolated_plugin_dir.is_dir()
        
        # Test that we can create files in the isolated directory
        test_file = isolated_plugin_dir / "test.txt"
        test_file.write_text("test content")
        assert test_file.exists()
        assert test_file.read_text() == "test content"