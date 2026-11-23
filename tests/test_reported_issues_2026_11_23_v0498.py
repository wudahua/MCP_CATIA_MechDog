"""0.3.161: the 2026-11-23 report - three "ok:true" calls that built the wrong solid.

The report's three P0s share one shape: the call succeeded, the geometry was not
what was asked for, and only a volume comparison found out.

  P0-1  a 180-degree arc with direction='ccw' came out inside out on 3 of 8 caps
  P0-2  catia_fillet(radius=2) with no edge named built 4 of 12 and said ok:true
  P0-3  run_plan(rollback=true) tried to delete a sketch that was open in the
        editor, and CATIA answered with a modal dialog that froze the session

plus four smaller ones: a semicircle refused as "chord longer than the diameter"
(P1-1), a pad refused on a profile that mixed an arc with a line because the
threshold was finer than the record (P1-2), feature names that do not survive a
rebuild (P1-3), and a legal seam reported as a self-crossing (P2-3).

Every number below is either the report's or computed from the report's.
"""
from __future__ import annotations

import math

import pytest

from catia_mcp.geometry2d import (_arc, _line, crossings, describe,
                                  free_ends_with_gaps, open_ends, segments_of)
from catia_mcp.results import CodedError
from catia_mcp.tools.planner import PlannerTools
from catia_mcp.tools.sketcher import HALF_CIRCLE_TOL, SketcherTools, _sweep_choice


# ── P0-1: the 180-degree cap ─────────────────────────────────────────────
# The report's own numbers, six decimals and all.
CAP_CX, CAP_CY, CAP_R = 211.171943, 18.704630, 6.142572
CAP_P1 = (207.648709, 23.736330)
CAP_P2 = (214.695178, 13.672929)
# What the arc must bulge to. The report prints (206.139879, 15.181915) for its
# own float path; the exact half circle through these ends bulges to the value
# below, 0.00036 mm away - so the test asserts within 1e-3 and the DIRECTION is
# what is being pinned, not the last four digits of somebody's rounding.
CAP_APEX_OUT = (206.139879, 15.181915)


def _angles():
    return (math.atan2(CAP_P1[1] - CAP_CY, CAP_P1[0] - CAP_CX),
            math.atan2(CAP_P2[1] - CAP_CY, CAP_P2[0] - CAP_CX))


def _apex(start, sweep):
    return (CAP_CX + CAP_R * math.cos(start + sweep / 2),
            CAP_CY + CAP_R * math.sin(start + sweep / 2))


def test_the_report_s_arc_is_two_ten_thousandths_of_a_degree_past_half():
    """The premise: 180.0000023 degrees, which is why 1e-9 rad was too tight."""
    a1, a2 = _angles()
    ccw = (a2 - a1) % (2 * math.pi)
    assert math.degrees(ccw) == pytest.approx(180.0000023, abs=1e-6)
    assert ccw - math.pi > 1e-9, (
        "if this failed the old flip would not have fired and the test would "
        "prove nothing")


def test_a_half_circle_ccw_keeps_the_ccw_half():
    """direction='ccw' must bulge to the report's (206.139879, 15.181915)."""
    a1, a2 = _angles()
    start, sweep, ccw = _sweep_choice(a1, a2, "ccw", False)
    assert ccw is True
    assert sweep == pytest.approx(math.pi, abs=1e-12)
    apex = _apex(start, sweep)
    assert math.dist(apex, CAP_APEX_OUT) < 1e-3
    # And it is on the OUTWARD side: the apex is a radius away from the centre,
    # on the opposite side from the other end of the slot.
    assert math.dist(apex, (CAP_CX, CAP_CY)) == pytest.approx(CAP_R, abs=1e-9)


def test_a_half_circle_cw_takes_the_OTHER_half():
    """The control: at 180 degrees the two directions are different geometry.

    Before 0.3.161 both directions returned the same arc here, which is the
    clearest statement of the defect - the direction was not merely unstable, it
    was ignored.
    """
    a1, a2 = _angles()
    start, sweep, ccw = _sweep_choice(a1, a2, "cw", False)
    assert ccw is False
    apex = _apex(start, sweep)
    assert math.dist(apex, CAP_APEX_OUT) == pytest.approx(2 * CAP_R, abs=1e-4)


