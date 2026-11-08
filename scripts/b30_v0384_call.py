"""0.3.84 probe 2: can the declared methods actually be called?

b30_v0384_sigs.py settled WHERE the members are. That is half the question; the
other half is whether a declared member can be instantiated, updated and edited,
which is what the caller needs. Three things to measure:

  (a) ShapeFactory.AddNewGSDCircPattern - 14 arguments. If it updates, a GSD
      pattern is ONE tree node and 'count' is an editable property, which ends
      the rotate-and-join workaround and its shared-edge class of failures.
  (b) HybridShapeFactory.AddNewPlaneAngle - 4 arguments. The tutorial's Step 3
      is "a datum plane 36 degrees from the front plane, about the axis".
  (c) catia_new_part(file_path=Chinese folder) with ANOTHER document already
      open - the 0.3.83 report's P0, and its claim is that the open-document
      state is what decides it.

Everything is built in this probe's own part under ROOT.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402

ROOT = r"F:\catia_probe_v0384"


def say(line: str = "") -> None:
    print(line, flush=True)


def attempt(label, call):
    try:
        result = call()
        say(f"  {label}: OK -> {type(result).__name__}")
        return result
    except Exception as error:  # noqa: BLE001 - the refusal is the measurement
        say(f"  {label}: REFUSED {str(error).splitlines()[0][:150]}")
        return None


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)

    own_root = os.path.normcase(os.path.abspath(ROOT))
    for index in range(conn.documents.Count, 0, -1):
        try:
            stale = conn.documents.Item(index)
            full = os.path.normcase(os.path.abspath(str(stale.FullName)))
        except Exception:  # noqa: BLE001
            continue
        if full.startswith(own_root):
            say(f"closing a document left by an earlier run: {stale.Name}")
            stale.Close()

    part_path = os.path.join(ROOT, "prb84.CATPart")
    if os.path.isfile(part_path):
        os.remove(part_path)
    document.execute("catia_new_part", {"name": "PRB84", "file_path": part_path})
    part = conn.get_active_part()
    hsf = part.HybridShapeFactory
    sf = part.ShapeFactory

    say("")
    say("=== geometry the patterns need ===")
    say(gsd.execute("catia_gsd_line",
                    {"point1": [0, 0, 0], "point2": [0, 0, 10], "name": "AXZ"})
        .splitlines()[0])
    say(gsd.execute("catia_gsd_line",
                    {"point1": [20, 0, 0], "point2": [30, 0, 0], "name": "SPOKE"})
        .splitlines()[0])
    say(gsd.execute("catia_gsd_extrude",
                    {"profile": "SPOKE", "direction": [0, 0, 1], "limit1": 5,
                     "name": "PATCH"}).splitlines()[0])

    geoset = part.HybridBodies.Item(1)
    center = hsf.AddNewPointCoord(0.0, 0.0, 0.0)
    geoset.AppendHybridShape(center)
    center.Name = "CEN"
    part.UpdateObject(center)

    axis_ref = part.CreateReferenceFromObject(part.FindObjectByName("AXZ"))
    surf_ref = part.CreateReferenceFromObject(part.FindObjectByName("PATCH"))
    center_ref = part.CreateReferenceFromObject(center)

    say("")
    say("=== (a) ShapeFactory.AddNewGSDCircPattern, 14 arguments ===")
    pattern = attempt(
        "AddNewGSDCircPattern(1 radial, 5 angular, step 72 deg, full crown)",
        lambda: sf.AddNewGSDCircPattern(
            surf_ref, 1, 5, 0.0, 72.0, 0.0, 0.0,
            center_ref, axis_ref, False, 360.0, False, True, 0))
    if pattern is not None:
        # Do not assume which container takes it: a Pattern belongs to the body
        # that owns the copied feature, not necessarily to a geometrical set.
        attempt("geoset.AppendHybridShape(GSD_PAT)",
                lambda: geoset.AppendHybridShape(pattern))
        attempt("body.Shapes.Add(GSD_PAT)",
                lambda: conn.get_active_part_body().Shapes.Add(pattern))
        part.Update()
        attempt("part.UpdateObject(GSD_PAT)", lambda: part.UpdateObject(pattern))
        # What the caller actually wants to edit afterwards.
        for prop in ("NbOfCopiesInAngularDir", "StepInAngularDir",
                     "RotationAngle", "CompleteCrown"):
            try:
                say(f"  property {prop} = {getattr(pattern, prop)!r}")
            except Exception as error:  # noqa: BLE001
                say(f"  property {prop}: REFUSED "
                    f"{str(error).splitlines()[0][:70]}")
        for prop, value in (("NbOfCopiesInAngularDir", 8),
                           ("StepInAngularDir", 45.0)):
            try:
                setattr(pattern, prop, value)
                part.UpdateObject(pattern)
                say(f"  writing {prop} = {value!r}: accepted and updated")
            except Exception as error:  # noqa: BLE001
                say(f"  writing {prop} = {value!r}: REFUSED "
                    f"{str(error).splitlines()[0][:80]}")

    say("")
    say("=== (b) HybridShapeFactory.AddNewPlaneAngle, 4 arguments ===")
    plane_xy = part.CreateReferenceFromObject(part.OriginElements.PlaneXY)
    angled = attempt(
        "AddNewPlaneAngle(PlaneXY, AXZ, 72 deg, orientation 0)",
        lambda: hsf.AddNewPlaneAngle(plane_xy, axis_ref, 72.0, 0))
    if angled is not None:
        geoset.AppendHybridShape(angled)
        angled.Name = "PLANE72"
        attempt("part.UpdateObject(PLANE72)", lambda: part.UpdateObject(angled))
        # The angle is only real if the plane is where the angle says: measure
        # between the new plane and its source.
        try:
            spa = conn.active_document.GetWorkbench("SPAWorkbench")
            angle = spa.GetMeasurable(
                part.CreateReferenceFromObject(angled)).GetAngle(
                    part.CreateReferenceFromObject(
                        part.FindObjectByName("PLANE72")))
            say(f"  GetAngle against itself = {float(angle):.3f} (sanity only)")
        except Exception as error:  # noqa: BLE001
            say(f"  angle unreadable: {str(error).splitlines()[0][:70]}")
        # A datum point at 45 deg in the XY plane tells us which way it turned.
        probe = hsf.AddNewPointCoord(20.0 * 0.7071, 20.0 * 0.7071, 0.0)
        geoset.AppendHybridShape(probe)
        probe.Name = "P45"
        part.UpdateObject(probe)
        try:
            spa = conn.active_document.GetWorkbench("SPAWorkbench")
            distance = spa.GetMeasurable(
                part.CreateReferenceFromObject(angled)).GetMinimumDistance(
                    part.CreateReferenceFromObject(probe))
            say(f"  distance to the 45-degree point (r=20) = {float(distance):.4f} mm")
        except Exception as error:  # noqa: BLE001
            say(f"  distance unreadable: {str(error).splitlines()[0][:70]}")

    say("")
    say("=== (c) new_part into a Chinese folder, WITH another document open ===")
    say(f"  documents open now: {conn.documents.Count}")
    chinese = os.path.join(ROOT, "中文目录")
    os.makedirs(chinese, exist_ok=True)
    target = os.path.join(chinese, "prb84_cn.CATPart")
    if os.path.isfile(target):
        os.remove(target)
    try:
        reply = document.execute("catia_new_part",
                                 {"name": "PRB84CN", "file_path": target})
        say("  OK: " + str(reply).splitlines()[0][:300])
    except Exception as error:  # noqa: BLE001 - the failure is the measurement
        say(f"  REFUSED code={getattr(error, 'code', '')} "
            f"stage={getattr(error, 'stage', '')}")
        say("  " + str(error).splitlines()[0][:400])
    say(f"  documents open after: {conn.documents.Count}")


if __name__ == "__main__":
    main()
