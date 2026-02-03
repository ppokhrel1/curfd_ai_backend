from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_current_user_id
from app.core.jwt import decode_access_token, token_hash
from app.db.session import SessionLocal, get_db
from app.models.asset import Asset as AssetModel
from app.models.asset_meta import AssetMeta as AssetMetaModel
from app.models.chat import Chat as ChatModel
from app.models.job import Job as JobModel
from app.models.message import Message as MessageModel
from app.models.revoked_token import RevokedToken
from app.schemas.asset import AssetRead
from app.schemas.message import MessageRead
from app.schemas.runpod import ChatRunpodRequest, ChatRunpodResponse
from app.services.chat_socket import chat_socket_manager
from app.services.runpod import get_runpod_client

router = APIRouter()


def _serialize_message(message: MessageModel) -> dict[str, Any]:
    return MessageRead.model_validate(message).model_dump()


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


def _serialize_asset(asset: AssetModel) -> dict[str, Any]:
    return AssetRead.model_validate(asset).model_dump()


def _extract_runpod_asset_data(output: Any) -> dict[str, Any] | None:
    if not isinstance(output, dict):
        return None
    if isinstance(output.get("data"), dict):
        return output["data"]
    inner = output.get("output")
    if isinstance(inner, dict) and isinstance(inner.get("data"), dict):
        return inner["data"]
    return None


