"""0.3.45 - the 2026-11-01 report, from a 20-part monitor bracket built on B32.

Six findings, all reproduced from the report's own reproduction list. The two that
matter most are not in the geometry layer at all:

1. `catia_new_part(file_path=X)` called twice on the same path left TWO documents
   open - one holding the file, one with no path holding all the geometry - and an
   EMPTY part already written to disk. Hit three times in a 20-part build.
2. `catia_sketch_arc` with p1/p2 four degrees apart and direction="ccw" drew the
   355-degree arc, because "ccw from -71 to -75" means almost a full turn. The
   shaft built on it then failed with a bare "UpdateObject 失败", which points at
   nothing.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest


# ─────────────────── 1. two documents on one path

class _Docs:
    def __init__(self, items):
        self._items = items
        self.Count = len(items)

    def Item(self, index):
        return self._items[index - 1]

    def Add(self, kind):
        doc = SimpleNamespace(Name=f"{kind}1.CATPart", FullName=f"{kind}1.CATPart")
        self._items.append(doc)
        self.Count = len(self._items)
        return doc


def _doc_tools(items):
    from catia_mcp.tools.document import DocumentTools

    tools = DocumentTools(connection=None)
    tools.conn = SimpleNamespace(
        documents=_Docs(items),
        ensure_connected=lambda: None,
        refresh_display=lambda: None,
        last_sketch=None, active_body=None,
    )
    return tools


def test_new_part_refuses_a_path_that_is_already_open():
    """The failure is unrecoverable once a document exists, so it is checked first."""
    from catia_mcp.results import CodedError

    held = SimpleNamespace(Name="P05.CATPart", FullName=r"C:\work\P05.CATPart")
    tools = _doc_tools([held])
    with pytest.raises(CodedError) as caught:
        tools._new_part("P05", r"C:\work\P05.CATPart")
    error = caught.value
    assert error.code == "DOCUMENT_ALREADY_OPEN"
    assert error.stage == "validate"
    assert error.modified is False
    assert "P05.CATPart" in str(error)
    # Nothing was created: the collection still holds exactly the document that
    # was already open. Before 0.3.45 this count was 2, and the second one had no
    # path - so every later `document="P05"` answered "matches 2 open documents".
    assert tools.conn.documents.Count == 1


def test_new_part_still_works_when_the_path_is_free():
    tools = _doc_tools([])
    tools.conn.part_of = lambda doc: SimpleNamespace(Name="Part1")
    tools.conn.part_of = lambda doc: SimpleNamespace(Name="Part1")
    tools._save_document = lambda path=None, overwrite=False: f"Document saved as: {path}"
    reply = tools._new_part("P05", r"C:\work\P05.CATPart")
    assert "Created new Part document" in reply
    assert tools.conn.documents.Count == 1


def test_new_product_accepts_a_file_path():
    """P6: without it, "create an assembly" was two calls and the second is the one
    people forget - and an assembly with no path cannot be referenced."""
    from catia_mcp.server import CATIAMCPServer

    props = CATIAMCPServer()._schemas["catia_new_product"]["properties"]
    assert "file_path" in props


# ─────────────────── 2. an arc from two endpoints takes the short way

def _arc_tools():
    from catia_mcp.tools.sketcher import SketcherTools

    class _Factory:
        def __init__(self):
            self.calls = []

        def CreateCircle(self, cx, cy, radius, start, end):
            self.calls.append((cx, cy, radius, start, end))
            return SimpleNamespace(Name="Arc.1")

    factory = _Factory()
    tools = SketcherTools(connection=None)
    tools._ensure_sketch_open = lambda: None
    tools._active_factory = factory
    tools._note_ends = lambda *a, **k: None
    tools._note_element = lambda *a, **k: None
    return tools, factory


def test_the_arc_from_the_report_takes_the_short_way():
    """4.39 degrees apart, direction=ccw: 355.6 was never what was meant."""
    tools, factory = _arc_tools()
    c = (20 * math.cos(math.radians(-71.0402)), 20 * math.sin(math.radians(-71.0402)))
    d = (20 * math.cos(math.radians(-75.4285)), 20 * math.sin(math.radians(-75.4285)))
    reply = tools._draw_arc(0.0, 0.0, 20.0, p1=list(c), p2=list(d), direction="ccw")
    _, _, _, start, end = factory.calls[0]
    assert math.degrees(end - start) == pytest.approx(4.3883, abs=1e-3)
    assert "SHORT way round" in reply
    assert "Pass major=true" in reply


def test_major_true_still_gives_the_long_arc():
    """The behaviour that was there before is still reachable, on purpose."""
    tools, factory = _arc_tools()
    p1 = [20 * math.cos(math.radians(10)), 20 * math.sin(math.radians(10))]
    p2 = [20 * math.cos(math.radians(250)), 20 * math.sin(math.radians(250))]
    reply = tools._draw_arc(0.0, 0.0, 20.0, p1=p1, p2=p2, direction="ccw", major=True)
    _, _, _, start, end = factory.calls[0]
    assert math.degrees(end - start) == pytest.approx(240.0, abs=1e-6)
    assert "MAJOR" in reply


def test_a_half_circle_is_drawn_as_written_and_says_so():
    """180 degrees has two answers only in the arithmetic, not in the geometry."""
    tools, factory = _arc_tools()
    p1 = [20.0, 0.0]
    p2 = [-20.0, 0.0]
    reply = tools._draw_arc(0.0, 0.0, 20.0, p1=p1, p2=p2, direction="ccw")
    _, _, _, start, end = factory.calls[0]
    assert math.degrees(end - start) == pytest.approx(180.0, abs=1e-6)
    assert "direction decides nothing here" in reply


def test_two_endpoints_on_top_of_each_other_are_refused():
    """A zero-length arc is a circle request or a typo, not a 360-degree sweep."""
    from catia_mcp.results import ArgumentRejected

    tools, factory = _arc_tools()
    with pytest.raises(ArgumentRejected) as caught:
        tools._draw_arc(0.0, 0.0, 20.0, p1=[20.0, 0.0], p2=[20.0, 0.0])
    assert caught.value.code == "DEGENERATE_ARC"
    assert not factory.calls


# ─────────────────── 3. summary keeps the warnings

def test_summary_keeps_the_warning_a_successful_step_printed():
    """The MAJOR-arc line was the only clue, and summary dropped it (P3)."""
    from catia_mcp.tools.planner import PlannerTools

    class _Module:
        def execute(self, name, arguments):
            if arguments.get("warn"):
                return "Arc created - 355.6117° swept. WARNING: this is a major arc"
            return "built"

    planner = PlannerTools(connection=None)
    planner.bind({"catia_sketch_arc": _Module()})
    planner._schemas = {"catia_sketch_arc": {"properties": {"warn": {}}}}
    reply = planner._run_plan({
        "steps": [
            {"tool": "catia_sketch_arc", "args": {"warn": True}},
            {"tool": "catia_sketch_arc", "args": {}},
        ],
        "measure": False, "summary": True,
    })
    data = reply["data"]
    assert data["steps"] == []          # nothing failed
    assert data["steps_omitted"] == 2
    warnings = data["step_warnings"]
    assert len(warnings) == 1
    assert warnings[0]["index"] == 1
    assert "WARNING" in warnings[0]["message"]


# ─────────────────── 4. an assembly box can come from the meshes

def test_the_method_description_explains_what_mesh_does_on_an_assembly():
    """The two routes differ there, and the doc used to say they were the same."""
    from catia_mcp.server import CATIAMCPServer

    text = CATIAMCPServer()._schemas[
        "catia_get_bounding_box"]["properties"]["method"]["description"]
    assert "ASSEMBLY" in text
    assert "VERTICES" in text and "mesh" in text
    assert "not available for an assembly" not in text


def test_a_part_forced_to_mesh_reports_the_route_it_used():
    """A mesh that yields nothing falls back to vertices - and says so."""
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools._mesh_cloud = lambda part: ([], 0, "")
    tools._local_box = lambda part, body, stats: ([[0, 0, 0], [1, 1, 1]], 2)
    entry = tools._part_box_entry(object(), object(), True, force_mesh=True)
    assert entry["source"] == "vertices"
    assert "LOWER bound" in entry["mesh_note"]


def test_a_part_forced_to_mesh_uses_the_mesh_when_it_has_one():
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools._mesh_cloud = lambda part: ([0.0, 0.0, 0.0, 2.0, 2.0, 2.0], 2, "")
    tools._local_box = lambda part, body, stats: ([[0, 0, 0]], 1)
    entry = tools._part_box_entry(object(), object(), True, force_mesh=True)
    assert entry["source"] == "mesh"
    assert entry["used"] == 2
