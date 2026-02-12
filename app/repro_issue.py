
import asyncio
import httpx
import websockets
import json
import sys
import os

API_URL = "http://localhost:8000/api/v1/cadquery/upload"
WS_URL = "ws://localhost:8000/api/v1/cadquery/ws"
FILES = [
    "/code/app/sample_files/errors/drone_20931580/assembly.py",
    "/code/app/sample_files/errors/drone_810f790e/assembly.py",
    # "/code/app/sample_files/errors/drone_810f790e/assembly.py",
]

async def verify(FILE_PATH):
    if not os.path.exists(FILE_PATH):
        print(f"File not found: {FILE_PATH}")
        return

    print(f"Uploading {FILE_PATH}...")
    
    async with httpx.AsyncClient() as client:
        with open(FILE_PATH, "rb") as f:
            files = {"file": ("error_assembly.py", f, "text/x-python")}
            data = {"output_format": "STL"}
            response = await client.post(API_URL, files=files, data=data, timeout=30.0)
        
    if response.status_code != 200:
        print(f"Error: {response.status_code} - {response.text}")
        sys.exit(1)
        
    data = response.json()
    task_id = data["task_id"]
    cached = data.get("cached", False)
    print(f"Task submitted. ID: {task_id} (Cached: {cached})")
    
    ws_endpoint = f"{WS_URL}/{task_id}"
    print(f"Connecting to WebSocket: {ws_endpoint}")
    
    try:
        async with websockets.connect(ws_endpoint) as websocket:
            while True:
                message = await websocket.recv()
                data = json.loads(message)
                # print(f"Received: {data}")
                
                if data["status"] == "SUCCESS":
                    print("Status: SUCCESS")
                    print(f"Result: {data.get('result')}")
                    break
                elif data["status"] == "FAILURE":
                    print("Status: FAILURE")
                    print(f"Error: {json.dumps(data.get('error'), indent=2)}")
                    break
                    
    except Exception as e:
        print(f"WebSocket error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    for file_path in FILES:
        asyncio.run(verify(file_path))
