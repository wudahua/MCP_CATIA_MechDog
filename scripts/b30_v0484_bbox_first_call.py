"""Is the 74 s the FIRST measurable of a document, or every one of them? (v0484)

v0483: a 24-cell part sweeps cold in 767 ms, a 582-cell part in 74 673 ms, and
a second 582-cell part in a new document pays 71 087 ms again - so the cost is
per part document, not per session.  582 cells at 4 ms warm says the warm price
per cell is real; what is not known is whether the cold price is one big charge
on the first measurable or a 30x surcharge on all 582.

Read the first five measurables one at a time, then the whole sweep, in one
fresh document.  If call #1 is ~70 s the charge is one-off; if all five are
~130 ms the surcharge is per cell and the mesh route is the cheaper reader.
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
from catia_mcp.tools.measurement import (  # noqa: E402
    batch_vertex_sweep, read_measurable_points)
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402
from catia_mcp.topo_search import search_topology  # noqa: E402

ROOT = os.path.join(os.environ.get("TEMP", "."), "catia_probe_v0484")
os.makedirs(ROOT, exist_ok=True)


def say(text: str) -> None:
    print(text, flush=True)


def outline(n: int = 194) -> list[list[float]]:
    points = []
    for i in range(n):
        angle = 2 * math.pi * i / n
        radius = 40 + 8 * math.cos(5 * angle)
        points.append([round(radius * math.cos(angle), 4),
                       round(radius * math.sin(angle), 4)])
    points.append(points[0])
    return points


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    parts = PartDesignTools(conn)
    say(f"probe v0484 on {conn.app.Name}")

    try:
        while True:
            docs.execute("catia_close_document", {"save": False})
    except Exception:  # noqa: BLE001
        pass

    path = os.path.join(ROOT, "HeavyC.CATPart")
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": "HeavyC", "file_path": path})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "OUT"})
    sketcher.execute("catia_sketch_strokes", {"strokes": [outline()]})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "OUT", "height": 6})
    docs.execute("catia_save_document", {})

    doc = conn.active_document
    body = conn.get_active_part_body()
    spa = doc.GetWorkbench("SPAWorkbench")
    sel = doc.Selection
    sel.Clear()
    sel.Add(body)
    search_topology(sel, "Vertex")
    say(f"  cells: {sel.Count}")

    refs = [sel.Item(i).Reference for i in range(1, 6)]
    for index, ref in enumerate(refs, start=1):
        started = time.perf_counter()
        points = read_measurable_points(conn.app, spa, [ref])
        took = (time.perf_counter() - started) * 1000
        say(f"  measurable #{index}: {took:8.0f} ms  -> {points}")

    started = time.perf_counter()
    batch_vertex_sweep(conn.app, sel, spa)
    say(f"  full sweep after those: {(time.perf_counter() - started) * 1000:.0f} ms")
    sel.Clear()
    say("\ndone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
