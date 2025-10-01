from __future__ import annotations
import os
from app.core.config import settings
from app.core.system_settings import seed_system_settings

def _maybe_run_migrations():
    try:
        from app.core.migrations import run_migrations
        run_migrations()
    except Exception as e:
        print(f"[entrypoint] migrations failed: {e}", flush=True)
        raise

def main():
    print(f"[entrypoint] starting (prod) version={settings.version} db={settings.database_url}", flush=True)
    # Only attempt migrations in production image (dev uses create_all convenience)
    _maybe_run_migrations()
    print('[entrypoint] migrations complete', flush=True)
    try:
        seed_system_settings()
    except Exception as e:
        print(f"[entrypoint] seed_system_settings failed: {e}", flush=True)
    import uvicorn
    host = os.getenv('AI_SERVER_HOST', '0.0.0.0')
    port = int(os.getenv('AI_SERVER_PORT', '8000'))
    uvicorn.run('app.main:app', host=host, port=port, reload=False)

if __name__ == '__main__':  # pragma: no cover
    main()