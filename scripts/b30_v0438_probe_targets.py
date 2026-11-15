"""Round 2 of the 0.3.137 probe: the two questions round 1 left open.

Round 1 (b30_v0437_probe_deviation_views.py) established:

    * a SKETCH exposes NOTHING to SPAWorkbench (Length, Area, Volume, GetPoint,
      GetCOG, GetBoundingBox, GetPlane all fail) - so "where did this sketch
      land" cannot be read back, and a multi-view import has to prove it another
      way;
    * a Line2D inside a sketch and a CLOSED spline both refuse
      AddNewPointOnCurveFromPercent + UpdateObject ("方法 UpdateObject 失败"),
      so `catia_measure_deviation` cannot sample either;
    * GetMeasurable(<3D point>).GetMinimumDistance(<spline>) works, and reads
      0.0000 at a corner the spline passes through - the calibration reading;
    * GetMeasurable(<3D point>).GetMinimumDistance(<whole sketch>) works too.

This script measures what is still needed to ship `deviation_mm`:

    D  two sketches on two frames sharing the X axis, padded: does the frame put
       the sketch where the frame says? Read back from the SOLIDS, since the
       sketch itself reports nothing.
    F  can a Spline2D INSIDE a sketch be the target of GetMinimumDistance? That
       is what decides whether `import_drawing(profile_mode='spline')` can quote
       its own deviation or has to defer to catia_gsd_spline.
    G  can a Line2D inside a sketch be the target? (the segments side of the
       same question)
    H  one point, two targets, same instant: distance to the Spline2D vs to the
       whole sketch. If they differ the sketch route is measuring something else.

    .venv/Scripts/python.exe -u scripts/b30_v0438_probe_targets.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0438t")
ROWS: list[tuple[str, bool, str]] = []


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


def fresh(conn, docs, name: str):
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, name + ".CATPart")
    close_ours(conn)
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    return conn.get_active_part()


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    parts = PartDesignTools(conn)
    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)
    say(f"MCP probe v0.3.138 on {conn.app.Name}")

    # ── D: two frames sharing X, read back from the solids ──────────────────
    say("\nD. two frames sharing X - where do the solids land?")
    part = fresh(conn, docs, "ProbeTwoViews")
    sketcher.execute("catia_create_sketch",
                     {"plane": {"type": "frame", "origin": [0, 0, 0],
                                "u": [1, 0, 0], "v": [0, 1, 0]}, "name": "FrontView"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 60, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    say("        pad front: " + " ".join(str(parts.execute(
        "catia_pad", {"height": 30})).split())[:150])
    sketcher.execute("catia_create_sketch",
                     {"plane": {"type": "frame", "origin": [0, 0, 0],
                                "u": [1, 0, 0], "v": [0, 0, 1]}, "name": "TopView"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 60, "y2": 25})
    sketcher.execute("catia_close_sketch", {})
    say("        pad top: " + " ".join(str(parts.execute(
        "catia_pad", {"height": 20})).split())[:150])
    try:
        info = json.loads(measurement.execute("catia_get_bounding_box",
                                              {"method": "vertices"}))
        say(f"        whole part box: min {info.get('min')}  max {info.get('max')}"
            f"  dims {info.get('dimensions')}")
        say("        expectation: one solid 60 x 40 x 30 in xy, one 60 x 25 x 20 "
            "in the xz plane - the X range must be 0..60 for both")
        cell("D1 both photos read back", True, str(info.get("dimensions")))
    except Exception as error:  # noqa: BLE001
        cell("D1 both photos read back", False, str(error)[:140])
    try:
        rows = json.loads(parts.execute("catia_list_bodies", {}))
        say(f"        bodies: {[b.get('name') for b in rows.get('bodies', [])]}"
            if isinstance(rows, dict) else f"        bodies: {rows}")
    except Exception as error:  # noqa: BLE001
        say(f"        list_bodies failed: {str(error)[:90]}")

    # ── F/G/H: what a SKETCH ELEMENT can be the target of ───────────────────
    say("\nF. a Spline2D inside a sketch as a GetMinimumDistance target")
    part = fresh(conn, docs, "ProbeSketchSplineTarget")
    spa = conn.active_document.GetWorkbench("SPAWorkbench")
    factory = conn.active_document.Part.HybridShapeFactory
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    # Four corners of a 100 x 40 rectangle as ONE closed 2D spline - exactly what
    # import_drawing(profile_mode='spline') draws - then close the sketch.
    sketcher.execute("catia_sketch_spline",
                     {"points": [[0, 0], [100, 0], [100, 40], [0, 40]],
                      "closed": True})
    sketcher.execute("catia_close_sketch", {})
    sketch = conn.last_sketch
    say(f"        sketch '{sketch.Name}' holds "
        f"{int(sketch.GeometricElements.Count)} element(s)")
    spline2d, line2d = None, None
    for index in range(1, int(sketch.GeometricElements.Count) + 1):
        item = sketch.GeometricElements.Item(index)
        name = str(item.Name)
        if "样条" in name or "Spline" in name:
            spline2d = item
        if "直线" in name or "Line" in name:
            line2d = item
    say(f"        spline element: {getattr(spline2d, 'Name', None)}   "
        f"line element: {getattr(line2d, 'Name', None)}")
    sets = part.HybridBodies
    gsd.execute("catia_gsd_create_geoset", {"name": "ProbeGeoset"})
    holder = sets.Item(int(sets.Count))
    probes = [(50.0, 0.0), (50.0, 20.0), (0.0, 0.0), (100.0, 40.0)]
    if spline2d is not None:
        spline_ref = part.CreateReferenceFromObject(spline2d)
        for x, y in probes:
            try:
                point = factory.AddNewPointCoord(x, y, 0.0)
                holder.AppendHybridShape(point)
                part.UpdateObject(point)
                distance = float(spa.GetMeasurable(
                    part.CreateReferenceFromObject(point)).GetMinimumDistance(spline_ref))
                cell(f"F ({x:g}, {y:g}) -> Spline2D", True, f"{distance:.4f} mm")
                # H: same point, whole sketch, same instant
                sketch_ref = part.CreateReferenceFromObject(sketch)
                other = float(spa.GetMeasurable(
                    part.CreateReferenceFromObject(point)).GetMinimumDistance(sketch_ref))
                cell(f"H ({x:g}, {y:g}) -> whole sketch", True,
                     f"{other:.4f} mm  (spline said {distance:.4f})")
            except Exception as error:  # noqa: BLE001
                cell(f"F ({x:g}, {y:g}) -> Spline2D", False,
                     str(error).splitlines()[0][:110])
    else:
        cell("F find the Spline2D", False, "no spline element in the sketch")

    if line2d is not None:
        try:
            reference = part.CreateReferenceFromObject(line2d)
            point = factory.AddNewPointCoord(50.0, 0.0, 0.0)
            holder.AppendHybridShape(point)
            part.UpdateObject(point)
            distance = float(spa.GetMeasurable(
                part.CreateReferenceFromObject(point)).GetMinimumDistance(reference))
            cell("G (50, 0) -> Line2D", True, f"{distance:.4f} mm (expect 0)")
        except Exception as error:  # noqa: BLE001
            cell("G (50, 0) -> Line2D", False, str(error).splitlines()[0][:110])

    # ── I: does the 2D spline give the SAME number as the 3D one? ───────────
    say("\nI. the same rectangle as a 3D spline, for comparison")
    part = fresh(conn, docs, "Probe3DSplineCompare")
    spa = conn.active_document.GetWorkbench("SPAWorkbench")
    factory = conn.active_document.Part.HybridShapeFactory
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 100, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    gsd.execute("catia_gsd_spline",
                {"element": str(conn.last_sketch.Name), "closed": True,
                 "name": "AsSpline3D"})
    spline3d = None
    sets = part.HybridBodies
    for index in range(1, int(sets.Count) + 1):
        block = sets.Item(index)
        for j in range(1, int(block.HybridShapes.Count) + 1):
            item = block.HybridShapes.Item(j)
            if str(item.Name) == "AsSpline3D":
                spline3d = item
    if spline3d is None:
        cell("I0 find the 3D spline", False, "missing")
    else:
        gsd.execute("catia_gsd_create_geoset", {"name": "ProbeGeoset"})
        holder = sets.Item(int(sets.Count))
        spline_ref = part.CreateReferenceFromObject(spline3d)
        for x, y in ((50.0, 0.0), (50.0, 20.0)):
            point = factory.AddNewPointCoord(x, y, 0.0)
            holder.AppendHybridShape(point)
            part.UpdateObject(point)
            distance = float(spa.GetMeasurable(
                part.CreateReferenceFromObject(point)).GetMinimumDistance(spline_ref))
            cell(f"I1 3D spline ({x:g}, {y:g})", True, f"{distance:.4f} mm")

    say("\n--- summary ---")
    ok = sum(1 for _, good, _ in ROWS if good)
    say(f"{ok}/{len(ROWS)} cells answered")
    for what, good, detail in ROWS:
        if not good:
            say(f"  FAILED {what}: {detail}")
    close_ours(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
