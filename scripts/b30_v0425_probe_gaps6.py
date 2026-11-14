"""Round six: one route left for sampling an edge - extract it first.

Rounds one and five closed the direct routes:
  AddNewPointOnCurveFromPercent(edge) -> created, UpdateObject failed 5/5
  Measurable.GetPointsOnCurve(pts), (5, pts), (pts, 5, 1), Dim pts(4) -> all refused

0.3.119 proved AddNewPointOnCurveFromPercent WORKS on a hybrid curve. So: can an
edge be extracted into a hybrid curve, and sampled there? If yes, P1-5's
"samples=N" is a wrapper, not a gap.

    .venv/Scripts/python.exe scripts/b30_v0425_probe_gaps6.py
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

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0425")
ROOT = os.path.join(_PROBE_ROOT, "gaps6")


def say(line: str = "") -> None:
    print(line, flush=True)


def close_ours(conn: CATIAConnection) -> None:
    for index in range(int(conn.documents.Count), 0, -1):
        document = conn.documents.Item(index)
        try:
            full = str(document.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).startswith(os.path.abspath(_PROBE_ROOT)):
            try:
                document.Close()
            except Exception:  # noqa: BLE001
                pass


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    gsd = GSDTools(conn)

    path = os.path.join(ROOT, "Extract.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": "Extract", "file_path": path, "overwrite": True})
    for index in range(1, int(conn.documents.Count) + 1):
        if conn.documents.Item(index).Name == "Extract.CATPart":
            conn.documents.Item(index).Activate()
    part = conn.get_active_part()

    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 40, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_circle", {"cx": 20, "cy": 20, "radius": 5})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pocket", {"through_all": True, "direction": "normal"})

    listing = json.loads(part_design.execute(
        "catia_list_edges", {"min_length": 30.0, "max_length": 32.5, "coords": True}))
    rim = None
    for edge in listing.get("edges", []):
        point = edge.get("point_on_edge") or []
        if point and abs(point[2] - 10.0) < 0.01:
            rim = edge["name"]
            break
    say(f"rim edge at z=10: {rim}")
    if rim is None:
        say("no rim - stopping")
        close_ours(conn)
        return 0

    say("")
    say("=" * 74)
    say("extract the edge, then sample the extract")
    say("=" * 74)
    extracted = None
    try:
        say("  " + " ".join(str(gsd.execute(
            "catia_gsd_extract",
            {"element": rim, "name": "RimCurve"})).split())[:220])
        extracted = "RimCurve"
    except Exception as error:  # noqa: BLE001
        say("  extract refused: " + " ".join(str(error).split())[:220])

    if extracted is None:
        close_ours(conn)
        return 0

    spa = part.Parent.GetWorkbench("SPAWorkbench")
    factory = part.HybridShapeFactory
    gs = gsd._get_geoset()
    reference = gsd._ref(extracted)
    try:
        length = float(spa.GetMeasurable(reference).Length)
        say(f"  extracted curve length {length:.4f} (the rim is 31.4159)")
    except Exception as error:  # noqa: BLE001
        say("  length unreadable: " + str(error).splitlines()[0][:90])
        length = None

    body_ref = part.CreateReferenceFromObject(part.MainBody)
    made = 0
    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        try:
            point = factory.AddNewPointOnCurveFromPercent(reference, fraction, False)
            gs.AppendHybridShape(point)
            part.UpdateObject(point)
        except Exception as error:  # noqa: BLE001
            say(f"  t={fraction}: refused " + str(error).splitlines()[0][:80])
            continue
        made += 1
        ref = part.CreateReferenceFromObject(point)
        try:
            distance = float(spa.GetMeasurable(ref).GetMinimumDistance(body_ref))
            say(f"  t={fraction:<5} distance to the solid {distance:.6f} mm")
        except Exception as error:  # noqa: BLE001
            say(f"  t={fraction:<5} distance unreadable: "
                + str(error).splitlines()[0][:60])
        try:
            gs.Remove(point)
        except Exception:  # noqa: BLE001
            pass
    say(f"  sampled {made}/5 points successfully")

    say("")
    say("done")
    close_ours(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
