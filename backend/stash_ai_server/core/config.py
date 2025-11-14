from pathlib import Path
from pydantic import BaseModel
import os
from stash_ai_server import __version__
# Optionally load a repo-level config.env file for local/conda development so
# users can keep secrets out of docker-compose and environment dumps. The
# project includes `backend/config.sample.env` — copy it to `backend/config.env`.
try:
    from dotenv import load_dotenv
    # Allow explicit override of config file path
    cfg_override = os.getenv('AI_SERVER_CONFIG_FILE')
    candidates = []
    if cfg_override:
        candidates.append(Path(cfg_override))

    # Prefer the working directory (where docker-compose or the user runs the process)
    candidates.append(Path.cwd() / 'config.env')
    candidates.append(Path.cwd() / 'backend' / 'config.env')

    for p in candidates:
        try:
            if p and p.exists():
                load_dotenv(str(p))
                break
        except Exception:
            continue
except Exception:
    # If python-dotenv isn't available or load fails, fall back to env vars
    pass

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


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


_docker_mode = _env_flag("DOCKER")
if _docker_mode:
    _diagnostics.append("docker_mode=true")

env_data_dir = os.getenv('AI_SERVER_DATA_DIR')

# Build ordered candidate list (dedup while preserving order)
_candidates = []
for c in [env_data_dir, str(Path.cwd() / 'data'), '/app/data']:
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
    # Logging level for the backend (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    # Can be set via the environment variable AI_SERVER_LOG_LEVEL
    log_level: str = os.getenv('AI_SERVER_LOG_LEVEL', 'DEBUG')
    docker_mode: bool = _docker_mode
    diagnostics: list[str] | None = _diagnostics

settings = Settings()

