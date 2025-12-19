from __future__ import annotations
import os
from stash_ai_server.core.config import settings
from stash_ai_server.core.logging_config import configure_logging
from stash_ai_server.core.system_settings import seed_system_settings

def _maybe_run_migrations():
    try:
        from stash_ai_server.core.migrations import run_migrations
        run_migrations()
    except Exception as e:
        print(f"[entrypoint] migrations failed: {e}", flush=True)
        raise


def _maybe_migrate_sqlite():
    try:
        from stash_ai_server.db.session import engine
        from stash_ai_server.db.sqlite_migrator import migrate_sqlite_to_postgres
        if migrate_sqlite_to_postgres(engine):
            print("[entrypoint] migrated legacy SQLite database", flush=True)
    except Exception as e:
        print(f"[entrypoint] sqlite migration failed: {e}", flush=True)
        raise

def main():
    configure_logging(settings.log_level)
    print(f"[entrypoint] starting (prod) version={settings.version} db={settings.database_url} log_level={settings.log_level}", flush=True)
    try:
        print(f"[entrypoint] data_dir={settings.data_dir}", flush=True)
        if getattr(settings, 'diagnostics', None):
            for line in settings.diagnostics:
                print(f"[entrypoint][config] {line}", flush=True)
    except Exception:
        pass
    # Only attempt migrations in production image (dev uses create_all convenience)
    _maybe_run_migrations()
    _maybe_migrate_sqlite()
    print('[entrypoint] migrations complete', flush=True)
    try:
        seed_system_settings()
    except Exception as e:
        print(f"[entrypoint] seed_system_settings failed: {e}", flush=True)
    import uvicorn
    from uvicorn.config import LOGGING_CONFIG
    host = os.getenv('AI_SERVER_HOST', '0.0.0.0')
    port = int(os.getenv('AI_SERVER_PORT', '4153'))
    print(f"[entrypoint] launching uvicorn on {host}:{port}", flush=True)
    try:
        uvicorn.run(
            'stash_ai_server.main:app',
            host=host,
            port=port,
            reload=False,
            log_level=settings.log_level.lower(),
            # Use uvicorn's default logging config to surface startup errors
            log_config=LOGGING_CONFIG,
        )
    except BaseException as exc:  # catch SystemExit too
        import traceback
        print(f"[entrypoint] uvicorn crashed: {exc}", flush=True)
        traceback.print_exc()
        raise
    finally:
        print("[entrypoint] uvicorn stopped", flush=True)

if __name__ == '__main__':  # pragma: no cover
    main()