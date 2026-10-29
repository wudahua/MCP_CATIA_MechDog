"""0.3.31 - the two 2026-10-27 acceptance reports.

Report A: "six of eight closed, but the sketch deletion was only disclosed and the
cold 4.65x was only annotated".
Report B: "the hole ignores the profile's position and drills at the sketch origin".

Measured on V5-6R2020 B30 2026-10-27 (probe_v0331a..i):
  * circles at (0,0) and (10,0) on a 40 x 20 x 20 block gave bit-identical parts
    (volume 15246.0178, COG (0,0,-0.1236)) - the bore sat at the sketch origin;
  * one construction point at the centre moved it to (10,0): COG x -0.4945, and
    the centroid of the removed material came out at exactly (10.000, 0.000,
    2.500), 0.000 mm from the circle;
  * the same point added by reopening the CLOSED sketch worked identically;
  * `Hole.DirectionOrientation` does not exist, so there is no reverse argument;
  * a 60-cell part: one script call 826 ms warm against 1134 ms for the per-cell
    loop, with identical boxes and identical kept-vertex counts.
"""

from __future__ import annotations

import inspect
import json

import pytest


# ─────────────────── 1. the bore goes where the profile is


def test_the_sketch_frame_maps_every_datum_plane_it_claims():
    from catia_mcp.tools.part_design import _sketch_point_world

    # Measured on B30 2026-10-25 by padding a 20 x 6 rectangle on each plane and
    # reading the box back: xy is (u, v, d), yz is (d, u, v), zx is (v, d, u).
    assert _sketch_point_world({"base": "xy", "distance": 25.0}, 3, 4) == [3.0, 4.0, 25.0]
    assert _sketch_point_world({"base": "yz", "distance": 25.0}, 3, 4) == [25.0, 3.0, 4.0]
    assert _sketch_point_world({"base": "zx", "distance": 25.0}, 3, 4) == [4.0, 25.0, 3.0]
    assert _sketch_point_world(None, 3, 4) is None


def test_a_frame_placement_is_used_as_given():
    from catia_mcp.tools.part_design import _sketch_point_world

    placement = {"base": "frame", "origin": [10.0, 20.0, 30.0],
                 "u": [0.0, 0.0, 1.0], "v": [0.0, 1.0, 0.0]}
    # origin + 2*u + 5*v, with u along +z and v along +y: no axis is re-mapped.
    assert _sketch_point_world(placement, 2, 5) == [10.0, 25.0, 32.0]


def test_the_plane_distance_is_measured_in_the_sketch_plane():
    from catia_mcp.tools.part_design import _in_plane_distance

    placement = {"base": "xy", "distance": 0.0}
    # A difference along the normal (z) is not a position error in the plane.
    assert _in_plane_distance([1.0, 2.0, 9.0], [1.0, 2.0, -4.0], placement) == 0.0
    assert _in_plane_distance([3.0, 4.0, 0.0], [0.0, 0.0, 0.0], placement) == 5.0


def test_the_removed_centroid_is_recovered_from_the_cog_shift():
    """V0*G0 - V1*G1 = v*g: the only witness that says WHERE a cut went."""
    from catia_mcp.tools.part_design import _removed_centroid

    point, amplification = _removed_centroid(
        16000.0, [0.0, 0.0, 0.0], 15246.0178, [-0.4945, 0.0, -0.1236])
    assert point[0] == pytest.approx(10.0, abs=0.01)
    assert point[1] == pytest.approx(0.0, abs=0.01)
    assert point[2] == pytest.approx(2.5, abs=0.01)
    assert amplification == pytest.approx(20.2, abs=0.2)
    # Nothing was removed: no centroid to report, and no division by zero.
    assert _removed_centroid(100.0, [0, 0, 0], 100.0, [0, 0, 0]) == (None, None)


