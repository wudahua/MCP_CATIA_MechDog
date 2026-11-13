"""0.3.113: the seam had two standards, and the rest of the ninth-plus report.

The 2026-11-12 report (its namesake at 0.3.112 was the ninth) measured a 73-segment
profile and got two answers three seconds apart:

    catia_sketch_profile   "every one of the 73 junction(s) meets within 0.0000 mm"
    catia_close_sketch     "not closed - 60 loose endpoint(s) in 30 break(s);
                            smallest gap 0.0043, widest 0.0960"

Both were right about their own numbers. An arc is created from centre + radius +
angles, so the point CATIA draws is the RADIAL PROJECTION of the caller's p1 onto
that circle; profile compared the p1 it was given, close_sketch compared what was
drawn. Reproduced on B30 2026-11-12 with a 4-element stadium whose arc ends sat
0.05 mm off the circle: profile said "within 0.0000 mm", close_sketch said
"8 loose endpoint(s) in 4 break(s), 0.0500 mm".

What these tests pin, without CATIA:
  1  _sweep_choice is one rule, and _plan_segment's midpoint uses it
  2  _circle_through passes through its three points and refuses collinear ones
  3  an arc whose ends are off the circle gets a circle rebuilt THROUGH them
  4  the junction gap after that rebuild is zero, not "within tolerance"
  5  an arc whose ends are already on the circle is left alone and says nothing
  6  a wild radius mismatch is NOT rebuilt - it says where the ends will land
  7  a seam that meets at an endpoint is a "touch", not a crossing
  8  a figure-eight is still a "crossing"
  9  'CATIA' reports the I's serif as a touch and never says CROSSES ITSELF
 10  the text box is always reported, and -0.000 is written as 0.000
 11  deleting a sketch that is still open says SKETCH_IS_OPEN
 12  and the fallback no longer asserts "dead reference" as fact
 13  a message box raises a DEGRADED marker a caller can grep
 14  the schemas carry what the report had to learn by trial
"""
from __future__ import annotations

import math

import pytest

from catia_mcp.geometry2d import _arc, _line, crossings, describe, segments_of
from catia_mcp.results import CodedError
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.sketcher import (
    SketcherTools,
    _circle_through,
    _mm,
    _point_gap,
    _sweep_choice,
    _stroke_box,
)

# ── §1 the sweep rule, in one place ────────────────────────────────────────
def test_the_sweep_rule_is_shared_between_the_builder_and_the_planner() -> None:
    start, sweep, ccw = _sweep_choice(0.0, math.pi / 2, "ccw", False)
    assert (round(math.degrees(start)), round(math.degrees(sweep)), ccw) == (0, 90, True)
    # Asked clockwise from the same pair, the short way wins: the arc still runs
    # 0 -> 90 ccw. This is the flip the 2026-11-01 report paid for.
    start, sweep, ccw = _sweep_choice(0.0, math.pi / 2, "cw", False)
    assert (round(math.degrees(start)), round(math.degrees(sweep)), ccw) == (0, 90, True)
    # major=true is how a caller says the long way IS what was meant.
    start, sweep, ccw = _sweep_choice(0.0, math.pi / 2, "cw", True)
    assert (round(math.degrees(start)), round(math.degrees(sweep)), ccw) == (90, 270, False)


# ── §2 a circle through three points ──────────────────────────────────────
def test_the_circle_through_three_points_passes_through_all_three() -> None:
    points = [(20.0, 0.0), (20.0 * math.cos(math.pi / 4), 20.0 * math.sin(math.pi / 4)),
              (0.0, 20.0)]
    center, radius = _circle_through(*points)
    for point in points:
        assert _point_gap(point, center) == pytest.approx(radius, abs=1e-9)


def test_collinear_points_have_no_circle() -> None:
    assert _circle_through((0.0, 0.0), (5.0, 0.0), (10.0, 0.0)) is None


