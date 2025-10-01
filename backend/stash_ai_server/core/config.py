from pathlib import Path
from pydantic import BaseModel
import os
from stash_ai_server import __version__

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

_diagnostics: list[str] = []

env_data_dir = os.getenv('AI_SERVER_DATA_DIR')

# Build ordered candidate list (dedup while preserving order)
_candidates = []
for c in [env_data_dir, '/app/data', str(Path.cwd() / 'data')]:
    if c and c not in _candidates:
        _candidates.append(c)

data_dir = None
for cand in _candidates:
    p = Path(cand)
    try:
        p.mkdir(parents=True, exist_ok=True)
        data_dir = p
        _diagnostics.append(f"selected_data_dir={p} (candidate)")
        break
    except Exception as e:  # pragma: no cover
        _diagnostics.append(f"candidate_failed path={p} err={e}")
        continue

if data_dir is None:
    data_dir = Path(__file__).resolve().parent.parent.parent / 'data'
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        _diagnostics.append(f"fallback_site_packages_dir={data_dir}")
    except Exception as e:  # pragma: no cover
        _diagnostics.append(f"fatal_failed_create_fallback path={data_dir} err={e}")

# If an env override was explicitly provided but we somehow fell back to a site-packages path, prefer the env path lazily.
if env_data_dir and data_dir and 'site-packages' in str(data_dir) and Path(env_data_dir).exists():
    env_p = Path(env_data_dir)
    try:
        env_p.mkdir(parents=True, exist_ok=True)
        _diagnostics.append(f"override_env_data_dir_applied={env_p}")
        data_dir = env_p
    except Exception as e:  # pragma: no cover
        _diagnostics.append(f"override_env_data_dir_failed path={env_p} err={e}")

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
    diagnostics: list[str] | None = _diagnostics

settings = Settings()