def test_the_hole_tool_describes_where_the_bore_goes():
    from catia_mcp.tools.part_design import PartDesignTools

    definition = [d for d in PartDesignTools(connection=None).get_tool_definitions()
                  if d["name"] == "catia_hole"][0]
    description = definition["description"]
    # The old claim - "The sketch supplies the bore's position" - was false.
    assert "drilled at the SKETCH ORIGIN" in description
    assert "construction point" in description
    assert "FROM THE SKETCH PLANE" in description
    assert "The sketch supplies the bore's position" not in description
    # depth says which datum it is measured from, in the schema too.
    assert "SKETCH PLANE" in definition["inputSchema"]["properties"]["depth"][
        "description"]


def test_a_point_away_from_the_circle_is_refused_before_cutting():
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    tools._hole_profile = lambda sketch: {
        "name": "草图.2", "placement": {"base": "xy", "distance": 0.0},
        "circles": [{"cx": 10.0, "cy": 0.0, "r": 4.0}],
        "points": [{"x": 0.0, "y": 0.0}], "known": True}
    with pytest.raises(RuntimeError) as caught:
        tools._place_bore(object(), object())
    message = str(caught.value)
    assert "holds a point at (0, 0)" in message
    assert "circle centred at (10, 0)" in message
    assert "10.000 mm apart" in message
    assert "Nothing was created" in message


def test_a_lone_circle_gets_a_construction_point_and_says_so():
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    tools._hole_profile = lambda sketch: {
        "name": "草图.2", "placement": {"base": "xy", "distance": 0.0},
        "circles": [{"cx": 10.0, "cy": 0.0, "r": 4.0}],
        "points": [], "known": True}
    added = {}
    tools._add_locating_point = lambda part, sketch, x, y: (
        added.update({"x": x, "y": y}) or None)
    note, target = tools._place_bore(object(), object())
    assert (added["x"], added["y"]) == (10.0, 0.0)
    assert target == [10.0, 0.0, 0.0]
    assert "construction point was added at the circle's centre" in note


def test_a_point_already_at_the_centre_changes_nothing():
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    tools._hole_profile = lambda sketch: {
        "name": "草图.2", "placement": {"base": "xy", "distance": 0.0},
        "circles": [{"cx": 10.0, "cy": 0.0, "r": 4.0}],
        "points": [{"x": 10.0, "y": 0.0}], "known": True}
    tools._add_locating_point = lambda *a, **k: pytest.fail("must not touch it")
    note, target = tools._place_bore(object(), object())
    assert note == ""
    assert target == [10.0, 0.0, 0.0]


def test_a_bore_off_the_profile_is_reported_not_kept():
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    placement = {"base": "xy", "distance": 0.0}
    # Measured on B30 2026-10-27: a lone circle at (10,0) drilled at the sketch
    # origin left the body's COG on x=0, so the removed material sits at x=0.
    wrong = tools._bore_off_target(
        16000.0, [0.0, 0.0, 0.0], 15246.0178, [0.0, 0.0, -0.1236],
        [10.0, 0.0, 0.0], placement, 8.0)
    assert wrong is not None
    assert "the sketch's circle is centred on (10.000, 0.000, 0.000)" in wrong
    assert "10.000 mm away" in wrong
    # With a construction point at (10,0) the bore follows it: COG x = -0.4945.
    right = tools._bore_off_target(
        16000.0, [0.0, 0.0, 0.0], 15246.0178, [-0.4945, 0.0, -0.1236],
        [10.0, 0.0, 0.0], placement, 8.0)
    assert right is None


def test_a_cut_too_small_to_locate_is_not_called_wrong():
    """A 0.1 mm3 nick in a 10 kg part is arithmetic, not a position error."""
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    placement = {"base": "xy", "distance": 0.0}
    assert tools._bore_off_target(
        10_000_000.0, [0.0, 0.0, 0.0], 9_999_999.9, [-1.0, 0.0, 0.0],
        [10.0, 0.0, 0.0], placement, 8.0) is None


