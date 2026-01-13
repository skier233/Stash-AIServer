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


@pytest.mark.database
class TestDatabaseMigrations:
    """Test database migration functionality."""
    
    @pytest.fixture(scope="class")
    def migration_runner(self):
        """Provide migration test runner."""
        try:
            runner = MigrationTestRunner(test_config)
            # Test if the runner can create an admin engine (this will check for database availability)
            runner.create_admin_engine()
            yield runner
            runner.cleanup()
        except RuntimeError as e:
            if "PostgreSQL database" in str(e):
                pytest.skip(f"Migration tests require PostgreSQL database: {e}")
            else:
                raise
    
    def test_migration_files_validation(self):
        """Test that migration files are valid."""
        issues = validate_migration_files()
        assert len(issues) == 0, f"Migration file validation issues: {issues}"
    
    def test_migration_upgrade_to_head(self, migration_runner):
        """Test migration upgrade to head revision."""
        expected_tables = get_expected_tables_from_models()
        result = migration_runner.test_migration_upgrade("head", expected_tables)
        
        assert result.success, f"Migration upgrade failed: {result.error_message}"
        
        # If schema validation failed, let's see what tables were actually created
        if not result.schema_validation_passed:
            # Get the actual schema info for debugging
            with migration_runner.isolated_migration_environment("debug_schema") as (db_url, db_name):
                # Run migration again to get schema info
                migration_runner.run_alembic_command(['upgrade', 'head'], db_url)
                schema_validation = migration_runner.validate_schema_after_migration(db_url, expected_tables)
                
                print(f"\nSchema validation details:")
                print(f"Expected tables: {expected_tables}")
                print(f"Actual tables: {schema_validation.tables_created}")
                print(f"Missing tables: {schema_validation.missing_tables}")
                print(f"Unexpected tables: {schema_validation.unexpected_tables}")
        
        assert result.schema_validation_passed, "Schema validation failed after migration"
        assert result.execution_time_ms is not None
        assert result.execution_time_ms < 60000, "Migration took too long (>60s)"
    
    def test_migration_idempotency(self, migration_runner):
        """Test that migrations are idempotent."""
        result = migration_runner.test_migration_idempotency("head")
        
        assert result.success, f"Migration idempotency test failed: {result.error_message}"
    
    def test_migration_performance(self, migration_runner):
        """Test migration performance characteristics."""
        result = migration_runner.test_migration_upgrade("head")
        
        assert result.success, "Migration must succeed for performance testing"
        assert result.execution_time_ms is not None
        
        # Performance thresholds
        assert result.execution_time_ms < 60000, f"Migration too slow: {result.execution_time_ms}ms (limit: 60s)"
        
        # Log performance for monitoring
        print(f"Migration execution time: {result.execution_time_ms:.2f}ms")


@pytest.mark.database
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


@pytest.mark.database
class TestMigrationErrorHandling:
    """Test migration error handling and edge cases."""
    
    @pytest.fixture(scope="class")
    def migration_runner(self):
        """Provide migration test runner."""
        try:
            runner = MigrationTestRunner(test_config)
            # Test if the runner can create an admin engine (this will check for database availability)
            runner.create_admin_engine()
            yield runner
            runner.cleanup()
        except RuntimeError as e:
            if "PostgreSQL database" in str(e):
                pytest.skip(f"Migration tests require PostgreSQL database: {e}")
            else:
                raise
    
    def test_migration_with_database_connection_issues(self, migration_runner):
        """Test migration behavior with database connection issues."""
        # Test alembic command with invalid database URL directly
        invalid_db_url = "postgresql://invalid:invalid@localhost:9999/invalid_db"
        
        # This should fail quickly due to connection timeout
        success, output = migration_runner.run_alembic_command(['current'], invalid_db_url)
        
        # Should fail due to connection issues
        assert not success, "Expected alembic command to fail with invalid database URL"
        assert output is not None, "Expected error output from failed alembic command"
        
        # The error should mention connection issues
        error_indicators = ['connection', 'timeout', 'refused', 'failed', 'error']
        assert any(indicator in output.lower() for indicator in error_indicators), \
            f"Expected connection error in output, got: {output}"


@pytest.mark.integration
@pytest.mark.database
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