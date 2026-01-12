"""Migration testing system for validating database migrations."""

import os
import tempfile
import subprocess
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from contextlib import contextmanager
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.engine import Engine
from dataclasses import dataclass

from tests.config import test_config

logger = logging.getLogger(__name__)


@dataclass
class MigrationTestResult:
    """Result of migration test execution."""
    migration_id: str
    success: bool
    error_message: Optional[str] = None
    execution_time_ms: Optional[float] = None
    schema_validation_passed: bool = False
    rollback_success: bool = False


@dataclass
class SchemaValidationResult:
    """Result of schema validation after migration."""
    tables_created: List[str]
    indexes_created: List[str]
    constraints_created: List[str]
    expected_tables: List[str]
    missing_tables: List[str]
    unexpected_tables: List[str]
    validation_passed: bool


class MigrationTestRunner:
    """Manages migration testing with isolated databases."""
    
    def __init__(self, config=None):
        self.config = config or test_config
        self.alembic_config_path = Path(__file__).parent.parent / "stash_ai_server" / "alembic.ini"
        self.alembic_dir = Path(__file__).parent.parent / "stash_ai_server" / "alembic"
        self.admin_engine = None
        
    def create_admin_engine(self) -> Engine:
        """Create admin engine for database operations."""
        if not self.admin_engine:
            admin_url = self.config.database_url.replace(f"/{self.config.database_name}", "/postgres")
            self.admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        return self.admin_engine
    
    def create_migration_test_database(self, test_suffix: str) -> str:
        """Create isolated database for migration testing."""
        db_name = f"{self.config.database_name}_migration_{test_suffix}"
        admin_engine = self.create_admin_engine()
        
        with admin_engine.connect() as conn:
            # Drop if exists
            conn.execute(text(f"DROP DATABASE IF EXISTS {db_name}"))
            # Create fresh database
            conn.execute(text(f"CREATE DATABASE {db_name}"))
            logger.info(f"Created migration test database: {db_name}")
        
        return db_name
    
    def drop_migration_test_database(self, db_name: str):
        """Drop migration test database."""
        admin_engine = self.create_admin_engine()
        
        with admin_engine.connect() as conn:
            # Terminate connections
            conn.execute(text(f"""
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = '{db_name}' AND pid <> pg_backend_pid()
            """))
            # Drop database
            conn.execute(text(f"DROP DATABASE IF EXISTS {db_name}"))
            logger.info(f"Dropped migration test database: {db_name}")
    
    @contextmanager
    def isolated_migration_environment(self, test_suffix: str):
        """Context manager for isolated migration testing environment."""
        db_name = self.create_migration_test_database(test_suffix)
        migration_db_url = self.config.database_url.replace(self.config.database_name, db_name)
        
        # Store original environment
        original_db_url = os.environ.get('DATABASE_URL')
        
        try:
            # Set test database URL
            os.environ['DATABASE_URL'] = migration_db_url
            yield migration_db_url, db_name
        finally:
            # Restore original environment
            if original_db_url:
                os.environ['DATABASE_URL'] = original_db_url
            else:
                os.environ.pop('DATABASE_URL', None)
            
            # Cleanup test database
            try:
                self.drop_migration_test_database(db_name)
            except Exception as e:
                logger.warning(f"Failed to cleanup migration test database {db_name}: {e}")
    
    def run_alembic_command(self, command: List[str], db_url: str) -> Tuple[bool, str]:
        """Run alembic command with specified database URL."""
        env = os.environ.copy()
        env['DATABASE_URL'] = db_url
        
        # Change to alembic directory
        alembic_dir = self.alembic_dir.parent
        
        try:
            result = subprocess.run(
                ['alembic', '-c', str(self.alembic_config_path)] + command,
                cwd=alembic_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=60  # 60 second timeout
            )
            
            success = result.returncode == 0
            output = result.stdout + result.stderr
            
            if not success:
                logger.error(f"Alembic command failed: {' '.join(command)}")
                logger.error(f"Output: {output}")
            
            return success, output
            
        except subprocess.TimeoutExpired:
            logger.error(f"Alembic command timed out: {' '.join(command)}")
            return False, "Command timed out"
        except Exception as e:
            logger.error(f"Error running alembic command: {e}")
            return False, str(e)
    
    def get_database_schema_info(self, db_url: str) -> Dict[str, List[str]]:
        """Get current database schema information."""
        engine = create_engine(db_url)
        inspector = inspect(engine)
        
        try:
            schema_info = {
                'tables': inspector.get_table_names(),
                'indexes': [],
                'constraints': []
            }
            
            # Get indexes and constraints for each table
            for table_name in schema_info['tables']:
                indexes = inspector.get_indexes(table_name)
                schema_info['indexes'].extend([idx['name'] for idx in indexes if idx.get('name')])
                
                # Get constraints (foreign keys, unique, etc.)
                fks = inspector.get_foreign_keys(table_name)
                schema_info['constraints'].extend([fk['name'] for fk in fks if fk.get('name')])
                
                unique_constraints = inspector.get_unique_constraints(table_name)
                schema_info['constraints'].extend([uc['name'] for uc in unique_constraints if uc.get('name')])
            
            return schema_info
            
        finally:
            engine.dispose()
    
    def validate_schema_after_migration(self, db_url: str, expected_tables: List[str] = None) -> SchemaValidationResult:
        """Validate database schema after migration."""
        schema_info = self.get_database_schema_info(db_url)
        
        actual_tables = set(schema_info['tables'])
        expected_tables_set = set(expected_tables or [])
        
        # Remove alembic_version from comparison
        actual_tables.discard('alembic_version')
        
        missing_tables = list(expected_tables_set - actual_tables)
        unexpected_tables = list(actual_tables - expected_tables_set) if expected_tables else []
        
        validation_passed = len(missing_tables) == 0 and (not expected_tables or len(unexpected_tables) == 0)
        
        return SchemaValidationResult(
            tables_created=list(actual_tables),
            indexes_created=schema_info['indexes'],
            constraints_created=schema_info['constraints'],
            expected_tables=expected_tables or [],
            missing_tables=missing_tables,
            unexpected_tables=unexpected_tables,
            validation_passed=validation_passed
        )
    
    def test_migration_upgrade(self, migration_id: str = "head", expected_tables: List[str] = None) -> MigrationTestResult:
        """Test migration upgrade to specified revision."""
        import time
        
        with self.isolated_migration_environment(f"upgrade_{migration_id}") as (db_url, db_name):
            start_time = time.time()
            
            # Run migration
            success, output = self.run_alembic_command(['upgrade', migration_id], db_url)
            
            execution_time = (time.time() - start_time) * 1000  # Convert to milliseconds
            
            if not success:
                return MigrationTestResult(
                    migration_id=migration_id,
                    success=False,
                    error_message=output,
                    execution_time_ms=execution_time
                )
            
            # Validate schema
            schema_validation = self.validate_schema_after_migration(db_url, expected_tables)
            
            return MigrationTestResult(
                migration_id=migration_id,
                success=True,
                execution_time_ms=execution_time,
                schema_validation_passed=schema_validation.validation_passed
            )
    
    def test_migration_downgrade(self, from_revision: str, to_revision: str) -> MigrationTestResult:
        """Test migration downgrade from one revision to another."""
        import time
        
        with self.isolated_migration_environment(f"downgrade_{from_revision}_{to_revision}") as (db_url, db_name):
            # First upgrade to from_revision
            upgrade_success, upgrade_output = self.run_alembic_command(['upgrade', from_revision], db_url)
            
            if not upgrade_success:
                return MigrationTestResult(
                    migration_id=f"{from_revision}->{to_revision}",
                    success=False,
                    error_message=f"Upgrade failed: {upgrade_output}"
                )
            
            # Then downgrade to to_revision
            start_time = time.time()
            downgrade_success, downgrade_output = self.run_alembic_command(['downgrade', to_revision], db_url)
            execution_time = (time.time() - start_time) * 1000
            
            return MigrationTestResult(
                migration_id=f"{from_revision}->{to_revision}",
                success=downgrade_success,
                error_message=downgrade_output if not downgrade_success else None,
                execution_time_ms=execution_time,
                rollback_success=downgrade_success
            )
    
    def test_migration_idempotency(self, migration_id: str = "head") -> MigrationTestResult:
        """Test that running the same migration twice is idempotent."""
        with self.isolated_migration_environment(f"idempotent_{migration_id}") as (db_url, db_name):
            # Run migration first time
            first_success, first_output = self.run_alembic_command(['upgrade', migration_id], db_url)
            
            if not first_success:
                return MigrationTestResult(
                    migration_id=migration_id,
                    success=False,
                    error_message=f"First migration failed: {first_output}"
                )
            
            # Get schema after first migration
            first_schema = self.get_database_schema_info(db_url)
            
            # Run migration second time
            second_success, second_output = self.run_alembic_command(['upgrade', migration_id], db_url)
            
            if not second_success:
                return MigrationTestResult(
                    migration_id=migration_id,
                    success=False,
                    error_message=f"Second migration failed: {second_output}"
                )
            
            # Get schema after second migration
            second_schema = self.get_database_schema_info(db_url)
            
            # Compare schemas - they should be identical
            schemas_match = (
                set(first_schema['tables']) == set(second_schema['tables']) and
                set(first_schema['indexes']) == set(second_schema['indexes']) and
                set(first_schema['constraints']) == set(second_schema['constraints'])
            )
            
            return MigrationTestResult(
                migration_id=migration_id,
                success=schemas_match,
                error_message=None if schemas_match else "Schema changed on second migration run"
            )
    
    def get_available_migrations(self) -> List[str]:
        """Get list of available migration revisions."""
        with self.isolated_migration_environment("list_migrations") as (db_url, db_name):
            success, output = self.run_alembic_command(['history'], db_url)
            
            if not success:
                logger.error(f"Failed to get migration history: {output}")
                return []
            
            # Parse migration IDs from output
            migrations = []
            for line in output.split('\n'):
                if ' -> ' in line and '(head)' not in line:
                    # Extract revision ID (first part before space)
                    parts = line.strip().split()
                    if parts:
                        revision_id = parts[0]
                        if revision_id and revision_id != 'Rev:':
                            migrations.append(revision_id)
            
            return migrations
    
    def test_all_migrations(self, expected_tables: List[str] = None) -> List[MigrationTestResult]:
        """Test all available migrations."""
        results = []
        
        # Test upgrade to head
        head_result = self.test_migration_upgrade("head", expected_tables)
        results.append(head_result)
        
        # Test idempotency
        idempotent_result = self.test_migration_idempotency("head")
        results.append(idempotent_result)
        
        # Get available migrations for downgrade testing
        migrations = self.get_available_migrations()
        
        # Test downgrade from head to base (if migrations exist)
        if migrations:
            downgrade_result = self.test_migration_downgrade("head", "base")
            results.append(downgrade_result)
        
        return results
    
    def cleanup(self):
        """Clean up migration test runner resources."""
        if self.admin_engine:
            self.admin_engine.dispose()


