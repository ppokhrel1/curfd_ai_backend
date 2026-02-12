import asyncio
import os
import sys
import logging
from unittest.mock import MagicMock, patch
from app.core.task_manager import task_manager
from app.cadquery.tasks import generate_cad, GENERATED_FILES_DIR

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_cad_generation():
    print("Testing CAD generation task (with MOCKS)...")
    
    # Simple valid CadQuery script
    script = """
import cadquery as cq
result = cq.Workplane("XY").box(10, 10, 10)
"""
    
    # We need to mock subprocess.run in app.cadquery.tasks
    # We also need to simulate the file creation that the script would do.
    
    with patch('app.cadquery.tasks.subprocess.run') as mock_run:
        # Define side effect to create the output file
        def side_effect(*args, **kwargs):
            # Extract task_id from the script path in args[0][1]
            # cmd is [sys.executable, script_path]
            cmd = args[0]
            script_path = cmd[1]
            filename = os.path.basename(script_path)
            task_id = filename.replace('.py', '')
            output_filename = f"{task_id}.stl"
            
            # Create dummy output file
            output_path = os.path.join(GENERATED_FILES_DIR, output_filename)
            with open(output_path, 'w') as f:
                f.write("dummy stl content")
                
            return MagicMock(stdout="Mocked execution success", returncode=0)

        mock_run.side_effect = side_effect

        # Submit task
        task_id = task_manager.submit_task(generate_cad, script, "STL")
        print(f"Task submitted with ID: {task_id}")
        
        # Poll for completion
        for _ in range(20): # Wait up to 20 seconds
            status = task_manager.get_task_status(task_id)
            print(f"Task status: {status['status']}")
            
            if status['status'] == 'SUCCESS':
                print(f"Task completed successfully! Result: {status['result']}")
                
                # Verify file exists
                output_file = os.path.join(GENERATED_FILES_DIR, status['result'])
                
                if os.path.exists(output_file):
                    print(f"File {output_file} exists.")
                else:
                     print(f"File {output_file} DOES NOT exist.")
                     
                return
            elif status['status'] == 'FAILURE':
                print(f"Task failed with error: {status['error']}")
                return
                
            await asyncio.sleep(1)
            
        print("Task timed out.")

if __name__ == "__main__":
    # Ensure generated files directory exists
    if not os.path.exists(GENERATED_FILES_DIR):
        try:
             os.makedirs(GENERATED_FILES_DIR)
        except OSError:
             # If permission denied (e.g. /app), use local dir and monkeypatch
             local_dir = "./generated_files_test"
             if not os.path.exists(local_dir):
                 os.makedirs(local_dir)
             
             import app.cadquery.tasks
             app.cadquery.tasks.GENERATED_FILES_DIR = local_dir
             GENERATED_FILES_DIR = local_dir
             
    asyncio.run(test_cad_generation())
