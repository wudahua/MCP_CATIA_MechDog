"""0.3.136 acceptance on B30 - the two gaps both 2026-11-15 reports left open.

    A  a single-view import still behaves as before (regression, one cell)
    B  deviation_mm: the conversion's cost is READ BACK, not promised
       B1  import_drawing(profile_mode='spline') quotes DEVIATION on a real file
       B2  the calibration corners read 0.0000, which is what makes B1 a reading
       B3  deviation=false quotes nothing (the switch is not decoration)
       B4  catia_gsd_spline quotes its OWN number, and it DIFFERS from B1's:
           a 2D sketch spline and a 3D gsd spline are different interpolants
    C  multi-view: one anchor, one sheet frame
       C1  two views on the front/top frames land in one coordinate system
       C2  pad both: the world X range stays 0..60, which is the alignment
       C3  two views whose u vectors disagree are REPORTED as unaligned
       C4  an explicit anchor moves the whole sheet, and says where from

    .venv/Scripts/python.exe -u scripts/b30_v0437_acceptance.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.blueprint import BlueprintTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0437a")
ROWS: list[tuple[str, bool, str]] = []

# Two views on one sheet: a 60 x 40 front view and a 60 x 25 top view, the top
# one 60 mm lower. y is flipped by the SVG parser (y up), so the sheet boxes are
# x 10..70, y -50..-10 and x 10..70, y -95..-70.
SHEET = b'''<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="120mm"
viewBox="0 0 100 120"><rect x="10" y="10" width="60" height="40"/>
<rect x="10" y="70" width="60" height="25"/></svg>'''

FRONT = {"type": "frame", "origin": [0, 0, 0], "u": [1, 0, 0], "v": [0, 0, 1]}
TOP = {"type": "frame", "origin": [0, 0, 0], "u": [1, 0, 0], "v": [0, 1, 0]}


def say(line: str = "") -> None:
    print(line, flush=True)


def cell(what: str, ok: bool, detail: str = "") -> None:
    ROWS.append((what, ok, detail))
    say(f"  [{'OK  ' if ok else 'FAIL'}] {what}" + (f"  -> {detail}" if detail else ""))


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


def fresh(conn, docs, name: str) -> str:
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, name + ".CATPart")
    close_ours(conn)
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    return path


def sheet_path() -> str:
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, "two_views.svg")
    with open(path, "wb") as handle:
        handle.write(SHEET)
    return path


def number(reply: str, pattern: str) -> float | None:
    found = re.search(pattern, reply)
    if not found:
        return None
    try:
        return float(found.group(1))
    except (IndexError, TypeError, ValueError):
        return None


def box(measurement, **extra) -> dict:
    return json.loads(measurement.execute("catia_get_bounding_box",
                                          {"method": "vertices", **extra}))


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    blueprint = BlueprintTools(conn)
    parts = PartDesignTools(conn)
    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)
    svg = sheet_path()
    say(f"MCP acceptance v0.3.136 on {conn.app.Name}")
    say(f"sheet: {svg}")

    # ── A: a single view is unchanged ──────────────────────────────────────
    say("\nA. single-view import (regression)")
    fresh(conn, docs, "Acc136Single")
    reply = blueprint.execute("catia_import_drawing",
                              {"file_path": svg, "region": [0, -60, 100, 0],
                               "plane": "xy"})
    cell("A1 one view, one sketch",
         "Imported 1 contour(s)" in reply and "Sketch '" in reply,
         " ".join(reply.split())[:110])
    cell("A2 no DEVIATION line without profile_mode='spline'",
         "DEVIATION" not in reply)

    # ── B: the conversion's cost, read back ────────────────────────────────
    say("\nB. deviation_mm on the polyline -> spline conversion")
    fresh(conn, docs, "Acc136Deviation")
    reply = blueprint.execute("catia_import_drawing",
                              {"file_path": svg, "region": [0, -60, 100, 0],
                               "plane": "xy", "profile_mode": "spline"})
    sketch2d = ""
    found = re.search(r"Sketch '([^']+)' created", reply)
    if found:
        sketch2d = found.group(1)
    worst = number(reply, r"DEVIATION[^.]*?max (\d+\.\d+) mm")
    cell("B1 import quotes a deviation for its own conversion", worst is not None,
         f"max {worst} mm, sketch {sketch2d!r}")
    cell("B2 the deviation is calibrated on corners that read 0.0000",
         "calibrated" in reply and "NOT calibrated" not in reply,
         " | ".join(part for part in reply.split(". ")
                    if "passes THROUGH" in part)[:160])
    if worst is not None:
        # A 60 x 40 rectangle as an interpolating closed spline bulges a long way;
        # anything under a millimetre would mean the probe measured the wrong
        # thing (the corners, not the sides).
        cell("B3 the side midpoints really do leave the outline",
             worst > 1.0, f"{worst:.4f} mm > 1.0 mm")

    fresh(conn, docs, "Acc136NoDeviation")
    reply = blueprint.execute("catia_import_drawing",
                              {"file_path": svg, "region": [0, -60, 100, 0],
                               "plane": "xy", "profile_mode": "spline",
                               "deviation": False})
    cell("B4 deviation=false quotes nothing", "DEVIATION" not in reply,
         " ".join(reply.split())[:90])

    fresh(conn, docs, "Acc136GsdDeviation")
    blueprint.execute("catia_import_drawing",
                      {"file_path": svg, "region": [0, -60, 100, 0],
                       "plane": "xy"})
    reply = gsd.execute("catia_gsd_spline",
                        {"element": sketch2d or "草图.1", "closed": True,
                         "name": "AccSpline3D"})
    third = number(reply, r"DEVIATION[^.]*?max (\d+\.\d+) mm")
    cell("B5 gsd_spline quotes its own deviation", third is not None,
         f"max {third} mm")
    cell("B6 the 3D spline's number DIFFERS from the 2D sketch spline's",
         third is not None and worst is not None and abs(third - worst) > 0.01,
         f"3D {third} vs 2D {worst} - the two interpolants are not the same")

    # ── C: multi-view alignment ────────────────────────────────────────────
    say("\nC. multi-view: one anchor for the whole sheet")
    fresh(conn, docs, "Acc136Views")
    reply = blueprint.execute("catia_import_drawing", {
        "file_path": svg,
        "views": [
            {"region": [0, -60, 100, 0], "plane": FRONT, "name": "FrontView"},
            {"region": [0, -120, 100, -60], "plane": TOP, "name": "TopView"},
        ]})
    say("        " + " ".join(reply.split())[:400])
    cell("C1 two sketches from one call",
         "sketch 'FrontView'" in reply and "sketch 'TopView'" in reply)
    check = re.search(r"Alignment check: ([^.]{0,160})", reply)
    cell("C2 the reply names the shared axis", "agree on X" in reply,
         check.group(1) if check else "no alignment sentence")
    cell("C3 nothing claims alignment it did not compare",
         "NO axis agrees" not in reply)

    # The alignment is a claim about WORLD coordinates: pad both and read the
    # X range of the result. If the views were placed in different frames this
    # number stops being 0..60.
    pad_front = parts.execute("catia_pad", {"height": 30,
                                            "sketch_name": "FrontView"})
    cell("C4 the front view pads", "Pad created" in pad_front,
         " ".join(pad_front.split())[:100])
    only_front = box(measurement)
    say(f"        front alone: {only_front.get('min')} .. {only_front.get('max')}")
    pad_top = parts.execute("catia_pad", {"height": 20, "sketch_name": "TopView"})
    cell("C5 the top view pads", "Pad created" in pad_top,
         " ".join(pad_top.split())[:100])
    both = box(measurement)
    say(f"        both:        {both.get('min')} .. {both.get('max')}")
    low = both.get("min") or {}
    high = both.get("max") or {}
    x_low, x_high = float(low.get("x", 999)), float(high.get("x", -999))
    cell("C6 both solids share the X range 0..60",
         abs(x_low) < 0.01 and abs(x_high - 60.0) < 0.01,
         f"X {x_low:.4f}..{x_high:.4f}")
    first_low = float((only_front.get("min") or {}).get("x", 999))
    first_high = float((only_front.get("max") or {}).get("x", -999))
    cell("C7 the front view alone is already 0..60 in X",
         abs(first_low) < 0.01 and abs(first_high - 60.0) < 0.01,
         f"X {first_low:.4f}..{first_high:.4f} - so C6 is not a coincidence "
         "of the two boxes covering each other")

    # ── C8: the negative case, reported rather than hidden ────────────────
    say("\nC8. two views whose u vectors disagree")
    fresh(conn, docs, "Acc136Unaligned")
    reply = blueprint.execute("catia_import_drawing", {
        "file_path": svg,
        "views": [
            {"region": [0, -60, 100, 0], "plane": "xy", "name": "AsXY"},
            {"region": [0, -120, 100, -60], "plane": "zx", "name": "AsZX"},
        ]})
    check = re.search(r"Alignment check: ([^.]{0,170})", reply)
    cell("C8 'xy' and 'zx' are reported as NOT aligned",
         "NO axis agrees" in reply, check.group(1) if check else "no sentence")

    # ── C9: an explicit anchor ────────────────────────────────────────────
    say("\nC9. an explicit anchor")
    fresh(conn, docs, "Acc136Anchor")
    reply = blueprint.execute("catia_import_drawing", {
        "file_path": svg, "anchor": [70.0, -50.0],
        "views": [
            {"region": [0, -60, 100, 0], "plane": FRONT, "name": "Anchored"},
        ]})
    cell("C9 the anchor is the one that was asked for",
         "70.0000, -50.0000" in reply,
         " | ".join(part for part in reply.split(". ") if "point (" in part)[:170])

    say("\n--- summary ---")
    ok = sum(1 for _, good, _ in ROWS if good)
    say(f"{ok}/{len(ROWS)} cells passed")
    for what, good, detail in ROWS:
        if not good:
            say(f"  FAILED {what}: {detail}")
    close_ours(conn)
    return 0 if ok == len(ROWS) else 1


if __name__ == "__main__":
    sys.exit(main())
