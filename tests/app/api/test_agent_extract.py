"""Tests for agent helper functions (_extract_parameters_from_code, _score_openscad_code, etc.)."""

import pytest

from app.services.openscad_agent.agent import (
    _extract_parameters_from_code,
    _score_openscad_code,
    _looks_like_openscad,
    _strip_code_fences,
    CodeGenResult,
    CodeParameter,
)


# ── _extract_parameters_from_code ────────────────────────────────────────────

def test_extract_parameters_basic():
    code = "width = 20;\nheight = 30;\n$fn = 64;\n"
    params = _extract_parameters_from_code(code)
    names = [p["name"] for p in params]
    assert "width" in names
    assert "height" in names
    # $fn should be excluded
    assert "$fn" not in names


def test_extract_parameters_with_floats():
    code = "wall = 1.5;\nradius = 10.0;\n"
    params = _extract_parameters_from_code(code)
    assert len(params) == 2
    wall = next(p for p in params if p["name"] == "wall")
    assert wall["default_val"] == 1.5


def test_extract_parameters_ignores_special_vars():
    code = "$fn = 64;\n$fa = 12;\n$fs = 2;\neps = 0.01;\nreal_param = 5;\n"
    params = _extract_parameters_from_code(code)
    names = [p["name"] for p in params]
    assert names == ["real_param"]


def test_extract_parameters_empty_code():
    assert _extract_parameters_from_code("") == []


def test_extract_parameters_ranges():
    """Verify min/max range calculation."""
    code = "size = 20;\n"
    params = _extract_parameters_from_code(code)
    p = params[0]
    assert p["min_val"] == 10.0   # 20 * 0.5
    assert p["max_val"] == 30.0   # 20 * 1.5


# ── _score_openscad_code ─────────────────────────────────────────────────────

def test_score_openscad_code_valid():
    """Valid OpenSCAD code should score high."""
    code = """\
$fn = 64;
width = 20;
height = 30;

module box() {
    cube([width, width, height]);
}

main();
"""
    score = _score_openscad_code(code)
    assert score >= 3


def test_score_openscad_code_complex():
    """Code with many OpenSCAD features should score very high."""
    code = """\
$fn = 64;
width = 20;
height = 30;
wall = 2;

module body() {
    difference() {
        cylinder(h=height, r=width);
        translate([0, 0, wall])
            cylinder(h=height, r=width-wall);
    }
}

module handle() {
    translate([width, 0, height/2])
    rotate([90, 0, 0])
    linear_extrude(height=10)
    circle(r=15);
}

module main() {
    union() {
        body();
        handle();
    }
}

main();
"""
    score = _score_openscad_code(code)
    assert score >= 5


def test_score_openscad_code_not_openscad():
    """Regular text should score low."""
    assert _score_openscad_code("Hello, this is just regular text.") < 3


def test_score_openscad_code_empty():
    assert _score_openscad_code("") == 0


def test_score_openscad_code_short():
    assert _score_openscad_code("x = 1;") == 0


# ── _looks_like_openscad ────────────────────────────────────────────────────

def test_looks_like_openscad_true():
    code = "$fn=64;\nmodule foo() {\n  cube([10,10,10]);\n  translate([5,5,5]) sphere(r=3);\n}"
    assert _looks_like_openscad(code) is True


def test_looks_like_openscad_false():
    assert _looks_like_openscad("This is just text") is False


# ── _strip_code_fences ───────────────────────────────────────────────────────

def test_strip_code_fences_openscad():
    code = "```openscad\ncube([10,10,10]);\n```"
    assert _strip_code_fences(code) == "cube([10,10,10]);"


def test_strip_code_fences_plain():
    code = "```\ncube([10,10,10]);\n```"
    assert _strip_code_fences(code) == "cube([10,10,10]);"


def test_strip_code_fences_no_fences():
    code = "cube([10,10,10]);"
    assert _strip_code_fences(code) == "cube([10,10,10]);"


# ── CodeGenResult schema ────────────────────────────────────────────────────

def test_code_gen_result_schema():
    """Verify the structured output schema can be instantiated."""
    result = CodeGenResult(
        code="cube([10, 10, 10]);",
        parameters=[
            CodeParameter(name="size", value=10.0, min_val=5.0, max_val=20.0, description="Cube size"),
        ],
        description="A simple cube",
    )
    assert result.code == "cube([10, 10, 10]);"
    assert len(result.parameters) == 1
    assert result.parameters[0].name == "size"
    assert result.parameters[0].min_val == 5.0


def test_code_gen_result_to_dict():
    """Verify model_dump produces the right structure."""
    result = CodeGenResult(
        code="sphere(r=5);",
        parameters=[
            CodeParameter(name="radius", value=5.0, min_val=1.0, max_val=15.0, description="Sphere radius"),
        ],
        description="A sphere",
    )
    params = [p.model_dump() for p in result.parameters]
    assert params[0]["name"] == "radius"
    assert params[0]["value"] == 5.0
