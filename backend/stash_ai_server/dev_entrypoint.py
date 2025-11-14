from __future__ import annotations
import os
from stash_ai_server.core.migrations import run_migrations
from stash_ai_server.core.system_settings import seed_system_settings
from stash_ai_server.core.config import settings
from stash_ai_server.core.logging_config import configure_logging


def main():  # pragma: no cover
    configure_logging(settings.log_level)
    print(f"[dev-entrypoint] starting dev server version={settings.version} db={settings.database_url} log_level={settings.log_level}", flush=True)
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
    port = int(os.getenv('AI_SERVER_PORT', '4153'))
    uvicorn.run(
        'stash_ai_server.main:app',
        host=host,
        port=port,
        reload=True,
        log_level=settings.log_level.lower(),
        log_config=None,
    )


if __name__ == '__main__':
    main()
