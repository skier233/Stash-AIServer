from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.core.config import settings
from app.api import interactions as interactions_router
from app.api import actions as actions_router
from app.api import tasks as tasks_router
from app.api import ws as ws_router
from app.api import recommendations as recommendations_router
from app.api import plugins as plugins_router
from app.recommendations.registry import recommender_registry
from app.recommendations.models import RecContext
from app.tasks.manager import manager
from app.db.session import engine, Base
import pathlib, hashlib, os
from contextlib import asynccontextmanager
from app.plugin_runtime.loader import initialize_plugins
from app.core.system_settings import seed_system_settings, get_value as sys_get
from app.services import registry as services_registry  # registry remains for core non-plugin definitions (if any)



@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler: run startup actions here instead of @app.on_event.

    We start the in-memory task manager and load plugins (which themselves
    register services, actions, and recommenders). Legacy autodiscovery of
    services/recommenders has been removed in favor of the explicit plugin
    system.
    """
    # Setup / startup

    if os.getenv('AIO_DEVMODE'):
        try:
            h = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()[:12]
            print(f'[dev] main.py sha256 {h}', flush=True)
        except Exception as _e:
            print(f'[dev] hash error: {_e}', flush=True)

    # Seed system (global) settings table entries before plugin load.
    try:
        seed_system_settings()
    except Exception as e:
        print(f"[system_settings] seed error: {e}", flush=True)

    # Load plugins (migrations + registration via decorator imports)
    try:
        initialize_plugins()
    except Exception as e:  # plugin loading errors are logged internally; keep startup going
        print(f"[plugin] unexpected loader exception: {e}", flush=True)

    # Start background task manager with configured loop interval / debug flags
    try:
        loop_interval = float(sys_get('TASK_LOOP_INTERVAL', 0.05) or 0.05)
    except Exception:
        loop_interval = 0.05
    manager._loop_interval = loop_interval  # internal tweak before start
    manager._debug = bool(sys_get('TASK_DEBUG', False))
    await manager.start()

    # Diagnostic count of registered recommenders (populated by plugins)
    print(f"[recommenders] initialized count={len(recommender_registry._defs)}", flush=True)

    # Yield control to application runtime
    yield

    # Shutdown placeholder (graceful task manager stop could go here later)
    # Currently no explicit teardown required.


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    try:
        body = await request.body()
    except Exception:
        body = b''
    print('[validation_error] url=', request.url, 'body=', body.decode(errors='replace'), 'errors=', exc.errors(), flush=True)
    return JSONResponse(status_code=422, content={'detail': exc.errors()})


# Routers
app.include_router(actions_router.router, prefix=settings.api_v1_prefix)
app.include_router(tasks_router.router, prefix=settings.api_v1_prefix)
app.include_router(ws_router.router, prefix=settings.api_v1_prefix)
app.include_router(recommendations_router.router, prefix=settings.api_v1_prefix)
app.include_router(plugins_router.router, prefix=settings.api_v1_prefix)
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



