"""Two open questions from the 0.3.120 acceptance reports, asked of CATIA itself.

A  What Viewpoint3D exposes, and what the view direction READS BACK as after
   PutSightDirection / PutUpDirection - the reports say set_view('front') is
   tilted 15-20 deg with the bottom of the bottle visible. A read-back tells
   us whether the put was ignored (like AddNewPointOnSurfaceWithReference's
   direction) or whether the projection mode is the culprit.
B  Whether a FACE answers geometry questions of its own: GetCOG, GetPlane,
   GetBoundingBox, an Inertia on the face reference. list_faces has only area
   because GetCOG on a hybrid reference measures the enclosing SOLID (measured
   2026-11-10); a topological face may behave differently and that has never
   been asked.

    .venv/Scripts/python.exe scripts/b30_v0423_probe_view_faces.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402
from catia_mcp.tools.measurement import read_measurable_array  # noqa: E402
from catia_mcp.topo_search import topology_cells  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0423")
ROOT = os.path.join(_PROBE_ROOT, "view_faces")


def say(line: str = "") -> None:
    print(line, flush=True)


def members(obj) -> list[str]:
    try:
        info = obj._oleobj_.GetTypeInfo()
        count = info.GetTypeAttr().cFuncs
    except Exception as error:  # noqa: BLE001
        say("  typelib unreadable: " + str(error).splitlines()[0][:80])
        return []
    rows = []
    for index in range(count):
        try:
            desc = info.GetFuncDesc(index)
            names = info.GetNames(desc.memid)
        except Exception:  # noqa: BLE001
            continue
        rows.append(names[0])
    return rows


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)

    # ── A: viewpoint ─────────────────────────────────────────────────────
    say("")
    say("── A: Viewpoint3D ──")
    viewer = conn.active_window.ActiveViewer
    viewpoint = viewer.Viewpoint3D
    names = members(viewpoint)
    say(f"  {len(names)} member(s): {names}")

    _VBS = (
        "Public Function catia_mcp_dirs(oVP)\n"
        "    Dim s(2), u(2)\n"
        "    oVP.GetSightDirection s\n"
        "    oVP.GetUpDirection u\n"
        "    Dim a(5)\n"
        "    a(0)=s(0) : a(1)=s(1) : a(2)=s(2)\n"
        "    a(3)=u(0) : a(4)=u(1) : a(5)=u(2)\n"
        "    catia_mcp_dirs = a\n"
        "End Function\n"
    )

    def dirs(tag: str) -> None:
        try:
            raw = conn.app.SystemService.Evaluate(_VBS, 0, "catia_mcp_dirs",
                                                  [viewpoint])
            say(f"  {tag} sight={[round(float(v), 4) for v in raw[:3]]} "
                f"up={[round(float(v), 4) for v in raw[3:6]]}")
        except Exception as error:  # noqa: BLE001
            say(f"  {tag} GetSightDirection -> {str(error).splitlines()[0][:90]}")

    def read_pair(tag: str) -> None:
        dirs(tag)
        for prop in ("ProjectionMode", "FieldOfView"):
            try:
                say(f"  {tag} {prop} = {getattr(viewpoint, prop)}")
            except Exception as error:  # noqa: BLE001
                say(f"  {tag} {prop} -> {str(error).splitlines()[0][:70]}")

    read_pair("before")
    try:
        viewpoint.PutSightDirection([0, 1, 0])
        viewpoint.PutUpDirection([0, 0, 1])
        say("  put sight (0,1,0) up (0,0,1)")
    except Exception as error:  # noqa: BLE001
        say("  PUT REFUSED " + " ".join(str(error).split())[:160])
    read_pair("after")
    try:
        say(f"  ProjectionMode settable? {viewpoint.ProjectionMode}")
    except Exception as error:  # noqa: BLE001
        say("  ProjectionMode read failed " + str(error).splitlines()[0][:80])
    for value in (1, 2):
        try:
            viewpoint.ProjectionMode = value
            say(f"  ProjectionMode set to {value} -> reads {viewpoint.ProjectionMode}")
        except Exception as error:  # noqa: BLE001
            say(f"  ProjectionMode={value} refused: {str(error).splitlines()[0][:90]}")
    try:
        say(f"  viewer count/name: {viewer.Name}")
    except Exception:  # noqa: BLE001
        pass
    try:
        say(f"  active_window.Caption: {conn.active_window.Caption}")
    except Exception as error:  # noqa: BLE001
        say("  caption unreadable " + str(error).splitlines()[0][:60])

    # ── A2: does parallel projection change the picture? ─────────────────
    say("")
    say("── A2: one plate + tower, front view, projection 0 vs 1 ──")
    view_path = os.path.join(ROOT, "ViewTest.CATPart")
    if os.path.exists(view_path):
        os.remove(view_path)
    documents.execute("catia_new_part",
                      {"name": "ViewTest", "file_path": view_path, "overwrite": True})
    for index in range(1, conn.documents.Count + 1):
        if conn.documents.Item(index).Name == "ViewTest.CATPart":
            conn.documents.Item(index).Activate()
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 60, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 4})
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 50, "y1": 10, "x2": 58, "y2": 18})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 26})
    viewpoint = viewer.Viewpoint3D
    for mode, tag in ((0, "persp"), (1, "parallel")):
        try:
            viewpoint.ProjectionMode = mode
        except Exception as error:  # noqa: BLE001
            say(f"  ProjectionMode={mode} refused " + str(error).splitlines()[0][:80])
        viewpoint.PutSightDirection([0, 1, 0])
        viewpoint.PutUpDirection([0, 0, 1])
        viewer.Reframe()
        dirs(f"mode={viewpoint.ProjectionMode} {tag}")
        shot = os.path.join(ROOT, f"front_{tag}.jpg")
        try:
            viewer.CaptureToFile(5, os.path.normpath(shot))
            say(f"  wrote {shot}")
        except Exception as error:  # noqa: BLE001
            say("  capture failed " + str(error).splitlines()[0][:90])

    # ── B: per-face geometry ─────────────────────────────────────────────
    say("")
    say("── B: what a FACE answers (60 x 40 x 40 block) ──")
    path = os.path.join(ROOT, "Faces.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": "Faces", "file_path": path, "overwrite": True})
    for index in range(1, conn.documents.Count + 1):
        if conn.documents.Item(index).Name == "Faces.CATPart":
            conn.documents.Item(index).Activate()
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 60, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 40})
    part = conn.get_active_part()
    body = part.MainBody
    spa = part.Parent.GetWorkbench("SPAWorkbench")
    body_ref = part.CreateReferenceFromObject(body)
    say("  body COG = " + str(read_measurable_array(
        conn.app, spa.GetMeasurable(body_ref), "GetCOG", 3)))
    try:
        bb = read_measurable_array(conn.app, spa.GetMeasurable(body_ref),
                                   "GetBoundingBox", 6)
        say("  body GetBoundingBox = " + str(bb))
    except Exception as error:  # noqa: BLE001
        say("  body GetBoundingBox -> " + str(error).splitlines()[0][:90])

    cells = topology_cells(conn, body, "Face")
    say(f"  {len(cells)} face(s)")
    for i, cell in enumerate(cells, start=1):
        measurable = spa.GetMeasurable(cell.Reference)
        area = None
        try:
            area = float(measurable.Area) * 1e6
        except Exception as error:  # noqa: BLE001
            area = "ERR " + str(error).splitlines()[0][:40]
        cog = "n/a"
        try:
            cog = read_measurable_array(conn.app, measurable, "GetCOG", 3)
        except Exception as error:  # noqa: BLE001
            cog = "ERR " + str(error).splitlines()[0][:60]
        plane = "n/a"
        try:
            plane = read_measurable_array(conn.app, measurable, "GetPlane", 9)
        except Exception as error:  # noqa: BLE001
            plane = "ERR " + str(error).splitlines()[0][:60]
        bbox = "n/a"
        try:
            bbox = read_measurable_array(conn.app, measurable, "GetBoundingBox", 6)
        except Exception as error:  # noqa: BLE001
            bbox = "ERR " + str(error).splitlines()[0][:60]
        inertia = "n/a"
        try:
            obj = spa.Inertias.Add(cell.Reference)
            inertia = read_measurable_array(conn.app, obj, "GetBoundingBox", 6)
        except Exception as error:  # noqa: BLE001
            inertia = "ERR " + str(error).splitlines()[0][:60]
        say(f"  Face.{i} area={area} cog={cog}")
        say(f"          plane={plane}")
        say(f"          bbox={bbox} inertia.bbox={inertia}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
