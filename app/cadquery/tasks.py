
import os
import uuid
import sys
import subprocess
import logging
import re
import json
import time
import hashlib
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

# from .celery_app import celery_app

logger = logging.getLogger(__name__)

GENERATED_FILES_DIR = os.getenv("GENERATED_FILES_DIR", "/app/generated_files")
try:
    os.makedirs(GENERATED_FILES_DIR, exist_ok=True)
except OSError:
    logger.warning(f"Could not create directory {GENERATED_FILES_DIR}. Using current directory as fallback.")
    GENERATED_FILES_DIR = os.path.join(os.getcwd(), "generated_files")
    os.makedirs(GENERATED_FILES_DIR, exist_ok=True)

# ── Cache directory ──
_CACHE_DIR = os.path.join(GENERATED_FILES_DIR, ".cache")
os.makedirs(_CACHE_DIR, exist_ok=True)
_CACHE_MAX_AGE = 3600 * 24  # 24 hours

# Modules that are structural wrappers / entry-points, not individual selectable parts
_EXCLUDED_MODULES = {
    'main', 'combined', 'assembly', 'full', 'complete', 'result', 'model',
    'scene', 'object', 'all_parts', 'body_combined', 'total',
}

# ── Detect Manifold support once at import time ──
_OPENSCAD_EXTRA_FLAGS: list[str] = []

def _detect_manifold():
    try:
        result = subprocess.run(
            ["openscad", "--help"],
            capture_output=True, text=True, timeout=5,
        )
        combined = result.stdout + result.stderr
        if "manifold" in combined.lower():
            return ["--enable=manifold"]
    except Exception:
        pass
    return []

_OPENSCAD_EXTRA_FLAGS = _detect_manifold()
if _OPENSCAD_EXTRA_FLAGS:
    logger.info(f"OpenSCAD Manifold backend enabled: {_OPENSCAD_EXTRA_FLAGS}")
else:
    logger.info("OpenSCAD using default CGAL backend")


_FENCE_RE = re.compile(r"^```[\w]*\n?|```\s*$", re.MULTILINE)
_LANG_TAG_RE = re.compile(r"^\s*openscad\s*\n", re.IGNORECASE)


def _clean_scad(code: str) -> str:
    """Strip markdown fences and bare language tags that break OpenSCAD parsing.
    Also ensures main() is called if a main module is defined."""
    code = _FENCE_RE.sub("", code)
    code = _LANG_TAG_RE.sub("", code)
    code = code.strip()

    # If a main module is defined but never called, append the call
    if re.search(r'^\s*module\s+main\s*\(', code, re.MULTILINE):
        if not re.search(r'^\s*main\s*\(\s*\)\s*;', code, re.MULTILINE):
            code += "\nmain();\n"
            logger.info("[clean_scad] appended missing main(); call")

    return code


def _extract_module_names(scad_code: str) -> list:
    """Return user-defined module names from OpenSCAD code, excluding structural wrappers
    and parameterized helper modules (which can't be compiled standalone)."""
    pattern = r'^\s*module\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(([^)]*)\)'
    results = []
    for name, params in re.findall(pattern, scad_code, re.MULTILINE):
        if name.lower() in _EXCLUDED_MODULES or name.startswith('_'):
            continue
        # Skip modules that take parameters — they're helpers, not standalone parts
        if params.strip():
            continue
        results.append(name)
    return results


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _cache_get(key: str, ext: str) -> str | None:
    """Return cached file path if it exists and isn't stale."""
    path = os.path.join(_CACHE_DIR, f"{key}.{ext}")
    if os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        if age < _CACHE_MAX_AGE:
            return path
        os.remove(path)
    return None


def _cache_put(key: str, ext: str, src_path: str) -> str:
    """Copy a file into the cache and return the cache path."""
    dst = os.path.join(_CACHE_DIR, f"{key}.{ext}")
    import shutil
    shutil.copy2(src_path, dst)
    return dst


