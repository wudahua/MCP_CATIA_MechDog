"""Two unknowns behind the 0.3.136 work: can an imported SKETCH be measured,
and is a polyline -> spline conversion measurable at all?

The 2026-11-15 report (R2) asked for `deviation_mm` on the polyline -> spline
conversion. `catia_measure_deviation` samples a CURVE along its length, and an
imported outline is a SKETCH holding 110 curves, so the tool cannot reach it
until these are measured:

    A  what a SKETCH exposes to SPAWorkbench (Length? Area? box?) - the only
       route to "where did this sketch actually land", which a multi-view import
       (C2) has to prove; its 2D coordinates cannot be read back at all
    B  AddNewPointOnCurveFromPercent(<Line2D inside a sketch>, t, 0) + Update
       -> can a sketch's OWN element carry a point-on-curve? Every sample of
       `catia_measure_deviation` needs this.
    C  GetMeasurable(point3D).GetMinimumDistance(<spline>, <sketch>) -> the
       deviation in the direction R2 asks for (polyline -> spline), and whether
       a sketch can be the target.
    D  two sketches on two frames sharing the X axis: does the frame place the
       sketch where the frame says? Read back by padding both and measuring the
       solids, since a sketch's box is not readable (see A).
    E  point-on-curve on a CLOSED spline (the conversion's output)

    .venv/Scripts/python.exe -u scripts/b30_v0437_probe_deviation_views.py
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
                    or tempfile.gettempdir(), "catia_probe_v0437d")
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


def geoset(part, gsd):
    """A geometrical set to hold the probes, created once per document."""
    sets = part.HybridBodies
    for index in range(1, int(sets.Count) + 1):
        if str(sets.Item(index).Name) == "ProbeGeoset":
            return sets.Item(index)
    gsd.execute("catia_gsd_create_geoset", {"name": "ProbeGeoset"})
    for index in range(1, int(sets.Count) + 1):
        if str(sets.Item(index).Name) == "ProbeGeoset":
            return sets.Item(index)
    raise RuntimeError("no ProbeGeoset")


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    parts = PartDesignTools(conn)
    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)
    say(f"MCP probe v0.3.137 on {conn.app.Name}")
    factory = None

    # ── A: what a SKETCH exposes to SPAWorkbench ────────────────────────────
    say("\nA. SPAWorkbench on a SKETCH reference")
    part = fresh(conn, docs, "ProbeSketchBox")
    spa = conn.active_document.GetWorkbench("SPAWorkbench")
    factory = conn.active_document.Part.HybridShapeFactory
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 10, "y1": 20, "x2": 40, "y2": 50})
    sketch = conn.last_sketch
    sketch_ref = part.CreateReferenceFromObject(sketch)
    measurable = spa.GetMeasurable(sketch_ref)
    for member in ("Length", "Area", "Volume", "GetBoundingBox", "GetPoint",
                   "GetCOG", "GetPlane"):
        try:
            value = getattr(measurable, member)
            if callable(value):
                value = value()
            cell(f"A: measurable.{member}", True, str(value)[:90])
        except Exception as error:  # noqa: BLE001
            cell(f"A: measurable.{member}", False,
                 str(error).splitlines()[0][:90])
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 5})
    # D's read-back route: the SOLID of the sketch, whose box IS readable.
    try:
        info = json.loads(parts.execute("catia_get_bounding_box", {"method": "vertices"}))
        say(f"        pad of (10,20)-(40,50) x5 -> vertices box "
            f"{info.get('min')} .. {info.get('max')}")
        cell("A2 the solid of that sketch reports its own box", True,
             f"{info.get('dimensions')}")
    except Exception as error:  # noqa: BLE001
        cell("A2 the solid of that sketch reports its own box", False,
             str(error)[:100])

    # ── B: a point-on-curve on a sketch's OWN line ──────────────────────────
    say("\nB. AddNewPointOnCurveFromPercent on a line INSIDE a sketch")
    part = fresh(conn, docs, "ProbeSketchLineSample")
    spa = conn.active_document.GetWorkbench("SPAWorkbench")
    factory = conn.active_document.Part.HybridShapeFactory
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 100, "y2": 40})
    sketch = conn.last_sketch
    elements = []
    try:
        count = int(sketch.GeometricElements.Count)
        for index in range(1, count + 1):
            item = sketch.GeometricElements.Item(index)
            elements.append((str(item.Name), item))
        say(f"        sketch holds {count} element(s): "
            f"{[name for name, _ in elements]}")
        cell("B0 enumerate GeometricElements", True, f"{count} element(s)")
    except Exception as error:  # noqa: BLE001
        cell("B0 enumerate GeometricElements", False, str(error)[:100])
    sketcher.execute("catia_close_sketch", {})
    holder = geoset(part, gsd)
    lines = [(name, item) for name, item in elements if "直线" in name or "Line" in name]
    for ratio in (0.0, 0.5, 1.0):
        placed = False
        for name, item in lines:
            try:
                reference = part.CreateReferenceFromObject(item)
                point = factory.AddNewPointOnCurveFromPercent(reference, ratio, 0)
                holder.AppendHybridShape(point)
                part.UpdateObject(point)
                coords = spa.GetMeasurable(
                    part.CreateReferenceFromObject(point)).GetPoint()
                cell(f"B t={ratio:.2f} on '{name}'", True,
                     f"({float(coords[0]):.4f}, {float(coords[1]):.4f}, "
                     f"{float(coords[2]):.4f})")
                placed = True
                break
            except Exception as error:  # noqa: BLE001
                cell(f"B t={ratio:.2f} on '{name}'", False,
                     str(error).splitlines()[0][:100])
        if not placed:
            say("        (no line of this sketch carried a point)")

    # ── C: polyline vertex -> converted spline ──────────────────────────────
    say("\nC. deviation of a polyline vertex from the spline through it")
    part = fresh(conn, docs, "ProbeSplineDeviation")
    spa = conn.active_document.GetWorkbench("SPAWorkbench")
    factory = conn.active_document.Part.HybridShapeFactory
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 100, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    outline = conn.last_sketch
    report = gsd.execute("catia_gsd_spline",
                         {"element": str(outline.Name), "closed": True,
                          "name": "AsSpline"})
    say("        gsd_spline: " + " ".join(str(report).split())[:240])
    spline = None
    sets = part.HybridBodies
    for index in range(1, int(sets.Count) + 1):
        block = sets.Item(index)
        for j in range(1, int(block.HybridShapes.Count) + 1):
            item = block.HybridShapes.Item(j)
            if str(item.Name) == "AsSpline":
                spline = item
    if spline is None:
        cell("C0 find the spline", False, "not in the tree")
        spline_ref = None
    else:
        cell("C0 find the spline", True, str(spline.Name))
        spline_ref = part.CreateReferenceFromObject(spline)
        holder = geoset(part, gsd)
        # Corners are ON the spline by construction (interpolation), so 0.000 is
        # the calibration reading; the SIDE MIDPOINTS are where it bulges.
        probes = [(0.0, 0.0), (50.0, 0.0), (100.0, 0.0), (100.0, 20.0),
                  (50.0, 40.0), (50.0, 20.0), (25.0, 0.0), (25.0, 40.0)]
        last_point = None
        for x, y in probes:
            try:
                point = factory.AddNewPointCoord(x, y, 0.0)
                holder.AppendHybridShape(point)
                part.UpdateObject(point)
                last_point = point
                distance = float(spa.GetMeasurable(
                    part.CreateReferenceFromObject(point)).GetMinimumDistance(spline_ref))
                cell(f"C1 ({x:g}, {y:g}) -> spline", True, f"{distance:.4f} mm")
            except Exception as error:  # noqa: BLE001
                cell(f"C1 ({x:g}, {y:g}) -> spline", False,
                     str(error).splitlines()[0][:100])
        if last_point is not None:
            try:
                distance = float(spa.GetMeasurable(
                    part.CreateReferenceFromObject(last_point)).GetMinimumDistance(
                        part.CreateReferenceFromObject(outline)))
                cell("C2 point -> SKETCH as the target", True, f"{distance:.4f} mm")
            except Exception as error:  # noqa: BLE001
                cell("C2 point -> SKETCH as the target", False,
                     str(error).splitlines()[0][:110])

    # ── E: point-on-curve on the CLOSED spline itself ───────────────────────
    say("\nE. point-on-curve on the CLOSED spline (the conversion's output)")
    if spline_ref is None:
        cell("E1 closed spline sample at t=0.25", False, "no spline from C")
    else:
        holder = geoset(part, gsd)
        for ratio in (0.0, 0.25, 0.5, 1.0):
            try:
                point = factory.AddNewPointOnCurveFromPercent(spline_ref, ratio, 0)
                holder.AppendHybridShape(point)
                part.UpdateObject(point)
                coords = spa.GetMeasurable(
                    part.CreateReferenceFromObject(point)).GetPoint()
                cell(f"E1 closed spline at t={ratio:.2f}", True,
                     f"({float(coords[0]):.3f}, {float(coords[1]):.3f}, "
                     f"{float(coords[2]):.3f})")
            except Exception as error:  # noqa: BLE001
                cell(f"E1 closed spline at t={ratio:.2f}", False,
                     str(error).splitlines()[0][:110])

    # ── D: two frames sharing the X axis ────────────────────────────────────
    say("\nD. two frames sharing the X axis - does the frame place the sketch?")
    part = fresh(conn, docs, "ProbeTwoViews")
    sketcher.execute("catia_create_sketch",
                     {"plane": {"type": "frame", "origin": [0, 0, 0],
                                "u": [1, 0, 0], "v": [0, 1, 0]}, "name": "FrontView"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 60, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 30, "name": "FrontSolid"})
    sketcher.execute("catia_create_sketch",
                     {"plane": {"type": "frame", "origin": [0, 0, 0],
                                "u": [1, 0, 0], "v": [0, 0, 1]}, "name": "TopView"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 60, "y2": 25})
    sketcher.execute("catia_close_sketch", {})
    names = [str(part.MainBody.Sketches.Item(i).Name)
             for i in range(1, int(part.MainBody.Sketches.Count) + 1)]
    say(f"        sketches in the body: {names}")
    try:
        info = json.loads(parts.execute("catia_get_bounding_box",
                                        {"method": "vertices", "body": "FrontSolid"}))
        say(f"        FrontSolid box {info.get('min')} .. {info.get('max')}"
            f"  (expect 0,0,0 .. 60,40,30)")
        cell("D1 frame xy placed the front view", True, str(info.get("dimensions")))
    except Exception as error:  # noqa: BLE001
        cell("D1 frame xy placed the front view", False, str(error)[:120])
    parts.execute("catia_pad", {"height": 20, "name": "TopSolid"})
    try:
        info = json.loads(parts.execute("catia_get_bounding_box",
                                        {"method": "vertices", "body": "TopSolid"}))
        say(f"        TopSolid box {info.get('min')} .. {info.get('max')}"
            f"  (expect 0,0,0 .. 60,20,25)")
        cell("D2 frame xz placed the top view", True, str(info.get("dimensions")))
    except Exception as error:  # noqa: BLE001
        cell("D2 frame xz placed the top view", False, str(error)[:120])
    # Both solids in one body would union; the two body boxes above are read
    # separately on purpose. What a multi-view import needs is the shared axis:
    # x 0..60 in both.
    try:
        info = json.loads(parts.execute("catia_get_bounding_box",
                                        {"method": "vertices"}))
        say(f"        whole part box {info.get('min')} .. {info.get('max')}")
    except Exception as error:  # noqa: BLE001
        say(f"        whole part box failed: {str(error)[:110]}")

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