def test_just_past_half_still_flips_to_the_short_way():
    """The 2026-11-01 rule survives: 181 degrees means the 179-degree arc."""
    start, sweep, ccw = _sweep_choice(math.radians(10), math.radians(191), "ccw",
                                      False)
    assert math.degrees(sweep) == pytest.approx(179.0, abs=1e-9)
    assert ccw is False
    # ... and 179 degrees is left alone.
    _, sweep, ccw = _sweep_choice(math.radians(10), math.radians(189), "ccw", False)
    assert math.degrees(sweep) == pytest.approx(179.0, abs=1e-9)
    assert ccw is True


def test_major_true_still_keeps_a_sweep_past_half():
    _, sweep, ccw = _sweep_choice(math.radians(0), math.radians(270), "ccw", True)
    assert math.degrees(sweep) == pytest.approx(270.0, abs=1e-9)
    assert ccw is True


def test_the_half_circle_band_is_wide_enough_and_still_narrow():
    """1e-6 rad has to cover the report's 4e-8 and stay under a real angle."""
    assert HALF_CIRCLE_TOL >= 1e-7, "the report's own noise was 4e-08 rad"
    assert math.degrees(HALF_CIRCLE_TOL) < 1e-3, "0.000057 degrees at 1e-6"
    a1, a2 = _angles()
    _, sweep, _ = _sweep_choice(a1, a2, "ccw", False)
    assert sweep == math.pi, "a sweep this close to pi is EXACTLY pi"


def test_the_arc_reply_prints_where_it_bulges():
    """Suggestion 3 of the report: the apex, so the half can be read back."""
    tools = SketcherTools(connection=None)

    class _Curve:
        def __init__(self):
            self.args = None

        def __getattr__(self, item):
            return lambda *a, **k: None

    class _Factory:
        def __init__(self):
            self.calls = []

        def CreateCircle(self, *args):
            self.calls.append(args)
            return _Curve()

    tools._active_factory = _Factory()
    tools._active_sketch = object()
    tools._ensure_sketch_open = lambda: None          # type: ignore[assignment]
    tools._note_ends = lambda *a, **k: None           # type: ignore[assignment]
    tools._note_element = lambda *a, **k: None        # type: ignore[assignment]

    reply = tools._draw_arc(CAP_CX, CAP_CY, CAP_R, p1=list(CAP_P1),
                            p2=list(CAP_P2), direction="ccw")
    assert "bulge apex (206.1402, 15.1814)" in reply
    assert "decides WHICH half" in reply
    assert "counter-clockwise" in reply


def test_the_schema_no_longer_claims_major_is_irrelevant_at_a_half_circle():
    """The sentence that made the reporter think direction='ccw' was safe."""
    schema = next(tool for tool in SketcherTools(connection=None).get_tool_definitions()
                  if tool["name"] == "catia_sketch_arc")
    text = schema["inputSchema"]["properties"]["major"]["description"]
    assert "NO effect on a request that is already a half circle" not in text
    assert "decides WHICH of the two semicircles" in text
    assert "bulge apex" in text


# ── P1-1: chord == diameter is a half circle, not a contradiction ────────

def test_a_semicircle_whose_chord_is_a_whisker_over_the_diameter_is_drawn():
    """The report's R30 cap: ends 60.00000075 apart, refused as "no arc"."""
    tools = SketcherTools(connection=None)
    plan = tools._plan_segment(3, {
        "type": "arc", "center": [642.740917, 320.892479], "radius": 30,
        "p1": [625.533624, 345.467040], "p2": [659.948211, 296.317917],
        "direction": "cw"})
    assert plan["kind"] == "arc"
    assert plan["adjust"], "the ends move onto the circle, and it says so"
    note = plan["adjust"][0]
    assert "KEPT" in note
    # A number that can be read: the note used to print 0.0000 mm.
    assert "e-0" in note


def test_a_chord_that_is_really_too_long_is_still_refused():
    """The control: 0.1 mm past the diameter is geometry, not rounding."""
    tools = SketcherTools(connection=None)
    with pytest.raises(CodedError) as caught:
        tools._plan_segment(1, {
            "type": "arc", "center": [0, 0], "radius": 30,
            "p1": [-30.05, 0], "p2": [30.05, 0]})
    assert caught.value.code == "ARC_RADIUS_MISMATCH"
    assert "1e-05" in str(caught.value), (
        "the refusal has to print the threshold it used, or the caller cannot "
        "tell how far over the line they are")


# ── P1-2: the open-curve threshold ───────────────────────────────────────

