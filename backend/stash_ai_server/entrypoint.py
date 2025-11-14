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

def main():
    configure_logging(settings.log_level)
    print(f"[entrypoint] starting (prod) version={settings.version} db={settings.database_url} log_level={settings.log_level}", flush=True)
    try:
        print(f"[entrypoint] data_dir={settings.data_dir} db_file={settings.db_file}", flush=True)
        if getattr(settings, 'diagnostics', None):
            for line in settings.diagnostics:
                print(f"[entrypoint][config] {line}", flush=True)
    except Exception:
        pass
    # Only attempt migrations in production image (dev uses create_all convenience)
    _maybe_run_migrations()
    print('[entrypoint] migrations complete', flush=True)
    try:
        seed_system_settings()
    except Exception as e:
        print(f"[entrypoint] seed_system_settings failed: {e}", flush=True)
    import uvicorn
    host = os.getenv('AI_SERVER_HOST', '0.0.0.0')
    port = int(os.getenv('AI_SERVER_PORT', '4153'))
    uvicorn.run(
        'stash_ai_server.main:app',
        host=host,
        port=port,
        reload=False,
        log_level=settings.log_level.lower(),
        log_config=None,
    )

if __name__ == '__main__':  # pragma: no cover
    main()