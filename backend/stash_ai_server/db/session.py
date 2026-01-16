from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from typing import Optional
from functools import lru_cache

from stash_ai_server.core.config import settings


class Base(DeclarativeBase):
    pass


@lru_cache()
def get_engine():
    """Get the database engine, creating it once and caching it."""
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=int(settings.db_credentials['pool_size']),
        max_overflow=int(settings.db_credentials['max_overflow']),
        echo=bool(settings.db_credentials['echo']),
        future=True,
    )


@lru_cache()
def get_session_factory():
    """Get the SessionLocal factory, creating it once and caching it."""
    return sessionmaker(autocommit=False, autoflush=False, bind=get_engine())


def get_db():
    """FastAPI dependency to get a database session."""
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# For backward compatibility with existing code
def get_session():
    """Get a database session (not a generator like get_db)."""
    SessionLocal = get_session_factory()
    return SessionLocal()


def get_session_local():
    """Get the session factory (returns the factory itself, not a session).
    
    Used in contexts like: with get_session_local()() as session:
    This is for backward compatibility with code expecting a callable factory.
    """
    return get_session_factory()


# Legacy compatibility - these should be phased out
engine = property(lambda self: get_engine())
SessionLocal = get_session_factory
