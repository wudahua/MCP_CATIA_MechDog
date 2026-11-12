"""0.3.108: a mixed profile (straight + curved) in ONE call.

The 2026-11-11 eighth report put this first: "a profile that is straight on one
side and curved on the other has no workable combination", because
catia_sketch_spline rounds the straight stretches and catia_sketch_strokes
leaves the curved ones faceted.

Measured on B30 2026-11-12, before any tool was written: drawing the four
elements with the primitive for each kind works. A 60x40 stadium - two 60 mm
lines and two r=20 half circles, endpoints typed to the same numbers - made
catia_close_sketch answer "2 arc(s), 2 line(s), outline 245.664 mm", and the
10 mm pad came out at 36566.37 mm3, which is 60*40*10 + pi*20^2*10 exactly. The
same profile with a 0.05 mm gap was reported as not closed, with both
coordinates.

These tests pin the parts that do not need CATIA:
  1  validation happens BEFORE anything is drawn - a bad third segment leaves
     no first two behind
  2  a closed profile reports every junction as joined
  3  a gap wider than the tolerance is named with both coordinates
  4  the degenerate cases are refused with a reason (zero-length line, full
     circle given as an arc, unknown type, positive tolerance)
  5  arcs and lines are dispatched to the primitives that draw them
"""
from __future__ import annotations

import pytest

from catia_mcp.results import ArgumentOutOfRange, ArgumentRejected
from catia_mcp.tools.sketcher import SketcherTools


class _Recorder(SketcherTools):
    """A SketcherTools that records what it would draw instead of drawing it."""

    def __init__(self) -> None:
        super().__init__(connection=None)
        self._active_sketch = object()
        self.calls: list[tuple] = []

    def _draw_line(self, x1, y1, x2, y2, construction=False):   # noqa: D401
        self.calls.append(("line", x1, y1, x2, y2))
        return "Line created"

    def _draw_arc(self, cx, cy, radius, start_angle=None, end_angle=None,
                  p1=None, p2=None, direction="ccw", major=False):
        self.calls.append(("arc", cx, cy, radius, start_angle, end_angle,
                           direction))
        return "Arc created"


STADIUM = [
    {"type": "line", "p1": [0, 20], "p2": [60, 20]},
    {"type": "arc", "center": [60, 0], "radius": 20,
     "start_angle": 90, "end_angle": -90, "direction": "cw"},
    {"type": "line", "p1": [60, -20], "p2": [0, -20]},
    {"type": "arc", "center": [0, 0], "radius": 20,
     "start_angle": -90, "end_angle": 90, "direction": "cw"},
]


def test_a_bad_segment_is_refused_before_a_single_element_is_drawn():
    tools = _Recorder()
    broken = STADIUM[:2] + [{"type": "line", "p1": [60, -20]}, STADIUM[3]]

    with pytest.raises(ArgumentRejected) as caught:
        tools._draw_profile(broken)

    assert "p2" in str(caught.value)
    assert tools.calls == [], "the first two segments must not survive the refusal"


def test_a_closed_stadium_reports_every_junction_as_joined():
    tools = _Recorder()

    reply = tools._draw_profile(STADIUM)

    assert [call[0] for call in tools.calls] == ["line", "arc", "line", "arc"]
    assert "4 segment(s)" in reply and "2 line(s), 2 arc(s)" in reply
    assert "Every one of the 4 junction(s) meets within 0.0000 mm" in reply
    assert "WARNING" not in reply


def test_a_gap_is_named_with_both_coordinates():
    tools = _Recorder()
    gapped = [
        {"type": "line", "p1": [0, 20], "p2": [60, 20]},
        {"type": "arc", "center": [60, 0], "radius": 20,
         "start_angle": 90, "end_angle": -90, "direction": "cw"},
        {"type": "line", "p1": [60.06, -20], "p2": [0, -20]},
        {"type": "arc", "center": [0, 0], "radius": 20,
         "start_angle": -90, "end_angle": 90, "direction": "cw"},
    ]

    reply = tools._draw_profile(gapped)

    assert "NOT CLOSED" in reply
    assert "0.0600 mm" in reply
    # Both sides of the break, so the caller can see which one to move.
    assert "(60.0000, -20.0000)" in reply and "(60.0600, -20.0000)" in reply
    # The advice has to match what CATIA actually does with small gaps.
    assert "try the pad before rebuilding" in reply


def test_the_tolerance_is_what_decides_joined_not_the_drawing():
    tools = _Recorder()
    gapped = [
        {"type": "line", "p1": [0, 20], "p2": [60, 20]},
        {"type": "arc", "center": [60, 0], "radius": 20,
         "start_angle": 90, "end_angle": -90, "direction": "cw"},
        {"type": "line", "p1": [60.04, -20], "p2": [0, -20]},
        {"type": "arc", "center": [0, 0], "radius": 20,
         "start_angle": -90, "end_angle": 90, "direction": "cw"},
    ]
    assert "WARNING" not in tools._draw_profile(gapped)
    assert "NOT CLOSED" in tools._draw_profile(gapped, tolerance=0.01)


@pytest.mark.parametrize("segment, needle", [
    ({"type": "line", "p1": [0, 0], "p2": [0, 0]}, "zero length"),
    ({"type": "arc", "center": [0, 0], "radius": 0,
      "start_angle": 0, "end_angle": 90}, "has to be positive"),
    ({"type": "arc", "center": [0, 0], "radius": 10,
      "start_angle": 45, "end_angle": 45}, "full circle"),
    ({"type": "spline", "points": [[0, 0], [1, 1]]}, "this tool draws"),
    ({"type": "arc", "center": [0, 0], "radius": 10,
      "start_angle": 0, "end_angle": 90, "direction": "clockwise"}, "'cw' or 'ccw'"),
])
def test_degenerate_segments_are_refused_with_a_reason(segment, needle):
    tools = _Recorder()
    with pytest.raises(ArgumentRejected) as caught:
        tools._draw_profile([segment])
    assert needle in str(caught.value)
    assert tools.calls == []


def test_an_empty_profile_and_a_negative_tolerance_are_refused():
    tools = _Recorder()
    with pytest.raises(ArgumentRejected):
        tools._draw_profile([])
    with pytest.raises(ArgumentOutOfRange):
        tools._draw_profile(STADIUM, tolerance=0.0)


def test_the_schema_points_the_two_other_tools_at_this_one():
    definitions = {tool["name"]: tool
                   for tool in SketcherTools(connection=None).get_tool_definitions()}
    profile = definitions["catia_sketch_profile"]
    assert profile["inputSchema"]["required"] == ["segments"]
    assert "36566.37" in profile["description"]
    # The two tools the report tried both have to say where the third route is.
    assert "catia_sketch_profile" in definitions["catia_sketch_spline"]["description"]
    assert "catia_sketch_profile" in definitions["catia_sketch_strokes"]["description"]
