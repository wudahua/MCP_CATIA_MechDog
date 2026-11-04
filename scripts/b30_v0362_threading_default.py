"""Does building a threaded hole change what the NEXT hole defaults to?

End-to-end (b30_v0362_threading_e2e.py) showed something the type library cannot:
a threaded hole reads ThreadingMode 0 as it should, but the PLAIN hole built
after it ALSO read 0, where a fresh hole reads 1. Either the finder picked the
wrong feature, or CATIA carries the last threading mode over to the next hole -
and if it does, "the default is 1" is a statement about a fresh part, not about
every hole, which changes what the tool has to do.

This builds three holes in a row (true, false, false), lists the tree with each
feature's own ThreadingMode, and prints the Hole interface's thread members so
the names stop being guesses.

Throwaway part, closed without saving.
"""

from __future__ import annotations

import sys

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.sketcher import SketcherTools

say = lambda text: print(text, flush=True)  # noqa: E731


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    design = PartDesignTools(conn)
    sketch = SketcherTools(conn)

    docs.execute("catia_new_part", {})
    sketch.execute("catia_create_sketch", {"plane": "xy"})
    sketch.execute("catia_sketch_centered_rectangle", {"width": 60, "height": 40})
    sketch.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"height": 20})

    body = conn.get_active_part_body()

    def add_hole(x: float, threaded: bool, label: str) -> None:
        sketch.execute("catia_create_sketch", {"plane": "xy", "offset": 20})
        sketch.execute("catia_sketch_point", {"x": x, "y": 0})
        sketch.execute("catia_close_sketch", {})
        reply = design.execute("catia_hole",
                               {"diameter": 8, "depth": 12, "threaded": threaded})
        first = reply.splitlines()[0][:150]
        say(f"  {label}: {'THREADED' in reply} | {first}")

    say("\nA. three holes in a row")
    add_hole(0, True, "1 threaded=True ")
    add_hole(15, False, "2 threaded=False")
    add_hole(-15, False, "3 threaded=False")

    say("\nB. every feature in the tree, with its own ThreadingMode")
    for index in range(1, body.Shapes.Count + 1):
        shape = body.Shapes.Item(index)
        try:
            mode = shape.ThreadingMode
        except Exception:  # noqa: BLE001 - not a hole
            mode = "-"
        say(f"  {index}: {shape.Name!r} ThreadingMode={mode}")

    say("\nC. thread-related members on the Hole interface")
    hole = None
    for index in range(body.Shapes.Count, 0, -1):
        shape = body.Shapes.Item(index)
        try:
            _ = shape.ThreadingMode
            hole = shape
            break
        except Exception:  # noqa: BLE001
            continue
    if hole is not None:
        try:
            type_info = hole._oleobj_.GetTypeInfo()
            count = type_info.GetTypeAttr().cFuncs
            names = []
            for slot in range(count):
                function = type_info.GetFuncDesc(slot)
                member = type_info.GetNames(function.memid)
                if member and "thread" in member[0].lower():
                    names.append(member[0])
            say("  " + ", ".join(sorted(set(names))))
        except Exception as error:  # noqa: BLE001
            say(f"  unreadable: {str(error).splitlines()[0][:100]}")

    print(docs.execute("catia_close_document", {"save": False}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