# ── §3/§4/§5/§6 the planner resolves the ends that will be DRAWN ─────────
def _stadium(off: float) -> list[dict]:
    r = 20.0 + off
    return [
        {"type": "line", "p1": [r, 0.0], "p2": [60.0 - r, 0.0]},
        {"type": "arc", "center": [60.0, 0.0], "radius": 20.0,
         "p1": [60.0 - r, 0.0], "p2": [60.0, r], "direction": "ccw"},
        {"type": "line", "p1": [60.0, r], "p2": [0.0, r]},
        {"type": "arc", "center": [0.0, 0.0], "radius": 20.0,
         "p1": [0.0, r], "p2": [r, 0.0], "direction": "ccw"},
    ]


def test_an_arc_off_its_circle_gets_a_circle_through_its_endpoints() -> None:
    tools = SketcherTools(connection=None)
    plan = [tools._plan_segment(i, raw)
            for i, raw in enumerate(_stadium(0.05), start=1)]
    arc = plan[1]
    # The ends the caller gave are the ends the plan now carries...
    assert _point_gap(arc["start"], (39.95, 0.0)) == pytest.approx(0.0, abs=1e-9)
    assert _point_gap(arc["end"], (60.0, 20.05)) == pytest.approx(0.0, abs=1e-9)
    # ...and both really sit on the rebuilt circle.
    assert _point_gap(arc["start"], arc["center"]) == pytest.approx(arc["radius"], abs=1e-9)
    assert arc["radius"] > 20.0
    assert any("rebuilt" in note for note in arc["adjust"])


def test_after_the_rebuild_every_junction_is_exactly_zero() -> None:
    tools = SketcherTools(connection=None)
    plan = [tools._plan_segment(i, raw)
            for i, raw in enumerate(_stadium(0.05), start=1)]
    gaps = [_point_gap(plan[i]["end"], plan[(i + 1) % len(plan)]["start"])
            for i in range(len(plan))]
    assert max(gaps) == pytest.approx(0.0, abs=1e-12)


def test_ends_already_on_the_circle_are_left_alone_and_not_reported() -> None:
    tools = SketcherTools(connection=None)
    plan = [tools._plan_segment(i, raw)
            for i, raw in enumerate(_stadium(0.0), start=1)]
    assert plan[1]["radius"] == pytest.approx(20.0, abs=1e-12)
    assert plan[1]["adjust"] == []


def test_a_radius_that_would_change_by_more_than_5_percent_is_not_rebuilt() -> None:
    """A wild mismatch is bad data, not rounding: say where the ends land."""
    tools = SketcherTools(connection=None)
    # Same two ends, but told they sit on a R12 circle - the rebuild would need
    # R20.17, so the caller's circle is kept and the ends are reported as moved.
    plan = [tools._plan_segment(1, {"type": "arc", "center": [60.0, 0.0],
                                    "radius": 12.0, "p1": [39.95, 0.0],
                                    "p2": [60.0, 20.05], "direction": "ccw"})]
    arc = plan[0]
    assert arc["radius"] == pytest.approx(12.0)
    assert any("NOT rebuilt" in note for note in arc["adjust"])
    assert _point_gap(arc["start"], (39.95, 0.0)) > 1.0        # it moved, as said


# ── §7/§8/§9 what counts as crossing itself ──────────────────────────────
def test_a_seam_that_meets_at_an_endpoint_is_a_touch_not_a_crossing() -> None:
    one = _line(1, "line #1", 0.0, 0.0, 10.0, 0.0)
    # 0.0043 mm past the corner: the gap the report measured at its widest seam
    # after the circles were fixed, and what the letter I does on purpose.
    two = _line(2, "line #2", 5.0, 0.0, 5.0, 10.0)
    found = crossings([one, two])
    assert [item["kind"] for item in found] == ["touch"]
    assert "CROSSES ITSELF" not in describe(found, 2)
    assert "ENDS on another" in describe(found, 2)


