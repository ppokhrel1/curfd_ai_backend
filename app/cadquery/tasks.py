
import os
import uuid
import sys
import subprocess
import logging
import re
import json
import time
import zipfile

# from .celery_app import celery_app

logger = logging.getLogger(__name__)

GENERATED_FILES_DIR = os.getenv("GENERATED_FILES_DIR", "/app/generated_files")
try:
    os.makedirs(GENERATED_FILES_DIR, exist_ok=True)
except OSError:
    logger.warning(f"Could not create directory {GENERATED_FILES_DIR}. Using current directory as fallback.")
    GENERATED_FILES_DIR = os.path.join(os.getcwd(), "generated_files")
    os.makedirs(GENERATED_FILES_DIR, exist_ok=True)

# Modules that are structural wrappers / entry-points, not individual selectable parts
_EXCLUDED_MODULES = {
    'main', 'combined', 'assembly', 'full', 'complete', 'result', 'model',
    'scene', 'object', 'all_parts', 'body_combined', 'total',
}

def _extract_module_names(scad_code: str) -> list:
    """Return user-defined module names from OpenSCAD code, excluding structural wrappers."""
    pattern = r'^\s*module\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\('
    modules = re.findall(pattern, scad_code, re.MULTILINE)
    return [m for m in modules if m.lower() not in _EXCLUDED_MODULES and not m.startswith('_')]


def _run_openscad(script_path: str, output_path: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["openscad", "-o", output_path, script_path],
        capture_output=True,
        text=True,
        cwd=GENERATED_FILES_DIR,
        timeout=60,
    )


def _raise_openscad_error(stderr: str):
    error_info = {"line": None, "message": "OpenSCAD compilation failed."}
    lines = [line.strip() for line in stderr.strip().split('\n') if line.strip()]
    if lines:
        error_info["message"] = lines[-1]
    match = re.search(r'line\s+(\d+)', stderr, re.IGNORECASE)
    if match:
        error_info["line"] = int(match.group(1))
    raise RuntimeError(json.dumps(error_info))


def _generate_multipart_zip(task_id: str, script_content: str) -> str:
    """
    Compile each named module in the SCAD script as a separate STL, then bundle
    them into a ZIP.  The frontend's ModelImporter.importZip() loads each STL
    as a separate named mesh, enabling per-part selection in the viewer.
    """
    modules = _extract_module_names(script_content)
    logger.info(f"[multipart] found modules: {modules}")

    # Strip bare top-level calls (e.g. `main();`) so we can inject our own entry point.
    top_call_re = re.compile(r'^\s*[a-zA-Z_][a-zA-Z0-9_]*\s*\(\s*\)\s*;\s*$', re.MULTILINE)
    base_script = top_call_re.sub('', script_content)

    module_stls: dict = {}

    for module_name in modules:
        scad_path = os.path.join(GENERATED_FILES_DIR, f"{task_id}_{module_name}.scad")
        stl_path = os.path.join(GENERATED_FILES_DIR, f"{task_id}_{module_name}.stl")

        module_script = base_script + f"\n{module_name}();\n"
        with open(scad_path, "w") as f:
            f.write(module_script)

        try:
            result = _run_openscad(scad_path, stl_path)
            # 84 bytes = empty STL header; skip modules that produce no geometry
            if result.returncode == 0 and os.path.exists(stl_path) and os.path.getsize(stl_path) > 84:
                module_stls[module_name] = stl_path
                logger.info(f"[multipart] compiled {module_name}.stl ({os.path.getsize(stl_path)} bytes)")
            else:
                logger.warning(f"[multipart] {module_name} produced no geometry, skipping")
        except subprocess.TimeoutExpired:
            logger.warning(f"[multipart] {module_name} timed out, skipping")
        except Exception as e:
            logger.warning(f"[multipart] {module_name} failed: {e}")
        finally:
            if os.path.exists(scad_path):
                os.remove(scad_path)

    # Fall back to full model if no individual modules produced geometry
    if not module_stls:
        logger.info("[multipart] no modules compiled; falling back to full model STL")
        fallback_scad = os.path.join(GENERATED_FILES_DIR, f"{task_id}.scad")
        fallback_stl = os.path.join(GENERATED_FILES_DIR, f"{task_id}.stl")
        with open(fallback_scad, "w") as f:
            f.write(script_content)
        try:
            result = _run_openscad(fallback_scad, fallback_stl)
            if result.returncode == 0 and os.path.exists(fallback_stl):
                module_stls["model"] = fallback_stl
            else:
                _raise_openscad_error(result.stderr)
        except subprocess.TimeoutExpired:
            raise RuntimeError(json.dumps({"line": None, "message": "OpenSCAD timed out."}))
        finally:
            if os.path.exists(fallback_scad):
                os.remove(fallback_scad)

    # Bundle into ZIP
    zip_filename = f"{task_id}.zip"
    zip_path = os.path.join(GENERATED_FILES_DIR, zip_filename)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for module_name, stl_path in module_stls.items():
            zf.write(stl_path, f"{module_name}.stl")

    for stl_path in module_stls.values():
        try:
            os.remove(stl_path)
        except OSError:
            pass

    logger.info(f"[multipart] created ZIP with {len(module_stls)} part(s): {zip_filename}")
    return zip_filename


