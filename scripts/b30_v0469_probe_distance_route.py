"""Can an edge be measured against a point WITHOUT a Measurable per edge?

`_edge_table` builds one `SPAWorkbench.GetMeasurable(edge.Reference)` per edge
only to read Length, and `_anchors_to_edges` builds a SECOND one per edge to call
GetMinimumDistance. On a 288-edge part that is 27 s of the 31 s `edges resolved`
(probe v0468, B30 2026-11-19), so the question is what each of those calls is
actually for:

  A  is `GetDistanceBetween(point, edge)` a thing, and does it answer the same
     distance as `GetMeasurable(edge).GetMinimumDistance(point)`?
  B  does `GetMeasurable` get cheaper when the same reference is asked twice
     (i.e. is SPA caching it, and is the cost the FIRST pass)?
  C  is `Length` reachable without a per-edge Measurable at all?
  D  what does one pass of NLS cost (it is what a length-free table needs)?

One fresh document, one probe per cell; a failure here is document-level.
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
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402
from catia_mcp.topo_search import topology_cells  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "catia_probe_v0469")
os.makedirs(ROOT, exist_ok=True)
TEETH = 24


def say(text: str) -> None:
    print(text, flush=True)


def timed(label: str, fn):
    started = time.perf_counter()
    out = fn()
    say(f"  {label}: {(time.perf_counter() - started) * 1000:.0f} ms")
    return out


def star(teeth: int) -> list[list[float]]:
    points: list[list[float]] = []
    for index in range(teeth * 2):
        angle = 2 * math.pi * index / (teeth * 2)
        radius = 50.0 if index % 2 == 0 else 40.0
        points.append([round(radius * math.cos(angle), 6),
                       round(radius * math.sin(angle), 6)])
    points.append(points[0])
    return points


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    say(f"probe v0469 on {conn.app.Name}")
    try:
        while True:
            docs.execute("catia_close_document", {"save": False})
    except Exception:  # noqa: BLE001
        pass

    path = os.path.join(ROOT, "DistanceRoute.CATPart")
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": "DistanceRoute", "file_path": path})
    part = conn.get_active_part()
    body = conn.get_active_part_body()
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "S"})
    sketcher.execute("catia_sketch_strokes", {"strokes": [star(TEETH)]})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "S", "height": 6.0})

    cells = topology_cells(conn, body, "Edge")
    say(f"  edges: {len(cells)}")
    spa = parts._topo_measure(part)

    # A probe point, built the way the tool builds one.
    probe = parts._edge_probe_points(part, body)
    point_ref = probe.add(50.0, 0.0, 3.0)
    probe.update()

    # ── A: GetDistanceBetween vs GetMeasurable.GetMinimumDistance ─────────
    edge = cells[len(cells) // 2]
    try:
        reference_distance = float(spa.GetDistanceBetween(point_ref, edge.Reference))
        say(f"  A GetDistanceBetween: {reference_distance:.6f} mm")
    except Exception as error:  # noqa: BLE001
        reference_distance = None
        say(f"  A GetDistanceBetween FAILED: {str(error).splitlines()[0][:110]}")
    measurable = spa.GetMeasurable(edge.Reference)
    say(f"  A GetMinimumDistance: "
        f"{float(measurable.GetMinimumDistance(point_ref)):.6f} mm")

    # ── B: is the first GetMeasurable pass the expensive one? ──────────────
    first = timed("B GetMeasurable x N (1st pass)",
                  lambda: [spa.GetMeasurable(cell.Reference) for cell in cells])
    second = timed("B GetMeasurable x N (2nd pass, same references)",
                   lambda: [spa.GetMeasurable(cell.Reference) for cell in cells])
    say(f"  B kept {len(first)} + {len(second)} objects")

    # ── B2: GetDistanceBetween over every edge, no Measurable at all ──────
    if reference_distance is not None:
        def all_distances():
            return [float(spa.GetDistanceBetween(point_ref, cell.Reference))
                    for cell in cells]
        distances = timed("B2 GetDistanceBetween x N", all_distances)
        closest = min(range(len(distances)), key=distances.__getitem__)
        say(f"  B2 nearest Edge.{closest + 1} @ {distances[closest]:.4f} mm")
    else:
        say("  B2 skipped (no GetDistanceBetween)")

    # ── C: Length without a Measurable, per edge ─────────────────────────
    try:
        say(f"  C cell.Value.Length: "
            f"{float(edge.Value.Length):.4f} mm (GetMeasurable said "
            f"{float(spa.GetMeasurable(edge.Reference).Length):.4f})")
    except Exception as error:  # noqa: BLE001
        say(f"  C cell.Value.Length FAILED: {str(error).splitlines()[0][:110]}")

    # ── D: one NLS pass ──────────────────────────────────────────────────
    timed("D NLS x N", lambda: [str(cell.Value.Name) for cell in cells])

    probe.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
