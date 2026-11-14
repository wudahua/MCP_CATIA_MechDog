"""Round two: the three questions round one opened instead of closing.

B2  Measurable.GetPointsOnCurve - can it sample a topological EDGE? Round one
    proved AddNewPointOnCurveFromPercent cannot (5/5 UpdateObject failures), but
    Measurable lists GetPointsOnCurve, so the P1-5 request may already be possible.
D2  Viewpoint3D.Zoom - readable? writable? If it is, "crop the screenshot" has a
    cheaper answer: make the model fill the window.
E   Sketch geometry - round one found no sketch under MainBody.Shapes, so the
    member lists were never printed; and IsPeriodic's writability is the whole
    basis of the "periodic 2D spline is impossible" claim.

    .venv/Scripts/python.exe scripts/b30_v0425_probe_gaps2.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0425")
ROOT = os.path.join(_PROBE_ROOT, "gaps2")

# A parametric 2D spline, then the same spline closed - the exact profile the
# P1-3 report needed.
_VBS_POINTS = r"""
Function catia_mcp_probe_pts(oMeas)
    Dim pts, i, out
    On Error Resume Next
    oMeas.GetPointsOnCurve pts, 5
    If Err.Number <> 0 Then
        catia_mcp_probe_pts = Array("ERR-A: " & Err.Description)
        Err.Clear
        Exit Function
    End If
    On Error GoTo 0
    If IsEmpty(pts) Then
        catia_mcp_probe_pts = Array("EMPTY")
        Exit Function
    End If
    ReDim out(UBound(pts))
    For i = 0 To UBound(pts)
        On Error Resume Next
        out(i) = CStr(pts(i).X) & "," & CStr(pts(i).Y) & "," & CStr(pts(i).Z)
        If Err.Number <> 0 Then
            out(i) = "item " & i & " unreadable: " & Err.Description
            Err.Clear
        End If
        On Error GoTo 0
    Next
    catia_mcp_probe_pts = out
