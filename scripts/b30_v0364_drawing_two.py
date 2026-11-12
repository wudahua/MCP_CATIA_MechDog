"""Two failures from the all-tools run, isolated:

  1. Selection.Search("Topology.Face,sel") -> 方法 Search 失败
  2. DrawingDimensions.Add(code, elems, pts, 3) -> 方法 Add 失败

Reads the real parameter names out of the type library instead of guessing, and
tries the argument forms that are plausible.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.sketcher import SketcherTools


def say(message: str) -> None:
    print(message, flush=True)


def members(obj: object, wanted: str) -> list[tuple[str, str, int, int]]:
    """(member, argnames, cArgs, invkind) for one method of a live object."""
    rows = []
    try:
        info = obj._oleobj_.GetTypeInfo()
        attributes = info.GetTypeAttr()
        for slot in range(attributes.cFuncs):
            function = info.GetFuncDesc(slot)
            names = info.GetNames(function.memid)
            if not names or names[0].lower() != wanted.lower():
                continue
            rows.append((names[0], ", ".join(names[1:]), function.args,
                         function.invkind))
    except Exception as error:  # noqa: BLE001
        say(f"   typeinfo unreadable: {str(error).splitlines()[0][:90]}")
    return rows


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
    part_doc.Activate()

    say("=== 1. Selection.Search forms ===")
    selection = part_doc.Selection
    last_shape = part.MainBody.Shapes.Item(part.MainBody.Shapes.Count)
    for query in ("Topology.Face,sel", "Topology.Face,all", "Topology.Face",
                  "Topology.Face,sel ", "Topology.Face,all "):
        try:
            selection.Clear()
            selection.Add(last_shape)
            selection.Search(query)
            say(f"   {query!r:24s} -> count {selection.Count}")
            if selection.Count:
                first = selection.Item(1)
                try:
                    ref = first.Reference
                    spa = part_doc.GetWorkbench("SPAWorkbench")
                    measurable = spa.GetMeasurable(ref)
                    plane = [float(measurable.GetPlane()[i]) for i in range(9)]
                    say(f"      first face reference ok, GetPlane[0:3] "
                        f"{[round(v, 3) for v in plane[:3]]}")
                except Exception as error:  # noqa: BLE001
                    say(f"      reference/plane failed: {str(error).splitlines()[0][:90]}")
        except Exception as error:  # noqa: BLE001
            say(f"   {query!r:24s} -> FAILED {str(error).splitlines()[0][:100]}")
    try:
        selection.Clear()
    except Exception:  # noqa: BLE001
        pass

    say("\n=== 2. DrawingDimensions.Add argument forms ===")
    drawing = conn.documents.Add("Drawing")
    sheet = drawing.Sheets.ActiveSheet
    views = sheet.Views
    view = views.Add("Manual View")
    try:
        view.Activate()
    except Exception as error:  # noqa: BLE001
        say(f"   Activate: {str(error).splitlines()[0][:70]}")
    factory = view.Factory2D
    factory.CreateLine(0.0, 0.0, 40.0, 0.0)
    factory.CreateLine(0.0, 0.0, 0.0, 25.0)
    geometry = view.GeometricElements
    say(f"   elements: {geometry.Count}")

    for row in members(view.Dimensions, "Add"):
        say(f"   Add({row[1]}) args={row[2]} invkind={row[3]}")

    elem1 = geometry.Item(2)
    elem2 = geometry.Item(3)
    forms = [
        ("list, list, pt=3", lambda: view.Dimensions.Add(
            0, [elem1, elem2], [0.0, 0.0, 0.0, 0.0], 3)),
        ("tuple, tuple, pt=3", lambda: view.Dimensions.Add(
            0, (elem1, elem2), (0.0, 0.0, 0.0, 0.0), 3)),
        ("single elem, pt=3", lambda: view.Dimensions.Add(
            0, [elem1], [0.0, 0.0], 3)),
        ("list, list, pt=0", lambda: view.Dimensions.Add(
            0, [elem1, elem2], [0.0, 0.0, 0.0, 0.0], 0)),
        ("length type=2 on one", lambda: view.Dimensions.Add(
            2, [elem1], [0.0, 0.0], 3)),
    ]
    for label, call in forms:
        before = view.Dimensions.Count
        try:
            dim = call()
            after = view.Dimensions.Count
            say(f"   {label:26s} -> {dim}  count {before}->{after}")
        except Exception as error:  # noqa: BLE001
            say(f"   {label:26s} -> FAILED {str(error).splitlines()[0][:110]}")

    say("\n=== close ===")
    for index in range(conn.documents.Count, 0, -1):
        try:
            conn.documents.Item(index).Close()
            say("   closed one")
        except Exception as error:  # noqa: BLE001
            say(f"   close failed: {str(error).splitlines()[0][:90]}")


if __name__ == "__main__":
    main()
