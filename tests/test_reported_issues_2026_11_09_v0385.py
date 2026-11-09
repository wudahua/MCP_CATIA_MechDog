"""0.3.85: the three conveniences the 2026-11-09 report asked for.

The report named three APIs. Two of them turned out narrower than it assumed, and
the tests here are as much about the MEASURED boundaries as about the code:

  * ``AddNewBoundary(edge, support, propagation)`` IS the loop selector - it
    returns one loop (cone: 62.832 / 125.664 mm) where the one-argument version
    returns both (188.496 mm) - but only on a single feature;
  * on a JOIN result every border edge fails at update, so the reply has to say
    that instead of blaming the caller's geometry;
  * ``cap_center`` closes the hole with the hole's OWN edge (rotated and joined),
    because a circle either lies wholly inside a five-sided hole or cuts a wavy
    border no join will take;
  * ``catia_gsd_fill`` accepts ``Edge.N``, and the extract behind it returns the
    whole ring - a fact the description states rather than hides.
"""
from __future__ import annotations

import types

import pytest

from catia_mcp.results import CodedError


class _Shape:
    """A hybrid shape: a name, and the append/update calls CATIA would take."""

    def __init__(self, name: str = "") -> None:
        self.Name = name

    def AddElement(self, reference):
        self.elements = getattr(self, "elements", []) + [reference]


class _Fill(_Shape):
    def __init__(self) -> None:
        super().__init__()
        self.bounds = []

    def AddBound(self, reference):
        self.bounds.append(reference)


class _Factory:
    def __init__(self, refuse_edges=(), fail_on=None) -> None:
        self.refuse_edges = set(refuse_edges)
        self.fail_on = fail_on
        self.rotations: list[float] = []
        self.joins = 0
        self.fills = 0

    def AddNewBoundary(self, edge, support, propagation):
        if edge in self.refuse_edges:
            raise RuntimeError("方法 AddNewBoundary 失败")
        return _Shape()

    def AddNewRotate(self, reference, axis, angle):
        if self.fail_on == "rotate":
            raise RuntimeError("方法 AddNewRotate 失败")
        self.rotations.append(angle)
        return _Shape()

    def AddNewJoin(self, first, second):
        if self.fail_on == "join":
            raise RuntimeError("方法 UpdateObject 失败")
        self.joins += 1
        return _Shape()

    def AddNewFill(self):
        self.fills += 1
        return _Fill()


class _Selection:
    def Clear(self):
        pass


def _tools(factory=None, rows=None, update_error=None):
    """GSDTools with the COM surface _boundary_loop and _cap_centre use."""
    from catia_mcp.tools.gsd import GSDTools

    factory = factory or _Factory()
    rows = rows if rows is not None else [(1, "edge-1", 62.8319),
                                          (2, "edge-2", 125.6637)]
    part = types.SimpleNamespace(
        Name="PART", InWorkObject=None,
        FindObjectByName=lambda name: None,
        CreateReferenceFromObject=lambda obj: ("ref", obj),
        UpdateObject=lambda shape: None,
    )
    tools = GSDTools(connection=None)
    tools.conn = types.SimpleNamespace(
        ensure_connected=lambda: None,
        get_active_part=lambda: part,
        active_document=types.SimpleNamespace(Selection=_Selection()),
        hso=_Selection(),
        refresh_display=lambda: None,
    )
    tools._factory = lambda: factory                      # type: ignore[method-assign]
    tools._ref = lambda name: ("ref", name)               # type: ignore[method-assign]
    tools._get_geoset = lambda: types.SimpleNamespace(    # type: ignore[method-assign]
        Name="GSD_Set", AppendHybridShape=lambda shape: None)
    tools._geoset_border_edges = lambda: (_Selection(), rows)  # type: ignore[method-assign]

    def completed(shape, name, message, geoset):
        if update_error is not None:
            raise RuntimeError(f"CATIA rejected the feature: {update_error}")
        return f"{message} Element: '{shape.Name}'"

    tools._completed = completed                          # type: ignore[method-assign]
    return tools, factory, part


# ── catia_gsd_boundary: one loop by index ───────────────────────────────


