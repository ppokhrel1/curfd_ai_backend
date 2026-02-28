import re

from langchain_core.tools import tool


@tool
def analyze_openscad_parameters(openscad_code: str) -> str:
    """Analyze OpenSCAD code to extract top-level parameters (variables defined before
    any module). Returns a list of found parameters with their default values and
    suggested min/max ranges. Use this to verify that parameters are correctly defined
    and have physically meaningful ranges."""

    lines = openscad_code.split("\n")
    parameters = []

    var_pattern = re.compile(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*([^;]+);")

    for line in lines:
        stripped = line.strip()

        # Stop at first module/function definition
        if re.match(r"^\s*(module|function)\s+", stripped):
            break

        if stripped.startswith("//") or stripped.startswith("/*"):
            continue

        match = var_pattern.match(stripped)
        if match:
            name = match.group(1)
            value_str = match.group(2).strip()

            # Skip special OpenSCAD variables
            if name.startswith("$") or name in ("eps", "epsilon"):
                continue

            try:
                default_val = float(value_str)

                if "angle" in name.lower() or "rotation" in name.lower():
                    suggested_min, suggested_max = 0.0, 360.0
                elif "thickness" in name.lower() or "wall" in name.lower():
                    suggested_min = max(0.5, default_val * 0.25)
                    suggested_max = default_val * 4.0
                elif "count" in name.lower() or "num" in name.lower():
                    suggested_min = max(1, default_val * 0.5)
                    suggested_max = default_val * 3.0
                elif default_val > 0:
                    suggested_min = round(default_val * 0.25, 2)
                    suggested_max = round(default_val * 4.0, 2)
                else:
                    suggested_min = default_val - abs(default_val)
                    suggested_max = (
                        default_val + abs(default_val) if default_val != 0 else 10.0
                    )

                parameters.append(
                    {
                        "name": name,
                        "default": default_val,
                        "suggested_min": round(suggested_min, 2),
                        "suggested_max": round(suggested_max, 2),
                    }
                )
            except ValueError:
                parameters.append(
                    {
                        "name": name,
                        "default": value_str,
                        "note": "Non-numeric; cannot auto-range",
                    }
                )

    if not parameters:
        return (
            "NO PARAMETERS FOUND. The code has no top-level variables before the "
            "first module definition. Per the coding guidelines, all dimensions "
            "should be defined as named top-level variables."
        )

    result_lines = [f"Found {len(parameters)} parameter(s):"]
    for p in parameters:
        if "note" in p:
            result_lines.append(f"  - {p['name']} = {p['default']} ({p['note']})")
        else:
            result_lines.append(
                f"  - {p['name']} = {p['default']} "
                f"(suggested range: {p['suggested_min']} to {p['suggested_max']})"
            )

    return "\n".join(result_lines)
