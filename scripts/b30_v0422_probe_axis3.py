"""Round 3: the previous probe measured its own bugs. Fix both, then ask again.

What went wrong in b30_v0422_probe_axis2.py, and why it matters to the answer:

* A failed at AddNewShaftFromRef because the probe skipped ``_prepare_body`` - the
  tool's own first step. The axis question was never asked.
* B looked for a sketch line by the name it passed, which the sketcher does not
  use. Nothing was found, so nothing was tested.
* C DID find something real: HybridShapeSpline exposes ``SetSplineType``,
  ``GetSplineType``, ``GetNbControlPoint`` and ``RemoveControlPoint`` - a
  control-point route that no earlier probe had looked for.
* D settled sheet metal: the factory's only real member is
  CreateSheetMetalParameters(), and that object has GetThickness and nothing else.

So this script asks: can a shaft take an external axis, and what does
SetSplineType do?

    .venv/Scripts/python.exe scripts/b30_v0422_probe_axis3.py
"""

from __future__ import annotations

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
    tempfile.gettempdir(), "catia_probe_v0422")
ROOT = os.path.join(_PROBE_ROOT, "axis3")
PAPPUS = 2 * 3.14159265358979 * 35.0 * 200.0     # r=35 centroid, area 200


def say(line: str = "") -> None:
    print(line, flush=True)


def activate(conn: CATIAConnection, name: str) -> None:
    for index in range(1, conn.documents.Count + 1):
        if conn.documents.Item(index).Name == name:
            conn.documents.Item(index).Activate()
            return
    raise RuntimeError(f"{name} is not open")


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


def fresh(conn, documents, name: str) -> None:
    path = os.path.join(ROOT, f"{name}.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    activate(conn, f"{name}.CATPart")


def volume(conn, part_design):
    part = conn.get_active_part()
    return part_design._body_volume_mm3(part, part.MainBody)


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    gsd = GSDTools(conn)

    # ── A: external line as the revolution axis ──────────────────────────
    say("")
    say("── A: rectangle x 30..40, z 0..20, revolved about a gsd_line ──")
    fresh(conn, documents, "AxA")
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 0], "point2": [0, 0, 50], "name": "SpinAxis"})
    sketcher.execute("catia_create_sketch", {"plane": "zx"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 30, "y1": 0, "x2": 40, "y2": 20})
    sketcher.execute("catia_close_sketch", {})
    part = conn.get_active_part()
    part_design._prepare_body(part)          # the step the last probe skipped
    sketch = part_design._get_last_sketch(None)
    try:
        feature = part.ShapeFactory.AddNewShaftFromRef(
            part.CreateReferenceFromName(""))
        feature.FirstAngle.Value = 360.0
        feature.SetProfileElement(part.CreateReferenceFromObject(sketch))
        feature.RevoluteAxis = gsd._ref("SpinAxis")
        part.UpdateObject(feature)
        got = volume(conn, part_design)
        say(f"  ACCEPTED: '{feature.Name}', volume {got:.1f} mm3, "
            f"Pappus {PAPPUS:.1f}, error {abs(got - PAPPUS):.2f} mm3")
    except Exception as error:  # noqa: BLE001
        say("  REFUSED " + " ".join(str(error).split())[:260])

    # ── B: the sketch's own absolute axis, for the control ───────────────
    say("")
    say("── B: control - the same profile about the sketch's horizontal axis ──")
    fresh(conn, documents, "AxB")
    sketcher.execute("catia_create_sketch", {"plane": "zx"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 30, "y1": 0, "x2": 40, "y2": 20})
    sketcher.execute("catia_close_sketch", {})
    part = conn.get_active_part()
    part_design._prepare_body(part)
    sketch = part_design._get_last_sketch(None)
    try:
        feature = part.ShapeFactory.AddNewShaftFromRef(
            part.CreateReferenceFromName(""))
        feature.FirstAngle.Value = 360.0
        feature.SetProfileElement(part.CreateReferenceFromObject(sketch))
        feature.RevoluteAxis = part_design._revolve_axis_reference(
            part, sketch, "horizontal")
        part.UpdateObject(feature)
        say(f"  ACCEPTED: volume {volume(conn, part_design):.1f} mm3")
    except Exception as error:  # noqa: BLE001
        say("  REFUSED " + " ".join(str(error).split())[:200])

    # ── C: what SetSplineType does ───────────────────────────────────────
    say("")
    say("── C: HybridShapeSpline.SetSplineType(0 / 1 / 2) ──")
    points = [[0, 0, 0], [10, 25, 0], [30, 25, 0], [40, 0, 0]]
    for kind in (None, 0, 1, 2):
        fresh(conn, documents, f"Spl{kind}")
        part = conn.get_active_part()
        part_design._prepare_body(part)
        factory = part.HybridShapeFactory
        geoset = part.HybridBodies.Add()
        geoset.Name = "G"
        spline = factory.AddNewSpline()
        for x, y, z in points:
            spline.AddPoint(factory.AddNewPointCoord(x, y, z))
        geoset.AppendHybridShape(spline)
        label = "as created" if kind is None else f"SetSplineType({kind})"
        try:
            if kind is not None:
                spline.SetSplineType(kind)
            part.UpdateObject(spline)
            spa = conn.active_document.GetWorkbench("SPAWorkbench")
            length = float(spa.GetMeasurable(
                part.CreateReferenceFromObject(spline)).Length)
            controls = spline.GetNbControlPoint()
            say(f"  {label:18} length {length:.4f} mm, "
                f"GetNbControlPoint {controls}, GetSplineType {spline.GetSplineType()}")
        except Exception as error:  # noqa: BLE001
            say(f"  {label:18} REFUSED " + " ".join(str(error).split())[:200])

    # ── D: does the spline type change a PAD? ────────────────────────────
    say("")
    say("── D: pad a closed spline profile with each type, compare volume ──")
    for kind in (None, 0, 1):
        fresh(conn, documents, f"Pad{kind}")
        part = conn.get_active_part()
        part_design._prepare_body(part)
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_spline",
                         {"points": [[0, 0], [10, 22], [30, 22], [40, 0]],
                          "closed": True})
        sketcher.execute("catia_close_sketch", {})
        if kind is not None:
            # the sketch's own spline: find it and change its type
            sketch2d = part_design._get_last_sketch(None)
            try:
                element = sketch2d.GeometricElements.Item(1)
                element.SetSplineType(kind)
                sketch2d.Update()
            except Exception as error:  # noqa: BLE001
                say(f"  type {kind}: SetSplineType on the 2D spline REFUSED "
                    + " ".join(str(error).split())[:160])
                continue
        try:
            part_design.execute("catia_pad", {"height": 10})
            say(f"  type {kind if kind is not None else 'default'}: volume "
                f"{volume(conn, part_design):.2f} mm3")
        except Exception as error:  # noqa: BLE001
            say(f"  type {kind}: pad REFUSED " + " ".join(str(error).split())[:160])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