def test_an_edge_index_of_zero_is_refused_before_catia_is_asked():
    """Loops are counted from 1; 0 is not a loop and must not reach the factory."""
    tools, factory, _part = _tools()
    with pytest.raises(CodedError) as caught:
        tools.execute("catia_gsd_boundary", {"surface": "CONE", "edge": 0})
    assert caught.value.code == "EDGE_OUT_OF_RANGE"
    assert factory.joins == 0


def test_a_propagation_that_is_not_a_catia_mode_is_refused():
    tools, _factory, _part = _tools()
    with pytest.raises(CodedError) as caught:
        tools.execute("catia_gsd_boundary",
                      {"surface": "CONE", "edge": 1, "propagation": 7})
    assert caught.value.code == "PROPAGATION_OUT_OF_RANGE"
    assert "whole border" in str(caught.value)


def test_the_loop_reply_says_which_loop_and_what_a_fill_on_it_covers():
    tools, _factory, _part = _tools()
    reply = tools.execute("catia_gsd_boundary",
                          {"surface": "CONE", "edge": 2, "name": "TOP_LOOP"})
    assert "Loop #2" in reply
    assert "125.6637" in reply          # the length of the loop that was made
    assert "THIS loop only" in reply


def test_an_index_past_the_last_loop_lists_what_the_surface_has():
    tools, _factory, _part = _tools()
    with pytest.raises(CodedError) as caught:
        tools.execute("catia_gsd_boundary", {"surface": "CONE", "edge": 5})
    message = str(caught.value)
    assert "exposes 2 surface-border edge(s)" in message
    assert "62.8319" in message and "125.6637" in message


def test_no_border_edge_at_all_is_a_precondition_not_an_empty_answer():
    tools, _factory, _part = _tools(rows=[])
    with pytest.raises(CodedError) as caught:
        tools.execute("catia_gsd_boundary", {"surface": "CONE", "edge": 1})
    assert caught.value.code == "NO_BORDER_EDGES"


def test_an_edge_belonging_to_another_surface_is_refused_with_a_next_step():
    """The factory rejects an edge that is not on the support - offer nothing."""
    factory = _Factory(refuse_edges={"edge-1", "edge-2"})
    tools, _factory, _part = _tools(factory=factory)
    with pytest.raises(CodedError) as caught:
        tools.execute("catia_gsd_boundary", {"surface": "OTHER", "edge": 1})
    assert caught.value.code == "BOUNDARY_EDGE_NOT_ON_SURFACE"
    assert "no 'edge'" in str(caught.value)


def test_a_join_result_is_named_as_the_reason_the_loop_could_not_be_built():
    """Measured: all 30 border edges of a five-patch join failed at update."""
    tools, _factory, _part = _tools(
        update_error=RuntimeError("方法 UpdateObject 失败"))
    with pytest.raises(CodedError) as caught:
        tools.execute("catia_gsd_boundary", {"surface": "BASE", "edge": 1})
    message = str(caught.value)
    assert caught.value.code == "BOUNDARY_LOOP_FAILED"
    assert "JOIN result" in message
    assert "cap_center" in message


def test_the_boundary_description_no_longer_claims_there_is_no_selector():
    """0.3.84 said splitting further needed a split; the selector exists."""
    from catia_mcp.tools.gsd import GSDTools

    schema = next(entry for entry in GSDTools(connection=None).get_tool_definitions()
                  if entry["name"] == "catia_gsd_boundary")
    description = schema["description"]
    assert "id='edge'" not in description
    assert "TAKING ONE LOOP" in description
    assert "SINGLE-FEATURE tool" in description
    properties = schema["inputSchema"]["properties"]
    assert properties["edge"]["type"] == "integer"
    assert properties["propagation"]["enum"] == [0, 1, 2]


# ── catia_gsd_fill: Edge.N as a boundary ────────────────────────────────


