# Requests: User Creation to First Runpod Response

This file lists the API requests needed to go from a new user to receiving the first Runpod response via the chat socket.

## 1) Register a user
```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"user1","email":"user@example.com","password":"password123"}'
```
Response (example):
```json
{
  "id": "4b8b6d6c-1c5a-4dd1-9f46-9c3c7d9c1a11",
  "created_at": "2026-01-30T12:00:00+00:00",
  "updated_at": "2026-01-30T12:00:00+00:00",
  "username": "user1",
  "email": "user@example.com",
  "display_name": null
}
```

## 2) Login (get access token)
```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username_or_email":"user@example.com","password":"password123"}'
```
Response (example):
```json
{
  "access_token": "<access_token>",
  "token_type": "bearer",
  "user_id": "4b8b6d6c-1c5a-4dd1-9f46-9c3c7d9c1a11"
}
```

## 3) Create a session
```bash
curl -X POST http://localhost:8000/api/v1/sessions \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"status":"active"}'
```
Response (example):
```json
{
  "id": "0ef0b5a7-2b7e-4e0f-9c7b-6e1db75c8d2d",
  "created_at": "2026-01-30T12:01:00+00:00",
  "updated_at": "2026-01-30T12:01:00+00:00",
  "user_id": "4b8b6d6c-1c5a-4dd1-9f46-9c3c7d9c1a11",
  "status": "active",
  "last_active_at": "2026-01-30T12:01:00+00:00",
  "metadata_json": null
}
```

## 4) Create a chat in the session
```bash
curl -X POST http://localhost:8000/api/v1/chats \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<session_id>","title":"First chat"}'
```
Response (example):
```json
{
  "id": "9e99e7d9-9f12-46b2-9b02-7e1f4f4f0c34",
  "created_at": "2026-01-30T12:02:00+00:00",
  "updated_at": "2026-01-30T12:02:00+00:00",
  "session_id": "0ef0b5a7-2b7e-4e0f-9c7b-6e1db75c8d2d",
  "title": "First chat"
}
```

## 5) Open the chat socket
```bash
# Use your websocket client of choice
ws://localhost:8000/api/v1/chat-socket/<chat_id>?token=<access_token>
```

## 6) Start a Runpod request (process requirements)
```bash
curl -X POST http://localhost:8000/api/v1/chats/<chat_id>/runpod \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"content":"Make me a table for flying","action":"process_requirements"}'
```
Response (example):
```json
{
  "status": "queued",
  "runpod_id": "74f215e6-835c-4cb2-9311-f33bb377fca9-e2",
  "message_id": "2d72f8a4-9b9b-44c8-89a8-0d8adcc6f6b0"
}
```

## 6b) Start a Runpod request over the socket
Send this JSON payload over the open WebSocket connection:
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

## 7) Listen for the first Runpod response on the socket
Expected socket events (examples):
```json
{"type":"runpod.started","chat_id":"<chat_id>","runpod_id":"<runpod_id>","action":"process_requirements"}
```
```json
{"type":"runpod.status","chat_id":"<chat_id>","runpod_id":"<runpod_id>","status":"IN_QUEUE"}
```
```json
{
  "type":"runpod.completed",
  "chat_id":"<chat_id>",
  "runpod_id":"<runpod_id>",
  "message": {"id":"<message_id>","role":"assistant","content":"<output>"},
  "output": {"status":"success"}
}
```

---

### Optional: generate_scad request
```bash
curl -X POST http://localhost:8000/api/v1/chats/<chat_id>/runpod \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"content":"Generate SCAD","action":"generate_scad","requirements_json":{"constraints":{"environment":"outdoor","size":"medium"}}}'
```
Response (example):
```json
{
  "status": "queued",
  "runpod_id": "0477ef6a-7152-4941-86df-836b84951a0e-e2",
  "message_id": "9c2c5d3c-1a6f-4a83-a247-0a2f6d3c1d11"
}
```

---

## Example run (happy path)
```bash
# 1) Register
curl -s -X POST http://localhost:8000/api/v1/auth/register \\
  -H "Content-Type: application/json" \\
  -d '{\"username\":\"user1\",\"email\":\"user@example.com\",\"password\":\"password123\"}'

# 2) Login
curl -s -X POST http://localhost:8000/api/v1/auth/login \\
  -H "Content-Type: application/json" \\
  -d '{\"username_or_email\":\"user@example.com\",\"password\":\"password123\"}'

# 3) Create session
curl -s -X POST http://localhost:8000/api/v1/sessions \\
  -H "Authorization: Bearer <access_token>" \\
  -H "Content-Type: application/json" \\
  -d '{\"status\":\"active\"}'

# 4) Create chat
curl -s -X POST http://localhost:8000/api/v1/chats \\
  -H "Authorization: Bearer <access_token>" \\
  -H "Content-Type: application/json" \\
  -d '{\"session_id\":\"<session_id>\",\"title\":\"First chat\"}'

# 5) Start Runpod
curl -s -X POST http://localhost:8000/api/v1/chats/<chat_id>/runpod \\
  -H "Authorization: Bearer <access_token>" \\
  -H "Content-Type: application/json" \\
  -d '{\"content\":\"Make me a table for flying\",\"action\":\"process_requirements\"}'
```
