"""Round five: the three things round four left ambiguous.

1  FindObjectByName - round four printed "<COMObject FindObjectByName>", which is
   pywin32 returning the METHOD, not an element. Probe it with a name that cannot
   exist: if that also answers non-None, then any "found it in the tree" check
   built on it is vacuous.
2  The projection redone with gsd._ref(), the lookup the tools themselves use.
   Round four projected something else and the pocket removed 785.4 mm3 (D10)
   instead of 3141.6 (D20).
3  GetPointsOnCurve with a PRE-DIMENSIONED array, which is the one shape not yet
   tried.

    .venv/Scripts/python.exe scripts/b30_v0425_probe_gaps5.py
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
ROOT = os.path.join(_PROBE_ROOT, "gaps5")

_VBS_DIM = r"""
Function catia_mcp_pts_dim(oMeas)
    Dim pts(4), i, out
    On Error Resume Next
    oMeas.GetPointsOnCurve pts
    If Err.Number <> 0 Then
        catia_mcp_pts_dim = Array("ERR: " & Err.Description)
        Err.Clear
        Exit Function
    End If
    On Error GoTo 0
    ReDim out(4)
    For i = 0 To 4
        On Error Resume Next
        out(i) = CStr(pts(i).X) & "," & CStr(pts(i).Y) & "," & CStr(pts(i).Z)
        If Err.Number <> 0 Then
            out(i) = "unreadable: " & Err.Description
            Err.Clear
        End If
        On Error GoTo 0
    Next
    catia_mcp_pts_dim = out
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


def build(documents, name: str) -> str:
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
    say(f"  WARNING: could not activate {base}")


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    gsd = GSDTools(conn)

    path = build(documents, "Proj2")
    activate(conn, path)
    part = conn.get_active_part()
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 40, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})

    # ── 1: is FindObjectByName a lookup or a method wrapper? ─────────────
    say("=" * 74)
    say("1: FindObjectByName, probed with a name that cannot exist")
    say("=" * 74)
    for probe in ("PeriodicRing", "NoSuchElement_zzz"):
        try:
            got = part.FindObjectByName(probe)
            say(f"  {probe!r} -> {got!r} (type {type(got).__name__})")
            say(f"     is None: {got is None}")
        except Exception as error:  # noqa: BLE001
            say(f"  {probe!r} -> raised " + str(error).splitlines()[0][:100])

    # ── 2: projection with the tools' own lookup ────────────────────────
    say("")
    say("=" * 74)
    say("2: gsd._ref('PeriodicRing') -> CreateProjection -> pocket")
    say("=" * 74)
    points = [[round(10 * math.cos(2 * math.pi * i / 12), 4),
               round(10 * math.sin(2 * math.pi * i / 12), 4), 0.0]
              for i in range(12)]
    say("  " + " ".join(str(gsd.execute(
        "catia_gsd_spline",
        {"points": points, "closed": True, "name": "PeriodicRing"})).split())[:180])
    reference = None
    try:
        reference = gsd._ref("PeriodicRing")
        say(f"  _ref -> {reference!r}")
    except Exception as error:  # noqa: BLE001
        say("  _ref refused: " + " ".join(str(error).split())[:160])

    if reference is not None:
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketch = part.MainBody.Sketches.Item(int(part.MainBody.Sketches.Count))
        say(f"  sketch {sketch.Name}, elements {int(sketch.GeometricElements.Count)}")
        try:
            projection = sketch.Factory2D.CreateProjection(reference)
            say(f"  CreateProjection -> {projection!r}")
        except Exception as error:  # noqa: BLE001
            say("  CreateProjection refused: " + str(error).splitlines()[0][:140])
        say("  elements: " + ", ".join(
            str(sketch.GeometricElements.Item(i).Name)
            for i in range(1, int(sketch.GeometricElements.Count) + 1)))
        try:
            say("  " + " ".join(str(sketcher.execute(
                "catia_close_sketch", {})).split())[:300])
        except Exception as error:  # noqa: BLE001
            say("  close refused: " + " ".join(str(error).split())[:200])
        try:
            say("  " + " ".join(str(part_design.execute(
                "catia_pocket", {"through_all": True,
                                 "direction": "normal"})).split())[:300])
        except Exception as error:  # noqa: BLE001
            say("  pocket refused: " + " ".join(str(error).split())[:240])
        volume = part_design._body_volume_mm3(part, part.MainBody)
        say(f"  volume {volume:.1f} mm3" if volume is not None else "  unreadable")
        say("  reference: 16000.0 solid; 12858.4 if the D20 ring went through")

    # ── 3: GetPointsOnCurve with a dimensioned array ─────────────────────
    say("")
    say("=" * 74)
    say("3: GetPointsOnCurve with Dim pts(4)")
    say("=" * 74)
    path = build(documents, "PtsDim")
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
                _VBS_DIM, 0, "catia_mcp_pts_dim", [measurable])
            say(f"  {list(raw)}")
        except Exception as error:  # noqa: BLE001
            say("  failed: " + str(error).splitlines()[0][:140])

    say("")
    say("done")
    close_ours(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
