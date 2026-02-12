
import asyncio
import httpx
import websockets
import json
import sys

API_URL = "http://localhost:8000/api/v1/cadquery/generate"
WS_URL = "ws://localhost:8000/api/v1/cadquery/ws"

SCRIPT = """
import cadquery as cq
result = cq.Workplane("XY").box(10, 10, 10)
"""

async def verify():
    print(f"Sending request to {API_URL}...")
    async with httpx.AsyncClient() as client:
        response = await client.post(API_URL, json={"script": SCRIPT, "format": "STL"}, timeout=10.0)
        
    if response.status_code != 200:
        print(f"Error: {response.status_code} - {response.text}")
        sys.exit(1)
        
    data = response.json()
    task_id = data["task_id"]
    print(f"Task submitted. ID: {task_id}")
    
    ws_endpoint = f"{WS_URL}/{task_id}"
    print(f"Connecting to WebSocket: {ws_endpoint}")
    
    try:
        async with websockets.connect(ws_endpoint) as websocket:
            print("Connected to WebSocket.")
            while True:
                message = await websocket.recv()
                print(f"Received: {message}")
                data = json.loads(message)
                
                if data["status"] == "SUCCESS":
                    print("Generation SUCCESS!")
                    print(f"Result file: {data.get('result')}")
                    break
                elif data["status"] == "FAILURE":
                    print(f"Generation FAILED: {data.get('error')}")
                    sys.exit(1)
                    
    except Exception as e:
        print(f"WebSocket error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(verify())