def test_fill_extracts_an_edge_instead_of_passing_a_raw_topology_cell():
    """Fill.AddBound rejects a topological edge; the extract makes it a curve."""
    from catia_mcp.tools.gsd import GSDTools

    calls: list[str] = []

    class _FillFactory:
        def AddNewFill(self):
            fill = _Fill()

            def bound(reference):
                calls.append(str(reference))
            fill.AddBound = bound          # type: ignore[method-assign]
            return fill

    tools = GSDTools(connection=None)
    tools.conn = types.SimpleNamespace(
        ensure_connected=lambda: None,
        active_document=types.SimpleNamespace(Selection=_Selection()),
        refresh_display=lambda: None,
    )
    tools._factory = lambda: _FillFactory()               # type: ignore[method-assign]
    tools._topology_reference = lambda name: f"extracted({name})"  # type: ignore[method-assign]
    tools._ref = lambda name: f"named({name})"            # type: ignore[method-assign]
    tools._finish = lambda shape, name, message: message  # type: ignore[method-assign]

    tools.execute("catia_gsd_fill", {"boundaries": ["Edge.3", "HOLE"]})
    assert calls == ["extracted(Edge.3)", "named(HOLE)"]


def test_fill_documents_that_an_extract_returns_the_whole_ring():
    from catia_mcp.tools.gsd import GSDTools

    schema = next(entry for entry in GSDTools(connection=None).get_tool_definitions()
                  if entry["name"] == "catia_gsd_fill")
    description = schema["inputSchema"]["properties"]["boundaries"]["description"]
    assert "Edge.N" in description
    assert "WHOLE border ring" in description
    assert "91.1450" in description


# ── catia_gsd_circ_pattern: cap_center ─────────────────────────────────


def test_cap_center_without_a_curve_says_there_is_no_way_to_guess_it():
    tools, _factory, _part = _tools()
    with pytest.raises(CodedError) as caught:
        tools.execute("catia_gsd_circ_pattern",
                      {"element": "SECTOR", "count": 5, "cap_center": True})
    assert caught.value.code == "CAP_CURVE_REQUIRED"
    assert "cap_curve" in str(caught.value)
    assert "91.1450" in str(caught.value)


def test_cap_center_on_a_partial_array_is_refused_before_anything_is_built():
    tools, factory, _part = _tools()
    with pytest.raises(CodedError) as caught:
        tools.execute("catia_gsd_circ_pattern",
                      {"element": "SECTOR", "count": 5, "total_angle": 180,
                       "cap_center": True, "cap_curve": "INNER"})
    assert caught.value.code == "CAP_NOT_FULL_TURN"
    assert factory.rotations == []


def test_capping_the_centre_rotates_the_hole_edge_and_joins_three_times():
    """The measured chain: rotate count-1, join the border, fill, join the base."""
    tools, factory, part = _tools()
    reply = tools._cap_centre(part, factory,
                              types.SimpleNamespace(
                                  Name="GSD_Set",
                                  AppendHybridShape=lambda shape: None),
                              ("axis", "z"), "INNER", 5, 72.0,
                              types.SimpleNamespace(Name="BASE"))
    assert factory.rotations == [72.0, 144.0, 216.0, 288.0]
    assert factory.fills == 1
    assert factory.joins == 2            # hole border, then base + cap
    assert "CENTRE CAPPED" in reply
    assert "INNER" in reply
    assert "no circle" in reply


def test_a_failed_cap_rolls_back_what_it_built_and_leaves_the_array_alone():
    tools, factory, part = _tools(factory=_Factory(fail_on="join"))
    with pytest.raises(CodedError) as caught:
        tools._cap_centre(part, factory,
                          types.SimpleNamespace(
                              Name="GSD_Set",
                              AppendHybridShape=lambda shape: None),
                          ("axis", "z"), "INNER", 5, 72.0,
                          types.SimpleNamespace(Name="BASE"))
    message = str(caught.value)
    assert caught.value.code == "CAP_CENTRE_FAILED"
    assert "were deleted" in message
    assert "the array itself was NOT touched" in message


def test_the_pattern_description_says_the_cap_is_not_a_circle():
    from catia_mcp.tools.gsd import GSDTools

    schema = next(entry for entry in GSDTools(connection=None).get_tool_definitions()
                  if entry["name"] == "catia_gsd_circ_pattern")
    description = schema["description"]
    assert "CAPPING THE CENTRE" in description
    assert "5*cos36 = 4.045" in description
    properties = schema["inputSchema"]["properties"]
    assert properties["cap_center"]["default"] is False
    assert "cap_curve" in properties
