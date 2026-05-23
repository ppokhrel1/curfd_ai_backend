from __future__ import annotations

import asyncio
import io
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict
import zipfile

import aiohttp
import httpx
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user_id, get_supabase_user
from app.db.session import SessionLocal, get_db
from app.models.asset import Asset as AssetModel
from app.models.asset_meta import AssetMeta as AssetMetaModel
from app.models.chat import Chat as ChatModel
from app.models.job import Job as JobModel
from app.models.message import Message as MessageModel
from app.models.session import Session as SessionModel
from app.schemas.asset import AssetRead
from app.schemas.message import MessageRead
from app.schemas.runpod import ChatRunpodRequest, ChatRunpodResponse, ImageTo3DRequest, ImageTo3DResponse
from app.services.chat_socket import chat_socket_manager
from app.services.runpod import get_runpod_client, get_image_to_3d_client
from app.services.modal_client import get_image_to_3d_modal_client


def _get_image_to_3d_client():
    """Pick Modal or RunPod for `action="image_to_3d"` based on config.

    All other actions (modify_mesh, inpaint) keep using
    `get_image_to_3d_client()` → RunPod, since only image_to_3d has been
    ported to Modal. Set IMAGE_TO_3D_BACKEND=modal in .env to switch;
    flip back to runpod for an instant rollback.
    """
    from app.core.config import settings
    if settings.image_to_3d_backend.lower() == "modal":
        return get_image_to_3d_modal_client()
    return get_image_to_3d_client()
from app.services.openscad_agent import run_agent_stream
from app.api.routes.gemini_openscad_generate_route import (
    _build_lc_history,
    _save_parts_as_assets,
    _process_payload_images,
)
from app.api.routes.uploads import save_chat_image

# Module-level logger. Most functions in this file do their own
# `logger = logging.getLogger(__name__)` re-bind inside the function
# body, but module-scope helpers like `_safe_send_json` and
# `_upsert_picker_message` reference `logger` without that local
# binding. Without this top-level declaration the helpers crashed
# with NameError the first time they hit their error paths
# (WebSocketDisconnect log, picker upsert error log) — see
# https://github.com/ppokhrel1/curfd_ai_backend (the trace from
# StringDataRightTruncationError on the picker DB write, and the
# ClientDisconnected → NameError chain in _safe_send_json).
logger = logging.getLogger(__name__)

router = APIRouter()


class RequestContext(TypedDict, total=False):
    job_id: str | None
    storage_provider: str | None
    status_timeout_seconds: Any


class AssetContext(TypedDict, total=False):
    requirements_json: dict[str, Any] | None
    job_id: str | None
    asset_type: str | None
    storage_provider: str | None


def _serialize_message(message: MessageModel) -> dict[str, Any]:
    return MessageRead.model_validate(message).model_dump(mode="json")


async def _safe_send_json(ws: WebSocket, payload: dict) -> bool:
    """Send a JSON message over a WebSocket, swallowing every flavour of
    "the other side hung up" exception so background tasks (RunPod
    pollers, image-to-3d generators, etc.) don't crash when the client
    has already navigated away or the server is restarting mid-request.

    Returns True on success, False if the socket was already closed.

    Catches: RuntimeError ("after websocket.close"), starlette
    WebSocketDisconnect, uvicorn ClientDisconnected, and anything else
    that escapes — we never want a noisy traceback for "couldn't send
    a status update because the user closed their tab."

    NB: parameter is named `ws`, not `websocket`, so the call below
    doesn't match the bulk-replace pattern that introduced this helper
    (`await websocket.send_json(` → `await _safe_send_json(websocket, `)
    — that rewrite would turn this internal line into infinite recursion.
    """
    msg_type = "?"
    try:
        # Defensive: payload should be a dict but we don't trust it
        # enough to let an AttributeError here mask the real send error.
        if isinstance(payload, dict):
            msg_type = str(payload.get("type", "?"))
    except Exception:
        pass
    try:
        await ws.send_json(payload)
        return True
    except WebSocketDisconnect:
        logger.debug(f"[ws] dropped send for type={msg_type} — WebSocketDisconnect")
        return False
    except RuntimeError as e:
        # Common when the ASGI app keeps sending after the socket closed.
        if "websocket.close" in str(e) or "response already completed" in str(e):
            logger.debug(f"[ws] dropped send for type={msg_type} — client gone")
            return False
        raise
    except Exception as e:
        # Catch-all so a transient network/server error never escapes
        # a background task. We log INFO (not debug) because anything
        # non-disconnect-flavoured is worth seeing once in production.
        name = type(e).__name__
        if name in ("ClientDisconnected", "ConnectionClosedError", "ConnectionClosedOK"):
            logger.debug(f"[ws] dropped send for type={msg_type} — {name}")
        else:
            logger.info(f"[ws] send failed for type={msg_type}: {name}: {e}")
        return False


# ─── Picker candidate cache (WebSocket-resilient delivery) ────────────────
# When `_handle_generate_custom_image_ws` / `_handle_edit_candidate_ws`
# finish, the result is sent over the WS *and* stashed here keyed by
# `request_id`. If the WS dropped mid-task (server reload, network blip),
# the frontend recovers the candidate via `GET /picker/candidate/{rid}`.
# Memory-only with TTL — the R2 URL inside is durable for ~7 days.
import time as _time_mod

_PICKER_TTL_SECONDS = 600  # 10 min — way longer than any normal session gap.
_PICKER_RESULTS: dict[str, tuple[float, dict]] = {}
# Per-picker-session prompt log keyed by request_id. The picker reuses
# the same request_id across generate → edit → edit, so this accumulates
# the full prompt chain for a single visual session. Used to thread
# context into the Gemini call on edits so iteration carries intent
# (e.g. "make them gold" after "a bunny with stars" knows what 'them'
# refers to).
_PICKER_SESSIONS: dict[str, list[dict]] = {}


def _evict_expired_picker_results(now: float | None = None) -> None:
    """Drop stale entries. Called lazily on every read/write so we never
    need a background sweeper task. Sessions are evicted in lockstep
    with results since they share the same request_id keyspace."""
    now = now if now is not None else _time_mod.time()
    cutoff = now - _PICKER_TTL_SECONDS
    stale = [rid for rid, (ts, _) in _PICKER_RESULTS.items() if ts < cutoff]
    for rid in stale:
        _PICKER_RESULTS.pop(rid, None)
        _PICKER_SESSIONS.pop(rid, None)


def _cache_picker_result(request_id: str, payload: dict) -> None:
    """Store a candidate payload for later REST recovery. Overwrites any
    earlier cached entry for the same `request_id` (ready replaces error,
    error replaces ready, etc.) — the most recent state wins."""
    if not request_id:
        return
    now = _time_mod.time()
    _evict_expired_picker_results(now)
    _PICKER_RESULTS[request_id] = (now, payload)


def _get_picker_result(request_id: str) -> dict | None:
    if not request_id:
        return None
    now = _time_mod.time()
    _evict_expired_picker_results(now)
    entry = _PICKER_RESULTS.get(request_id)
    if entry is None:
        return None
    _, payload = entry
    return payload


def _append_picker_history(request_id: str, action: str, prompt: str) -> None:
    """Record a prompt in the picker's in-memory session log. `action` is
    one of "generate" / "edit"; `prompt` is the user's instruction.

    NB: in-memory only. To survive backend restarts / TTL eviction /
    page reloads, callers should ALSO persist via _persist_picker_history
    (which writes the same list into metadata_json.image_search_payload
    .prompt_history on the picker's DB row). Done in the caller because
    this function lacks chat_id.
    """
    if not request_id or not prompt:
        return
    _evict_expired_picker_results()
    session = _PICKER_SESSIONS.setdefault(request_id, [])
    session.append({"action": action, "prompt": prompt})


async def _persist_picker_history(chat_id: str, request_id: str) -> None:
    """Mirror the current in-memory session log into the DB so edits
    after a backend restart, TTL eviction, or browser reload still
    have the prompt chain available for context-threading.

    Fire-and-forget — DB errors are swallowed (persistence is a UX
    nicety, not correctness)."""
    session = _PICKER_SESSIONS.get(request_id) or []
    if not session:
        return
    try:
        await _upsert_picker_message(
            chat_id=chat_id,
            request_id=request_id,
            payload_patch={"prompt_history": list(session)},
        )
    except Exception:
        # _upsert_picker_message already swallows + logs internally; if it
        # somehow escapes, swallow here too.
        pass


async def _rehydrate_picker_history(request_id: str) -> None:
    """If the in-memory session log for `request_id` is empty (e.g. after
    a backend restart, TTL eviction, or first edit following a browser
    reload), repopulate it from the persisted DB row's
    metadata_json.image_search_payload.prompt_history. Best-effort: if
    nothing is there, the session stays empty and _build_edit_prompt_with_context
    falls through to the bare edit prompt.
    """
    if not request_id:
        return
    if _PICKER_SESSIONS.get(request_id):
        # In-memory copy is fresher; don't overwrite with stale DB state.
        return
    try:
        async with SessionLocal() as db:
            row = await db.get(MessageModel, request_id)
            if row is None:
                return
            meta = row.metadata_json or {}
            payload = meta.get("image_search_payload") or {}
            history = payload.get("prompt_history") or []
            if not isinstance(history, list) or not history:
                # Fallback: the candidate's bare prompt field is enough
                # to anchor at least one turn of context.
                cand = payload.get("candidate") or {}
                cand_prompt = cand.get("prompt")
                if isinstance(cand_prompt, str) and cand_prompt.strip():
                    history = [{"action": "generate", "prompt": cand_prompt}]
            if history:
                _PICKER_SESSIONS[request_id] = [
                    {"action": str(e.get("action") or ""),
                     "prompt":  str(e.get("prompt")  or "")}
                    for e in history
                    if isinstance(e, dict) and e.get("prompt")
                ]
    except Exception:
        pass


def _strip_persistence_bloat(payload: dict | None) -> dict | None:
    """Remove fields that bloat the persisted row without helping
    rehydration. Currently strips `candidate.runpod_url` — that's the
    base64 data URL the RunPod worker decodes inline; for UI rehydration
    we only need `candidate.url` (the R2 public URL). Storing the data
    URL pushed metadata_json over 1 MB per row in observation."""
    if not payload:
        return payload
    cand = payload.get("candidate")
    if isinstance(cand, dict) and "runpod_url" in cand:
        cand = {k: v for k, v in cand.items() if k != "runpod_url"}
        payload = {**payload, "candidate": cand}
    return payload


