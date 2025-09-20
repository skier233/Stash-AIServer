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
  app/
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
Then visit http://localhost:8000/docs for the Swagger UI.

During development, source changes under `backend/app` auto-reload thanks to `--reload` and bind mount.

## Manual Local Run (Without Docker)
```
python -m venv .venv
. .venv/Scripts/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload
```

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
