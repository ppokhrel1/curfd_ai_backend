from fastapi import APIRouter, BackgroundTasks, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
import logging
import os
import json
import asyncio
from typing import Literal
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Import your custom task manager
from app.core.task_manager import task_manager

# Import the generate_openscad task we just created
from app.cadquery.tasks import generate_openscad

router = APIRouter()

class OpenScadGenerateRequest(BaseModel):
    script: str
    # OpenSCAD supported export formats; GLB triggers multi-part ZIP compilation
    format: Literal["STL", "OFF", "AMF", "3MF", "CSG", "DXF", "SVG", "GLB"] = "STL"

@router.post("/generate")
async def generate_openscad_model(request: OpenScadGenerateRequest):
    """
    Submit an OpenSCAD script for generation via the task manager.
    """
    # Submit to your custom task manager
    task_id = task_manager.submit_task(generate_openscad, request.script, request.format)
    
    return {"task_id": task_id, "status": "processing"}

@router.post("/upload")
async def upload_openscad_script(
    file: UploadFile = File(...),
    output_format: Literal["STL", "OFF", "AMF", "3MF", "CSG", "DXF", "SVG", "GLB"] = Form("STL"),
):
    """
    Upload an OpenSCAD (.scad) script file for generation.
    """
    try:
        content = await file.read()
        try:
            script_content = content.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=400, 
                detail="Invalid file encoding. Please upload a valid UTF-8 text file."
            )
        
        # Submit to your custom task manager
        task_id = task_manager.submit_task(generate_openscad, script_content, output_format)
        
        return {"task_id": task_id, "status": "processing"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@router.websocket("/ws/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    """
    WebSocket endpoint to stream task status updates to the frontend client.
    """
    await websocket.accept()
    try:
        while True:
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
                    # Try to parse the error message as JSON (OpenSCAD task returns line numbers in JSON)
                    error_data = json.loads(error_msg)
                    response["error"] = error_data
                except json.JSONDecodeError:
                    # Fallback for standard string errors
                    response["error"] = error_msg
                    
                await websocket.send_text(json.dumps(response))
                break
            else:
                # Still processing
                await websocket.send_text(json.dumps(response))
                await asyncio.sleep(1) 
                
    except WebSocketDisconnect:
        print(f"Client disconnected for OpenSCAD task {task_id}")

@router.get("/download/{filename}")
async def download_file(filename: str, background_tasks: BackgroundTasks):
    """
    Download a generated OpenSCAD output file and delete it afterward.
    """
    file_path = os.path.join(os.getenv("GENERATED_FILES_DIR", "/app/generated_files"), filename)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    def _cleanup(path: str):
        try:
            if os.path.exists(path):
                os.remove(path)
                logger.info(f"Deleted served file: {path}")
        except OSError as e:
            logger.warning(f"Could not delete served file {path}: {e}")

    background_tasks.add_task(_cleanup, file_path)
    return FileResponse(file_path, filename=filename)