async def _upsert_picker_message(
    chat_id: str,
    request_id: str,
    image_search_payload: dict | None = None,
    payload_patch: dict | None = None,
    content: str | None = None,
) -> None:
    """Insert or update the persistent DB row backing this picker session.

    Message id is the bare `request_id` (a UUID, 36 chars) so it fits
    the messages.id VARCHAR(36) column. Earlier attempt used
    `img-search-{request_id}` (47 chars) and overflowed the column —
    StringDataRightTruncationError.

    Pass either:
      - `image_search_payload=…` to set the whole payload (e.g. at picker
        creation, when we have image_urls + search_query + request_id).
      - `payload_patch=…` to merge fields into the existing payload (e.g.
        after candidate_ready, when we only want to add the candidate
        field without overwriting image_urls).

    Picker candidates persisted here drop `runpod_url` (the data URL
    only used during the RunPod send) — see _strip_persistence_bloat.

    All errors are swallowed: persistence is a UX-niceness, not a
    correctness requirement. WS delivery is still the primary path.
    """
    if not chat_id or not request_id:
        return
    msg_id = request_id
    # Logger here would otherwise NameError — this helper runs at module
    # scope and chat_stream.py doesn't define a module-level `logger`.
    _logger = logging.getLogger(__name__)
    try:
        async with SessionLocal() as db:
            existing = await db.get(MessageModel, msg_id)
            if existing is None:
                merged = dict(image_search_payload or {})
                if payload_patch:
                    merged.update(payload_patch)
                merged = _strip_persistence_bloat(merged)
                db.add(MessageModel(
                    id=msg_id,
                    chat_id=chat_id,
                    role="assistant",
                    content=content or "Image picker",
                    metadata_json={
                        "action": "picker",
                        "image_search_payload": merged,
                    },
                ))
            else:
                meta = dict(existing.metadata_json or {})
                meta["action"] = "picker"
                current_payload = dict(meta.get("image_search_payload") or {})
                if image_search_payload is not None:
                    current_payload = dict(image_search_payload)
                if payload_patch:
                    current_payload.update(payload_patch)
                meta["image_search_payload"] = _strip_persistence_bloat(current_payload)
                existing.metadata_json = meta
                if content and not existing.content:
                    existing.content = content
            await db.commit()
    except Exception as e:
        _logger.warning(f"[picker persist] upsert failed for {msg_id}: {e}")


def _build_edit_prompt_with_context(request_id: str, edit_prompt: str) -> str:
    """Prepend recent picker history to an edit prompt so Gemini has
    the full session context for compound edits.

    The image itself carries most of the visual state, so we only need
    a brief textual hint about prior intent — enough to disambiguate
    pronouns ("make them gold" → "them" refers to stars from the
    original generate prompt). Keep it to the last 5 turns and truncate
    each prompt at 120 chars to keep the preamble small.

    Returns the original prompt unchanged when there's no prior history
    (i.e., this is the first edit on a session)."""
    session = _PICKER_SESSIONS.get(request_id) or []
    if not session:
        return edit_prompt
    recent = session[-5:]
    lines = []
    for entry in recent:
        action = entry.get("action", "?")
        prompt = (entry.get("prompt") or "")[:120]
        if not prompt:
            continue
        lines.append(f"- {action}: {prompt}")
    if not lines:
        return edit_prompt
    return (
        "Context — earlier prompts applied to this image:\n"
        + "\n".join(lines)
        + f"\n\nNow apply this change: {edit_prompt}"
    )