End Function
"""


def say(line: str = "") -> None:
    print(line, flush=True)


def members(obj: object) -> list[str]:
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


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)

    path = os.path.join(ROOT, "Gaps2.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": "Gaps2", "file_path": path, "overwrite": True})
    for index in range(1, int(conn.documents.Count) + 1):
        if conn.documents.Item(index).Name == "Gaps2.CATPart":
            conn.documents.Item(index).Activate()
    part = conn.get_active_part()

    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 40, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_circle", {"cx": 20, "cy": 20, "radius": 5})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pocket", {"through_all": True,
                                         "direction": "normal"})

    # ── B2: GetPointsOnCurve on the hole rim ─────────────────────────────
    say("=" * 74)
    say("B2: Measurable.GetPointsOnCurve(5) on a topological edge")
    say("=" * 74)
    from catia_mcp.topo_search import topology_cells

    spa = part.Parent.GetWorkbench("SPAWorkbench")
    edge_ref = None
    for cell in topology_cells(conn, part.MainBody, "Edge"):
        try:
            measurable = spa.GetMeasurable(cell.Reference)
            if abs(float(measurable.Length) - 31.4159) < 0.05:
                edge_ref = cell.Reference
                say(f"  edge {cell.Name if hasattr(cell, 'Name') else ''} "
                    f"length {float(measurable.Length):.4f}")
                break
        except Exception:  # noqa: BLE001
            continue
    if edge_ref is None:
        say("  no rim edge found")
    else:
        measurable = spa.GetMeasurable(edge_ref)
        say("  members mentioning point: "
            + ", ".join(n for n in members(measurable) if "oint" in n))
        try:
            raw = conn.app.SystemService.Evaluate(
                _VBS_POINTS, 0, "catia_mcp_probe_pts", [measurable])
            say(f"  raw type {type(raw)}, value {raw}")
            values = [str(v) for v in raw] if raw is not None else []
            say(f"  {len(values)} entries")
            for value in values:
                say(f"    {value}")
        except Exception as error:  # noqa: BLE001
            say("  Evaluate failed: " + str(error).splitlines()[0][:160])

    # ── D2: Zoom ─────────────────────────────────────────────────────────
    say("")
    say("=" * 74)
    say("D2: Viewpoint3D.Zoom and Viewer3D.ZoomIn")
    say("=" * 74)
    viewpoint = conn.active_window.ActiveViewer.Viewpoint3D
    try:
        before = float(viewpoint.Zoom)
        say(f"  Zoom reads {before}")
    except Exception as error:  # noqa: BLE001
        say("  Zoom unreadable: " + str(error).splitlines()[0][:90])
        before = None
    if before is not None:
        for target in (before * 2.0, before):
            try:
                viewpoint.Zoom = target
                say(f"  Zoom := {target:.4f} -> reads {float(viewpoint.Zoom):.4f}")
            except Exception as error:  # noqa: BLE001
                say(f"  Zoom := {target:.4f} refused: "
                    + str(error).splitlines()[0][:90])

    # ── E: the sketch side ───────────────────────────────────────────────
    say("")
    say("=" * 74)
    say("E: where the sketches are, and what a 2D spline allows")
    say("=" * 74)
    shapes = part.MainBody.Shapes
    say(f"  MainBody.Shapes: {int(shapes.Count)}")
    sketch_shape = None
    for index in range(1, int(shapes.Count) + 1):
        shape = shapes.Item(index)
        say(f"    {index}: {shape.Name} (Type {getattr(shape, 'Type', '?')})")
        try:
            candidate = shape.Sketch
            sketch_shape = candidate
        except Exception:  # noqa: BLE001
            pass
    if sketch_shape is None:
        say("  no .Sketch on any shape - trying the pad's profile reference")
        pad = shapes.Item(1)
        try:
            reference = pad.Profile
            say(f"  pad.Profile -> {reference}")
        except Exception as error:  # noqa: BLE001
            say("  pad.Profile unreadable: " + str(error).splitlines()[0][:90])

    if sketch_shape is None:
        say("  SKETCH NOT REACHABLE from this route; IsPeriodic not tested here")
    else:
        say(f"  sketch: {sketch_shape.Name}, "
            f"{len(members(sketch_shape))} members")
        say("  Sketch members: " + ", ".join(members(sketch_shape)))
        try:
            say("  Factory2D: " + ", ".join(members(sketch_shape.Factory2D)))
        except Exception as error:  # noqa: BLE001
            say("  Factory2D unreadable: " + str(error).splitlines()[0][:80])
        # A closed 2D spline, exactly what P1-3 asked for a periodic version of.
        sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 0})
        sketcher.execute("catia_sketch_spline", {
            "points": [[0, 0], [10, 6], [20, -4], [30, 5], [34, 0]],
            "closed": True, "name": "PeriodicProbe"})
        sketcher.execute("catia_close_sketch", {})
        sketch_shape = None
        for index in range(1, int(shapes.Count) + 1):
            try:
                sketch_shape = shapes.Item(index).Sketch
            except Exception:  # noqa: BLE001
                continue
        if sketch_shape is not None:
            say(f"  second sketch: {sketch_shape.Name}, "
                f"{int(sketch_shape.GeometricElements.Count)} element(s)")
        spline = None
        elements = sketch_shape.GeometricElements
        for index in range(1, int(elements.Count) + 1):
            element = elements.Item(index)
            name = str(element.Name)
            if "Spline" in name or "样条" in name:
                spline = element
                break
        if spline is None:
            # draw one at 3D level? fall back to the 2D factory
            say("  no spline in this sketch - listing element types")
            for index in range(1, min(6, int(elements.Count) + 1)):
                element = elements.Item(index)
                say(f"    {element.Name}: {type(element).__name__}")
        else:
            say(f"  {spline.Name}: {len(members(spline))} members")
            say("  " + ", ".join(members(spline)))
            for attr in ("IsPeriodic", "SetClosing", "GetClosing"):
                try:
                    say(f"    {attr} -> {getattr(spline, attr)}")
                except Exception as error:  # noqa: BLE001
                    say(f"    {attr} -> {str(error).splitlines()[0][:80]}")
            try:
                spline.IsPeriodic = True
                say("    IsPeriodic := True ACCEPTED")
            except Exception as error:  # noqa: BLE001
                say("    IsPeriodic := True refused: "
                    + str(error).splitlines()[0][:100])

    say("")
    say("done")
    close_ours(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
