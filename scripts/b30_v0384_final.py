"""0.3.84 probe 7: two last attempts, then the conclusion is written down.

(a) AddNewGSDCircPattern with a PAD as iShapeToCopy, and with the body set as
    the in-work object first. Probes 2 and 6 used a GSD surface and every iType
    /crown combination refused to update; ShapeFactory is the Part Design
    factory, so a Part Design feature as the source is the one shape not yet
    tried. If this refuses too, the member is declared and not callable, which
    is what the description has to say.

(b) AddNewPlaneAngle, measured properly. Probe 5's "72-degree point" was placed
    on the wrong ray: rotating the YZ plane 72 degrees about Z gives the plane
    whose NORMAL is (cos72, sin72, 0), so the plane contains (-sin72, cos72, 0).
    Measure both rays, and 0 mm on the right one is the evidence that the angle
    argument is in DEGREES.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0384\final"


def say(line: str = "") -> None:
    print(line, flush=True)


def fresh(document, name):
    path = os.path.join(ROOT, f"{name}.CATPart")
    if os.path.isfile(path):
        os.remove(path)
    document.execute("catia_new_part", {"name": name, "file_path": path})


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    design = PartDesignTools(conn)
    sketcher = SketcherTools(conn)

    own_root = os.path.normcase(os.path.abspath(r"F:\catia_probe_v0384"))
    for index in range(conn.documents.Count, 0, -1):
        try:
            stale = conn.documents.Item(index)
            full = os.path.normcase(os.path.abspath(str(stale.FullName)))
        except Exception:  # noqa: BLE001
            continue
        if full.startswith(own_root):
            stale.Close()

    say("=== (a) GSD circ pattern with a PAD as the source ===")
    fresh(document, "FPAD")
    part = conn.get_active_part()
    hsf = part.HybridShapeFactory
    sf = part.ShapeFactory
    body = conn.get_active_part_body()
    try:
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 10, "y1": 0, "x2": 20, "y2": 5})
        sketcher.execute("catia_close_sketch", {})
        say("  pad: " + design.execute("catia_pad", {"height": 5}).splitlines()[0][:130])
    except Exception as error:  # noqa: BLE001
        say(f"  could not build the pad: {str(error).splitlines()[0][:130]}")
        return
    part.Update()

    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 0], "point2": [0, 0, 20], "name": "AXZ"})
    geoset = part.FindObjectByName("GSD_Set")
    centre = hsf.AddNewPointCoord(0.0, 0.0, 0.0)
    if geoset is not None:
        geoset.AppendHybridShape(centre)
    centre.Name = "CEN"
    part.UpdateObject(centre)

    axis_ref = part.CreateReferenceFromObject(part.FindObjectByName("AXZ"))
    centre_ref = part.CreateReferenceFromObject(centre)
    pad = part.FindObjectByName("凸台.1") or part.FindObjectByName("Pad.1")
    say(f"  pad element found: {'yes' if pad is not None else 'no'}")
    pad_ref = part.CreateReferenceFromObject(pad) if pad is not None else None

    try:
        part.InWorkObject = body
        say("  in-work object set to the body")
    except Exception as error:  # noqa: BLE001
        say(f"  InWorkObject refused: {str(error).splitlines()[0][:90]}")

    for label, source_ref in (("pad feature", pad_ref), ("GSD surface", None)):
        if source_ref is None and label == "GSD surface":
            gsd.execute("catia_gsd_extrude",
                        {"profile": "AXZ", "direction": [1, 0, 0], "limit1": 5,
                         "name": "SRF"})
            source_ref = part.CreateReferenceFromObject(
                part.FindObjectByName("SRF"))
        if source_ref is None:
            continue
        try:
            made = sf.AddNewGSDCircPattern(
                source_ref, 1, 5, 0.0, 72.0, 0.0, 0.0,
                centre_ref, axis_ref, False, 360.0, False, True, 0)
        except Exception as error:  # noqa: BLE001
            say(f"  {label}: create REFUSED {str(error).splitlines()[0][:110]}")
            continue
        try:
            part.UpdateObject(made)
            say(f"  {label}: UPDATED - the member works after all")
        except Exception as error:  # noqa: BLE001
            say(f"  {label}: UpdateObject REFUSED "
                f"{str(error).splitlines()[0][:110]}")
            try:
                part.Update()
            except Exception:  # noqa: BLE001
                pass

    say("")
    say("=== (b) AddNewPlaneAngle, both rays ===")
    fresh(document, "FPLN")
    part = conn.get_active_part()
    hsf = part.HybridShapeFactory
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 0], "point2": [0, 0, 40], "name": "AXZ"})
    geoset = part.HybridBodies.Item(1)
    axis_ref = part.CreateReferenceFromObject(part.FindObjectByName("AXZ"))
    plane_ref = part.CreateReferenceFromObject(part.OriginElements.PlaneYZ)
    made = hsf.AddNewPlaneAngle(plane_ref, axis_ref, 72.0, 0)
    geoset.AppendHybridShape(made)
    made.Name = "PLANE72"
    part.UpdateObject(made)
    part.Update()

    spa = conn.active_document.GetWorkbench("SPAWorkbench")
    plane_measurable = spa.GetMeasurable(part.CreateReferenceFromObject(made))
    radians = math.radians(72.0)
    probes = {
        "along -sin72,+cos72 (in plane if 72 deg)":
            (0.0, -20 * math.sin(radians), 20 * math.cos(radians)),
        "along +cos72,+sin72 (the normal if 72 deg)":
            (0.0, 20 * math.cos(radians), 20 * math.sin(radians)),
        "still on the source plane":
            (0.0, 20.0, 0.0),
    }
    for label, (x, y, z) in probes.items():
        probe = hsf.AddNewPointCoord(x, y, z)
        geoset.AppendHybridShape(probe)
        probe.Name = "P"
        part.UpdateObject(probe)
        try:
            distance = float(plane_measurable.GetMinimumDistance(
                part.CreateReferenceFromObject(probe)))
            say(f"  {label}: {distance:.4f} mm")
        except Exception as error:  # noqa: BLE001
            say(f"  {label}: unreadable ({str(error).splitlines()[0][:50]})")


if __name__ == "__main__":
    main()
