"""Tests for database migrations."""

import pytest
import pytest_asyncio
from pathlib import Path

from tests.migration_testing import (
    MigrationTestRunner,
    PluginMigrationTester,
    get_expected_tables_from_models,
    validate_migration_files
)
from tests.config import test_config


class TestDatabaseMigrations:
    """Test database migration functionality."""
    
    @pytest.fixture(scope="class")
    def migration_runner(self):
        """Provide migration test runner."""
        runner = MigrationTestRunner(test_config)
        yield runner
        runner.cleanup()
    
    def test_migration_files_validation(self):
        """Test that migration files are valid."""
        issues = validate_migration_files()
        assert len(issues) == 0, f"Migration file validation issues: {issues}"
    
    def test_migration_upgrade_to_head(self, migration_runner):
        """Test migration upgrade to head revision."""
        expected_tables = get_expected_tables_from_models()
        result = migration_runner.test_migration_upgrade("head", expected_tables)
        
        assert result.success, f"Migration upgrade failed: {result.error_message}"
        assert result.schema_validation_passed, "Schema validation failed after migration"
        assert result.execution_time_ms is not None
        assert result.execution_time_ms < 30000, "Migration took too long (>30s)"
    
    def test_migration_idempotency(self, migration_runner):
        """Test that migrations are idempotent."""
        result = migration_runner.test_migration_idempotency("head")
        
        assert result.success, f"Migration idempotency test failed: {result.error_message}"
    
    def test_migration_downgrade(self, migration_runner):
        """Test migration downgrade from head to base."""
        result = migration_runner.test_migration_downgrade("head", "base")
        
        assert result.success, f"Migration downgrade failed: {result.error_message}"
        assert result.rollback_success, "Migration rollback was not successful"
    
    def test_all_migrations_comprehensive(self, migration_runner):
        """Test all migrations comprehensively."""
        expected_tables = get_expected_tables_from_models()
        results = migration_runner.test_all_migrations(expected_tables)
        
        # Check that all tests passed
        failed_results = [r for r in results if not r.success]
        assert len(failed_results) == 0, f"Some migration tests failed: {[r.error_message for r in failed_results]}"
        
        # Check that we have results for key operations
        migration_types = [r.migration_id for r in results]
        assert "head" in migration_types, "Missing head migration test"
    
    def test_migration_performance(self, migration_runner):
        """Test migration performance characteristics."""
        result = migration_runner.test_migration_upgrade("head")
        
        assert result.success, "Migration must succeed for performance testing"
        assert result.execution_time_ms is not None
        
        # Performance thresholds
        assert result.execution_time_ms < 60000, f"Migration too slow: {result.execution_time_ms}ms (limit: 60s)"
        
        # Log performance for monitoring
        print(f"Migration execution time: {result.execution_time_ms:.2f}ms")


class TestPluginMigrations:
    """Test plugin-specific migrations."""
    
    @pytest.fixture(scope="class")
    def plugin_dirs(self):
        """Get available plugin directories."""
        plugins_dir = Path(__file__).parent.parent / "plugins"
        if not plugins_dir.exists():
            return []
        
        return [d for d in plugins_dir.iterdir() if d.is_dir()]
    
    def test_plugin_migration_detection(self, plugin_dirs):
        """Test detection of plugin migrations."""
        for plugin_dir in plugin_dirs:
            plugin_name = plugin_dir.name
            tester = PluginMigrationTester(plugin_name, plugin_dir)
            
            # Test migration detection (should not raise errors)
            has_migrations = tester.has_migrations()
            
            # Log for visibility
            if has_migrations:
                print(f"Plugin {plugin_name} has migrations")
            else:
                print(f"Plugin {plugin_name} has no migrations")
    
    def test_plugin_migrations_if_present(self, plugin_dirs):
        """Test plugin migrations if they exist."""
        for plugin_dir in plugin_dirs:
            plugin_name = plugin_dir.name
            tester = PluginMigrationTester(plugin_name, plugin_dir)
            
            if tester.has_migrations():
                results = tester.test_plugin_migrations()
                
                # Check results if any migrations were tested
                failed_results = [r for r in results if not r.success]
                assert len(failed_results) == 0, f"Plugin {plugin_name} migration tests failed: {[r.error_message for r in failed_results]}"


class TestMigrationErrorHandling:
    """Test migration error handling and edge cases."""
    
    @pytest.fixture(scope="class")
    def migration_runner(self):
        """Provide migration test runner."""
        runner = MigrationTestRunner(test_config)
        yield runner
        runner.cleanup()
    
    def test_invalid_migration_revision(self, migration_runner):
        """Test handling of invalid migration revision."""
        result = migration_runner.test_migration_upgrade("invalid_revision_12345")
        
        # Should fail gracefully
        assert not result.success
        assert result.error_message is not None
        assert "invalid_revision_12345" in result.error_message or "Unknown revision" in result.error_message
    
    def test_migration_with_database_connection_issues(self, migration_runner):
        """Test migration behavior with database connection issues."""
        # Create runner with invalid database configuration
        invalid_config = test_config
        invalid_config.database_url = "postgresql://invalid:invalid@localhost:9999/invalid_db"
        
        invalid_runner = MigrationTestRunner(invalid_config)
        
        try:
            result = invalid_runner.test_migration_upgrade("head")
            
            # Should fail due to connection issues
            assert not result.success
            assert result.error_message is not None
            
        finally:
            invalid_runner.cleanup()


@pytest.mark.integration
class TestMigrationIntegration:
    """Integration tests for migration system."""
    
    def test_migration_with_existing_data(self):
        """Test migrations work correctly with existing data."""
        # This would test that migrations preserve existing data
        # For now, this is a placeholder for more complex integration testing
        pass
    
    def test_migration_rollback_preserves_data(self):
        """Test that migration rollbacks preserve data integrity."""
        # This would test data preservation during rollbacks
        # For now, this is a placeholder for more complex integration testing
        pass