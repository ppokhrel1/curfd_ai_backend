"""Tests for agent helper functions (_extract_from_text, _extract_parameters_from_code, _score_openscad_code, etc.)."""

import pytest

from app.services.openscad_agent.agent import (
    _extract_from_text,
    _extract_parameters_from_code,
    _score_openscad_code,
    _extract_openscad_from_text,
    _looks_like_openscad,
    _strip_code_fences,
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


# ── _extract_openscad_from_text ──────────────────────────────────────────────

def test_extract_openscad_from_text_code_block():
    text = "Here is your model:\n\n```openscad\ncube([10, 10, 10]);\nsphere(r=5);\nmodule foo() {}\n```\n"
    code = _extract_openscad_from_text(text)
    assert code is not None
    assert "cube([10, 10, 10]);" in code


def test_extract_openscad_from_text_raw_code():
    """Raw OpenSCAD without fences should be extracted if score >= 5."""
    code = "$fn=64;\nwidth=20;\nheight=30;\nmodule box() {\n  cube([width,width,height]);\n}\ntranslate([0,0,0]) box();\n"
    result = _extract_openscad_from_text(code)
    assert result is not None
    assert "cube" in result


def test_extract_openscad_from_text_no_code():
    assert _extract_openscad_from_text("Just a regular chat message.") is None


def test_extract_openscad_from_text_empty():
    assert _extract_openscad_from_text("") is None


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


# ── _extract_from_text ───────────────────────────────────────────────────────

def test_extract_from_text_code_block():
    text = "Here is a cube:\n\n```openscad\ncube([10, 10, 10]);\n```\n\nEnjoy!"
    result = _extract_from_text(text)
    assert result["openscad_code"] == "cube([10, 10, 10]);"
    assert result["model_type"] == "mechanical"
    assert "cube" in result["message"].lower() or "Here" in result["message"]


def test_extract_from_text_sentinel_code():
    """Test that sentinel-delimited code from apply_parameter_changes is extracted."""
    text = (
        "PATCHED 1 parameter(s): width: 20 -> 40\n"
        "\n---PATCHED_CODE_START---\n"
        "width = 40;\ncube([width, width, 10]);\n"
        "\n---PATCHED_CODE_END---"
    )
    result = _extract_from_text(text)
    assert "width = 40;" in result["openscad_code"]
    assert result["model_type"] == "mechanical"


def test_extract_from_text_no_code():
    text = "OpenSCAD is a great tool for parametric design!"
    result = _extract_from_text(text)
    assert result["openscad_code"] == ""
    assert result["model_type"] == "chat"
    assert "OpenSCAD" in result["message"]


def test_extract_from_text_message_truncation():
    long_message = "A" * 600 + "\n```openscad\ncube(10);\n```"
    result = _extract_from_text(long_message)
    assert len(result["message"]) <= 500


def test_extract_from_text_code_block_preferred_over_sentinel():
    """If both code block and sentinel exist, code block should win."""
    text = (
        "```openscad\ncube([20, 20, 20]);\n```\n"
        "\n---PATCHED_CODE_START---\ncube([10, 10, 10]);\n---PATCHED_CODE_END---"
    )
    result = _extract_from_text(text)
    # The code block version should be used (20, not 10)
    assert "20" in result["openscad_code"]


def test_extract_from_text_raw_openscad_fallback():
    """Test that raw OpenSCAD code is extracted via score-based fallback."""
    raw_code = "$fn=64;\nwidth=20;\nheight=30;\nmodule box() {\n  cube([width,width,height]);\n}\ntranslate([0,0,0]) box();\n"
    result = _extract_from_text(raw_code)
    assert result["openscad_code"] != ""
    assert result["model_type"] == "mechanical"