# @celery_app.task(bind=True)
def generate_openscad(task_id: str, script_content: str, output_format: str = "STL"):
    """
    Generates a 3D model file from the provided OpenSCAD script content.

    When output_format is "GLB", each named module is compiled separately and
    bundled into a ZIP so the frontend can display individually selectable parts.
    """
    logger.info(f"Starting OpenSCAD generation for task {task_id} (format={output_format})")

    fmt = output_format.upper()

    if fmt == "GLB":
        return _generate_multipart_zip(task_id, script_content)

    # Standard single-file compilation
    script_filename = f"{task_id}.scad"
    script_path = os.path.join(GENERATED_FILES_DIR, script_filename)
    output_filename = f"{task_id}.{fmt.lower()}"
    output_path = os.path.join(GENERATED_FILES_DIR, output_filename)

    with open(script_path, "w") as f:
        f.write(script_content)

    try:
        result = _run_openscad(script_path, output_path)
        if result.returncode != 0:
            _raise_openscad_error(result.stderr)
        logger.info(f"OpenSCAD output: {result.stdout}")
    except subprocess.TimeoutExpired:
        raise RuntimeError(json.dumps({"line": None, "message": "OpenSCAD timed out."}))
    finally:
        if os.path.exists(script_path):
            os.remove(script_path)

    if not os.path.exists(output_path):
        raise RuntimeError("Output file was not created by OpenSCAD.")

    return output_filename

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
    
    wrapper_template = """
import cadquery as cq
import sys
import os

# User script content
{user_script}

# Export logic 
if 'result' in locals():
    try:
        res = locals()['result']
        out_path = r'{out_path}'
        fmt = '{fmt}'.upper()
        
        # 2.6.1 logic: GLTF/GLB requires an Assembly context
        if fmt in ['GLTF', 'GLB']:
            if not isinstance(res, cq.Assembly):
                res = cq.Assembly(res, name="Part")
            # Assembly.save handles the GLTF tessellation internally
            res.save(out_path, exportType=fmt)
        else:
            # STL/STEP can be handled by standard exporters or Assembly
            if isinstance(res, cq.Assembly):
                res.save(out_path, exportType=fmt)
            else:
                cq.exporters.export(res, out_path, exportType=fmt)
            
        if os.path.exists(out_path):
            print(f"Successfully exported to {{out_path}}")
        else:
            sys.exit(1)
            
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
else:
    print("No 'result' variable found in script.", file=sys.stderr)
    sys.exit(1)
"""

    wrapper_code = wrapper_template.format(
        user_script=script_content,
        out_path=output_path,
        fmt=output_format.upper()
    )
    logger.info(f"Generated wrapper code for task {task_id}:\n{wrapper_code}")
    
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
        if os.path.exists(script_path):
            os.remove(script_path)

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
