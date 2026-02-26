# CURFD AI Backend

FastAPI backend for session management, chat records, and 3D generation workflow support aligned with `app_diagram.xml`.

## Features
- Session and chat management
- User registration and login (bcrypt password hashing)
- Message storage
- 3D generation job tracking
- **CadQuery Generation**: Async service for generating CAD models from Python scripts (STL/STEP)
- Asset registration for outputs (STL/GLB/images)
- Postgres default, Alembic-ready

## Workflow Overview
The backend follows the pipeline in `app_diagram.xml`:
- **User → Session → Chat → Message**: a user starts a session, opens chats, and stores messages.
- **Session → Job**: a 3D generation request is tracked as a job under a session.
- **Job → Asset**: generated outputs (STL/GLB/images) are stored as assets linked to the job.

## Data Model Connections
Key relationships from `app/models`:
- `User` → `Session` (one-to-many) via `sessions.user_id`
- `Session` → `Chat` (one-to-many) via `chats.session_id`
- `Chat` → `Message` (one-to-many) via `messages.chat_id`
- `Session` → `Job` (one-to-many) via `jobs.session_id`
- `Job` → `Asset` (one-to-many) via `assets.job_id`
- `RevokedToken` stores revoked JWT hashes for logout/invalidation

These relationships allow you to:
- fetch all chats/messages for a session
- list all jobs and assets for a session
- enforce user ownership across sessions and related records

## Quickstart
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

## API Base
`/api/v1`

## Development Notes
- Postgres DB: `postgresql+asyncpg://curfd:curfd@localhost:5432/curfd_ai`
- Supabase DB: set `SUPABASE_DB_URL` in `.env` to override
- Supabase CLI: `python -m app.cli supabase-get /rest/v1/users --param select=*`
- Supabase API: set `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` if you want to call Supabase APIs
- Migrations: `alembic` folder ready for `alembic revision` and `alembic upgrade`
- Runpod: set `RUNPOD_API_TOKEN` (and optionally `RUNPOD_BASE_URL`) in `.env` to enable chat runpod flow
- Runpod actions: `process_requirements`, `generate_scad`, `health` (history + sync supported via `/chats/{chat_id}/runpod` or the chat socket)
