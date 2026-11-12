"""Enums and members that a frame/title block needs.

  * CatPaperSize: which number is A3? (the sheet reports 13 out of the box)
  * CatSheetOrientation: which number is landscape?
  * DrawingTable: is there a merge, an alignment, a border?
  * DrawingText: is there an anchor, a justification, a font size?
"""

from __future__ import annotations

import os
import sys

import pythoncom

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection

WANTED = ("paper", "orient", "format", "justif", "anchor", "align")


def say(message: str) -> None:
    print(message, flush=True)


def enums(obj: object, title: str) -> None:
    say(f"\n=== enums in the library of {title} ===")
    try:
        info = obj._oleobj_.GetTypeInfo()
        library = info.GetContainingTypeLib()[0]
        count = library.GetTypeInfoCount()
    except Exception as error:  # noqa: BLE001
        say(f"   unreadable: {str(error).splitlines()[0][:120]}")
        return
    say(f"   {count} typeinfo(s) in this library")
    for position in range(count):
        try:
            info = library.GetTypeInfo(position)
            attributes = info.GetTypeAttr()
        except Exception:  # noqa: BLE001
            continue
        if attributes.typekind != pythoncom.TKIND_ENUM:
            continue
        try:
            name = info.GetDocumentation(-1)[0]
        except Exception:  # noqa: BLE001
            continue
        if not name:
            continue
        if WANTED and not any(word in name.lower() for word in WANTED):
            continue
        say(f"   ENUM {name}")
        for slot in range(attributes.cVars):
            try:
                variable = info.GetVarDesc(slot)
                member = info.GetNames(variable.memid)[0]
                say(f"     {member} = {variable.value}")
            except Exception:  # noqa: BLE001
                continue


def members(obj: object, title: str, keywords: tuple[str, ...]) -> None:
    say(f"\n=== {title} -- members matching {keywords} ===")
    try:
        info = obj._oleobj_.GetTypeInfo()
        attributes = info.GetTypeAttr()
    except Exception as error:  # noqa: BLE001
        say(f"   unreadable: {str(error).splitlines()[0][:120]}")
        return
    kinds = {1: "METHOD", 2: "GET", 4: "PUT"}
    seen = set()
    for slot in range(attributes.cFuncs):
        try:
            function = info.GetFuncDesc(slot)
            names = info.GetNames(function.memid)
        except Exception:  # noqa: BLE001
            continue
        name = names[0] if names else "?"
        if not any(word.lower() in name.lower() for word in keywords):
            continue
        key = (name, function.invkind)
        if key in seen:
            continue
        seen.add(key)
        say(f"   {kinds.get(function.invkind, function.invkind):6s} {name:26s} "
            f"args={function.args} ({', '.join(names[1:])})")
    say(f"   -- {len(seen)} shown")


def main() -> None:
    conn = CATIAConnection()
    conn.connect()
    for index in range(conn.documents.Count, 0, -1):
        try:
            conn.documents.Item(index).Close()
        except Exception:  # noqa: BLE001
            pass

    drawing = conn.documents.Add("Drawing")
    sheet = drawing.Sheets.ActiveSheet
    background = sheet.Views.Item(2)
    table = background.Tables.Add(20.0, 20.0, 3, 3, 8.0, 30.0)
    text = background.Texts.Add("probe", 5.0, 5.0)

    enums(sheet, "DrawingSheet")
    members(sheet, "DrawingSheet", ("paper", "orient", "scale", "frame",
                                    "format", "border", "title"))
    members(table, "DrawingTable", ("cell", "merge", "size", "border", "align",
                                    "justif", "text", "row", "column"))
    members(text, "DrawingText", ("anchor", "justif", "size", "font", "align",
                                  "position", "angle"))

    say("\n=== paper sizes by trial (each value, then read back) ===")
    for value in range(0, 16):
        try:
            sheet.PaperSize = value
            say(f"   PaperSize {value:2d} -> {sheet.PaperSize}")
        except Exception as error:  # noqa: BLE001
            say(f"   PaperSize {value:2d} -> FAILED {str(error).splitlines()[0][:60]}")

    say("\n=== orientation by trial ===")
    for value in (0, 1, 2):
        try:
            sheet.Orientation = value
            say(f"   Orientation {value} -> {sheet.Orientation}")
        except Exception as error:  # noqa: BLE001
            say(f"   Orientation {value} -> FAILED {str(error).splitlines()[0][:60]}")

    say("\n=== close ===")
    for index in range(conn.documents.Count, 0, -1):
        try:
            conn.documents.Item(index).Close()
        except Exception:  # noqa: BLE001
            pass
    say(f"open at end: {conn.documents.Count}")


if __name__ == "__main__":
    main()
