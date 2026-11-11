"""0.3.103: text from a font that ships with the server.

The font half is pure arithmetic, so it is pinned to numbers that can be checked
by hand: a capital is 21 font units, so height_mm / 21 is the scale, and 'A'
spans y = 0..10 when height_mm is 10.

The tool half is about the two ways this fails - a character that is not in the
font, and a request that has nothing to draw - plus the measured catch that is
repeated in its reply: a glyph's projection refuses a parameter point at high
coverage.
"""
from __future__ import annotations

import types

import pytest

from catia_mcp import fonts
from catia_mcp.results import ArgumentRejected, PreconditionRejected
from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS
from catia_mcp.tools.sketcher import SketcherTools


# ── the font ──────────────────────────────────────────────────────────────


def _bounds(result):
    xs = [point[0] for stroke in result.strokes for point in stroke]
    ys = [point[1] for stroke in result.strokes for point in stroke]
    return min(xs), max(xs), min(ys), max(ys)


def test_a_capital_is_exactly_the_cap_height_tall():
    result = fonts.text_to_strokes("A", height_mm=10.0)
    _x0, _x1, y0, y1 = _bounds(result)
    assert y0 == pytest.approx(0.0, abs=1e-9), "the baseline sits on the origin"
    assert y1 == pytest.approx(10.0, abs=1e-9), "a 10 mm capital is 10 mm tall"


def test_the_scale_is_height_over_21_font_units():
    """'A' spans 18 font units of advance, so at 10 mm it is 18/21*10 wide."""
    result = fonts.text_to_strokes("A", height_mm=10.0)
    assert result.width_mm == pytest.approx(18 * 10 / 21, abs=1e-6)


def test_a_descender_goes_below_the_baseline():
    """'g' reaches 7 units below the baseline in the font's own numbers."""
    result = fonts.text_to_strokes("Hg", height_mm=21.0)
    _x0, _x1, y0, _y1 = _bounds(result)
    assert y0 == pytest.approx(-7.0, abs=1e-6)


def test_two_lines_step_down_by_1_6_cap_heights():
    result = fonts.text_to_strokes("AB\nCD", height_mm=10.0)
    assert result.lines == 2
    _x0, _x1, y0, y1 = _bounds(result)
    assert y1 == pytest.approx(10.0, abs=1e-9), "the first line still caps at 10"
    assert y0 == pytest.approx(-16.0, abs=1e-9), (
        "the second baseline sits 1.6 cap heights below the first, and these "
        "letters have no descender, so the lowest point IS that baseline")


def test_the_origin_moves_the_baseline_not_the_glyph():
    result = fonts.text_to_strokes("A", height_mm=10.0, origin=(5.0, 3.0))
    _x0, _x1, y0, y1 = _bounds(result)
    assert y0 == pytest.approx(3.0, abs=1e-9)
    assert y1 == pytest.approx(13.0, abs=1e-9)


def test_spacing_widens_the_text_and_defaults_to_a_fifth_of_the_height():
    tight = fonts.text_to_strokes("HH", height_mm=10.0, spacing_mm=0.0)
    default = fonts.text_to_strokes("HH", height_mm=10.0)
    assert default.width_mm == pytest.approx(
        tight.width_mm + fonts.DEFAULT_SPACING_RATIO * 10.0, abs=1e-9)


def test_a_space_has_a_width_and_no_strokes():
    with_space = fonts.text_to_strokes("A A", height_mm=10.0)
    without = fonts.text_to_strokes("AA", height_mm=10.0)
    assert with_space.spaces == 1
    assert len(with_space.strokes) == len(without.strokes)
    assert with_space.width_mm > without.width_mm


def test_a_character_with_no_glyph_is_named_and_not_drawn_as_a_box():
    result = fonts.text_to_strokes("A瓶B", height_mm=10.0)
    assert result.missing == ["瓶"]
    assert result.glyphs == 2
    alone = fonts.text_to_strokes("瓶", height_mm=10.0)
    assert alone.strokes == []
    assert alone.missing == ["瓶"]


def test_no_stroke_ever_repeats_a_point_after_scaling():
    """catia_sketch_strokes refuses a zero-length segment outright, so the font
    must not produce one - including at heights where two vertices round onto
    each other."""
    for text in ("O", "0", "8", "B", "&", "%"):
        for height in (0.5, 1.0, 10.0):
            result = fonts.text_to_strokes(text, height_mm=height)
            for stroke in result.strokes:
                for (x1, y1), (x2, y2) in zip(stroke, stroke[1:]):
                    assert abs(x2 - x1) > 0 or abs(y2 - y1) > 0, (
                        f"{text} at {height} mm repeats a point")


def test_every_printable_ascii_character_has_a_glyph():
    printable = "".join(chr(code) for code in range(32, 127))
    missing = [char for char in printable if not fonts.has_glyph(char)]
    assert missing == []


