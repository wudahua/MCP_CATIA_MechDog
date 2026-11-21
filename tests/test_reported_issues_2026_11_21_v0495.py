"""0.3.158: the 2026-11-21 report's last four items - three answers, one refusal.

P2-8  ``catia_diagnose`` was 80 222 characters and the client truncated it, so the
      answer had to be grepped out of a saved tool-result file. ``brief`` and
      ``sections`` are measured here (23 011 and 3 584 characters on B30).
P2-20 ``catia_measure_topology`` said 33 faces / 65 edges / 30 vertices where the
      source part has 33 / 65 / 33, and no tool could say WHICH points were
      counted. ``include_vertex_points`` publishes both lists, and on the
      reporter's own P01 the 26 dropped sketch-wire corners were all >= 5.0 mm
      from every counted vertex - so the rule was not merging coincident corners,
      which is what the report hypothesised. A profile corner landing exactly on
      the solid's edge was built here and counted (10/24/16), the same check.
P1-13 a mid-plane cut makes two features; the face PAIRS the report measured did
      not reproduce on a controlled through-cut (10 faces either way), so the note
      now carries both results instead of asserting a mechanism.
P2-15 a batch delete of a consumed sketch left its frame datum and three points
      with NO word in the reply: the cleanup was skipped for cascaded sketches and
      quiet=True swallowed the refusal. Both halves are fixed and measured.

No CATIA here: the helpers are pure, and the two that need a session are fed
stubs.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from catia_mcp.results import ArgumentRejected
from catia_mcp.tools.diagnostics import DiagnosticsTools
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.planner import PlannerTools
from catia_mcp.topo_search import vertex_references


# ══ P2-8: a report you can read, and a refusal that lists its sections ════
def test_the_schema_offers_brief_and_sections():
    tool = {d["name"]: d for d in
            DiagnosticsTools(connection=None).get_tool_definitions()}["catia_diagnose"]
    properties = tool["inputSchema"]["properties"]
    assert properties["brief"]["type"] == "boolean"
    assert properties["brief"]["default"] is False
    assert properties["sections"]["type"] == "array"
    assert set(properties["sections"]["items"]["enum"]) == {
        "mcp", "python", "catia", "session", "workbenches", "sheetmetal",
        "factories", "capability_ceilings", "notes"}


def test_the_description_carries_the_measured_sizes():
    """A caller deciding whether to ask for less needs the numbers, not 'big'."""
    tool = {d["name"]: d for d in
            DiagnosticsTools(connection=None).get_tool_definitions()}["catia_diagnose"]
    text = tool["description"]
    assert "80 219" in text and "23 011" in text and "3 584" in text
    assert "brief=true" in text


def _report():
    return {
        "mcp": {"name": "X", "version": "1"},
        "session": {"document": "P.CATPart"},
        "capability_ceilings": [
            {"member": "A", "status": "UNSUPPORTED_ON_B30", "detail": "x" * 100},
            {"member": "B", "status": "AVAILABLE_WITH_LIMIT", "detail": "y" * 50},
        ],
        "notes": ["n"],
    }


def test_brief_keeps_every_member_and_status_but_not_the_prose():
    brief = DiagnosticsTools(connection=None)._brief(_report())
    ceilings = brief["capability_ceilings"]
    assert ceilings["count"] == 2
    assert [e["member"] for e in ceilings["entries"]] == ["A", "B"]
    assert [e["status"] for e in ceilings["entries"]] == [
        "UNSUPPORTED_ON_B30", "AVAILABLE_WITH_LIMIT"]
    assert all("detail" not in entry for entry in ceilings["entries"])
    assert [entry["detail_chars"] for entry in ceilings["entries"]] == [100, 50]
    assert ceilings["detail_withheld_chars"] == 150
    # Nothing else is touched, and the withheld prose is named as reachable.
    assert brief["session"] == {"document": "P.CATPart"}
    assert "sections=['capability_ceilings']" in ceilings["note"]


def test_brief_leaves_a_report_alone_when_there_is_nothing_to_compress():
    report = {"mcp": {"version": "1"}}
    assert DiagnosticsTools(connection=None)._brief(report) == report


def test_sections_are_returned_in_the_reports_order():
    tools = DiagnosticsTools(connection=None)
    report = _report()
    chosen = tools._select_sections(report, ["capability_ceilings", "mcp"])
    assert list(chosen) == ["mcp", "capability_ceilings"]
    assert tools._select_sections(report, ["session", "session"]) == {
        "session": report["session"]}


def test_an_unknown_section_is_refused_with_the_list():
    tools = DiagnosticsTools(connection=None)
    with pytest.raises(ArgumentRejected) as caught:
        tools._select_sections(_report(), ["mcp", "license"])
    text = str(caught.value)
    assert "license" in text and "capability_ceilings" in text
    assert caught.value.code == "BAD_ARGUMENT"


# ══ P2-20: which points are counted, and what was dropped ════════════════
class _Cell:
    def __init__(self, nls):
        self.Value = SimpleNamespace(Name=nls)
        self.Reference = ("ref", nls)


class _Selection:
    """The HSO selection: Clear, Add, then Item(1..N) after a search."""

    def __init__(self, cells):
        self.cells = cells
        self.Count = 0

    def Clear(self):
        self.Count = 0

    def Add(self, _body):
        pass

    def Item(self, index):
        return self.cells[index - 1]


def _conn_with(nlss):
    return SimpleNamespace(hso=_Selection([_Cell(nls) for nls in nlss]))


def test_vertex_references_splits_solid_from_construction_by_reason(monkeypatch):
    import catia_mcp.topo_search as topo

    monkeypatch.setattr(topo, "search_topology",
                        lambda selection, kind: setattr(selection, "Count",
                                                        len(selection.cells)))
    conn = _conn_with([
        "Selection_FVertex:(Vertex:(Brp:(Pad.1;3)",
        "Selection_WireFVertex:(Vertex:(Brp:(Sketch.1;2)",
        "Selection_BorderRVertex:(Vertex:(GSMPlane.1)",
        "Selection_FVertex:(Vertex:(Brp:(Pad.1;7)",
    ])
    kept, excluded = vertex_references(conn, body=object())
    assert len(kept) == 2
    assert sorted(excluded) == ["datum_plane_corner", "sketch_wire_vertex"]
    assert len(excluded["sketch_wire_vertex"]) == 1


def _tools_for_points(monkeypatch, points_by_prefix):
    """A PlannerTools whose SPA reader answers coordinates from a table."""
    planner = PlannerTools(connection=SimpleNamespace(app=object()))

    def fake_read(app, spa, refs, fallback=None, stats=None):
        return [points_by_prefix[ref[1]] for ref in refs]

    import catia_mcp.tools.measurement as measurement
    monkeypatch.setattr(measurement, "read_measurable_points", fake_read)
    monkeypatch.setattr(measurement.MeasurementTools, "_spa_workbench",
                        lambda self: object())
    monkeypatch.setattr(measurement.MeasurementTools, "_read_measurable_array",
                        lambda self, measure, name, count: [0.0, 0.0, 0.0])
    return planner


def test_vertex_points_are_published_and_unbounded_corners_are_counted(monkeypatch):
    import catia_mcp.topo_search as topo

    monkeypatch.setattr(topo, "search_topology",
                        lambda selection, kind: setattr(selection, "Count",
                                                        len(selection.cells)))
    monkeypatch.setattr(topo, "vertex_references", lambda conn, body: (
        [("ref", "Selection_FVertex:(Vertex:(Brp:(Pad.1;3)")],
        {"sketch_wire_vertex": [("ref", "Selection_WireFVertex:(x)"),
                                ("ref", "Selection_BorderRVertex:(y)")]},
    ))
    planner = _tools_for_points(monkeypatch, {
        "Selection_FVertex:(Vertex:(Brp:(Pad.1;3)": [1.23456, -2.0, 3.0],
        "Selection_WireFVertex:(x)": [10.0, 0.0, 0.0],
        "Selection_BorderRVertex:(y)": [1e7, 1e7, 0.0],
    })
    result = planner._vertex_point_lists(SimpleNamespace())
    assert result["vertex_points"] == [[1.2346, -2.0, 3.0]]     # 4 decimals
    assert result["construction_vertex_points"] == {
        "sketch_wire_vertex": [[10.0, 0.0, 0.0]]}                # 1e7 dropped
    assert "1 point(s) omitted" in result["construction_points_omitted"]
    assert "not vertices of the solid" in result["vertex_points_note"]


def test_a_vertex_list_over_the_cap_says_so(monkeypatch):
    import catia_mcp.topo_search as topo

    monkeypatch.setattr(topo, "vertex_references", lambda conn, body: (
        [("ref", f"v{i}") for i in range(600)], {}))
    planner = _tools_for_points(monkeypatch, {f"v{i}": [float(i), 0.0, 0.0]
                                              for i in range(600)})
    result = planner._vertex_point_lists(SimpleNamespace())
    assert len(result["vertex_points"]) == 500
    assert result["vertex_points_truncated"] == "500 of 600"


def test_the_topology_schema_offers_the_points():
    tool = {d["name"]: d for d in
            PlannerTools(connection=None).get_tool_definitions()}[
                "catia_measure_topology"]
    assert tool["inputSchema"]["properties"]["include_vertex_points"][
        "default"] is False
    assert "5.0 mm" in tool["description"]


# ══ P1-13: the mid-plane note, with both measurements ════════════════════
def test_the_midplane_note_is_absent_for_a_single_cut():
    assert PartDesignTools._midplane_topology_note(None) == ""


def test_the_midplane_note_carries_the_report_and_the_control():
    text = PartDesignTools._midplane_topology_note(object())
    assert "37 faces against the source's 33" in text   # the report
    assert "did NOT split anything" in text             # the control here
    assert "10 faces" in text
    assert "cut one side from an offset plane" in text


def test_the_direction_description_says_it_depends_on_the_geometry():
    tool = {d["name"]: d for d in
            PartDesignTools(connection=None).get_tool_definitions()}["catia_pocket"]
    text = tool["inputSchema"]["properties"]["direction"]["description"]
    assert "DEPENDS ON THE GEOMETRY" in text
    assert "37 faces" in text and "10 faces" in text


# ══ P2-15: what the batch left, by name ═════════════════════════════════
class _Shapes:
    def __init__(self, names):
        self.names = list(names)
        self.Count = len(self.names)

    def Item(self, index):
        return SimpleNamespace(Name=self.names[index - 1])


class _FramePart:
    def __init__(self, names):
        self.Bodies = SimpleNamespace(
            Count=1, Item=lambda index: SimpleNamespace(HybridShapes=_Shapes(names)))


def test_frame_leftovers_report_only_the_sketches_that_were_deleted():
    tools = PartDesignTools(connection=SimpleNamespace(
        get_active_part=lambda: _FramePart([
            "FR_A_FRAME", "FR_A_FRAME.O", "FR_A_FRAME.U", "FR_A_FRAME.V",
            "FR_B_FRAME", "FR_B_FRAME.O", "FR_B_FRAME.U", "FR_B_FRAME.V"])))
    assert tools._frame_leftovers(["FR_A"]) == {"FR_A": 4}
    assert tools._frame_leftovers(["FR_A", "FR_B"]) == {"FR_A": 4, "FR_B": 4}
    assert tools._frame_leftovers(["FR_C"]) == {}


def test_frame_leftovers_survive_a_part_that_cannot_be_read():
    tools = PartDesignTools(connection=SimpleNamespace(
        get_active_part=lambda: (_ for _ in ()).throw(RuntimeError("no part"))))
    assert tools._frame_leftovers(["FR_A"]) == {}


def test_the_batch_note_names_a_by_name_route_out():
    """The sketch's own name stops resolving once it is deleted, so the reply
    has to say how to remove the scaffold without it."""
    import inspect

    source = inspect.getsource(PartDesignTools)
    assert "_FRAME.O', '{owner}_FRAME.U'" in source.replace('"', "'").replace(
        f"{{owner}}_FRAME", "{owner}_FRAME")
    assert "deleted or cascaded sketch(es)" in source
    # And the cascaded sketches are in the same loop - that was the defect.
    assert "gone_sketches = list(removed_features)" in source
