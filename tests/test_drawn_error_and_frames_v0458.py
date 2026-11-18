"""2026-11-18 (0.3.147) report: the bogus drawn error, the doubling mirror, and
the frame criterion that never fired.

The three findings are answered differently, because they are different kinds of
claim:

* P0 - ``catia_sketch_spline`` reported a drawn error of 99 900 mm for a 100 mm
  radius. It was arithmetic, not CATIA: ``frame_of`` handed ``point_world`` the
  caller's raw direction vector, so ``u=[1000, 0, 0]`` multiplied every sketch
  coordinate by 1000 and the probe points were placed a thousand times too far
  away. Measured on B30 2026-11-18: the same curve on a unit-u frame reports
  0.0000 mm (probe v0459, cell E), and the pad volume was always right
  (78 539.8 mm3 against 78 539.816, -0.00002 %) - the sketch was never wrong.
* P1 - "the mirror counts an overlapping solid twice" is REFUTED by measurement.
  36 000 -> 40 000 mm3 on a body the plane really cuts through, and 132 000 ->
  264 000 on one that sits entirely at z >= 0 (probe v0459 cells B and H): a
  doubling is what a disjoint copy gives, not a double count. The reply now
  measures the envelope and says which case it found.
* P2 - ``cleanup_frame_datum="auto"`` asked "are there other sketches", which is
  true in every real part. It now asks "is another sketch on MY plane", which
  ``Sketch.GetAbsoluteAxisData`` answers.
"""
from __future__ import annotations

import pytest

from catia_mcp import sketch_frames
from catia_mcp.tools.planner import PlannerTools
from catia_mcp.tools.sketcher import SketcherTools

SP15 = [(37.214626, 336.867322), (49.398155, 391.693203), (90.0, 454.321087)]


# ── P0: a direction vector is a direction, whatever its length ────────────
def test_a_thousand_millimetre_u_is_still_a_unit_direction():
    placement = {"base": "frame", "origin": [0.0, 0.0, 1000.0],
                 "u": [1000.0, 0.0, 0.0], "v": [0.0, 1000.0, 0.0]}
    origin, u, v = sketch_frames.frame_of(placement)
    assert origin == [0.0, 0.0, 1000.0]
    assert u == [1.0, 0.0, 0.0] and v == [0.0, 1.0, 0.0]


def test_the_probe_point_of_a_scaled_frame_is_where_the_sketch_says():
    # This is the number that turned into "99 900 mm": sketch (100, 0) on a frame
    # whose u is 1000 mm long. It has to be 100 mm from the origin, not 100 000.
    placement = {"base": "frame", "origin": [0.0, 0.0, 1000.0],
                 "u": [1000.0, 0.0, 0.0], "v": [0.0, 1000.0, 0.0]}
    assert sketch_frames.point_world(placement, 100.0, 0.0) == [100.0, 0.0, 1000.0]


