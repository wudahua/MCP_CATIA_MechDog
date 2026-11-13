"""0.3.120 - text outside ASCII: a real font's OUTLINES, read with no new dependency.

The 2026-11-11 report's complaint was that every glyph had to be handed over as
point sets. Hershey answered it for ASCII; a Chinese word still needed the hand
work, because there is no public-domain SINGLE-LINE CJK face with real coverage.
What every machine has is an outline font, so this reads it directly.

Two things these tests insist on:

* the reader works on the fonts that are actually on the machine (simhei.ttf is a
  cmap-format-4 font, msyh.ttc is format 12 and a collection), and
* the tool says which face it used and what that costs - an outline has two walls
  per stem, and a closed contour cannot be engraved by a point-on-curve sweep.
"""

from __future__ import annotations

import os
import types

import pytest

from catia_mcp import fonts
from catia_mcp.font_outline import FontFormatError, TrueTypeFont
from catia_mcp.results import ArgumentRejected, PreconditionRejected
from catia_mcp.tools.sketcher import SketcherTools

FONT = fonts.find_outline_font()
needs_font = pytest.mark.skipif(
    FONT is None, reason="no outline font on this machine")


def _font() -> TrueTypeFont:
    return fonts.outline_font()


# ── the reader ────────────────────────────────────────────────────────


@needs_font
def test_a_chinese_glyph_has_several_closed_contours() -> None:
    # 中 is one box plus one stroke through it: three contours, and each one
    # comes back closed (first point == last), which is what makes it pad-able.
    glyph = _font().glyph(ord("中"), flatten=_font().units_per_em * 0.004)
    assert glyph is not None and 2 <= len(glyph.contours) <= 6
    for contour in glyph.contours:
        assert contour[0] == contour[-1]
        assert len(contour) >= 4


@needs_font
def test_a_glyph_with_a_hole_reports_the_hole_as_its_own_contour() -> None:
    # '回' is a box inside a box: the outer one, its hole, and the inner one plus
    # its hole. Filling all four would be wrong; the count is the whole point.
    glyph = _font().glyph(ord("回"), flatten=_font().units_per_em * 0.004)
    assert glyph is not None and len(glyph.contours) == 4


@needs_font
def test_the_advance_of_a_cjk_glyph_is_the_em() -> None:
    font = _font()
    glyph = font.glyph(ord("文"), flatten=1.0)
    assert glyph is not None
    assert glyph.advance == pytest.approx(font.units_per_em, rel=0.02)


@needs_font
def test_an_unmapped_code_point_is_none_not_an_empty_glyph() -> None:
    assert _font().glyph(0x0378) is None      # unassigned in Unicode


def test_a_missing_file_is_reported_by_the_caller_not_the_reader() -> None:
    assert fonts.find_outline_font("C:/definitely/not/a/font.ttf") is None


def test_no_font_at_all_is_a_coded_refusal(monkeypatch) -> None:
    monkeypatch.setattr(fonts, "find_outline_font", lambda preferred=None: None)
    monkeypatch.setattr(fonts, "OUTLINE_FONT_CANDIDATES", ())
    with pytest.raises(ArgumentRejected) as caught:
        fonts.outline_font()
    assert getattr(caught.value, "code", "") == "NO_OUTLINE_FONT"
    # The message names what was looked for, so the caller can fix it.
    assert "font_path" in str(caught.value)


def test_a_cff_opentype_file_is_refused_with_the_reason() -> None:
    """The reader decodes quadratic outlines; 'OTTO' means PostScript ones."""
    import struct

    header = b"OTTO" + struct.pack(">H", 2) + b"\x00" * 6
    record = b"head" + b"\x00" * 4 + struct.pack(">II", 0, 0)
    path = os.path.join(os.environ.get("TEMP", "."), "cff_probe.otf")
    with open(path, "wb") as handle:
        handle.write(header + record + record)
    try:
        with pytest.raises(FontFormatError) as caught:
            TrueTypeFont(path)
        assert "PostScript" in str(caught.value) or "CFF" in str(caught.value)
    finally:
        os.remove(path)


# ── laying the outlines out ───────────────────────────────────────────