def test_a_figure_eight_is_still_a_crossing() -> None:
    segs = segments_of([
        {"kind": "line", "x1": 0.0, "y1": 0.0, "x2": 10.0, "y2": 10.0},
        {"kind": "line", "x1": 10.0, "y1": 10.0, "x2": 0.0, "y2": 10.0},
        {"kind": "line", "x1": 0.0, "y1": 10.0, "x2": 10.0, "y2": 0.0},
        {"kind": "line", "x1": 10.0, "y1": 0.0, "x2": 0.0, "y2": 0.0},
    ])
    found = crossings(segs)
    assert [item["kind"] for item in found] == ["crossing"]
    assert "CROSSES ITSELF" in describe(found, len(segs))


def test_the_letter_i_in_catia_is_reported_as_a_touch() -> None:
    from catia_mcp.fonts import text_to_strokes

    strokes = text_to_strokes("CATIA", 20).strokes
    entries = [{"kind": "line", "x1": stroke[k][0], "y1": stroke[k][1],
                "x2": stroke[k + 1][0], "y2": stroke[k + 1][1]}
               for stroke in strokes for k in range(len(stroke) - 1)]
    segs = segments_of(entries)
    found = crossings(segs)
    assert found, "the serif bar does meet the stem"
    assert {item["kind"] for item in found} == {"touch"}
    said = describe(found, len(segs))
    assert "CROSSES ITSELF" not in said
    assert "BRANCHED" in said


# ── §10 the text box ─────────────────────────────────────────────────────
def test_the_text_box_is_measured_from_the_points() -> None:
    assert _stroke_box([[[0, 0], [10, 5]], [[-2, 3], [4, 9]]]) == (-2.0, 10.0, 0.0, 9.0)
    assert _stroke_box([]) == (0.0, 0.0, 0.0, 0.0)


def test_a_round_off_just_below_zero_is_written_as_zero() -> None:
    """'centred on (-0.000, 0.000)' is a correct number that reads as a minus."""
    assert _mm(-1e-9) == "0.000"
    assert _mm(-0.0004) == "0.000"          # rounds to zero at three decimals
    assert _mm(-0.004) == "-0.004"          # a real four-thousandth keeps its sign


# ── §11/§12 deleting a sketch that is still open ─────────────────────────
class _Item:
    def __init__(self, name: str) -> None:
        self.Name = name


class _Collection:
    def __init__(self, items: list) -> None:
        self._items = list(items)
        self.Count = len(self._items)

    def Item(self, key):                                   # noqa: N802 - COM name
        if isinstance(key, int):
            return self._items[key - 1]
        for item in self._items:
            if str(item.Name) == str(key):
                return item
        raise KeyError(key)


class _Body:
    def __init__(self, sketch: str) -> None:
        self.Name = "零件几何体"
        self.Shapes = _Collection([])
        self.Sketches = _Collection([_Item(sketch)])
        self.HybridBodies = _Collection([])
        self.HybridShapes = _Collection([])


class _Part:
    def __init__(self, sketch: str) -> None:
        self.MainBody = _Body(sketch)
        self.Bodies = _Collection([self.MainBody])


class _Selection:
    def Clear(self) -> None:                               # noqa: N802 - COM name
        pass

    def Add(self, _thing) -> None:                         # noqa: N802 - COM name
        pass

    def Delete(self) -> None:                              # noqa: N802 - COM name
        raise RuntimeError("(-2147352567, '发生意外。', (0, 'CATIASelection', "
                           "'方法 Delete 失败', None, 0, -2147467259), None)")


class _Conn:
    def __init__(self, part, open_sketch) -> None:
        self._part = part
        self.hso = _Selection()
        self.open_sketch = open_sketch

    def ensure_connected(self) -> None:
        pass

    def get_active_part(self):
        return self._part

    def refresh_display(self) -> None:
        pass


def _delete_tools(open_sketch):
    part = _Part("草图.1")
    tools = PartDesignTools(connection=None)
    tools.conn = _Conn(part, open_sketch)
    tools._sketch_names = lambda: ["草图.1"]                # type: ignore[assignment]
    return tools


def test_deleting_the_sketch_this_session_has_open_says_so() -> None:
    tools = _delete_tools("草图.1")
    with pytest.raises(CodedError) as caught:
        tools._delete_one({"feature_name": "草图.1"}, None)
    error = caught.value
    assert error.code == "SKETCH_IS_OPEN"
    said = str(error)
    assert "catia_close_sketch" in said
    # The advice that cost the reporter their work must be gone.
    assert "dead reference" not in said
    assert "Do NOT close or re-open the document" in said