@router.get("/picker/candidate/{request_id}")
async def get_picker_candidate(
    request_id: str,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    """Recover a picker candidate when the WebSocket dropped before the
    response landed. Returns {"status": "pending"} if we have nothing
    cached (generation still running OR never reached us OR TTL expired).
    Frontend polls this after submitting a generate/edit when the WS
    delivery times out.

    Auth-gated (same bearer token as the rest of the chat stream).
    request_id is a uuid4 generated client-side so even with auth, the
    cache key isn't enumerable.
    """
    cached = _get_picker_result(request_id)
    if cached is None:
        return {"status": "pending"}
    return {"status": "found", "payload": cached}


# Maximum string length we'll print into a log. Anything longer (data: URLs,
# raw base64 payloads echoed back by RunPod) gets collapsed to a marker so
# the worker output doesn't dump multi-MB blobs into the log file.
_MAX_LOGGED_STRING = 200


def _redact_for_log(value: Any) -> Any:
    """Recursively shorten data: URLs and oversized strings inside a payload
    before it goes to the logger. Used for RunPod status payloads, which
    echo the input image_url verbatim — for the nano-banana custom flow
    that's a multi-MB base64 string."""
    if isinstance(value, str):
        if value.startswith("data:"):
            head, _, _ = value.partition(",")
            return f"<{head} elided, {len(value)} chars>"
        if len(value) > _MAX_LOGGED_STRING:
            return f"{value[:_MAX_LOGGED_STRING]}…<+{len(value) - _MAX_LOGGED_STRING} chars>"
        return value
    if isinstance(value, dict):
        return {k: _redact_for_log(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_redact_for_log(v) for v in value)
    return value


def _normalize_action(action: str) -> str:
    if action == "process_scad":
        return "generate_scad"
    return action


def _normalize_history(history: list[dict] | None) -> list[dict] | None:
    if history is None:
        return None
    normalized: list[dict] = []
    for item in history:
        role = item.get("role")
        content = item.get("content")
        if role is None or content is None:
            continue
        normalized.append({"role": role, "content": content})
    return normalized


async def _load_chat_history(db: AsyncSession, chat_id: str) -> list[dict]:
    result = await db.execute(
        select(MessageModel.role, MessageModel.content)
        .where(MessageModel.chat_id == chat_id)
        .order_by(MessageModel.created_at.asc())
    )
    rows = result.all()
    history: list[dict] = []
    for role, content in rows:
        if role is None or content is None:
            continue
        history.append({"role": role, "content": content})
    return history


def _serialize_asset(asset: AssetModel) -> dict[str, Any]:
    return AssetRead.model_validate(asset).model_dump(mode="json")


async def _load_serialized_messages(
    db: AsyncSession, chat_id: str, limit: int = 100
) -> list[dict[str, Any]]:
    result = await db.execute(
        select(MessageModel)
        .where(MessageModel.chat_id == chat_id)
        .order_by(MessageModel.created_at.desc())
        .limit(limit)
    )
    messages = list(reversed(result.scalars().all()))
    return [_serialize_message(message) for message in messages]


def _extract_runpod_asset_data(output: Any) -> dict[str, Any] | None:
    import logging

    logger = logging.getLogger(__name__)

    logger.info(f"_extract_runpod_asset_data called with output type: {type(output)}")
    logger.info(f"Output content: {output}")

    if not isinstance(output, dict):
        logger.warning(f"Output is not a dict, it's {type(output)}")
        return None

    if isinstance(output.get("data"), dict):
        logger.info("Found data at output['data']")
        return output["data"]

    inner = output.get("output")
    if isinstance(inner, dict) and isinstance(inner.get("data"), dict):
        logger.info("Found data at output['output']['data']")
        return inner["data"]

    logger.warning(
        f"Could not extract asset data. Output keys: {output.keys() if isinstance(output, dict) else 'N/A'}"
    )
    return None

async def _download_and_extract_scad(download_url: str) -> str | None:
    """Downloads the zip from B2 and extracts the first .py file content."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(download_url) as response:
                if response.status != 200:
                    return None
                
                content = await response.read()
                
                # Use io.BytesIO to treat the raw bytes as a file for zipfile
                with zipfile.ZipFile(io.BytesIO(content)) as z:
                    scad_files = [f for f in z.namelist() if f.endswith('.py')]
                    if not scad_files:
                        return None
                    
                    # Read and decode the first SCAD file found
                    with z.open(scad_files[0]) as f:
                        return f.read().decode('utf-8')
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to extract SCAD: {e}")
        return None

async def _download_and_extract_scad(download_url: str) -> str | None:
    """Downloads the zip and extracts the content of assembly.py."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(download_url) as response:
                if response.status != 200:
                    return None
                
                content = await response.read()
                
                with zipfile.ZipFile(io.BytesIO(content)) as z:
                    # Look specifically for assembly.py
                    target_file = "assembly.py"
                    if target_file not in z.namelist():
                        # Fallback: check if it's nested (e.g., 'folder/assembly.py')
                        matches = [f for f in z.namelist() if f.endswith('assembly.py')]
                        if not matches:
                            return None
                        target_file = matches[0]
                    
                    with z.open(target_file) as f:
                        return f.read().decode('utf-8')
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to extract assembly.py: {e}")
        return None
    
async def _persist_generate_scad_asset(
    *,
    db: AsyncSession,
    chat_id: str,
    output: Any,
    runpod_id: str | None,
    requirements_json: dict | None,
    job_id: str | None,
    asset_type: str | None,
    storage_provider: str | None,
    status_payload: dict | None = None,
) -> dict[str, Any] | None:
    import logging

    logger = logging.getLogger(__name__)

    logger.info(
        f"_persist_generate_scad_asset called for chat_id={chat_id}, runpod_id={runpod_id}, job_id={job_id}"
    )

    data = _extract_runpod_asset_data(output)
    if not data:
        logger.error("Failed to extract asset data from RunPod output")
        return None

    logger.info(f"Extracted data: {data}")

    download_url = data.get("download_url")
    if not download_url:
        logger.error(f"No download_url in extracted data. Data keys: {data.keys()}")
        return None

    logger.info(f"Found download_url: {download_url}")

    chat = await db.get(ChatModel, chat_id)
    if not chat:
        logger.error(f"Chat not found: {chat_id}")
        return None
    
    # Any 'await db.commit()' below will expire the 'chat' instance, 
    # making chat.session_id inaccessible without a re-fetch.
    current_session_id = chat.session_id

    resolved_job = None
    if job_id:
        resolved_job = await db.get(JobModel, job_id)
        if not resolved_job:
            logger.error(f"job_id {job_id} not found in database")
            raise ValueError("job_id not found for generate_scad asset persistence")

    if not resolved_job:
        logger.info("Creating new job for asset")
        prompt = None
        if isinstance(requirements_json, dict):
            prompt = requirements_json.get("primary_function") or requirements_json.get(
                "description_natural_language"
            )
        finished_at = datetime.now(timezone.utc)
        sp = status_payload or {}
        exec_ms = sp.get("executionTime") or sp.get("execution_time") or 0
        delay_ms = sp.get("delayTime") or sp.get("delay_time") or 0
        total_ms = int(exec_ms) + int(delay_ms)
        started_at = (finished_at - timedelta(milliseconds=total_ms)) if total_ms > 0 else finished_at

        resolved_job = JobModel(
            session_id=current_session_id,
            status="succeeded",
            prompt=prompt,
            spec_json=requirements_json,
            output_format=asset_type or "scad_zip",
            started_at=started_at,
            finished_at=finished_at,
        )
        db.add(resolved_job)
        await db.commit()
        await db.refresh(resolved_job)
        logger.info(f"Created job with id: {resolved_job.id}")

    logger.info(f"Creating asset for job_id: {resolved_job.id}")
    scad_file = await _download_and_extract_scad(download_url)
    asset = AssetModel(
        job_id=resolved_job.id,
        asset_type=asset_type or "scad_zip",
        uri=download_url,
        storage_provider=storage_provider or "b2",
        metadata_json={
            "runpod_id": runpod_id,
            "file_id": data.get("file_id"),
            "scadCode": scad_file,
            "filename": data.get("filename"),
            "download_url": download_url,
        },
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    logger.info(f"Created asset with id: {asset.id}")

    # Use the local current_session_id variable to avoid lazy-loading on the expired 'chat' object
    uploaded_by = await db.scalar(
        select(SessionModel.user_id).where(SessionModel.id == current_session_id)
    )
    
    meta = AssetMetaModel(
        asset_id=asset.id,
        part_name=data.get("filename"),
        uploaded_by=uploaded_by,
    )
    db.add(meta)
    await db.commit()
    
    await db.refresh(asset)
    logger.info(f"Created asset_meta for asset_id: {asset.id}")

    return _serialize_asset(asset)

async def _persist_image_to_3d_asset(
    *,
    db: AsyncSession,
    chat_id: str,
    output: Any,
    runpod_id: str | None,
    status_payload: dict | None = None,
) -> dict[str, Any] | None:
    """Persist a generated 3D model (GLB/STL) from the image-to-3D RunPod worker."""
    import logging

    logger = logging.getLogger(__name__)

    if not isinstance(output, dict):
        logger.error(f"Image-to-3D output is not a dict: {type(output)}")
        return None

    # Handle nested output formats
    data = output
    if "output" in output and isinstance(output["output"], dict):
        data = output["output"]

    model_url = data.get("model_url") or data.get("download_url")
    if not model_url:
        logger.error(f"No model_url in image-to-3D output. Keys: {data.keys()}")
        return None

    chat = await db.get(ChatModel, chat_id)
    if not chat:
        return None
    current_session_id = chat.session_id

    # Compute real timestamps from RunPod timing data if available
    finished_at = datetime.now(timezone.utc)
    sp = status_payload or {}
    exec_ms = sp.get("executionTime") or sp.get("execution_time") or 0
    delay_ms = sp.get("delayTime") or sp.get("delay_time") or 0
    total_ms = int(exec_ms) + int(delay_ms)
    if total_ms > 0:
        started_at = finished_at - timedelta(milliseconds=total_ms)
    else:
        started_at = finished_at

    job = JobModel(
        session_id=current_session_id,
        status="succeeded",
        prompt="Image-to-3D generation",
        output_format="glb",
        started_at=started_at,
        finished_at=finished_at,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # When the worker ran Hunyuan3D-Paint (with_texture=true) the
    # response also carries `textured_url` — a UV-mapped GLB sibling
    # of the plain mesh. We persist it alongside `model_url` so the
    # frontend can offer both: the plain mesh for fast preview /
    # geometry edits, and the textured one for final render.
    textured_url = data.get("textured_url")

    # Create parent asset
    asset = AssetModel(
        job_id=job.id,
        asset_type="image_to_3d_glb",
        uri=model_url,
        storage_provider="runpod",
        metadata_json={
            "runpod_id": runpod_id,
            "model_url": model_url,
            "textured_url": textured_url,
        },
    )
    db.add(asset)

    # Sibling asset row for the textured variant (separate row so it
    # has its own lifecycle / can be queried independently).
    if textured_url:
        textured_asset = AssetModel(
            job_id=job.id,
            asset_type="image_to_3d_textured_glb",
            uri=textured_url,
            storage_provider="runpod",
            metadata_json={
                "runpod_id": runpod_id,
                "parent_model_url": model_url,
            },
        )
        db.add(textured_asset)

    # Create child assets for parts if available
    parts = data.get("parts", [])
    persisted_parts = []
    for part in parts:
        part_name = part.get("name", "part")
        part_url = part.get("mesh_url") or part.get("url")
        if part_url:
            part_asset = AssetModel(
                job_id=job.id,
                asset_type="image_to_3d_part",
                uri=part_url,
                storage_provider="runpod",
                metadata_json={
                    "part_name": part_name,
                    "primitive": part.get("primitive", "part"),
                    "parent_runpod_id": runpod_id,
                    "is_watertight": part.get("is_watertight", False),
                },
            )
            db.add(part_asset)
            persisted_parts.append({
                "name": part_name,
                "mesh_url": part_url,
                "primitive": part.get("primitive", "part"),
                "face_count": part.get("face_count"),
                "is_watertight": part.get("is_watertight", False),
            })

    await db.commit()
    await db.refresh(asset)

    uploaded_by = await db.scalar(
        select(SessionModel.user_id).where(SessionModel.id == current_session_id)
    )
    meta = AssetMetaModel(
        asset_id=asset.id,
        part_name="image_to_3d_model",
        uploaded_by=uploaded_by,
    )
    db.add(meta)
    await db.commit()
    await db.refresh(asset)

    # Return parent asset + parts so they're included in the assistant message metadata
    result = _serialize_asset(asset)
    if persisted_parts:
        result["parts"] = persisted_parts
    if textured_url:
        result["textured_url"] = textured_url
    return result


import re as _re

_STRIP_PREFIXES = _re.compile(
    r"^(generate\s+(a\s+)?(3d\s+model|an?\s+image|mesh|shape)\s*(of|for|:)?\s*"
    r"|create\s+(a\s+)?(3d\s+model|an?\s+image|mesh|shape)\s*(of|for|:)?\s*"
    r"|make\s+(a\s+)?(3d\s+model|an?\s+image|mesh|shape)\s*(of|for|:)?\s*"
    r"|build\s+(a\s+)?(3d\s+model|mesh|shape)\s*(of|for|:)?\s*)",
    _re.IGNORECASE,
)


def _clean_search_query(raw: str) -> str:
    """Strip action prefixes to get the core subject for image search."""
    cleaned = _STRIP_PREFIXES.sub("", raw).strip()
    return cleaned or raw.strip()


def _search_images_sync(query: str) -> list[dict]:
    """Search for images via Brave Search (server-rendered, no API key, no rate limits)."""
    import logging
    import re
    import httpx as _httpx

    logger = logging.getLogger(__name__)

    url = f"https://search.brave.com/images?q={query}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
    }

    try:
        resp = _httpx.get(url, headers=headers, timeout=10.0, follow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Brave image search request failed: {e}")
        return []

    # Brave renders image URLs server-side in the HTML
    img_urls = re.findall(
        r'https?://[^"\s<>]+\.(?:jpg|jpeg|png|webp)',
        resp.text,
    )

    # Skip stock photo sites (they block direct downloads) and brave assets
    skip = (
        "brave.com", "imgs.search.brave", "favicon",
        "istockphoto.com", "shutterstock.com", "gettyimages.com",
        "adobestock.com", "123rf.com", "depositphotos.com",
        "alamy.com", "dreamstime.com", "stock.adobe.com",
    )
    seen: set[str] = set()
    results = []
    for img_url in img_urls:
        if any(s in img_url for s in skip):
            continue
        if img_url in seen:
            continue
        seen.add(img_url)
        results.append({"image": img_url, "title": query})
        if len(results) >= 10:
            break

    if results:
        logger.info(f"Brave Search returned {len(results)} image results for '{query}'")
    else:
        logger.warning(f"Brave Search found no images for '{query}'")

    return results


async def _extract_search_keywords(user_message: str) -> str:
    """Use a fast LLM (no thinking) to convert user message into optimal image search keywords."""
    import logging

    logger = logging.getLogger(__name__)

    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        from app.services.openscad_agent.llm_provider import get_llm

        llm = get_llm(provider="groq", model="llama-3.3-70b-versatile", thinking=False, temperature_override=0.0, max_tokens_override=50)
        result = await llm.ainvoke([
            SystemMessage(content=(
                "Extract the subject from the user's request and build an image search query "
                "that finds a SINGLE isolated object photo (one item, plain background, no hands/people/extras).\n"
                "Always append qualifiers like 'product photo white background' or '3D render isolated'.\n"
                "Reply with ONLY the search query. 5-7 words max.\n"
                "Examples:\n"
                "'make a sports car' → 'sports car product photo white background'\n"
                "'nepali temple pashupatinath' → 'pashupatinath temple 3D render isolated'\n"
                "'gold ring with emerald' → 'emerald gold ring product photo white background'\n"
                "'dragon' → 'dragon figurine isolated white background'"
            )),
            HumanMessage(content=user_message),
        ])
        keywords = result.content.strip().strip('"').strip("'")
        if keywords:
            logger.info(f"LLM search keywords: '{user_message}' → '{keywords}'")
            return keywords
    except Exception as e:
        logger.warning(f"LLM keyword extraction failed: {e}")

    # Fallback to regex cleanup
    return _clean_search_query(user_message)


async def _fetch_image_candidates(query: str) -> tuple[list[str], str]:
    """Extract keywords via LLM, search Brave for images, return (list of URLs, search_query)."""
    import logging

    logger = logging.getLogger(__name__)

    # Step 1: LLM extracts optimal search keywords
    search_query = await _extract_search_keywords(query)
    logger.info(f"Search query: '{query}' → '{search_query}'")

    # Step 2: Search Brave for images
    try:
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(None, _search_images_sync, search_query)
    except Exception as e:
        logger.warning(f"Image search failed for '{search_query}': {e}")
        return [], search_query

    if not results:
        logger.warning(f"No image results for '{search_query}'")
        return [], search_query

    logger.info(f"Found {len(results)} image results for '{search_query}'")

    # Return raw URLs without downloading
    urls = [r.get("image") or r.get("thumbnail") for r in results if r.get("image") or r.get("thumbnail")]
    return urls, search_query


async def _download_image_as_base64(url: str) -> str | None:
    """Download a single image URL and return as base64 data URL."""
    import logging
    import base64

    logger = logging.getLogger(__name__)

    if not url:
        return None

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
        "Referer": "https://www.google.com/",
    }

    # Map file extensions to MIME types for when content-type is generic
    _EXT_TO_MIME = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
        ".gif": "image/gif", ".bmp": "image/bmp",
        ".svg": "image/svg+xml",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as http:
            resp = await http.get(url, headers=headers)
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "")
            media_type = ct.split(";")[0].strip()

            # Many CDNs return binary/octet-stream — infer type from URL extension
            if "image" not in media_type:
                import re
                ext_match = re.search(r'\.(jpe?g|png|webp|gif|bmp|svg)', url.lower())
                if ext_match:
                    ext = "." + ext_match.group(1)
                    if ext == ".jpeg":
                        ext = ".jpg"
                    inferred = _EXT_TO_MIME.get(ext)
                    if inferred:
                        logger.info(f"Inferred media type {inferred} from URL extension for {url} (server sent {media_type})")
                        media_type = inferred
                    else:
                        logger.warning(f"Invalid content-type for {url}: {ct} (status={resp.status_code})")
                        return None
                else:
                    logger.warning(f"Invalid content-type for {url}: {ct} (status={resp.status_code})")
                    return None

            if len(resp.content) > 5 * 1024 * 1024:
                logger.warning(f"Image too large: {url} ({len(resp.content)} bytes)")
                return None
            if len(resp.content) < 100:
                logger.warning(f"Image too small (likely placeholder): {url} ({len(resp.content)} bytes)")
                return None
            b64 = base64.b64encode(resp.content).decode("utf-8")
            logger.info(f"Downloaded image {url} ({len(resp.content)} bytes, {media_type})")
            return f"data:{media_type};base64,{b64}"
    except Exception as e:
        logger.warning(f"Failed to download {url}: {e}")
        return None


async def _resolve_search_image(query: str) -> str | None:
    """Use LLM to extract keywords, search Brave for images, download first as base64 (legacy path)."""
    import logging

    logger = logging.getLogger(__name__)

    candidates, _ = await _fetch_image_candidates(query)
    if not candidates:
        return None

    # Download first usable image
    for url in candidates:
        b64_url = await _download_image_as_base64(url)
        if b64_url:
            return b64_url

    logger.warning(f"All image downloads failed")
    return None


async def _handle_image_to_3d_request(
    *,
    chat_id: str,
    payload: ImageTo3DRequest,
    db: AsyncSession,
) -> ImageTo3DResponse:
    """Submit an image-to-3D job to RunPod and start polling.

    If no image_url is provided, this function should NOT be called directly.
    Use the HTTP endpoint or WS task which handle image search + selection first.
    """
    import logging

    logger = logging.getLogger(__name__)

    resolved_image_url = payload.image_url

    if not resolved_image_url:
        raise HTTPException(
            status_code=422,
            detail="image_url is required. Use the image search flow to select an image first.",
        )

    # Save user message
    user_message = MessageModel(
        chat_id=chat_id,
        role="user",
        content=f"Generate 3D model: {payload.prompt or 'from image'}",
        metadata_json={
            "action": "image_to_3d",
            "has_image": bool(resolved_image_url),
            "output_format": payload.output_format,
        },
    )
    db.add(user_message)
    await db.commit()
    await db.refresh(user_message)

    try:
        # Modal or RunPod, controlled by IMAGE_TO_3D_BACKEND env var.
        client = _get_image_to_3d_client()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Download image on backend side to avoid 403s from sites that block
    # non-browser requests (e.g. Reddit, Pinterest) AND auth-required
    # 400s from our own private R2 bucket (the picker may forward an R2
    # cache URL when the rehydrated candidate has no inline runpod_url).
    # We use fetch_object_bytes (storage_proxy) for URLs that point at
    # our R2/B2 buckets so we go through the authenticated boto client;
    # otherwise fall back to bare httpx for external public images.
    if resolved_image_url and not resolved_image_url.startswith("data:"):
        import base64 as _b64
        is_our_storage = (
            ".r2.cloudflarestorage.com/" in resolved_image_url
            or ".backblazeb2.com/" in resolved_image_url
        )
        downloaded = False
        if is_our_storage:
            try:
                from app.api.routes.storage_proxy import fetch_object_bytes
                img_bytes, ct = await fetch_object_bytes(resolved_image_url)
                resolved_image_url = (
                    f"data:{ct or 'image/png'};base64,"
                    f"{_b64.b64encode(img_bytes).decode()}"
                )
                logger.info(
                    f"Downloaded image via storage_proxy "
                    f"({len(img_bytes)} bytes), sending as base64"
                )
                downloaded = True
            except Exception as dl_err:
                logger.warning(
                    f"storage_proxy fetch failed ({dl_err}); "
                    f"falling through to bare httpx"
                )
        if not downloaded:
            try:
                import httpx as _httpx
                async with _httpx.AsyncClient(
                    follow_redirects=True, timeout=30.0
                ) as _http:
                    img_resp = await _http.get(
                        resolved_image_url,
                        headers={"User-Agent": "Mozilla/5.0"},
                    )
                    img_resp.raise_for_status()
                    ct = (
                        img_resp.headers.get("content-type", "image/png")
                        .split(";")[0]
                    )
                    b64 = _b64.b64encode(img_resp.content).decode()
                    resolved_image_url = f"data:{ct};base64,{b64}"
                    logger.info(
                        f"Downloaded image via httpx "
                        f"({len(img_resp.content)} bytes), sending as base64"
                    )
            except Exception as dl_err:
                logger.warning(
                    f"Image download failed ({dl_err}), sending URL "
                    f"directly to RunPod"
                )

    try:
        runpod_response = await client.start_raw_job({
            "action": "image_to_3d",
            "image_url": resolved_image_url,
            "prompt": payload.prompt,
            "output_format": payload.output_format,
            "skip_segmentation": payload.skip_segmentation,
            "with_texture": payload.with_texture,
        })
        logger.info(f"Image-to-3D RunPod job submitted: {runpod_response.get('id')}")
    except Exception as exc:
        logger.exception(f"Image-to-3D RunPod request failed: {exc}")
        raise HTTPException(
            status_code=502, detail=f"Image-to-3D request failed: {exc}"
        ) from exc

    runpod_id = runpod_response.get("id")
    if not runpod_id:
        raise HTTPException(status_code=502, detail="RunPod did not return a job id")

    asyncio.create_task(
        _runpod_poll_and_emit(
            chat_id=chat_id,
            runpod_id=runpod_id,
            action="image_to_3d",
            status_timeout_seconds=settings.image_to_3d_timeout_seconds,
        )
    )

    return ImageTo3DResponse(
        status="queued",
        runpod_id=runpod_id,
        message_id=str(user_message.id),
    )


async def _handle_image_to_3d_ws_task(
    websocket: WebSocket,
    chat_id: str,
    payload: dict,
) -> None:
    """Async task wrapper for image-to-3D WS handler.

    If image_url is provided, goes straight to generation.
    If only prompt is provided, sends image options for user selection (non-blocking).
    """
    import logging
    import uuid as _uuid

    logger = logging.getLogger(__name__)

    try:
        i3d_payload = ImageTo3DRequest.model_validate(payload.get("payload", {}))

        # If no image provided, search and send options — don't block
        if not i3d_payload.image_url and i3d_payload.prompt:
            logger.info(f"No image provided via WS, searching for: {i3d_payload.prompt}")

            candidates, search_query = await _fetch_image_candidates(i3d_payload.prompt)
            if not candidates:
                await _safe_send_json(websocket, {
                    "type": "image_to_3d.error",
                    "chat_id": chat_id,
                    "message": f"Could not find reference images for '{i3d_payload.prompt}'. Try uploading an image instead.",
                })
                return

            request_id = str(_uuid.uuid4())

            # Persist the picker as a real chat message so reloads
            # restore it (with whatever candidate the user produces
            # later, once candidate_ready also upserts).
            initial_payload = {
                "image_urls": candidates,
                "search_query": search_query,
                "request_id": request_id,
                "prompt": i3d_payload.prompt or "",
            }
            await _upsert_picker_message(
                chat_id=chat_id,
                request_id=request_id,
                image_search_payload=initial_payload,
                content=(
                    f"Found {len(candidates)} reference images for "
                    f"\"{search_query or i3d_payload.prompt or ''}\""
                ),
            )

            # Send image options to frontend — user picks via image_to_3d.image_selected
            await _safe_send_json(websocket, {
                "type": "image_to_3d.image_options",
                "chat_id": chat_id,
                "search_query": search_query,
                "image_urls": candidates,
                "request_id": request_id,
                "prompt": i3d_payload.prompt,
                "output_format": i3d_payload.output_format,
            })
            return  # Non-blocking — generation starts when user selects an image

        # Image provided — go straight to generation
        async with SessionLocal() as db:
            response = await _handle_image_to_3d_request(
                chat_id=chat_id,
                payload=i3d_payload,
                db=db,
            )
        await _safe_send_json(websocket, {
            "type": "image_to_3d.queued",
            "chat_id": chat_id,
            "runpod_id": response.runpod_id,
            "message_id": response.message_id,
            "status": response.status,
        })
    except Exception as e:
        logger.error(f"Image-to-3D WS error: {e}", exc_info=True)
        await _safe_send_json(websocket, {
            "type": "image_to_3d.error",
            "chat_id": chat_id,
            "message": str(e),
        })


async def _handle_mesh_modification_ws(
    websocket: WebSocket,
    chat_id: str,
    payload: dict,
) -> None:
    """Submit a mesh boolean-modification job to RunPod and start polling."""
    import logging
    logger = logging.getLogger(__name__)

    try:
        inner = payload.get("payload", payload)
        mesh_url = inner.get("mesh_url", "")
        modification = inner.get("modification", "")
        output_filename = inner.get("output_filename")

        if not mesh_url or not modification:
            await _safe_send_json(websocket, {
                "type": "mesh_modification.error",
                "chat_id": chat_id,
                "message": "mesh_url and modification are required",
            })
            return

        client = get_image_to_3d_client()
        job_payload: dict = {
            "action": "modify_mesh",
            "mesh_url": mesh_url,
            "modification": modification,
        }
        if output_filename:
            job_payload["output_filename"] = output_filename

        runpod_response = await client.start_raw_job(job_payload)
        runpod_id = runpod_response.get("id")
        if not runpod_id:
            await _safe_send_json(websocket, {
                "type": "mesh_modification.error",
                "chat_id": chat_id,
                "message": "RunPod did not return a job id",
            })
            return

        await _safe_send_json(websocket, {
            "type": "mesh_modification.queued",
            "chat_id": chat_id,
            "runpod_id": runpod_id,
        })
        asyncio.create_task(
            _runpod_poll_and_emit(
                chat_id=chat_id,
                runpod_id=runpod_id,
                action="modify_mesh",
                status_timeout_seconds=settings.image_to_3d_timeout_seconds,
                runpod_client=client,
            )
        )
    except Exception as e:
        logger.error(f"Mesh modification WS error: {e}", exc_info=True)
        await _safe_send_json(websocket, {
            "type": "mesh_modification.error",
            "chat_id": chat_id,
            "message": str(e),
        })


async def _handle_inpaint_ws(
    websocket: WebSocket,
    chat_id: str,
    payload: dict,
) -> None:
    """Submit a Flux inpainting job to RunPod and start polling."""
    import logging
    logger = logging.getLogger(__name__)

    try:
        inner = payload.get("payload", payload)
        image_data = inner.get("image_data") or inner.get("image_url")
        prompt = inner.get("prompt", "")

        if not image_data or not prompt:
            await _safe_send_json(websocket, {
                "type": "inpaint.error",
                "chat_id": chat_id,
                "message": "image_data and prompt are required",
            })
            return

        client = get_image_to_3d_client()
        job_payload = {
            "action": "inpaint",
            "image_data": image_data,
            "prompt": prompt,
        }
        for key in ("mask_data", "strength", "steps"):
            if inner.get(key) is not None:
                job_payload[key] = inner[key]

        runpod_response = await client.start_raw_job(job_payload)
        runpod_id = runpod_response.get("id")
        if not runpod_id:
            await _safe_send_json(websocket, {
                "type": "inpaint.error",
                "chat_id": chat_id,
                "message": "RunPod did not return a job id",
            })
            return

        await _safe_send_json(websocket, {
            "type": "inpaint.queued",
            "chat_id": chat_id,
            "runpod_id": runpod_id,
        })
        asyncio.create_task(
            _runpod_poll_and_emit(
                chat_id=chat_id,
                runpod_id=runpod_id,
                action="inpaint",
                status_timeout_seconds=settings.image_to_3d_timeout_seconds,
                runpod_client=client,
            )
        )
    except Exception as e:
        logger.error(f"Inpaint WS error: {e}", exc_info=True)
        await _safe_send_json(websocket, {
            "type": "inpaint.error",
            "chat_id": chat_id,
            "message": str(e),
        })


def _gen_candidate_via_gemini(
    contents,
    purpose: str,
) -> tuple[str | None, str | None, str]:
    """Run Gemini, upload R2, return (display_url, runpod_url, error).

    `display_url` is what the picker should render (R2 if available, else
    data URL). `runpod_url` is always a data URL — the worker decodes it
    inline so we never have to expose private R2 to RunPod. `error` is a
    user-friendly string when generation failed, otherwise empty.

    Checks the prompt-keyed R2 cache before calling Gemini. Identical
    prompts (and for edits, identical source image + edit prompt) skip
    the API call and serve the cached bytes directly. See
    `image_gen._cache_key` / `_try_cached_image` for the cache scheme.
    """
    import io as _io
    import logging
    logger = logging.getLogger(__name__)

    from app.services.openscad_agent.tools.image_gen import (
        _bytes_to_data_url,
        _cache_key,
        _client,
        _extract_image_bytes,
        _generate_with_fallback,
        _refusal_summary,
        _store_cached_image,
        _try_cached_image,
        _upload_to_r2,
    )

    # Derive cache key from contents — same hashing rules as the agent
    # tool paths. `contents` is either a plain prompt string (generate)
    # or [prompt, PIL.Image] (edit).
    cache_prompt: str | None = None
    cache_ref_bytes: bytes | None = None
    try:
        if isinstance(contents, str):
            cache_prompt = contents
        elif isinstance(contents, (list, tuple)) and contents:
            for item in contents:
                if isinstance(item, str) and cache_prompt is None:
                    cache_prompt = item
                elif hasattr(item, "save") and cache_ref_bytes is None:
                    buf = _io.BytesIO()
                    item.save(buf, format="PNG")
                    cache_ref_bytes = buf.getvalue()
    except Exception as e:
        logger.warning(f"[i2i {purpose}] cache-key build skipped: {e}")
        cache_prompt = None

    cache_key = _cache_key(cache_prompt, cache_ref_bytes) if cache_prompt else None
    if cache_key:
        cached = _try_cached_image(cache_key)
        if cached is not None:
            image_bytes, media_type, cached_url = cached
            data_url = _bytes_to_data_url(image_bytes, media_type)
            logger.info(
                f"[i2i {purpose}] cache HIT — skipping Gemini "
                f"({len(image_bytes)} bytes)"
            )
            return cached_url, data_url, ""

    try:
        client = _client()
        response, _model_used = _generate_with_fallback(client, contents)
    except Exception as e:
        logger.error(f"[i2i {purpose}] Gemini call failed: {e}")
        return None, None, f"Image generation failed: {e}"

    extracted = _extract_image_bytes(response)
    if not extracted:
        # `_refusal_summary` now returns a self-contained user-facing sentence
        # (e.g. "Gemini blocked this prompt as prohibited content..."); pass it
        # through verbatim so the picker shows the actual reason.
        reason = _refusal_summary(response)
        logger.warning(f"[i2i {purpose}] No image in Gemini response: {reason}")
        return None, None, reason

    image_bytes, media_type = extracted
    # Store at the deterministic cache key when we have one; otherwise
    # fall back to the uuid-keyed upload. Either way the URL becomes
    # display_url. We don't double-upload — cache key serves both
    # cache and public-URL roles when present.
    public_url: str | None = None
    if cache_key:
        public_url = _store_cached_image(cache_key, image_bytes, media_type)
    if public_url is None:
        public_url = _upload_to_r2(image_bytes, media_type)
    data_url = _bytes_to_data_url(image_bytes, media_type)
    display_url = public_url or data_url
    logger.info(
        f"[i2i {purpose}] candidate ready ({len(image_bytes)} bytes, "
        f"chat={'r2' if public_url else 'data-url'}, "
        f"cached={'yes' if cache_key and public_url else 'no'})"
    )
    return display_url, data_url, ""


async def _handle_generate_custom_image_ws(
    websocket: WebSocket,
    chat_id: str,
    custom_prompt: str,
    request_id: str,
) -> None:
    """Picker action: user wants a custom reference image. Generate via
    Gemini, surface the result back to the picker as a candidate. Does NOT
    trigger RunPod — the user must click "Use for 3D" to commit."""
    import logging
    logger = logging.getLogger(__name__)

    if not custom_prompt or not custom_prompt.strip():
        err_event = {
            "type": "image_to_3d.candidate_error",
            "chat_id": chat_id,
            "request_id": request_id,
            "message": "Description required to generate a custom image.",
        }
        _cache_picker_result(request_id, err_event)
        await _safe_send_json(websocket, err_event)
        return

    # Cache fast-path: if we've generated this exact prompt before, the
    # R2 lookup (~50-200 ms via asyncio.to_thread) returns the cached
    # image and we skip both the `candidate_pending` event and the
    # Gemini call entirely. User sees the image arrive in one shot
    # instead of going through Generating… → REVIEW.
    from app.services.openscad_agent.tools.image_gen import _quick_cache_lookup
    cached_pre = await asyncio.to_thread(_quick_cache_lookup, custom_prompt, None)
    if cached_pre is not None:
        cached_url, cached_data_url = cached_pre
        _append_picker_history(request_id, "generate", custom_prompt)
        await _persist_picker_history(chat_id, request_id)
        ready_event = {
            "type": "image_to_3d.candidate_ready",
            "chat_id": chat_id,
            "request_id": request_id,
            "display_url": cached_url,
            "runpod_url": cached_data_url,
            "source": "ai_generated",
            "prompt": custom_prompt,
        }
        _cache_picker_result(request_id, ready_event)
        await _upsert_picker_message(
            chat_id=chat_id,
            request_id=request_id,
            payload_patch={
                "candidate": {
                    "url": cached_url,
                    "runpod_url": cached_data_url,
                    "source": "ai_generated",
                    "prompt": custom_prompt,
                },
                "candidateError": None,
            },
        )
        await _safe_send_json(websocket, ready_event)
        return

    await _safe_send_json(websocket, {
        "type": "image_to_3d.candidate_pending",
        "chat_id": chat_id,
        "request_id": request_id,
        "action": "generate",
    })

    # `_gen_candidate_via_gemini` calls the sync google-genai SDK which
    # blocks for 3-8 s. Offload to a thread so the event loop stays
    # free — multiple concurrent picker submits now run in parallel
    # via the threadpool instead of serializing on one event loop.
    display_url, runpod_url, err = await asyncio.to_thread(
        _gen_candidate_via_gemini, custom_prompt, "generate"
    )
    if err or not display_url:
        err_event = {
            "type": "image_to_3d.candidate_error",
            "chat_id": chat_id,
            "request_id": request_id,
            "message": err or "Generation failed",
        }
        # Cache BEFORE sending so the REST-recovery path works even when
        # the WS send raises mid-task (server reload, broken pipe).
        _cache_picker_result(request_id, err_event)
        await _upsert_picker_message(
            chat_id=chat_id,
            request_id=request_id,
            payload_patch={"candidateError": err or "Generation failed"},
        )
        await _safe_send_json(websocket, err_event)
        return

    # Anchor the picker session with the original generate prompt — every
    # subsequent edit will reference it via _build_edit_prompt_with_context.
    _append_picker_history(request_id, "generate", custom_prompt)
    await _persist_picker_history(chat_id, request_id)

    ready_event = {
        "type": "image_to_3d.candidate_ready",
        "chat_id": chat_id,
        "request_id": request_id,
        "display_url": display_url,
        "runpod_url": runpod_url,
        "source": "ai_generated",
        "prompt": custom_prompt,
    }
    _cache_picker_result(request_id, ready_event)
    await _upsert_picker_message(
        chat_id=chat_id,
        request_id=request_id,
        payload_patch={
            "candidate": {
                "url": display_url,
                "runpod_url": runpod_url,
                "source": "ai_generated",
                "prompt": custom_prompt,
            },
            "candidateError": None,
        },
    )
    await _safe_send_json(websocket, ready_event)


async def _handle_edit_candidate_ws(
    websocket: WebSocket,
    chat_id: str,
    request_id: str,
    image_url: str,
    edit_prompt: str,
) -> None:
    """Picker action: refine the current candidate with a Gemini edit.
    Returns a new candidate; user can keep iterating before committing."""
    import logging
    logger = logging.getLogger(__name__)

    if not edit_prompt or not edit_prompt.strip():
        err_event = {
            "type": "image_to_3d.candidate_error",
            "chat_id": chat_id,
            "request_id": request_id,
            "message": "Describe the change you want to apply.",
        }
        _cache_picker_result(request_id, err_event)
        await _safe_send_json(websocket, err_event)
        return
    if not image_url:
        err_event = {
            "type": "image_to_3d.candidate_error",
            "chat_id": chat_id,
            "request_id": request_id,
            "message": "No image to edit.",
        }
        _cache_picker_result(request_id, err_event)
        await _safe_send_json(websocket, err_event)
        return

    # Load the current image into a PIL.Image so Gemini can use it as a
    # multimodal reference. Offload the HTTP fetch (could be slow for
    # http URLs) — the load itself is sync and uses httpx internally.
    from app.services.openscad_agent.tools.image_gen import (
        _load_reference_image,
        _quick_cache_lookup,
    )
    try:
        ref_image = await asyncio.to_thread(_load_reference_image, image_url)
    except Exception as e:
        err_event = {
            "type": "image_to_3d.candidate_error",
            "chat_id": chat_id,
            "request_id": request_id,
            "message": f"Could not load source image: {e}",
        }
        _cache_picker_result(request_id, err_event)
        await _safe_send_json(websocket, err_event)
        return

    # Rehydrate the in-memory session log from DB if it's empty —
    # covers backend restarts, TTL eviction, and the very first edit
    # after a browser reload (where the picker payload came from DB but
    # _PICKER_SESSIONS started cold). Without this, the edit prompt
    # would be sent to Gemini without any "previously the user asked
    # for X" context, and the model would treat each edit as a fresh
    # generation, losing the iterative intent.
    await _rehydrate_picker_history(request_id)

    # Build the effective prompt (user's prompt + session context) so
    # the cache key matches what we'd actually send to Gemini — without
    # this, the cache key would be the raw user prompt but the cache
    # store key (inside _gen_candidate_via_gemini) uses the effective
    # prompt → cache populated by one call wouldn't hit the next.
    effective_prompt = _build_edit_prompt_with_context(request_id, edit_prompt)
    if effective_prompt != edit_prompt:
        logger.info(
            f"[i2i edit] threaded session context into prompt "
            f"({len(effective_prompt) - len(edit_prompt)} extra chars)"
        )

    # Cache fast-path for edits — same scheme as the generate handler.
    # Hash includes the source image bytes so two edits on the same
    # source with the same prompt collide; different sources don't.
    def _ref_bytes_for_cache():
        try:
            buf = io.BytesIO()
            ref_image.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return None
    ref_bytes = await asyncio.to_thread(_ref_bytes_for_cache)
    cached_pre = await asyncio.to_thread(
        _quick_cache_lookup, effective_prompt, ref_bytes
    )
    if cached_pre is not None:
        cached_url, cached_data_url = cached_pre
        _append_picker_history(request_id, "edit", edit_prompt)
        await _persist_picker_history(chat_id, request_id)
        ready_event = {
            "type": "image_to_3d.candidate_ready",
            "chat_id": chat_id,
            "request_id": request_id,
            "display_url": cached_url,
            "runpod_url": cached_data_url,
            "source": "ai_edited",
            "prompt": edit_prompt,
        }
        _cache_picker_result(request_id, ready_event)
        await _upsert_picker_message(
            chat_id=chat_id,
            request_id=request_id,
            payload_patch={
                "candidate": {
                    "url": cached_url,
                    "runpod_url": cached_data_url,
                    "source": "ai_edited",
                    "prompt": edit_prompt,
                },
                "candidateError": None,
            },
        )
        await _safe_send_json(websocket, ready_event)
        return

    await _safe_send_json(websocket, {
        "type": "image_to_3d.candidate_pending",
        "chat_id": chat_id,
        "request_id": request_id,
        "action": "edit",
    })

    # Offload the sync Gemini call to a thread so the event loop stays
    # free during the 3-8 s API call. See _handle_generate_custom_image_ws
    # for the same reasoning.
    display_url, runpod_url, err = await asyncio.to_thread(
        _gen_candidate_via_gemini, [effective_prompt, ref_image], "edit"
    )
    if err or not display_url:
        err_event = {
            "type": "image_to_3d.candidate_error",
            "chat_id": chat_id,
            "request_id": request_id,
            "message": err or "Edit failed",
        }
        _cache_picker_result(request_id, err_event)
        await _upsert_picker_message(
            chat_id=chat_id,
            request_id=request_id,
            payload_patch={"candidateError": err or "Edit failed"},
        )
        await _safe_send_json(websocket, err_event)
        return

    # Record the raw user prompt (not the augmented one) so subsequent
    # edits see what the user actually asked for, not our internal
    # context preamble.
    _append_picker_history(request_id, "edit", edit_prompt)
    await _persist_picker_history(chat_id, request_id)

    ready_event = {
        "type": "image_to_3d.candidate_ready",
        "chat_id": chat_id,
        "request_id": request_id,
        "display_url": display_url,
        "runpod_url": runpod_url,
        "source": "ai_edited",
        "prompt": edit_prompt,
    }
    _cache_picker_result(request_id, ready_event)
    await _upsert_picker_message(
        chat_id=chat_id,
        request_id=request_id,
        payload_patch={
            "candidate": {
                "url": display_url,
                "runpod_url": runpod_url,
                "source": "ai_edited",
                "prompt": edit_prompt,
            },
            "candidateError": None,
        },
    )
    await _safe_send_json(websocket, ready_event)


async def _handle_image_selected_ws(
    websocket: WebSocket,
    chat_id: str,
    selected_url: str,
    prompt: str,
    output_format: str,
    skip_segmentation: bool = False,
    with_texture: bool = False,
) -> None:
    """Start 3D generation with the user-selected image URL.

    The RunPod worker supports both base64 data URLs and raw HTTPS URLs,
    so we pass the URL directly without downloading first.
    """
    import logging

    logger = logging.getLogger(__name__)

    try:
        logger.info(
            f"Starting 3D generation with selected image: "
            f"{_redact_for_log(selected_url)}"
        )
        async with SessionLocal() as db:
            response = await _handle_image_to_3d_request(
                chat_id=chat_id,
                payload=ImageTo3DRequest(
                    image_url=selected_url,
                    prompt=prompt,
                    output_format=output_format,
                    skip_segmentation=skip_segmentation,
                    with_texture=with_texture,
                ),
                db=db,
            )
        await _safe_send_json(websocket, {
            "type": "image_to_3d.queued",
            "chat_id": chat_id,
            "runpod_id": response.runpod_id,
            "message_id": response.message_id,
            "status": response.status,
        })
    except Exception as e:
        logger.error(f"Image selection handler error: {e}", exc_info=True)
        await _safe_send_json(websocket, {
            "type": "image_to_3d.error",
            "chat_id": chat_id,
            "message": str(e),
        })


async def _handle_runpod_request(
    *,
    chat_id: str,
    payload: ChatRunpodRequest,
    db: AsyncSession,
) -> ChatRunpodResponse:
    requested_action = payload.action
    resolved_action = _normalize_action(requested_action)

    if resolved_action == "generate_scad" and not payload.requirements_json:
        raise HTTPException(
            status_code=422, detail="requirements_json is required for generate_scad"
        )
    if resolved_action == "process_requirements" and not payload.content:
        raise HTTPException(
            status_code=422, detail="content is required for process_requirements"
        )

    metadata_json = payload.metadata_json or {}
    metadata_json["runpod_action"] = resolved_action
    if requested_action != resolved_action:
        metadata_json["runpod_action_requested"] = requested_action
    if payload.requirements_json is not None:
        metadata_json["requirements_json"] = payload.requirements_json
    if payload.history is not None:
        metadata_json["history"] = [item.model_dump() for item in payload.history]

    user_message = None
    if payload.content is not None:
        user_message = MessageModel(
            chat_id=chat_id,
            role="user",
            content=payload.content,
            metadata_json=metadata_json,
        )
        db.add(user_message)
        await db.commit()
        await db.refresh(user_message)

    try:
        client = get_runpod_client()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    import logging

    logger = logging.getLogger(__name__)
    logger.info(
        f"Sending RunPod request: action={resolved_action}, requested_action={requested_action}, chat_id={chat_id}"
    )
    logger.info(f"Requirements JSON present: {payload.requirements_json is not None}")
    if payload.requirements_json:
        logger.info(f"Requirements keys: {list(payload.requirements_json.keys())}")
    logger.info(f"Metadata: {payload.metadata_json}")

    runpod_history = _normalize_history(
        [item.model_dump() for item in payload.history] if payload.history else None
    )
    if runpod_history is None:
        runpod_history = await _load_chat_history(db, chat_id)

    try:
        runpod_response = await client.start_job(
            action=resolved_action,
            prompt=(
                payload.content if resolved_action == "process_requirements" else None
            ),
            requirements_json=(
                payload.requirements_json if resolved_action == "generate_scad" else None
            ),
            history=runpod_history if resolved_action != "health" else None,
            sync=payload.sync,
        )
        logger.info(f"RunPod accepted job: {runpod_response.get('id')}")
    except Exception as exc:
        logger.exception(f"RunPod request failed: {exc}")
        raise HTTPException(
            status_code=502, detail=f"Runpod request failed: {exc}"
        ) from exc

    meta = payload.metadata_json or {}
    job_id_val = meta.get("job_id")
    storage_provider_val = meta.get("storage_provider")
    
    request_context: RequestContext = {
        "job_id": str(job_id_val) if job_id_val is not None else None,
        "storage_provider": str(storage_provider_val) if storage_provider_val is not None else None,
        "status_timeout_seconds": meta.get("status_timeout_seconds"),
    }

    asset_context: AssetContext | None = None
    if resolved_action == "generate_scad":
        asset_type_val = meta.get("asset_type")
        asset_context = {
            "requirements_json": payload.requirements_json,
            "job_id": request_context.get("job_id"),
            "asset_type": str(asset_type_val) if asset_type_val is not None else None,
            "storage_provider": request_context.get("storage_provider"),
        }

    if payload.sync:
        output = runpod_response
        if resolved_action == "generate_scad":
            try:
                asset_output = await _persist_generate_scad_asset(
                    db=db,
                    chat_id=chat_id,
                    output=output,
                    runpod_id=None,
                    requirements_json=(
                        asset_context["requirements_json"] if asset_context else None
                    ),
                    job_id=asset_context["job_id"] if asset_context else None,
                    asset_type=asset_context["asset_type"] if asset_context else None,
                    storage_provider=(
                        asset_context["storage_provider"] if asset_context else None
                    ),
                )
            except Exception as exc:
                asset_output = {
                    "error": f"asset persistence failed: {exc}",
                    "output": output,
                }
            if asset_output is not None:
                output = asset_output
        assistant_content = (
            output
            if isinstance(output, str)
            else json.dumps(output or {}, ensure_ascii=True)
        )
        assistant_message = MessageModel(
            chat_id=chat_id,
            role="assistant",
            content=assistant_content,
            metadata_json={
                "runpod_action": resolved_action,
                "runpod_action_requested": requested_action,
                "output": output,
            },
        )
        db.add(assistant_message)
        await db.commit()
        await db.refresh(assistant_message)
        await chat_socket_manager.send_to_chat(
            chat_id,
            {
                "type": "runpod.completed",
                "chat_id": chat_id,
                "runpod_id": None,
                "message": _serialize_message(assistant_message),
                "output": output,
            },
        )
        message_id = user_message.id if user_message else assistant_message.id
        return ChatRunpodResponse(
            status="completed", runpod_id=None, message_id=message_id
        )

    runpod_id = runpod_response.get("id")
    if not runpod_id:
        raise HTTPException(status_code=502, detail="Runpod did not return a job id")

    asyncio.create_task(
        _runpod_poll_and_emit(
            chat_id=chat_id,
            runpod_id=runpod_id,
            action=resolved_action,
            asset_context=asset_context,
            request_context=request_context,
            status_timeout_seconds=request_context["status_timeout_seconds"],
        )
    )

    message_id = user_message.id if user_message else runpod_id
    return ChatRunpodResponse(
        status="queued", runpod_id=runpod_id, message_id=message_id
    )


async def _runpod_poll_and_emit(
    *,
    chat_id: str,
    runpod_id: str,
    action: str,
    asset_context: AssetContext | None = None,
    request_context: RequestContext | None = None,
    status_timeout_seconds: int | float | str | None = None,
    runpod_client=None,
) -> None:
    try:
        if runpod_client is not None:
            client = runpod_client
        elif action == "image_to_3d":
            # Routes to Modal or RunPod based on IMAGE_TO_3D_BACKEND. The
            # polling loop is shape-compatible across both — ModalClient
            # returns the same status dict that get_status emitted before.
            client = _get_image_to_3d_client()
        else:
            client = get_runpod_client()
    except ValueError as exc:
        await chat_socket_manager.send_to_chat(
            chat_id,
            {
                "type": "runpod.failed",
                "chat_id": chat_id,
                "runpod_id": runpod_id,
                "error": {"detail": str(exc)},
            },
        )
        return

    job_id = None
    if request_context:
        job_id = request_context.get("job_id")
    elif asset_context:
        job_id = asset_context.get("job_id")

    await chat_socket_manager.send_to_chat(
        chat_id,
        {
            "type": "runpod.started",
            "chat_id": chat_id,
            "runpod_id": runpod_id,
            "job_id": job_id,
            "action": action,
        },
    )
    last_status = None
    timeout_seconds = settings.runpod_status_timeout_seconds
    if status_timeout_seconds is not None:
        try:
            parsed_timeout = int(float(status_timeout_seconds))
            if parsed_timeout > 0:
                timeout_seconds = parsed_timeout
        except (TypeError, ValueError):
            pass
    deadline = time.monotonic() + timeout_seconds

    while True:
        if time.monotonic() > deadline:
            await chat_socket_manager.send_to_chat(
                chat_id,
                {
                    "type": "runpod.timeout",
                    "chat_id": chat_id,
                    "runpod_id": runpod_id,
                    "timeout_seconds": timeout_seconds,
                    "last_status": last_status,
                },
            )
            return

        try:
            status_payload = await client.get_status(runpod_id)
        except Exception as exc:
            await chat_socket_manager.send_to_chat(
                chat_id,
                {
                    "type": "runpod.failed",
                    "chat_id": chat_id,
                    "runpod_id": runpod_id,
                    "error": {"detail": str(exc)},
                },
            )
            return

        status_value = status_payload.get("status")

        if status_value and status_value != last_status:
            status_update = {
                "type": "runpod.status",
                "chat_id": chat_id,
                "runpod_id": runpod_id,
                "job_id": job_id,
                "status": status_value,
                "assets_ready": status_value == "COMPLETED",
                "action": action,
            }
            if status_value == "COMPLETED":
                status_update["output"] = status_payload.get("output")

            await chat_socket_manager.send_to_chat(chat_id, status_update)
            last_status = status_value

        if status_value == "COMPLETED":
            import logging

            logger = logging.getLogger(__name__)

            output = status_payload.get("output")
            logger.info(f"RunPod COMPLETED for runpod_id={runpod_id}, action={action}")
            logger.info(f"Status payload: {_redact_for_log(status_payload)}")

            async with SessionLocal() as db:
                if action == "generate_scad":
                    logger.info("Attempting to persist generate_scad asset")
                    try:
                        asset_output = await _persist_generate_scad_asset(
                            db=db,
                            chat_id=chat_id,
                            output=output,
                            runpod_id=runpod_id,
                            requirements_json=(
                                asset_context.get("requirements_json")
                                if asset_context
                                else None
                            ),
                            job_id=(
                                asset_context.get("job_id") if asset_context else None
                            ),
                            asset_type=(
                                asset_context.get("asset_type")
                                if asset_context
                                else None
                            ),
                            storage_provider=(
                                asset_context.get("storage_provider")
                                if asset_context
                                else None
                            ),
                            status_payload=status_payload if isinstance(status_payload, dict) else None,
                        )
                        if asset_output is None:
                            logger.error(
                                "_persist_generate_scad_asset returned None - asset not saved!"
                            )
                        else:
                            logger.info(f"Asset persisted successfully: {asset_output}")
                    except Exception as exc:
                        logger.exception(
                            f"Exception in _persist_generate_scad_asset: {exc}"
                        )
                        asset_output = {
                            "error": f"asset persistence failed: {exc}",
                            "output": output,
                        }
                    if asset_output is not None:
                        output = asset_output
                elif action == "image_to_3d":
                    logger.info("Attempting to persist image_to_3d asset")
                    try:
                        asset_output = await _persist_image_to_3d_asset(
                            db=db,
                            chat_id=chat_id,
                            output=output,
                            runpod_id=runpod_id,
                            status_payload=status_payload,
                        )
                        if asset_output is None:
                            logger.error("_persist_image_to_3d_asset returned None")
                        else:
                            logger.info(f"Image-to-3D asset persisted: {asset_output}")
                    except Exception as exc:
                        logger.exception(f"Image-to-3D asset persistence failed: {exc}")
                        asset_output = {
                            "error": f"asset persistence failed: {exc}",
                            "output": output,
                        }
                    if asset_output is not None:
                        output = asset_output
                else:
                    logger.info(f"Action is '{action}', not persisting asset")

                def make_json_safe(obj):
                    if isinstance(obj, dict):
                        return {k: make_json_safe(v) for k, v in obj.items()}
                    elif isinstance(obj, list):
                        return [make_json_safe(item) for item in obj]
                    elif isinstance(obj, datetime):
                        return obj.isoformat()
                    else:
                        return obj

                json_safe_output = make_json_safe(output) if output else status_payload

                assistant_content = (
                    json_safe_output
                    if isinstance(json_safe_output, str)
                    else json.dumps(json_safe_output, ensure_ascii=True)
                )
                assistant_message = MessageModel(
                    chat_id=chat_id,
                    role="assistant",
                    content=assistant_content,
                    metadata_json={
                        "runpod_id": runpod_id,
                        "status": status_value,
                        "output": json_safe_output,
                    },
                )
                db.add(assistant_message)
                await db.commit()
                await db.refresh(assistant_message)
                internal_job_id = None
                if isinstance(json_safe_output, dict):
                    internal_job_id = json_safe_output.get("job_id")

                final_job_id = job_id or internal_job_id

                await chat_socket_manager.send_to_chat(
                    chat_id,
                    {
                        "type": "runpod.completed",
                        "chat_id": chat_id,
                        "runpod_id": runpod_id,
                        "job_id": final_job_id,
                        "action": action,
                        # Frontend's handleJobCompletion gates asset extraction
                        # on `event.status === "COMPLETED"`. Without this field,
                        # the model URL is never pulled out of `output` and
                        # the chat never renders the generated mesh.
                        "status": status_value or "COMPLETED",
                        "message": _serialize_message(assistant_message),
                        "output": json_safe_output,
                    },
                )
            return

        if status_value in {"FAILED", "CANCELLED", "TIMED_OUT", "ERROR"}:
            import logging

            logger = logging.getLogger(__name__)

            logger.error(f" RunPod job FAILED: {runpod_id}")
            logger.error(f"Status: {status_value}")
            logger.error(
                f"Full status payload: "
                f"{json.dumps(_redact_for_log(status_payload), indent=2)}"
            )

            error_detail = (
                status_payload.get("error")
                or status_payload.get("output", {}).get("error")
                if isinstance(status_payload.get("output"), dict)
                else None
                or status_payload.get("message")
                or f"RunPod job failed with status: {status_value}"
            )
            logger.error(f"Error detail: {error_detail}")

            async with SessionLocal() as db:
                assistant_content = json.dumps(status_payload, ensure_ascii=True)
                assistant_message = MessageModel(
                    chat_id=chat_id,
                    role="assistant",
                    content=assistant_content,
                    metadata_json={
                        "runpod_id": runpod_id,
                        "status": status_value,
                        "error": status_payload,
                    },
                )
                db.add(assistant_message)
                await db.commit()
                await db.refresh(assistant_message)
                await chat_socket_manager.send_to_chat(
                    chat_id,
                    {
                        "type": "runpod.failed",
                        "chat_id": chat_id,
                        "runpod_id": runpod_id,
                        "message": _serialize_message(assistant_message),
                        "error": status_payload,
                    },
                )
            return

        await asyncio.sleep(settings.runpod_status_poll_interval_seconds)


@router.post(
    "/chats/{chat_id}/runpod",
    response_model=ChatRunpodResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def runpod_chat(
    chat_id: str,
    payload: ChatRunpodRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    # Single query: verify chat exists + ownership
    row = (
        await db.execute(
            select(ChatModel, SessionModel.user_id)
            .join(SessionModel, ChatModel.session_id == SessionModel.id)
            .where(ChatModel.id == chat_id)
        )
    ).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Chat not found")
    _, owner_id = row
    if owner_id and owner_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return await _handle_runpod_request(chat_id=chat_id, payload=payload, db=db)


from pydantic import BaseModel as _BaseModel


class ImageSearchResponse(_BaseModel):
    """Returned when the HTTP endpoint needs user to pick an image first."""
    status: str = "image_selection_required"
    search_query: str
    image_urls: list[str]
    request_id: str
    prompt: str


@router.post(
    "/chats/{chat_id}/image-to-3d",
    status_code=status.HTTP_202_ACCEPTED,
)
async def image_to_3d_chat(
    chat_id: str,
    payload: ImageTo3DRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> ImageTo3DResponse | ImageSearchResponse:
    import uuid as _uuid
    import logging
    from app.services import image_search_pending

    logger = logging.getLogger(__name__)

    row = (
        await db.execute(
            select(ChatModel, SessionModel.user_id)
            .join(SessionModel, ChatModel.session_id == SessionModel.id)
            .where(ChatModel.id == chat_id)
        )
    ).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Chat not found")
    _, owner_id = row
    if owner_id and owner_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    # If image is already provided, go straight to generation
    if payload.image_url:
        return await _handle_image_to_3d_request(chat_id=chat_id, payload=payload, db=db)

    # No image — fetch candidates and return them for user selection (non-blocking)
    if not payload.prompt:
        raise HTTPException(status_code=422, detail="Either image_url or prompt is required")

    candidates, search_query = await _fetch_image_candidates(payload.prompt)
    if not candidates:
        raise HTTPException(
            status_code=422,
            detail=f"Could not find reference images for '{payload.prompt}'. Try uploading an image instead.",
        )

    request_id = str(_uuid.uuid4())

    # Don't send WebSocket event here — the frontend already handles
    # the HTTP response and shows the picker. Sending both causes duplicates.

    return ImageSearchResponse(
        search_query=search_query,
        image_urls=candidates,
        request_id=request_id,
        prompt=payload.prompt,
    )


async def _handle_openscad_ws(
    websocket: WebSocket,
    chat_id: str,
    payload: dict,
) -> None:
    """Handle OpenSCAD generation request over WebSocket with token streaming."""
    import logging

    logger = logging.getLogger(__name__)

    content = payload.get("content", "")
    images = payload.get("images")  # list of {data, media_type}
    options = payload.get("options", {})
    logger.info(f"[WS] code_language={options.get('code_language', 'openscad')}")

    try:
        async with SessionLocal() as db:
            # 1. Process images
            base64_urls: list[str] = []
            persistent_urls: list[str] = []
            if images:
                for img in images[:4]:
                    filename = save_chat_image(img["data"], img["media_type"])
                    base64_urls.append(f"data:{img['media_type']};base64,{img['data']}")
                    persistent_urls.append(f"/uploads/chat-images/{filename}")

            # 2. Save user message
            user_meta = {"images": persistent_urls} if persistent_urls else None
            user_msg = MessageModel(
                chat_id=chat_id,
                role="user",
                content=content,
                metadata_json=user_meta,
            )
            db.add(user_msg)
            await db.commit()

            # 3. Build history
            history = await _build_lc_history(db, chat_id)

            # 4. Get chat session_id for asset saving
            chat_obj = await db.get(ChatModel, chat_id)
            chat_session_id = chat_obj.session_id if chat_obj else None

            # 5. Stream tokens
            #    Pass db=None — the agent will open its own session for RAG to avoid
            #    asyncpg "operation in progress" errors from sharing one connection.
            final_data = None
            async for event in run_agent_stream(
                content,
                history,
                image_data_urls=base64_urls or None,
                provider=options.get("llm_provider"),
                model=options.get("llm_model"),
                thinking=options.get("llm_thinking", False),
                db=None,
                code_language=options.get("code_language", "openscad"),
            ):
                if event["type"] == "token":
                    await _safe_send_json(websocket, {
                        "type": "openscad.token",
                        "chat_id": chat_id,
                        "text": event["text"],
                    })
                elif event["type"] == "tool":
                    await _safe_send_json(websocket, {
                        "type": "openscad.tool",
                        "chat_id": chat_id,
                        "tool_name": event["tool_name"],
                        "status": event["status"],
                    })
                elif event["type"] == "image_to_3d.trigger":
                    # Agent wants to generate 3D from image — submit job
                    # to whichever backend IMAGE_TO_3D_BACKEND selects.
                    trigger_data = event["data"]
                    image_url = trigger_data.get("image_url")
                    if image_url:
                        try:
                            i3d_client = _get_image_to_3d_client()
                            i3d_response = await i3d_client.start_raw_job({
                                "action": "image_to_3d",
                                "image_url": image_url,
                                "prompt": trigger_data.get("prompt", ""),
                                "output_format": "glb",
                            })
                            i3d_runpod_id = i3d_response.get("id")
                            if i3d_runpod_id:
                                asyncio.create_task(
                                    _runpod_poll_and_emit(
                                        chat_id=chat_id,
                                        runpod_id=i3d_runpod_id,
                                        action="image_to_3d",
                                        status_timeout_seconds=settings.image_to_3d_timeout_seconds,
                                    )
                                )
                                await _safe_send_json(websocket, {
                                    "type": "image_to_3d.started",
                                    "chat_id": chat_id,
                                    "runpod_id": i3d_runpod_id,
                                    "image_query": trigger_data.get("image_query", ""),
                                })
                        except Exception as e:
                            logger.error(f"Image-to-3D trigger failed: {e}")
                            await _safe_send_json(websocket, {
                                "type": "image_to_3d.error",
                                "chat_id": chat_id,
                                "message": str(e),
                            })
                elif event["type"] == "image.generated":
                    # Surface a Gemini-generated/edited image inline in chat.
                    img_data = event.get("data", {})
                    img_url = img_data.get("url") or ""
                    img_prompt = (img_data.get("prompt") or "").strip()
                    img_tool = img_data.get("tool")

                    # Persist a row so the image survives reload. The chat
                    # loader (chatService.ts) reads metadata_json.images
                    # and surfaces them as message.imageUrls. Only persist
                    # when the URL is durable — http(s) (R2) or a data:
                    # URL the browser can decode standalone. Skipping the
                    # write entirely if the URL is empty avoids creating
                    # ghost messages.
                    if img_url:
                        action = "Edited" if img_tool == "edit_image" else "Generated"
                        caption = (
                            f"{action}: {img_prompt}" if img_prompt else f"{action} image"
                        )
                        try:
                            async with SessionLocal() as _img_db:
                                img_msg = MessageModel(
                                    chat_id=chat_id,
                                    role="assistant",
                                    content=caption,
                                    metadata_json={
                                        "images": [img_url],
                                        "generated_image": True,
                                        "tool": img_tool,
                                        "prompt": img_prompt,
                                    },
                                )
                                _img_db.add(img_msg)
                                await _img_db.commit()
                                await _img_db.refresh(img_msg)
                                # Tag the WS event with the persisted id so
                                # the frontend can dedupe the optimistic
                                # client-side row against the server one.
                                img_data = {
                                    **img_data,
                                    "message_id": str(img_msg.id),
                                }
                        except Exception as exc:
                            logger.exception(
                                f"Failed to persist generated-image message: {exc}"
                            )

                    await _safe_send_json(websocket, {
                        "type": "image.generated",
                        "chat_id": chat_id,
                        "url": img_url,
                        "prompt": img_prompt,
                        "tool": img_tool,
                        "source_image_url": img_data.get("source_image_url"),
                        "message_id": img_data.get("message_id"),
                    })
                elif event["type"] == "done":
                    final_data = event["data"]
                elif event["type"] == "error":
                    await _safe_send_json(websocket, {
                        "type": "openscad.error",
                        "chat_id": chat_id,
                        "message": event["message"],
                    })
                    return

            # 6. Persist assistant message and send done event
            if final_data:
                code = final_data.get("openscad_code", "")
                parameters = final_data.get("parameters", [])
                model_type = final_data.get("model_type", "chat")
                message = final_data.get("message", "Model generated." if code else "Here to help!")

                msg_meta = {
                    "openscad_code": code,
                    "parameters": parameters,
                    "model_type": model_type,
                    "message": message,
                }
                if final_data.get("experiment"):
                    msg_meta["experiment"] = final_data["experiment"]
                if final_data.get("quality_metrics"):
                    msg_meta["quality_metrics"] = final_data["quality_metrics"]

                ai_msg = MessageModel(
                    chat_id=chat_id,
                    role="assistant",
                    content=message,
                    metadata_json=msg_meta,
                )
                db.add(ai_msg)
                await db.commit()
                await db.refresh(ai_msg)
                ai_msg_id = str(ai_msg.id)

                # Auto-save parts
                if code and chat_session_id:
                    try:
                        await _save_parts_as_assets(
                            db=db,
                            session_id=chat_session_id,
                            code=code,
                            model_type=model_type,
                            message=message,
                        )
                    except Exception:
                        logger.exception("Part auto-save failed (non-critical)")

                await _safe_send_json(websocket, {
                    "type": "openscad.done",
                    "chat_id": chat_id,
                    "data": {
                        "id": ai_msg_id,
                        "openscad_code": code,
                        "parameters": parameters,
                        "model_type": model_type,
                        "message": message,
                    },
                })

    except Exception as e:
        logger.exception(f"OpenSCAD WS handler error: {e}")
        try:
            await _safe_send_json(websocket, {
                "type": "openscad.error",
                "chat_id": chat_id,
                "message": str(e),
            })
        except Exception:
            pass


@router.websocket("/chat-socket/{chat_id}")
async def chat_socket(
    websocket: WebSocket,
    chat_id: str,
    token: str | None = Query(default=None),
):
    if not token:
        await websocket.close(code=1008)
        return

    try:
        user = get_supabase_user(token)
    except HTTPException:
        await websocket.close(code=1008)
        return
    user_id = str(user["id"])

    async with SessionLocal() as db:
        # Single query: verify chat exists + ownership
        row = (
            await db.execute(
                select(ChatModel, SessionModel.user_id)
                .join(SessionModel, ChatModel.session_id == SessionModel.id)
                .where(ChatModel.id == chat_id)
            )
        ).one_or_none()
        if not row:
            await websocket.close(code=1008)
            return
        _, owner_id = row
        if owner_id and owner_id != user_id:
            await websocket.close(code=1008)
            return

    await chat_socket_manager.connect(chat_id, websocket)
    async with SessionLocal() as db:
        messages = await _load_serialized_messages(db, chat_id)
    await _safe_send_json(websocket, 
        {
            "type": "chat.history",
            "chat_id": chat_id,
            "messages": messages,
        }
    )
    try:
        while True:
            message = await websocket.receive_text()
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue

            msg_type = payload.get("type")

            # Handle OpenSCAD generation requests
            if msg_type == "openscad.request":
                asyncio.create_task(_handle_openscad_ws(websocket, chat_id, payload))
                continue

            # Handle image-to-3D requests (async task so WS loop stays responsive during image selection)
            if msg_type == "image_to_3d.request":
                asyncio.create_task(_handle_image_to_3d_ws_task(websocket, chat_id, payload))
                continue

            # Handle image selection — user picked an image, download it and start 3D generation
            if msg_type == "image_to_3d.image_selected":
                selected_url = payload.get("image_url")
                prompt = payload.get("prompt", "")
                output_format = payload.get("output_format", "glb")
                skip_segmentation = bool(payload.get("skip_segmentation", False))
                with_texture = bool(payload.get("with_texture", False))
                if selected_url:
                    asyncio.create_task(_handle_image_selected_ws(
                        websocket, chat_id, selected_url, prompt, output_format,
                        skip_segmentation, with_texture,
                    ))
                continue

            # Picker action: generate a fresh candidate image via nano banana.
            # Returns it back to the picker; the user has to click "Use for 3D"
            # to actually trigger RunPod via image_to_3d.image_selected.
            if msg_type == "image_to_3d.generate_custom":
                custom_prompt = (payload.get("prompt") or "").strip()
                request_id = payload.get("request_id") or ""
                if custom_prompt:
                    asyncio.create_task(_handle_generate_custom_image_ws(
                        websocket, chat_id, custom_prompt, request_id,
                    ))
                continue

            # Picker action: refine the current candidate via Gemini edit.
            if msg_type == "image_to_3d.edit_candidate":
                edit_prompt = (payload.get("prompt") or "").strip()
                image_url = payload.get("image_url") or ""
                request_id = payload.get("request_id") or ""
                if edit_prompt and image_url:
                    asyncio.create_task(_handle_edit_candidate_ws(
                        websocket, chat_id, request_id, image_url, edit_prompt,
                    ))
                continue

            # Handle mesh boolean modification requests
            if msg_type == "mesh_modification.request":
                asyncio.create_task(_handle_mesh_modification_ws(websocket, chat_id, payload))
                continue

            # Handle Flux inpaint requests
            if msg_type == "inpaint.request":
                asyncio.create_task(_handle_inpaint_ws(websocket, chat_id, payload))
                continue

            # Handle RunPod requests
            if msg_type != "runpod.request":
                continue
            request_payload = payload.get("payload") or {}
            try:
                request_model = ChatRunpodRequest.model_validate(request_payload)
            except Exception:
                continue
            async with SessionLocal() as db:
                response = await _handle_runpod_request(
                    db=db,
                    chat_id=chat_id,
                    payload=request_model,
                )
            await chat_socket_manager.send_to_chat(
                chat_id,
                {
                    "type": "runpod.queued",
                    "chat_id": chat_id,
                    "runpod_id": response.runpod_id,
                    "message_id": response.message_id,
                    "status": response.status,
                },
            )
    except WebSocketDisconnect:
        await chat_socket_manager.disconnect(chat_id, websocket)
    except RuntimeError as e:
        # Starlette raises `WebSocket is not connected. Need to call
        # "accept" first.` from receive_text() when the application
        # state isn't CONNECTED — that happens both pre-accept (a true
        # bug) AND when the client disconnected mid-handshake or right
        # after accept(), before our first receive_text(). In our case
        # accept() ran (chat_socket_manager.connect awaited it), so
        # this branch fires only on the race condition. Treat as a
        # normal disconnect; anything else re-raises.
        msg = str(e)
        if "not connected" in msg or "accept" in msg:
            logger.debug(
                f"[chat_socket] client disconnected before first message: {e}"
            )
            await chat_socket_manager.disconnect(chat_id, websocket)
        else:
            raise
