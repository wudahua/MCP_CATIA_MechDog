"""Can the reported `1/2 edge(s) done` be reproduced on B30, and does the fix fire?

The 2026-11-16 report gives the shape but not the file: `Hom_v15.CATPart`, "four
lobes on top of a column", Y -3..52.8, bounding box +-27.5 in X and Z, un-chamfered
volume 98007.6155 mm3, and two symmetric top edges at anchors (+-19, 52.8, 0). The
report also says a plain box does NOT reproduce it, so the trigger is the topology
- which is exactly what has to be found before any "could not reproduce" is
written down.

Three candidates, each in its OWN document (one update failure is document-wide):

  A  a cylinder with one slot cut through the top: two facing walls, one edge each
  B  a cross slot: four lobes, eight wall edges
  C  the control - a plain box with two opposite top edges, which the report says
     does not fail

For each: chamfer the two symmetric top edges, then read the volume before/after,
and print what the tool answered (a CodedError now, not a sentence).

    .venv/Scripts/python.exe -u scripts/b30_v0440_chamfer_repro.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.results import CodedError  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0440")
RADIUS = 27.5
TOP = 55.8           # the report's Y -3..52.8, shifted so the base sits on z = 0
SLOT_HALF = 19.0
SLOT_DEPTH = 23.9    # 132540 - 1444 * 23.9 ~= 98007, the report's volume


def say(line: str = "") -> None:
    print(line, flush=True)


def close_ours(conn) -> None:
    root = os.path.abspath(ROOT).lower()
    for index in range(int(conn.documents.Count), 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = str(doc.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).lower().startswith(root):
            try:
                doc.Close()
            except Exception:  # noqa: BLE001
                pass


def fresh(conn, docs, name: str):
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, name + ".CATPart")
    close_ours(conn)
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    return conn.get_active_part()


def volume(measurement) -> float | None:
    try:
        return json.loads(measurement.execute("catia_get_inertia", {})).get("volume_mm3")
    except Exception:  # noqa: BLE001
        return None


def cylinder_with_slot(conn, docs, sketcher, parts, name: str,
                       cross: bool) -> Any:
    """The report's column: r 27.5, 55.8 tall, slot(s) cut 23.9 deep from the top."""
    part = fresh(conn, docs, name)
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": RADIUS})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": TOP})
    for along in (("x", "y") if cross else ("x",)):
        # the sketch sits on the TOP face and the pocket cuts DOWN: a pocket
        # removes material on the sketch normal's negative side, so putting the
        # sketch at the slot's floor cut the bottom of the cylinder instead and
        # left no edge for the anchors to find (measured: 19 mm from any edge).
        sketcher.execute("catia_create_sketch",
                         {"plane": "xy", "offset": TOP})
        if along == "x":
            # a band across Y: |x| < SLOT_HALF is removed
            sketcher.execute("catia_sketch_rectangle",
                             {"x1": -SLOT_HALF, "y1": -RADIUS - 2,
                              "x2": SLOT_HALF, "y2": RADIUS + 2})
        else:
            sketcher.execute("catia_sketch_rectangle",
                             {"x1": -RADIUS - 2, "y1": -SLOT_HALF,
                              "x2": RADIUS + 2, "y2": SLOT_HALF})
        sketcher.execute("catia_close_sketch", {})
        parts.execute("catia_pocket", {"depth": SLOT_DEPTH})
    return part


def box(conn, docs, sketcher, parts, name: str) -> Any:
    part = fresh(conn, docs, name)
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": -27.5, "y1": -27.5, "x2": 27.5, "y2": 27.5})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": TOP})
    return part


