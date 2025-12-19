from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.engine import Engine

from stash_ai_server.core.config import settings


def _load_sqlite_engine(path: Path) -> Engine:
    return create_engine(f"sqlite:///{path}")


def migrate_sqlite_to_postgres(target_engine: Engine) -> bool:
    """Copy data from the legacy SQLite database into Postgres.

    Returns True when a migration was performed.
    """
    legacy_path = getattr(settings, "legacy_sqlite_path", settings.data_dir / "app.db")
    if not legacy_path.exists():
        return False

    sentinel = legacy_path.with_suffix(".migrated")
    if sentinel.exists():  # already migrated
        return False

    sqlite_engine = _load_sqlite_engine(legacy_path)
    sqlite_meta = MetaData()
    sqlite_meta.reflect(bind=sqlite_engine)

    if not sqlite_meta.tables:
        sentinel.write_text("empty sqlite database", encoding="utf-8")
        return False

    pg_meta = MetaData()
    pg_meta.reflect(bind=target_engine)

    def _normalize_row(table_name: str, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if table_name == "interaction_events":
            # client_event_id is stored as text now; leave strings intact
            value = row.get("client_event_id")
            if value is None:
                row["client_event_id"] = None
            entity = row.get("entity_id")
            # SQLite may contain large unsigned entity ids; clamp to None if they exceed PG integer range
            if isinstance(entity, int) and entity > 2147483647:
                row["entity_id"] = None
            # Postgres column is non-null; if entity_id is still None, coerce to 0 as a sentinel
            if row.get("entity_id") is None:
                row["entity_id"] = 0
        elif table_name == "task_history":
            # task_id/action_id/item_id are integers in Postgres schema; coerce and drop rows that cannot convert
            for key in ("task_id", "action_id", "item_id"):
                if key in row:
                    val = row.get(key)
                    if val is None:
                        continue
                    try:
                        row[key] = int(val)
                    except (TypeError, ValueError):
                        return None
        return row
        return row

    try:
        with sqlite_engine.connect() as sqlite_conn, target_engine.begin() as pg_conn:
            for table in sqlite_meta.sorted_tables:
                if table.name == "alembic_version":
                    continue
                target_table = pg_meta.tables.get(table.name)
                if target_table is None:
                    continue
                # If target table already has rows (e.g., from a previous partial migration), skip to avoid PK collisions
                existing_count = pg_conn.execute(text(f"SELECT COUNT(*) FROM {table.name}")).scalar()
                if existing_count and existing_count > 0:
                    continue
                raw_rows = sqlite_conn.execute(table.select()).mappings().all()
                rows = []
                for r in raw_rows:
                    normalized = _normalize_row(table.name, dict(r))  # copy so we can mutate
                    if normalized is None:
                        continue
                    rows.append(normalized)
                if not rows:
                    continue
                pg_conn.execute(target_table.insert(), rows)

                pk_cols = [col.name for col in target_table.primary_key.columns]
                if len(pk_cols) == 1:
                    pk = pk_cols[0]
                    seq_name = pg_conn.execute(
                        text("SELECT pg_get_serial_sequence(:table, :column)"),
                        {"table": table.name, "column": pk},
                    ).scalar()
                    if seq_name:
                        pg_conn.execute(
                            text(
                                f"SELECT setval(:seq, (SELECT COALESCE(MAX({pk}), 0) FROM {table.name}))"
                            ),
                            {"seq": seq_name},
                        )

            pg_conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    finally:
        sqlite_engine.dispose()

    # On Windows the SQLite file can stay locked briefly; retry rename a few times before giving up
    for _ in range(5):
        try:
            legacy_path.rename(sentinel)
            break
        except PermissionError:
            time.sleep(0.5)
    else:
        # If still locked, leave without renaming; migration already ran
        return True
    return True
