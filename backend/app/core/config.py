from pathlib import Path
from pydantic import BaseModel
import os
from app import __version__

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / 'app.db'

class Settings(BaseModel):
    app_name: str = 'AI Overhaul Backend'
    database_url: str = f'sqlite:///{DB_PATH}'
    api_v1_prefix: str = '/api/v1'
    version: str = os.getenv('AI_SERVER_VERSION', __version__)

settings = Settings()

