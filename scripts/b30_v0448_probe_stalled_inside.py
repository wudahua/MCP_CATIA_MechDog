"""Round 17 follow-up: when the projection stalls from INSIDE, does the line
really miss the surface - or does the reply only say so?

The all-green report (2026-11-16, round 17) shows four inside stations that came
back `offset_mm: null, usable: false, start_distance_mm 7.02, gap_mm 7.02` -
`gap == start_distance` is the signature of an iteration that never moved: the
nearest point sat at right angles to the axis, so the axial step was zero on the
first read and the loop broke.

The tip then says, in so many words, "the line does not come within 0.001 mm of
the surface". On that bottle the line along -Y from x=-13 DOES cross the front
skin a few mm further on. If that is true here too, the reply is making a
geometric claim that is false - and the answer it refused to give is one a single
restart along the axis would have found.

Two controlled stations on a r=15 cylinder, both with analytic answers:

  (0, 0, 25) along -Y -> the axis; the front wall is at y=-15        (15.0)
  (5, 0, 25) along -Y -> x=5, wall at y=-sqrt(225-25) = -14.142      (14.142)

Both are INSIDE the solid, and from both the nearest point on the lateral face is
purely in +X, i.e. dot(gap, -Y) = 0.

.venv/Scripts/python.exe -u scripts/b30_v0448_probe_stalled_inside.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402
from catia_mcp.topo_search import search_topology  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0448")


def say(line: str = "") -> None:
    print(line, flush=True)


def faces(conn, part):
    spa = conn.active_document.GetWorkbench("SPAWorkbench")
    sel = conn.hso
    sel.Clear()
    sel.Add(part.MainBody)
    search_topology(sel, "Face")
    count = int(sel.Count)
    refs = [sel.Item(index).Reference for index in range(1, count + 1)]
    sel.Clear()
    return [(index, float(spa.GetMeasurable(ref).Area) * 1e6)
            for index, ref in enumerate(refs, 1)]


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    gsd = GSDTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, "ProbeV0448Cylinder.CATPart")
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
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": "ProbeV0448Cylinder",
                                    "file_path": path})
    part = conn.get_active_part()
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_circle", {"x": 0.0, "y": 0.0, "radius": 15.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 50.0})
    lateral = max(faces(conn, part), key=lambda pair: pair[1])
    say(f"cylinder r=15 h=50, lateral Face.{lateral[0]} at {lateral[1]:.3f} mm2")

    stations = [([0.0, 0.0, 25.0], 15.0),
                ([5.0, 0.0, 25.0], math.sqrt(225.0 - 25.0))]
    for point, truth in stations:
        say("")
        say(f"station {point} along -Y, analytic height {truth:.6f}")
        reply = json.loads(gsd.execute("catia_surface_probe", {
            "surface": f"Face.{lateral[0]}", "points": [point],
            "direction": [0, -1, 0]}))
        row = reply["probes"][0]
        say("  row: " + json.dumps(row, ensure_ascii=False))
        for tip in reply["tips"]:
            say(f"  tip: {tip[:150]}")
        # does the LINE really meet the surface? ask the nearest reading at the
        # analytic answer, which is on the skin by construction
        answer = [point[0], -math.sqrt(max(0.0, 225.0 - point[0] ** 2)), point[2]]
        check = json.loads(gsd.execute("catia_surface_probe", {
            "surface": f"Face.{lateral[0]}", "points": [answer]}))
        # `0.0 or -1.0` is -1.0: a legal zero is a READING, and this is the third
        # time this script family has eaten one (0.3.132, then the v0447
        # acceptance). Existence first, magnitude second.
        raw = check["probes"][0].get("distance_mm")
        on_skin = float("nan") if raw is None else float(raw)
        say(f"  the analytic hit {[round(v, 6) for v in answer]} is "
            f"{on_skin:.6f} mm from the face")
        say(f"  -> the line DOES cross the surface; the tool said converged="
            f"{row.get('converged')}, gap={row.get('gap_mm')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
