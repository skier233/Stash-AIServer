#!/usr/bin/env python3
"""Validation script for database testing infrastructure."""

import sys
import logging
from pathlib import Path

# Add backend to path
backend_root = Path(__file__).parent.parent
sys.path.insert(0, str(backend_root))

from tests.database import DatabaseTestManager
from tests.migration_testing import MigrationTestRunner, validate_migration_files
from tests.config import test_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def validate_database_fixtures():
    """Validate database test fixtures."""
    logger.info("Validating database test fixtures...")
    
    try:
        # Test database manager creation
        db_manager = DatabaseTestManager(test_config)
        logger.info("✓ DatabaseTestManager created successfully")
        
        # Test configuration
        assert hasattr(db_manager, 'config')
        assert hasattr(db_manager, 'create_test_database')
        assert hasattr(db_manager, 'get_test_session')
        assert hasattr(db_manager, 'get_async_test_session')
        logger.info("✓ DatabaseTestManager has required methods")
        
        # Test validation method
        validation_result = db_manager.validate_database_state()
        logger.info(f"✓ Database validation method works (result: {validation_result})")
        
        return True
        
    except Exception as e:
        logger.error(f"✗ Database fixtures validation failed: {e}")
        return False


def validate_migration_testing():
    """Validate migration testing system."""
    logger.info("Validating migration testing system...")
    
    try:
        # Test migration runner creation
        migration_runner = MigrationTestRunner(test_config)
        logger.info("✓ MigrationTestRunner created successfully")
        
        # Test required methods
        assert hasattr(migration_runner, 'test_migration_upgrade')
        assert hasattr(migration_runner, 'test_migration_downgrade')
        assert hasattr(migration_runner, 'test_migration_idempotency')
        logger.info("✓ MigrationTestRunner has required methods")
        
        # Test migration file validation
        issues = validate_migration_files()
        if issues:
            logger.warning(f"Migration file issues found: {issues}")
        else:
            logger.info("✓ Migration files validation passed")
        
        migration_runner.cleanup()
        return True
        
    except Exception as e:
        logger.error(f"✗ Migration testing validation failed: {e}")
        return False


def main():
    """Run all validation tests."""
    logger.info("Starting database testing infrastructure validation...")
    
    results = []
    
    # Validate database fixtures
    results.append(validate_database_fixtures())
    
    # Validate migration testing
    results.append(validate_migration_testing())
    
    # Summary
    passed = sum(results)
    total = len(results)
    
    logger.info(f"\nValidation Summary: {passed}/{total} tests passed")
    
    if passed == total:
        logger.info("✓ All database testing infrastructure validation tests passed!")
        return 0
    else:
        logger.error("✗ Some validation tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())