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

async def test_formats():
    print("Testing CAD generation with multiple formats (MOCKS)...")
    
    script = """
import cadquery as cq
result = cq.Workplane("XY").box(10, 10, 10)
"""
    
    formats_to_test = ["STL", "STEP", "AMF", "3MF", "TJS", "VRML", "VTP", "DXF", "SVG"]
    
    with patch('app.cadquery.tasks.subprocess.run') as mock_run:
        def side_effect(*args, **kwargs):
            cmd = args[0]
            script_path = cmd[1]
            filename = os.path.basename(script_path)
            task_id = filename.replace('.py', '')
            
            # Identify format from the expected output file or passed args?
            # Actually, the task determines filename based on 'output_format' passed to generate_cad
            # But here we are mocking subprocess.run which is called INSIDE generate_cad.
            # We can't easily know format inside subprocess.run mock unless we inspect how generating code behaves?
            # Creating dummy file based on what the task EXPECTS to exist.
            # The task checks: if not os.path.exists(output_path): raise RuntimeError
            # So we need to create the file at output_path.
            # We can infer format by looking at recently created files in GENERATED_FILES_DIR matching task_id?
            # Or better: just create a file with ALL tested extensions for this task_id to be safe?
            # No, 'output_path' is local variable in generate_cad.
            
            # Wait, `generate_cad` calculates `output_filename` based on `output_format`.
            # We need to know what filename `generate_cad` expects.
            # But the mock doesn't get `output_format`.
            
            # HACK: Iterate over all possible extensions and create a dummy file for each matching task_id.
            for fmt in formats_to_test:
                 fname = f"{task_id}.{fmt.lower()}"
                 fpath = os.path.join(GENERATED_FILES_DIR, fname)
                 with open(fpath, 'w') as f:
                     f.write(f"dummy content for {fmt}")
            
            return MagicMock(stdout="Mocked execution success", returncode=0)

        mock_run.side_effect = side_effect

        for fmt in formats_to_test:
            print(f"Testing format: {fmt}")
            task_id = task_manager.submit_task(generate_cad, script, fmt)
            
            # Poll for completion
            success = False
            for _ in range(10):
                status = task_manager.get_task_status(task_id)
                if status['status'] == 'SUCCESS':
                    print(f"  SUCCESS: {status['result']}")
                    success = True
                    break
                elif status['status'] == 'FAILURE':
                    print(f"  FAILURE: {status['error']}")
                    break
                await asyncio.sleep(0.1)
            
            if not success:
                print(f"  TIMEOUT for {fmt}")

if __name__ == "__main__":
    GENERATED_FILES_DIR = os.getenv("GENERATED_FILES_DIR", "/app/generated_files")
    # Ensure generated files directory exists (local fallback)
    if not os.path.exists(GENERATED_FILES_DIR):
         try:
             os.makedirs(GENERATED_FILES_DIR)
         except OSError:
             local_dir = "./generated_files_test"
             if not os.path.exists(local_dir):
                 os.makedirs(local_dir)
             import app.cadquery.tasks
             app.cadquery.tasks.GENERATED_FILES_DIR = local_dir
             GENERATED_FILES_DIR = local_dir

    asyncio.run(test_formats())
