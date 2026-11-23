"""B30 probe for 0.3.163 - is there ANY way to reorder a feature?

The 2026-11-23 report (P1-4, third suggestion) asked for catia_reorder_feature.
In the UI a feature is dragged in the specification tree; whether the automation
API exposes anything like that is a separate question, and it is the one that
decides whether the tool can exist.

Asked here, in this order:
  A  TypeInfo of Body.Shapes (the collection), a Pad, Body, Part, Application -
     filtered for order/move/insert/sort/reorder/position
  B  the collection's declared members, printed in full
  C  Shape members in full, so "no reorder" cannot be a bad keyword
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402


def say(text: str = "") -> None:
    print(text, flush=True)


def all_members(obj) -> list[str]:
    """Every declared member, by GetNames - GetDocumentation raises on this build."""
    try:
        info = obj._oleobj_.GetTypeInfo()
        count = info.GetTypeAttr().cFuncs
    except Exception as error:  # noqa: BLE001
        return [f"<no typeinfo: {type(error).__name__}: {error}>"]
    names = []
    for index in range(count):
        try:
            desc = info.GetFuncDesc(index)
            found = info.GetNames(desc.memid)
        except Exception:  # noqa: BLE001
            continue
        if found:
            names.append(found[0])
    return names


KEYWORDS = ("order", "move", "insert", "sort", "reorder", "position", "drag",
            "parent", "owner", "swap")


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sk = SketcherTools(conn)
    design = PartDesignTools(conn)

    root = os.path.join(tempfile.gettempdir(), "p0499reorder")
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "REORDER0499.CATPart")
    try:
        docs.execute("catia_close_document", {"document": path, "save": False})
    except Exception:  # noqa: BLE001
        pass
    if os.path.isfile(path):
        os.remove(path)

    docs.execute("catia_new_part", {"name": "REORDER0499", "file_path": path})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "S"})
    sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 25, "y2": 45})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"sketch_name": "S", "height": 80})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "H"})
    sk.execute("catia_sketch_circle", {"cx": 12.5, "cy": 22.5, "radius": 5})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pocket", {"sketch_name": "H", "depth": 80})

    part = conn.get_active_part()
    body = part.MainBody
    pad = body.Shapes.Item(1)

    for label, obj in (("Body.Shapes (collection)", body.Shapes),
                       ("Shape #1 (Pad.1)", pad),
                       ("Body", body),
                       ("Part", part),
                       ("Application", conn.app)):
        names = all_members(obj)
        hits = [n for n in names if any(k in n.lower() for k in KEYWORDS)]
        say(f"\n=== {label}: {len(names)} member(s)")
        say(f"  order/move-ish: {hits or 'NONE'}")
        if "Shapes" in label or label.startswith("Shape #1"):
            say(f"  all: {names}")

    say("\n=== the tree order, as CATIA reports it")
    say(f"  body.Shapes: {[str(s.Name) for s in body.Shapes]}")

    say("\n=== can InWorkObject be set to the FIRST feature and a new one land "
        "in the middle? (the only ordering control the API does expose)")
    try:
        part.InWorkObject = pad
        say(f"  InWorkObject = '{pad.Name}': accepted")
        say("  (InWorkObject decides where a NEW feature is inserted; it does not "
            "move an existing one)")
    except Exception as error:  # noqa: BLE001
        say(f"  InWorkObject: {type(error).__name__}: {str(error).splitlines()[0][:120]}")

    docs.execute("catia_close_document", {"document": path, "save": False})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
