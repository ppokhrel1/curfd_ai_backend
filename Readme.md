# CURFD AI Backend

FastAPI backend for session management, chat records, and 3D generation workflow support aligned with `app_diagram.xml`.

## Features
- Session and chat management
- User registration and login (bcrypt password hashing)
- Message storage
- 3D generation job tracking
- Asset registration for outputs (STL/GLB/images)
- SQLite default, Alembic-ready

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
- SQLite database file: `curfd_ai.db`
- Migrations: `alembic` folder ready for `alembic revision` and `alembic upgrade`
