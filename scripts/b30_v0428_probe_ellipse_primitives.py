"""What can a GSD body put on a plane that is NOT a circle?

P0-4 of the 2026-11-14 report asks for elliptic sweep sections. The docstring of
catia_solid_sweep_variable says "ELLIPSE SECTIONS ARE NOT OFFERED HERE", and that
sentence was written from the same place as every other one this project has had
to correct - memory, not measurement.

dir() on this factory raises (-2147467259) because the object carries no type
info, so existence is asked by NAME, through GetIDsOfNames: a name that is not
there is an AttributeError before any CATIA call happens.

    .venv/Scripts/python.exe scripts/b30_v0428_probe_ellipse_primitives.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0428")
ROOT = os.path.join(_PROBE_ROOT, "ellipse")


def say(line: str = "") -> None:
    print(line, flush=True)


def fresh(conn: CATIAConnection, documents: DocumentTools, name: str):
    path = os.path.join(ROOT, name + ".CATPart")
    # Our own leftovers, never the caller's: matched by the probe root only.
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


CANDIDATES = [
    "AddNewEllipse",
    "AddNewConic",
    "AddNewSpline",
    "AddNewCircleCtrRad",
    "AddNewCircleCtrPt",
    "AddNewCurveParPoints",
    "AddNewParabola",
    "AddNewHyperbola",
    "AddNewLinePtPt",
]


def exists(obj, name: str) -> bool:
    try:
        getattr(obj, name)
        return True
    except AttributeError:
        return False
    except Exception as error:  # noqa: BLE001 - a COM "no such member" varies
        text = str(error)
        return "不存在" not in text and "not found" not in text.lower()


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    documents = DocumentTools(conn)
    say(f"CATIA connected, {conn.documents.Count} document(s) open")

    part = fresh(conn, documents, "ProbeNames")
    factory = part.HybridShapeFactory
    say("")
    say("does HybridShapeFactory have ... ?")
    for name in CANDIDATES:
        say(f"  {name:26s} {'YES' if exists(factory, name) else 'no'}")

    geoset = part.HybridBodies.Add()
    try:
        geoset.Name = "Geometry"
    except Exception:  # noqa: BLE001
        pass
    plane = part.OriginElements.PlaneXY
    centre = factory.AddNewPointCoord(0.0, 0.0, 0.0)
    centre.Name = "Ctr"
    geoset.AppendHybridShape(centre)
    part.UpdateObject(centre)

    say("")
    say("now trying to actually build one (ellipse, semi 10 x 6 on XY):")
    if exists(factory, "AddNewEllipse"):
        shapes = [
            ("(center, majorAxisRef, minorLen, majorLen, support, orientation)",
             [part.CreateReferenceFromObject(centre),
              part.CreateReferenceFromObject(part.OriginElements.PlaneYZ),
              6.0, 10.0, part.CreateReferenceFromObject(plane), 0]),
            ("(center, majorAxisRef, minorAxisRef, support, orientation)",
             [part.CreateReferenceFromObject(centre),
              part.CreateReferenceFromObject(part.OriginElements.PlaneYZ),
              part.CreateReferenceFromObject(part.OriginElements.PlaneXZ),
              part.CreateReferenceFromObject(plane), 0]),
            ("(center, majorLen, minorLen, support, orientation)",
             [part.CreateReferenceFromObject(centre), 10.0, 6.0,
              part.CreateReferenceFromObject(plane), 0]),
        ]
        for label, args in shapes:
            try:
                result = factory.AddNewEllipse(*args)
                result.Name = "ProbeEllipse"
                geoset.AppendHybridShape(result)
                part.UpdateObject(result)
                spa = conn.active_document.GetWorkbench("SPAWorkbench")
                length = spa.GetMeasurable(
                    part.CreateReferenceFromObject(result)).Length
                say(f"  {label}: BUILT, length={length:.4f} mm "
                    f"(an exact 10x6 ellipse is 51.05 mm)")
            except Exception as error:  # noqa: BLE001
                say(f"  {label}: {type(error).__name__}: "
                    f"{str(error).splitlines()[0][:150]}")

    try:
        circle = factory.AddNewCircleCtrRad(
            part.CreateReferenceFromObject(centre),
            part.CreateReferenceFromObject(plane), False, 5.0)
        circle.Name = "BaseCircle"
        geoset.AppendHybridShape(circle)
        part.UpdateObject(circle)
        say(f"  circle baseline: BUILT {circle.Name}")
    except Exception as error:  # noqa: BLE001
        say(f"  circle baseline: FAILED {str(error).splitlines()[0][:120]}")


if __name__ == "__main__":
    main()
