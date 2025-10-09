# AI Overhaul Backend

Minimal FastAPI backend with SQLite + Alembic migrations.

## Features
- FastAPI application exposing `/api/v1/requests` CRUD (create + read/list now)
- SQLite database stored under `backend/data/app.db`
- SQLAlchemy 2.0 style models
- Alembic migration management
- Docker dev environment with live reload (code mounted as volume)

## Directory Layout
```
backend/
  stash_ai_server/
    api/          # Routers
    core/         # Config
    db/           # Session + base
    models/       # SQLAlchemy models
    schemas/      # Pydantic schemas
    main.py       # FastAPI entry
  alembic/        # Migration scripts
  alembic.ini     # Alembic config
  requirements.txt
  Dockerfile
```

## Run (Docker Compose)
```
docker compose up --build
```
Then visit http://localhost:4153/docs for the Swagger UI.

During development, source changes under `backend/stash_ai_server` auto-reload thanks to `--reload` and bind mount.

## Manual Local Run (Without Docker)
```
python -m venv .venv
. .venv/Scripts/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
uvicorn stash_ai_server.main:app --reload
```

## Configuration file

For secrets and a few runtime overrides (data dir, plugin dir, stash API key), copy `config.sample.env` to `backend/config.env` and edit values. This file is gitignored so your secrets won't be checked in.

Example:
```
cp backend/config.sample.env backend/config.env
# edit backend/config.env and then start the server
```

When running in Docker Compose the image expects `/app/data` and the compose file or secrets file should mount the appropriate host paths.

## Conda / pip install users

If you install the package from PyPI (or from the built wheel), the `alembic` folder and `alembic.ini` are included in the package so you can run migrations after creating `backend/config.env`. For local editable installs (`pip install -e .`) ensure you run from the repo root so `alembic` is present in the working tree.

## Apply Migrations
The container runs with `Base.metadata.create_all()` for convenience right now. To use Alembic migration fully:
```
# Inside container or local env (with PYTHONPATH set to backend):
alembic upgrade head
```

## Example Requests
```
POST /api/v1/requests
{
  "prompt": "Describe scenic imagery"
}

GET /api/v1/requests
GET /api/v1/requests/1
```

## Next Steps
- Add status update endpoint (PATCH)
- Introduce background processing queue
- Add filtering/pagination on list endpoint
- Add CORS config if frontend fetches directly
- Authentication (API key or session) when needed