@needs_font
def test_a_chinese_character_comes_out_taller_than_the_cap_height() -> None:
    """'height' is the CAP height, as for ASCII, and an ideograph fills the em.

    So 10 mm of "height" is about 12.5 mm of 中 - stated rather than hidden,
    because a caller sizing a label needs the number that is really drawn.
    """
    result = fonts.text_to_outlines("中", height_mm=10.0)
    ys = [point[1] for stroke in result.strokes for point in stroke]
    assert result.outline is True
    assert 10.5 < (max(ys) - min(ys)) < 14.0


@needs_font
def test_every_contour_is_closed_and_the_baseline_is_the_origin() -> None:
    result = fonts.text_to_outlines("AB", height_mm=10.0, origin=(5.0, 2.0))
    assert all(stroke[0] == stroke[-1] for stroke in result.strokes)
    ys = [point[1] for stroke in result.strokes for point in stroke]
    assert min(ys) <= 2.0 <= max(ys), "the baseline sits inside the ink"


@needs_font
def test_the_two_faces_agree_on_width_within_a_few_percent() -> None:
    """A sanity check that the outline path is scaled like the stroke path."""
    strokes = fonts.text_to_strokes("ABC", height_mm=10.0)
    outlines = fonts.text_to_outlines("ABC", height_mm=10.0)
    assert outlines.width_mm == pytest.approx(strokes.width_mm, rel=0.2)


@needs_font
def test_a_character_the_font_lacks_is_named_not_drawn() -> None:
    result = fonts.text_to_outlines("A\u0378", height_mm=10.0)
    assert result.missing == ["\u0378"]
    assert result.glyphs == 1


@needs_font
def test_a_space_advances_without_drawing() -> None:
    result = fonts.text_to_outlines("A B", height_mm=10.0)
    assert result.spaces == 1 and result.glyphs == 2


# ── the tool ──────────────────────────────────────────────────────────


class _Line:
    def __init__(self, x1, y1, x2, y2):
        self.points = (x1, y1, x2, y2)
        self.Construction = False


class _Factory:
    def __init__(self):
        self.lines = []

    def CreateLine(self, x1, y1, x2, y2):
        line = _Line(x1, y1, x2, y2)
        self.lines.append(line)
        return line


def _tools():
    tools = SketcherTools(connection=None)  # type: ignore[arg-type]
    factory = _Factory()
    tools._active_factory = factory
    tools._active_sketch = types.SimpleNamespace(Name="Sketch.1")
    tools._ensure_sketch_open = lambda: None      # type: ignore[method-assign]
    tools._note_ends = lambda a, b: None          # type: ignore[method-assign]
    tools._note_element = lambda kind, **params: None  # type: ignore[method-assign]
    return tools, factory


@needs_font
def test_chinese_text_is_drawn_and_the_reply_names_the_face() -> None:
    tools, factory = _tools()
    reply = tools.execute("catia_text_strokes", {"text": "中文", "height": 10.0})

    assert factory.lines, "the contours must be drawn"
    assert "OUTLINE FONT" in reply
    assert "closed contour(s)" in reply
    assert "PROFILE" in reply, "a closed contour is a pad profile, not lettering"


@needs_font
def test_ascii_still_comes_from_hershey_single_lines() -> None:
    tools, _factory = _tools()
    reply = tools.execute("catia_text_strokes", {"text": "OK", "height": 10.0})

    assert "Hershey simplex, single-line" in reply
    assert "OUTLINE FONT" not in reply


@needs_font
def test_outline_true_forces_the_outline_face_for_ascii() -> None:
    tools, _factory = _tools()
    reply = tools.execute("catia_text_strokes",
                          {"text": "OK", "height": 10.0, "outline": True})
    assert "OUTLINE FONT" in reply


@needs_font
def test_a_bad_font_path_is_refused_before_anything_is_drawn() -> None:
    tools, factory = _tools()
    with pytest.raises(ArgumentRejected) as caught:
        tools.execute("catia_text_strokes",
                      {"text": "中", "font_path": "C:/nope/nothing.ttf"})
    assert factory.lines == []
    assert getattr(caught.value, "code", "") == "NO_OUTLINE_FONT"


