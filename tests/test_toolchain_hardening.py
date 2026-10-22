# -*- coding: utf-8 -*-
"""Regression tests for the hardening pass of 2026-10-22.

These cover the four failures that cost the most time on the P03 propeller:
measurements that were not measurements, a batch of edges that could not be
applied in one call, no way to undo an experiment, and a "method failed" with
no state attached to it.
"""
import json

import pytest

from catia_mcp.server import CATIAMCPServer
from catia_mcp.tools.measurement import _pick
from catia_mcp.tools.part_design import PartDesignTools


# ─────────────────────────────────────────── measurement tells the truth
class _Measurable:
    def __init__(self, length=0.0, area=0.0):
        self.Length = length
        self.Area = area


class _Spa:
    def __init__(self, measurable):
        self._m = measurable

    def GetMeasurable(self, _ref):  # noqa: N802 - CATIA spelling
        return self._m


class _Cell:
    Reference = object()


def test_placeholder_length_is_reported_as_unknown_not_as_a_number():
    """SPA answers 2e7 instead of raising. That must never reach the caller."""
    tools = PartDesignTools(connection=None)
    assert tools._cell_measure(_Spa(_Measurable(length=2e7)), _Cell(), "Length") is None
    assert tools._cell_measure(_Spa(_Measurable(length=8e7)), _Cell(), "Length") is None


def test_real_measurements_pass_through_in_millimetres():
    tools = PartDesignTools(connection=None)
    assert tools._cell_measure(_Spa(_Measurable(length=30.0)), _Cell(), "Length") == 30.0
    # SPA hands areas back in m2; the tool publishes mm2.
    assert tools._cell_measure(_Spa(_Measurable(area=0.001)), _Cell(), "Area") == pytest.approx(1000.0)


def test_zero_area_is_unknown_because_a_face_cannot_have_no_area():
    tools = PartDesignTools(connection=None)
    assert tools._cell_measure(_Spa(_Measurable(area=0.0)), _Cell(), "Area") is None


def test_measurement_without_a_workbench_degrades_instead_of_crashing():
    tools = PartDesignTools(connection=None)
    assert tools._cell_measure(None, _Cell(), "Length") is None


# ─────────────────────────────────────────── batch fillet
def test_fillet_batch_applies_every_edge_and_says_so():
    tools = PartDesignTools(connection=None)
    applied = []
    tools._fillet_one = lambda radius, name: applied.append((radius, name)) or "ok"

    report = tools._fillet({"radius": 1.27, "edge_names": ["Edge.4", "Edge.7"]})

    assert applied == [(1.27, "Edge.4"), (1.27, "Edge.7")]
    assert "2/2" in report


def test_fillet_batch_survives_a_rejected_edge_and_names_it():
    tools = PartDesignTools(connection=None)

    def flaky(radius, name):
        if name == "Edge.9":
            raise RuntimeError("(-2147352567, '发生意外。', (0, 'CATIAShapeFactory', '方法失败', ...)")
        return "ok"

    tools._fillet_one = flaky
    report = tools._fillet({"radius": 3.81, "edge_names": ["Edge.4", "Edge.9"]})

    assert "1/2" in report
    assert "Edge.9" in report
    # The message has to point at the real cause, not just record the failure.
    assert "topology" in report


def test_single_fillet_still_takes_the_plain_edge_name():
    tools = PartDesignTools(connection=None)
    seen = []
    tools._fillet_one = lambda radius, name: seen.append(name) or "ok"

    tools._fillet({"radius": 2.0, "edge_name": "Edge.1"})

    assert seen == ["Edge.1"]


# ─────────────────────────────────────────── measurement argument aliases
def test_element_aliases_resolve_and_the_error_names_them_all():
    assert _pick({"elem1_name": "Edge.3"}, "element1", "elem1_name") == "Edge.3"
    assert _pick({"element1_name": "Edge.4"}, "element1", "element1_name") == "Edge.4"
    with pytest.raises(ValueError) as caught:
        _pick({"nope": 1}, "element1", "elem1_name", "element1_name")
    assert "element1" in str(caught.value)
    assert "elem1_name" in str(caught.value)


# ─────────────────────────────────────────── failure messages carry state
class _BrokenConnection:
    def __getattr__(self, name):
        raise RuntimeError("No active document")

    def ensure_connected(self):
        return None


def test_session_state_never_raises_even_with_nothing_connected():
    server = CATIAMCPServer()
    server.connection = _BrokenConnection()
    text = server._session_state()
    assert "document=" in text
    assert "Session state" not in text            # that prefix belongs to the caller


def test_failure_message_points_at_session_state():
    server = CATIAMCPServer()
    server.connection = _BrokenConnection()
    document_tools = server.document_tools
    document_tools.execute = lambda name, arguments: (_ for _ in ()).throw(
        RuntimeError("(-2147352567, '发生意外。', (0, 'CATIAShapeFactory', '方法失败', ...)")
    )
    result = server._execute_tool("catia_new_part", {})
    message = result.structuredContent["message"]
    assert message.startswith("(-2147352567")
    assert "Session state:" in message
    assert "document=" in message


# ─────────────────────────────────────────── new tool surfaces
def test_save_as_copy_exists_and_explains_the_saveas_limit():
    from catia_mcp.tools.document import DocumentTools

    tool = [d for d in DocumentTools(connection=None).get_tool_definitions()
            if d["name"] == "catia_save_as_copy"][0]
    text = tool["description"]
    assert "geometry" in text
    assert "Save As is refused" in text or "refused after" in text


def test_delete_feature_can_target_the_last_one_without_a_name():
    from catia_mcp.tools.part_design import PartDesignTools as PD

    tool = [d for d in PD(connection=None).get_tool_definitions()
            if d["name"] == "catia_delete_feature"][0]
    # feature_name must be optional, otherwise undoing your own last feature
    # needs CATIA's localized naming.
    assert "required" not in tool["inputSchema"]
    assert "index" in tool["inputSchema"]["properties"]


def test_circ_pattern_refusal_suggests_the_symmetry_route():
    from catia_mcp.tools.part_design import PartDesignTools as PD

    source = PD._circ_pattern.__code__.co_consts
    joined = " ".join(str(c) for c in source if isinstance(c, str))
    assert "catia_gsd_symmetry" in joined


def test_face_and_edge_listings_expose_the_element_type():
    for func in (PartDesignTools._list_edges, PartDesignTools._list_faces):
        import inspect

        assert 'entry["type"]' in inspect.getsource(func)


# ─────────────────────────────────────────── visibility
def test_set_visibility_is_registered_with_the_measured_enum_note():
    """Hiding is not cosmetic: leftover GSD surfaces show up in STL exports."""
    from catia_mcp.tools.part_design import PartDesignTools as PD

    tool = [d for d in PD(connection=None).get_tool_definitions()
            if d["name"] == "catia_set_visibility"][0]
    assert "show" in tool["inputSchema"]["properties"]
    text = tool["description"]
    assert "STL" in text
    assert "envelope" in text

    import inspect

    source = inspect.getsource(PD._set_visibility)
    # 0 shows, 1 hides - 2 is accepted and does nothing, which is why this was
    # silently broken for a while.
    assert "SetShow(0 if show else 1)" in source
    assert "HybridShapes" in source          # descend into the set