def test_a_long_u_and_a_unit_u_describe_the_same_plane():
    long_u = {"base": "frame", "origin": [0.0, 0.0, 0.0],
              "u": [1000.0, 0.0, 0.0], "v": [0.0, 1000.0, 0.0]}
    unit_u = {"base": "frame", "origin": [0.0, 0.0, 0.0],
              "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0]}
    assert sketch_frames.frame_of(long_u) == sketch_frames.frame_of(unit_u)


def test_a_zero_direction_is_no_frame_rather_than_a_divided_by_zero():
    placement = {"base": "frame", "origin": [0.0, 0.0, 0.0],
                 "u": [0.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0]}
    assert sketch_frames.frame_of(placement) is None
    assert sketch_frames.point_world(placement, 1.0, 1.0) is None


def test_a_scaled_frame_still_maps_the_datum_planes_correctly():
    # The regression this guard exists for: 'xy' at 25 mm has always been right,
    # and normalising the frame branch must not touch it.
    placement = {"base": "xy", "distance": 25.0}
    assert sketch_frames.point_world(placement, 20.0, 6.0) == [20.0, 6.0, 25.0]


# ── P0's belt: a reading bigger than the curve is not a reading ───────────
def test_a_reading_larger_than_the_probe_span_is_refused_not_published():
    """The number must not reach the caller as a deviation.

    The 99 900 mm case is exactly this shape: the probe points spanned 141 mm and
    the reading claimed 99 900. The guard is arithmetic on the probes themselves,
    so it holds for any future frame bug as well.
    """
    class _Measurable:
        def __init__(self, value):
            self.value = value

        def GetMinimumDistance(self, _other):
            return self.value

    class _Spa:
        def GetMeasurable(self, _ref):
            return _Measurable(99_900.0)

    class _Document:
        def GetWorkbench(self, _name):
            return _Spa()

    class _Point:
        def __init__(self, coords):
            self.coords = coords

    class _Factory:
        def __init__(self):
            self.calls = 0

        def AddNewPointCoord(self, x, y, z):
            self.calls += 1
            return _Point((x, y, z))

    class _Body:
        Name = "Body"

        def InsertHybridShape(self, _point):
            pass

    class _Part:
        InWorkObject = None
        HybridShapeFactory = _Factory()

        def UpdateObject(self, _point):
            pass

        def CreateReferenceFromObject(self, node):
            return ("ref", id(node))

    class _Conn:
        active_document = _Document()

        def get_active_part_body(self):
            return _Body()

        class _Sel:
            def Clear(self):
                pass

            def Add(self, _node):
                pass

            def Delete(self):
                pass

        hso = _Sel()

    sketcher = SketcherTools(_Conn())
    sketcher._last_drawn_spline = object()
    sketcher._sketch_part = _Part()
    sketcher._last_placement = {"base": "frame", "origin": [0.0, 0.0, 0.0],
                                "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0]}
    measured = sketcher._measure_drawn_curve([(0.0, 0.0), (100.0, 0.0),
                                             (100.0, 100.0)])
    assert measured["checked"] == 3
    assert measured["max_mm"] is None, (
        "99 900 mm cannot be a distance to a curve that spans 141 mm")
    assert measured["rejected_mm"] == pytest.approx(99_900.0)
    assert "not a deviation" in measured["why"]


# ── P2: the plane is the criterion, and it is read, not assumed ───────────
def test_the_axis_route_returns_one_origin_and_two_axes_per_sketch():
    class _Service:
        def Evaluate(self, _script, _mode, _name, args):
            # one sketch per call: [origin, H, V], 9 floats
            assert len(args) == 1, "the batch form does not survive this build"
            return [0.0, 0.0, 1000.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]

    class _App:
        SystemService = _Service()

    systems = sketch_frames.read_absolute_axes(_App(), [object(), object()])
    assert systems is not None and len(systems) == 2
    assert systems[0][0] == [0.0, 0.0, 1000.0]
    assert systems[0][1] == [1.0, 0.0, 0.0]


def test_an_axis_route_that_fails_reads_as_unknown_not_as_no():
    class _App:
        class _Service:
            def Evaluate(self, *_args):
                raise RuntimeError("no CATScript on this build")

        SystemService = _Service()

    assert sketch_frames.read_absolute_axes(_App(), [object()]) is None


def test_two_sketches_on_one_plane_are_on_one_plane_rotated_or_not():
    base = sketch_frames.plane_signature(([0.0, 0.0, 1000.0], [1.0, 0.0, 0.0],
                                          [0.0, 1.0, 0.0]))
    rotated = sketch_frames.plane_signature(([5.0, 5.0, 1000.0], [0.0, 1.0, 0.0],
                                             [-1.0, 0.0, 0.0]))
    above = sketch_frames.plane_signature(([0.0, 0.0, 1001.0], [1.0, 0.0, 0.0],
                                           [0.0, 1.0, 0.0]))
    assert sketch_frames.same_plane(base, rotated) is True
    assert sketch_frames.same_plane(base, above) is False
    assert sketch_frames.same_plane(base, None) is None, (
        "unknown must not read as 'no', or the cleanup deletes a live support")


# ── P3: the numbers as numbers, and a plan that keeps them ────────────────
def _spline_stub():
    sketcher = SketcherTools(None)
    sketcher._draw_spline = lambda points, closed, echo_points: "Spline created. "
    sketcher._measure_drawn_curve = lambda checks: {
        "checked": len(checks), "max_mm": 0.0000, "failed": 0, "why": ""}
    return sketcher


def test_the_structured_spline_reply_carries_the_two_errors_as_numbers():
    reply = _spline_stub()._draw_control_spline(
        SP15, 2, None, None, None, None, False, structured=True)
    assert reply["ok"] is True
    assert reply["code"] == "SPLINE_CREATED"
    data = reply["data"]
    assert data["mode"] == "control_points"
    assert data["control_points"] == 3 and data["degree"] == 2
    assert data["sampling_error_mm"] == pytest.approx(0.0252, abs=0.001)
    assert data["drawn_error_mm"] == pytest.approx(0.0, abs=1e-9)
    assert data["drawn_error_state"] == "measured"
    assert data["sample_points_written"] == 17
    assert "Sampling error" in reply["message"], (
        "the sentence and the numbers describe the same call")


def test_an_unverified_drawn_error_is_null_with_its_reason():
    sketcher = _spline_stub()
    sketcher._measure_drawn_curve = lambda checks: {
        "checked": 0, "max_mm": None, "failed": 0,
        "why": "the spline object is not reachable from this session"}
    reply = sketcher._draw_control_spline(SP15, 2, None, None, None, None,
                                          False, structured=True)
    assert reply["data"]["drawn_error_mm"] is None
    assert reply["data"]["drawn_error_state"] == "unverified"
    assert "not reachable" in reply["data"]["drawn_error_reason"]


def test_a_plan_step_keeps_the_metrics_it_was_given():
    class _Spline:
        def execute(self, _tool, _args):
            return {"ok": True, "code": "SPLINE_CREATED",
                    "message": "Spline created through 33 points",
                    "data": {"sampling_error_mm": 0.0252, "drawn_error_mm": 0.0}}

        def get_tool_definitions(self):
            return []

    class _Conn:
        def get_active_part(self):
            raise RuntimeError("no CATIA in this test")

    planner = PlannerTools(_Conn())
    planner.bind({"catia_sketch_spline": _Spline()})
    reply = planner.execute("catia_run_plan", {"steps": [
        {"tool": "catia_sketch_spline", "args": {"control_points": [[0, 0]]}}]})
    assert isinstance(reply, str) or isinstance(reply, dict)
    step = reply["data"]["steps"][0] if isinstance(reply, dict) else None
    assert step is not None
    assert step["metrics"]["sampling_error_mm"] == pytest.approx(0.0252)
    assert step["metrics"]["drawn_error_mm"] == pytest.approx(0.0)

# ── P1: the doubling mirror, and what makes it correct or suspect ─────────
def _mirror_stub(extent):
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(None)
    tools._vertex_extent = lambda part, body, axis: extent
    return tools


def test_a_body_entirely_on_one_side_explains_the_doubling():
    # A body clear of the plane: 4 000 -> 8 000 mm3 on probe v0459 cell C.
    text = _mirror_stub((10.0, 20.0))._duplication_note((10.0, 20.0), "xy")
    assert "EVERY mm3" in text
    assert "all on one side" in text and "10.0000" in text
    assert "SUSPECT" not in text


def test_the_reports_own_body_touches_the_plane_and_says_so():
    # probe v0459 cell F, the report's shape: 132 000 -> 264 000 mm3 with the
    # vertices at z 0 .. 1010 - one side, in contact at z=0.
    text = _mirror_stub((0.0, 1010.0))._duplication_note((0.0, 1010.0), "xy")
    assert "sits on one side" in text and "only touches" in text
    assert "1010.0000" in text
    assert "SUSPECT" not in text


def test_a_body_the_plane_cuts_through_is_called_suspect():
    # probe v0459 cell B: 36 000 -> 40 000 for exactly this shape, so a doubling
    # here cannot be a union - the number has to be flagged, not published.
    text = _mirror_stub((-10.0, 10.0))._duplication_note((-10.0, 10.0), "xy")
    assert "SUSPECT" in text
    assert "36 000 -> 40 000" in text
    assert "catia_get_inertia" in text


def test_a_body_that_only_touches_the_plane_says_so():
    text = _mirror_stub((0.0, 10.0))._duplication_note((0.0, 10.0), "xy")
    assert "only touches" in text
    assert "SUSPECT" not in text


def test_an_unreadable_envelope_is_not_verified_not_verified_as_zero():
    text = _mirror_stub(None)._duplication_note(None, "xy")
    assert "NOT verified" in text
    assert "catia_get_inertia" in text


def test_the_axis_of_the_plane_is_the_one_that_is_measured(monkeypatch):
    # xy -> z, yz -> x, zx -> y. Reading the wrong axis would report a body as
    # one-sided when it is not, which is the same class of mistake.
    import inspect

    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._mirror)
    assert '"xy": 2, "yz": 0,\\r\\n' in source or '"xy": 2, "yz": 0,' in source, (
        "the plane-to-axis map is what decides which coordinate is the envelope")

