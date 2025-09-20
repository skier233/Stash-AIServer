from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api import requests as requests_router
from app.api import actions as actions_router
from app.db.session import engine, Base
from app.services.ai import register as register_ai_service

# Ensure tables exist if migrations not yet run (dev convenience)
Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.app_name)

# Register services & their actions (later could auto-discover)
register_ai_service()

# Routers
app.include_router(requests_router.router, prefix=settings.api_v1_prefix)
app.include_router(actions_router.router, prefix=settings.api_v1_prefix)

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
