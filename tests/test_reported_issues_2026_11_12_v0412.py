"""0.3.112: the 2026-11-12 report's four items.

Measured on the reporter's own rebuilt part (21 elements, 147 edges, largest
junction gap 0.0004 mm):

  §2.1 P1  the arc angle convention was not stated for catia_sketch_profile, and
           15 of 21 junctions opened on the first call
  §2.2 P2  close_sketch reported 4 "crossings" that all sat on ONE legal seam
  §2.3 P2  a dead sketch left by a failed pad answered a bare COM_ERROR
  §3       the all-edges fillet returned no elapsed time at all

The crossing tolerance is the one with arithmetic behind it: the seam is
0.0004 mm and the computed intersection lands ~1e-4 away from the endpoint, so
the old tol=1e-6 called a legal G0 seam a crossing.
"""
from __future__ import annotations

import inspect
import math

import pytest

from catia_mcp.geometry2d import _arc, _line, crossings
from catia_mcp.results import CodedError
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.sketcher import SketcherTools


# ── §2.2 the tolerance ───────────────────────────────────────────────────

def test_a_shared_corner_with_a_0_0004_mm_seam_is_not_a_crossing():
    """The report's shape: two edges meeting at a corner, ends 0.0004 apart."""
    segments = [_line(1, "line #1", 0.0, 0.0, 10.0, 0.0),
                _line(2, "line #2", 10.0, 10.0, 9.9996, 0.0)]
    assert crossings(segments, tol=1e-6), (
        "the old tolerance has to reproduce the false positive, or this test "
        "proves nothing")
    assert crossings(segments) == []


def test_two_arcs_meeting_at_a_rounded_corner_are_not_a_crossing():
    """The same seam between an arc and the line it runs into."""
    arc = _arc(1, "arc #11", 0.0, 0.0, 10.0, 0.0, math.pi / 2)   # (10,0) -> (0,10)
    line = _line(2, "line #12", 10.0004, 0.0, 5.0, 0.0)
    assert crossings([arc, line], tol=1e-6)
    assert crossings([arc, line]) == []


def test_a_real_t_junction_is_still_reported():
    """The control: a segment ENDING on another's middle is a defect either way."""
    segments = [_line(1, "line #1", 0.0, 0.0, 10.0, 0.0),
                _line(2, "line #2", 5.0, 0.0, 5.0, 10.0)]
    found = crossings(segments)
    assert len(found) == 1
    assert found[0]["point"] == (5.0, 0.0)


def test_the_tolerance_is_documented_where_it_will_be_read():
    doc = crossings.__doc__ or ""
    assert "1e-3" in doc
    assert "0.0004" in doc


# ── §2.1 the arc convention ──────────────────────────────────────────────

def test_the_profile_schema_states_how_an_arc_is_placed():
    schema = next(tool for tool in SketcherTools(connection=None).get_tool_definitions()
                  if tool["name"] == "catia_sketch_profile")
    text = schema["inputSchema"]["properties"]["segments"]["description"]
    assert "STARTS at start_angle" in text
    assert "SHORT way round" in text
    assert "major=true" in text
    assert "PREFER p1/p2" in text
    # The consequence the reporter hit, named so the next reader recognises it.
    assert "15 of 21 junctions" in text


def test_the_profile_description_also_says_the_short_way_wins():
    schema = next(tool for tool in SketcherTools(connection=None).get_tool_definitions()
                  if tool["name"] == "catia_sketch_profile")
    text = schema["description"]
    assert "SHORT way round" in text
    assert "major=true" in text


# ── §2.3 the dead handle ─────────────────────────────────────────────────

class _Sel:
    """A selection that refuses everything, like a stale feature handle."""

    def Clear(self) -> None:
        pass

    def Add(self, _feature) -> None:
        raise RuntimeError("(-2147352567, '发生意外。', (0, 'CATIASelection', '方法 Add 失败'))")

    def Delete(self) -> None:
        pass


class _Shape:
    Name = "凸台.1"


class _Sketch:
    Name = "草图.1"


class _Shapes:
    Count = 1

    def Item(self, _index):
        return _Shape()


class _Sketches:
    Count = 1

    def Item(self, _index):
        return _Sketch()


class _Body:
    Name = "零件几何体"
    Shapes = _Shapes()
    Sketches = _Sketches()


class _Bodies:
    Count = 1

    def Item(self, _index):
        return _Body()


class _Part:
    Bodies = _Bodies()

    def Update(self) -> None:
        pass


class _Conn:
    hso = _Sel()

    def ensure_connected(self) -> None:
        pass

    def get_active_part(self):
        return _Part()

    def get_active_part_body(self):
        return _Body()

    def refresh_display(self) -> None:
        pass