class PluginMigrationTester:
    """Test plugin-specific migrations."""
    
    def __init__(self, plugin_name: str, plugin_dir: Path):
        self.plugin_name = plugin_name
        self.plugin_dir = plugin_dir
        self.migration_runner = MigrationTestRunner()
    
    def has_migrations(self) -> bool:
        """Check if plugin has migration files."""
        alembic_dir = self.plugin_dir / "alembic"
        versions_dir = alembic_dir / "versions"
        
        return (
            alembic_dir.exists() and 
            versions_dir.exists() and 
            any(versions_dir.glob("*.py"))
        )
    
    def test_plugin_migrations(self) -> List[MigrationTestResult]:
        """Test plugin-specific migrations."""
        if not self.has_migrations():
            logger.info(f"Plugin {self.plugin_name} has no migrations to test")
            return []
        
        # Plugin migration testing would require plugin-specific alembic configuration
        # This is a placeholder for plugin migration testing logic
        logger.info(f"Testing migrations for plugin: {self.plugin_name}")
        
        # For now, return empty results - plugin migration testing would need
        # plugin-specific implementation based on how plugins handle migrations
        return []


# Utility functions for migration testing
def get_expected_tables_from_models() -> List[str]:
    """Get expected table names from SQLAlchemy models."""
    from stash_ai_server.db.session import Base
    
    # Import all models to ensure they're registered
    import stash_ai_server.models  # noqa
    
    return [table.name for table in Base.metadata.tables.values()]


def validate_migration_files() -> List[str]:
    """Validate migration files for common issues."""
    issues = []
    
    alembic_dir = Path(__file__).parent.parent / "stash_ai_server" / "alembic"
    versions_dir = alembic_dir / "versions"
    
    if not versions_dir.exists():
        issues.append("Migration versions directory does not exist")
        return issues
    
    migration_files = list(versions_dir.glob("*.py"))
    
    if not migration_files:
        issues.append("No migration files found")
        return issues
    
    # Check for common migration file issues
    for migration_file in migration_files:
        try:
            content = migration_file.read_text()
            
            # Check for required functions
            if 'def upgrade()' not in content:
                issues.append(f"Migration {migration_file.name} missing upgrade() function")
            
            if 'def downgrade()' not in content:
                issues.append(f"Migration {migration_file.name} missing downgrade() function")
            
            # Check for revision info
            if 'revision =' not in content:
                issues.append(f"Migration {migration_file.name} missing revision identifier")
                
        except Exception as e:
            issues.append(f"Error reading migration file {migration_file.name}: {e}")
    
    return issues