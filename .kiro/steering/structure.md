# Project Structure

## Root Layout
```
├── backend/           # Python FastAPI server
├── frontend/          # TypeScript React components  
├── data/             # Database and runtime data
├── scripts/          # Installation and setup scripts
├── tools/            # Build and deployment utilities
└── docker-compose.yml # Container orchestration
```

## Backend Structure (`backend/`)
```
├── stash_ai_server/          # Main application package
│   ├── api/                  # FastAPI route handlers
│   ├── core/                 # Configuration, logging, runtime
│   ├── db/                   # Database session, migrations
│   ├── models/               # SQLAlchemy ORM models
│   ├── plugin_runtime/       # Plugin loading and management
│   ├── recommendations/      # Recommendation system core
│   ├── schemas/              # Pydantic request/response models
│   ├── services/             # Business logic services
│   ├── tasks/                # Background task management
│   └── utils/                # Shared utilities
├── plugins/                  # Plugin implementations
├── alembic/                  # Database migration scripts
├── tests/                    # Test suite
└── pyproject.toml           # Python package configuration
```

## Frontend Structure (`frontend/`)
```
├── src/                     # TypeScript source files
│   ├── css/                 # Stylesheets
│   ├── *.tsx               # React components
│   └── *.ts                # Utility modules
├── dist/                   # Build output (generated)
├── build.js               # Custom build script
└── package.json           # Node.js dependencies
```

## Plugin Architecture
- **Location**: `backend/plugins/{plugin_name}/`
- **Required**: `plugin.yml` (metadata) + implementation files
- **Types**: Recommenders, actions, services
- **Registration**: Automatic via plugin loader during startup

## Key Conventions
- **API Routes**: Organized by feature in `api/` modules
- **Models**: SQLAlchemy models in `models/`, Pydantic schemas in `schemas/`
- **Configuration**: Environment-based via `core/config.py`
- **Migrations**: Alembic auto-generation from model changes
- **Frontend**: Standalone components, no shared state management
- **Testing**: pytest with async support, fixtures in `conftest.py`

## Data Flow
1. **Plugins** register recommenders/actions during startup
2. **API endpoints** handle requests via FastAPI routers
3. **Services** contain business logic, interact with database
4. **Task manager** processes background work with WebSocket updates
5. **Frontend components** integrate with Stash UI via direct script injection