def test_the_bore_length_is_converted_from_the_volume():
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    # depth 5 measured from a sketch plane 10 mm above the face: 15 mm of bore.
    note = tools._bore_length_note(753.9822, 8.0, 5.0, False, "simple", False)
    assert "Bore length 15.00 mm, not 5 mm" in note
    assert "measured from the SKETCH PLANE" in note
    assert "10.00 mm from the face" in note
    # A sketch on the face: depth and bore length agree, no lecture.
    assert tools._bore_length_note(251.3274, 8.0, 5.0, False, "simple",
                                   False) == " Bore length 5.00 mm."
    # Through all: the wall it crossed.
    assert "the wall it crossed" in tools._bore_length_note(
        1005.3096, 8.0, 0.0, True, "simple", False)
    # A counterbore is not a cylinder, so no length is claimed.
    assert tools._bore_length_note(900.0, 8.0, 5.0, False, "counterbored",
                                   False) == ""


def test_the_failure_lists_the_far_side_as_a_cause():
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    text = tools._bore_axis_sentence([0.0, 0.0, 0.0],
                                     {"base": "xy", "distance": -10.0})
    assert "FAR side of the sketch plane" in text
    assert "on plane 'xy' at offset -10" in text
    assert "no reverse argument" in text
    # The reason there is no `direction` argument, measured rather than assumed.
    assert "DirectionOrientation" in text
    assert "through_all" in text


# ─────────────────── 2. the lost sketch is put back


def test_the_rebuild_refuses_rather_than_guessing():
    from catia_mcp.tools.sketcher import SketcherTools

    class _Conn:
        sketch_outlines = {}
        sketch_aliases = {}

    tools = SketcherTools(_Conn())
    new_name, problem = tools.rebuild_lost_sketch(object(), object(), "草图.9")
    assert new_name is None
    assert "no drawing record" in problem

    tools = SketcherTools(type("C", (), {
        "sketch_outlines": {"?::草图.9": {
            "placement": {"base": "xy", "distance": 0.0},
            "elements": [{"kind": "arc", "cx": 0, "cy": 0, "radius": 5,
                          "sweep_rad": 1.0}]}},
        "sketch_aliases": {}})())
    new_name, problem = tools.rebuild_lost_sketch(object(), object(), "草图.9")
    assert new_name is None
    assert "arc" in problem


def test_the_rebuild_writes_the_aliases_and_the_record():
    from catia_mcp.tools.sketcher import SketcherTools

    drawn = {}

    class _Factory:
        def CreateLine(self, *a):
            drawn.setdefault("lines", []).append(a)

        def CreateClosedCircle(self, *a):
            drawn.setdefault("circles", []).append(a)

        def CreatePoint(self, x, y):
            drawn["points"] = (x, y)
            return type("P", (), {"Construction": False})()

    class _Sketch:
        Name = "草图.3"

        def OpenEdition(self):
            return _Factory()

        def CloseEdition(self):
            drawn["closed"] = True

    class _Sketches:
        def Add(self, ref):
            return _Sketch()

    class _Body:
        Sketches = _Sketches()

    class _Part:
        def Update(self):
            drawn["updated"] = True

    record = {
        "placement": {"base": "xy", "distance": 20.0},
        "elements": [{"kind": "rectangle", "x1": 30.0, "y1": 15.0,
                      "x2": 35.0, "y2": 25.0},
                     {"kind": "circle", "cx": 10.0, "cy": 0.0, "radius": 4.0,
                      "sweep_rad": 6.28},
                     {"kind": "point", "x": 10.0, "y": 0.0, "construction": True}],
    }

    class _Conn:
        sketch_outlines = {"?::草图.2": record}
        sketch_aliases: dict[str, str] = {}
        last_sketch = None

    tools = SketcherTools(_Conn())
    tools._resolve_support = lambda part, body, plane, offset: ("REF", "xy")
    new_name, problem = tools.rebuild_lost_sketch(_Part(), _Body(), "草图.2")
    assert problem == ""
    assert new_name == "草图.3"
    assert len(drawn["lines"]) == 4  # the rectangle, redrawn as four lines
    assert drawn["circles"] == [(10.0, 0.0, 4.0)]
    assert drawn["closed"] and drawn["updated"]
    # The old name resolves to the new sketch, so a retry with the same name works.
    assert _Conn.sketch_aliases["?::草图.2"] == "草图.3"
    # And the record follows the sketch, so a second rollback can rebuild again.
    assert "?::草图.3" in _Conn.sketch_outlines


