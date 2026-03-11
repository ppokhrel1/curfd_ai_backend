"""Tests for OpenSCAD agent tools (parameter_patcher, model_builder)."""

import json
import pytest

from app.services.openscad_agent.tools.parameter_patcher import (
    apply_parameter_changes,
    patch_code,
)
from app.services.openscad_agent.tools.model_builder import build_parametric_model


# ── patch_code (direct function, not tool) ──────────────────────────────────

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


def test_patch_code_single():
    patched, applied, not_found = patch_code(SAMPLE_CODE, [{"name": "width", "value": "40"}])
    assert len(applied) == 1
    assert "width: 20 -> 40" in applied[0]
    assert "width = 40;" in patched
    # Other params unchanged
    assert "height = 30;" in patched
    assert not not_found


def test_patch_code_multiple():
    patched, applied, not_found = patch_code(
        SAMPLE_CODE,
        [{"name": "width", "value": "50"}, {"name": "height", "value": "60"}],
    )
    assert len(applied) == 2
    assert "width = 50;" in patched
    assert "height = 60;" in patched


def test_patch_code_not_found():
    patched, applied, not_found = patch_code(
        SAMPLE_CODE, [{"name": "nonexistent", "value": "99"}]
    )
    assert not applied
    assert "nonexistent" in not_found


def test_patch_code_partial_match():
    patched, applied, not_found = patch_code(
        SAMPLE_CODE,
        [{"name": "width", "value": "25"}, {"name": "missing_param", "value": "10"}],
    )
    assert len(applied) == 1
    assert "missing_param" in not_found
    assert "width = 25;" in patched


def test_patch_code_float_value():
    patched, applied, not_found = patch_code(
        SAMPLE_CODE, [{"name": "wall_thickness", "value": "2.0"}]
    )
    assert len(applied) == 1
    assert "wall_thickness: 1.5 -> 2" in applied[0]


def test_patch_code_preserves_structure():
    """Ensure patching only changes the value, not surrounding code."""
    patched, applied, not_found = patch_code(
        SAMPLE_CODE, [{"name": "width", "value": "35"}]
    )
    assert "module box()" in patched
    assert "main();" in patched


def test_patch_code_empty_updates():
    patched, applied, not_found = patch_code(SAMPLE_CODE, [])
    assert patched == SAMPLE_CODE
    assert not applied
    assert not not_found


# ── apply_parameter_changes tool (schema only, intercepted by agent) ─────────

def test_apply_parameter_changes_tool_returns_message():
    """The tool itself just returns a status message (intercepted by agent loop)."""
    result = apply_parameter_changes.invoke({
        "updates": [{"name": "width", "value": "30"}],
    })
    assert "APPLY PARAMETER CHANGES" in result


# ── build_parametric_model ───────────────────────────────────────────────────

def test_build_parametric_model_returns_instruction():
    result = build_parametric_model.invoke({
        "text": "Spur gear with 20 teeth",
    })
    assert "PROCEED WITH FULL CODE GENERATION" in result
    assert "Spur gear with 20 teeth" in result


def test_build_parametric_model_with_base_code():
    result = build_parametric_model.invoke({
        "text": "Add a handle to this mug",
        "baseCode": "cylinder(h=100, r=40);",
    })
    assert "PROCEED WITH FULL CODE GENERATION" in result


def test_build_parametric_model_with_error():
    result = build_parametric_model.invoke({
        "text": "Fix the syntax error",
        "error": "Expected ';' at line 5",
    })
    assert "PROCEED WITH FULL CODE GENERATION" in result