def test_a_micron_seam_between_an_arc_and_a_line_is_not_an_open_profile():
    """The gap `catia_sketch_arc` leaves when it projects p1/p2 onto the circle.

    A three-segment closed shape - the arc's upper half, then the two straight
    runs that come back to its ends - with the arc's own ends 2e-6 mm off the
    lines that meet them. That is the size of gap the report had to disable the
    guard for; before 0.3.161 the threshold was 1e-6 and this was "an open
    profile".
    """
    arc = _arc(1, "arc #1", 0.0, 0.0, 10.0, 0.0, math.pi)     # (10,0) -> (-10,0)
    left = _line(2, "line #2", -10.000002, 0.0, 0.0, -12.0)
    right = _line(3, "line #3", 0.0, -12.0, 9.999998, 0.0)
    assert open_ends([arc, left, right], tol=1e-6), (
        "the old threshold has to reproduce the refusal, or this proves nothing")
    assert open_ends([arc, left, right]) == []


def test_a_real_gap_is_still_reported_with_its_size():
    """Every free end, with how far its nearest neighbour is - all four here."""
    arc = _arc(1, "arc #1", 0.0, 0.0, 10.0, 0.0, math.pi)
    line = _line(2, "line #2", -10.0, 0.5, -10.0, 20.0)
    ends = free_ends_with_gaps([arc, line])
    assert len(ends) == 4, "two elements that meet at neither end: four free ends"
    by_point = {entry["point"]: entry for entry in ends}
    assert by_point[(-10.0, 0.0)]["nearest_mm"] == pytest.approx(0.5, abs=1e-12)
    assert by_point[(-10.0, 0.0)]["nearest_label"] == "line #2"
    assert by_point[(10.0, 0.0)]["nearest_mm"] == pytest.approx(20.0062, abs=1e-4)


def test_the_refusal_prints_the_distance_and_the_threshold():
    """It used to print the same coordinate twice, four decimals deep."""
    from catia_mcp.results import PreconditionRejected
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)

    class _Sketch:
        Name = "SK_Main"

    tools._sketch_open_ends = lambda name: ([                # type: ignore
        {"point": (659.9482, 296.3179), "nearest_mm": 0.00032,
         "nearest_label": "line #10"},
        {"point": (659.9482, 296.3179), "nearest_mm": 0.00032,
         "nearest_label": "line #10"},
    ], 16)
    with pytest.raises(PreconditionRejected) as caught:
        tools._refuse_open_curves(_Sketch(), allow=False)
    message = str(caught.value)
    assert "3.200e-04 mm away" in message
    assert "0.001 mm or less counts as JOINED" in message
    assert caught.value.data["joined_below_mm"] == 0.001
    assert caught.value.data["widest_gap_mm"] == pytest.approx(0.00032)


# ── P2-3: a seam is not a crossing ───────────────────────────────────────

def test_a_three_micron_seam_at_a_shared_end_is_a_seam_not_a_crossing():
    """The report's slot: 'arc #1 crosses line #10 at (207.6510, 23.7380)'.

    That point is arc #1's start / line #10's end. The record puts them 0.0032 mm
    apart because an arc's ends are the ones CATIA drew (the projection of
    p1/p2), so the seam the report drew is 3.2e-3 mm wide - and 1e-3 read it as
    a crossing. The same shape 1 mm wide is still a TOUCH, below a crossing.
    """
    line = _line(1, "line #1", 0.0, 0.0, 10.0, 0.0)
    elbow = _line(10, "line #10", 9.9968, 0.0, 9.9968, 10.0)
    found = crossings([line, elbow])
    assert [item["kind"] for item in found] == ["seam"]
    assert found[0]["gap_a_mm"] == pytest.approx(0.0032, abs=1e-12)
    assert found[0]["gap_b_mm"] == pytest.approx(0.0, abs=1e-12)
    assert "SEAMS, not crossings" in describe(found, 2)
    assert "CROSSES ITSELF" not in describe(found, 2)
    assert "3.200e-03 mm" in describe(found, 2)

    # The control: the same elbow 0.05 mm short is a T junction, not a seam.
    wider = _line(10, "line #10", 9.95, 0.0, 9.95, 10.0)
    classified = crossings([line, wider])
    assert [item["kind"] for item in classified] == ["touch"]


def test_a_figure_eight_is_still_a_crossing_and_still_says_so():
    segs = segments_of([
        {"kind": "line", "x1": 0.0, "y1": 0.0, "x2": 10.0, "y2": 10.0},
        {"kind": "line", "x1": 10.0, "y1": 10.0, "x2": 0.0, "y2": 10.0},
        {"kind": "line", "x1": 0.0, "y1": 10.0, "x2": 10.0, "y2": 0.0},
        {"kind": "line", "x1": 10.0, "y1": 0.0, "x2": 0.0, "y2": 0.0},
    ])
    found = crossings(segs)
    assert [item["kind"] for item in found] == ["crossing"]
    assert "CROSSES ITSELF" in describe(found, len(segs))


