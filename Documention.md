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
Base URL: `http://localhost:8000/api/v1`

### Health
- `GET /health`
```bash
curl http://localhost:8000/api/v1/health
```

### Auth
- `POST /auth/register`
```bash
curl -X POST http://localhost:8000/api/v1/auth/register \\
  -H "Content-Type: application/json" \\
  -d '{"username":"demo","email":"demo@example.com","password":"secret","display_name":"Demo"}'
```
- `POST /auth/login`
```bash
curl -X POST http://localhost:8000/api/v1/auth/login \\
  -H "Content-Type: application/json" \\
  -d '{"username_or_email":"demo","password":"secret"}'
```
Response includes a JWT:
```json
{"access_token":"<token>","token_type":"bearer","user_id":"<user_id>"}
```
If the access token is within 10 minutes of expiry, responses include refreshed headers:
- `X-Refresh-Token`
- `X-Refresh-Token-Expires-At`
- `GET /auth/me`
```bash
curl http://localhost:8000/api/v1/auth/me \\
  -H "Authorization: Bearer <access_token>"
```
- `POST /auth/logout`
```bash
curl -X POST http://localhost:8000/api/v1/auth/logout \\
  -H "Authorization: Bearer <access_token>"
```

### Sessions
- `POST /sessions`
```bash
curl -X POST http://localhost:8000/api/v1/sessions \\
  -H "Authorization: Bearer <access_token>" \\
  -H "Content-Type: application/json" \\
  -d '{"user_id":null,"status":"active","metadata_json":{"source":"web"}}'
```
- `GET /sessions`
```bash
curl http://localhost:8000/api/v1/sessions \\
  -H "Authorization: Bearer <access_token>"
```
- `GET /sessions/{session_id}`
```bash
curl http://localhost:8000/api/v1/sessions/<session_id> \\
  -H "Authorization: Bearer <access_token>"
```
- `PATCH /sessions/{session_id}`
```bash
curl -X PATCH http://localhost:8000/api/v1/sessions/<session_id> \\
  -H "Authorization: Bearer <access_token>" \\
  -H "Content-Type: application/json" \\
  -d '{"status":"inactive","metadata_json":{"reason":"timeout"}}'
```
- `DELETE /sessions/{session_id}`
```bash
curl -X DELETE http://localhost:8000/api/v1/sessions/<session_id>
```

### Chats
- `POST /chats`
```bash
curl -X POST http://localhost:8000/api/v1/chats \\
  -H "Authorization: Bearer <access_token>" \\
  -H "Content-Type: application/json" \\
  -d '{"session_id":"<session_id>","title":"First chat"}'
```
- `GET /chats` (requires `session_id`)
```bash
curl "http://localhost:8000/api/v1/chats?session_id=<session_id>" \\
  -H "Authorization: Bearer <access_token>"
```
- `GET /chats/{chat_id}`
```bash
curl http://localhost:8000/api/v1/chats/<chat_id> \\
  -H "Authorization: Bearer <access_token>"
```
- `PATCH /chats/{chat_id}`
```bash
curl -X PATCH http://localhost:8000/api/v1/chats/<chat_id> \\
  -H "Authorization: Bearer <access_token>" \\
  -H "Content-Type: application/json" \\
  -d '{"title":"Renamed chat"}'
```
- `DELETE /chats/{chat_id}`
```bash
curl -X DELETE http://localhost:8000/api/v1/chats/<chat_id> \\
  -H "Authorization: Bearer <access_token>"
```

### Messages
- `POST /messages`
```bash
curl -X POST http://localhost:8000/api/v1/messages \\
  -H "Authorization: Bearer <access_token>" \\
  -H "Content-Type: application/json" \\
  -d '{"chat_id":"<chat_id>","role":"user","content":"Hello","tokens":5}'
```
- `GET /messages` (optional filter: `chat_id`)
```bash
curl "http://localhost:8000/api/v1/messages?chat_id=<chat_id>" \\
  -H "Authorization: Bearer <access_token>"
```
- `GET /messages/{message_id}`
```bash
curl http://localhost:8000/api/v1/messages/<message_id> \\
  -H "Authorization: Bearer <access_token>"
```
- `DELETE /messages/{message_id}`
```bash
curl -X DELETE http://localhost:8000/api/v1/messages/<message_id> \\
  -H "Authorization: Bearer <access_token>"
```

### Chat Socket + Runpod
- `WS /chat-socket/{chat_id}` (query param `token`)
```bash
ws://localhost:8000/api/v1/chat-socket/<chat_id>?token=<access_token>
```
- `POST /chats/{chat_id}/runpod`
```bash
curl -X POST http://localhost:8000/api/v1/chats/<chat_id>/runpod \\
  -H "Authorization: Bearer <access_token>" \\
  -H "Content-Type: application/json" \\
  -d '{"content":"Make me a table for flying","action":"process_requirements"}'
```
```bash
curl -X POST http://localhost:8000/api/v1/chats/<chat_id>/runpod \\
  -H "Authorization: Bearer <access_token>" \\
  -H "Content-Type: application/json" \\
  -d '{"content":"Generate SCAD","action":"generate_scad","requirements_json":{"constraints":{"environment":"outdoor","size":"medium"}}}'
```
```bash
curl -X POST http://localhost:8000/api/v1/chats/<chat_id>/runpod \\
  -H "Authorization: Bearer <access_token>" \\
  -H "Content-Type: application/json" \\
  -d '{"action":"health","sync":true}'
```

