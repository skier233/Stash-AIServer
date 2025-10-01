from __future__ import annotations
import os
from app.core.migrations import run_migrations
from app.core.system_settings import seed_system_settings
from app.core.config import settings


def main():  # pragma: no cover
    print(f"[dev-entrypoint] starting dev server version={settings.version} db={settings.database_url}", flush=True)
    try:
        run_migrations()
    except Exception as e:
        print(f"[dev-entrypoint] migration failure: {e}", flush=True)
        raise
    try:
        seed_system_settings()
    except Exception as e:
        print(f"[dev-entrypoint] seed failure: {e}", flush=True)
    import uvicorn
    host = os.getenv('AI_SERVER_HOST', '0.0.0.0')
    port = int(os.getenv('AI_SERVER_PORT', '8000'))
    uvicorn.run('app.main:app', host=host, port=port, reload=True)


if __name__ == '__main__':
    main()
