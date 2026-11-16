"""B30 acceptance for 0.3.140 - report round 14, on the real machine.

Every cell is judged against an ANALYTIC answer, not against what the tool says
about itself:

  cone r(z) = 30 - 0.3z      (built from two circles, r30 at z=0, r12 at z=60)
  box  40 x 30 x 20          (faces at x=0/40, y=0/30, z=0/20)

Cells
  A  nearest probe on a cone: 5 stations, radius at each foot vs the cone
  B  height along +Y on the same cone: offset from y=-30 to the surface
  C  calibration verdict on a real call
  D  a topological FACE target
  E  a solid BODY target: outside reads a foot, inside reads 0 + the flag
  F  nothing left in the tree afterwards
  G  P1-1: catia_delete_feature on a body's name -> BODY_NOT_FEATURE, then
     catia_delete_body removes it
  H  P1-2: create_points=true leaves 6 Points, cascade=true removes them

    .venv/Scripts/python.exe -u scripts/b30_v0443_acceptance.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.results import ArgumentRejected  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402
from catia_mcp.topo_search import search_topology  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0443acc")
CELLS: list[tuple[str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def cell(name: str, ok: bool, detail: str = "") -> None:
    CELLS.append((name, ok, detail))
    say(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  <- {detail}" if detail else ""))


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


def cone(conn, docs, gsd):
    part = fresh(conn, docs, "AccCone")
    gsd.execute("catia_gsd_create_geoset", {"name": "SurfSet"})
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": 30, "name": "C_Bottom"})
    gsd.execute("catia_gsd_plane_offset", {"base_plane": "xy", "offset": 60,
                                           "name": "Plane_Z60"})
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 60],
                                     "support_plane": "Plane_Z60",
                                     "radius": 12, "name": "C_Top"})
    gsd.execute("catia_gsd_multi_section_surface", {
        "sections": ["C_Bottom", "C_Top"], "name": "Cone"})
    return part


def cone_radius(z: float) -> float:
    return 30.0 - 0.3 * z


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    gsd = GSDTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    say(f"acceptance 0.3.140 on {conn.app.Name}")

    # ── A/B/C/F: the surface probe itself ─────────────────────────────────
    part = cone(conn, docs, gsd)
    before = int(part.HybridBodies.Item("SurfSet").HybridShapes.Count)
    stations = [[0.0, -32.0, 0.0], [0.0, -32.0, 15.0], [0.0, -32.0, 30.0],
                [0.0, -32.0, 45.0], [0.0, -32.0, 60.0]]
    report = json.loads(gsd.execute("catia_surface_probe", {
        "surface": "Cone", "points": stations}))
    worst = 0.0
    for row in report["probes"]:
        if "surface_point" not in row:
            worst = 99.0
            continue
        x, y, z = row["surface_point"]
        worst = max(worst, abs(math.hypot(x, y) - cone_radius(z)))
    cell("A 5 nearest feet land on the cone (radius vs 30-0.3z)", worst < 1e-3,
         f"worst radius error {worst:.6f} mm")
    distances = [row.get("distance_mm") for row in report["probes"]]
    # y=-32 is 2.00 mm outside the rim at z=0 (r=30) and further from the wall as
    # the cone narrows, so the distances must be positive and strictly growing.
    cell("A2 distances are positive and grow down the narrowing cone",
         all(value is not None and value > 0 for value in distances)
         and abs(distances[0] - 2.0) < 1e-3
         and all(b > a for a, b in zip(distances, distances[1:])),
         f"distances {[round(value, 4) for value in distances]} (first = 2.0000 "
         f"= the radial gap at the rim)")
    lengths = [math.sqrt(sum(c * c for c in row["normal"])) for row in report["probes"]
               if row.get("normal")]
    cell("A3 the normals are unit vectors",
         len(lengths) == len(stations) and all(abs(v - 1) < 1e-6 for v in lengths),
         f"{len(lengths)}/{len(stations)} normals")
    cell("C the call calibrated itself", report["calibration"].get("ok") is True,
         f"stepped {report['calibration'].get('stepped_mm')} mm, read "
         f"{report['calibration'].get('read_mm')} mm, foot drift "
         f"{report['calibration'].get('foot_drift_mm')} mm")

    along = json.loads(gsd.execute("catia_surface_probe", {
        "surface": "Cone", "points": [[0.0, -32.0, 30.0], [0.0, -32.0, 45.0]],
        "direction": [0, 1, 0]}))
    offsets = [row.get("offset_mm") for row in along["probes"]]
    # from y=-32 along +y the cone's front wall is at y=-21 (z=30, r=21) and
    # y=-16.5 (z=45, r=16.5): 11.0 and 15.5 mm of travel
    cell("B height along +Y matches the cone (11.0 and 15.5 mm)",
         len(offsets) == 2 and abs(offsets[0] - 11.0) < 0.01
         and abs(offsets[1] - 15.5) < 0.01,
         f"offsets {offsets} (expected [11.0, 15.5])")
    cell("B2 both solved probes converged", all(row["converged"]
                                                for row in along["probes"]),
         f"{[row['iterations'] for row in along['probes']]} iteration(s)")

    after = int(part.HybridBodies.Item("SurfSet").HybridShapes.Count)
    cell("F no probe point is left in the tree", after == before,
         f"{before} element(s) before, {after} after, left_behind="
         f"{report['left_behind']}")

    # ── D/E: other target types, on a box ─────────────────────────────────
    box = fresh(conn, docs, "AccBox")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0.0, "y1": 0.0,
                                                "x2": 40.0, "y2": 30.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 20.0})
    face_report = json.loads(gsd.execute("catia_surface_probe", {
        "surface": "Face.1", "points": [[20.0, 15.0, 25.0]]}))
    row = face_report["probes"][0]
    cell("D a topological Face is probeable",
         row.get("surface_point") is not None
         and abs(row["surface_point"][2] - 20.0) < 1e-3
         and abs(row["distance_mm"] - 5.0) < 1e-3,
         f"foot {row.get('surface_point')} distance {row.get('distance_mm')}")
    body_report = json.loads(gsd.execute("catia_surface_probe", {
        "surface": str(box.MainBody.Name), "points": [[20.0, 15.0, 25.0], [20.0, 15.0, 10.0]]}))
    outside, inside = body_report["probes"]
    cell("E a body target answers for an outside point",
         abs(outside["distance_mm"] - 5.0) < 1e-3,
         f"distance {outside['distance_mm']} foot {outside['surface_point']}")
    cell("E2 an INSIDE point is flagged, not passed off as touching",
         inside.get("on_surface_or_inside") is True
         and any("INSIDE" in tip for tip in body_report["tips"]),
         f"distance {inside['distance_mm']}, flag "
         f"{inside.get('on_surface_or_inside')}")

    # ── G: P1-1 ───────────────────────────────────────────────────────────
    parts.execute("catia_new_body", {"name": "HandleCrown"})
    try:
        parts.execute("catia_delete_feature", {"feature_name": "HandleCrown"})
        cell("G delete_feature on a body name is refused with a pointer", False,
             "the call went through")
    except ArgumentRejected as error:
        cell("G delete_feature on a body name is refused with a pointer",
             getattr(error, "code", "") == "BODY_NOT_FEATURE"
             and any("catia_delete_body" in action
                     for action in getattr(error, "next_actions", [])),
             f"code={getattr(error, 'code', '')} next={getattr(error, 'next_actions', None)}")
    try:
        reply = parts.execute("catia_delete_body", {"name": "HandleCrown"})
        cell("G2 catia_delete_body removes the empty body", "deleted" in reply, reply[:80])
    except Exception as error:  # noqa: BLE001
        cell("G2 catia_delete_body removes the empty body", False,
             str(error).splitlines()[0][:100])

    # ── H: P1-2 ───────────────────────────────────────────────────────────
    part = fresh(conn, docs, "AccPoints")
    gsd.execute("catia_gsd_create_geoset", {"name": "G_Set"})
    gsd.execute("catia_gsd_spline", {
        "points": [[0, 0, 0], [10, 0, 4], [20, 0, -3], [30, 0, 6],
                   [40, 0, 0], [50, 0, 2]],
        "name": "S_tree", "create_points": True})
    shapes = part.HybridBodies.Item("G_Set").HybridShapes
    cell("H create_points=true leaves the curve plus its 6 Points",
         int(shapes.Count) == 7, f"{int(shapes.Count)} elements")
    reply = parts.execute("catia_delete_feature",
                          {"feature_name": "S_tree", "cascade": False})
    left = int(part.HybridBodies.Item("G_Set").HybridShapes.Count)
    cell("H2 without cascade the 6 Points stay, and the reply names them",
         left == 6 and "cascade=true" in reply, f"{left} left; {reply[:90]}")
    names = [str(part.HybridBodies.Item("G_Set").HybridShapes.Item(i).Name)
             for i in range(1, left + 1)]
    parts.execute("catia_delete_feature", {"names": names, "cascade": True})
    cell("H3 cascade=true removes them",
         int(part.HybridBodies.Item("G_Set").HybridShapes.Count) == 0,
         f"{int(part.HybridBodies.Item('G_Set').HybridShapes.Count)} left")

    say("")
    passed = sum(1 for _name, ok, _detail in CELLS if ok)
    say("-" * 78)
    say(f"{passed}/{len(CELLS)} cells passed")
    with open(os.path.join(ROOT, "acceptance.json"), "w", encoding="utf-8") as handle:
        json.dump([{"cell": name, "pass": ok, "detail": detail}
                   for name, ok, detail in CELLS], handle, ensure_ascii=False, indent=2)
    return 0 if passed == len(CELLS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
