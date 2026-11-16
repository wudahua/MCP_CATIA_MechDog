"""Round 15 (0.3.140): reproduce the two P1s before touching the code.

P1-A  calibration.ok is null in `direction` mode - by construction: `along_rows`
      rows carry no `normal`, and `calibration_row` needs one. Measure the reply.

P1-B  a start point INSIDE the solid, probing along an axis at a FACE, is said to
      return a fake hit (offset 0, converged true, surface_point == the query
      point) while gm_mm reports 2.4-4.5. Those two numbers cannot both come from
      this code (`gap_mm = |along_point - surface_point|`), so the mechanism has
      to be measured rather than believed.

      Cell B1/B2 put a point inside a 40x30x20 box and probe Face.1 (the top face)
      both ways: along +Z and nearest. B3 does the same from outside as a control.

    .venv/Scripts/python.exe -u scripts/b30_v0444_probe_calibration_inside.py
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
                    or tempfile.gettempdir(), "catia_probe_v0444")


def say(line: str = "") -> None:
    print(line, flush=True)


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


def build_box(conn, docs, sketcher, parts):
    part = fresh(conn, docs, "ProbeV0444Box")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0.0, "y1": 0.0,
                                                "x2": 40.0, "y2": 30.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 20.0})
    return part


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
    say(f"probe v0444 on {conn.app.Name}")
    part = build_box(conn, docs, sketcher, parts)
    say(f"  box body '{part.MainBody.Name}', Face.1 = the top face (z=20)")

    say("")
    say("P1-A - calibration in ALONG mode (nearest control second)")
    along = json.loads(gsd.execute("catia_surface_probe", {
        "surface": "Face.1", "points": [[20.0, 15.0, 12.0]],
        "direction": [0, 0, 1]}))
    show("along +Z from (20,15,12), face z=20 -> offset should be 8.0", along)
    nearest = json.loads(gsd.execute("catia_surface_probe", {
        "surface": "Face.1", "points": [[20.0, 15.0, 25.0]]}))
    show("nearest from (20,15,25) -> distance should be 5.0", nearest)

    say("")
    say("P1-B - start point INSIDE the solid, probing the top face")
    inside_along = json.loads(gsd.execute("catia_surface_probe", {
        "surface": "Face.1", "points": [[20.0, 15.0, 10.0]],
        "direction": [0, 0, 1]}))
    show("along +Z from (20,15,10) [inside] -> if the line meets z=20, 10.0", inside_along)
    inside_nearest = json.loads(gsd.execute("catia_surface_probe", {
        "surface": "Face.1", "points": [[20.0, 15.0, 10.0]]}))
    show("nearest from (20,15,10) [inside] -> the top face is 10 mm away", inside_nearest)

    say("")
    say("P1-B2 - the SAME point against the body, for comparison")
    body = json.loads(gsd.execute("catia_surface_probe", {
        "surface": str(part.MainBody.Name), "points": [[20.0, 15.0, 10.0]]}))
    show("nearest from (20,15,10) against the body", body)

    say("")
    say("P1-B3 - a point that CANNOT reach the face: inside, probing +Z at the")
    say("        BOTTOM face's name, i.e. the axis leaves the face it was given")
    far = json.loads(gsd.execute("catia_surface_probe", {
        "surface": "Face.1", "points": [[20.0, 15.0, 30.0]],
        "direction": [0, 0, 1]}))
    show("along +Z from (20,15,30) ABOVE the top face -> the line never meets it",
         far)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
