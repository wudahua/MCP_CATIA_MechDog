# -*- coding: utf-8 -*-
"""Regression tests for the 2026-10-28 evening report on 0.3.32.

Two findings, both narrow and both about a name that leads nowhere:

1. Three error messages told the caller to run ``catia_list_sketches`` - a tool
   this server does not have (101 tools, and that is not one of them). It was the
   remedy offered in exactly the situation 0.3.31 created: a sketch deleted by a
   rollback and rebuilt under a new name. The report asked for a guard, so there
   is one here: every ``catia_*`` token in a string constant of this package has
   to be a tool the server actually serves. It found a fourth phantom on its
   first run (``catia_drawing_add_view``, in `catia_list_faces`' description).
2. GSD resolved element names from the tree verbatim, so a rebuilt sketch that
   `catia_pad` and `catia_sketch_get_geometry` both resolved was "not found" for
   `catia_gsd_spline(element='草图.2')` - and the message then suggested drawing
   it again, which was not needed.
"""

from __future__ import annotations

import ast
import pathlib
import re
from types import SimpleNamespace

import pytest

#: Tokens that look like tool names but are not tools: the package name, the
#: VBScript entry points the measurement sweep injects, and the "catia_gsd_" /
#: "catia_bbox_" prefixes that appear in f-strings before the suffix is known.
_NOT_A_TOOL = {"catia_mcp", "catia_type"}
_NOT_A_TOOL_PREFIXES = ("catia_mcp_", "catia_guard_", "catia_bbox_")


def _tool_names() -> set[str]:
    from catia_mcp.server import CATIAMCPServer

    srv = CATIAMCPServer()
    names: set[str] = set()
    for module in srv._tool_modules:
        names.update(d["name"] for d in module.get_tool_definitions())
    return names


def test_every_tool_name_in_a_message_exists():
    """A message may only send the caller to a tool the server serves."""
    tools = _tool_names()
    assert len(tools) > 100
    root = pathlib.Path(__file__).resolve().parent.parent / "catia_mcp"
    unknown: dict[str, set[str]] = {}
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            for token in re.findall(r"\bcatia_[a-z0-9_]+", node.value):
                if token in tools or token in _NOT_A_TOOL:
                    continue
                if token.endswith("_") or token.startswith(_NOT_A_TOOL_PREFIXES):
                    continue
                unknown.setdefault(token, set()).add(path.name)
    assert unknown == {}, (
        "these names look like tools but are not served: "
        + ", ".join(f"{name} (in {', '.join(sorted(files))})"
                    for name, files in sorted(unknown.items())))


# ─────────────── 1. the phantom name is gone from the messages that had it
def test_the_sketch_listings_the_messages_name_are_real():
    from catia_mcp.tools.gsd import GSDTools
    from catia_mcp.tools.part_design import PartDesignTools

    for source in (inspect_source(GSDTools._outline_points),
                   inspect_source(PartDesignTools._section_reference),
                   inspect_source(PartDesignTools._delete_feature)):
        assert "catia_list_sketches" not in source
    assert "catia_list_features" in inspect_source(GSDTools._outline_points)
    assert "catia_list_features" in inspect_source(PartDesignTools._delete_feature)


def inspect_source(function) -> str:
    import inspect

    return inspect.getsource(function)


def test_the_face_listing_does_not_point_at_a_drawing_tool_this_fork_dropped():
    from catia_mcp.tools.part_design import PartDesignTools

    definition = [d for d in PartDesignTools(connection=None).get_tool_definitions()
                  if d["name"] == "catia_list_faces"][0]
    assert "catia_drawing_add_view" not in definition["description"]
    assert "body_name" in definition["description"]


# ─────────────── 2. GSD resolves a rebuilt sketch's old name
class _Collection:
    def __init__(self, items):
        self._items = list(items)
        self.Count = len(self._items)

    def Item(self, key):
        if isinstance(key, int):
            return self._items[key - 1]
        for item in self._items:
            if getattr(item, "Name", None) == key:
                return item
        raise KeyError(key)


def _gsd_tools(alias=None, sketches=(("草图.4",),), outline_name="草图.2"):
    from catia_mcp.tools.gsd import GSDTools

    sketch_objects = [SimpleNamespace(Name=name[0] if isinstance(name, tuple) else name)
                      for name in sketches]
    body = SimpleNamespace(Name="PartBody",
                           Sketches=_Collection(sketch_objects))
    geoset = SimpleNamespace(Name="Geometrical Set.1",
                             HybridShapes=_Collection([]),
                             HybridSketches=_Collection([]))
    part = SimpleNamespace(
        Bodies=_Collection([body]),
        HybridBodies=_Collection([geoset]),
        OriginElements=SimpleNamespace())
    conn = SimpleNamespace(
        get_active_part=lambda: part,
        get_active_part_body=lambda: body,
        active_document=SimpleNamespace(Name="P.CATPart"),
        sketch_alias=lambda name: alias if name == "草图.2" else None,
        sketch_outlines={f"P.CATPart::{outline_name}": {
            "placement": {"base": "xy", "distance": 0.0},
            "elements": [{"kind": "rectangle", "x1": 0.0, "y1": 0.0,
                          "x2": 20.0, "y2": 6.0}]}})
    tools = GSDTools(connection=None)
    tools.conn = conn
    return tools


