"""Pins the four 0.3.9 reports: schema gap, wrong claims, delete rebuild, path."""

from __future__ import annotations

import inspect

from catia_mcp.tools.assembly import AssemblyTools
from catia_mcp.tools.measurement import MeasurementTools
from catia_mcp.tools.sketcher import SketcherTools


def test_get_inertia_declares_the_document_it_accepts() -> None:
    """The implementation took `document` since 0.3.9 but the schema did not.

    A client that builds its form from tools/list therefore could not use the
    only way to measure a named part (reported 2026-10-24).
    """
    tool = [d for d in MeasurementTools(connection=None).get_tool_definitions()
            if d["name"] == "catia_get_inertia"][0]
    props = tool["inputSchema"]["properties"]
    assert "document" in props
    assert props["document"]["type"] == "string"


def test_bounding_box_declares_document_too() -> None:
    tool = [d for d in MeasurementTools(connection=None).get_tool_definitions()
            if d["name"] == "catia_get_bounding_box"][0]
    assert "document" in tool["inputSchema"]["properties"]


def test_add_component_normalises_the_path_and_checks_the_result() -> None:
    """AddComponentsFromFiles rejects forward slashes on B32 and then blocks on a
    modal dialog; the old code claimed success either way (reported 2026-10-24).
    """
    source = inspect.getsource(AssemblyTools._add_component)
    assert "os.path.normpath" in source
    assert "DisplayFileAlerts" in source
    assert "no component appeared" in source


def test_delete_element_rebuilds_the_part_not_only_the_sketch() -> None:
    source = inspect.getsource(SketcherTools._delete_element)
    assert "part.UpdateObject(sketch)" in source
    assert "part.Update()" in source
