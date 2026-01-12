"""Database testing infrastructure with isolation."""

import asyncio
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text, event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import sessionmaker, Session
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Generator, Optional
import logging

from stash_ai_server.db.session import Base, get_db
from stash_ai_server.core.config import settings
from tests.config import test_config

logger = logging.getLogger(__name__)


class DatabaseTestManager:
    """Manages test database creation, isolation, and cleanup."""
    
    def __init__(self, config):
        self.config = config
        self.admin_engine = None
        self.test_engine = None
        self.test_session_factory = None
        self.async_test_engine = None
        self.async_test_session_factory = None
        self._transaction_stack = []
        self._session_registry = set()
    
    def create_admin_engine(self):
        """Create admin engine for database management operations."""
        # Connect to postgres database for admin operations
        admin_url = self.config.database_url.replace(f"/{self.config.database_name}", "/postgres")
        self.admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        return self.admin_engine
    
    def create_test_database(self):
        """Create isolated test database."""
        if not self.admin_engine:
            self.create_admin_engine()
        
        try:
            with self.admin_engine.connect() as conn:
                # Drop database if it exists
                conn.execute(text(f"DROP DATABASE IF EXISTS {self.config.database_name}"))
                # Create fresh test database
                conn.execute(text(f"CREATE DATABASE {self.config.database_name}"))
                logger.info(f"Created test database: {self.config.database_name}")
        except Exception as e:
            logger.error(f"Failed to create test database: {e}")
            raise RuntimeError(f"Could not create test database '{self.config.database_name}': {e}")
    
    def drop_test_database(self):
        """Drop test database."""
        if not self.admin_engine:
            return
            
        try:
            with self.admin_engine.connect() as conn:
                # Terminate active connections to the test database
                conn.execute(text(f"""
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = '{self.config.database_name}' AND pid <> pg_backend_pid()
                """))
                # Drop test database
                conn.execute(text(f"DROP DATABASE IF EXISTS {self.config.database_name}"))
                logger.info(f"Dropped test database: {self.config.database_name}")
        except Exception as e:
            logger.warning(f"Failed to drop test database: {e}")
    
    def create_test_engine(self):
        """Create test database engine."""
        self.test_engine = create_engine(
            self.config.database_url,
            echo=False,  # Reduce noise in tests
            pool_pre_ping=True,
            pool_recycle=300,
            # Enable nested transactions for test isolation
            connect_args={"options": "-c default_transaction_isolation=read_committed"}
        )
        self.test_session_factory = sessionmaker(
            bind=self.test_engine,
            autocommit=False,
            autoflush=False
        )
        return self.test_engine
    
    def create_async_test_engine(self):
        """Create async test database engine."""
        async_url = self.config.database_url.replace("postgresql+psycopg://", "postgresql+asyncpg://")
        self.async_test_engine = create_async_engine(
            async_url,
            echo=False,
            pool_pre_ping=True,
            pool_recycle=300
        )
        self.async_test_session_factory = async_sessionmaker(
            bind=self.async_test_engine,
            class_=AsyncSession,
            autocommit=False,
            autoflush=False
        )
        return self.async_test_engine
    
    def create_tables(self):
        """Create all database tables."""
        if not self.test_engine:
            self.create_test_engine()
        
        Base.metadata.create_all(bind=self.test_engine)
        logger.info("Created all database tables")
    
    def drop_tables(self):
        """Drop all database tables."""
        if not self.test_engine:
            self.create_test_engine()
        
        Base.metadata.drop_all(bind=self.test_engine)
        logger.info("Dropped all database tables")
    
    def truncate_all_tables(self):
        """Truncate all tables while preserving schema."""
        if not self.test_engine:
            return
            
        with self.test_engine.connect() as conn:
            # Get all table names except alembic_version
            result = conn.execute(text("""
                SELECT tablename FROM pg_tables 
                WHERE schemaname = 'public' AND tablename != 'alembic_version'
            """))
            tables = [row[0] for row in result]
            
            # Truncate all tables
            if tables:
                tables_str = ', '.join(f'"{table}"' for table in tables)
                conn.execute(text(f"TRUNCATE {tables_str} RESTART IDENTITY CASCADE"))
                conn.commit()
                logger.debug(f"Truncated tables: {tables}")
    
    @asynccontextmanager
    async def get_test_session(self) -> AsyncGenerator[Session, None]:
        """Get isolated test database session with transaction rollback."""
        if not self.test_session_factory:
            self.create_test_engine()
        
        session = self.test_session_factory()
        self._session_registry.add(session)
        transaction = session.begin()
        
        try:
            yield session
        except Exception:
            transaction.rollback()
            raise
        finally:
            transaction.rollback()  # Always rollback for test isolation
            session.close()
            self._session_registry.discard(session)
    
    @asynccontextmanager
    async def get_async_test_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get isolated async test database session with transaction rollback."""
        if not self.async_test_session_factory:
            self.create_async_test_engine()
        
        async with self.async_test_session_factory() as session:
            async with session.begin() as transaction:
                try:
                    yield session
                except Exception:
                    await transaction.rollback()
                    raise
                finally:
                    # Always rollback for test isolation
                    await transaction.rollback()
    
    def get_sync_test_session(self) -> Session:
        """Get synchronous test session for non-async tests."""
        if not self.test_session_factory:
            self.create_test_engine()
        
        session = self.test_session_factory()
        self._session_registry.add(session)
        return session
    
    def close_sync_session(self, session: Session):
        """Close synchronous test session with rollback."""
        if session in self._session_registry:
            if session.in_transaction():
                session.rollback()
            session.close()
            self._session_registry.discard(session)
    
    def validate_database_state(self) -> bool:
        """Validate that database is in expected state for testing."""
        if not self.test_engine:
            return False
            
        try:
            with self.test_engine.connect() as conn:
                # Check database exists and is accessible
                result = conn.execute(text("SELECT 1"))
                result.fetchone()
                
                # Check that required tables exist
                result = conn.execute(text("""
                    SELECT COUNT(*) FROM information_schema.tables 
                    WHERE table_schema = 'public' AND table_name != 'alembic_version'
                """))
                table_count = result.scalar()
                
                logger.debug(f"Database validation: {table_count} tables found")
                return table_count > 0
                
        except Exception as e:
            logger.error(f"Database validation failed: {e}")
            return False
    
    def cleanup(self):
        """Clean up database connections and resources."""
        # Close any remaining sessions
        for session in list(self._session_registry):
            try:
                if session.in_transaction():
                    session.rollback()
                session.close()
            except Exception as e:
                logger.warning(f"Error closing session during cleanup: {e}")
        
        self._session_registry.clear()
        
        # Dispose engines
        if self.test_engine:
            self.test_engine.dispose()
            logger.debug("Disposed sync test engine")
            
        if self.async_test_engine:
            # Schedule async engine disposal
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self.async_test_engine.dispose())
                else:
                    loop.run_until_complete(self.async_test_engine.dispose())
                logger.debug("Disposed async test engine")
            except Exception as e:
                logger.warning(f"Error disposing async engine: {e}")
                
        if self.admin_engine:
            self.admin_engine.dispose()
            logger.debug("Disposed admin engine")


# Global database test manager
db_manager = DatabaseTestManager(test_config)


@pytest_asyncio.fixture(scope="session")
async def test_database():
    """Session-scoped test database setup and teardown."""
    # Apply test configuration
    test_config.apply_environment_overrides()
    
    # Ensure database is available before proceeding
    if not test_config.ensure_database_available():
        pytest.skip("PostgreSQL database is not available for testing")
    
    # Create test database
    db_manager.create_test_database()
    db_manager.create_test_engine()
    db_manager.create_async_test_engine()
    db_manager.create_tables()
    
    # Validate database state
    if not db_manager.validate_database_state():
        raise RuntimeError("Test database validation failed")
    
    yield db_manager
    
    # Cleanup
    db_manager.drop_test_database()
    db_manager.cleanup()
    test_config.cleanup_environment()


@pytest_asyncio.fixture
async def db_session(test_database):
    """Provide transactional database session with rollback."""
    async with test_database.get_test_session() as session:
        yield session


@pytest_asyncio.fixture
async def async_db_session(test_database):
    """Provide async transactional database session with rollback."""
    async with test_database.get_async_test_session() as session:
        yield session


@pytest.fixture
def sync_db_session(test_database):
    """Provide synchronous database session with rollback for non-async tests."""
    session = test_database.get_sync_test_session()
    transaction = session.begin()
    
    yield session
    
    # Always rollback for test isolation
    transaction.rollback()
    test_database.close_sync_session(session)


@pytest_asyncio.fixture
async def clean_database(test_database):
    """Ensure clean database state between tests."""
    # Clear all tables but keep schema
    test_database.truncate_all_tables()
    
    yield
    
    # Additional cleanup after test if needed
    test_database.truncate_all_tables()


@pytest_asyncio.fixture
async def isolated_db_transaction(test_database):
    """Provide completely isolated database transaction that auto-rolls back."""
    if not test_database.test_engine:
        test_database.create_test_engine()
    
    connection = test_database.test_engine.connect()
    transaction = connection.begin()
    
    # Create session bound to this specific transaction
    session = Session(bind=connection)
    
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest_asyncio.fixture
async def database_state_validator(test_database):
    """Fixture that validates database state before and after tests."""
    # Validate state before test
    initial_state = test_database.validate_database_state()
    if not initial_state:
        raise RuntimeError("Database not in valid state before test")
    
    yield test_database
    
    # Validate state after test
    final_state = test_database.validate_database_state()
    if not final_state:
        logger.warning("Database not in valid state after test")


# Utility functions for test database operations
def create_test_data_factory(session: Session):
    """Factory function to create test data with proper session binding."""
    def _create_data(model_class, **kwargs):
        instance = model_class(**kwargs)
        session.add(instance)
        session.flush()  # Get ID without committing
        return instance
    return _create_data


def assert_table_empty(session: Session, model_class):
    """Assert that a table is empty."""
    count = session.query(model_class).count()
    assert count == 0, f"Table {model_class.__tablename__} is not empty (count: {count})"


def assert_table_count(session: Session, model_class, expected_count: int):
    """Assert that a table has expected number of rows."""
    count = session.query(model_class).count()
    assert count == expected_count, f"Table {model_class.__tablename__} has {count} rows, expected {expected_count}"