def test_a_rollback_that_took_the_sketch_says_it_was_put_back():
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)

    class _Sketcher:
        def __init__(self, conn):
            pass

        def rebuild_lost_sketch(self, part, body, name):
            return "草图.3", ""

    import catia_mcp.tools.sketcher as sketcher_module

    original = sketcher_module.SketcherTools
    sketcher_module.SketcherTools = _Sketcher
    try:
        text = tools._recover_lost_sketch(object(), object(), "草图.2", False)
    finally:
        sketcher_module.SketcherTools = original
    assert "has been re-created from its recorded definition as '草图.3'" in text
    assert "'草图.2' now resolves to it" in text


def test_the_tool_resolves_a_rebuilt_sketch_by_its_old_name():
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._get_last_sketch)
    assert "_sketch_alias" in source


# ─────────────────── 3. the sweep is one call, and says which ran


def test_the_sweep_script_carries_the_construction_filter():
    from catia_mcp.tools.measurement import _BATCH_SWEEP

    # The filter has to be the same rule the per-cell route applies, or the two
    # readers would disagree about which cells belong to the solid.
    assert 'InStr(head, "Wire")' in _BATCH_SWEEP
    assert 'InStr(head, "Border")' in _BATCH_SWEEP
    assert 'InStr(name, "Brp:(GSMPlane.")' in _BATCH_SWEEP
    # Out-of-range or unmeasurable cells must not abort the whole sweep.
    assert "On Error Resume Next" in _BATCH_SWEEP


def test_the_sweep_is_optional_and_falls_back():
    from catia_mcp.tools import measurement as M

    class _Boom:
        @property
        def Count(self):
            return 3

    class _App:
        class SystemService:
            @staticmethod
            def Evaluate(*args, **kwargs):
                raise RuntimeError("refused")

    stats: dict = {}
    assert M.batch_vertex_sweep(_App(), _Boom(), object(), stats) is None
    assert stats["sweep_route"] == "per_cell"


def test_an_empty_selection_is_not_a_failure():
    from catia_mcp.tools import measurement as M

    class _Empty:
        Count = 0

    assert M.batch_vertex_sweep(object(), _Empty(), object()) == ([], 0, 0)


def test_empty_slots_in_the_returned_array_do_not_break_it():
    """A VBScript array comes back with Empty slots; float(None) would abort."""
    from catia_mcp.tools import measurement as M

    class _Sel:
        Count = 1

    class _App:
        class SystemService:
            @staticmethod
            def Evaluate(*args, **kwargs):
                # out(0..3) = cells, kept, chars, reserved; 3 floats per cell
                # from out(4). out(3) and the unused tail come back Empty.
                return [1, 1, 40, None, 2.0, 3.0, 4.0, None, None]

    points, cells, chars = M.batch_vertex_sweep(_App(), _Sel(), object())
    assert (points, cells, chars) == ([[2.0, 3.0, 4.0]], 1, 40)


def test_both_single_part_routes_publish_the_same_fields():
    from catia_mcp.tools.measurement import MeasurementTools

    active = inspect.getsource(MeasurementTools._get_bounding_box)
    # The active-part path had its own copy of the block and published only
    # points_route / vertices_used / cells_scanned - no regime, no nls note.
    assert "_part_bbox(part, method)" in active
    named = inspect.getsource(MeasurementTools._part_bbox)
    for field in ("sweep_route", "cells_scanned", '"points_route"'):
        assert field in named
    # 0.3.39: `first_measure` and `elapsed_note` are no longer spelled out here -
    # they come from the shared `_BOOKS.describe`, the same call the assembly and
    # inertia routes make, which is why the two single-part routes cannot drift
    # apart again (they did: 0.3.38 filled the regime on three routes and left
    # two part routes and two empty branches without it).
    assert "_BOOKS.describe(" in named


# ─────────────────── 4. cost fields survive truncation


