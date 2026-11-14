"""Round three: three candidates for "this is actually possible already".

1  GetPointsOnCurve - round two used (pts, 5) and CATIA said "wrong number of
   arguments". Try the other shapes before concluding an edge cannot be sampled.
2  Where sketches live. MainBody.Shapes holds only solid features on this
   install, so the Spline2D was never reached; Body.Sketches is the other list.
3  Factory2D.CreateProjection - can a GSD PERIODIC spline become a sketch element?
   If yes, P1-3 ("no periodic 2D sketch spline") has a route that needs no new
   CATIA capability at all.

    .venv/Scripts/python.exe scripts/b30_v0425_probe_gaps3.py
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
    tempfile.gettempdir(), "catia_probe_v0425")
ROOT = os.path.join(_PROBE_ROOT, "gaps3")

_VBS_ONE_ARG = r"""
Function catia_mcp_pts_one(oMeas)
    Dim pts
    On Error Resume Next
    oMeas.GetPointsOnCurve pts
    If Err.Number <> 0 Then
        catia_mcp_pts_one = Array("ERR: " & Err.Description)
        Err.Clear
        Exit Function
    End If
    On Error GoTo 0
    catia_mcp_pts_one = Describe(pts)
End Function

Function catia_mcp_pts_count_first(oMeas)
    Dim pts
    On Error Resume Next
    oMeas.GetPointsOnCurve 5, pts
    If Err.Number <> 0 Then
        catia_mcp_pts_count_first = Array("ERR: " & Err.Description)
        Err.Clear
        Exit Function
    End If
    On Error GoTo 0
    catia_mcp_pts_count_first = Describe(pts)
End Function

Function catia_mcp_pts_three(oMeas)
    Dim pts
    On Error Resume Next
    oMeas.GetPointsOnCurve pts, 5, 1
    If Err.Number <> 0 Then
        catia_mcp_pts_three = Array("ERR: " & Err.Description)
        Err.Clear
        Exit Function
    End If
    On Error GoTo 0
    catia_mcp_pts_three = Describe(pts)
End Function

Function Describe(pts)
    Dim i, out
    On Error Resume Next
    If IsEmpty(pts) Then
        Describe = Array("EMPTY")
        Exit Function
    End If
    ReDim out(UBound(pts))
    For i = 0 To UBound(pts)
        out(i) = CStr(pts(i).X) & "," & CStr(pts(i).Y) & "," & CStr(pts(i).Z)
        If Err.Number <> 0 Then
            out(i) = "item unreadable: " & Err.Description
            Err.Clear
        End If
    Next
    On Error GoTo 0
    Describe = out
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
    gsd = GSDTools(conn)

    path = os.path.join(ROOT, "Gaps3.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": "Gaps3", "file_path": path, "overwrite": True})
    for index in range(1, int(conn.documents.Count) + 1):
        if conn.documents.Item(index).Name == "Gaps3.CATPart":
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

    # ── 1: GetPointsOnCurve signatures ───────────────────────────────────
    say("=" * 74)
    say("1: GetPointsOnCurve - three argument shapes")
    say("=" * 74)
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
        say("  no rim edge found")
    else:
        measurable = spa.GetMeasurable(edge_ref)
        for label, name in (("(pts)", "catia_mcp_pts_one"),
                            ("(5, pts)", "catia_mcp_pts_count_first"),
                            ("(pts, 5, 1)", "catia_mcp_pts_three")):
            try:
                raw = conn.app.SystemService.Evaluate(
                    _VBS_ONE_ARG, 0, name, [measurable])
                say(f"  {label}: {list(raw)[:6]}")
            except Exception as error:  # noqa: BLE001
                say(f"  {label}: Evaluate failed "
                    + str(error).splitlines()[0][:120])

    # ── 2: where the sketches are ────────────────────────────────────────
    say("")
    say("=" * 74)
    say("2: Body.Sketches")
    say("=" * 74)
    body = part.MainBody
    for attr in ("Sketches", "Shapes"):
        try:
            collection = getattr(body, attr)
            count = int(collection.Count)
            names = []
            for index in range(1, count + 1):
                names.append(str(collection.Item(index).Name))
            say(f"  Body.{attr}: {count} -> {names}")
        except Exception as error:  # noqa: BLE001
            say(f"  Body.{attr} -> {str(error).splitlines()[0][:90]}")

    # ── 3: GSD periodic spline → sketch projection → pocket ──────────────
    say("")
    say("=" * 74)
    say("3: a GSD closed spline, projected into a sketch, then pocketed")
    say("=" * 74)
    import math

    points = [[round(10 * math.cos(2 * math.pi * i / 12), 4),
               round(10 * math.sin(2 * math.pi * i / 12), 4), 0.0]
              for i in range(12)]
    try:
        say("  gsd spline: " + " ".join(str(gsd.execute(
            "catia_gsd_spline",
            {"points": points, "closed": True, "name": "PeriodicRing"})).split())[:200])
    except Exception as error:  # noqa: BLE001
        say("  gsd spline refused: " + " ".join(str(error).split())[:200])
    periodic = None
    try:
        periodic = part.FindObjectByName("PeriodicRing")
    except Exception:  # noqa: BLE001
        periodic = None
    say(f"  FindObjectByName('PeriodicRing') -> {periodic}")

    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketch = None
    try:
        sketch = part.MainBody.Sketches.Item(int(part.MainBody.Sketches.Count))
        say(f"  new sketch: {sketch.Name}")
    except Exception as error:  # noqa: BLE001
        say("  new sketch unreadable: " + str(error).splitlines()[0][:90])
    if sketch is not None and periodic is not None:
        reference = part.CreateReferenceFromObject(periodic)
        for label, call in (
            ("CreateProjection(ref)", lambda: sketch.Factory2D.CreateProjection(reference)),
            ("CreateProjection(ref, 1)", lambda: sketch.Factory2D.CreateProjection(reference, 1)),
            ("CreateProjections(ref)", lambda: sketch.Factory2D.CreateProjections(reference)),
        ):
            try:
                made = call()
                say(f"  {label} -> {made}")
                say(f"    elements now {int(sketch.GeometricElements.Count)}")
                break
            except Exception as error:  # noqa: BLE001
                say(f"  {label} -> {str(error).splitlines()[0][:110]}")
        say("  element types: " + ", ".join(
            f"{sketch.GeometricElements.Item(i).Name}"
            for i in range(1, int(sketch.GeometricElements.Count) + 1)))
    say("")
    say("done")
    close_ours(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
