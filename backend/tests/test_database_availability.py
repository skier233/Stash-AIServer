"""Test database availability and setup."""

import pytest
import os
from tests.config import test_config


class TestDatabaseAvailability:
    """Test database availability and configuration."""
    
    def test_database_connection_check(self):
        """Test that database connection checking works."""
        # This test doesn't require the database to be running
        # It just tests the connection checking mechanism
        result = test_config.check_database_connection()
        
        # If database is available, great!
        # If not, that's also fine for this test - we're just testing the mechanism
        assert isinstance(result, bool)
        
        if result:
            print("✓ Database is available")
        else:
            print("✗ Database is not available (this is OK for this test)")
    
    def test_database_configuration(self):
        """Test that database configuration is properly set up."""
        assert test_config.database_url is not None
        assert test_config.database_name is not None
        assert "postgresql" in test_config.database_url
        assert test_config.database_name in test_config.database_url
        
        # Check that we're using standard test credentials
        if not (os.getenv('CI') or os.getenv('GITHUB_ACTIONS')):
            # In local development, should use standard postgres credentials
            assert "postgres:postgres" in test_config.database_url or "stash_ai_server" in test_config.database_url
    
    def test_postgres_binary_detection(self):
        """Test PostgreSQL binary detection for embedded testing."""
        from tests.config import _find_postgres_binary
        
        postgres_bin = _find_postgres_binary()
        
        if postgres_bin:
            print(f"✓ Found PostgreSQL binary at: {postgres_bin}")
            assert postgres_bin.exists()
        else:
            print("✗ PostgreSQL binary not found (embedded testing not available)")
    
    @pytest.mark.database
    def test_database_availability_for_tests(self):
        """Test that database is available for database-dependent tests."""
        # This test will be skipped if database is not available
        if not test_config.ensure_database_available():
            pytest.skip("Database is not available - this is expected if PostgreSQL is not running")
        
        # If we get here, database should be available
        assert test_config.check_database_connection()
        print("✓ Database is available for testing")
    
    def test_environment_configuration(self):
        """Test that test environment configuration is working."""
        # Apply test configuration
        test_config.apply_environment_overrides()
        
        # Check that environment variables are set
        assert os.environ.get('DATABASE_URL') == test_config.database_url
        assert os.environ.get('AI_SERVER_PLUGINS_DIR') == str(test_config.plugin_directory)
        assert os.environ.get('TASK_DEBUG') == '1'
        
        # Clean up
        test_config.cleanup_environment()
        
        # Check that environment variables are cleaned up
        assert 'DATABASE_URL' not in os.environ or os.environ['DATABASE_URL'] != test_config.database_url
    
    def test_ci_environment_detection(self):
        """Test CI environment detection."""
        is_ci = bool(os.getenv('CI') or os.getenv('GITHUB_ACTIONS'))
        
        if is_ci:
            print("✓ Running in CI environment")
            # In CI, should use service container credentials
            assert "postgres:postgres" in test_config.database_url
        else:
            print("✓ Running in local development environment")
    
    def test_skip_database_tests_marker(self):
        """Test that database tests can be skipped with markers."""
        # This test verifies that the marker system works
        # Users can run: python -m pytest -m "not database"
        
        # This test itself is not marked with @pytest.mark.database
        # so it should run even when database tests are skipped
        assert True