def test_a_non_positive_height_is_refused_with_a_code():
    """A bare ValueError would be an unreported refusal - the package forbids
    them (tests/test_argument_envelopes.py), and so does a caller that branches
    on the code."""
    with pytest.raises(ArgumentRejected) as caught:
        fonts.text_to_strokes("A", height_mm=0.0)
    assert caught.value.code == "BAD_ARGUMENT"


# ── the tool ──────────────────────────────────────────────────────────────


class _Factory:
    def __init__(self):
        self.lines = []

    def CreateLine(self, x1, y1, x2, y2):
        self.lines.append((x1, y1, x2, y2))
        return types.SimpleNamespace(Construction=False)


def _tools() -> tuple[SketcherTools, _Factory]:
    tools = SketcherTools(connection=None)  # type: ignore[arg-type]
    factory = _Factory()
    tools._active_factory = factory
    tools._active_sketch = types.SimpleNamespace(Name="Sketch.1")
    tools._ensure_sketch_open = lambda: None  # type: ignore[method-assign]
    tools._note_ends = lambda a, b: None  # type: ignore[method-assign]
    tools._note_element = lambda kind, **params: None  # type: ignore[method-assign]
    return tools, factory


def test_text_is_drawn_as_the_font_says_and_the_reply_says_so():
    tools, factory = _tools()
    reply = tools.execute("catia_text_strokes",
                          {"text": "HI", "height": 10.0})
    wanted = fonts.text_to_strokes("HI", height_mm=10.0)
    segments = sum(len(stroke) - 1 for stroke in wanted.strokes)
    assert len(factory.lines) == segments
    assert "2 glyph(s)" in reply and "1 line(s)" in reply
    assert "10 mm cap height" in reply


def test_the_reply_repeats_the_measured_engraving_catch():
    """The caller's next step is project-then-engrave, which fails for some
    glyphs; saying it here is the difference between a tool and a trap."""
    tools, _factory = _tools()
    reply = tools.execute("catia_text_strokes", {"text": "O", "height": 10.0})
    assert "81 %" in reply and "57 %" in reply
    assert "catia_diagnose" in reply


def test_a_string_with_no_drawable_character_is_refused_by_name():
    tools, factory = _tools()
    with pytest.raises(PreconditionRejected) as caught:
        tools.execute("catia_text_strokes", {"text": "瓶子", "height": 10.0})
    assert caught.value.code == "TEXT_NO_GLYPHS"
    assert "瓶" in str(caught.value)
    assert factory.lines == [], "nothing may be drawn when nothing can be"


def test_a_partly_unknown_string_draws_what_it_can_and_names_the_rest():
    tools, factory = _tools()
    reply = tools.execute("catia_text_strokes",
                          {"text": "A瓶B", "height": 10.0})
    assert "SKIPPED" in reply and "瓶" in reply
    assert factory.lines, "the known characters must still be drawn"


@pytest.mark.parametrize("arguments", [
    {"text": "   "},
    {"text": "A", "height": 0},
    {"text": "A", "height": -3},
    {"text": "A", "origin": [1]},
    {"text": "A", "origin": "left"},
])
def test_bad_requests_are_refused_before_anything_is_drawn(arguments) -> None:
    tools, factory = _tools()
    with pytest.raises(ArgumentRejected):
        tools.execute("catia_text_strokes", arguments)
    assert factory.lines == []


def test_multi_line_text_steps_down_in_the_sketch():
    tools, factory = _tools()
    tools.execute("catia_text_strokes", {"text": "A\nB", "height": 10.0})
    ys = [y for _x1, y, _x2, _y2 in factory.lines] + [
        y2 for _x1, _y1, _x2, y2 in factory.lines]
    assert min(ys) < -5.0, "the second line must sit below the first baseline"


# ── what the ceilings now say ─────────────────────────────────────────────


def test_the_rib_ceiling_is_measured_rather_than_declared():
    entry = next((item for item in _CAPABILITY_CEILINGS
                  if item["member"] == "AddNewRib / AddNewStiffener"), None)
    assert entry is not None
    assert entry["status"] == "AVAILABLE_WITH_LIMIT"
    assert "1175.0633" in entry["detail"]
    assert "DECLARED_NOT_MEASURED" not in entry["status"]


def test_the_glyph_projection_ceiling_carries_the_measurements():
    entry = next((item for item in _CAPABILITY_CEILINGS
                  if "PROJECTED GLYPH" in item["member"]), None)
    assert entry is not None
    assert entry["status"] == "MEASURED_UNEXPLAINED"
    for number in ("81.4", "57.2", "18.2", "21 vertices"):
        assert number in entry["detail"], number
    assert "NOT CONFIRMED" in entry["detail"], "the closure reason is a guess"


def test_the_create_text_ceiling_points_at_the_font_that_now_exists():
    entry = next((item for item in _CAPABILITY_CEILINGS
                  if item["member"] == "Factory2D.CreateText"), None)
    assert entry is not None
    assert "catia_text_strokes" in entry["detail"]
    assert "ASCII 32-126" in entry["detail"]
