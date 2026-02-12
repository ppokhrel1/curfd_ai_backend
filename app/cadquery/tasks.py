
import os
import uuid
import sys
import subprocess
import logging
import re
import json
import time

# from .celery_app import celery_app

logger = logging.getLogger(__name__)

GENERATED_FILES_DIR = os.getenv("GENERATED_FILES_DIR", "/app/generated_files")
try:
    os.makedirs(GENERATED_FILES_DIR, exist_ok=True)
except OSError:
    logger.warning(f"Could not create directory {GENERATED_FILES_DIR}. Using current directory as fallback.")
    GENERATED_FILES_DIR = os.path.join(os.getcwd(), "generated_files")
    os.makedirs(GENERATED_FILES_DIR, exist_ok=True)

# @celery_app.task(bind=True)
def generate_cad(task_id: str, script_content: str, output_format: str = "STL"):
    """
    Generates a CAD file from the provided script content.
    """
    # task_id = self.request.id
    logger.info(f"Starting CAD generation for task {task_id}")

    # Create a temporary python file for the script
    script_filename = f"{task_id}.py"
    script_path = os.path.join(GENERATED_FILES_DIR, script_filename)
    
    output_filename = f"{task_id}.{output_format.lower()}"
    output_path = os.path.join(GENERATED_FILES_DIR, output_filename)

    # Wrap the user script to export the result
    # We assume the user script defines a 'result' variable or follows a convention.
    # For now, let's assume the user script is a complete CadQuery script 
    # that we might need to append an export command to, OR we act as a runner.
    
    # A safer/more flexible approach for 'cadquery' app:
    # We can inject code to export the first object found in 'show_object' or similar if we were parsing.
    # But a common pattern is `cq.exporters.export(result, 'file.stl')`.
    
    # Let's try to append a standard export if it's not present, 
    # OR better: run it and look for a specific variable name like `result`.
    
    wrapper_code = f"""
import cadquery as cq
import sys

# User script content
{script_content}

# Export logic (appended)
if 'result' in locals():
    cq.exporters.export(result, '{output_path}')
    print(f"Exported to {{'{output_path}'}}")
else:
    print("No 'result' variable found in script.", file=sys.stderr)
    sys.exit(1)
"""
    
    with open(script_path, "w") as f:
        f.write(wrapper_code)

    # Execute the script using the CadQuery venv interpreter if available
    cad_venv_python = "/app/.venv-cad/bin/python"
    python_executable = cad_venv_python if os.path.exists(cad_venv_python) else sys.executable
    
    try:
        result = subprocess.run(
            [python_executable, script_path], 
            capture_output=True, 
            text=True, 
            cwd=GENERATED_FILES_DIR,
            check=True
        )
        logger.info(f"Script execution output with {python_executable}: {result.stdout}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Script execution failed: {e.stderr}")
        
        # Parse traceback to find line number in the script
        # The script_path will appear in the traceback
        # File "...", line X, in <module>
        
        # Default error info
        error_info = {"line": None, "message": e.stderr}
        
        # Try to extract the last error message
        lines = e.stderr.strip().split('\n')
        if lines:
            error_info["message"] = lines[-1]
            
        # Try to find line number
        # We look for the LAST occurrence of the script file in the traceback
        # because that's where the error actually happened in the user code.
        pattern = re.compile(f'File "{re.escape(script_path)}", line (\\d+)')
        matches = pattern.findall(e.stderr)
        
        if matches:
            # Get the last match
            line_num = int(matches[-1])
            # Adjust for wrapper code offset
            # The user code starts at line 6 in wrapper_code
            # So user_line = line_num - 5
            # Ensure it's at least 1
            user_line = max(1, line_num - 5)
            error_info["line"] = user_line
            
        raise RuntimeError(json.dumps(error_info))
    finally:
        # Cleanup script? Maybe keep for debugging for now.
        pass

    if not os.path.exists(output_path):
         raise RuntimeError("Output file was not created.")

    return output_filename
    return output_filename

# @celery_app.task
def prune_generated_files():
    """
    Deletes files in GENERATED_FILES_DIR that are older than 5 hours.
    """
    logger.info("Starting file pruning...")
    cutoff_time = time.time() - (5 * 3600) # 5 hours ago
    deleted_count = 0
    
    try:
        if not os.path.exists(GENERATED_FILES_DIR):
            logger.info("Generated files directory does not exist.")
            return

        for filename in os.listdir(GENERATED_FILES_DIR):
            file_path = os.path.join(GENERATED_FILES_DIR, filename)
            if os.path.isfile(file_path):
                file_mtime = os.path.getmtime(file_path)
                if file_mtime < cutoff_time:
                    try:
                        os.remove(file_path)
                        deleted_count += 1
                        logger.info(f"Deleted old file: {filename}")
                    except OSError as e:
                        logger.error(f"Error deleting file {filename}: {e}")
        
        logger.info(f"Pruning complete. Deleted {deleted_count} files.")
    except Exception as e:
        logger.error(f"Error during file pruning: {e}")
