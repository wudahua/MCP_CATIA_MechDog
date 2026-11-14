"""Round four: the two questions that matter for the classification.

1  GetPointsOnCurve(5) - ONE integer argument. Round three showed the by-ref
   array shapes are all refused, so the remaining reading is "give me N points".
2  A GSD periodic spline projected into a sketch, then POCKETED. CreateProjection
   returns an element named 标记.1 - if a pocket can consume it, P1-3 has a route
   with no new CATIA capability; if close_sketch refuses it, the gap is real.

    .venv/Scripts/python.exe scripts/b30_v0425_probe_gaps4.py
"""

from __future__ import annotations

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
ROOT = os.path.join(_PROBE_ROOT, "gaps4")

_VBS_INT = r"""
Function catia_mcp_pts_int(oMeas)
    Dim pts, i, out
    On Error Resume Next
    pts = oMeas.GetPointsOnCurve(5)
    If Err.Number <> 0 Then
        catia_mcp_pts_int = Array("ERR: " & Err.Description)
        Err.Clear
        Exit Function
    End If
    On Error GoTo 0
    If IsEmpty(pts) Then
        catia_mcp_pts_int = Array("EMPTY")
        Exit Function
    End If
    ReDim out(UBound(pts))
    For i = 0 To UBound(pts)
        On Error Resume Next
        out(i) = CStr(pts(i).X) & "," & CStr(pts(i).Y) & "," & CStr(pts(i).Z)
        If Err.Number <> 0 Then
            out(i) = "unreadable: " & Err.Description
            Err.Clear
        End If
        On Error GoTo 0
    Next
    catia_mcp_pts_int = out
End Function
"""


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


def build(documents, name: str):
    path = os.path.join(ROOT, f"{name}.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    return path


def activate(conn, path: str) -> None:
    base = os.path.basename(path)
    for index in range(1, int(conn.documents.Count) + 1):
        if conn.documents.Item(index).Name == base:
            conn.documents.Item(index).Activate()
            return


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    gsd = GSDTools(conn)

    # ── 1: GetPointsOnCurve(5) ───────────────────────────────────────────
    say("=" * 74)
    say("1: Measurable.GetPointsOnCurve(5)")
    say("=" * 74)
    path = build(documents, "PtsOne")
    activate(conn, path)
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 40, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_circle", {"cx": 20, "cy": 20, "radius": 5})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pocket", {"through_all": True, "direction": "normal"})
    part = conn.get_active_part()
    from catia_mcp.topo_search import topology_cells

    spa = part.Parent.GetWorkbench("SPAWorkbench")
    edge_ref = None
    for cell in topology_cells(conn, part.MainBody, "Edge"):
        try:
            if abs(float(spa.GetMeasurable(cell.Reference).Length) - 31.4159) < 0.05:
                edge_ref = cell.Reference
                break
        except Exception:  # noqa: BLE001
            continue
    if edge_ref is None:
        say("  no rim edge")
    else:
        measurable = spa.GetMeasurable(edge_ref)
        try:
            raw = conn.app.SystemService.Evaluate(
                _VBS_INT, 0, "catia_mcp_pts_int", [measurable])
            values = [str(v) for v in raw]
            say(f"  {len(values)} entries: {values[:6]}")
        except Exception as error:  # noqa: BLE001
            say("  failed: " + str(error).splitlines()[0][:140])

    # ── 2: GSD periodic spline → projection → pocket ─────────────────────
    say("")
    say("=" * 74)
    say("2: GSD closed spline -> CreateProjection -> pocket")
    say("=" * 74)
    path = build(documents, "ProjRing")
    activate(conn, path)
    part = conn.get_active_part()
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 40, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})

    points = [[round(10 * math.cos(2 * math.pi * i / 12), 4),
               round(10 * math.sin(2 * math.pi * i / 12), 4), 0.0]
              for i in range(12)]
    gsd.execute("catia_gsd_spline",
                {"points": points, "closed": True, "name": "PeriodicRing"})
    periodic = part.FindObjectByName("PeriodicRing")
    say(f"  periodic spline found: {periodic is not None}")

    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketch = part.MainBody.Sketches.Item(int(part.MainBody.Sketches.Count))
    say(f"  sketch {sketch.Name}, elements {int(sketch.GeometricElements.Count)}")
    reference = part.CreateReferenceFromObject(periodic)
    projection = None
    try:
        projection = sketch.Factory2D.CreateProjection(reference)
        say(f"  CreateProjection -> {projection}")
    except Exception as error:  # noqa: BLE001
        say("  CreateProjection refused: " + str(error).splitlines()[0][:120])
    say("  elements now: " + ", ".join(
        f"{sketch.GeometricElements.Item(i).Name}"
        for i in range(1, int(sketch.GeometricElements.Count) + 1)))
    for index in range(1, int(sketch.GeometricElements.Count) + 1):
        element = sketch.GeometricElements.Item(index)
        name = str(element.Name)
        if name.startswith("绝对轴"):
            continue
        say(f"  {name}: type {element.Type if hasattr(element, 'Type') else '?'}, "
            f"Construction {getattr(element, 'Construction', '?')}")

    closed = ""
    try:
        closed = str(sketcher.execute("catia_close_sketch", {}))
    except Exception as error:  # noqa: BLE001
        closed = "CLOSE SKETCH REFUSED: " + " ".join(str(error).split())[:200]
    say("  " + " ".join(closed.split())[:420])

    if "REFUSED" not in closed.upper():
        try:
            pocket = part_design.execute("catia_pocket",
                                         {"through_all": True, "direction": "normal"})
            say("  " + " ".join(str(pocket).split())[:260])
        except Exception as error:  # noqa: BLE001
            say("  pocket refused: " + " ".join(str(error).split())[:220])
        volume = part_design._body_volume_mm3(part, part.MainBody)
        say(f"  volume {volume:.1f} mm3" if volume is not None else "  volume unreadable")
        say("  reference: 16000.0 solid, 12858.4 if a D20 hole went through")

    say("")
    say("done")
    close_ours(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