# ── P0-2: the enumerated fillet that only did 4 of 12 ────────────────────

def _fillet_tools(count: int):
    from catia_mcp.tools.part_design import PartDesignTools

    class _Body:
        Name = "零件几何体"

    class _Part:
        MainBody = _Body()

    class _Conn:
        def ensure_connected(self) -> None:
            pass

        def get_active_part(self) -> _Part:
            return _Part()

        def refresh_display(self) -> None:
            pass

    tools = PartDesignTools(connection=None)
    tools.conn = _Conn()
    tools._prepare_body = lambda part: _Body()          # type: ignore[assignment]
    tools._clear_selection = lambda: None               # type: ignore[assignment]
    tools._edge_table = lambda body: [                  # type: ignore[assignment]
        {"index": index, "name": f"Edge.{index}", "cell": object(),
         "nls": "", "bounded_by": ["Pad.1;2", "Sketch.1;1"],
         "length": 25.0, "fingerprint": f"L25.0|Pad.1;2|Sketch.1;1"}
        for index in range(1, count + 1)
    ]
    return tools


def test_the_all_edges_fillet_refuses_to_report_4_of_12_as_success():
    """The report's exact call: a 12-edge box, radius=2, no edge named."""
    tools = _fillet_tools(12)
    seen = {}

    def fake(radius, specs, propagation=0, sequential_retry=True, strict=True):
        seen["strict"] = strict
        raise CodedError("Fillet R2: 4/12 edge(s) done (Edge.1, Edge.2, ...)",
                         code="PARTIAL_EDGES_FAILED", modified=True,
                         stage="execute",
                         data={"requested": 12, "done": 4,
                               "applied": ["Edge.1"], "rejected": []},
                         next_actions=["catia_list_edges shows each edge"])

    tools._fillet_many = fake                           # type: ignore[assignment]
    with pytest.raises(CodedError) as caught:
        tools._fillet_all_edges(2, 0, 200)
    assert seen["strict"] is True, "the enumerated path must not opt out"
    error = caught.value
    assert error.code == "PARTIAL_EDGES_FAILED"
    assert error.modified is True, "four fillets DID build - that fact survives"
    assert error.data["self_enumerated"] is True
    assert error.data["done"] == 4
    assert "CHOSEN BY THE TOOL, NOT BY YOU" in str(error)
    assert "4/12 edge(s) done" in str(error)
    # The arithmetic paragraph travels with the refusal, not just with success.
    assert "were OFFERED to one fillet each" in str(error)
    assert "tangency" in " ".join(error.next_actions)


def test_the_chamfer_all_edges_path_does_the_same():
    tools = _fillet_tools(12)

    def fake(length, angle, specs, sequential_retry=True, strict=True,
             length2=None):
        raise CodedError("Chamfer 2: 3/12 edge(s) done",
                         code="PARTIAL_EDGES_FAILED", modified=True,
                         stage="execute", data={"done": 3, "requested": 12})

    tools._chamfer_many = fake                          # type: ignore[assignment]
    tools._mass_probe = lambda part, body: (None, None)  # type: ignore[assignment]
    with pytest.raises(CodedError) as caught:
        tools._chamfer_all_edges(2, 45, 200)
    assert caught.value.data["self_enumerated"] is True
    assert "CHOSEN BY THE TOOL" in str(caught.value)


# ── P0-3: the rollback that froze CATIA behind a modal dialog ────────────

class _RollbackConn:
    def __init__(self, open_sketch=None):
        self.open_sketch = open_sketch
        self.closed: list[str] = []

    def refresh_display(self) -> None:
        pass


def _planner(conn, delete_reply: str = "deleted"):
    planner = PlannerTools(conn)

    class _Handler:
        def __init__(self, name):
            self.name = name
            self.calls: list[dict] = []

        def execute(self, tool, arguments):
            self.calls.append(dict(arguments))
            if tool == "catia_close_sketch":
                conn.open_sketch = None
                conn.closed.append("closed")
                return "Sketch closed"
            return delete_reply

    delete = _Handler("catia_delete_feature")
    close = _Handler("catia_close_sketch")
    planner.bind({"catia_delete_feature": delete, "catia_close_sketch": close})
    planner._snapshot_names = lambda: {"document": "P", "names": []}  # type: ignore
    return planner, delete, close