def test_the_cost_fields_are_not_behind_the_per_part_list():
    from catia_mcp.tools.measurement import _cost_first

    payload = {"dimensions": {}, "components": [], "parts": [{}] * 28,
               "elapsed_ms": 197756.9, "session_first_parts": 28,
               "regime": "cold", "elapsed_note": "cold",
               "document": "A.CATProduct"}
    ordered = list(_cost_first(payload))
    assert ordered.index("elapsed_ms") < ordered.index("components")
    assert ordered.index("session_first_parts") < ordered.index("components")
    assert ordered.index("regime") < ordered.index("components")
    assert ordered.index("elapsed_note") < ordered.index("components")


# ─────────────────── 5. error codes come from the exception


def test_component_not_found_is_carried_by_the_exception():
    from catia_mcp.results import ComponentNotFound
    from catia_mcp.tools.planner import _failure_code

    error = ComponentNotFound("No component 'NOPE.9'. Available: ['P30A.1']")
    assert error.code == "COMPONENT_NOT_FOUND"
    # It is still a ValueError, so callers that catch that keep working.
    assert isinstance(error, ValueError)
    assert _failure_code(str(error), error) == "COMPONENT_NOT_FOUND"
    # And the phrase table alone now covers the wording the two other tools use.
    assert _failure_code("No component 'NOPE.9'. Available: ['A']") == \
        "COMPONENT_NOT_FOUND"


def test_an_unclassified_failure_still_says_step_exception():
    from catia_mcp.tools.planner import _failure_code

    assert _failure_code("catia is not running", RuntimeError("x")) == \
        "STEP_EXCEPTION"


# ─────────────────── 6. a stopping plan counts what it never ran


def test_a_stopping_plan_counts_its_total():
    from catia_mcp.tools.planner import PlannerTools

    report = [{"index": 1, "tool": "catia_pad", "ok": True},
              {"index": 2, "tool": "catia_pad", "ok": False}]
    message = PlannerTools._failure_summary(report, True, 15)
    assert message == ("1 of 15 planned steps failed at step 2 (catia_pad); "
                       "13 step(s) were not run")
    # Nothing left over: no "0 step(s) were not run" tail.
    assert "not run" not in PlannerTools._failure_summary(report, True, 2)


# ─────────────────── 7. nls_chars travels with its meaning


def test_the_nls_count_and_its_warning_travel_together():
    from catia_mcp.tools.measurement import _NLS_NOTE, _nls_fields

    assert _nls_fields({"nls_chars": 7})["nls_chars_note"] == _NLS_NOTE
    assert "5.49x" in _NLS_NOTE and "0.34" in _NLS_NOTE
    assert "not a cost predictor" in _NLS_NOTE


def test_the_single_part_regime_has_a_sentence():
    from catia_mcp import regime

    # Both sentences a one-part call can print, in the one place they are
    # written. Before 0.3.39 this test read `_part_bbox`'s source, so the same
    # sentence existed three times and only this copy was checked.
    source = inspect.getsource(regime.SessionBooks.describe)
    assert "'session_first_parts' counts" in source
    assert "the warm number" in source


def test_the_assembly_warm_note_warns_about_jitter():
    from catia_mcp import regime

    # The substance, not the reading: a single small part swings far more than a
    # big assembly, so one part's time must not be read to three digits. The
    # old assertion quoted "324 ms and then 549 ms" - two readings from one
    # session, which the next session would have moved (this project has
    # retracted four such numbers already).
    source = inspect.getsource(regime.SessionBooks.describe)
    assert "do not read one part's time to three digits" in source


# ─────────────────── 8. sketch points can be construction


def test_a_sketch_point_can_be_asked_for_as_construction():
    from catia_mcp.tools.sketcher import SketcherTools

    definition = [d for d in SketcherTools(connection=None).get_tool_definitions()
                  if d["name"] == "catia_sketch_point"][0]
    assert "construction" in definition["inputSchema"]["properties"]
    # Default is real geometry: a user pattern ignores construction points.
    assert definition["inputSchema"]["properties"]["construction"]["default"] is False
    source = inspect.getsource(SketcherTools._draw_point)
    assert "Construction point" in source


def test_the_plan_result_is_still_json():
    """Nothing above may have made the replies non-serialisable."""
    from catia_mcp.tools.measurement import _cost_first

    assert json.loads(json.dumps(_cost_first({"dimensions": {"a": 1}}))) == {
        "dimensions": {"a": 1}}
