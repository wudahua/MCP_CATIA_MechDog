"""Round 16 (0.3.141): is `inside_solid` really mode-dependent, and can a
non-converged row still be quoted as a coordinate?

The report makes three claims about `catia_surface_probe`:

  P1-1  in ALONG mode the same inside point comes back `inside_solid: null` (or
        false) while nearest mode says true - "7/7 wrong". Written from the code
        that can only happen through the hedge in `_mark_inside`, which is fed
        `1.0 if converged else 0.0` - i.e. the RAY's verdict is used as a stand-in
        for "is the query point on the target". Cell B puts an inside point on a
        face the axis never meets: converged false, so the hedge fires.

  P1-2  non-converged rows still print six decimals of `offset_mm` and a
        `surface_point`, so a caller reading only those builds a section that is
        0.19-0.79 mm off the surface. Cell E measures what a non-converged row
        looks like.

  P1-3  calibration is pinned to the FIRST row, and on a real surface the first
        station is the one most likely not to converge - so the field that was
        just fixed is silent again. Cell F: first row misses, later rows hit.

Everything runs on ONE 40x30x20 box, in one document, because the quantities
here are geometric and the box's faces are analytic: z=20 is 1200 mm2, y=0 is
800, x=0 is 600.

.venv/Scripts/python.exe -u scripts/b30_v0446_probe_along_inside.py
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
                    or tempfile.gettempdir(), "catia_probe_v0446")
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


def face_table(conn, part) -> list[tuple[int, float, list[float]]]:
    """(index, area mm2, centroid z) for every face of the active body."""
    doc = conn.active_document
    spa = doc.GetWorkbench("SPAWorkbench")
    sel = conn.hso
    sel.Clear()
    sel.Add(part.MainBody)
    search_topology(sel, "Face")
    total = int(sel.Count)
    refs = [sel.Item(index).Reference for index in range(1, total + 1)]
    sel.Clear()
    table = []
    for index, ref in enumerate(refs, 1):
        measurable = spa.GetMeasurable(ref)
        table.append((index, float(measurable.Area) * 1e6, measurable))
    return table


def show(label: str, report: dict) -> None:
    say(f"  {label}")
    say(f"    calibration: {json.dumps(report.get('calibration'), ensure_ascii=False)}")
    for row in report.get("probes", []):
        say("    " + json.dumps(row, ensure_ascii=False))
    for tip in report.get("tips", []):
        say(f"    tip: {tip}")


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    gsd = GSDTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    say(f"probe v0446 on {conn.app.Name}")

    part = fresh(conn, docs, "ProbeV0446Box")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0.0, "y1": 0.0,
                                                "x2": 40.0, "y2": 30.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 20.0})
    body_name = str(part.MainBody.Name)
    say(f"  box '{body_name}' 40 x 30 x 20; faces by area:")
    for index, area, _m in face_table(conn, part):
        say(f"    Face.{index}: {area:9.3f} mm2")
    # 40x30 = 1200 -> z faces; 40x20 = 800 -> y faces; 30x20 = 600 -> x faces
    top = next(index for index, area, _m in face_table(conn, part)
               if abs(area - 1200.0) < 1.0)
    side_y = None
    for index, area, _m in face_table(conn, part):
        if abs(area - 800.0) < 1.0:
            side_y = index
            break
    say(f"  using Face.{top} as the z=20 face and Face.{side_y} as a y face")

    # ── A: nearest mode on an inside point, as the control ────────────────
    say("")
    say("A - nearest mode, point (20,15,10) inside the solid")
    near = json.loads(gsd.execute("catia_surface_probe", {
        "surface": f"Face.{top}", "points": [[20.0, 15.0, 10.0]]}))
    show("the top face is 10 mm away", near)
    row = near["probes"][0]
    cell("A1 nearest mode marks the inside point", row.get("inside_solid") is True,
         f"inside_solid={row.get('inside_solid')}, distance="
         f"{row.get('distance_mm')}")

    # ── B: along mode, same point, a face the axis never meets ────────────
    say("")
    say("B - along mode, SAME inside point (20,15,10), at the z=20 face along -Y")
    along_miss = json.loads(gsd.execute("catia_surface_probe", {
        "surface": f"Face.{top}", "points": [[20.0, 15.0, 10.0]],
        "direction": [0, -1, 0]}))
    show("the line y->-inf at z=10 never meets z=20, so this MUST not converge",
         along_miss)
    miss = along_miss["probes"][0]
    cell("B1 that row is not converged", miss.get("converged") is False,
         f"converged={miss.get('converged')}, gap={miss.get('gap_mm')}")
    cell("B2 the inside point is marked anyway", miss.get("inside_solid") is True,
         f"inside_solid={miss.get('inside_solid')}")

    # ── C: along mode that DOES meet the face, same inside point ──────────
    say("")
    say(f"C - along mode at Face.{side_y} (a y face) from the same inside point")
    along_hit = json.loads(gsd.execute("catia_surface_probe", {
        "surface": f"Face.{side_y}", "points": [[20.0, 15.0, 10.0]],
        "direction": [0, -1, 0]}))
    show("along -Y should hit the y=0 face 15 mm away", along_hit)
    hit = along_hit["probes"][0]
    cell("C1 the converged along row marks the inside point",
         hit.get("inside_solid") is True, f"inside_solid={hit.get('inside_solid')}")
    cell("C2 and its offset is the 15 mm to that face",
         hit.get("converged") is True
         and abs(abs(float(hit.get("offset_mm") or 0.0)) - 15.0) < 1e-3,
         f"offset_mm={hit.get('offset_mm')} (sign = which side the face is on)")
    cell("C3 a converged row is usable and carries its own start distance",
         hit.get("usable") is True
         and abs(float(hit.get("start_distance_mm") or -1.0) - 15.0) < 1e-3,
         f"usable={hit.get('usable')}, start_distance_mm="
         f"{hit.get('start_distance_mm')}")

    # ── D: outside point, along mode - the flag must say false, not null ──
    say("")
    say("D - along mode from (20,-5,10), which is OUTSIDE the solid")
    outside = json.loads(gsd.execute("catia_surface_probe", {
        "surface": f"Face.{side_y}", "points": [[20.0, -5.0, 10.0]],
        "direction": [0, -1, 0]}))
    show("along -Y from y=-5 moves AWAY from the y=0 face", outside)
    out_row = outside["probes"][0]
    cell("D1 an outside start is false, never null",
         out_row.get("inside_solid") is False,
         f"inside_solid={out_row.get('inside_solid')}")

    # ── E: what a non-converged row actually carries ──────────────────────
    say("")
    say("E - the payload of a NON-converged row")
    say(f"    offset_mm      = {miss.get('offset_mm')!r}")
    say(f"    surface_point  = {miss.get('surface_point')!r}")
    say(f"    along_point    = {miss.get('along_point')!r}")
    say(f"    gap_mm         = {miss.get('gap_mm')!r}")
    cell("E1 a non-converged row does NOT hand out an offset to build from",
         miss.get("offset_mm") is None,
         f"offset_mm={miss.get('offset_mm')!r}")
    cell("E2 ... nor a surface point", miss.get("surface_point") is None,
         f"surface_point={miss.get('surface_point')!r}")
    cell("E3 ... and says usable:false so a caller cannot miss it",
         miss.get("usable") is False, f"usable={miss.get('usable')!r}")

    # ── F: calibration when the FIRST station misses and a later one hits ──
    say("")
    say("F - a cylinder, so ONE axis meets the surface at some stations and not")
    say("    at others: r=15, h=50, probing the lateral face along +X at z=25")
    cylinder = fresh(conn, docs, "ProbeV0446Cylinder")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_circle", {"x": 0.0, "y": 0.0, "radius": 15.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 50.0})
    lateral = max(face_table(conn, cylinder), key=lambda row: row[1])
    say(f"  lateral face is Face.{lateral[0]} at {lateral[1]:.3f} mm2 "
        f"(2*pi*15*50 = {math.pi * 30 * 50:.3f})")
    mixed = json.loads(gsd.execute("catia_surface_probe", {
        "surface": f"Face.{lateral[0]}",
        "points": [[-30.0, 20.0, 25.0], [-30.0, 0.0, 25.0], [-30.0, 10.0, 25.0]],
        "direction": [1, 0, 0]}))
    show("y=20 is outside r=15 so +X never touches the surface; y=0 and y=10 do",
         mixed)
    cell("F0 the first station really missed",
         mixed["probes"][0].get("converged") is False,
         f"converged={mixed['probes'][0].get('converged')}, "
         f"offset={mixed['probes'][0].get('offset_mm')!r}, "
         f"gap={mixed['probes'][0].get('gap_mm')}")
    calibration = mixed.get("calibration") or {}
    cell("F1 calibration still runs, on a station that converged",
         calibration.get("ok") is True and calibration.get("station") != 1,
         f"ok={calibration.get('ok')}, station={calibration.get('station')} of "
         f"{calibration.get('stations')}")
    cell("F2 a station past the silhouette is not quoted as a height",
         mixed["probes"][0].get("usable") is False
         and mixed["probes"][0].get("offset_mm") is None,
         f"usable={mixed['probes'][0].get('usable')}, "
         f"offset_mm={mixed['probes'][0].get('offset_mm')!r}")
    # y=0 meets the cylinder at x=-15 -> 15 mm of travel; y=10 meets it at
    # x=-sqrt(15^2-10^2) = -11.18034 -> 18.81966 mm. Analytic, so the cell is a
    # measurement rather than a memory.
    want = [None, 15.0, 30.0 - math.sqrt(15.0 ** 2 - 10.0 ** 2)]
    got = [row.get("offset_mm") for row in mixed["probes"]]
    cell("F3 the two that do meet it read the analytic heights (15, 18.81966)",
         got[1] is not None and got[2] is not None
         and abs(got[1] - want[1]) < 1e-3 and abs(got[2] - want[2]) < 1e-3,
         f"offsets {got} vs {[None if w is None else round(w, 5) for w in want]}")

    # ── G: nothing left in the tree ───────────────────────────────────────
    say("")
    shapes = int(conn.get_active_part().MainBody.Shapes.Count)
    say(f"  body feature count after all probes: {shapes}")
    active = conn.get_active_part()
    leftovers = int(active.HybridBodies.Count)
    total = 0
    for index in range(1, leftovers + 1):
        total += int(active.HybridBodies.Item(index).HybridShapes.Count)
    cell("G the probes left no helper element", total == 0,
         f"{leftovers} set(s), {total} element(s)")

    say("")
    passed = sum(1 for _n, ok, _d in CELLS if ok)
    say(f"{passed}/{len(CELLS)} cells passed")
    for name, ok, detail in CELLS:
        if not ok:
            say(f"  FAILED: {name}  ({detail})")
    return 0 if passed == len(CELLS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
