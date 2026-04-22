import json
import logging
import os
import re
import subprocess
import tempfile
import traceback

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from langchain_core.messages import HumanMessage, AIMessage

from app.core.deps import get_current_user_id_async
from app.db.session import get_db
from app.models.asset import Asset as AssetModel
from app.models.asset_meta import AssetMeta as AssetMetaModel
from app.models.chat import Chat as ChatModel
from app.models.job import Job as JobModel
from app.models.message import Message as MessageModel
from app.schemas.message import MessageCreate, MessageRead
from app.schemas.openscad import OpenSCADResponse
from app.services.openscad_agent import run_agent, run_agent_stream
from app.api.routes.uploads import save_chat_image
from app.cadquery.tasks import _strip_toplevel_calls, _clean_scad

logger = logging.getLogger(__name__)

router = APIRouter()

# Modules to exclude from part extraction (structural wrappers)
_EXCLUDED_MODULES = frozenset({
    "main", "combined", "assembly", "full", "complete",
    "result", "model", "scene", "object", "all_parts",
    "body_combined", "total",
})

_MODULE_RE = re.compile(r"^\s*module\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(([^)]*)\)", re.MULTILINE)


def _extract_module_names(scad_code: str) -> list[str]:
    """Extract module names from OpenSCAD code, excluding structural wrappers
    and parameterized helper modules (which can't be compiled standalone)."""
    return [
        name for name, params in _MODULE_RE.findall(scad_code)
        if name.lower() not in _EXCLUDED_MODULES and not params.strip()
    ]


async def _save_parts_as_assets(
    db: AsyncSession,
    session_id: str,
    code: str,
    model_type: str,
    message: str,
) -> None:
    """Save generated OpenSCAD parts as assets for future search/swap."""
    try:
        modules = _extract_module_names(code)
        if not modules:
            return

        # Find or create a job for this session
        job_result = await db.execute(
            select(JobModel)
            .where(JobModel.session_id == session_id)
            .order_by(JobModel.created_at.desc())
            .limit(1)
        )
        job = job_result.scalar_one_or_none()

        if not job:
            job = JobModel(
                session_id=session_id,
                status="completed",
                prompt=message,
            )
            db.add(job)
            await db.flush()

        job_id = job.id  # capture before any further flushes

        # Create parent asset for the full model
        parent_asset = AssetModel(
            job_id=job_id,
            asset_type="openscad_model",
            uri=f"openscad://{model_type}",
            metadata_json={
                "scadCode": code,
                "model_type": model_type,
                "message": message,
                "part_names": modules,
            },
        )
        db.add(parent_asset)
        await db.flush()

        # Create child asset + meta for each module/part
        parent_id = parent_asset.id  # capture before further flushes

        for module_name in modules:
            part_asset = AssetModel(
                job_id=job_id,
                asset_type="openscad_part",
                uri=f"openscad://{model_type}/{module_name}",
                metadata_json={
                    "model_type": model_type,
                    "parent_asset_id": parent_id,
                },
            )
            db.add(part_asset)
            await db.flush()

            meta = AssetMetaModel(
                asset_id=part_asset.id,
                part_name=module_name,
                component_of=parent_id,
            )
            db.add(meta)

        await db.commit()
        logger.info(f"Saved {len(modules)} parts as assets: {modules}")

    except Exception as e:
        logger.error(f"Failed to save parts as assets: {e}", exc_info=True)

# ── Image helpers ─────────────────────────────────────────────────────────────

def _process_payload_images(payload) -> tuple[list[str], list[str]]:
    """Save payload images to disk. Returns (base64_data_urls, persistent_urls)."""
    if not payload.images:
        return [], []

    base64_data_urls = []
    persistent_urls = []
    for img in payload.images[:4]:  # max 4 images
        filename = save_chat_image(img.data, img.media_type)
        base64_data_urls.append(f"data:{img.media_type};base64,{img.data}")
        persistent_urls.append(f"/uploads/chat-images/{filename}")

    logger.info(f"[IMAGES] Processed {len(persistent_urls)} images")
    return base64_data_urls, persistent_urls


# ── History builder ───────────────────────────────────────────────────────────

