"""Can CATIA V5 drafting SET and DISPLAY Chinese text?

Question from the user: the drawing frame comes out with English labels.
Before rewriting the labels I have to know whether the default drafting font
has Chinese glyphs at all - "缩放: 1" under the views renders fine, but that
label is drawn by CATIA itself with its own font, not by my table cells.

This probe writes the same Chinese string into four cells and gives three of
them a different font, then exports a PDF. Only the PDF shows which ones
actually rasterise.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.export import ExportTools

WORDS = "材料 图号 比例 制图 审核"


def main() -> None:
    conn = CATIAConnection()
    conn.connect()
    app = conn.app
    app.DisplayFileAlerts = False

    for index in range(conn.documents.Count, 0, -1):
        try:
            conn.documents.Item(index).Close()
        except Exception:  # noqa: BLE001
            pass
    print(f"open at start: {conn.documents.Count}", flush=True)

    drawing = conn.documents.Add("Drawing")
    sheet = drawing.Sheets.ActiveSheet
    sheet.PaperSize = 5          # A3
    sheet.Orientation = 1        # landscape
    print(f"sheet {sheet.GetPaperWidth()}x{sheet.GetPaperHeight()}", flush=True)

    background = sheet.Views.Item(2)
    try:
        background.Activate()
    except Exception as error:  # noqa: BLE001
        print(f"activate failed: {str(error).splitlines()[0][:60]}", flush=True)

    table = background.Tables.Add(20, 200, 4, 4, 12, 40)
    cells = [(1, 1), (2, 1), (3, 1), (4, 1)]
    for (row, column) in cells:
        table.SetCellString(row, column, WORDS)

    # What does the cell object offer at all?  dir() blows up on this
    # early-bound-ish dispatch ("未指定的错误"), so read the type library.
    cell = table.GetCellObject(1, 1)
    print(f"cell object type: {type(cell)}", flush=True)
    try:
        type_info = cell._oleobj_.GetTypeInfo()
        type_attr = type_info.GetTypeAttr()
        names = []
        for i in range(type_attr.cFuncs):
            func = type_info.GetFuncDesc(i)
            try:
                names.append(type_info.GetNames(func.memid)[0])
            except Exception:  # noqa: BLE001
                pass
        print(f"cell members ({type_attr.cFuncs}): {names}", flush=True)
    except Exception as error:  # noqa: BLE001
        print(f"cell typeinfo failed: {str(error).splitlines()[0][:90]}", flush=True)

    # Three attempts at a font that has Chinese glyphs.
    for row, font in ((2, "宋体"), (3, "SimSun"), (4, "Arial Unicode MS")):
        try:
            obj = table.GetCellObject(row, 1)
            obj.SetFontName(1, len(WORDS), font)
            read_back = obj.GetFontName(1, len(WORDS))
            print(f"row {row} font {font!r} -> {read_back!r}", flush=True)
        except Exception as error:  # noqa: BLE001
            print(f"row {row} font {font!r} FAILED: {str(error).splitlines()[0][:90]}",
                  flush=True)

    # A plain sheet text object, same question.
    try:
        text = background.Texts.Add(WORDS, 20, 120)
        print(f"text object type: {type(text)}", flush=True)
        print(f"text font name: {text.GetFontName(1, len(WORDS))!r}", flush=True)
    except Exception as error:  # noqa: BLE001
        print(f"text add failed: {str(error).splitlines()[0][:90]}", flush=True)

    # Read the strings back: does CATIA keep the characters or mangle them?
    for (row, column) in cells:
        try:
            print(f"cell({row},{column}) = {table.GetCellString(row, column)!r}",
                  flush=True)
        except Exception as error:  # noqa: BLE001
            print(f"cell({row},{column}) unreadable: {str(error).splitlines()[0][:60]}",
                  flush=True)

    # What fonts does this installation actually name?
    try:
        fonts = app.SystemService.GetEnvironmentValue("CATFontPath", "")
        print(f"CATFontPath: {fonts}", flush=True)
    except Exception as error:  # noqa: BLE001
        print(f"CATFontPath unreadable: {str(error).splitlines()[0][:60]}", flush=True)

    conn.refresh_display()
    export = ExportTools(conn)
    drawing.Activate()
    print(export.execute("catia_export",
                         {"file_path": r"C:\Temp\v0366_zh_font.pdf",
                          "format": "pdf"})[:200], flush=True)

    for index in range(conn.documents.Count, 0, -1):
        try:
            conn.documents.Item(index).Close()
        except Exception as error:  # noqa: BLE001
            print(f"close failed: {str(error).splitlines()[0][:70]}", flush=True)
    print(f"open at end: {conn.documents.Count}", flush=True)


if __name__ == "__main__":
    main()
