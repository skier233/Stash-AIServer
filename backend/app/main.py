from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api import requests as requests_router
from app.api import actions as actions_router
from app.api import tasks as tasks_router
from app.api import ws as ws_router
from app.api import recommendations as recommendations_router
from app.tasks.manager import manager
from app.db.session import engine, Base
import importlib, pkgutil, pathlib, hashlib, os, sys
from app.services import registry as services_registry  # ensures registry defined

# Ensure tables exist if migrations not yet run (dev convenience)
Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.app_name)

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
