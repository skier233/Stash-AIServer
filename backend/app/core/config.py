from pathlib import Path
from pydantic import BaseModel
import os
from app import __version__

"""Central configuration.

Previously the database path was derived from the installed package location
(`site-packages/app/...`). In container/pip installs this could point to a
read‑only layer and (critically) would not align with the mounted volume at
`/app/data`. We now prefer explicit environment overrides and fall back to
`/app/data` if present, otherwise a local `data/` folder relative to CWD.

Env vars:
  AI_SERVER_DATA_DIR  - directory for writable application data (created)
  AI_SERVER_DB_PATH   - explicit path to SQLite db file (overrides DATA dir)
  AI_SERVER_VERSION   - override reported version
"""

DEFAULT_DATA_DIRS = [
    os.getenv('AI_SERVER_DATA_DIR'),
    '/app/data',  # container runtime working directory
    str(Path.cwd() / 'data'),
]

data_dir = None
for cand in DEFAULT_DATA_DIRS:
    if not cand:
        continue
    p = Path(cand)
    try:
        p.mkdir(parents=True, exist_ok=True)
        data_dir = p
        break
    except Exception:  # pragma: no cover - fallback logic
        continue

if data_dir is None:  # final fallback near this file
    data_dir = Path(__file__).resolve().parent.parent.parent / 'data'
    data_dir.mkdir(parents=True, exist_ok=True)

db_path = os.getenv('AI_SERVER_DB_PATH')
if db_path:
    db_path = Path(db_path)
else:
    db_path = data_dir / 'app.db'

class Settings(BaseModel):
    app_name: str = 'AI Overhaul Backend'
    database_url: str = f'sqlite:///{db_path}'
    api_v1_prefix: str = '/api/v1'
    version: str = os.getenv('AI_SERVER_VERSION', __version__)
    data_dir: Path = data_dir
    db_file: Path = db_path

settings = Settings()

