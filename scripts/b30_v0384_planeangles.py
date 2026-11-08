"""0.3.84 probe 5: what call shape does AddNewPlaneAngle accept?

The typelib says AddNewPlaneAngle(iPlane, iRevolAxis, iAngle, iOrientation) and
the call RETURNS an object, but part.UpdateObject() refuses it - so the arity is
right and something else is wrong. The candidates, each cheap to rule out:

  * iAngle in radians rather than degrees (the typelib does not say)
  * iPlane as the Datum object rather than its Reference
  * iOrientation as an enum where 0 means "not set"
  * iRevolAxis built by AddNewLinePtPt rather than a GSD line element

One fresh part per combination: a refused UpdateObject can leave the part unable
to update at all (measured in probe 2), which would poison every later attempt in
the same document.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402

ROOT = r"F:\catia_probe_v0384\plane"


def say(line: str = "") -> None:
    print(line, flush=True)


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)

    own_root = os.path.normcase(os.path.abspath(r"F:\catia_probe_v0384"))
    for index in range(conn.documents.Count, 0, -1):
        try:
            stale = conn.documents.Item(index)
            full = os.path.normcase(os.path.abspath(str(stale.FullName)))
        except Exception:  # noqa: BLE001
            continue
        if full.startswith(own_root):
            stale.Close()

    cases = [
        ("ref  + deg  + ori 0", "ref", "deg", 0, "gsd"),
        ("ref  + rad  + ori 0", "ref", "rad", 0, "gsd"),
        ("obj  + deg  + ori 0", "obj", "deg", 0, "gsd"),
        ("obj  + rad  + ori 0", "obj", "rad", 0, "gsd"),
        ("ref  + deg  + ori 1", "ref", "deg", 1, "gsd"),
        ("ref  + rad  + ori 1", "ref", "rad", 1, "gsd"),
        ("ref  + deg  + ori -1", "ref", "deg", -1, "gsd"),
        ("ref  + deg  + ori 0 on AddNewLinePtPt axis", "ref", "deg", 0, "factory"),
    ]

    for position, (label, plane_form, unit, orientation, axis_kind) in enumerate(cases, 1):
        name = f"PL{position}"
        path = os.path.join(ROOT, f"{name}.CATPart")
        if os.path.isfile(path):
            os.remove(path)
        try:
            document.execute("catia_new_part", {"name": name, "file_path": path})
        except Exception as error:  # noqa: BLE001 - a leftover document
            say(f"{label}: could not create the part ({error})")
            continue

        part = conn.get_active_part()
        hsf = part.HybridShapeFactory
        geoset = part.HybridBodies.Add()
        geoset.Name = "GS"

        # The axis: a 40 mm line on Z, made the way the case asks for.
        if axis_kind == "factory":
            axis = hsf.AddNewLinePtPt(
                hsf.AddNewPointCoord(0, 0, 0), hsf.AddNewPointCoord(0, 0, 40))
            geoset.AppendHybridShape(axis)
            axis.Name = "AX"
            part.UpdateObject(axis)
            axis_ref = part.CreateReferenceFromObject(axis)
        else:
            line = hsf.AddNewLinePtPt(
                hsf.AddNewPointCoord(0, 0, 0), hsf.AddNewPointCoord(0, 0, 40))
            geoset.AppendHybridShape(line)
            line.Name = "AX"
            part.UpdateObject(line)
            axis_ref = part.CreateReferenceFromObject(line)

        plane_obj = part.OriginElements.PlaneYZ
        plane_arg = (plane_obj if plane_form == "obj"
                     else part.CreateReferenceFromObject(plane_obj))
        angle = 72.0 if unit == "deg" else math.radians(72.0)

        try:
            made = hsf.AddNewPlaneAngle(plane_arg, axis_ref, angle, orientation)
        except Exception as error:  # noqa: BLE001 - arity or type refused
            say(f"{label}: AddNewPlaneAngle REFUSED "
                f"{str(error).splitlines()[0][:120]}")
            continue

        try:
            geoset.AppendHybridShape(made)
            made.Name = "P72"
            part.UpdateObject(made)
        except Exception as error:  # noqa: BLE001 - the question
            say(f"{label}: created but UPDATE REFUSED "
                f"{str(error).splitlines()[0][:110]}")
            try:
                part.Update()
            except Exception:  # noqa: BLE001 - expected after a bad node
                pass
            continue

        # A plane rotated 72 degrees about Z contains the r=20 point at 72 deg and
        # not the r=20 point at 0 deg. Both distances, or the number says nothing.
        spa = conn.active_document.GetWorkbench("SPAWorkbench")
        distances = {}
        # Measured in the YZ plane now: the plane's own coordinates are (y, z),
        # so a point at radius 20 in that plane is 20*cos/sin from +Y towards +Z.
        for tag, (x, y, z) in (
                ("on-72deg", (0.0, 20 * math.cos(math.radians(72)),
                              20 * math.sin(math.radians(72)))),
                ("on-0deg", (0.0, 20.0, 0.0))):
            probe = hsf.AddNewPointCoord(x, y, z)
            geoset.AppendHybridShape(probe)
            probe.Name = f"PT_{tag}"
            part.UpdateObject(probe)
            try:
                distances[tag] = float(spa.GetMeasurable(
                    part.CreateReferenceFromObject(made)).GetMinimumDistance(
                        part.CreateReferenceFromObject(probe)))
            except Exception as error:  # noqa: BLE001
                distances[tag] = str(error).splitlines()[0][:40]
        say(f"{label}: UPDATED; distance {distances.get('on-72deg')} mm to the "
            f"72-degree point, {distances.get('on-0deg')} mm to the 0-degree point")


if __name__ == "__main__":
    main()
