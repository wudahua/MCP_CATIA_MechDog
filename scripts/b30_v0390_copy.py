"""0.3.90 probe: does the drawing record follow a DELETE?

The 0.3.89 report saw `sketch_copy` produce "2 spline(s)" and an outline exactly
twice the source's. If the session's drawing record keeps an element that
`catia_sketch_delete_element` removed, then a copy replays geometry the sketch no
longer has - and the profile echo counts it too.

Two lines (30 mm outline), delete one, copy: if the copy still holds 30 mm, the
record did not follow the delete.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0390"


def say(line: str = "") -> None:
    print(line, flush=True)


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    sketcher = SketcherTools(conn)

    own = os.path.normcase(os.path.abspath(ROOT))
    for index in range(conn.documents.Count, 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = os.path.normcase(os.path.abspath(str(doc.FullName)))
        except Exception:  # noqa: BLE001
            continue
        if full.startswith(own):
            say(f"closing leftover {doc.Name}")
            doc.Close()

    path = os.path.join(ROOT, "copy90.CATPart")
    if os.path.isfile(path):
        os.remove(path)
    document.execute("catia_new_part", {"name": "COPY90", "file_path": path})

    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_line",
                     {"x1": 0, "y1": 0, "x2": 20, "y2": 0})
    sketcher.execute("catia_sketch_line",
                     {"x1": 20, "y1": 0, "x2": 20, "y2": 10})
    closed = sketcher.execute("catia_close_sketch", {})
    say("--- source closed ---")
    say(str(closed)[:400])

    geometry = json.loads(sketcher.execute("catia_sketch_get_geometry",
                                           {"sketch_name": "草图.1"}))
    say("")
    say("--- elements before the delete ---")
    for element in geometry["elements"]:
        say(f"  {element['index']}: {element['name']} ({element.get('type')})"
            f" construction={element['construction']}")
    say(f"  count={geometry['count']}, "
        f"record={len(geometry.get('drawn_this_session') or [])} entries")

    last = geometry["elements"][-1]["index"]
    reply = sketcher.execute("catia_sketch_delete_element", {"index": last})
    say("")
    say(f"--- deleted index {last} ---")
    say(str(reply)[:400])

    geometry = json.loads(sketcher.execute("catia_sketch_get_geometry",
                                           {"sketch_name": "草图.1"}))
    say("")
    say(f"--- elements after the delete: {geometry['count']} ---")
    for element in geometry["elements"]:
        say(f"  {element['index']}: {element['name']}")
    recorded = geometry.get("drawn_this_session") or []
    say(f"  record={len(recorded)} entries: "
        + str([entry.get("kind") for entry in recorded]))
    lengths = [round(abs(float(e["y2"]) - float(e["y1"]))
                     + abs(float(e["x2"]) - float(e["x1"])), 4)
               for e in recorded if e.get("kind") == "line"]
    say(f"  recorded line lengths: {lengths} (sum {sum(lengths):.4f} mm)")

    # Copy what the sketch STILL has and compare.
    say("")
    say("--- sketch_copy ---")
    try:
        copy = sketcher.execute("catia_sketch_copy",
                               {"source": "草图.1", "plane": "yz"})
        say(str(copy)[:500])
    except Exception as error:  # noqa: BLE001
        say(f"REFUSED {getattr(error, 'code', '')}: {str(error)[:400]}")

    document.execute("catia_close_document", {"save": False})
    say("")
    say("still open: " + ", ".join(str(conn.documents.Item(i).Name)
                                  for i in range(1, conn.documents.Count + 1)))


if __name__ == "__main__":
    main()
