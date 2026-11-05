"""Chinese sheet furniture, end to end, through the server's tool layer.

Three things have to be true, and only CATIA can say so:

  1. The default labels land in the cells as 材料/比例/... and survive a PDF
     export (a table cell is drawn by the sheet's default text properties,
     not by CATIA's own view label).
  2. A Chinese VIEW NAME still works as a handle - projection_view looks its
     parent up by name, and the Chinese name is also what gets printed under
     the view.
  3. Refusing an unknown language leaves the sheet untouched.
"""

from __future__ import annotations

import sys

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

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
    say(f"   {'OK  ' if ok else 'FAIL'} {label}" + (f"  [{detail[:200]}]" if detail else ""))


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

    say("\n=== a small plate to project ===")
    docs.execute("catia_new_part", {})
    sketch.execute("catia_create_sketch", {"plane": "xy"})
    sketch.execute("catia_sketch_centered_rectangle", {"width": 120, "height": 80})
    sketch.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"height": 12})
    part_name = str(conn.active_document.Name)
    say(f"   part = {part_name}")

    say("\n=== E1 A3 landscape, views with DEFAULT names ===")
    draw.execute("catia_new_drawing", {})
    say("   " + draw.execute("catia_drawing_set_sheet",
                             {"paper": "A3", "orientation": "landscape"})[:160])
    say("   " + draw.execute("catia_drawing_add_view",
                             {"part_name": part_name, "plane": "xy",
                              "x": 250, "y": 190})[:160])
    drawing = draw._find_active_drawing()
    sheet = drawing.Sheets.ActiveSheet
    names = [str(sheet.Views.Item(i).Name) for i in range(1, sheet.Views.Count + 1)]
    say(f"   view names: {names}")
    check("E1 front view default is 主视图", any("主视图" in n for n in names), str(names))

    say("\n=== E2 projection views: default name AND Chinese parent lookup ===")
    say("   " + draw.execute("catia_drawing_projection_view",
                             {"direction": "right", "parent_view": "主视图"})[:160])
    say("   " + draw.execute("catia_drawing_projection_view",
                             {"direction": "top", "parent_view": "主视图"})[:160])
    names = [str(sheet.Views.Item(i).Name) for i in range(1, sheet.Views.Count + 1)]
    say(f"   view names: {names}")
    check("E2 right view named 右视图", any("右视图" in n for n in names), str(names))
    check("E2 top view named 俯视图", any("俯视图" in n for n in names), str(names))

    say("\n=== E3 an unknown language must be refused AND draw nothing ===")
    background = sheet.Views.Item(2)
    before = (background.GeometricElements.Count, background.Tables.Count)
    try:
        draw.execute("catia_drawing_add_frame", {"paper": "A3", "language": "fr"})
        check("E3 refused", False, "fr was accepted")
    except Exception as error:  # noqa: BLE001
        check("E3 refused", "Unknown language" in str(error), str(error).splitlines()[0][:150])
    after = (background.GeometricElements.Count, background.Tables.Count)
    check("E3 nothing drawn", before == after, f"{before} -> {after}")

    say("\n=== E4 the frame, default language (Chinese) ===")
    text = draw.execute("catia_drawing_add_frame", {
        "paper": "A3",
        "orientation": "landscape",
        "title_block": {
            "title": "可乐瓶花瓣底座",
            "drawing_number": "MCP-0002",
            "material": "聚对苯二甲酸乙二醇酯",
            "scale": "1:1",
            "drawn_by": "毛工",
            "date": "2026-11-05",
            "checked_by": "李工",
            "sheet": "1/1",
            "footer": "单位 mm   第一角投影   ISO 2768-mK   ISO 5457 A3",
        },
        "notes": ["1. 未注圆角 R2",
                  "2. 未注公差按 GB/T 1804-m"],
    })
    say("   " + text[:600])
    check("E4 frame drawn", "4 border line(s)" in text, text[:200])
    check("E4 no silent failure", "NOT EVERYTHING TOOK" not in text,
          text[text.find("NOT EVERYTHING"):][:200] if "NOT EVERYTHING" in text else "")
    check("E4 reply states the language", "中文" in text, text[:200])

    table = background.Tables.Item(1)
    cells = {}
    for row, column in ((1, 1), (2, 1), (2, 3), (2, 5), (3, 1), (3, 3),
                        (3, 5), (4, 1), (4, 5), (5, 1)):
        cells[(row, column)] = str(table.GetCellString(row, column))
    for key, value in cells.items():
        say(f"   cell{key} = {value!r}")
    check("E4 labels are Chinese",
          cells[(2, 1)] == "材料" and cells[(2, 5)] == "比例"
          and cells[(3, 1)] == "制图" and cells[(4, 5)] == "张次",
          str(cells))
    check("E4 values kept their characters",
          cells[(1, 1)] == "可乐瓶花瓣底座" and cells[(3, 3)] == "毛工", str(cells))

    say("\n=== E5 export and look at it ===")
    drawing.Activate()
    say("   " + export.execute("catia_export",
                               {"file_path": r"C:\Temp\v0366_frame_zh.pdf",
                                "format": "pdf"})[:200])
    say("   " + export.execute("catia_screenshot",
                               {"file_path": r"C:\Temp\v0366_frame_zh.jpg"}
                               ).splitlines()[0][:150])

    say("\n=== summary ===")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    say(f"   {passed}/{len(RESULTS)} passed")
    for label, ok, detail in RESULTS:
        if not ok:
            say(f"   FAILED: {label} - {detail}")

    for index in range(conn.documents.Count, 0, -1):
        try:
            conn.documents.Item(index).Close()
        except Exception as error:  # noqa: BLE001
            say(f"   close failed: {str(error).splitlines()[0][:80]}")
    say(f"open at end: {conn.documents.Count}")


if __name__ == "__main__":
    main()