async def _build_lc_history(db: AsyncSession, chat_id: str) -> list:
    """
    Build LangChain message history from DB.
    For assistant messages, inject the actual OpenSCAD code from metadata_json
    so the model can see and refine its previous output (enables iterative design).
    """
    result = await db.execute(
        select(MessageModel)
        .where(MessageModel.chat_id == chat_id)
        .order_by(MessageModel.created_at.asc())
    )
    records = result.scalars().all()

    # Drop the most-recent user message — it's passed separately as {input}
    records = list(records)
    if records and records[-1].role == "user":
        records = records[:-1]

    lc_messages = []
    for msg in records:
        if msg.role == "user":
            text = msg.content or ""
            # Note if images were attached (don't re-send actual images)
            meta = msg.metadata_json
            if isinstance(meta, dict) and meta.get("images"):
                text += "\n[User attached image(s)]"
            lc_messages.append(HumanMessage(content=text))
        elif msg.role == "assistant":
            meta = msg.metadata_json
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = None

            code = meta.get("openscad_code") if isinstance(meta, dict) else None

            # Build shape context summary from metadata
            context_lines = []
            if isinstance(meta, dict):
                # Model description
                if meta.get("message"):
                    context_lines.append(f"Description: {meta['message']}")
                if meta.get("model_type"):
                    context_lines.append(f"Type: {meta['model_type']}")
                # Parameters
                params = meta.get("parameters")
                if params and isinstance(params, list):
                    param_strs = [f"{p.get('name', '?')}={p.get('value', '?')}" for p in params[:15]]
                    context_lines.append(f"Parameters: {', '.join(param_strs)}")
                # Image-to-3D output
                output = meta.get("output")
                if isinstance(output, dict):
                    model_url = output.get("model_url") or output.get("download_url") or output.get("uri")
                    if model_url:
                        context_lines.append(f"3D model: {model_url}")
                    parts = output.get("parts", [])
                    if parts:
                        part_names = [p.get("name", "?") for p in parts]
                        context_lines.append(f"Parts ({len(parts)}): {', '.join(part_names)}")

            # CADAM pattern: send raw code as assistant content so the code-gen
            # model sees its own previous output cleanly (no markdown wrapping).
            if code:
                content = code
                if context_lines:
                    # Prepend shape context as a comment block
                    summary = "\n".join(f"// {line}" for line in context_lines)
                    content = f"{summary}\n{code}"
                lc_messages.append(AIMessage(content=content))
            elif context_lines:
                # No code but has shape context (e.g. image_to_3d result)
                summary = "\n".join(context_lines)
                lc_messages.append(AIMessage(content=summary))
            elif msg.content and msg.content != "Model generated.":
                lc_messages.append(AIMessage(content=msg.content))

    return lc_messages


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post("/process_requirements", response_model=MessageRead)
async def gemini_openscad_generate_route(
    payload: MessageCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id_async),
):
    stmt = (
        select(ChatModel)
        .options(selectinload(ChatModel.session))
        .where(ChatModel.id == payload.chat_id)
    )
    result = await db.execute(stmt)
    chat = result.scalar_one_or_none()

    if not chat or (chat.session and user_id and chat.session.user_id != user_id):
        raise HTTPException(status_code=403, detail="Unauthorized")

    # Capture before any commit expires the ORM object
    chat_session_id = chat.session_id

    try:
        # 1. Process images if present
        base64_urls, persistent_urls = _process_payload_images(payload)

        # Save user message with image metadata
        user_meta = {"images": persistent_urls} if persistent_urls else None
        db.add(MessageModel(
            chat_id=payload.chat_id, role="user", content=payload.content,
            metadata_json=user_meta,
        ))
        await db.commit()

        # 2. Build smart history (includes previous OpenSCAD code for iterative refinement)
        history = await _build_lc_history(db, payload.chat_id)

        # 3. Run LangChain agent with tools + RAG examples
        extra_meta: dict = {}
        response: OpenSCADResponse = await run_agent(
            user_input=payload.content,
            history=history,
            image_data_urls=base64_urls or None,
            provider=payload.llm_provider,
            model=payload.llm_model,
            thinking=payload.llm_thinking,
            db=db,
            _extra_meta=extra_meta,
        )

        # 4. Extract fields (already validated by schema)
        code: str = response.openscad_code or ""
        parameters: list = [p.model_dump() for p in (response.parameters or [])]
        model_type: str = response.model_type or "chat"
        message: str = response.message or ("Model generated." if code else "Here to help!")

        # 5. Persist assistant message — store code in metadata, friendly text as content
        msg_meta: dict = {
            "openscad_code": code,
            "parameters": parameters,
            "model_type": model_type,
            "message": message,
        }
        if extra_meta.get("experiment"):
            msg_meta["experiment"] = extra_meta["experiment"]
        if extra_meta.get("quality_metrics"):
            msg_meta["quality_metrics"] = extra_meta["quality_metrics"]

        ai_msg = MessageModel(
            chat_id=payload.chat_id,
            role="assistant",
            content=message,
            metadata_json=msg_meta,
        )
        db.add(ai_msg)
        await db.commit()
        await db.refresh(ai_msg)

        # 6. Auto-save parts as searchable assets (best-effort, non-blocking)
        if code:
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
            # _save_parts_as_assets calls db.commit() which expires ai_msg;
            # refresh so FastAPI can serialize it
            await db.refresh(ai_msg)

        return ai_msg

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ── SSE Streaming Route ──────────────────────────────────────────────────────

