"""Database testing infrastructure with isolation."""

import asyncio
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import sessionmaker, Session
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Generator

from stash_ai_server.db.session import Base, get_db
from stash_ai_server.core.config import settings
from tests.config import test_config


class DatabaseTestManager:
    """Manages test database creation, isolation, and cleanup."""
    
    def __init__(self, config):
        self.config = config
        self.admin_engine = None
        self.test_engine = None
        self.test_session_factory = None
        self.async_test_engine = None
        self.async_test_session_factory = None
    
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
        
        with self.admin_engine.connect() as conn:
            # Drop database if it exists
            conn.execute(text(f"DROP DATABASE IF EXISTS {self.config.database_name}"))
            # Create fresh test database
            conn.execute(text(f"CREATE DATABASE {self.config.database_name}"))
    
    def drop_test_database(self):
        """Drop test database."""
        if not self.admin_engine:
            self.create_admin_engine()
        
        with self.admin_engine.connect() as conn:
            # Terminate active connections to the test database
            conn.execute(text(f"""
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = '{self.config.database_name}' AND pid <> pg_backend_pid()
            """))
            # Drop test database
            conn.execute(text(f"DROP DATABASE IF EXISTS {self.config.database_name}"))
    
    def create_test_engine(self):
        """Create test database engine."""
        self.test_engine = create_engine(
            self.config.database_url,
            echo=False,  # Reduce noise in tests
            pool_pre_ping=True,
            pool_recycle=300
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
    
    def drop_tables(self):
        """Drop all database tables."""
        if not self.test_engine:
            self.create_test_engine()
        
        Base.metadata.drop_all(bind=self.test_engine)
    
    @asynccontextmanager
    async def get_test_session(self) -> AsyncGenerator[Session, None]:
        """Get isolated test database session with transaction rollback."""
        if not self.test_session_factory:
            self.create_test_engine()
        
        session = self.test_session_factory()
        transaction = session.begin()
        
        try:
            yield session
        finally:
            transaction.rollback()
            session.close()
    
    @asynccontextmanager
    async def get_async_test_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get isolated async test database session with transaction rollback."""
        if not self.async_test_session_factory:
            self.create_async_test_engine()
        
        async with self.async_test_session_factory() as session:
            async with session.begin():
                try:
                    yield session
                finally:
                    await session.rollback()
    
    def cleanup(self):
        """Clean up database connections and resources."""
        if self.test_engine:
            self.test_engine.dispose()
        if self.async_test_engine:
            asyncio.create_task(self.async_test_engine.dispose())
        if self.admin_engine:
            self.admin_engine.dispose()


# Global database test manager
db_manager = DatabaseTestManager(test_config)


@pytest_asyncio.fixture(scope="session")
async def test_database():
    """Session-scoped test database setup and teardown."""
    # Apply test configuration
    test_config.apply_environment_overrides()
    
    # Create test database
    db_manager.create_test_database()
    db_manager.create_test_engine()
    db_manager.create_async_test_engine()
    db_manager.create_tables()
    
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


@pytest_asyncio.fixture
async def clean_database(test_database):
    """Ensure clean database state between tests."""
    # Clear all tables but keep schema
    with test_database.test_engine.connect() as conn:
        # Get all table names
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
    
    yield
    
    # Additional cleanup after test if needed
    with test_database.test_engine.connect() as conn:
        result = conn.execute(text("""
            SELECT tablename FROM pg_tables 
            WHERE schemaname = 'public' AND tablename != 'alembic_version'
        """))
        tables = [row[0] for row in result]
        
        if tables:
            tables_str = ', '.join(f'"{table}"' for table in tables)
            conn.execute(text(f"TRUNCATE {tables_str} RESTART IDENTITY CASCADE"))
        conn.commit()