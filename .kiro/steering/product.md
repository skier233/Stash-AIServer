# Product Overview

**Stash AI Server** is a backend server that provides AI-powered features, task management, and extensible plugins for Stash (a media management application).

## Core Features

- **AI-powered recommendations** - Scene recommendation system with pluggable algorithms
- **Task management** - Background task processing with WebSocket updates
- **Plugin system** - Extensible architecture for custom recommenders and actions
- **Database integration** - PostgreSQL with pgvector for AI/ML operations
- **API endpoints** - RESTful API with FastAPI framework
- **Frontend integration** - TypeScript/React components for Stash UI integration

## Architecture

The system follows a plugin-based architecture where:
- Backend plugins provide recommenders, actions, and services
- Frontend components integrate with Stash's existing UI
- Database stores interactions, recommendations, and AI results
- Task manager handles background processing with real-time updates

## Target Users

Developers extending Stash with AI capabilities, particularly for content recommendation and automated tagging systems.