Socket request payload (send over WS after connecting):
```json
{
  "type": "runpod.request",
  "payload": {
    "action": "process_requirements",
    "content": "Design a small quadcopter frame",
    "history": [
      {"role": "user", "content": "I need a lightweight drone"}
    ]
  }
}
```

Socket events (examples):
```json
{"type":"runpod.queued","chat_id":"<chat_id>","runpod_id":"<runpod_id>","message_id":"<message_id>","status":"queued"}
```
```json
{"type":"runpod.completed","chat_id":"<chat_id>","runpod_id":"<runpod_id>","message":{"id":"<message_id>","role":"assistant","content":"<output>"},"output":{"status":"success"}}
```

### Jobs
- `POST /jobs`
```bash
curl -X POST http://localhost:8000/api/v1/jobs \\
  -H "Authorization: Bearer <access_token>" \\
  -H "Content-Type: application/json" \\
  -d '{"session_id":"<session_id>","prompt":"a red chair","output_format":"glb"}'
```
- `GET /jobs` (optional filter: `session_id`)
```bash
curl "http://localhost:8000/api/v1/jobs?session_id=<session_id>" \\
  -H "Authorization: Bearer <access_token>"
```
- `GET /jobs/{job_id}`
```bash
curl http://localhost:8000/api/v1/jobs/<job_id> \\
  -H "Authorization: Bearer <access_token>"
```
- `PATCH /jobs/{job_id}`
```bash
curl -X PATCH http://localhost:8000/api/v1/jobs/<job_id> \\
  -H "Authorization: Bearer <access_token>" \\
  -H "Content-Type: application/json" \\
  -d '{"status":"running"}'
```
- `POST /jobs/{job_id}/start`
```bash
curl -X POST http://localhost:8000/api/v1/jobs/<job_id>/start \\
  -H "Authorization: Bearer <access_token>"
```
- `POST /jobs/{job_id}/complete?success=true`
```bash
curl -X POST "http://localhost:8000/api/v1/jobs/<job_id>/complete?success=true" \\
  -H "Authorization: Bearer <access_token>"
```

### Assets
- `POST /assets`
```bash
curl -X POST http://localhost:8000/api/v1/assets \\
  -H "Authorization: Bearer <access_token>" \\
  -H "Content-Type: application/json" \\
  -d '{"job_id":"<job_id>","asset_type":"glb","uri":"s3://bucket/file.glb"}'
```
- `GET /assets` (optional filter: `job_id`)
```bash
curl "http://localhost:8000/api/v1/assets?job_id=<job_id>" \\
  -H "Authorization: Bearer <access_token>"
```
- `GET /assets/{asset_id}`
```bash
curl http://localhost:8000/api/v1/assets/<asset_id> \\
  -H "Authorization: Bearer <access_token>"
```
- `DELETE /assets/{asset_id}`
```bash
curl -X DELETE http://localhost:8000/api/v1/assets/<asset_id> \\
  -H "Authorization: Bearer <access_token>"
```

### Asset Meta
- `POST /asset-meta`
```bash
curl -X POST http://localhost:8000/api/v1/asset-meta \\
  -H "Authorization: Bearer <access_token>" \\
  -H "Content-Type: application/json" \\
  -d '{"asset_id":"<asset_id>","part_name":"propeller","component_of":"<asset_id>","position_json":{"x":0,"y":0,"z":0},"image_paths_json":[{"face_direction":[0,0,0,1],"image_src":""}],"material_json":{"type":"plastic","thickness":"10mm"},"is_composite_of":["<asset_id>"],"used_for_json":["build air turbulence","engine coupling"]}'
```
- `GET /asset-meta` (optional filter: `asset_id`)
```bash
curl "http://localhost:8000/api/v1/asset-meta?asset_id=<asset_id>" \\
  -H "Authorization: Bearer <access_token>"
```
- `GET /asset-meta/{meta_id}`
```bash
curl http://localhost:8000/api/v1/asset-meta/<meta_id> \\
  -H "Authorization: Bearer <access_token>"
```
- `PATCH /asset-meta/{meta_id}`
```bash
curl -X PATCH http://localhost:8000/api/v1/asset-meta/<meta_id> \\
  -H "Authorization: Bearer <access_token>" \\
  -H "Content-Type: application/json" \\
  -d '{"part_name":"propeller v2","material_json":{"type":"carbon","thickness":"8mm"}}'
```
- `DELETE /asset-meta/{meta_id}`
```bash
curl -X DELETE http://localhost:8000/api/v1/asset-meta/<meta_id> \\
  -H "Authorization: Bearer <access_token>"
```

## Notes
- Vector DB and object storage integrations are stubbed via metadata fields and asset records.
- Use Alembic for migrations when moving beyond local SQLite.

## Supabase Integration (Optional)
Add these to `.env` if you want to call Supabase APIs:
- `SUPABASE_URL` (e.g., `https://<project>.supabase.co`)
- `SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_ROLE_KEY`

Minimal client wrapper lives in `app/services/supabase.py`. It exposes:
- `supabase_anon` for client-level calls
- `supabase_service` for server-level calls

Example usage:
```python
from app.services.supabase import supabase_anon

if supabase_anon:
    resp = supabase_anon.request(\"GET\", \"/rest/v1/some_table\", params={\"select\": \"*\"})
```