def test_a_rollback_closes_the_open_sketch_before_deleting():
    """CATIA answers a delete of an open sketch with a modal dialog; this is why."""
    conn = _RollbackConn(open_sketch="SK_Slots")
    planner, delete, close = _planner(conn)
    report = planner._rollback(["SK_Slots"])
    assert conn.closed == ["closed"], "the sketch has to be closed first"
    assert report["open_sketch_closed"] == "SK_Slots"
    assert report["verified"] is True


def test_a_rollback_with_nothing_open_says_nothing_about_sketches():
    conn = _RollbackConn(open_sketch=None)
    planner, delete, close = _planner(conn)
    report = planner._rollback(["SK_Slots"])
    assert "open_sketch_closed" not in report
    assert conn.closed == []


def test_a_sketch_that_will_not_close_is_named_as_still_open():
    conn = _RollbackConn(open_sketch="SK_Slots")
    planner, delete, close = _planner(conn)

    def refuse(tool, arguments):
        raise RuntimeError("close refused")

    close.execute = refuse                              # type: ignore[assignment]
    report = planner._rollback(["SK_Slots"])
    assert report["open_sketch_still_open"] == "SK_Slots", (
        "'I tried to close it' and 'it is closed' are different facts")
    assert "open_sketch_closed" not in report


def test_deleting_the_open_sketch_by_name_is_refused_before_the_dialog():
    """The standalone delete asks first, so CATIA never puts the box up."""
    from catia_mcp.tools.part_design import PartDesignTools

    # _delete_one's lookup is long; go straight at the guard it now opens with.
    source = __import__("inspect").getsource(PartDesignTools._delete_one)
    assert "SKETCH_IS_OPEN" in source
    assert source.index("SKETCH_IS_OPEN") < source.index("sel.Delete()"), (
        "the refusal must come BEFORE the delete is attempted - that is the "
        "whole point, the dialog is what freezes the session")


# ── P1-3: names that do not survive a rebuild ────────────────────────────

class _Item:
    def __init__(self, name):
        self.Name = name


class _Collection:
    def __init__(self, items):
        self._items = list(items)

    @property
    def Count(self):
        return len(self._items)

    def Item(self, index):
        return self._items[index - 1]


class _TreeBody:
    def __init__(self, name, shapes=(), sketches=(), hybrids=()):
        self.Name = name
        self.Shapes = _Collection(_Item(n) for n in shapes)
        self.Sketches = _Collection(_Item(n) for n in sketches)
        self.HybridShapes = _Collection(_Item(n) for n in hybrids)


class _TreePart:
    def __init__(self, bodies, sets=()):
        self.Bodies = _Collection(bodies)
        self.HybridBodies = _Collection(sets)


def test_a_wildcard_is_expanded_against_the_live_tree():
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    part = _TreePart([_TreeBody("零件几何体",
                                shapes=["凸台.1", "倒圆角.13", "倒圆角.14",
                                        "凹槽.1", "倒圆角.18"],
                                sketches=["SK_1"])])
    expanded, notes = tools._expand_name_patterns(part, ["倒圆角.*"])
    assert expanded == ["倒圆角.13", "倒圆角.14", "倒圆角.18"]
    assert "matched 3 element(s)" in notes[0]
    assert "倒圆角.13" in notes[0]


def test_a_pattern_that_matches_nothing_is_refused_not_silently_empty():
    from catia_mcp.results import ArgumentRejected
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    part = _TreePart([_TreeBody("零件几何体", shapes=["凸台.1"])])
    with pytest.raises(ArgumentRejected) as caught:
        tools._expand_name_patterns(part, ["倒圆角.6*"])
    assert caught.value.code == "PATTERN_MATCHED_NOTHING"
    assert "凸台.1" in str(caught.value), "it lists what IS there"


def test_names_without_a_wildcard_are_left_exactly_as_given():
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    expanded, notes = tools._expand_name_patterns(
        _TreePart([]), ["凸台.1", "SK_1"])
    assert expanded == ["凸台.1", "SK_1"]
    assert notes == []


def test_a_name_matched_twice_is_deleted_once():
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    part = _TreePart([_TreeBody("零件几何体", shapes=["倒圆角.1"])])
    expanded, _notes = tools._expand_name_patterns(part, ["倒圆角.*", "倒圆角.1"])
    assert expanded == ["倒圆角.1"]
