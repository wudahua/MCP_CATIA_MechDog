"""What can be READ from the elements a variable sweep already builds?

An elliptic station needs three numbers CATIA has not been asked for yet: the
centre of the point on the spine, the spine's tangent there, and the two
in-plane directions the ellipse's semi-axes lie along. Two candidate sources,
measured here one per fresh document:

  * GetMeasurable(point-on-curve).GetPoint()  - the centre
  * GetMeasurable(normal plane).GetPlane()    - origin + two axes + normal

If GetPlane answers for a DATUM plane, the frame comes free with the plane the
sweep already builds, and no temporary points are needed. If it does not, the
fallback is a numerical tangent from two extra point-on-curve elements, which is
more tree traffic and has to be written deliberately.

    .venv/Scripts/python.exe scripts/b30_v0428_probe_plane_frame.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.measurement import read_measurable_array  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0428")
ROOT = os.path.join(_PROBE_ROOT, "frame")


def say(line: str = "") -> None:
    print(line, flush=True)


def fresh(conn: CATIAConnection, documents: DocumentTools, name: str):
    path = os.path.join(ROOT, name + ".CATPart")
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
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    for index in range(1, int(conn.documents.Count) + 1):
        if conn.documents.Item(index).Name == name + ".CATPart":
            conn.documents.Item(index).Activate()
    return conn.get_active_part()


def show(label: str, value) -> None:
    say(f"  {label}: {value}")


def read_plane(app, spa, part, element) -> str:
    try:
        measurable = spa.GetMeasurable(part.CreateReferenceFromObject(element))
    except Exception as error:  # noqa: BLE001
        return f"GetMeasurable failed: {str(error).splitlines()[0][:120]}"
    try:
        plane = read_measurable_array(app, measurable, "GetPlane", 9)
    except Exception as error:  # noqa: BLE001
        return f"GetPlane failed: {str(error).splitlines()[0][:150]}"
    return (f"origin=({plane[0]:.4f},{plane[1]:.4f},{plane[2]:.4f}) "
            f"u=({plane[3]:.4f},{plane[4]:.4f},{plane[5]:.4f}) "
            f"v=({plane[6]:.4f},{plane[7]:.4f},{plane[8]:.4f})")


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    documents = DocumentTools(conn)
    gsd = GSDTools(conn)

    part = fresh(conn, documents, "ProbeFrame")
    spa = conn.active_document.GetWorkbench("SPAWorkbench")
    factory = part.HybridShapeFactory
    geoset = part.HybridBodies.Add()
    try:
        geoset.Name = "Geometry"
    except Exception:  # noqa: BLE001
        pass

    # A spine that is deliberately NOT along an axis: a straight line tilted in
    # space. A plane normal to it therefore has a frame CATIA had to choose, and
    # that choice is exactly the unknown.
    p0 = factory.AddNewPointCoord(0.0, 0.0, 0.0)
    p0.Name = "P0"
    p1 = factory.AddNewPointCoord(30.0, 10.0, 40.0)
    p1.Name = "P1"
    for point in (p0, p1):
        geoset.AppendHybridShape(point)
        part.UpdateObject(point)
    spine = factory.AddNewLinePtPt(part.CreateReferenceFromObject(p0),
                                   part.CreateReferenceFromObject(p1))
    spine.Name = "Spine"
    geoset.AppendHybridShape(spine)
    part.UpdateObject(spine)
    say(f"spine '{spine.Name}' built 0,0,0 -> 30,10,40")

    for t in (0.0, 0.25, 0.5, 1.0):
        try:
            point = factory.AddNewPointOnCurveFromPercent(
                part.CreateReferenceFromObject(spine), t, 0)
            point.Name = f"Pt{t:g}"
            geoset.AppendHybridShape(point)
            part.UpdateObject(point)
        except Exception as error:  # noqa: BLE001
            say(f"  t={t:g}: point FAILED {str(error).splitlines()[0][:120]}")
            continue
        say(f"  t={t:g}:")
        try:
            centre = read_measurable_array(
                conn.app, spa.GetMeasurable(
                    part.CreateReferenceFromObject(point)), "GetPoint", 3)
            show("GetPoint()", "(%.4f, %.4f, %.4f)" % tuple(centre))
        except Exception as error:  # noqa: BLE001
            show("GetPoint() FAILED", f"{str(error).splitlines()[0][:140]}")

        plane = factory.AddNewPlaneNormal(
            part.CreateReferenceFromObject(spine),
            part.CreateReferenceFromObject(point))
        plane.Name = f"Pl{t:g}"
        geoset.AppendHybridShape(plane)
        try:
            part.UpdateObject(plane)
        except Exception as error:  # noqa: BLE001
            say(f"  t={t:g}: plane FAILED {str(error).splitlines()[0][:120]}")
            continue
        show("plane GetPlane()", read_plane(conn.app, spa, part, plane))
        for attr in ("GetFirstAxis", "GetSecondAxis", "GetOrigin", "FirstAxis",
                     "SecondAxis", "Origin"):
            try:
                value = getattr(plane, attr)
                show(f"plane.{attr}", repr(value))
            except Exception:  # noqa: BLE001 - absence is the answer
                pass


if __name__ == "__main__":
    main()
