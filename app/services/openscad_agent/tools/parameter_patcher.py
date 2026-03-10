import json as _json
import logging
import re

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def apply_parameter_changes(
    existing_code: str,
    parameter_changes: str,
) -> str:
    """Apply parameter value changes to existing OpenSCAD code WITHOUT regenerating.
    Use this when the user wants to change specific parameter values (dimensions,
    counts, angles) in existing code. This is faster and preserves existing structure.

    Args:
        existing_code: The current complete OpenSCAD code.
        parameter_changes: JSON object of changes, e.g. '{"width": 30, "height": 50}'

    Returns:
        The updated OpenSCAD code with parameters patched, or an error message.
    """
    try:
        changes = _json.loads(parameter_changes)
    except _json.JSONDecodeError:
        return f"ERROR: parameter_changes must be valid JSON. Got: {parameter_changes[:200]}"

    if not isinstance(changes, dict):
        return 'ERROR: parameter_changes must be a JSON object like {"name": value}'

    patched_code = existing_code
    applied = []
    not_found = []

    for param_name, new_value in changes.items():
        pattern = re.compile(
            rf"^(\s*{re.escape(param_name)}\s*=\s*)([-+]?[0-9]*\.?[0-9]+)(\s*;.*)$",
            re.MULTILINE,
        )
        match = pattern.search(patched_code)
        if match:
            replacement = f"{match.group(1)}{new_value}{match.group(3)}"
            patched_code = pattern.sub(replacement, patched_code, count=1)
            applied.append(f"{param_name}: {match.group(2)} -> {new_value}")
        else:
            not_found.append(param_name)

    result_parts = []
    if applied:
        result_parts.append(f"PATCHED {len(applied)} parameter(s): {', '.join(applied)}")
    if not_found:
        result_parts.append(f"NOT FOUND: {', '.join(not_found)}")

    if applied:
        result_parts.append(f"\n---PATCHED_CODE_START---\n{patched_code}\n---PATCHED_CODE_END---")
        return "\n".join(result_parts)

    return "No parameters matched. The user may need a structural code change instead."
