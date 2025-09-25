from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api import requests as requests_router
from app.api import interactions as interactions_router
from app.api import actions as actions_router
from app.api import tasks as tasks_router
from app.api import ws as ws_router
from app.api import recommendations as recommendations_router
from app.recommendations.registry import autodiscover as _auto_discover_recommenders, recommender_registry
from app.recommendations.models import RecContext
from app.tasks.manager import manager
from app.db.session import engine, Base
import importlib, pkgutil, pathlib, hashlib, os, sys
from app.services import registry as services_registry  # ensures registry defined

# Ensure tables exist if migrations not yet run (dev convenience)
Base.metadata.create_all(bind=engine)

# Attempt to add missing columns for dev convenience (safe if column already exists)
try:
    with engine.connect() as conn:
        # SQLite allows ADD COLUMN; ignore if it fails
        conn.execute("ALTER TABLE interaction_sessions ADD COLUMN session_start_ts DATETIME DEFAULT (CURRENT_TIMESTAMP)")
except Exception:
    pass

app = FastAPI(title=settings.app_name)

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi import Request


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    try:
        body = await request.body()
    except Exception:
        body = b''
    print('[validation_error] url=', request.url, 'body=', body.decode(errors='replace'), 'errors=', exc.errors(), flush=True)
    return JSONResponse(status_code=422, content={'detail': exc.errors()})

# Auto-discover and register service packages (each may expose register())
def _auto_register_services():
    services_path = pathlib.Path(__file__).parent / 'services'
    if not services_path.exists():
        print('[services] directory missing:', services_path, flush=True)
        return
    for m in pkgutil.iter_modules([str(services_path)]):
        name = m.name
        full = f'app.services.{name}'
        try:
            mod = importlib.import_module(full)
            if hasattr(mod, 'register'):
                mod.register()
                print(f'[services] registered {full}', flush=True)
            else:
                print(f'[services] no register() in {full}', flush=True)
        except Exception as e:
            print(f'[services] failed {full}: {e}', flush=True)

_auto_register_services()

if os.getenv('AIO_DEVMODE'):
    try:
        h = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()[:12]
        print(f'[dev] main.py sha256 {h}', flush=True)
    except Exception as _e:
        print(f'[dev] hash error: {_e}', flush=True)

# Routers
app.include_router(requests_router.router, prefix=settings.api_v1_prefix)
app.include_router(actions_router.router, prefix=settings.api_v1_prefix)
app.include_router(tasks_router.router, prefix=settings.api_v1_prefix)
app.include_router(ws_router.router, prefix=settings.api_v1_prefix)
app.include_router(recommendations_router.router, prefix=settings.api_v1_prefix)
app.include_router(interactions_router.router, prefix=settings.api_v1_prefix)

# Basic CORS (development) – restrict/adjust later as needed
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

@app.get('/')
async def root():
    return {'status': 'ok', 'app': settings.app_name}


@app.on_event('startup')
async def _start_task_manager():
    await manager.start()

@app.on_event('startup')
async def _init_recommenders():
    """Pre-load recommender modules at startup so first request is fast and
    failures surface early."""
    _auto_discover_recommenders()
    if not recommender_registry.list_for_context(RecContext.global_feed):
        try:
            import importlib
            importlib.import_module('app.recommendations.recommenders.baseline_popularity.popularity')
            importlib.import_module('app.recommendations.recommenders.random_recent.random_recent')
        except Exception as e:
            print('[recommenders] startup fallback import error', e, flush=True)
    print(f"[recommenders] initialized count={len(recommender_registry._defs)}", flush=True)