def test_the_fallback_stops_asserting_a_dead_reference() -> None:
    tools = _delete_tools(None)
    with pytest.raises(CodedError) as caught:
        tools._delete_one({"feature_name": "草图.1"}, None)
    error = caught.value
    assert error.code == "DELETE_FAILED"
    said = str(error)
    assert "did NOT have that sketch open" in said
    assert "likeliest cause" in said


# ── §13 the message box leaves a marker ──────────────────────────────────
def test_a_message_box_marks_the_result_degraded() -> None:
    from catia_mcp.tools.gsd import _answered_boxes

    assert "dialogs answered: 0" in _answered_boxes("Engraved.", 0)
    marked = _answered_boxes("Engraved.", 1)
    assert "DEGRADED" in marked
    assert "catia_gsd_list_elements" in marked          # what to re-check
    assert "COULD NOT BE READ BACK" in marked


# ── §14 the schemas say what the report learned the hard way ─────────────
def _schema(name: str) -> dict:
    return next(tool for tool in SketcherTools(connection=None).get_tool_definitions()
                if tool["name"] == name)


def test_text_strokes_documents_the_baseline_axis_and_returns_a_box() -> None:
    text = _schema("catia_text_strokes")
    said = text["description"]
    assert "BASELINE RUNS ALONG THE SKETCH'S OWN +H AXIS" in said
    assert "'zx'" in said and "standing up" in said
    properties = text["inputSchema"]["properties"]
    assert "center_at" in properties
    assert "bounding box" in properties["center_at"]["description"].lower()


def test_pocket_warns_that_both_makes_two_features() -> None:
    """0.3.123: the claim became conditional, because it was measured both ways.

    A pocket with direction='both' on a 40x40x10 block added ONE feature
    ("5.000 mm total, 2.500 each side"), while the same call from a sketch plane
    INSIDE the material (offset=5) added TWO ("凹槽.1 + 凹槽.2, two one-sided
    cuts"). The old text stated the two-feature case as if it were unconditional,
    and also left 'depth' ambiguous for 'both' - the 2026-11-13 report cut 14 mm
    expecting 14 per side and got 7.
    """
    schema = next(tool for tool in PartDesignTools(connection=None).get_tool_definitions()
                  if tool["name"] == "catia_pocket")
    said = schema["inputSchema"]["properties"]["direction"]["description"]
    assert "TOTAL, on 'both' AS WELL" in said
    assert "TWO when the" in said and "one, when the sketch plane sits" in said


def test_the_engrave_note_names_both_causes_of_a_shortfall() -> None:
    from catia_mcp.tools.gsd import _engrave_outcome

    _kind, note, _tail = _engrave_outcome(0.81, 1.0, 0.4)
    assert "cannot tell them apart" in note
    assert "already gone" in note
    assert "does not follow the surface perfectly" not in note


# ── §13b a box answered before the call FAILED is still a box ─────────────
def test_a_box_answered_before_a_failure_reaches_the_error(monkeypatch) -> None:
    """The count used to be appended only on the success path."""
    import contextlib

    from catia_mcp import dialogs as dialogs_module
    from catia_mcp.tools.gsd import GSDTools

    @contextlib.contextmanager
    def one_box(**_kwargs):
        answered = dialogs_module.Answered()
        answered.clicks = 1
        yield answered

    monkeypatch.setattr("catia_mcp.tools.gsd.dialogs.watch", one_box)
    tools = GSDTools(connection=None)

    def boom(_args):
        raise RuntimeError("the boolean refused")

    tools._surface_engrave = boom                        # type: ignore[assignment]
    with pytest.raises(RuntimeError) as caught:
        tools.execute("catia_surface_engrave", {"curve": "X", "depth": 0.4})
    said = str(caught.value)
    assert "the boolean refused" in said
    assert "DEGRADED" in said
