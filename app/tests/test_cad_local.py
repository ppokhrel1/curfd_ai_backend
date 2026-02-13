import asyncio
import os
import sys
import logging
from app.core.task_manager import task_manager
from app.cadquery.tasks import generate_cad, GENERATED_FILES_DIR

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# List of formats to test
FORMATS = ["STL", "STEP", "AMF", "3MF", "TJS", "VRML", "VTP", "DXF", "SVG", "GLTF"]

async def test_cad_generation_samples():
    print("Testing CAD generation with sample files and all formats (REAL GENERATION)...")
    
    # Path to sample files directory
    # If running from app root, it should be app/sample_files
    # But let's check relative to this script just in case or use absolute path if needed
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Assuming this script is in app/tests/, base_dir is app/
    # But we want project root usually.
    # Let's try project root first.
    project_root = os.getcwd()
    sample_dir = os.path.join(project_root, "app", "sample_files")
    
    if not os.path.exists(sample_dir):
        print(f"Sample files directory '{sample_dir}' not found. Trying local 'sample_files'...")
        sample_dir = "sample_files"
        if not os.path.exists(sample_dir):
             print(f"Sample files directory '{sample_dir}' not found.")
             return

    print(f"Scanning for scripts in: {sample_dir}")

    # Find sample scripts recursively
    sample_scripts = []
    for root, dirs, files in os.walk(sample_dir):
        for file in files:
            if file.endswith('.py'):
                sample_scripts.append(os.path.join(root, file))

    if not sample_scripts:
        print(f"No python scripts found in '{sample_dir}'.")
        return

    # Open log.txt for appending
    log_file_path = "log.txt"
    
    with open(log_file_path, "a") as log_file:
        log_file.write(f"\n--- Test Run: {len(sample_scripts)} scripts x {len(FORMATS)} formats ---\n")
        log_file.write(f"GENERATED_FILES_DIR: {GENERATED_FILES_DIR}\n")

        for script_path in sample_scripts:
            script_name = os.path.basename(script_path)
            rel_path = os.path.relpath(script_path, sample_dir)
            
            with open(script_path, 'r') as f:
                script_content = f.read()

            print(f"Processing script: {rel_path} ({script_name})")
            
            for fmt in FORMATS:
                print(f"  Format: {fmt} ...", end=" ", flush=True)
                
                # To execute DIFFERENT variations, we could modify the script content?
                # The prompt said "Generate different variations for different files".
                # Maybe just generating different formats IS the variation.
                # Or maybe modify parameters if possible? 
                # For now, let's stick to generating different formats from the same script.
                
                try:
                    task_id = task_manager.submit_task(generate_cad, script_content, fmt)
                    
                    # Poll for completion
                    success = False
                    result_file = None
                    # Wait longer for real generation (e.g., 30 seconds)
                    for _ in range(60): 
                        status = task_manager.get_task_status(task_id)
                        if status['status'] == 'SUCCESS':
                            result_file = status['result']
                            success = True
                            break
                        elif status['status'] == 'FAILURE':
                            print(f"FAILED: {status['error']}")
                            log_file.write(f"Script: {rel_path} | Format: {fmt} | Status: FAILURE | Error: {status['error']}\n")
                            break
                        await asyncio.sleep(0.5)
                    
                    if success:
                        print(f"SUCCESS -> {result_file}")
                        full_path = os.path.join(GENERATED_FILES_DIR, result_file)
                        log_file.write(f"Script: {rel_path} | Format: {fmt} | Generated: {full_path}\n")
                    elif not result_file and status['status'] != 'FAILURE':
                        print("TIMEOUT")
                        log_file.write(f"Script: {rel_path} | Format: {fmt} | Status: TIMEOUT\n")

                except Exception as e:
                    print(f"ERROR submitting: {e}")
                    log_file.write(f"Script: {rel_path} | Format: {fmt} | Status: EXCEPTION | Error: {e}\n")

if __name__ == "__main__":
    # Ensure generated files directory exists for test environment
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
             
    asyncio.run(test_cad_generation_samples())
