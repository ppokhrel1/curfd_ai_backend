import logging
import re

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def apply_parameter_changes(
    updates: list[dict],
) -> str:
    """Apply simple parameter updates to the current artifact without re-generating the whole model.
    Use this when the user wants to change specific parameter values (dimensions,
    counts, angles) in existing code. This is faster and preserves existing structure.

    Args:
        updates: List of parameter updates, each with 'name' and 'value' keys.
                 Example: [{"name": "width", "value": "30"}, {"name": "height", "value": "50"}]

    Returns:
        Status message about applied changes.
    """
    # This tool is intercepted by the agent loop which patches the current
    # artifact code. This return value is only a fallback.
    return f"APPLY PARAMETER CHANGES: {updates}"


def patch_code(existing_code: str, updates: list[dict]) -> tuple[str, list[str], list[str]]:
    """Apply parameter patches to OpenSCAD or CadQuery Python code via regex.

    Returns:
        (patched_code, applied_list, not_found_list)
    """
    is_python = "import cadquery" in existing_code or "cq.Workplane" in existing_code
    patched_code = existing_code
    applied = []
    not_found = []

    for update in updates:
        name = update.get("name", "")
        value = update.get("value", "")
        if not name:
            continue

        if is_python:
            # Python-style: name = value  # optional comment
            py_num_pattern = re.compile(
                rf"^(\s*{re.escape(name)}\s*=\s*)([-+]?[0-9]*\.?[0-9]+)(\s*(?:#.*)?)$",
                re.MULTILINE,
            )
            match = py_num_pattern.search(patched_code)
            if match:
                old_val = match.group(2)
                try:
                    coerced = float(value)
                    if coerced == int(coerced):
                        coerced = int(coerced)
                except (ValueError, TypeError):
                    coerced = value
                replacement = f"{match.group(1)}{coerced}{match.group(3)}"
                patched_code = py_num_pattern.sub(replacement, patched_code, count=1)
                applied.append(f"{name}: {old_val} -> {coerced}")
            else:
                not_found.append(name)
        else:
            # OpenSCAD-style: name = value;
            num_pattern = re.compile(
                rf"^(\s*{re.escape(name)}\s*=\s*)([-+]?[0-9]*\.?[0-9]+)(\s*;.*)$",
                re.MULTILINE,
            )
            str_pattern = re.compile(
                rf'^(\s*{re.escape(name)}\s*=\s*)"([^"]*)"(\s*;.*)$',
                re.MULTILINE,
            )
            bool_pattern = re.compile(
                rf"^(\s*{re.escape(name)}\s*=\s*)(true|false)(\s*;.*)$",
                re.MULTILINE,
            )

            matched = False
            for pattern, is_string in [(num_pattern, False), (str_pattern, True), (bool_pattern, False)]:
                match = pattern.search(patched_code)
                if match:
                    old_val = match.group(2)
                    if is_string:
                        coerced = str(value).replace('"', '\\"')
                        replacement = f'{match.group(1)}"{coerced}"{match.group(3)}'
                    else:
                        try:
                            coerced = float(value)
                            if coerced == int(coerced):
                                coerced = int(coerced)
                        except (ValueError, TypeError):
                            coerced = value
                        replacement = f"{match.group(1)}{coerced}{match.group(3)}"

                    patched_code = pattern.sub(replacement, patched_code, count=1)
                    applied.append(f"{name}: {old_val} -> {coerced}")
                    matched = True
                    break

            if not matched:
                not_found.append(name)

    return patched_code, applied, not_found
