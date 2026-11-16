"""Acceptance for 0.3.142 (round 16), on B30.

The report's three points, each measured through the TOOL (not the pure
functions), plus the two defects found while reproducing them:

  A  nearest mode marks a station inside the material            (control)
  B  along mode marks the same station even when the ray misses   (P1-1)
  C  a converged along row marks it too and reads the analytic height
  D  a row that missed answers offset_mm/surface_point null, usable false,
     and keeps what it reached under last_iterate_mm              (P1-2)
  E  a station ON the target is the one null, and says why by being 0.0000 away
  F  calibration runs on a station that converged, not on station 1 (P1-3)
  G  `gap_mm` equals an INDEPENDENT nearest reading of the same point
  H  inside_solid asks EVERY body: a station inside a second body reads true,
     and the reply names the bodies it asked
  I  no helper element is left in the tree

.venv/Scripts/python.exe -u scripts/b30_v0447_acceptance.py
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
                    or tempfile.gettempdir(), "catia_probe_v0447")
CELLS: list[tuple[str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def cell(name: str, ok: bool, detail: str = "") -> None:
    CELLS.append((name, ok, detail))
    say(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def fresh(conn, docs, name: str):
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, name + ".CATPart")
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
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    return conn.get_active_part()


def faces(conn, part) -> list[tuple[int, float]]:
    doc = conn.active_document
    spa = doc.GetWorkbench("SPAWorkbench")
    sel = conn.hso
    sel.Clear()
    sel.Add(part.MainBody)
    search_topology(sel, "Face")
    total = int(sel.Count)
    refs = [sel.Item(index).Reference for index in range(1, total + 1)]
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
    say(f"acceptance v0447 on {conn.app.Name}")

    # ── the box: 40 x 30 x 20, faces 1200/1200/600/800/600/800 ────────────
    box = fresh(conn, docs, "AccV0447Box")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0.0, "y1": 0.0,
                                                "x2": 40.0, "y2": 30.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 20.0})
    table = dict(faces(conn, box))
    top = next(index for index, area in table.items() if abs(area - 1200.0) < 1.0)
    side = next(index for index, area in table.items() if abs(area - 800.0) < 1.0)
    say(f"  box faces: {sorted(table.items())}; z=20 is Face.{top}, a y face is "
        f"Face.{side}")

    # ── A: nearest mode marks an inside station (control) ─────────────────
    near = json.loads(gsd.execute("catia_surface_probe", {
        "surface": f"Face.{top}", "points": [[20.0, 15.0, 10.0]]}))
    row = near["probes"][0]
    cell("A nearest mode: an inside station is marked and 10 mm from the face",
         row.get("inside_solid") is True
         and abs(float(row.get("distance_mm")) - 10.0) < 1e-6,
         f"inside_solid={row.get('inside_solid')}, distance={row.get('distance_mm')}")

    # ── B: the report's case - along mode, a face the axis cannot reach ───
    miss = json.loads(gsd.execute("catia_surface_probe", {
        "surface": f"Face.{top}", "points": [[20.0, 15.0, 10.0]],
        "direction": [0, -1, 0]}))
    miss_row = miss["probes"][0]
    cell("B1 along mode: the line never meets z=20, so it cannot converge",
         miss_row.get("converged") is False,
         f"converged={miss_row.get('converged')}, gap={miss_row.get('gap_mm')}")
    cell("B2 ... and the inside station is marked anyway (round 16 P1-1)",
         miss_row.get("inside_solid") is True,
         f"inside_solid={miss_row.get('inside_solid')}")

    # ── C: a converged along row at the y face ────────────────────────────
    hit = json.loads(gsd.execute("catia_surface_probe", {
        "surface": f"Face.{side}", "points": [[20.0, 15.0, 10.0]],
        "direction": [0, -1, 0]}))
    hit_row = hit["probes"][0]
    cell("C1 a converged along row is usable and marked inside",
         hit_row.get("usable") is True and hit_row.get("inside_solid") is True,
         f"usable={hit_row.get('usable')}, inside_solid={hit_row.get('inside_solid')}")
    cell("C2 and its offset is the 15 mm to that face",
         abs(abs(float(hit_row.get("offset_mm") or 0.0)) - 15.0) < 1e-6,
         f"offset_mm={hit_row.get('offset_mm')} (sign = which side it is on)")

    # ── D: the payload of the miss ────────────────────────────────────────
    cell("D1 the miss hands out NO offset", miss_row.get("offset_mm") is None,
         f"offset_mm={miss_row.get('offset_mm')!r}")
    cell("D2 ... no surface point", "surface_point" not in miss_row,
         f"keys={sorted(miss_row)}")
    cell("D3 ... and says usable=false", miss_row.get("usable") is False,
         f"usable={miss_row.get('usable')!r}")
    # `0.0 or -1` is -1: a legal zero must be tested with `is None`, not with
    # truthiness (this script's own first version failed D4 that way).
    cell("D4 what it did reach is kept, with the gap that says why",
         miss_row.get("last_iterate_mm") is not None
         and abs(float(miss_row["last_iterate_mm"])) < 1e-9
         and abs(float(miss_row.get("gap_mm") or 0.0) - 10.0) < 1e-6,
         f"last_iterate_mm={miss_row.get('last_iterate_mm')}, "
         f"gap_mm={miss_row.get('gap_mm')}")

    # ── E: the one station that IS the target: null, and 0.0000 away ──────
    on_face = json.loads(gsd.execute("catia_surface_probe", {
        "surface": f"Face.{top}", "points": [[20.0, 15.0, 20.0]],
        "direction": [0, 0, 1]}))
    on_row = on_face["probes"][0]
    cell("E a station ON the target is the one case that reads null",
         on_row.get("inside_solid") is None
         and on_row.get("start_distance_mm") is not None
         and abs(float(on_row["start_distance_mm"])) < 1e-6,
         f"inside_solid={on_row.get('inside_solid')}, "
         f"start_distance_mm={on_row.get('start_distance_mm')}")

    # ── G: gap_mm against an INDEPENDENT nearest reading of the same point
    if miss_row.get("last_along_point"):
        point = miss_row["last_along_point"]
        check = json.loads(gsd.execute("catia_surface_probe", {
            "surface": f"Face.{top}", "points": [point]}))
        measured = float(check["probes"][0].get("distance_mm") or -1.0)
        cell("G the published gap is what an independent reading of that point says",
             abs(measured - float(miss_row.get("gap_mm") or 0.0)) < 1e-4,
             f"gap_mm={miss_row.get('gap_mm')} vs nearest {measured:.6f} at "
             f"{point}")
    else:
        cell("G the published gap is what an independent reading of that point says",
             False, "the row published no last_along_point")

    # ── F: the cylinder - station 1 misses, station 2 carries the calibration
    cylinder = fresh(conn, docs, "AccV0447Cylinder")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_circle", {"x": 0.0, "y": 0.0, "radius": 15.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 50.0})
    lateral = max(faces(conn, cylinder), key=lambda pair: pair[1])
    say(f"  cylinder lateral Face.{lateral[0]} at {lateral[1]:.3f} mm2 "
        f"(2*pi*15*50 = {math.pi * 30 * 50:.3f})")
    mixed = json.loads(gsd.execute("catia_surface_probe", {
        "surface": f"Face.{lateral[0]}",
        "points": [[-30.0, 20.0, 25.0], [-30.0, 0.0, 25.0],
                   [-30.0, 10.0, 25.0]],
        "direction": [1, 0, 0]}))
    rows = mixed["probes"]
    calibration = mixed.get("calibration") or {}
    cell("F1 the first station is past the silhouette and misses",
         rows[0].get("converged") is False and rows[0].get("offset_mm") is None,
         f"converged={rows[0].get('converged')}, gap={rows[0].get('gap_mm')}")
    cell("F2 calibration runs anyway, on station 2",
         calibration.get("ok") is True and calibration.get("station") == 2,
         f"ok={calibration.get('ok')}, station={calibration.get('station')} of "
         f"{calibration.get('stations')}")
    want = [None, 15.0, 30.0 - math.sqrt(15.0 ** 2 - 10.0 ** 2)]
    got = [row.get("offset_mm") for row in rows]
    cell("F3 the two that reach the surface read the analytic heights",
         got[1] is not None and got[2] is not None
         and abs(got[1] - want[1]) < 1e-3 and abs(got[2] - want[2]) < 1e-3,
         f"offsets {got} vs [None, 15.0, {want[2]:.5f}]")

    # ── H: inside_solid asks EVERY body ───────────────────────────────────
    two = fresh(conn, docs, "AccV0447TwoBodies")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0.0, "y1": 0.0,
                                                "x2": 40.0, "y2": 30.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 20.0})
    parts.execute("catia_new_body", {"name": "Upper"})
    sketcher.execute("catia_create_sketch", {
        "plane": {"type": "offset", "base": "xy", "distance": 100.0}})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0.0, "y1": 0.0,
                                                "x2": 40.0, "y2": 30.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 10.0})
    say(f"  two bodies: {[str(two.Bodies.Item(i).Name) for i in range(1, int(two.Bodies.Count) + 1)]}")
    upper = json.loads(gsd.execute("catia_surface_probe", {
        "surface": "Face.1", "points": [[20.0, 15.0, 105.0]]}))
    upper_row = upper["probes"][0]
    cell("H1 a station inside the SECOND body reads inside",
         upper_row.get("inside_solid") is True,
         f"inside_solid={upper_row.get('inside_solid')}, checked against "
         f"{upper.get('inside_checked_against')}")
    cell("H2 the reply names every body it asked",
         len(upper.get("inside_checked_against") or []) == 2,
         f"{upper.get('inside_checked_against')}")

    # ── I: nothing left behind ────────────────────────────────────────────
    left = 0
    for part in (box, cylinder, two):
        try:
            for index in range(1, int(part.HybridBodies.Count) + 1):
                left += int(part.HybridBodies.Item(index).HybridShapes.Count)
        except Exception:  # noqa: BLE001 - a body without sets
            pass
    cell("I no helper element was left in any document", left == 0,
         f"{left} element(s) across three documents")

    say("")
    passed = sum(1 for _name, ok, _detail in CELLS if ok)
    say(f"{passed}/{len(CELLS)} cells passed")
    for name, ok, detail in CELLS:
        if not ok:
            say(f"  FAILED: {name}  ({detail})")
    return 0 if passed == len(CELLS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