@router.post("/process_requirements/stream")
async def stream_openscad_generate(
    payload: MessageCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id_async),
):
    """SSE streaming endpoint — single LLM call, tokens streamed in real time."""
    stmt = (
        select(ChatModel)
        .options(selectinload(ChatModel.session))
        .where(ChatModel.id == payload.chat_id)
    )
    result = await db.execute(stmt)
    chat = result.scalar_one_or_none()

    if not chat or (chat.session and user_id and chat.session.user_id != user_id):
        raise HTTPException(status_code=403, detail="Unauthorized")

    chat_session_id = chat.session_id

    # Process images if present
    base64_urls, persistent_urls = _process_payload_images(payload)

    # Save user message with image metadata
    user_meta = {"images": persistent_urls} if persistent_urls else None
    db.add(MessageModel(
        chat_id=payload.chat_id, role="user", content=payload.content,
        metadata_json=user_meta,
    ))
    await db.commit()

    # Build history
    history = await _build_lc_history(db, payload.chat_id)

    async def event_generator():
        try:
            final_data = None
            async for event in run_agent_stream(
                payload.content, history,
                image_data_urls=base64_urls or None,
                provider=payload.llm_provider,
                model=payload.llm_model,
                thinking=payload.llm_thinking,
                db=db,
            ):
                if event["type"] == "token":
                    yield f"event: token\ndata: {json.dumps({'text': event['text']})}\n\n"
                elif event["type"] == "tool":
                    yield f"event: tool\ndata: {json.dumps({'tool_name': event['tool_name'], 'status': event['status']})}\n\n"
                elif event["type"] == "done":
                    final_data = event["data"]
                elif event["type"] == "error":
                    yield f"event: error\ndata: {json.dumps({'message': event['message']})}\n\n"
                    return

            if final_data:
                code = final_data.get("openscad_code", "")
                parameters = final_data.get("parameters", [])
                model_type = final_data.get("model_type", "chat")
                message = final_data.get("message", "Model generated." if code else "Here to help!")

                # Persist assistant message
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
                    chat_id=payload.chat_id,
                    role="assistant",
                    content=message,
                    metadata_json=msg_meta,
                )
                db.add(ai_msg)
                await db.commit()
                await db.refresh(ai_msg)

                # Capture ID before any further commits expire the ORM object
                ai_msg_id = str(ai_msg.id)

                # Auto-save parts as searchable assets (preserves parts functionality)
                if code:
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

                # Send final done event with structured data
                yield f"event: done\ndata: {json.dumps({'id': ai_msg_id, 'openscad_code': code, 'parameters': parameters, 'model_type': model_type, 'message': message})}\n\n"

        except Exception as e:
            logger.exception("SSE streaming error")
            yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Compile single part ──────────────────────────────────────────────────────

class CompilePartRequest(BaseModel):
    asset_id: str


@router.post("/compile-part")
async def compile_part(
    payload: CompilePartRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id_async),
):
    """Compile a single part's OpenSCAD module to STL for swap.

    Looks up the part asset → parent asset (has full SCAD code) → compiles
    just the target module → returns the STL file.
    """
    # 1. Load the part asset and its meta
    part_asset = await db.get(AssetModel, payload.asset_id)
    if not part_asset:
        raise HTTPException(status_code=404, detail="Part asset not found")

    meta_result = await db.execute(
        select(AssetMetaModel).where(AssetMetaModel.asset_id == payload.asset_id)
    )
    meta = meta_result.scalar_one_or_none()
    if not meta or not meta.part_name:
        raise HTTPException(status_code=400, detail="No part name found for asset")

    module_name = meta.part_name

    # 2. Get parent asset with the full SCAD code
    parent_id = meta.component_of or (
        part_asset.metadata_json or {}
    ).get("parent_asset_id")
    if not parent_id:
        raise HTTPException(status_code=400, detail="No parent model found for part")

    parent_asset = await db.get(AssetModel, parent_id)
    if not parent_asset or not parent_asset.metadata_json:
        raise HTTPException(status_code=404, detail="Parent model not found")

    scad_code = parent_asset.metadata_json.get("scadCode", "")
    if not scad_code:
        raise HTTPException(status_code=400, detail="Parent model has no SCAD code")

    # 3. Compile just this module
    scad_code = _clean_scad(scad_code)  # Strip markdown fences/language tags
    base_script = _strip_toplevel_calls(scad_code)
    module_script = base_script + f"\n{module_name}();\n"

    logger.info(f"[compile-part] module={module_name}, script length={len(module_script)}")

    tmp_dir = tempfile.mkdtemp(prefix="scad_part_")
    scad_path = os.path.join(tmp_dir, "part.scad")
    stl_path = os.path.join(tmp_dir, f"{module_name}.stl")

    try:
        with open(scad_path, "w") as f:
            f.write(module_script)

        result = subprocess.run(
            ["openscad", "-o", stl_path, scad_path],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            raise HTTPException(
                status_code=422,
                detail=f"OpenSCAD compilation failed: {result.stderr[:500]}",
            )

        if not os.path.exists(stl_path) or os.path.getsize(stl_path) < 84:
            raise HTTPException(
                status_code=422,
                detail="Compiled STL is empty — module may not produce geometry",
            )

        return FileResponse(
            path=stl_path,
            media_type="application/sla",
            filename=f"{module_name}.stl",
        )

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="OpenSCAD compilation timed out")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"compile-part failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
