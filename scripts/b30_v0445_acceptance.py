"""B30 acceptance for 0.3.141 - report round 15, judged against analytic answers.

Round 15 reported two credibility defects in `catia_surface_probe`, and chasing
them turned up a third that neither report could see from outside: a 'Face.N'
target was resolved through the generic resolver, whose Extract answers the WHOLE
SHELL (Area 5200.000 mm2 on a 40x30x20 box against 1200.000 for Face.1), so "the
distance to this face" was the distance to whichever face was nearest.

Cells
  A  the round-14 capability still works (cone, analytic radius)
  B  calibration now runs in BOTH modes and says which
  C  Face.N is the face itself: Face.1 and Face.2 must answer DIFFERENTLY from a
     point between them, and the measurable's area must be the face's
  D  an inside query point is flagged, on a face and on a body
  E  nothing is left in the tree

    .venv/Scripts/python.exe -u scripts/b30_v0445_acceptance.py
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

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0445acc")
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
    part = fresh(conn, docs, "Acc15Cone")
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


def box(conn, docs, sketcher, parts):
    part = fresh(conn, docs, "Acc15Box")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0.0, "y1": 0.0,
                                                "x2": 40.0, "y2": 30.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 20.0})
    return part


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    gsd = GSDTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    say(f"acceptance 0.3.141 on {conn.app.Name}")

    # ── A: the round-14 capability, unchanged ─────────────────────────────
    part = cone(conn, docs, gsd)
    stations = [[0.0, -32.0, 0.0], [0.0, -32.0, 15.0], [0.0, -32.0, 30.0],
                [0.0, -32.0, 45.0], [0.0, -32.0, 60.0]]
    report = json.loads(gsd.execute("catia_surface_probe", {
        "surface": "Cone", "points": stations}))
    worst = 0.0
    for row in report["probes"]:
        x, y, z = row["surface_point"]
        worst = max(worst, abs(math.hypot(x, y) - (30.0 - 0.3 * z)))
    cell("A nearest feet still land on the cone", worst < 1e-3,
         f"worst radius error {worst:.6f} mm")
    cell("A2 nearest-mode calibration still passes",
         report["calibration"].get("ok") is True
         and report["calibration"].get("mode") == "nearest",
         json.dumps(report["calibration"], ensure_ascii=False))

    along = json.loads(gsd.execute("catia_surface_probe", {
        "surface": "Cone", "points": [[0.0, -32.0, 30.0], [0.0, -32.0, 45.0]],
        "direction": [0, 1, 0]}))
    offsets = [row["offset_mm"] for row in along["probes"]]
    cell("A3 heights along +Y are still exact",
         abs(offsets[0] - 11.0) < 0.01 and abs(offsets[1] - 15.5) < 0.01,
         f"{offsets} (expected [11.0, 15.5])")

    # ── B: calibration in the mode that had none ──────────────────────────
    cell("B along-mode calibration now runs and says so",
         along["calibration"].get("ok") is True
         and along["calibration"].get("mode") == "along"
         and abs(float(along["calibration"].get("read_mm", 0)) - 0.5) < 1e-3,
         json.dumps(along["calibration"], ensure_ascii=False))
    cell("B2 no probe is left unconverged on a clean cone",
         all(row["converged"] for row in along["probes"]),
         f"{[row['iterations'] for row in along['probes']]} iteration(s)")

    cone_shapes = int(part.HybridBodies.Item("SurfSet").HybridShapes.Count)

    # ── C: Face.N is the face, not the shell ──────────────────────────────
    boxed = box(conn, docs, sketcher, parts)
    spa = conn.active_document.GetWorkbench("SPAWorkbench")
    before = int(boxed.HybridBodies.Count)
    inside_point = [[20.0, 15.0, 9.5]]     # 10.5 below the top face, 9.5 above the bottom
    top_report = json.loads(gsd.execute("catia_surface_probe", {
        "surface": "Face.1", "points": inside_point}))
    top = top_report["probes"][0]
    bottom = json.loads(gsd.execute("catia_surface_probe", {
        "surface": "Face.2", "points": inside_point}))["probes"][0]
    cell("C Face.1 and Face.2 answer DIFFERENTLY from the same inside point",
         abs(top["distance_mm"] - 10.5) < 1e-3
         and abs(bottom["distance_mm"] - 9.5) < 1e-3,
         f"Face.1 {top['distance_mm']} mm (foot z={top['surface_point'][2]}), "
         f"Face.2 {bottom['distance_mm']} mm (foot z={bottom['surface_point'][2]})")
    grr = gsd._probe_surface_ref(boxed, "Face.1")
    area = float(spa.GetMeasurable(grr).Area) * 1e6
    cell("C2 the resolved Face.1 measurable is ONE face (1200 mm2, not the shell)",
         abs(area - 1200.0) < 1e-3, f"Area {area:.3f} mm2")
    cell("C3 resolving a face adds nothing to the tree",
         int(boxed.HybridBodies.Count) == before,
         f"{before} -> {int(boxed.HybridBodies.Count)} geometrical set(s)")

    # ── D: the inside flag ────────────────────────────────────────────────
    cell("D an inside query point is flagged on a FACE target",
         top.get("inside_solid") is True, f"inside_solid={top.get('inside_solid')}")
    outside = json.loads(gsd.execute("catia_surface_probe", {
        "surface": "Face.1", "points": [[20.0, 15.0, 25.0]]}))["probes"][0]
    cell("D2 an outside query point is not flagged",
         outside.get("inside_solid") is False,
         f"distance {outside['distance_mm']} mm, inside_solid="
         f"{outside.get('inside_solid')}")
    body_report = json.loads(gsd.execute("catia_surface_probe", {
        "surface": str(boxed.MainBody.Name),
        "points": [[20.0, 15.0, 9.5]]}))
    body_row = body_report["probes"][0]
    cell("D3 a body target still says on-or-inside and claims no more",
         body_row.get("on_surface_or_inside") is True
         and body_row.get("inside_solid") is None,
         f"distance {body_row['distance_mm']}, "
         f"inside_solid={body_row.get('inside_solid')}")
    cell("D4 the reply says those stations sit in the material",
         any("START INSIDE" in tip for tip in top_report["tips"]),
         next((tip[:100] for tip in top_report["tips"] if "START INSIDE" in tip),
              "no tip mentions it"))

    # ── E: no scaffolding left anywhere ───────────────────────────────────
    cell("E the cone call left no helper element", cone_shapes == 6,
         f"{cone_shapes} element(s) in SurfSet (6 created), left_behind="
         f"{report['left_behind']}")

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
