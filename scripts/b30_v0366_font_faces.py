"""TextProperties.FontName is the writable font entry (SetFontName is a no-op).

Four lines, four fonts, one PDF. The point is to see whether the glyphs change
- in particular whether the digit 1 stops looking like 'н'.
"""

from __future__ import annotations

import sys

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.export import ExportTools

CASES = ("SICH", "宋体", "FangSong", "SimHei", "Arial")


def main() -> None:
    conn = CATIAConnection()
    conn.connect()
    conn.app.DisplayFileAlerts = False
    for index in range(conn.documents.Count, 0, -1):
        try:
            conn.documents.Item(index).Close()
        except Exception:  # noqa: BLE001
            pass

    drawing = conn.documents.Add("Drawing")
    sheet = drawing.Sheets.ActiveSheet
    sheet.PaperSize = 5
    sheet.Orientation = 1
    background = sheet.Views.Item(2)
    background.Activate()

    for offset, font in enumerate(CASES):
        label = f"{font}: 材料 1:1 R10 0.05mm"
        text = background.Texts.Add(label, 20, 260 - offset * 18)
        text.SetFontSize(1, len(label), 4.5)
        try:
            props = text.TextProperties
            props.FontName = font
            props.Update()
            print(f"{font!r}: set ok, reads {props.FontName!r}", flush=True)
        except Exception as error:  # noqa: BLE001
            print(f"{font!r}: FAILED {str(error).splitlines()[0][:80]}", flush=True)

    conn.refresh_display()
    export = ExportTools(conn)
    drawing.Activate()
    print(export.execute("catia_export",
                         {"file_path": r"C:\Temp\v0366_font_faces.pdf",
                          "format": "pdf"})[:160], flush=True)

    for index in range(conn.documents.Count, 0, -1):
        try:
            conn.documents.Item(index).Close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
