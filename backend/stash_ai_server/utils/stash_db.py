from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Dict, Iterator

import sqlalchemy as sa
from sqlalchemy.engine import Engine, URL
from sqlalchemy.orm import Session, sessionmaker

from stash_ai_server.core.runtime import register_backend_refresh_handler
from stash_ai_server.utils.stash_api import stash_api

_log = logging.getLogger(__name__)

_ENGINE_LOCK = RLock()
_STASH_ENGINE: Engine | None = None
_STASH_SESSION_FACTORY: sessionmaker[Session] | None = None
_STASH_DB_PATH: Path | None = None
_METADATA: sa.MetaData | None = None
_TABLE_CACHE: Dict[str, sa.Table] = {}


def _resolve_db_path() -> Path | None:
    """Return the configured path (or URL) to the Stash database."""

    client = stash_api.stash_interface
    if not client:
        _log.debug("Stash interface unavailable; cannot resolve database path")
        return None
    try:
        config = client.get_configuration() or {}
    except Exception:  # pragma: no cover - defensive
        _log.exception("Failed to retrieve Stash configuration for database path")
        return None

    general_cfg = config.get("general") if isinstance(config, dict) else None
    #_log.debug("Stash general configuration: %s", config)
    db_path = None

    # TODO: figure out best way to get the db path. (probably need to use a setting for the db path)
    return Path("C:\\Coding\\Testing\\Stash-PornServer\\stash-go.sqlite")
    if isinstance(general_cfg, dict):
        # Use generatedPath + databasePath when available (Stash provides generatedPath as a base dir)
        generated = general_cfg.get("generatedPath") or general_cfg.get("generated_path")
        relative = general_cfg.get("databasePath") or general_cfg.get("database_path")
        if generated and relative:
            try:
                candidate = Path(generated) / relative
                try:
                    resolved = candidate.expanduser()
                    try:
                        resolved = resolved.resolve(strict=False)
                    except Exception:
                        # best-effort normalization; file may not exist yet
                        pass
                    _log.debug("Resolved Stash database path via generatedPath: %s, base: %s, relative: %s", resolved, candidate, relative)
                    return resolved
                except Exception:
                    _log.warning("Could not interpret generatedPath+databasePath: %s + %s", generated, relative)
            except Exception:
                _log.exception("Failed to join generatedPath and databasePath from Stash configuration")

        # Fallback to legacy single-field values
        db_path = relative or general_cfg.get("databasePath") or general_cfg.get("database_path")
    if not db_path:
        _log.debug("Stash configuration missing general.databasePath")
        return None
    _log.debug("Resolved Stash database path (legacy): %s", db_path)
    try:
        resolved = Path(db_path).expanduser()
        try:
            resolved = resolved.resolve(strict=False)
        except Exception:  # pragma: no cover - path may not exist yet
            pass
        return resolved
    except Exception:  # pragma: no cover - defensive
        _log.warning("Could not interpret Stash database path: %s", db_path)
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

    url = URL.create("sqlite", database=str(resolved_path))
    return sa.create_engine(url, connect_args={"check_same_thread": False})


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
