"""Is the 74 s paid per call, per part, or once per session?  (v0483)

v0482 timed one part's cold vertex sweep at 74 345 ms and the same sweep warm at
2 258 ms (33x), with the search at 734 ms and everything else under 15 ms.  The
74 s is therefore inside the one CATScript that reads the coordinates, and the
question that decides what to do about it is what the cost belongs to:

  A  a small part, cold in a new document - if this is also ~74 s the cost is
     the first script call / the first measurable, not the 582 cells;
  B  a second heavy part, new document, same process - if this is ~2 s the cost
     was per session and is worth paying once, deliberately;
  C  a heavy part in a new document in a NEW process - if this is ~74 s again,
     the cost is per document (a cold measurable per part) and cannot be
     amortised by anything this server does.
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import batch_vertex_sweep  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402
from catia_mcp.topo_search import search_topology  # noqa: E402

ROOT = os.path.join(os.environ.get("TEMP", "."), "catia_probe_v0483")
os.makedirs(ROOT, exist_ok=True)


def say(text: str) -> None:
    print(text, flush=True)


def outline(n: int) -> list[list[float]]:
    points = []
    for i in range(n):
        angle = 2 * math.pi * i / n
        radius = 40 + 8 * math.cos(5 * angle)
        points.append([round(radius * math.cos(angle), 4),
                       round(radius * math.sin(angle), 4)])
    points.append(points[0])
    return points


def build(docs, sketcher, parts, name: str, n: int) -> None:
    path = os.path.join(ROOT, f"{name}.CATPart")
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "OUT"})
    sketcher.execute("catia_sketch_strokes", {"strokes": [outline(n)]})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "OUT", "height": 6})
    docs.execute("catia_save_document", {})


def sweep(conn, label: str) -> None:
    doc = conn.active_document
    part = conn.get_active_part()
    del part
    body = conn.get_active_part_body()
    spa = doc.GetWorkbench("SPAWorkbench")
    sel = doc.Selection
    sel.Clear()
    sel.Add(body)
    started = time.perf_counter()
    search_topology(sel, "Vertex")
    searched = (time.perf_counter() - started) * 1000
    cells = sel.Count
    started = time.perf_counter()
    result = batch_vertex_sweep(conn.app, sel, spa)
    swept = (time.perf_counter() - started) * 1000
    sel.Clear()
    points = len(result[0]) if result else 0
    say(f"  {label}: {cells} cells, search {searched:.0f} ms, "
        f"sweep {swept:.0f} ms, {points} points")


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    parts = PartDesignTools(conn)
    say(f"probe v0483 on {conn.app.Name}")

    try:
        while True:
            docs.execute("catia_close_document", {"save": False})
    except Exception:  # noqa: BLE001
        pass

    say("\nA: a small part (8 points), cold, new document, this process")
    build(docs, sketcher, parts, "Small", 8)
    sweep(conn, "small cold")

    say("\nB: a heavy part (194 points), cold, new document, same process")
    build(docs, sketcher, parts, "HeavyA", 194)
    sweep(conn, "heavy cold #1")
    sweep(conn, "heavy warm #2")

    say("\nC: second heavy part, new document, same process")
    build(docs, sketcher, parts, "HeavyB", 194)
    sweep(conn, "heavy cold #2 (new doc)")

    say("\ndone - compare A against B to separate 'first script call' from "
        "'per cell', and B against C to separate 'per document' from 'per session'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
