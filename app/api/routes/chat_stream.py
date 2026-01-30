from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_current_user_id
from app.core.jwt import decode_access_token, token_hash
from app.db.session import SessionLocal, get_db
from app.models.chat import Chat as ChatModel
from app.models.message import Message as MessageModel
from app.models.revoked_token import RevokedToken
from app.schemas.message import MessageRead
from app.schemas.runpod import ChatRunpodRequest, ChatRunpodResponse
from app.services.chat_socket import chat_socket_manager
from app.services.runpod import get_runpod_client

router = APIRouter()


def _serialize_message(message: MessageModel) -> dict[str, Any]:
    return MessageRead.model_validate(message).model_dump()


async def _runpod_poll_and_emit(
    *,
    chat_id: str,
    runpod_id: str,
    action: str,
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
            assistant_content = (
                output
                if isinstance(output, str)
                else json.dumps(output or status_payload, ensure_ascii=True)
            )
            db = SessionLocal()
            try:
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

    if payload.action == "generate_scad" and not payload.requirements_json:
        raise HTTPException(status_code=422, detail="requirements_json is required for generate_scad")

    metadata_json = payload.metadata_json or {}
    metadata_json["runpod_action"] = payload.action
    if payload.requirements_json is not None:
        metadata_json["requirements_json"] = payload.requirements_json

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
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Runpod request failed: {exc}") from exc
    runpod_id = runpod_response.get("id")
    if not runpod_id:
        raise HTTPException(status_code=502, detail="Runpod did not return a job id")

    asyncio.create_task(
        _runpod_poll_and_emit(chat_id=chat_id, runpod_id=runpod_id, action=payload.action)
    )

    return ChatRunpodResponse(status="queued", runpod_id=runpod_id, message_id=user_message.id)


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
            await websocket.receive_text()
    except WebSocketDisconnect:
        await chat_socket_manager.disconnect(chat_id, websocket)