def test_an_element_is_found_under_the_name_a_rebuild_gave_it():
    """catia_pad resolved 草图.2; catia_gsd_spline(element='草图.2') did not."""
    tools = _gsd_tools(alias="草图.4", sketches=[("草图.4",)])
    found = tools._find_element("草图.2")
    assert found.Name == "草图.4"


def test_a_sketch_that_only_exists_under_its_alias_still_counts_as_present():
    tools = _gsd_tools(alias="草图.4", sketches=[("草图.4",)])
    assert tools._sketch_exists("草图.2") is True
    assert tools._sketch_exists("草图.9") is False


def test_the_outline_of_a_rebuilt_sketch_is_still_there():
    """The record survives under the old name, and the tree has the new one."""
    tools = _gsd_tools(alias="草图.4", sketches=[("草图.4",)])
    coords, note = tools._outline_points("草图.2", closed=True)
    assert coords[0] == [0.0, 0.0, 0.0]
    assert coords[2] == [20.0, 6.0, 0.0]
    assert "rebuilt as '草图.4'" in note


def test_an_outline_recorded_only_under_the_new_name_is_found():
    tools = _gsd_tools(alias="草图.4", sketches=[("草图.4",)])
    tools.conn.sketch_outlines = {
        "P.CATPart::草图.4": {
            "placement": {"base": "xy", "distance": 0.0},
            "elements": [{"kind": "rectangle", "x1": 0.0, "y1": 0.0,
                          "x2": 10.0, "y2": 5.0}]}}
    coords, note = tools._outline_points("草图.2", closed=True)
    assert coords[2] == [10.0, 5.0, 0.0]
    assert "rebuilt as '草图.4'" in note


def test_a_missing_outline_names_the_listing_that_can_help():
    tools = _gsd_tools(alias="草图.4", sketches=[("草图.4",)], outline_name="草图.99")
    with pytest.raises(RuntimeError) as caught:
        tools._outline_points("草图.2", closed=False)
    message = str(caught.value)
    assert "No outline recorded for sketch '草图.2'" in message
    assert "re-created as '草图.4'" in message
    assert "catia_list_sketches" not in message


def test_a_name_that_is_gone_everywhere_says_so_and_offers_a_real_tool():
    tools = _gsd_tools(alias=None, sketches=[("草图.4",)], outline_name="草图.2")
    with pytest.raises(RuntimeError) as caught:
        tools._outline_points("草图.2", closed=False)
    message = str(caught.value)
    # The record is there under the old name, but no sketch answers to it now.
    assert not tools._sketch_exists("草图.2")
    assert "catia_list_features" in message
    assert "catia_list_sketches" not in message


def test_an_element_not_found_message_names_the_alias_it_tried():
    tools = _gsd_tools(alias="草图.4", sketches=[("草图.4",)])
    tools._lookup_element = lambda part, name: None
    tools._element_names = lambda part: []
    tools._documents_holding = lambda name: []
    with pytest.raises(RuntimeError) as caught:
        tools._find_element("草图.2")
    message = str(caught.value)
    assert "'草图.2' was re-created as '草图.4'" in message
    assert "not in the tree either" in message


# ─────────────── 3. a loft section may name a rebuilt sketch too
def test_a_loft_section_may_name_a_rebuilt_sketch():
    from catia_mcp.tools.part_design import PartDesignTools

    body = SimpleNamespace(Sketches=_Collection(
        [SimpleNamespace(Name="草图.3")]))
    part = SimpleNamespace(
        Bodies=_Collection([body]),
        HybridBodies=_Collection([]),
        CreateReferenceFromObject=lambda obj: ("REF", obj.Name))
    conn = SimpleNamespace(
        get_active_part=lambda: part,
        get_active_part_body=lambda: body,
        sketch_alias=lambda name: "草图.3" if name == "草图.2" else None)
    tools = PartDesignTools(connection=None)
    tools.conn = conn
    assert tools._section_reference("草图.2") == ("REF", "草图.3")
    # A name that resolves to nothing still fails, and now says why.
    with pytest.raises(RuntimeError) as caught:
        tools._section_reference("草图.9")
    assert "catia_list_features" in str(caught.value)
