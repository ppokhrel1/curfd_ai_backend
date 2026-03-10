"""Tests for agent helper functions (_extract_from_text, _extract_parameters_from_code)."""

import pytest

from app.services.openscad_agent.agent import (
    _extract_from_text,
    _extract_parameters_from_code,
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
