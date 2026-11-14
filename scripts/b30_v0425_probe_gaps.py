"""The five leftover items of the 2026-11-13 report: which are impossible, which are unnecessary.

Nothing here changes a model except one throwaway document, and that one is built to
answer two questions that decide the classification:

A  a through hole cut ONE-SIDED (through_all, direction=normal): does CATIA report
   its two rims as TWO edges? If it does, "different radius front and back" needs no
   new tool - it needs two one-sided cuts instead of direction='both'.
B  can a topological EDGE be sampled with AddNewPointOnCurveFromPercent? That is the
   whole of the P1-5 request (list_edges(coords=true, samples=N)).

C/D/E are pure member enumerations, no model needed:
C  Measurable's members - is there any parameter-domain or bounding box to read?
D  Viewer3D / Viewpoint3D members - any zoom, capture-region or crop switch?
E  Sketch / Spline2D members - is IsPeriodic really read-only, and is there a way to
   put a 3D curve into a sketch?

    .venv/Scripts/python.exe scripts/b30_v0425_probe_gaps.py
"""

from __future__ import annotations

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.topo_search import topology_cells  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0425")
ROOT = os.path.join(_PROBE_ROOT, "gaps")


def say(line: str = "") -> None:
    print(line, flush=True)


def members(obj: object) -> list[str]:
    """Every name an object exposes, via its type info - not via dir()."""
    try:
        info = obj._oleobj_.GetTypeInfo()
        count = info.GetTypeAttr().cFuncs
    except Exception:  # noqa: BLE001
        return []
    names: list[str] = []
    for index in range(count):
        try:
            names.append(str(info.GetNames(info.GetFuncDesc(index).memid)[0]))
        except Exception:  # noqa: BLE001
            continue
    return names


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


