# Requests: User Creation to First Runpod Response

This file lists the API requests needed to go from a new user to receiving the first Runpod response via the chat socket.

## 1) Register a user
```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"password123"}'
```

## 2) Login (get access token)
```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"password123"}'
```

## 3) Create a session
```bash
curl -X POST http://localhost:8000/api/v1/sessions \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"status":"active"}'
```

## 4) Create a chat in the session
```bash
curl -X POST http://localhost:8000/api/v1/chats \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<session_id>","title":"First chat"}'
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
