from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Dict, Iterator

import sqlalchemy as sa
from sqlalchemy.engine import Engine, URL
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker

from stash_ai_server.core.runtime import register_backend_refresh_handler
from stash_ai_server.utils.stash_api import stash_api
from stash_ai_server.core.system_settings import get_value as sys_get
from stash_ai_server.utils.path_mutation import mutate_path_for_backend

_log = logging.getLogger(__name__)

_ENGINE_LOCK = RLock()
_STASH_ENGINE: Engine | None = None
_STASH_SESSION_FACTORY: sessionmaker[Session] | None = None
_STASH_DB_PATH: Path | None = None
_METADATA: sa.MetaData | None = None
_TABLE_CACHE: Dict[str, sa.Table] = {}
_CACHED_DB_PATH: Path | None = None


def _resolve_db_path() -> Path | None:
    """Return the configured path (or URL) to the Stash database."""
    global _CACHED_DB_PATH
    if _CACHED_DB_PATH is not None:
        return _CACHED_DB_PATH
    try:
        configured = sys_get("STASH_DB_PATH")
    except Exception:
        configured = None

    if configured:
        try:
            raw = str(configured)
            if raw.strip() and not raw.strip().upper().startswith("REPLACE_WITH"):
                mutated = mutate_path_for_backend(raw)
                resolved = Path(mutated).expanduser()
                try:
                    resolved = resolved.resolve(strict=False)
                except Exception:
                    pass
                if resolved.exists():
                    _CACHED_DB_PATH = resolved
                    return resolved
                _log.warning("Configured STASH_DB_PATH (after mutation) does not exist: %s", resolved)
        except Exception:
            _log.exception("Failed to interpret STASH_DB_PATH system setting: %r", configured)

    _log.debug("No valid STASH_DB_PATH system setting available; not resolving DB path")
    return None


def _dispose_locked() -> None:
    global _STASH_ENGINE, _STASH_SESSION_FACTORY, _STASH_DB_PATH, _METADATA, _TABLE_CACHE

    if _STASH_ENGINE is not None:
        try:
            _STASH_ENGINE.dispose()
        except Exception:  # pragma: no cover - defensive cleanup
            _log.exception("Failed to dispose previous Stash DB engine")
    _STASH_ENGINE = None
    _STASH_SESSION_FACTORY = None
    _STASH_DB_PATH = None
    _METADATA = None
    _TABLE_CACHE = {}


def _build_engine_for_path(resolved_path: Path) -> Engine:
    """Construct a SQLAlchemy engine for the provided SQLite path."""

    # Prefer opening the DB in read-only mode so we never block or write to the
    # upstream Stash database. Use the SQLite URI "file:path?mode=ro" and
    # instruct SQLAlchemy to treat the database string as a URI.
    # If this fails (older sqlite builds or unusual environments), fall back to
    # the previous behaviour (read/write) but log a warning.
    pathstr = str(resolved_path)
    readonly_uri = f"file:{resolved_path.as_posix()}?mode=ro&cache=shared"
    try:
        def connect_readonly() -> sqlite3.Connection:
            return sqlite3.connect(readonly_uri, uri=True, check_same_thread=False)

        engine = sa.create_engine(
            "sqlite+pysqlite://",
            creator=connect_readonly,
            poolclass=StaticPool,
            future=True,
        )
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
        return engine
    except Exception:
        _log.exception("Failed to open Stash DB in read-only mode")
        return None


def get_stash_engine(*, refresh: bool = False) -> Engine | None:
    """Return (and cache) an engine connected to the Stash database."""

    global _STASH_ENGINE, _STASH_SESSION_FACTORY, _STASH_DB_PATH, _METADATA

    with _ENGINE_LOCK:
        path = _resolve_db_path()
        if path is None:
            if _STASH_ENGINE is not None:
                _log.info("Stash DB path unavailable; disposing cached engine")
                _dispose_locked()
            return None
        if not path.exists():
            _log.warning("Stash database path does not exist: %s", path)
            if refresh:
                _dispose_locked()
            return None

        if refresh or _STASH_ENGINE is None or _STASH_DB_PATH != path:
            _dispose_locked()
            engine = _build_engine_for_path(path)
            _STASH_ENGINE = engine
            _STASH_SESSION_FACTORY = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
            _STASH_DB_PATH = path
            _METADATA = sa.MetaData()
            _TABLE_CACHE = {}
            _log.info("Connected to Stash database at %s", path)
        return _STASH_ENGINE


def get_stash_sessionmaker() -> sessionmaker[Session] | None:
    """Return a session factory bound to the Stash database (if available)."""

    engine = get_stash_engine()
    if engine is None:
        return None
    return _STASH_SESSION_FACTORY


@contextmanager
def stash_db_session() -> Iterator[Session]:
    """Context manager yielding a session connected to the Stash database.

    Raises RuntimeError if the database is not available.
    """

    factory = get_stash_sessionmaker()
    if factory is None:
        raise RuntimeError("Stash database is not configured")
    session = factory()
    try:
        session.execute(sa.text("PRAGMA foreign_keys = ON"))
    except Exception:  # pragma: no cover - best effort pragma (ignored on failure)
        pass
    try:
        yield session
    finally:
        session.close()


def _ensure_table_cache() -> None:
    global _METADATA
    if _METADATA is None:
        _METADATA = sa.MetaData()


def get_stash_table(name: str, *, required: bool = True) -> sa.Table | None:
    """Reflect and cache a table from the Stash database."""

    engine = get_stash_engine()
    if engine is None:
        return None

    with _ENGINE_LOCK:
        _ensure_table_cache()
        table = _TABLE_CACHE.get(name)
        if table is not None:
            return table
        try:
            table = sa.Table(name, _METADATA, autoload_with=engine)
        except Exception as exc:
            if required:
                _log.error("Failed to reflect Stash table '%s': %s", name, exc)
            else:
                _log.debug("Stash table '%s' unavailable: %s", name, exc)
            return None
        _TABLE_CACHE[name] = table
        return table


def get_first_available_table(*names: str, required_columns: tuple[str, ...] = ()) -> sa.Table | None:
    """Return the first table (from candidates) present in the Stash DB that has the required columns."""

    engine = get_stash_engine()
    if engine is None:
        return None
    with _ENGINE_LOCK:
        for name in names:
            table = _TABLE_CACHE.get(name)
            if table is None:
                try:
                    _ensure_table_cache()
                    table = sa.Table(name, _METADATA, autoload_with=engine)
                    _TABLE_CACHE[name] = table
                except Exception:
                    continue
            if required_columns and any(table.c.get(col) is None for col in required_columns):
                continue
            return table
    return None


def stash_db_available() -> bool:
    """Return True if the Stash DB connection can be established."""

    return get_stash_engine() is not None


def _refresh_stash_db() -> None:
    with _ENGINE_LOCK:
        _dispose_locked()


register_backend_refresh_handler("stash_db", _refresh_stash_db)