def fresh(conn, documents, name):
    path = os.path.join(ROOT, f"{name}.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    for index in range(1, int(conn.documents.Count) + 1):
        if conn.documents.Item(index).Name == f"{name}.CATPart":
            conn.documents.Item(index).Activate()
    return conn.get_active_part()


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    # Idempotent: a previous run that crashed mid-document leaves it open, and
    # catia_new_part then refuses a second document with the same name.
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    gsd = GSDTools(conn)

    # ── A: a through hole cut ONE-SIDED - one edge per rim, or one? ───────
    say("=" * 74)
    say("A: through_all + direction=normal - how many rims does CATIA report?")
    say("=" * 74)
    part = fresh(conn, documents, "OneSided")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 40, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_circle", {"cx": 20, "cy": 20, "radius": 5})
    sketcher.execute("catia_close_sketch", {})
    say("  " + " ".join(str(part_design.execute(
        "catia_pocket", {"through_all": True, "direction": "normal"})).split())[:220])
    shapes = part.MainBody.Shapes
    say("  features: " + ", ".join(str(shapes.Item(i).Name)
                                   for i in range(1, int(shapes.Count) + 1)))
    import json
    listing = json.loads(part_design.execute(
        "catia_list_edges", {"min_length": 30.0, "max_length": 32.5,
                             "coords": True}))
    rims = listing.get("edges", [])
    say(f"  edges of length 2*pi*5=31.4159: {len(rims)}")
    for edge in rims:
        point = edge.get("point_on_edge") or []
        say(f"    {edge['name']}  len {edge.get('length') or 0:.4f}  "
            f"point {[round(v, 3) for v in point]}  "
            f"bounded_by {edge.get('bounded_by')}"
            + ("  INCOMPLETE" if edge.get("bounded_by_incomplete") else ""))
    # The two rims sit at z=0 and z=10. Two edges means "different radii" is a
    # modelling choice, not a missing tool.
    zs = sorted({round(float((edge.get("point_on_edge") or [0, 0, -1])[2]), 2)
                 for edge in rims})
    say(f"  distinct z of those rims: {zs} -> "
        + ("TWO separate edges" if len(zs) >= 2 else "ONE fused edge"))

    # ── B: can a topological edge be sampled? ────────────────────────────
    say("")
    say("=" * 74)
    say("B: AddNewPointOnCurveFromPercent on a topological EDGE")
    say("=" * 74)
    cells = topology_cells(conn, part.MainBody, "Edge")
    target = None
    for cell in cells:
        try:
            edge_obj = cell.Reference
        except Exception:  # noqa: BLE001
            continue
        try:
            length = float(conn.get_active_part().Parent
                           .GetWorkbench("SPAWorkbench")
                           .GetMeasurable(edge_obj).Length)
        except Exception:  # noqa: BLE001
            continue
        if abs(length - 2 * math.pi * 5) < 0.05:
            target = edge_obj
            say(f"  picked an edge of length {length:.4f}")
            break
    if target is None:
        say("  no circle-rim edge found - cannot test sampling")
    else:
        factory = part.HybridShapeFactory
        gs = gsd._get_geoset()
        spa = part.Parent.GetWorkbench("SPAWorkbench")
        body_ref = part.CreateReferenceFromObject(part.MainBody)
        made = []
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
            for order in ("percent,orient", "orient,percent"):
                try:
                    if order == "percent,orient":
                        point = factory.AddNewPointOnCurveFromPercent(
                            target, fraction, False)
                    else:
                        point = factory.AddNewPointOnCurveFromPercent(
                            target, False, fraction)
                except Exception as error:  # noqa: BLE001
                    last = str(error).splitlines()[0][:60]
                    continue
                gs.AppendHybridShape(point)
                try:
                    part.UpdateObject(point)
                except Exception as error:  # noqa: BLE001 - the answer IS the failure
                    say(f"  t={fraction} ({order}): created but UpdateObject -> "
                        + str(error).splitlines()[0][:90])
                    try:
                        gs.Remove(point)
                    except Exception:  # noqa: BLE001
                        pass
                    made.append((fraction, None, order + "/UPDATE_FAILED"))
                    break
                made.append((fraction, point, order))
                break
            else:
                say(f"  t={fraction}: REFUSED {last}")
        say(f"  points created: {len(made)}")
        for fraction, point, order in made:
            if point is None:
                say(f"    t={fraction:<5} ({order}) - no point to measure")
                continue
            ref = part.CreateReferenceFromObject(point)
            try:
                measurable = spa.GetMeasurable(ref)
                distance = float(measurable.GetMinimumDistance(body_ref))
                say(f"    t={fraction:<5} ({order}) distance to solid "
                    f"{distance:.6f} mm")
            except Exception as error:  # noqa: BLE001
                say(f"    t={fraction:<5} distance unreadable: "
                    + str(error).splitlines()[0][:60])
        for _, point, _ in made:
            if point is None:
                continue
            try:
                gs.Remove(point)
            except Exception:  # noqa: BLE001
                pass

    # ── C: Measurable members - any parameter domain or box? ─────────────
    say("")
    say("=" * 74)
    say("C: Measurable members")
    say("=" * 74)
    spa = part.Parent.GetWorkbench("SPAWorkbench")
    measurable = spa.GetMeasurable(part.CreateReferenceFromObject(part.MainBody))
    names = members(measurable)
    say(f"  {len(names)} members")
    say("  all: " + ", ".join(names))
    keys = ("param", "bound", "box", "extent", "domain", "min", "max",
            "point", "edge", "curve", "length", "angle", "radius")
    say("  matching " + str(keys) + ": "
        + ", ".join(n for n in names if any(k in n.lower() for k in keys)))

    # ── D: viewer switches - zoom / capture region? ──────────────────────
    say("")
    say("=" * 74)
    say("D: Viewer3D / Viewpoint3D members")
    say("=" * 74)
    viewer = conn.active_window.ActiveViewer
    for label, obj in (("Viewer3D", viewer), ("Viewpoint3D", viewer.Viewpoint3D)):
        got = members(obj)
        say(f"  {label}: {len(got)} members")
        keys = ("zoom", "capture", "crop", "region", "window", "pan", "scale",
                "size", "width", "height", "pixel", "image")
        say("    matching: " + ", ".join(n for n in got
                                        if any(k in n.lower() for k in keys)))

    # ── E: the sketch side - periodic, and 3D-into-sketch ────────────────
    say("")
    say("=" * 74)
    say("E: Sketch / Spline2D members")
    say("=" * 74)
    sketch = None
    for index in range(1, int(part.MainBody.Shapes.Count) + 1):
        shape = part.MainBody.Shapes.Item(index)
        if str(shape.Name).startswith(("Sketch", "草图")):
            try:
                sketch = shape.Sketch
                break
            except Exception:  # noqa: BLE001
                continue
    if sketch is None:
        say("  no sketch found")
    else:
        got = members(sketch)
        say(f"  Sketch: {len(got)} members")
        keys = ("project", "3d", "period", "close", "constraint", "element",
                "import", "convert", "isoparam")
        say("    matching: " + ", ".join(n for n in got
                                        if any(k in n.lower() for k in keys)))
        spline = None
        for index in range(1, int(sketch.GeometricElements.Count) + 1):
            element = sketch.GeometricElements.Item(index)
            if "Spline" in str(element.Name) or "样条" in str(element.Name):
                spline = element
                break
        if spline is None:
            say("  no spline in this sketch")
        else:
            say(f"  {spline.Name}: {members(spline)}")
            for attr in ("IsPeriodic", "GetPeriodicity", "SetPeriodicity"):
                try:
                    say(f"    {attr} -> {getattr(spline, attr)}")
                except Exception as error:  # noqa: BLE001
                    say(f"    {attr} -> {str(error).splitlines()[0][:70]}")
            try:
                spline.IsPeriodic = True
                say("    IsPeriodic = True ACCEPTED (it is writable)")
            except Exception as error:  # noqa: BLE001
                say("    IsPeriodic = True refused: "
                    + str(error).splitlines()[0][:90])

    say("")
    say("done")
    close_ours(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
