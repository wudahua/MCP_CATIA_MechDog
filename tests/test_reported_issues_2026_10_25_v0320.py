# -*- coding: utf-8 -*-
"""Regression tests for the 2026-10-25 v0.3.20 batch (two newer reports).

The reports are both about the same shape of failure: a tool that already knew
enough to say something useful stayed silent or said something misleading.

* the pad/pocket diagnosis blamed the profile for a zero-volume sweep, and told
  the caller to retry (a retry can never fix a reversed direction);
* ``catia_list_edges`` returned no coordinates at all, so "fillet the edge at
  (0, 65, -20)" could not be mapped to an edge - 22 edges in the report, 4
  applied;
* ``catia_run_plan`` threw a bare ``KeyError`` for a step with a missing
  argument, after the plan had already changed the model;
* a plan's measurements only covered the last active document.

The measured facts behind the mechanisms (B30, 2026-10-25):
``Measurable.GetMinimumDistance`` takes a POINT REFERENCE and rejects a
Measurable with "type mismatch"; ``GetMinimumDistancePoints`` fills its out
array only through the CATScript engine and returns
``[on-object xyz..., other xyz...]``; ``AddNewShell(None, ...)`` builds an empty
feature, so a closed shell really is not expressible through that factory.
"""

from __future__ import annotations

import inspect
import json

import pytest


# --------------------------------------------------------------------------
# 1. Arc by endpoints - the form reverse-engineered point lists come in.
# --------------------------------------------------------------------------

class _FakeFactory:
    def __init__(self):
        self.calls = []

    def CreateCircle(self, cx, cy, r, start, end):
        self.calls.append((cx, cy, r, start, end))


def _sketcher_with_factory():
    from catia_mcp.tools.sketcher import SketcherTools

    tools = SketcherTools(connection=None)
    tools._active_factory = _FakeFactory()
    tools._active_sketch = type("S", (), {"Name": "Sketch.1"})()
    tools._ensure_sketch_open = lambda: None
    return tools


def test_arc_from_two_endpoints_sweeps_the_major_way_when_asked():
    import math

    tools = _sketcher_with_factory()
    p1 = [20 * math.cos(math.radians(10)), 20 * math.sin(math.radians(10))]
    p2 = [20 * math.cos(math.radians(250)), 20 * math.sin(math.radians(250))]
    reply = tools._draw_arc(0, 0, 20, p1=p1, p2=p2, direction="ccw")
    assert "swept 240.0000°" in reply
    assert "MAJOR" in reply
    start, end = tools._active_factory.calls[0][3:]
    assert math.degrees(end - start) == pytest.approx(240, abs=1e-6)


def test_arc_from_two_endpoints_can_be_entered_clockwise():
    import math

    tools = _sketcher_with_factory()
    p1 = [20 * math.cos(math.radians(10)), 20 * math.sin(math.radians(10))]
    p2 = [20 * math.cos(math.radians(250)), 20 * math.sin(math.radians(250))]
    reply = tools._draw_arc(0, 0, 20, p1=p1, p2=p2, direction="cw")
    assert "swept 120.0000°" in reply
    assert "clockwise from p1 to p2" in reply


def test_arc_reports_a_radius_that_does_not_fit_its_endpoints():
    tools = _sketcher_with_factory()
    reply = tools._draw_arc(0, 0, 20, p1=[25.0, 0.0], p2=[0.0, 20.0])
    assert "off the R20 circle" in reply
    assert "5.0000 mm" in reply


def test_arc_needs_both_forms():
    tools = _sketcher_with_factory()
    with pytest.raises(ValueError) as error:
        tools._draw_arc(0, 0, 20, p1=[20.0, 0.0])
    assert "BOTH p1 and p2" in str(error.value)
    with pytest.raises(ValueError) as error:
        tools._draw_arc(0, 0, 20)
    assert "start_angle" in str(error.value)


def test_arc_schema_accepts_endpoints_and_direction():
    from catia_mcp.tools.sketcher import SketcherTools

    schema = [d for d in SketcherTools(connection=None).get_tool_definitions()
              if d["name"] == "catia_sketch_arc"][0]["inputSchema"]
    assert "required" not in schema or "p1" not in schema["required"]
    assert set(schema["properties"]) >= {"p1", "p2", "direction", "cx", "cy", "radius"}
    assert schema["properties"]["direction"]["enum"] == ["ccw", "cw"]


# --------------------------------------------------------------------------
# 2. close_sketch names every breakpoint, not just the closest pair.
# --------------------------------------------------------------------------

def test_every_gap_is_reported_not_only_the_smallest():
    from catia_mcp.tools.sketcher import SketcherTools

    tools = SketcherTools(connection=None)
    tools._note_ends((0.0, 0.0), (50.0, 0.0))
    tools._note_ends((50.0, 0.0004), (50.0, 30.0))
    tools._note_ends((50.0, 30.0), (0.0006, 30.0))
    tools._note_ends((0.0, 30.0), (0.0, 0.0))
    warning = tools._profile_warning()
    assert "4 loose endpoint(s)" in warning
    assert "2 break(s)" in warning
    assert "= 0.0004 mm" in warning
    assert "= 0.0006 mm" in warning


