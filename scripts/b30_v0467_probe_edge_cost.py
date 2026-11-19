"""Where does a single-edge chamfer's 17 s go?

The acceptance cell H measured: resolve 17 501 ms, build 16 ms, rebuild 16 ms.
So the cost is in turning an anchor coordinate into an edge, not in CATIA
rebuilding anything. This splits that 17.5 s into its two halves on the same
40 x 20 x 20 block (12 edges, 1 anchor):

  a  _edge_table          - topology_cells + one length measurement per edge
  b  the anchor match     - one GetMinimumDistance per (anchor, edge) pair
  c  probe point create/update/close

.venv/Scripts/python.exe -u scripts/b30_v0467_probe_edge_cost.py
"""
from __future__ import annotations

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

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0467")


def say(line: str = "") -> None:
    print(line, flush=True)


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    say(f"probe v0467 on {conn.app.Name}")
    for index in range(int(conn.documents.Count), 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = str(doc.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).lower().startswith(
                os.path.abspath(ROOT).lower()):
            try:
                doc.Close()
            except Exception:  # noqa: BLE001
                pass

    path = os.path.join(ROOT, "ProbeV0467.CATPart")
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": "ProbeV0467", "file_path": path})
    part = conn.get_active_part()
    body = conn.get_active_part_body()
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "B"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 40.0, "y2": 20.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "B", "height": 20.0})

    started = time.perf_counter()
    cells = topology_cells(conn, body, "Edge")
    say(f"a topology_cells: {len(cells)} edge(s) in "
        f"{(time.perf_counter() - started) * 1000:.0f} ms")

    started = time.perf_counter()
    spa = parts._topo_measure(part)
    measurables = []
    for cell in cells:
        measurables.append(spa.GetMeasurable(cell.Reference))
    say(f"a GetMeasurable x{len(cells)}: "
        f"{(time.perf_counter() - started) * 1000:.0f} ms")

    started = time.perf_counter()
    lengths = [parts._cell_measure(spa, cell, "Length") for cell in cells]
    say(f"a Length x{len(cells)}: {(time.perf_counter() - started) * 1000:.0f} ms"
        f" (first {lengths[0]})")

    started = time.perf_counter()
    table = parts._edge_table(body)
    say(f"a whole _edge_table: {(time.perf_counter() - started) * 1000:.0f} ms")

    probe = parts._edge_probe_points(part)
    started = time.perf_counter()
    point_ref = probe.add(0.0, 10.0, 0.0)
    probe.update()
    say(f"c probe point create+update: "
        f"{(time.perf_counter() - started) * 1000:.0f} ms")

    started = time.perf_counter()
    dists = [float(measurable.GetMinimumDistance(point_ref))
             for measurable in measurables]
    per = (time.perf_counter() - started) * 1000
    say(f"b GetMinimumDistance x{len(cells)}: {per:.0f} ms "
        f"({per / max(1, len(cells)):.0f} ms each); nearest "
        f"{min(dists):.4f} mm")

    started = time.perf_counter()
    probe.close()
    say(f"c probe close: {(time.perf_counter() - started) * 1000:.0f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
