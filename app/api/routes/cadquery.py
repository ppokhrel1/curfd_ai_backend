from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Form, Depends
from fastapi.responses import FileResponse
import os
import hashlib
# from app.core.redis_client import get_redis
# from redis.asyncio import Redis
from typing import Optional
from pydantic import BaseModel
# from celery.result import AsyncResult
from app.cadquery.tasks import generate_cad
# from app.cadquery.celery_app import celery_app
from app.core.task_manager import task_manager
import asyncio
import json

router = APIRouter()

from typing import Literal

class GenerateRequest(BaseModel):
    script: str
    format: Literal["STL", "STEP", "AMF", "3MF", "TJS", "VRML", "VTP", "DXF", "SVG", "GLTF", "GLB"] = "STL"

@router.post("/generate")
async def generate_cad_model(
    request: GenerateRequest,
    # redis: Redis = Depends(get_redis)
):
    """
    Submit a CadQuery script for generation.
    """
    # Create a cache key based on the script content
    # script_hash = hashlib.sha256(request.script.encode('utf-8')).hexdigest()
    # cache_key = f"cad_task:{script_hash}"
    
    # Check if we have a cached task_id
    # cached_task_id = await redis.get(cache_key)
    # if cached_task_id:
    #     return {"task_id": cached_task_id, "status": "processing", "cached": True}

    # task = generate_cad.delay(request.script, request.format)
    task_id = task_manager.submit_task(generate_cad, request.script, request.format)
    
    # Cache the task_id for 10 seconds
    # await redis.setex(cache_key, 10, task.id)
    
    return {"task_id": task_id, "status": "processing"}

@router.post("/upload")
async def upload_cad_script(
    file: UploadFile = File(...),
    output_format: Literal["STL", "STEP", "AMF", "3MF", "TJS", "VRML", "VTP", "DXF", "SVG", "GLTF"] = Form("STL"),
    # redis: Redis = Depends(get_redis)
):
    """
    Upload a CadQuery script file for generation.
    """
    try:
        content = await file.read()
        try:
            script_content = content.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="Invalid file encoding. Please upload a valid UTF-8 text file.")
        
        # Create a cache key based on the script content
        # script_hash = hashlib.sha256(script_content.encode('utf-8')).hexdigest()
        # cache_key = f"cad_task:{script_hash}"
        
        # Check if we have a cached task_id
        # cached_task_id = await redis.get(cache_key)
        # if cached_task_id:
        #    return {"task_id": cached_task_id, "status": "processing", "cached": True}
        
        # task = generate_cad.delay(script_content, output_format)
        task_id = task_manager.submit_task(generate_cad, script_content, output_format)
        
        # Cache the task_id for 10 seconds
        # await redis.setex(cache_key, 10, task.id)
        
        return {"task_id": task_id, "status": "processing"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@router.websocket("/ws/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    await websocket.accept()
    try:
        while True:
            # result = AsyncResult(task_id, app=celery_app)
            # status = result.status
            task_info = task_manager.get_task_status(task_id)
            
            if not task_info:
                 await websocket.send_text(json.dumps({"task_id": task_id, "error": "Task not found"}))
                 break
            
            status = task_info["status"]
            response = {"task_id": task_id, "status": status}
            
            if status == 'SUCCESS':
                response["result"] = task_info["result"]
                await websocket.send_text(json.dumps(response))
                break
            elif status == 'FAILURE':
                error_msg = str(task_info["error"])
                try:
                    # Try to parse the error message as JSON
                    # The task now returns a JSON string for structured errors
                    error_data = json.loads(error_msg)
                    response["error"] = error_data
                except json.JSONDecodeError:
                    # Fallback for other errors
                    response["error"] = error_msg
                    
                await websocket.send_text(json.dumps(response))
                break
            else:
                await websocket.send_text(json.dumps(response))
                await asyncio.sleep(1) 
    except WebSocketDisconnect:
        print(f"Client disconnected for task {task_id}")

@router.get("/download/{filename}")
async def download_file(filename: str):
    """
    Download a generated CAD file.
    """
    file_path = os.path.join("/app/generated_files", filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(file_path, filename=filename)