def _persist_generate_scad_asset(
    *,
    db: Session,
    chat_id: str,
    output: Any,
    runpod_id: str | None,
    requirements_json: dict | None,
    job_id: str | None,
    asset_type: str | None,
    storage_provider: str | None,
) -> dict[str, Any] | None:
    data = _extract_runpod_asset_data(output)
    if not data:
        return None
    download_url = data.get("download_url")
    if not download_url:
        return None

    chat = db.get(ChatModel, chat_id)
    if not chat:
        return None

    resolved_job = None
    if job_id:
        resolved_job = db.get(JobModel, job_id)
        if not resolved_job:
            raise ValueError("job_id not found for generate_scad asset persistence")

    if not resolved_job:
        prompt = None
        if isinstance(requirements_json, dict):
            prompt = requirements_json.get("primary_function") or requirements_json.get(
                "description_natural_language"
            )
        resolved_job = JobModel(
            session_id=chat.session_id,
            status="succeeded",
            prompt=prompt,
            spec_json=requirements_json,
            output_format=asset_type or "scad_zip",
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        db.add(resolved_job)
        db.commit()
        db.refresh(resolved_job)

    asset = AssetModel(
        job_id=resolved_job.id,
        asset_type=asset_type or "scad_zip",
        uri=download_url,
        storage_provider=storage_provider or "b2",
        metadata_json={
            "runpod_id": runpod_id,
            "file_id": data.get("file_id"),
            "filename": data.get("filename"),
            "download_url": download_url,
            "requirements_json": requirements_json,
        },
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)

    meta = AssetMetaModel(
        asset_id=asset.id,
        part_name=data.get("filename"),
        uploaded_by=chat.session.user_id if chat.session else None,
    )
    db.add(meta)
    db.commit()

    return _serialize_asset(asset)


async def _handle_runpod_request(
    *,
    chat_id: str,
    payload: ChatRunpodRequest,
    db: Session,
) -> ChatRunpodResponse:
    if payload.action == "generate_scad" and not payload.requirements_json:
        raise HTTPException(status_code=422, detail="requirements_json is required for generate_scad")
    if payload.action == "process_requirements" and not payload.content:
        raise HTTPException(status_code=422, detail="content is required for process_requirements")

    metadata_json = payload.metadata_json or {}
    metadata_json["runpod_action"] = payload.action
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
        db.commit()
        db.refresh(user_message)

    try:
        client = get_runpod_client()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        runpod_response = await client.start_job(
            action=payload.action,
            prompt=payload.content if payload.action == "process_requirements" else None,
            requirements_json=payload.requirements_json if payload.action == "generate_scad" else None,
            history=_normalize_history(
                [item.model_dump() for item in payload.history] if payload.history else None
            ),
            sync=payload.sync,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Runpod request failed: {exc}") from exc

    asset_context = None
    if payload.action == "generate_scad":
        asset_context = {
            "requirements_json": payload.requirements_json,
            "job_id": (payload.metadata_json or {}).get("job_id"),
            "asset_type": (payload.metadata_json or {}).get("asset_type"),
            "storage_provider": (payload.metadata_json or {}).get("storage_provider"),
        }

    if payload.sync:
        output = runpod_response
        if payload.action == "generate_scad":
            try:
                asset_output = _persist_generate_scad_asset(
                    db=db,
                    chat_id=chat_id,
                    output=output,
                    runpod_id=None,
                    requirements_json=asset_context["requirements_json"] if asset_context else None,
                    job_id=asset_context["job_id"] if asset_context else None,
                    asset_type=asset_context["asset_type"] if asset_context else None,
                    storage_provider=asset_context["storage_provider"] if asset_context else None,
                )
            except Exception as exc:
                asset_output = {"error": f"asset persistence failed: {exc}", "output": output}
            if asset_output is not None:
                output = asset_output
        assistant_content = (
            output if isinstance(output, str) else json.dumps(output or {}, ensure_ascii=True)
        )
        assistant_message = MessageModel(
            chat_id=chat_id,
            role="assistant",
            content=assistant_content,
            metadata_json={"runpod_action": payload.action, "output": output},
        )
        db.add(assistant_message)
        db.commit()
        db.refresh(assistant_message)
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
        return ChatRunpodResponse(status="completed", runpod_id=None, message_id=message_id)

    runpod_id = runpod_response.get("id")
    if not runpod_id:
        raise HTTPException(status_code=502, detail="Runpod did not return a job id")

    asyncio.create_task(
        _runpod_poll_and_emit(
            chat_id=chat_id,
            runpod_id=runpod_id,
            action=payload.action,
            asset_context=asset_context,
        )
    )

    message_id = user_message.id if user_message else runpod_id
    return ChatRunpodResponse(status="queued", runpod_id=runpod_id, message_id=message_id)


async def _runpod_poll_and_emit(
    *,
    chat_id: str,
    runpod_id: str,
    action: str,
    asset_context: dict[str, Any] | None = None,
) -> None:
    try:
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

    await chat_socket_manager.send_to_chat(
        chat_id,
        {"type": "runpod.started", "chat_id": chat_id, "runpod_id": runpod_id, "action": action},
    )
    last_status = None
    deadline = time.monotonic() + settings.runpod_status_timeout_seconds

    while True:
        if time.monotonic() > deadline:
            await chat_socket_manager.send_to_chat(
                chat_id,
                {
                    "type": "runpod.timeout",
                    "chat_id": chat_id,
                    "runpod_id": runpod_id,
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
            await chat_socket_manager.send_to_chat(
                chat_id,
                {
                    "type": "runpod.status",
                    "chat_id": chat_id,
                    "runpod_id": runpod_id,
                    "status": status_value,
                },
            )
            last_status = status_value

        if status_value == "COMPLETED":
            output = status_payload.get("output")
            db = SessionLocal()
            try:
                if action == "generate_scad":
                    try:
                        asset_output = _persist_generate_scad_asset(
                            db=db,
                            chat_id=chat_id,
                            output=output,
                            runpod_id=runpod_id,
                            requirements_json=(
                                asset_context.get("requirements_json") if asset_context else None
                            ),
                            job_id=asset_context.get("job_id") if asset_context else None,
                            asset_type=asset_context.get("asset_type") if asset_context else None,
                            storage_provider=(
                                asset_context.get("storage_provider") if asset_context else None
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
                    else json.dumps(output or status_payload, ensure_ascii=True)
                )
                assistant_message = MessageModel(
                    chat_id=chat_id,
                    role="assistant",
                    content=assistant_content,
                    metadata_json={"runpod_id": runpod_id, "status": status_value, "output": output},
                )
                db.add(assistant_message)
                db.commit()
                db.refresh(assistant_message)
                await chat_socket_manager.send_to_chat(
                    chat_id,
                    {
                        "type": "runpod.completed",
                        "chat_id": chat_id,
                        "runpod_id": runpod_id,
                        "message": _serialize_message(assistant_message),
                        "output": output,
                    },
                )
            finally:
                db.close()
            return

        if status_value in {"FAILED", "CANCELLED", "TIMED_OUT", "ERROR"}:
            db = SessionLocal()
            try:
                assistant_content = json.dumps(status_payload, ensure_ascii=True)
                assistant_message = MessageModel(
                    chat_id=chat_id,
                    role="assistant",
                    content=assistant_content,
                    metadata_json={"runpod_id": runpod_id, "status": status_value, "error": status_payload},
                )
                db.add(assistant_message)
                db.commit()
                db.refresh(assistant_message)
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
            finally:
                db.close()
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
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    chat = db.get(ChatModel, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    if chat.session and chat.session.user_id and chat.session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return await _handle_runpod_request(chat_id=chat_id, payload=payload, db=db)


@router.websocket("/chat-socket/{chat_id}")
async def chat_socket(
    websocket: WebSocket,
    chat_id: str,
    token: str | None = Query(default=None),
):
    if not token:
        await websocket.close(code=1008)
        return

    user_id = decode_access_token(token)
    if not user_id:
        await websocket.close(code=1008)
        return

    db = SessionLocal()
    try:
        revoked = (
            db.query(RevokedToken)
            .filter(RevokedToken.token_hash == token_hash(token))
            .first()
        )
        if revoked:
            await websocket.close(code=1008)
            return

        chat = db.get(ChatModel, chat_id)
        if not chat:
            await websocket.close(code=1008)
            return
        if chat.session and chat.session.user_id and chat.session.user_id != user_id:
            await websocket.close(code=1008)
            return
    finally:
        db.close()

    await chat_socket_manager.connect(chat_id, websocket)
    try:
        while True:
            message = await websocket.receive_text()
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("type") != "runpod.request":
                continue
            request_payload = payload.get("payload") or {}
            try:
                request_model = ChatRunpodRequest.model_validate(request_payload)
            except Exception:
                continue
            db = SessionLocal()
            try:
                response = await _handle_runpod_request(
                    chat_id=chat_id, payload=request_model, db=db
                )
            finally:
                db.close()
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
