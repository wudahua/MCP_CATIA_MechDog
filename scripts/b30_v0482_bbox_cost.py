"""Where a cold bounding box spends its time (v0482)

The 2026-11-19 report measured 178 s for 7 parts / 17 instances and 64 s for
one leg-rod.  The tool already batches its vertex reads into a single script
call, so the remaining cost is in one of three places, and the tool's own
"topology cold start" wording does not say which:

  1  getting the SPA workbench (a cold workbench load),
  2  Selection.Search('Topology.Vertex') on the body,
  3  reading the coordinates.

Timed separately here on a part with a 194-point outline (the report's leg rod
had 194 points and 132), cold in a fresh document and again warm in the same
document.  GUARD: every phase is timed on its own, so the numbers add up to the
total instead of being argued about.
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools, batch_vertex_sweep  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402
from catia_mcp.topo_search import search_topology  # noqa: E402

ROOT = os.path.join(os.environ.get("TEMP", "."), "catia_probe_v0482")
os.makedirs(ROOT, exist_ok=True)


def say(text: str) -> None:
    print(text, flush=True)


def outline(n: int = 194) -> list[list[float]]:
    """A closed wavy polygon with n points - many vertices, one shell."""
    points = []
    for i in range(n):
        angle = 2 * math.pi * i / n
        radius = 40 + 8 * math.cos(5 * angle)
        points.append([round(radius * math.cos(angle), 4),
                       round(radius * math.sin(angle), 4)])
    points.append(points[0])
    return points


def timed(label: str, fn):
    started = time.perf_counter()
    value = fn()
    say(f"    {label}: {(time.perf_counter() - started) * 1000:.0f} ms")
    return value


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    parts = PartDesignTools(conn)
    measurement = MeasurementTools(conn)
    say(f"probe v0482 on {conn.app.Name}")

    try:
        while True:
            docs.execute("catia_close_document", {"save": False})
    except Exception:  # noqa: BLE001
        pass

    path = os.path.join(ROOT, "Heavy.CATPart")
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": "Heavy", "file_path": path})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "OUT"})
    sketcher.execute("catia_sketch_strokes", {"strokes": [outline()]})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "OUT", "height": 6})
    docs.execute("catia_save_document", {})

    part = conn.get_active_part()
    body = conn.get_active_part_body()
    doc = part.Parent

    for attempt in (1, 2):
        say(f"\n{'COLD' if attempt == 1 else 'WARM'} pass, same document:")
        spa = timed("GetWorkbench('SPAWorkbench')",
                    lambda: doc.GetWorkbench("SPAWorkbench"))
        sel = doc.Selection
        timed("sel.Clear + sel.Add(body)",
              lambda: (sel.Clear(), sel.Add(body)))
        timed("Selection.Search('Topology.Vertex')",
              lambda: search_topology(sel, "Vertex"))
        say(f"    cells found: {sel.Count}")
        timed("batch_vertex_sweep (one CATScript)",
              lambda: batch_vertex_sweep(conn.app, sel, spa))
        sel.Clear()

    say("\nthrough the tool, twice:")
    for attempt in (1, 2):
        started = time.perf_counter()
        reply = measurement.execute("catia_get_bounding_box",
                                    {"method": "vertices"})
        took = (time.perf_counter() - started) * 1000
        payload = reply if isinstance(reply, dict) else json.loads(reply)
        data = payload.get("data", {})
        say(f"  call {attempt}: {took:.0f} ms wall")
        say(f"    message: {' '.join(str(payload.get('message', '')).split())[:220]}")
        for entry in data.get("parts", []):
            say(f"    part: cells={entry.get('cells')} points={entry.get('points')} "
                f"ms={entry.get('ms')} reason={entry.get('cold_reason')}")
        say(f"    elapsed_note: {' '.join(str(data.get('elapsed_note', '')).split())[:240]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
