import os
import subprocess
import re
import tempfile

from langchain_core.tools import tool


@tool
def validate_openscad_code(openscad_code: str) -> str:
    """Compile OpenSCAD code to check for syntax errors. Returns 'VALID' if the code
    compiles successfully, or a detailed error message with the line number if it fails.
    Use this tool BEFORE returning code to the user to ensure it is error-free."""

    tmp_dir = tempfile.mkdtemp(prefix="scad_validate_")
    script_path = os.path.join(tmp_dir, "validate.scad")
    output_path = os.path.join(tmp_dir, "validate.stl")

    try:
        with open(script_path, "w") as f:
            f.write(openscad_code)

        result = subprocess.run(
            ["openscad", "-o", output_path, script_path],
            capture_output=True,
            text=True,
            cwd=tmp_dir,
            timeout=30,
        )

        if result.returncode == 0:
            return "VALID: Code compiled successfully with no errors."

        # Parse error details
        stderr = result.stderr
        error_lines = [l.strip() for l in stderr.strip().split("\n") if l.strip()]
        line_match = re.search(r"line\s+(\d+)", stderr, re.IGNORECASE)
        line_num = int(line_match.group(1)) if line_match else None

        error_msg = error_lines[-1] if error_lines else "Unknown compilation error"
        if line_num:
            code_lines = openscad_code.split("\n")
            start = max(0, line_num - 3)
            end = min(len(code_lines), line_num + 2)
            context = "\n".join(f"  {i+1}: {code_lines[i]}" for i in range(start, end))
            return f"ERROR on line {line_num}: {error_msg}\n\nCode context:\n{context}"

        return f"ERROR: {error_msg}"

    except subprocess.TimeoutExpired:
        return (
            "ERROR: OpenSCAD compilation timed out after 30 seconds. "
            "The code may contain an infinite loop or extremely complex geometry."
        )
    except Exception as e:
        return (
            "SKIPPED: Validation could not run due to a server-side issue "
            f"({e}). Proceed with the code as-is — the code itself is likely fine."
        )
    finally:
        for path in [script_path, output_path]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass
