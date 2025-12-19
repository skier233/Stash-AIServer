from __future__ import annotations
import os
import logging
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.engine import Engine
from stash_ai_server.utils.stash_db import get_stash_db_path

_log = logging.getLogger(__name__)

# Schema to hold foreign tables
FDW_SCHEMA = "stash_sqlite"
FDW_SERVER = "stash_sqlite_server"

def setup_sqlite_fdw(engine: Engine) -> None:
    """Create sqlite_fdw server and foreign tables for the Stash SQLite DB.

    This is best-effort: if the extension is missing or the DB path is absent,
    we log and continue without raising.
    """
    enable_flag = os.getenv("AI_ENABLE_SQLITE_FDW", "1").strip().lower() not in {"0","false","off","no"}
    if not enable_flag:
        _log.info("sqlite_fdw setup skipped (AI_ENABLE_SQLITE_FDW disabled)")
        return

    db_path: Path | None = get_stash_db_path()
    if db_path is None or not db_path.exists():
        _log.info("sqlite_fdw skipped: Stash DB path not available")
        return

    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE SCHEMA IF NOT EXISTS %s" % FDW_SCHEMA))
            try:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS sqlite_fdw"))
            except Exception as exc:
                _log.warning("sqlite_fdw extension unavailable; skipping FDW setup: %s", exc)
                return

            # Ensure server uses the latest DB path (drop/recreate is simplest and safe for foreign tables we manage)
            conn.execute(text(f"DROP SERVER IF EXISTS {FDW_SERVER} CASCADE"))
            conn.execute(
                text(
                    f"CREATE SERVER {FDW_SERVER} FOREIGN DATA WRAPPER sqlite_fdw OPTIONS (database :dbpath)"
                ),
                {"dbpath": str(db_path)},
            )

            # Drop and recreate foreign tables we rely on for recommendations
            tables = {
                "stash_scenes": "CREATE FOREIGN TABLE %s.stash_scenes (id INTEGER, title TEXT NULL, play_duration INTEGER NULL, updated_at TEXT NULL, created_at TEXT NULL) SERVER %s OPTIONS (table 'scenes')",
                "stash_scene_views": "CREATE FOREIGN TABLE %s.stash_scene_views (scene_id INTEGER, view_date TEXT) SERVER %s OPTIONS (table 'scenes_view_dates')",
                "stash_tags": "CREATE FOREIGN TABLE %s.stash_tags (id INTEGER, name TEXT, created_at TEXT NULL, updated_at TEXT NULL) SERVER %s OPTIONS (table 'tags')",
                "stash_scene_tags": "CREATE FOREIGN TABLE %s.stash_scene_tags (scene_id INTEGER, tag_id INTEGER) SERVER %s OPTIONS (table 'scene_tags')",
                "stash_performers": "CREATE FOREIGN TABLE %s.stash_performers (id INTEGER, name TEXT, created_at TEXT NULL, updated_at TEXT NULL) SERVER %s OPTIONS (table 'performers')",
                "stash_scene_performers": "CREATE FOREIGN TABLE %s.stash_scene_performers (scene_id INTEGER, performer_id INTEGER) SERVER %s OPTIONS (table 'scene_performers')",
            }

            for ft_name, create_sql in tables.items():
                conn.execute(text(f"DROP FOREIGN TABLE IF EXISTS {FDW_SCHEMA}.{ft_name} CASCADE"))
                conn.execute(text(create_sql % (FDW_SCHEMA, FDW_SERVER)))

            _log.info("sqlite_fdw setup complete (schema=%s path=%s)", FDW_SCHEMA, db_path)
    except Exception as exc:  # pragma: no cover - best effort
        _log.warning("sqlite_fdw setup failed: %s", exc)
        return
