"""Constraint indices after 0.3.62: does index 1 explain itself now?

Before: `geometry_index_1: 1` - the natural reading of "my first line" - hit the
sketch's ABSOLUTE AXIS at item 1 and CATIA answered "方法 AddMonoEltCst 失败",
which names neither the axis nor the index. Item 2 was the first line all along.

Checks the three things the fix claims: the geometry list marks the axis, index 1
is refused with a message that says where the first line is, and index 2 works.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.sketcher import SketcherTools

say = lambda text: print(text, flush=True)  # noqa: E731


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketch = SketcherTools(conn)

    docs.execute("catia_new_part", {})
    sketch.execute("catia_create_sketch", {"plane": "xy"})
    sketch.execute("catia_sketch_line", {"x1": 0, "y1": 0, "x2": 10, "y2": 0})
    sketch.execute("catia_sketch_line", {"x1": 0, "y1": 8, "x2": 10, "y2": 8})

    say("A. catia_sketch_get_geometry")
    payload = json.loads(sketch.execute("catia_sketch_get_geometry", {}))
    for element in payload["elements"]:
        say(f"  index {element['index']}: {element['name']!r} "
            f"absolute_axis={element.get('absolute_axis')}")

    say("\nB. fix on index 1 (the axis)")
    try:
        sketch.execute("catia_sketch_constraint",
                       {"type": "fix", "geometry_index_1": 1})
        say("  ACCEPTED - the refusal did not fire")
    except Exception as error:  # noqa: BLE001
        say(f"  refused: {str(error).splitlines()[0][:220]}")

    say("\nC. fix on index 2 (the first line drawn)")
    try:
        say("  " + sketch.execute("catia_sketch_constraint",
                                  {"type": "fix", "geometry_index_1": 2}).splitlines()[0])
    except Exception as error:  # noqa: BLE001
        say(f"  failed: {str(error).splitlines()[0][:160]}")

    say("\nD. distance 1-2 (axis + line) must be refused too")
    try:
        sketch.execute("catia_sketch_constraint",
                       {"type": "distance", "geometry_index_1": 2,
                        "geometry_index_2": 1, "value": 25})
        say("  ACCEPTED - geometry_index_2 was not checked")
    except Exception as error:  # noqa: BLE001
        say(f"  refused: {str(error).splitlines()[0][:180]}")

    say("\nE. distance 2-3 (both drawn lines)")
    try:
        say("  " + sketch.execute("catia_sketch_constraint",
                                  {"type": "distance", "geometry_index_1": 2,
                                   "geometry_index_2": 3,
                                   "value": 25}).splitlines()[0])
    except Exception as error:  # noqa: BLE001
        say(f"  failed: {str(error).splitlines()[0][:160]}")

    print(docs.execute("catia_close_document", {"save": False}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