def test_a_feature_the_selection_refuses_answers_with_a_code_not_a_com_error():
    tools = PartDesignTools(connection=_Conn())
    tools.conn = _Conn()
    tools._sketch_names = lambda: ["草图.1"]  # type: ignore[method-assign]
    with pytest.raises(CodedError) as caught:
        tools._delete_one({"feature_name": "草图.1"}, None)
    error = caught.value
    assert error.code == "DELETE_FAILED"
    message = str(error)
    assert "refused to delete" in message
    # It has to say what to do next, not just fail.
    assert "catia_update_part" in message
    assert "catia_list_features" in message


# ── §2.1 the endpoint form of an arc in a profile ────────────────────────

def test_a_profile_arc_given_as_endpoints_reaches_the_arc_tool(monkeypatch):
    """The endpoint form was documented and refused, before 0.3.112.

    _plan_segment turns p1/p2 into angles and then clears them to None, and its
    result carries no 'p1' key - so _draw_profile handed _draw_arc three Nones
    and it answered "needs either start_angle and end_angle, or p1 and p2".
    """
    tools = SketcherTools(connection=None)
    monkeypatch.setattr(tools, "_ensure_sketch_open", lambda: None)
    calls: list[tuple] = []
    monkeypatch.setattr(tools, "_draw_line", lambda *a: calls.append(("line", a)))
    monkeypatch.setattr(tools, "_draw_arc",
                        lambda *a, **k: calls.append(("arc", a, k)))
    reply = tools._draw_profile([
        {"type": "line", "p1": [0, 0], "p2": [40, 0]},
        {"type": "arc", "center": [40, 20], "radius": 20,
         "p1": [40, 0], "p2": [40, 40]},
    ], 0.05)
    arc = next(call for call in calls if call[0] == "arc")
    assert list(arc[2]["p1"]) == [40.0, 0.0]
    assert list(arc[2]["p2"]) == [40.0, 40.0]
    # No angles were given, so none may be invented for the tool call.
    assert arc[1][3] is None and arc[1][4] is None
    # Two elements cannot close a loop; what matters is that both were drawn.
    assert "2 segment(s)" in reply


def test_a_profile_arc_given_as_angles_keeps_the_angle_form(monkeypatch):
    """The control: the angle form must not be converted into endpoints."""
    tools = SketcherTools(connection=None)
    monkeypatch.setattr(tools, "_ensure_sketch_open", lambda: None)
    calls: list[tuple] = []
    monkeypatch.setattr(tools, "_draw_line", lambda *a: calls.append(("line", a)))
    monkeypatch.setattr(tools, "_draw_arc",
                        lambda *a, **k: calls.append(("arc", a, k)))
    tools._draw_profile([
        {"type": "line", "p1": [0, 20], "p2": [60, 20]},
        {"type": "arc", "center": [60, 0], "radius": 20,
         "start_angle": 90, "end_angle": -90, "direction": "cw"},
    ], 0.05)
    arc = next(call for call in calls if call[0] == "arc")
    assert arc[1][3] == 90.0 and arc[1][4] == -90.0
    assert arc[2]["p1"] is None and arc[2]["p2"] is None


# ── §3 the elapsed time ──────────────────────────────────────────────────

def test_the_all_edges_fillet_reports_how_long_it_took(monkeypatch):
    tools = PartDesignTools(connection=_Conn())
    tools.conn = _Conn()
    monkeypatch.setattr(tools, "_prepare_body", lambda part: _Body())
    monkeypatch.setattr(tools, "_edge_table",
                        lambda body: [{"index": 1, "bounded_by": ["A", "B"],
                                       "length": 10.0}])
    monkeypatch.setattr(tools, "_fillet_many",
                        lambda radius, specs, propagation, sequential_retry=True:
                        "Fillet R1.5: 1/1 edge(s) done")
    reply = tools._fillet_all_edges(1.5, 0, 200)
    assert reply.rstrip().endswith("s."), reply[-40:]
    assert " Took " in reply


def test_the_all_edges_chamfer_reports_how_long_it_took(monkeypatch):
    tools = PartDesignTools(connection=_Conn())
    tools.conn = _Conn()
    monkeypatch.setattr(tools, "_prepare_body", lambda part: _Body())
    monkeypatch.setattr(tools, "_edge_table",
                        lambda body: [{"index": 1, "bounded_by": ["A", "B"],
                                       "length": 10.0}])
    monkeypatch.setattr(tools, "_chamfer_many",
                        lambda length, angle, specs, sequential_retry=True:
                        "Chamfer: 1/1 edge(s) done")
    reply = tools._chamfer_all_edges(1.0, 45.0, 200)
    assert " Took " in reply
