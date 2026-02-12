# Chat Stream Documentation

This document describes the behavior implemented in `app/api/routes/chat_stream.py`.

## Base Routes

- HTTP: `POST /api/v1/chats/{chat_id}/runpod`
- WebSocket: `WS /api/v1/chat-socket/{chat_id}?token=<access_token>`

## Authentication and Authorization

### HTTP endpoint

- Uses bearer token auth via `get_current_user_id`.
- Validates chat ownership by checking the chat's session owner (`sessions.user_id`).
- Returns:
  - `404` if chat does not exist
  - `403` if chat belongs to another user

### WebSocket endpoint

- Requires `token` query parameter.
- Validates token by calling Supabase auth (`get_supabase_user`).
- Validates chat ownership using the same session-owner check.
- Closes socket with code `1008` on invalid token/ownership/chat id.

## HTTP Endpoint: `POST /chats/{chat_id}/runpod`

Accepts `ChatRunpodRequest` and returns `ChatRunpodResponse`.

### Supported actions

- `process_requirements`
  - requires `content`
- `generate_scad`
  - requires `requirements_json`
- `process_scad`
  - backward-compatible alias for `generate_scad`
  - requires `requirements_json`
- `health`

### Validation behavior

- `generate_scad` without `requirements_json` -> `422`
- `process_scad` without `requirements_json` -> `422` (after alias normalization)
- `process_requirements` without `content` -> `422`
- Runpod client/config error -> `500`
- Runpod call failure -> `502`

### Request side effects

1. If `content` is present, user message is stored in `messages`.
2. Request is sent to Runpod with:
   - `action`
   - `prompt` (only for `process_requirements`)
   - `requirements_json` (for `generate_scad` and `process_scad`)
   - normalized `history` from request, or fallback to stored messages in the same chat
   - `sync`
   - optional timeout override via `metadata_json.status_timeout_seconds`
   - `process_scad` is normalized to `generate_scad` before Runpod submission
3. If `sync=true`:
   - waits for output
   - stores assistant message immediately
   - returns `status="completed"`
4. If `sync=false`:
   - returns `status="queued"` with `runpod_id`
   - background polling task tracks job status and emits socket events

## Generate SCAD Asset Persistence

When action is `generate_scad`, the code attempts to persist generated assets.
(`process_scad` follows the same path after normalization.)

### Output extraction

Looks for asset data in:
- `output["data"]`
- `output["output"]["data"]`

Requires `download_url` to proceed.

### Persistence behavior

- Resolves existing job from `metadata_json.job_id` if provided.
- If job is missing, creates a new `jobs` row linked to the chat's session.
- Creates an `assets` row with:
  - `asset_type` (default `scad_zip`)
  - `uri` = `download_url`
  - `storage_provider` (default `b2`)
  - metadata including runpod details
- Creates `asset_meta` row (`part_name`, `uploaded_by`).

If persistence fails, assistant output includes an error payload and processing continues.

## Background Polling (`sync=false`)

`_runpod_poll_and_emit` sends these chat-socket events:

- `chat.history` (sent immediately on socket connect with persisted messages)
- `runpod.started`
- `runpod.status` (with incremental status updates)
- `runpod.completed`
- `runpod.failed`
- `runpod.timeout`

Polling stops when:
- job completes
- job fails/cancels/times out/errors
- deadline (`runpod_status_timeout_seconds`) is exceeded

Default poll timeout is `7200` seconds unless overridden per request with
`metadata_json.status_timeout_seconds`.

## Socket Message Contract

Client must send:

```json
{
  "type": "runpod.request",
  "payload": {
    "action": "process_requirements",
    "content": "Design a small drone frame"
  }
}
```

Non-JSON payloads, unsupported message types, or invalid payload models are ignored.

## Typical Flow

1. Client calls `POST /chats/{chat_id}/runpod` or sends `runpod.request` over WS.
2. Backend stores user message (if content exists).
3. Backend submits Runpod job.
4. Backend emits queue/start/status/completion events to chat socket listeners.
5. Backend stores assistant messages for completion/failure.
6. For `generate_scad`, backend also stores generated asset records.
   (`process_scad` also stores assets because it is normalized to `generate_scad`.)

## Notes

- This route module assumes chat ownership is represented by `sessions.user_id`.
- WebSocket auth uses token query param, not Authorization headers.
- JSON outputs are normalized to be datetime-safe before persistence and socket emission.

## CadQuery Integration

While this document focuses on Runpod-based chat generation, the system also supports direct CadQuery script execution via:
- `POST /api/v1/cadquery/generate`
- `POST /api/v1/cadquery/upload`
- `WS /api/v1/cadquery/ws/{task_id}`

These endpoints run on a dedicated Celery worker with a specialized environment (`.venv-cad`) containing `cadquery` and `OCP`. They provide an alternative to the Runpod flow for local generation.