def test_a_deliberately_open_profile_is_still_quiet():
    from catia_mcp.tools.sketcher import SketcherTools

    tools = SketcherTools(connection=None)
    tools._note_ends((0.0, 0.0), (100.0, 0.0))
    assert tools._profile_warning() == ""


# --------------------------------------------------------------------------
# 3. Edges by coordinate: listing and selecting.
# --------------------------------------------------------------------------

def test_list_edges_can_report_a_point_on_each_edge():
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    schema = [d for d in tools.get_tool_definitions()
              if d["name"] == "catia_list_edges"][0]["inputSchema"]
    assert schema["properties"]["coords"]["default"] is False
    assert "point_on_edge" in schema["properties"]["coords"]["description"]


def test_edge_anchor_arguments_are_parsed_and_refused_clearly():
    from catia_mcp.tools.part_design import PartDesignTools

    anchors, tol = PartDesignTools._anchor_args(
        {"edge_anchors": [[0, 65, -20], {"x": 1, "y": 2, "z": 3}]})
    assert anchors == [[0.0, 65.0, -20.0], [1.0, 2.0, 3.0]]
    assert tol == 0.5
    with pytest.raises(RuntimeError) as error:
        PartDesignTools._anchor_args({"edge_anchors": [[0, 1]]})
    assert "must be [x, y, z]" in str(error.value)
    with pytest.raises(RuntimeError) as error:
        PartDesignTools._anchor_args({"edge_anchors": [[0, 1, 2]], "anchor_tol": 0})
    assert "greater than 0" in str(error.value)


def test_fillet_and_chamfer_accept_anchors():
    from catia_mcp.tools.part_design import PartDesignTools

    for name in ("catia_fillet", "catia_chamfer"):
        schema = [d for d in PartDesignTools(connection=None).get_tool_definitions()
                  if d["name"] == name][0]["inputSchema"]
        assert "edge_anchors" in schema["properties"], name
        assert schema["properties"]["anchor_tol"]["default"] == 0.5, name


def test_anchors_are_measured_against_a_point_reference():
    """The distance API takes a REFERENCE; a Measurable raises 'type mismatch'."""
    from catia_mcp.tools import part_design

    source = inspect.getsource(part_design.PartDesignTools._anchors_to_edges)
    assert "GetMinimumDistance(point_ref)" in source
    assert "ambiguous" in source
    assert "beyond anchor_tol" in source


def test_the_probe_point_set_is_deleted_again():
    from catia_mcp.tools import part_design

    source = inspect.getsource(part_design._ProbePoints)
    assert "sel.Delete()" in source
    assert part_design._ProbePoints.NAME == "MCP_probe_points"


def test_reading_a_point_on_an_edge_goes_through_the_script_engine():
    from catia_mcp.tools import measurement

    assert "GetMinimumDistancePoints other, a" in measurement._MIN_DISTANCE_POINTS
    assert "REFERENCE" in inspect.getsource(measurement.read_min_distance_points)


# --------------------------------------------------------------------------
# 4. A plan refuses a bad step argument instead of failing mid-run.
# --------------------------------------------------------------------------

def _planner(router):
    from catia_mcp.tools.planner import PlannerTools

    tools = PlannerTools(connection=None)
    tools.bind(router)
    return tools


def test_a_plan_step_with_a_missing_argument_is_bad_args_not_keyerror():
    from catia_mcp.tools.document import DocumentTools

    docs = DocumentTools(connection=None)
    planner = _planner({"catia_activate_document": docs})
    result = planner._run_plan({"steps": [{"tool": "catia_activate_document",
                                           "args": {}}]})
    assert result["code"] == "BAD_ARGS"
    details = result["data"]["details"]
    assert details[0]["step"] == 1
    assert details[0]["missing"] == ["name"]
    assert details[0]["schema"]["required"] == ["name"]


def test_a_plan_with_an_unknown_tool_is_still_invalid_plan():
    planner = _planner({"catia_activate_document": object()})
    result = planner._run_plan({"steps": [{"tool": "catia_nope", "args": {}}]})
    assert result["code"] == "INVALID_PLAN"


# --------------------------------------------------------------------------
# 5. Assertions per document.
# --------------------------------------------------------------------------

def test_expect_may_be_a_list_with_one_entry_per_document():
    planner = _planner({})
    seen = []

    def measure(document=None):
        seen.append(document)
        return {"volume_mm3": 800.0 if document == "PA.CATPart" else 1200.0}

    planner._measure_part = measure
    checks = planner._check_all(
        [{"document": "PA.CATPart", "volume_mm3": 800},
         {"doc": "PB.CATPart", "volume_mm3": 1200}],
        {"volume_mm3": 0.0},
    )
    assert seen == ["PA.CATPart", "PB.CATPart"]
    assert [c["document"] for c in checks] == ["PA.CATPart", "PB.CATPart"]
    assert all(c["pass"] for c in checks)