def _run_openscad(script_path: str, output_path: str) -> subprocess.CompletedProcess:
    cmd = ["openscad"] + _OPENSCAD_EXTRA_FLAGS + ["-o", output_path, script_path]
    return subprocess.run(
        cmd,
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


def _compile_single_module(
    task_id: str, base_script: str, module_name: str
) -> tuple[str, str | None]:
    """Compile one module to STL. Returns (module_name, stl_path_or_None).

    Uses content-hash cache to skip recompilation of identical modules.
    """
    module_script = base_script + f"\n{module_name}();\n"
    cache_key = _content_hash(module_script)

    # Check cache first
    cached = _cache_get(cache_key, "stl")
    if cached:
        # Copy cached file to task-specific path for ZIP bundling
        stl_path = os.path.join(GENERATED_FILES_DIR, f"{task_id}_{module_name}.stl")
        import shutil
        shutil.copy2(cached, stl_path)
        logger.info(f"[multipart] cache hit for {module_name}")
        return (module_name, stl_path)

    scad_path = os.path.join(GENERATED_FILES_DIR, f"{task_id}_{module_name}.scad")
    stl_path = os.path.join(GENERATED_FILES_DIR, f"{task_id}_{module_name}.stl")

    with open(scad_path, "w") as f:
        f.write(module_script)

    logger.debug(
        f"[multipart] {module_name} script first 15 lines:\n"
        + "\n".join(f"  {i+1}: {l}" for i, l in enumerate(module_script.split("\n")[:15]))
    )

    try:
        result = _run_openscad(scad_path, stl_path)
        # 84 bytes = empty STL header; skip modules that produce no geometry
        if result.returncode == 0 and os.path.exists(stl_path) and os.path.getsize(stl_path) > 84:
            _cache_put(cache_key, "stl", stl_path)
            logger.info(f"[multipart] compiled {module_name}.stl ({os.path.getsize(stl_path)} bytes)")
            return (module_name, stl_path)
        else:
            size = os.path.getsize(stl_path) if os.path.exists(stl_path) else 0
            logger.warning(
                f"[multipart] {module_name} produced no geometry "
                f"(rc={result.returncode}, size={size}B). "
                f"stderr: {result.stderr[:300] if result.stderr else 'none'}"
            )
            return (module_name, None)
    except subprocess.TimeoutExpired:
        logger.warning(f"[multipart] {module_name} timed out, skipping")
        return (module_name, None)
    except Exception as e:
        logger.warning(f"[multipart] {module_name} failed: {e}")
        return (module_name, None)
    finally:
        if os.path.exists(scad_path):
            os.remove(scad_path)


def _strip_toplevel_calls(scad_code: str) -> str:
    """Remove only top-level (brace-depth 0) bare function calls like `main();`.

    Calls inside module bodies are preserved so modules remain valid.
    """
    call_re = re.compile(r'^\s*[a-zA-Z_][a-zA-Z0-9_]*\s*\(\s*\)\s*;\s*$')
    lines = scad_code.split('\n')
    depth = 0
    out = []
    for line in lines:
        # Track brace depth (simple char count — good enough for generated code)
        for ch in line:
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth = max(0, depth - 1)
        # Only blank-out calls at depth 0 (top-level)
        if depth == 0 and call_re.match(line):
            out.append('')  # blank the line, preserving line count
        else:
            out.append(line)
    return '\n'.join(out)


def _generate_multipart_zip(task_id: str, script_content: str) -> str:
    """
    Compile each named module in the SCAD script as a separate STL, then bundle
    them into a ZIP.  The frontend's ModelImporter.importZip() loads each STL
    as a separate named mesh, enabling per-part selection in the viewer.

    Modules are compiled in parallel for speed.
    """
    modules = _extract_module_names(script_content)
    logger.info(f"[multipart] found modules: {modules}")

    # Strip bare top-level calls (e.g. `main();`) so we can inject our own entry point.
    # Only strips at brace-depth 0 — calls inside module bodies are preserved.
    base_script = _strip_toplevel_calls(script_content)

    module_stls: dict = {}

    if modules:
        # Compile all modules in parallel (up to 4 concurrent OpenSCAD processes)
        max_workers = min(len(modules), 2)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_compile_single_module, task_id, base_script, mod): mod
                for mod in modules
            }
            for future in as_completed(futures):
                module_name, stl_path = future.result()
                if stl_path:
                    module_stls[module_name] = stl_path

    # Fall back to full model if no individual modules produced geometry
    if not module_stls:
        logger.warning(
            "[multipart] no modules compiled; falling back to full model STL. "
            f"Code first 20 lines:\n"
            + "\n".join(f"  {i+1}: {l}" for i, l in enumerate(script_content.split("\n")[:20]))
        )

        cache_key = _content_hash(script_content)
        cached = _cache_get(cache_key, "stl")
        if cached:
            import shutil
            fallback_stl = os.path.join(GENERATED_FILES_DIR, f"{task_id}.stl")
            shutil.copy2(cached, fallback_stl)
            module_stls["model"] = fallback_stl
            logger.info("[multipart] cache hit for full model fallback")
        else:
            fallback_scad = os.path.join(GENERATED_FILES_DIR, f"{task_id}.scad")
            fallback_stl = os.path.join(GENERATED_FILES_DIR, f"{task_id}.stl")
            with open(fallback_scad, "w") as f:
                f.write(script_content)
            try:
                result = _run_openscad(fallback_scad, fallback_stl)
                if (
                    result.returncode == 0
                    and os.path.exists(fallback_stl)
                    and os.path.getsize(fallback_stl) > 84
                ):
                    module_stls["model"] = fallback_stl
                    _cache_put(cache_key, "stl", fallback_stl)
                else:
                    logger.error(
                        f"[multipart] full model fallback failed. "
                        f"rc={result.returncode}, stderr={result.stderr[:500]}"
                    )
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
def _convert_stl_to_step(stl_path: str, step_path: str) -> None:
    """Convert an STL mesh to STEP solid using CadQuery/OCC."""
    import sys
    cad_venv_python = "/app/.venv-cad/bin/python"
    python_executable = cad_venv_python if os.path.exists(cad_venv_python) else sys.executable

    convert_script = f"""
import cadquery as cq
result = cq.importers.importMesh("{stl_path}")
cq.exporters.export(result, "{step_path}", exportType="STEP")
"""
    script_path = stl_path + ".convert.py"
    with open(script_path, "w") as f:
        f.write(convert_script)
    try:
        result = subprocess.run(
            [python_executable, script_path],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            logger.error(f"STL→STEP conversion failed: {result.stderr[:500]}")
            raise RuntimeError(f"STEP conversion failed: {result.stderr[:300]}")
    finally:
        if os.path.exists(script_path):
            os.remove(script_path)


def _convert_step_to_glb(step_path: str, glb_path: str) -> None:
    """Convert a STEP file to GLB using CadQuery/OCC."""
    import sys
    cad_venv_python = "/app/.venv-cad/bin/python"
    python_executable = cad_venv_python if os.path.exists(cad_venv_python) else sys.executable

    convert_script = f"""
import cadquery as cq
result = cq.importers.importStep("{step_path}")
assy = cq.Assembly(result, name="Part")
assy.save("{glb_path}", exportType="GLB")
"""
    script_path = step_path + ".convert.py"
    with open(script_path, "w") as f:
        f.write(convert_script)
    try:
        result = subprocess.run(
            [python_executable, script_path],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            logger.error(f"STEP→GLB conversion failed: {result.stderr[:500]}")
            raise RuntimeError(f"STEP→GLB conversion failed: {result.stderr[:300]}")
    finally:
        if os.path.exists(script_path):
            os.remove(script_path)


def convert_step_file(task_id: str, step_path: str, output_format: str = "GLB") -> str:
    """Convert an uploaded STEP file to a display format (GLB/STL)."""
    logger.info(f"Converting STEP file for task {task_id} to {output_format}")
    fmt = output_format.upper()
    output_filename = f"{task_id}.{fmt.lower()}"
    output_path = os.path.join(GENERATED_FILES_DIR, output_filename)

    if fmt == "GLB":
        _convert_step_to_glb(step_path, output_path)
    elif fmt == "STL":
        import sys
        cad_venv_python = "/app/.venv-cad/bin/python"
        python_executable = cad_venv_python if os.path.exists(cad_venv_python) else sys.executable

        convert_script = f"""
import cadquery as cq
result = cq.importers.importStep("{step_path}")
cq.exporters.export(result, "{output_path}", exportType="STL")
"""
        script_file = step_path + ".convert.py"
        with open(script_file, "w") as f:
            f.write(convert_script)
        try:
            result = subprocess.run(
                [python_executable, script_file],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                raise RuntimeError(f"STEP→STL conversion failed: {result.stderr[:300]}")
        finally:
            if os.path.exists(script_file):
                os.remove(script_file)
    else:
        raise ValueError(f"Unsupported output format for STEP conversion: {fmt}")

    # Clean up the uploaded STEP file
    try:
        if os.path.exists(step_path):
            os.remove(step_path)
    except OSError:
        pass

    if not os.path.exists(output_path):
        raise RuntimeError("STEP conversion produced no output file.")

    return output_filename


def generate_openscad(task_id: str, script_content: str, output_format: str = "STL"):
    """
    Generates a 3D model file from the provided OpenSCAD script content.

    When output_format is "GLB", each named module is compiled separately and
    bundled into a ZIP so the frontend can display individually selectable parts.
    When output_format is "STEP", compiles to STL first then converts via CadQuery.
    """
    logger.info(f"Starting OpenSCAD generation for task {task_id} (format={output_format})")

    # Strip markdown fences / language tags that LLMs sometimes inject
    script_content = _clean_scad(script_content)

    fmt = output_format.upper()

    if fmt == "GLB":
        return _generate_multipart_zip(task_id, script_content)

    # STEP: compile to STL first, then convert
    if fmt == "STEP":
        cache_key = _content_hash(script_content + "STEP")
        cached = _cache_get(cache_key, "step")
        if cached:
            import shutil
            output_filename = f"{task_id}.step"
            output_path = os.path.join(GENERATED_FILES_DIR, output_filename)
            shutil.copy2(cached, output_path)
            logger.info(f"Cache hit for STEP task {task_id}")
            return output_filename

        # First compile to STL
        stl_filename = generate_openscad(task_id, script_content, "STL")
        stl_path = os.path.join(GENERATED_FILES_DIR, stl_filename)
        step_filename = f"{task_id}.step"
        step_path = os.path.join(GENERATED_FILES_DIR, step_filename)
        try:
            _convert_stl_to_step(stl_path, step_path)
        finally:
            # Clean up intermediate STL
            if os.path.exists(stl_path):
                os.remove(stl_path)
        if not os.path.exists(step_path):
            raise RuntimeError("STEP conversion produced no output file.")
        _cache_put(cache_key, "step", step_path)
        return step_filename

    # Check cache for single-file compilation
    cache_key = _content_hash(script_content + fmt)
    cached = _cache_get(cache_key, fmt.lower())
    if cached:
        import shutil
        output_filename = f"{task_id}.{fmt.lower()}"
        output_path = os.path.join(GENERATED_FILES_DIR, output_filename)
        shutil.copy2(cached, output_path)
        logger.info(f"Cache hit for task {task_id}")
        return output_filename

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

    _cache_put(cache_key, fmt.lower(), output_path)
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

# @celery_app.task
def prune_generated_files():
    """
    Deletes files in GENERATED_FILES_DIR that are older than 5 hours.
    Also prunes stale cache entries.
    """
    logger.info("Starting file pruning...")
    cutoff_time = time.time() - (5 * 3600) # 5 hours ago
    cache_cutoff = time.time() - _CACHE_MAX_AGE
    deleted_count = 0

    try:
        # Prune generated files
        if os.path.exists(GENERATED_FILES_DIR):
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

        # Prune stale cache
        if os.path.exists(_CACHE_DIR):
            for filename in os.listdir(_CACHE_DIR):
                file_path = os.path.join(_CACHE_DIR, filename)
                if os.path.isfile(file_path):
                    if os.path.getmtime(file_path) < cache_cutoff:
                        try:
                            os.remove(file_path)
                            deleted_count += 1
                        except OSError:
                            pass

        logger.info(f"Pruning complete. Deleted {deleted_count} files.")
    except Exception as e:
        logger.error(f"Error during file pruning: {e}")
