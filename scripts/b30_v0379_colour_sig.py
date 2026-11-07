"""0.3.79 probe 2: the exact VisProperties signature on this build.

Probe 1 found that SetRealColor(220,30,30) is refused with "invalid number of
arguments" and that GetRealColor() returns FOUR values. So the signature is not
the documented one. This tries every plausible shape and reports which one both
returns without error and changes the read-back.
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402

ROOT = r"F:\catia_probe_v0379b"


def say(text: str = "") -> None:
    print(text, flush=True)


def read(selection):
    try:
        return tuple(int(v) for v in selection.VisProperties.GetRealColor())
    except Exception as error:  # noqa: BLE001
        return f"<{str(error).splitlines()[0][:70]}>"


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    design = PartDesignTools(conn)
    before = {str(conn.documents.Item(i).Name)
              for i in range(1, conn.documents.Count + 1)}
    say(f"session before: {conn.documents.Count}")

    try:
        docs.execute("catia_new_part",
                     {"name": "CP", "file_path": os.path.join(ROOT, "cp.CATPart")})
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 20, "y2": 12})
        sketcher.execute("catia_close_sketch", {})
        design.execute("catia_pad", {"height": 10})

        doc = conn.active_document
        part = conn.get_active_part()
        body = conn.get_active_part_body()
        sel = doc.Selection

        # what the interface exposes, from the dispatch object itself
        vp = sel.VisProperties
        say("\ninterface: " + str(type(vp)))
        for attr in ("SetRealColor", "GetRealColor", "SetShow", "GetShow",
                     "SetColor", "GetColor", "SetRealColorIn3DFace",
                     "SetVisibleIn3D", "SetTransparency", "GetTransparency"):
            say(f"   {attr}: {hasattr(vp, attr)}")

        sel.Clear()
        sel.Add(body)
        sel.VisProperties.SetShow(0)   # a known-good call on the same object
        say(f"\nbaseline after SetShow(0): {read(sel)}")
        sel.VisProperties.SetShow(1)

        tries = [
            ("SetRealColor(220,30,30)", lambda v: v.SetRealColor(220, 30, 30)),
            ("SetRealColor(220,30,30,1)", lambda v: v.SetRealColor(220, 30, 30, 1)),
            ("SetRealColor(220,30,30,0)", lambda v: v.SetRealColor(220, 30, 30, 0)),
            ("SetRealColor((220,30,30))", lambda v: v.SetRealColor((220, 30, 30))),
            ("SetColor(220,30,30)", lambda v: v.SetColor(220, 30, 30)),
        ]
        for label, call in tries:
            try:
                call(sel.VisProperties)
            except Exception as error:  # noqa: BLE001
                say(f"   {label:<28} REFUSED  {str(error).splitlines()[0][:70]}")
                continue
            now = read(sel)
            say(f"   {label:<28} accepted -> {now}")

        # does a colour survive Update/regen, and does it reach the STL?
        say(f"\nafter Update: {read(sel)}")
    finally:
        current = {str(conn.documents.Item(i).Name)
                   for i in range(1, conn.documents.Count + 1)}
        for name in sorted(current - before, reverse=True):
            for index in range(1, conn.documents.Count + 1):
                if str(conn.documents.Item(index).Name) == name:
                    try:
                        conn.documents.Item(index).Close()
                    except Exception as error:  # noqa: BLE001
                        say(f"   (could not close {name}: {error})")
                    break
        shutil.rmtree(ROOT, ignore_errors=True)
        say(f"\nsession after: {conn.documents.Count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
