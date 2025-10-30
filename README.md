# Stash AI Server

Backend server for Stash providing AI-powered features, task management, and extensible plugin architecture.

## Overview

Stash AI Server is a FastAPI-based backend that extends [Stash](https://github.com/stashapp/stash) with AI capabilities, including scene recommendations, automated tagging, and a flexible task queue system.

## Features

- **AI Task Queue**: Async task processing with priority support and concurrency controls
- **Scene Recommendations**: Pluggable recommendation engines with configurable algorithms
- **Plugin System**: Dynamic plugin discovery and execution runtime
- **REST API**: Full API for task submission, status tracking, and plugin management
- **Database**: SQLAlchemy-based persistence with Alembic migrations

## Project Structure

```
├── backend/               # Python backend (FastAPI)
│   ├── stash_ai_server/  # Main application code
│   │   ├── api/          # REST API endpoints
│   │   ├── tasks/        # Task queue and manager
│   │   ├── recommendations/  # Recommendation engines
│   │   ├── actions/      # Action registry system
│   │   ├── plugin_runtime/   # Plugin loader and runtime
│   │   └── db/           # Database models and utilities
│   ├── tests/            # Test suite
│   └── Dockerfile        # Container build configuration
├── frontend/             # Minimal frontend plugin interface
└── .github/              # CI/CD workflows and automation
```

## Quick Start

### Configuration

```bash
# Copy sample config and configure settings
cp backend/config.sample.env backend/config.env
# Edit config.env with your Stash API key and preferences
```

### Development with Docker Compose

```bash
# Start development server with live reload
docker-compose up backend_dev

# Access API at http://localhost:4153
```

### Production Deployment

```bash
# Use pre-built image from GitHub Container Registry
docker-compose up backend_prod
```

### Manual Installation

```bash
cd backend
pip install -r requirements.txt
python -m stash_ai_server.entrypoint
```

## Development

- **Python**: Requires Python 3.12+
- **Testing**: Run `pytest` in the backend directory