def test_a_single_expect_object_still_checks_the_active_document():
    planner = _planner({})
    checks = planner._check_all({"volume_mm3": 800}, {"volume_mm3": 800.0})
    assert len(checks) == 1 and checks[0]["pass"]


def test_a_part_is_not_treated_as_a_product_when_measuring_topology():
    """PartDocument carries a stub Product, so 'has .Product' proves nothing."""
    from catia_mcp.tools.planner import PlannerTools

    class _Part:
        Type = "PartDocument"

    class _Product:
        Type = "ProductDocument"

    assert PlannerTools._is_product_document(_Part()) is False
    assert PlannerTools._is_product_document(_Product()) is True


def test_a_product_reports_area_as_not_applicable():
    planner = _planner({})

    class _Doc:
        Type = "ProductDocument"

    planner._named_document = lambda document: _Doc()
    planner._measure_part.__self__  # noqa: B018 - attribute exists on the class

    # The branch itself: a Product has no surface area to report.
    source = inspect.getsource(type(planner)._measure_part)
    assert '"n/a (Product document)"' in source


# --------------------------------------------------------------------------
# 6. Batch assembly: count + placements.
# --------------------------------------------------------------------------

def test_add_component_takes_a_count():
    from catia_mcp.tools.assembly import AssemblyTools

    schema = [d for d in AssemblyTools(connection=None).get_tool_definitions()
              if d["name"] == "catia_add_component"][0]["inputSchema"]
    assert schema["properties"]["count"]["minimum"] == 1


def test_placements_batch_reports_each_component():
    from catia_mcp.tools.assembly import AssemblyTools

    tools = AssemblyTools(connection=None)
    tools.refresh = lambda: None
    tools.conn = type("C", (), {"refresh_display": staticmethod(lambda: None)})()
    tools._set_component_transform = lambda args: json.dumps({
        "component": args["component_name"],
        "source": "rotation_rows",
        "verified": {"max_deviation": 0.0, "matches_request": True},
    })
    out = json.loads(tools._apply_placements(
        [{"component_name": "P01.1"}, {"component_name": "P01.2"}]))
    assert out["count"] == 2 and out["placed"] == 2 and out["failed"] == 0
    assert out["worst_max_deviation"] == 0.0


def test_placements_batch_survives_one_bad_entry():
    from catia_mcp.tools.assembly import AssemblyTools

    tools = AssemblyTools(connection=None)
    tools.conn = type("C", (), {"refresh_display": staticmethod(lambda: None)})()

    def place(args):
        if args["component_name"] == "bad":
            raise RuntimeError("no component matches 'bad'")
        return json.dumps({"component": args["component_name"],
                           "verified": {"max_deviation": 0.0}})

    tools._set_component_transform = place
    out = json.loads(tools._apply_placements(
        [{"component_name": "good"}, {"component_name": "bad"}]))
    assert out["placed"] == 1 and out["failed"] == 1
    assert out["placements"][1]["ok"] is False


def test_a_placements_entry_must_name_its_component():
    from catia_mcp.tools.assembly import AssemblyTools

    tools = AssemblyTools(connection=None)
    with pytest.raises(ValueError) as error:
        tools._apply_placements([{"rotation_rows": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}])
    assert "component_name" in str(error.value)


# --------------------------------------------------------------------------
# 7. new_part can save itself; bounding boxes say whether they are a bound.
# --------------------------------------------------------------------------

def test_new_part_takes_a_file_path():
    from catia_mcp.tools.document import DocumentTools

    schema = [d for d in DocumentTools(connection=None).get_tool_definitions()
              if d["name"] == "catia_new_part"][0]["inputSchema"]
    assert "file_path" in schema["properties"]
    assert "cannot be inserted into an assembly" in (
        [d for d in DocumentTools(connection=None).get_tool_definitions()
         if d["name"] == "catia_new_part"][0]["description"])


def test_bounding_box_states_whether_it_is_a_lower_bound():
    from catia_mcp.tools.measurement import MeasurementTools

    payload = json.loads(MeasurementTools._bbox_json(
        [[0, 0, 0], [10, 20, 30]], "mesh sweep", bounds="lower bound"))
    assert payload["bounds"] == "lower bound"
    assert payload["dimensions"]["length_y"] == 20.0


# --------------------------------------------------------------------------
# 8. diagnose answers with the server's own identity, even with no Part open.
# --------------------------------------------------------------------------

def test_diagnose_reports_the_server_identity_before_it_can_fail():
    from catia_mcp.tools.diagnostics import DiagnosticsTools

    tools = DiagnosticsTools(connection=None)
    tools.tool_count = 101
    info = tools._server_info()
    assert info["name"] == "MCP_CATIA_MechDog"
    assert info["tool_count"] == 101
    assert info["version"]

    source = inspect.getsource(DiagnosticsTools._diagnose)
    # The block has to be the FIRST key: the factory probe returns early when no
    # Part is open, which is exactly when a bug report is written.
    assert 'report: dict[str, Any] = {"mcp": self._server_info()}' in source
