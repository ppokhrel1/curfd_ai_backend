"""Tests for OpenSCAD agent tools (parameter_patcher, model_builder)."""

import json
import pytest

from app.services.openscad_agent.tools.parameter_patcher import apply_parameter_changes
from app.services.openscad_agent.tools.model_builder import build_parametric_model


# ── apply_parameter_changes ──────────────────────────────────────────────────

SAMPLE_CODE = """\
$fn = 64;
width = 20;
height = 30;
wall_thickness = 1.5;

module box() {
    difference() {
        cube([width, width, height]);
        translate([wall_thickness, wall_thickness, wall_thickness])
            cube([width - 2*wall_thickness, width - 2*wall_thickness, height]);
    }
}

main();
"""


def test_apply_parameter_changes_single():
    result = apply_parameter_changes.invoke({
        "existing_code": SAMPLE_CODE,
        "parameter_changes": '{"width": 40}',
    })
    assert "PATCHED 1 parameter(s)" in result
    assert "width: 20 -> 40" in result
    assert "---PATCHED_CODE_START---" in result
    assert "width = 40;" in result
    # Other params unchanged
    assert "height = 30;" in result


def test_apply_parameter_changes_multiple():
    result = apply_parameter_changes.invoke({
        "existing_code": SAMPLE_CODE,
        "parameter_changes": '{"width": 50, "height": 60}',
    })
    assert "PATCHED 2 parameter(s)" in result
    assert "width = 50;" in result
    assert "height = 60;" in result


def test_apply_parameter_changes_not_found():
    result = apply_parameter_changes.invoke({
        "existing_code": SAMPLE_CODE,
        "parameter_changes": '{"nonexistent": 99}',
    })
    assert "No parameters matched" in result
    assert "---PATCHED_CODE_START---" not in result


def test_apply_parameter_changes_partial_match():
    result = apply_parameter_changes.invoke({
        "existing_code": SAMPLE_CODE,
        "parameter_changes": '{"width": 25, "missing_param": 10}',
    })
    assert "PATCHED 1 parameter(s)" in result
    assert "NOT FOUND: missing_param" in result
    assert "---PATCHED_CODE_START---" in result


def test_apply_parameter_changes_invalid_json():
    result = apply_parameter_changes.invoke({
        "existing_code": SAMPLE_CODE,
        "parameter_changes": "not valid json",
    })
    assert "ERROR" in result
    assert "valid JSON" in result


def test_apply_parameter_changes_non_object_json():
    result = apply_parameter_changes.invoke({
        "existing_code": SAMPLE_CODE,
        "parameter_changes": "[1, 2, 3]",
    })
    assert "ERROR" in result
    assert "JSON object" in result


def test_apply_parameter_changes_float_value():
    result = apply_parameter_changes.invoke({
        "existing_code": SAMPLE_CODE,
        "parameter_changes": '{"wall_thickness": 2.0}',
    })
    assert "PATCHED 1 parameter(s)" in result
    assert "wall_thickness: 1.5 -> 2.0" in result


def test_apply_parameter_changes_preserves_structure():
    """Ensure patching only changes the value, not surrounding code."""
    result = apply_parameter_changes.invoke({
        "existing_code": SAMPLE_CODE,
        "parameter_changes": '{"width": 35}',
    })
    # Extract patched code
    start = result.index("---PATCHED_CODE_START---") + len("---PATCHED_CODE_START---\n")
    end = result.index("\n---PATCHED_CODE_END---")
    patched = result[start:end]
    # Module definition should be untouched
    assert "module box()" in patched
    assert "main();" in patched


# ── build_parametric_model ───────────────────────────────────────────────────

def test_build_parametric_model_returns_instruction():
    result = build_parametric_model.invoke({
        "description": "Spur gear with 20 teeth",
        "requirements": "Module 2, pressure angle 20 degrees, center bore 8mm",
    })
    assert "PROCEED WITH FULL CODE GENERATION" in result
    assert "Spur gear with 20 teeth" in result
    assert "Module 2" in result


def test_build_parametric_model_empty_requirements():
    result = build_parametric_model.invoke({
        "description": "Simple box",
        "requirements": "",
    })
    assert "PROCEED WITH FULL CODE GENERATION" in result
    assert "Simple box" in result