def try_chamfer(parts, measurement, label: str, anchors, length: float,
                angle: float) -> None:
    before = volume(measurement)
    answer = ""
    code = ""
    try:
        answer = parts.execute("catia_chamfer",
                               {"length": length, "angle": angle,
                                "edge_anchors": anchors, "anchor_tol": 1.0,
                                "on_anchor_miss": "skip"})
        code = "OK (no error raised)"
    except CodedError as error:
        answer = str(error)
        code = (f"{getattr(error, 'code', type(error).__name__)} "
                f"modified={getattr(error, 'modified', None)} "
                + json.dumps(getattr(error, "data", None) or {},
                             ensure_ascii=False)[:300])
    except Exception as error:  # noqa: BLE001 - anything else is the answer
        answer = str(error)
        code = "OTHER: " + type(error).__name__
    after = volume(measurement)
    say(f"\n  {label}")
    say(f"    before {before} -> after {after}")
    say(f"    answer: {code}")
    say(f"    text: " + " ".join(answer.split())[:340])


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    parts = PartDesignTools(conn)
    measurement = MeasurementTools(conn)
    say(f"chamfer repro v0.3.139 on {conn.app.Name}")

    # ── A: one slot, two facing walls ──────────────────────────────────────
    say("\nA. cylinder r27.5 + ONE slot (two facing top edges)")
    part = cylinder_with_slot(conn, docs, sketcher, parts, "ReproSlotOne",
                              cross=False)
    edges = json.loads(parts.execute("catia_list_edges", {}))
    say(f"    {edges.get('count')} edge(s); the top ones:")
    for entry in edges.get("edges") or []:
        if (entry.get("length") or 0) > 40:
            say(f"      Edge.{entry['index']} len={entry.get('length')} "
                f"faces={entry.get('bounded_by')}")
    try_chamfer(parts, measurement, "chamfer 8 x 70 on (+-19, 0, 55.8)",
                [[19.0, 0.0, TOP], [-19.0, 0.0, TOP]], 8.0, 70.0)

    # ── B: a cross slot, four lobes ────────────────────────────────────────
    say("\nB. cylinder r27.5 + CROSS slot (four lobes)")
    part = cylinder_with_slot(conn, docs, sketcher, parts, "ReproSlotCross",
                              cross=True)
    before = volume(measurement)
    say(f"    volume with the lobes: {before} "
        f"(the report's un-chamfered part is 98007.6155)")
    edges = json.loads(parts.execute("catia_list_edges", {}))
    say(f"    {edges.get('count')} edge(s)")
    try_chamfer(parts, measurement, "chamfer 8 x 70 on (+-19, 0, 55.8)",
                [[19.0, 0.0, TOP], [-19.0, 0.0, TOP]], 8.0, 70.0)

    # ── D: two edges that SHARE A VERTEX ──────────────────────────────────
    # The project's own note says adjacent edges fail as a batch and succeed one
    # at a time (2026-10-23, three edges of one face). That is the path the
    # report's failure came through, so it is the next thing to try.
    say("\nD. plain box, two ADJACENT top edges (they share a corner)")
    part = box(conn, docs, sketcher, parts, "ReproBoxAdjacent")
    try_chamfer(parts, measurement, "chamfer 8 x 70 on (27.5,0,55.8) and (0,27.5,55.8)",
                [[27.5, 0.0, TOP], [0.0, 27.5, TOP]], 8.0, 70.0)

    say("\nE. plain box, ALL FOUR top edges at 8 x 70")
    part = box(conn, docs, sketcher, parts, "ReproBoxAllTop")
    try_chamfer(parts, measurement, "four top edges",
                [[27.5, 0.0, TOP], [0.0, 27.5, TOP],
                 [-27.5, 0.0, TOP], [0.0, -27.5, TOP]], 8.0, 70.0)

    # ── C: the control the report says does NOT fail ───────────────────────
    say("\nC. plain box, two opposite top edges (the report's negative control)")
    part = box(conn, docs, sketcher, parts, "ReproBox")
    try_chamfer(parts, measurement, "chamfer 8 x 70 on (+-27.5, 0, 55.8)",
                [[27.5, 0.0, TOP], [-27.5, 0.0, TOP]], 8.0, 70.0)
    close_ours(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
