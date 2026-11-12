"""Is there a font setting that actually CHANGES what the sheet shows?

The 0.3.66 probe wrote the same string into four cells and gave three of them
SetFontName('宋体'/'SimSun'/'Arial Unicode MS'). All four read back the name
they were given and the exported PDF rendered all four identically in SICH -
so the setter accepts the call and does nothing. This probe looks for the
other route: DrawingText.TextProperties, which is what the CATIA UI's text
editor writes through.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.export import ExportTools


def members(obj) -> list[str]:
    out = []
    try:
        type_info = obj._oleobj_.GetTypeInfo()
        attr = type_info.GetTypeAttr()
        for i in range(attr.cFuncs):
            try:
                out.append(type_info.GetNames(type_info.GetFuncDesc(i).memid)[0])
            except Exception:  # noqa: BLE001
                pass
        for i in range(attr.cVars):
            try:
                out.append(type_info.GetNames(type_info.GetVarDesc(i).memid)[0])
            except Exception:  # noqa: BLE001
                pass
    except Exception as error:  # noqa: BLE001
        out.append(f"<typeinfo failed: {str(error).splitlines()[0][:60]}>")
    return out


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

    drawing = conn.documents.Add("Drawing")
    sheet = drawing.Sheets.ActiveSheet
    sheet.PaperSize = 5
    sheet.Orientation = 1
    background = sheet.Views.Item(2)
    background.Activate()

    texts = []
    for offset, label in enumerate(("SICH 默认 1:1 R10", "宋体 SetFontName 1:1 R10",
                                    "FangSong 1:1 R10", "SimHei 1:1 R10")):
        text = background.Texts.Add(label, 20, 250 - offset * 20)
        texts.append(text)
    for text, font in zip(texts[1:], ("宋体", "FangSong", "SimHei")):
        try:
            text.SetFontName(1, len(text.Text), font)
            print(f"SetFontName({font!r}) ok, reads {text.GetFontName(1, len(text.Text))!r}",
                  flush=True)
        except Exception as error:  # noqa: BLE001
            print(f"SetFontName({font!r}) FAILED {str(error).splitlines()[0][:70]}", flush=True)

    props = None
    try:
        props = texts[0].TextProperties
        print(f"\nTextProperties type: {type(props)}", flush=True)
        print(f"TextProperties members: {members(props)}", flush=True)
    except Exception as error:  # noqa: BLE001
        print(f"TextProperties FAILED: {str(error).splitlines()[0][:80]}", flush=True)

    if props is not None:
        for name in ("FontName", "FontSize", "Bold", "Italic", "Underline",
                     "Slant", "CharSpace", "StrikeThrough", "Overline",
                     "Color", "Font", "Name"):
            try:
                print(f"   props.{name} = {getattr(props, name)!r}", flush=True)
            except Exception as error:  # noqa: BLE001
                print(f"   props.{name} unreadable: {str(error).splitlines()[0][:60]}",
                      flush=True)

    conn.refresh_display()
    export = ExportTools(conn)
    drawing.Activate()
    print("\n" + export.execute("catia_export",
                                {"file_path": r"C:\Temp\v0366_font_props.pdf",
                                 "format": "pdf"})[:160], flush=True)

    for index in range(conn.documents.Count, 0, -1):
        try:
            conn.documents.Item(index).Close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
