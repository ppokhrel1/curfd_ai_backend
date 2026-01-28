# Documentation

## Overview
FastAPI backend for session management, chat records, and a 3D generation pipeline aligned with `app_diagram.xml`.

## Architecture Mapping (from app_diagram.xml)
- **User Inputs**: Session + Chat + Messages
- **LLM / Orchestrator**: Job creation and spec normalization
- **RAG / Vector DB**: Placeholder for retrieval services (future integration)
- **3D Generator / QA / Exporter**: Job lifecycle and asset registration
- **Object Storage / Metadata DB**: Asset records and metadata storage

## Core Entities
- **User**: Optional user profile for sessions
- **Session**: Tracks a user session and metadata
- **Chat**: Conversation container under a session
- **Message**: Individual chat messages
- **Job**: 3D generation workflow entry
- **Asset**: Output artifact (STL/GLB/images)

## API Overview
Base path: `/api/v1`

- **Sessions**: `POST /sessions`, `GET /sessions`, `GET /sessions/{id}`, `PATCH /sessions/{id}`, `DELETE /sessions/{id}`
- **Auth**: `POST /auth/register`, `POST /auth/login`
- **Chats**: `POST /chats`, `GET /chats`, `GET /chats/{id}`, `PATCH /chats/{id}`, `DELETE /chats/{id}`
- **Messages**: `POST /messages`, `GET /messages`, `GET /messages/{id}`, `DELETE /messages/{id}`
- **Jobs**: `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`, `PATCH /jobs/{id}`, `POST /jobs/{id}/start`, `POST /jobs/{id}/complete`
- **Assets**: `POST /assets`, `GET /assets`, `GET /assets/{id}`, `DELETE /assets/{id}`

## Notes
- Vector DB and object storage integrations are stubbed via metadata fields and asset records.
- Use Alembic for migrations when moving beyond local SQLite.