@needs_font
def test_an_outline_sketch_is_not_called_lettering_at_close_time() -> None:
    """The close-time voice follows the FACE, not the fact that it came from text.

    Hershey's open strokes are lettering and are told so; a real font's closed
    contours are a profile, and telling the caller "Part Design features will
    refuse this" would be backwards advice.
    """
    tools = SketcherTools(connection=None)  # type: ignore[arg-type]
    tools._drawn = {}
    key = tools._sketch_key("Sketch.1")
    tools._drawn[key] = [
        {"kind": "line", "source": "text-outline", "stroke": 1,
         "x1": 0, "y1": 0, "x2": 10, "y2": 0},
        {"kind": "line", "source": "text-outline", "stroke": 1,
         "x1": 10, "y1": 0, "x2": 10, "y2": 10},
    ]
    elements, strokes, total, outline = tools._text_strokes_in("Sketch.1")

    assert (elements, strokes, total, outline) == (2, 1, 2, True)


def test_an_hershey_sketch_is_still_lettering() -> None:
    tools = SketcherTools(connection=None)  # type: ignore[arg-type]
    tools._drawn = {tools._sketch_key("Sketch.1"): [
        {"kind": "line", "source": "text", "stroke": 1,
         "x1": 0, "y1": 0, "x2": 10, "y2": 0}]}
    elements, _strokes, total, outline = tools._text_strokes_in("Sketch.1")

    assert (elements, total, outline) == (1, 1, False)


# ── the revolution axis may name an ELEMENT ───────────────────────────
#
# Measured on B30 2026-11-14, one fresh document per cell: the same profile
# (rectangle u 5..15 / v 0..30 on 'zx', area 300) revolved about
#     the sketch's H axis      28274.33 mm3   = 2*pi*15*300
#     the sketch's V axis      18849.56 mm3   = 2*pi*10*300
#     a gsd_line along Z       28274.33 mm3   (H IS Z on this plane)
#     a gsd_line along X       18849.56 mm3   (V IS X here)
#     a gsd_line along Z at x=60  84823.00 mm3 = 2*pi*45*300
# The last cell is the one that proves the reference is used rather than
# ignored: no sketch axis can produce 45 mm of centroid radius.

import types

from catia_mcp.tools import gsd as gsd_module
from catia_mcp.tools.part_design import PartDesignTools


def _revolution_tools() -> PartDesignTools:
    class Fake(PartDesignTools):
        def __init__(self) -> None:
            super().__init__(connection=None)  # type: ignore[arg-type]

        def _sketch_absolute_axis(self, sketch):
            return types.SimpleNamespace(HorizontalReference="H-axis",
                                         VerticalReference="V-axis")

    return Fake()


def test_the_two_named_axes_still_resolve_to_the_sketch(monkeypatch) -> None:
    tools = _revolution_tools()
    assert tools._revolve_axis_reference(None, object(), "horizontal") == "H-axis"
    assert tools._revolve_axis_reference(None, object(), "Vertical") == "V-axis"


def test_any_other_value_is_an_element_name(monkeypatch) -> None:
    tools = _revolution_tools()
    monkeypatch.setattr(gsd_module.GSDTools, "_ref",
                        lambda self, name: ("reference", name))
    assert tools._revolve_axis_reference(None, object(), "FarAxis") == (
        "reference", "FarAxis")


def test_an_axis_that_cannot_be_found_says_what_the_argument_takes(monkeypatch) -> None:
    def refuse(self, name):
        raise ArgumentRejected("Element 'Nope' not found", code="ELEMENT_NOT_FOUND")

    monkeypatch.setattr(gsd_module.GSDTools, "_ref", refuse)
    with pytest.raises(ArgumentRejected) as caught:
        _revolution_tools()._revolve_axis_reference(None, object(), "Nope")

    message = str(caught.value)
    assert getattr(caught.value, "code", "") == "BAD_AXIS"
    assert "'horizontal'" in message and "element" in message.lower()


def test_the_reply_names_the_axis_it_used() -> None:
    tools = _revolution_tools()
    sketch = types.SimpleNamespace(Name="Sketch.1")

    assert tools._axis_label("horizontal", sketch) == (
        "the horizontal axis of 'Sketch.1'")
    assert tools._axis_label("FarAxis", sketch) == (
        "'FarAxis' (an element of the tree, not a sketch axis)")
