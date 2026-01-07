# Technology Stack

## Backend
- **Framework**: FastAPI (Python 3.12+)
- **Database**: PostgreSQL with pgvector extension
- **ORM**: SQLAlchemy 2.0+ with Alembic migrations
- **API**: RESTful endpoints + WebSocket support
- **Task Management**: Custom async task manager
- **Dependencies**: Pydantic, httpx, stashapi, PyYAML

## Frontend
- **Language**: TypeScript
- **Framework**: React (components for Stash integration)
- **Build**: Custom Node.js build script (no bundler)
- **Output**: IIFE-wrapped modules for direct browser usage

## Infrastructure
- **Containerization**: Docker with docker-compose
- **Database**: pgvector/pgvector:pg16 image
- **Development**: Live reload with volume mounts

## Common Commands

### Backend Development
```bash
# Install dependencies (conda environment recommended)
cd backend
pip install -e .

# Run development server
python -m stash_ai_server.dev_entrypoint

# Database migrations
alembic upgrade head
alembic revision --autogenerate -m "description"

# Run tests
pytest
```

### Frontend Development
```bash
cd frontend
npm install
npm run build
npm run clean
```

### Docker Development
```bash
# Start full stack
docker-compose up backend_dev postgres

# Production build
docker-compose up backend_prod postgres

# Database only
docker-compose up postgres
```

## Build System Notes
- Backend uses setuptools with pyproject.toml
- Frontend uses custom build.js (no webpack/vite)
- TypeScript compiled to ES2019 CommonJS modules
- CSS and other assets copied directly to dist/
- IIFE wrapping prevents global namespace pollution