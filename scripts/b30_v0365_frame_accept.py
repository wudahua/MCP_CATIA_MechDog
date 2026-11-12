"""The frame tools, driven through the server's own tool layer.

Ends with a PDF export and a rendered PNG, because "the call returned" is not
evidence that the sheet looks like a drawing - the first prototype placed the
whole title block off the page and still returned cleanly.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.drawing import DraftingTools
from catia_mcp.tools.export import ExportTools
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.sketcher import SketcherTools

RESULTS: list[tuple[str, bool, str]] = []


def say(message: str) -> None:
    print(message, flush=True)


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    say(f"   {'OK  ' if ok else 'FAIL'} {label}" + (f"  [{detail[:180]}]" if detail else ""))


def main() -> None:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketch = SketcherTools(conn)
    design = PartDesignTools(conn)
    draw = DraftingTools(conn)
    export = ExportTools(conn)

    for index in range(conn.documents.Count, 0, -1):
        try:
            conn.documents.Item(index).Close()
        except Exception:  # noqa: BLE001
            pass
    say(f"open at start: {conn.documents.Count}")

    say("\n=== part: 120 x 80 x 12 plate, phi20 hole, 4 phi8 holes ===")
    docs.execute("catia_new_part", {})
    sketch.execute("catia_create_sketch", {"plane": "xy"})
    sketch.execute("catia_sketch_centered_rectangle", {"width": 120, "height": 80})
    sketch.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"height": 12})
    sketch.execute("catia_create_sketch", {"plane": "xy"})
    sketch.execute("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": 10})
    sketch.execute("catia_sketch_constraint",
                   {"type": "radius", "geometry_index_1": 2, "value": 10})
    sketch.execute("catia_close_sketch", {})
    design.execute("catia_hole", {"diameter": 20, "through_all": True})
    say("   " + design.execute("catia_list_bodies", {}).splitlines()[0][:100])
    part_name = str(conn.active_document.Name)
    say(f"   part = {part_name}")

    say("\n=== E1 catia_drawing_set_sheet (A3 landscape) ===")
    drawing_text = draw.execute("catia_new_drawing", {})
    say("   " + drawing_text.splitlines()[0][:140])
    text = draw.execute("catia_drawing_set_sheet",
                        {"paper": "A3", "orientation": "landscape"})
    say("   " + text.splitlines()[0][:200])
    check("E1 set_sheet", "420" in text and "297" in text and "landscape" in text, text[:150])

    drawing = draw._find_active_drawing()
    sheet = drawing.Sheets.ActiveSheet
    check("A3 really is 420x297",
          (float(sheet.GetPaperWidth()), float(sheet.GetPaperHeight())) == (420.0, 297.0),
          f"{sheet.GetPaperWidth()}x{sheet.GetPaperHeight()} PaperSize={sheet.PaperSize}")

    say("\n=== E2 an ANSI paper size must be refused by name ===")
    try:
        draw.execute("catia_drawing_set_sheet", {"paper": "Letter"})
        check("ANSI refused", False, "Letter was accepted")
    except Exception as error:  # noqa: BLE001
        message = str(error).splitlines()[0]
        check("ANSI refused", "offers A0" in message or "Unknown paper" in message,
              message[:150])

    say("\n=== E3 views on the sheet ===")
    for payload in (
        # First-angle layout: the right view is drawn to the LEFT of the front
        # view and the top view BELOW it, so the front view sits centre-left and
        # the projections stay inside x 20..410. Placing the front view at x=120
        # put the right view at x=20 - exactly on the border line.
        {"part_name": part_name, "plane": "xy", "x": 250, "y": 190,
         "name": "Front View"},
    ):
        say("   " + draw.execute("catia_drawing_add_view", payload).splitlines()[0][:160])
    say("   " + draw.execute("catia_drawing_projection_view",
                             {"direction": "right", "parent_view": "Front View",
                              "name": "Right View"}).splitlines()[0][:160])
    say("   " + draw.execute("catia_drawing_projection_view",
                             {"direction": "top", "parent_view": "Front View",
                              "name": "Top View"}).splitlines()[0][:160])

    say("\n=== E4 catia_drawing_add_frame ===")
    payload = {
        "paper": "A3",
        "orientation": "landscape",
        "margin": 10,
        "margin_left": 20,
        "title_block": {
            "title": "COLA BOTTLE PETAL BASE",
            "drawing_number": "MCP-0001",
            "material": "PET",
            "scale": "1:1",
            "drawn_by": "MCP",
            "date": "2026-11-05",
            "checked_by": "",
            "sheet": "1/1",
            "footer": "UNITS mm   THIRD ANGLE   ISO 2768-mK   ISO 5457 A3",
        },
        "notes": ["1. BREAK SHARP EDGES 0.3 MAX",
                  "2. WALL THICKNESS 0.30 +/- 0.02"],
    }
    text = draw.execute("catia_drawing_add_frame", payload)
    say("   " + text[:700])
    check("E4 frame drawn", "4 border line(s)" in text and "title block" in text, text[:200])
    check("E4 nothing silently failed", "NOT EVERYTHING TOOK" not in text,
          text[text.find("NOT EVERYTHING"):][:200] if "NOT EVERYTHING" in text else "")

    say("\n=== E5 a block that cannot fit must be refused AND change nothing ===")
    before_elements = sheet.Views.Item(2).GeometricElements.Count
    before_tables = sheet.Views.Item(2).Tables.Count
    try:
        draw.execute("catia_drawing_add_frame",
                     {"title_block": {"width": 500}})
        check("oversized block refused", False, "it was accepted")
    except Exception as error:  # noqa: BLE001
        message = str(error).splitlines()[0]
        check("oversized block refused", "leaves" in message and "mm" in message, message[:150])
    after_elements = sheet.Views.Item(2).GeometricElements.Count
    after_tables = sheet.Views.Item(2).Tables.Count
    check("a refused frame leaves the sheet untouched",
          (before_elements, before_tables) == (after_elements, after_tables),
          f"elements {before_elements}->{after_elements}, "
          f"tables {before_tables}->{after_tables}")

    say("\n=== E6 read back what is on the sheet now ===")
    background = sheet.Views.Item(2)
    say(f"   Background: elements={background.GeometricElements.Count} "
        f"tables={background.Tables.Count} texts={background.Texts.Count}")
    table = background.Tables.Item(1)
    say(f"   table {table.Name!r} rows={table.NumberOfRows} cols={table.NumberOfColumns}")
    for row, column in ((1, 1), (1, 6), (2, 1), (2, 3), (5, 1)):
        try:
            say(f"     cell({row},{column}) = {table.GetCellString(row, column)!r}")
        except Exception as error:  # noqa: BLE001
            say(f"     cell({row},{column}) unreadable: {str(error).splitlines()[0][:60]}")
    try:
        table.GetMergeInfos(1, 1, 0, 0, 0, 0)
    except Exception:
        pass

    say("\n=== E7 export PDF for a look ===")
    drawing.Activate()
    say("   " + export.execute(
        "catia_export",
        {"file_path": r"C:\Temp\v0365_frame_final.pdf", "format": "pdf"})[:200])
    say("   " + export.execute(
        "catia_screenshot",
        {"file_path": r"C:\Temp\v0365_frame_final.jpg"}).splitlines()[0][:150])

    say("\n=== summary ===")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    say(f"   {passed}/{len(RESULTS)} passed")
    for label, ok, detail in RESULTS:
        if not ok:
            say(f"   FAILED: {label} - {detail[:180]}")

    for index in range(conn.documents.Count, 0, -1):
        try:
            conn.documents.Item(index).Close()
        except Exception as error:  # noqa: BLE001
            say(f"   close failed: {str(error).splitlines()[0][:80]}")
    say(f"open at end: {conn.documents.Count}")


if __name__ == "__main__":
    main()
