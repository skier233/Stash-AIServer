from __future__ import annotations
import os
import pathlib
from stash_ai_server.core.config import settings


def run_migrations():
    """Proper Alembic migration workflow.

    States handled:
    1. No DB file / empty DB: run `alembic upgrade head`.
    2. Tables exist but no alembic_version: assume legacy unmanaged schema -> stamp head.
    3. alembic_version present: normal upgrade head.
    """
    try:
        from alembic.config import Config
        from alembic import command
        from sqlalchemy import create_engine, inspect, text
    except Exception as e:
        print(f"[migrations] prerequisites missing (alembic/sqlalchemy): {e}", flush=True)
        raise

    # Resolve alembic.ini path. Prefer package-embedded file (works for pip
    # installed wheels), then allow override via AI_SERVER_ALEMBIC_INI, then
    # check repo-level or current working directory locations.
    env_ini = os.getenv('AI_SERVER_ALEMBIC_INI')
    candidates = []
    # package-embedded alembic.ini (inside installed package)
    candidates.append(pathlib.Path(__file__).resolve().parent.parent / 'alembic.ini')
    if env_ini:
        candidates.append(pathlib.Path(env_ini))
    # repo root / working dir fallbacks
    candidates.append(pathlib.Path(__file__).resolve().parent.parent.parent / 'alembic.ini')
    candidates.append(pathlib.Path.cwd() / 'alembic.ini')
    cfg_path = next((p for p in candidates if p.exists() and p.is_file()), None)
    if not cfg_path:
        raise RuntimeError('alembic.ini not found; cannot run migrations')

    cfg = Config(str(cfg_path))
    # Force script_location in case config file path resolution changes in packaging
    script_location = pathlib.Path(cfg_path).parent / 'alembic'
    cfg.set_main_option('script_location', str(script_location))
    cfg.set_main_option('sqlalchemy.url', settings.database_url)

    print(f"[migrations] config={cfg_path} script_location={script_location}", flush=True)

    # Determine DB file for sqlite
    db_file = None
    if settings.database_url.startswith('sqlite:///'):
        db_file = pathlib.Path(settings.database_url.replace('sqlite:///', '', 1))

    engine = create_engine(settings.database_url)
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    print(f"[migrations] existing_tables={sorted(existing_tables)}", flush=True)

    has_version = 'alembic_version' in existing_tables
    empty_db = len(existing_tables) == 0

    try:
        if empty_db:
            print('[migrations] state=EMPTY -> upgrade head', flush=True)
            command.upgrade(cfg, 'head')
        elif not has_version:
            print('[migrations] state=UNMANAGED (no alembic_version) -> stamping head', flush=True)
            command.stamp(cfg, 'head')
            # After stamp, still run upgrade in case new migrations were added beyond head expectation
            command.upgrade(cfg, 'head')
        else:
            print('[migrations] state=MANAGED -> upgrade head', flush=True)
            command.upgrade(cfg, 'head')
    except Exception as e:
        print(f"[migrations] error during migration: {e}", flush=True)
        raise

    # Verification: ensure key tables exist post-migration
    post_tables = set(inspect(engine).get_table_names())
    required = {'plugin_meta', 'plugin_settings'}
    missing = required - post_tables
    if missing:
        raise RuntimeError(f"[migrations] missing expected tables after migration: {missing}")
    print('[migrations] complete; verified core tables present', flush=True)
