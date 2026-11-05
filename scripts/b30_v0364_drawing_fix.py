"""The two fixes to validate before they go into the module.

  1. A planar face's plane, resolved the way this fork already resolves
     Face.N (topology_cells -> Reference -> SPAWorkbench.GetMeasurable.GetPlane)
     instead of upstream's hard-coded English Selection.Search.
  2. DrawingDimensions.Add with TWO elements: which form works, if any.
"""

from __future__ import annotations

import sys

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.sketcher import SketcherTools
from catia_mcp.topo_search import topology_cells


def say(message: str) -> None:
    print(message, flush=True)


def main() -> None:
    conn = CATIAConnection()
    conn.connect()
    for index in range(conn.documents.Count, 0, -1):
        try:
            conn.documents.Item(index).Close()
        except Exception:  # noqa: BLE001
            pass

    docs = DocumentTools(conn)
    sketch = SketcherTools(conn)
    design = PartDesignTools(conn)

    docs.execute("catia_new_part", {})
    sketch.execute("catia_create_sketch", {"plane": "xy"})
    sketch.execute("catia_sketch_centered_rectangle", {"width": 60, "height": 40})
    sketch.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"height": 10})
    part_doc = conn.active_document
    part = part_doc.Part
    body = part.MainBody
    spa = part_doc.GetWorkbench("SPAWorkbench")

    say("=== 1. face plane through topology_cells ===")
    cells = topology_cells(conn, body, "Face")
    say(f"   faces found: {len(cells)}")
    for index, cell in enumerate(cells, start=1):
        try:
            measurable = spa.GetMeasurable(cell.Reference)
            plane = measurable.GetPlane()
            values = [float(plane[i]) for i in range(9)]
            say(f"   Face.{index}: origin {[round(v, 2) for v in values[0:3]]} "
                f"dir1 {[round(v, 2) for v in values[3:6]]} "
                f"dir2 {[round(v, 2) for v in values[6:9]]}")
        except Exception as error:  # noqa: BLE001
            say(f"   Face.{index}: GetPlane failed - {str(error).splitlines()[0][:90]}")

    say("\n   control: does the old Search path fail on this install?")
    selection = part_doc.Selection
    try:
        selection.Clear()
        selection.Add(body)
        selection.Search("Topology.Face,sel")
        say(f"   English query worked, {selection.Count} items")
    except Exception as error:  # noqa: BLE001
        say(f"   English query FAILED: {str(error).splitlines()[0][:100]}")
    try:
        selection.Clear()
        selection.Add(body)
        selection.Search("拓扑.面,sel")
        say(f"   localized query worked, {selection.Count} items")
    except Exception as error:  # noqa: BLE001
        say(f"   localized query FAILED: {str(error).splitlines()[0][:100]}")
    try:
        selection.Clear()
    except Exception:  # noqa: BLE001
        pass

    say("\n=== 2. two-element dimension, parallel lines ===")
    drawing = conn.documents.Add("Drawing")
    sheet = drawing.Sheets.ActiveSheet
    view = sheet.Views.Add("Manual View")
    view.x = 200
    view.y = 150
    try:
        view.Activate()
    except Exception as error:  # noqa: BLE001
        say(f"   Activate: {str(error).splitlines()[0][:70]}")
    factory = view.Factory2D
    factory.CreateLine(0.0, 0.0, 40.0, 0.0)     # index 2
    factory.CreateLine(0.0, 20.0, 40.0, 20.0)   # index 3
    geometry = view.GeometricElements
    say(f"   elements: {geometry.Count}")
    first, second = geometry.Item(2), geometry.Item(3)

    forms = [
        ("distance 0, list[2], 4 zeros, 3", lambda: view.Dimensions.Add(
            0, [first, second], [0.0, 0.0, 0.0, 0.0], 3)),
        ("distance 0, list[2], 2 zeros, 3", lambda: view.Dimensions.Add(
            0, [first, second], [0.0, 0.0], 3)),
        ("distance 0, tuple[2], 4 zeros, 0", lambda: view.Dimensions.Add(
            0, (first, second), (0.0, 0.0, 0.0, 0.0), 0)),
        ("angle 4, list[2], 4 zeros, 3", lambda: view.Dimensions.Add(
            4, [first, second], [0.0, 0.0, 0.0, 0.0], 3)),
        ("length 2 on first only", lambda: view.Dimensions.Add(
            2, [first], [0.0, 0.0], 3)),
        ("radius 5 on first only", lambda: view.Dimensions.Add(
            5, [first], [0.0, 0.0], 3)),
    ]
    for label, call in forms:
        before = view.Dimensions.Count
        try:
            result = call()
            after = view.Dimensions.Count
            say(f"   {label:34s} -> {result}  count {before}->{after}")
        except Exception as error:  # noqa: BLE001
            say(f"   {label:34s} -> FAILED {str(error).splitlines()[0][:100]}")

    say("\n=== close ===")
    for index in range(conn.documents.Count, 0, -1):
        try:
            conn.documents.Item(index).Close()
        except Exception as error:  # noqa: BLE001
            say(f"   close failed: {str(error).splitlines()[0][:80]}")
    say(f"open at end: {conn.documents.Count}")


if __name__ == "__main__":
    main()
