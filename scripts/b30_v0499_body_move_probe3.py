"""B30 probe, part 3 of 0.3.162: is there ANY solid-side transform member?

Part 1 found no move/transform member on Part / Body / Shape. Part 2 found that
GSD's AddNewTranslate on a body reference yields a SURFACE copy (area matches the
solid's 13450 mm2, Volume raises). So this asks the last place a solid translate
could live: ShapeFactory and the two factories' full member lists, printed rather
than filtered, so "not found" cannot be a bad keyword.
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
    """Every declared member, by GetNames(memid) - GetDocumentation throws here.

    B30 2026-11-23: GetFuncDesc(index).GetDocumentation(-1) raised for every
    entry on a Body and a ShapeFactory, so an earlier version of this probe
    printed "0 member(s)" for an interface that declares 110. Ask GetNames.
    """
    try:
        info = obj._oleobj_.GetTypeInfo()
        count = info.GetTypeAttr().cFuncs
    except Exception as error:  # noqa: BLE001
        return [f"<typeinfo failed: {type(error).__name__}: {error}>"]
    names = []
    for index in range(count):
        try:
            desc = info.GetFuncDesc(index)
        except Exception:  # noqa: BLE001
            continue
        try:
            found = info.GetNames(desc.memid)
        except Exception:  # noqa: BLE001
            continue
        if found:
            names.append(found[0])
    return names


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sk = SketcherTools(conn)
    design = PartDesignTools(conn)

    root = os.path.join(tempfile.gettempdir(), "p0499move")
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "MOVE0499C.CATPart")
    try:
        docs.execute("catia_close_document", {"document": path, "save": False})
    except Exception:  # noqa: BLE001
        pass
    if os.path.isfile(path):
        os.remove(path)

    docs.execute("catia_new_part", {"name": "MOVE0499C", "file_path": path})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "S"})
    sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 25, "y2": 45})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"sketch_name": "S", "height": 80})

    part = conn.get_active_part()
    body = part.MainBody

    for label, obj in (("Body", body),
                       ("Body.Shapes.Item(1) (Pad)",
                        body.Shapes.Item(1)),
                       ("Part.ShapeFactory", part.ShapeFactory),
                       ("Part.HybridShapeFactory", part.HybridShapeFactory)):
        names = all_members(obj)
        say(f"\n=== {label}: {len(names)} member(s)")
        interesting = [n for n in names
                       if any(k in n.lower() for k in
                              ("trans", "move", "posit", "matrix", "mirror",
                               "rotate", "scale", "axis", "copy", "paste"))]
        say(f"  transform-ish: {interesting or 'NONE'}")
        if label.startswith("Body"):
            say(f"  all: {names}")

    say("\n=== Does the Pad's own sketch support move with it? "
        "(Sketch.SetAbsoluteAxisData)")
    sketch = body.Sketches.Item(1)
    say(f"  sketch members with axis: "
        f"{[n for n in all_members(sketch) if 'Axis' in n or 'axis' in n]}")

    docs.execute("catia_close_document", {"document": path, "save": False})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
