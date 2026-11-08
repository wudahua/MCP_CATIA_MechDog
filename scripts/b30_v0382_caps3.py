"""0.3.82 probe 3: which call shape does "plane normal to curve" actually take?

The audit answered "无效的参数数目" (invalid number of arguments) for
AddNewPlaneNormal(curve_ref, point_ref) even though GetNames reported two
parameters. So the failure is one of: the arity the typelib reports is not the
arity COM enforces, an argument needs to be the OBJECT rather than its
reference, or the point-on-curve call is the one refusing and its message is
being reported as the plane's. Ask each call on its own and print what it says.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402

ROOT = r"F:\catia_probe_v0382_caps3"

WANTED = ("AddNewPointOnCurveFromPercent", "AddNewPointOnCurveWithReferenceFromPercent",
          "AddNewPlaneNormal", "AddNewPlane1Curve", "AddNewPointOnCurve")


def say(line: str = "") -> None:
    print(line, flush=True)


def signatures(obj) -> None:
    info = obj._oleobj_.GetTypeInfo()
    count = info.GetTypeAttr().cFuncs
    for index in range(count):
        try:
            desc = info.GetFuncDesc(index)
            names = info.GetNames(desc.memid)
        except Exception:  # noqa: BLE001
            continue
        if names and names[0] in WANTED:
            say(f"  {names[0]}({', '.join(names[1:])}) params={len(names) - 1}")


def attempt(label, call):
    try:
        result = call()
        say(f"  {label}: OK -> {type(result).__name__}")
        return result
    except Exception as error:  # noqa: BLE001 - the refusal is the measurement
        say(f"  {label}: REFUSED {str(error).splitlines()[0][:130]}")
        return None


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    part_path = os.path.join(ROOT, "caps3.CATPart")
    document.execute("catia_new_part",
                     {"name": "CAPS3", "file_path": part_path, "overwrite": True})
    say(f"active: {conn.active_document.Name}")

    part = conn.get_active_part()
    factory = part.HybridShapeFactory
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 0], "point2": [0, 0, 100], "name": "AXIS100"})
    # The geometrical set exists only once GSD has made one.
    geoset = part.HybridBodies.Item(1)
    line = part.FindObjectByName("AXIS100")
    line_ref = part.CreateReferenceFromObject(line)

    say("\n=== typelib signatures ===")
    signatures(factory)

    say("\n=== point on curve: what does iLong mean? (line is 100 mm) ===")
    for value in (0.5, 50.0):
        point = attempt(f"AddNewPointOnCurveFromPercent(ref, {value}, 0)",
                        lambda v=value: factory.AddNewPointOnCurveFromPercent(
                            line_ref, v, 0))
        if point is None:
            continue
        geoset.AppendHybridShape(point)
        point.Name = f"PC{value}"
        attempt(f"part.UpdateObject(PC{value})",
                lambda target=point: part.UpdateObject(target))
        say("   -> GetPoint is not readable here; its position is measured below")
        if value == 0.5:
            say("\n=== plane normal to the curve at that point ===")
            created = attempt(
                "AddNewPlaneNormal(line_ref, point_ref)",
                lambda target=point: factory.AddNewPlaneNormal(
                    line_ref, part.CreateReferenceFromObject(target)))
            if created is not None:
                geoset.AppendHybridShape(created)
                created.Name = "NRM"
                attempt("part.UpdateObject(NRM)",
                        lambda target=created: part.UpdateObject(target))

    # What iLong means: put a datum point at z=50 (the midpoint of a 100 mm line)
    # and measure the plane built with iLong=0.5 against it. 0 mm means iLong is a
    # FRACTION; 49.5 mm means it is a LENGTH in mm.
    say("\n=== is iLong a fraction or a length? ===")
    spa = conn.active_document.GetWorkbench("SPAWorkbench")
    probe_point = factory.AddNewPointCoord(0, 0, 50)
    geoset.AppendHybridShape(probe_point)
    probe_point.Name = "PROBE_50"
    part.UpdateObject(probe_point)
    plane = part.FindObjectByName("NRM")
    second = factory.AddNewPlaneNormal(
        line_ref, part.CreateReferenceFromObject(part.FindObjectByName("PC0.5")))
    geoset.AppendHybridShape(second)
    second.Name = "NRM_HALF"
    part.UpdateObject(second)
    for label, target in (("plane built with iLong=0.5", second),):
        distance = spa.GetMeasurable(
            part.CreateReferenceFromObject(target)).GetMinimumDistance(
                part.CreateReferenceFromObject(probe_point))
        say(f"   {label}: {float(distance):.3f} mm from the z=50 datum point")

    say("\n=== cleanup ===")
    own = os.path.normcase(os.path.abspath(ROOT))
    for index in range(conn.documents.Count, 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = os.path.normcase(os.path.abspath(str(doc.FullName)))
        except Exception:  # noqa: BLE001
            continue
        if full.startswith(own):
            doc.Activate()
            document.execute("catia_close_document", {"save": False})
    say("closed the documents under " + ROOT)


if __name__ == "__main__":
    main()
