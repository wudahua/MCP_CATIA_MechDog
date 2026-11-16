"""Acceptance for 0.3.144, on B30: the walk that stalled and the line that did not.

Round 17's report is all-green, and its own data is what raised this: four inside
stations answered `gap_mm == start_distance_mm`, i.e. the iteration never moved,
while the tip said the line "does not come within 0.001 mm of the surface". On a
r=15 cylinder the same shape has an analytic answer, so it can be settled:

  A  start (0,0,25) inside, along -Y   the skin is at y=-15  -> 15.000
  B  start (5,0,25) inside, along -Y   x=5 wall: y=-sqrt(125) -> 14.142136
     both used to come back not converged with the surface 15.0 / 10.0 away
  C  a line that really cannot reach the face still answers a miss
  D  the reply says WHY, and no longer claims the line missed the surface
  E  a stalled-then-restarted station is a calibration station like any other
  F  no helper element is left behind

.venv/Scripts/python.exe -u scripts/b30_v0449_acceptance.py
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
                    or tempfile.gettempdir(), "catia_probe_v0449")
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
    spa = conn.active_document.GetWorkbench("SPAWorkbench")
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
    say(f"acceptance v0449 on {conn.app.Name}")

    cylinder = fresh(conn, docs, "AccV0449Cylinder")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_circle", {"x": 0.0, "y": 0.0, "radius": 15.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 50.0})
    lateral = max(faces(conn, cylinder), key=lambda pair: pair[1])
    say(f"  cylinder r=15 h=50, lateral Face.{lateral[0]} at {lateral[1]:.3f} mm2")

    # ── A: the axis station ───────────────────────────────────────────────
    on_axis = json.loads(gsd.execute("catia_surface_probe", {
        "surface": f"Face.{lateral[0]}", "points": [[0.0, 0.0, 25.0]],
        "direction": [0, -1, 0]}))
    row = on_axis["probes"][0]
    say("  A row: " + json.dumps(row, ensure_ascii=False))
    cell("A1 the station on the axis now reaches the skin (15.000 mm)",
         row.get("converged") is True
         and abs(float(row.get("offset_mm") or 0.0) - 15.0) < 1e-6,
         f"offset_mm={row.get('offset_mm')}, gap_mm={row.get('gap_mm')}")
    cell("A2 and it says it stalled and restarted",
         row.get("stalled") is True and row.get("restart_mm") is not None,
         f"stalled={row.get('stalled')}, restart_mm={row.get('restart_mm')}")
    cell("A3 the hit really is on the face: y = -15",
         row.get("surface_point") is not None
         and abs(float(row["surface_point"][1]) + 15.0) < 1e-3,
         f"surface_point={row.get('surface_point')}")

    # ── B: off-axis, where both ends of the line are crossings ────────────
    off = json.loads(gsd.execute("catia_surface_probe", {
        "surface": f"Face.{lateral[0]}", "points": [[5.0, 0.0, 25.0]],
        "direction": [0, -1, 0]}))
    off_row = off["probes"][0]
    say("  B row: " + json.dumps(off_row, ensure_ascii=False))
    want = math.sqrt(225.0 - 25.0)
    cell("B1 the off-axis station reads the analytic 14.142136",
         off_row.get("converged") is True
         and abs(float(off_row.get("offset_mm") or 0.0) - want) < 1e-3,
         f"offset_mm={off_row.get('offset_mm')} vs {want:.6f}")
    cell("B2 ... in the direction that was asked for, not behind it",
         float(off_row.get("offset_mm") or -1.0) > 0
         and float(off_row["surface_point"][1]) < 0.0,
         f"offset_mm={off_row.get('offset_mm')}, "
         f"hit y={off_row.get('surface_point', [0, None])[1]}")

    # ── C: a line that really cannot reach the surface ────────────────────
    box = fresh(conn, docs, "AccV0449Box")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0.0, "y1": 0.0,
                                                "x2": 40.0, "y2": 30.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 20.0})
    table = dict(faces(conn, box))
    top = next(index for index, area in table.items() if abs(area - 1200.0) < 1.0)
    miss = json.loads(gsd.execute("catia_surface_probe", {
        "surface": f"Face.{top}", "points": [[20.0, 15.0, 10.0]],
        "direction": [0, -1, 0]}))
    miss_row = miss["probes"][0]
    say("  C row: " + json.dumps(miss_row, ensure_ascii=False))
    cell("C1 a line parallel to the face is still a miss, with no offset",
         miss_row.get("converged") is False
         and miss_row.get("offset_mm") is None
         and miss_row.get("usable") is False,
         f"converged={miss_row.get('converged')}, "
         f"gap_mm={miss_row.get('gap_mm')}")
    cell("C2 and no restart was claimed, because none improved anything",
         miss_row.get("stalled") is True and "restart_mm" not in miss_row,
         f"stalled={miss_row.get('stalled')}, restart_mm="
         f"{miss_row.get('restart_mm')!r}")

    # ── D: the reply says why, and stops claiming a missed line ───────────
    joined = " ".join(miss["tips"])
    cell("D1 the miss tip says the line may still cross the surface",
         "NOT the same as" in joined and "RIGHT ANGLES" in joined,
         next((tip[:90] for tip in miss["tips"] if "NOT the same as" in tip),
              "no such tip"))
    cell("D2 the old claim is gone from every tip of this reply",
         "does not come within" not in joined,
         "checked the whole tips list")
    joined_restart = " ".join(on_axis["tips"])
    cell("D3 the restarted reply names the restart",
         "RESTARTED" in joined_restart,
         next((tip[:90] for tip in on_axis["tips"] if "RESTARTED" in tip),
              "no such tip"))

    # ── E: what calibration can and cannot say about a restarted station ──
    # The station on the axis is a line through a closed shell: moving the origin
    # 0.5 mm back puts the walk on the OPPOSITE wall (measured: base hit y=-15,
    # moved hit y=+15, "grown" -29.5). The exact 0.5 mm expectation does not apply
    # there, and the honest answer is "cannot be checked this way" - not "the
    # reading is bad".
    calibration = on_axis.get("calibration") or {}
    cell("E1 the ambiguous station is declined, not condemned",
         calibration.get("ok") is None
         and "more than once" in str(calibration.get("reason")),
         f"ok={calibration.get('ok')}, reason="
         f"{str(calibration.get('reason'))[:110]}")
    # ... and the mechanism that was there before still passes where the line is
    # unambiguous: a plane, one crossing, same hit either way.
    plain = json.loads(gsd.execute("catia_surface_probe", {
        "surface": f"Face.{top}", "points": [[20.0, 15.0, 12.0]],
        "direction": [0, 0, 1]}))
    plain_cal = plain.get("calibration") or {}
    cell("E2 a station with ONE crossing is still calibrated normally",
         plain_cal.get("ok") is True
         and abs(float(plain_cal.get("read_mm") or 0.0) - 0.5) < 1e-3,
         f"ok={plain_cal.get('ok')}, station={plain_cal.get('station')}, "
         f"read_mm={plain_cal.get('read_mm')}, drift="
         f"{plain_cal.get('foot_drift_mm')}")

    # ── F: nothing left behind ────────────────────────────────────────────
    left = 0
    for part in (cylinder, box):
        try:
            for index in range(1, int(part.HybridBodies.Count) + 1):
                left += int(part.HybridBodies.Item(index).HybridShapes.Count)
        except Exception:  # noqa: BLE001
            pass
    cell("F no helper element was left in any document", left == 0,
         f"{left} element(s) across two documents")

    say("")
    passed = sum(1 for _name, ok, _detail in CELLS if ok)
    say(f"{passed}/{len(CELLS)} cells passed")
    for name, ok, detail in CELLS:
        if not ok:
            say(f"  FAILED: {name}  ({detail})")
    return 0 if passed == len(CELLS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
