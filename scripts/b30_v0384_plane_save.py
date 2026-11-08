"""0.3.84 probe 3: the angle datum plane, and the Chinese-folder first save.

Two separate questions, asked in that order because both need a clean document:

  (b) HybridShapeFactory.AddNewPlaneAngle(plane, axis, angle, orientation) -
      declared with 4 parameters. The tutorial's Step 3 is exactly this call,
      and no tool exposes it. Measured with a point at 45 degrees: a plane that
      really turned 72 degrees is 0 mm from that point's radial line and the
      distance to the point itself says whether the angle took.

  (c) catia_new_part(file_path='...\\中文目录\\x.CATPart') with ANOTHER document
      already open. The 0.3.83 report says this fails 2/2 where an empty
      session succeeded, and its error text ("Save failed after 3 attempts",
      document name only) belongs to a bare doc.Save(), not to a SaveAs - so if
      it reproduces, a code path I believed unreachable is being reached.
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

    say("")
    say("=== (b) AddNewPlaneAngle(plane, axis, angle, orientation) ===")
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 0], "point2": [0, 0, 40], "name": "AXZ"})
    geoset = part.HybridBodies.Item(1)
    axis_ref = part.CreateReferenceFromObject(part.FindObjectByName("AXZ"))
    plane_xy = part.CreateReferenceFromObject(part.OriginElements.PlaneXY)

    made = {}
    for orientation in (0, 1):
        for angle in (72.0,):
            label = f"angle={angle:g} orientation={orientation}"
            plane = attempt(
                label,
                lambda a=angle, o=orientation: hsf.AddNewPlaneAngle(
                    plane_xy, axis_ref, a, o))
            if plane is None:
                continue
            geoset.AppendHybridShape(plane)
            plane.Name = f"PLANE72_{orientation}"
            attempt(f"part.UpdateObject(PLANE72_{orientation})",
                    lambda target=plane: part.UpdateObject(target))
            made[label] = plane

    # Where the plane actually is. A point on the 72-degree meridian at r=20 and
    # z=0 lies ON a plane rotated 72 degrees about Z; it is 20*sin(72)=19.02 mm
    # away from the 72-degree plane if the angle was ignored (plane stayed XY).
    spa = conn.active_document.GetWorkbench("SPAWorkbench")
    probe = hsf.AddNewPointCoord(20.0 * 0.309017, 20.0 * 0.951057, 0.0)  # 72 deg
    geoset.AppendHybridShape(probe)
    probe.Name = "P72"
    part.UpdateObject(probe)
    probe_ref = part.CreateReferenceFromObject(probe)
    # And a point on the 72-degree plane but at r=12, to tell "on the plane" from
    # "on the axis".
    second = hsf.AddNewPointCoord(12.0 * 0.309017, 12.0 * 0.951057, 0.0)
    geoset.AppendHybridShape(second)
    second.Name = "P72r12"
    part.UpdateObject(second)
    second_ref = part.CreateReferenceFromObject(second)
    for label, plane in made.items():
        try:
            distance = float(spa.GetMeasurable(
                part.CreateReferenceFromObject(plane)).GetMinimumDistance(probe_ref))
            inner = float(spa.GetMeasurable(
                part.CreateReferenceFromObject(plane)).GetMinimumDistance(second_ref))
            say(f"  {label}: {distance:.4f} mm from the r=20/72deg point, "
                f"{inner:.4f} mm from the r=12/72deg point")
        except Exception as error:  # noqa: BLE001
            say(f"  {label}: distance unreadable "
                f"({str(error).splitlines()[0][:70]})")

    say("")
    say("=== (c) new_part into a Chinese folder, another document open ===")
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
        for line in str(error).splitlines():
            say("  | " + line[:300])
    say(f"  documents open after: {conn.documents.Count}")

    # The same call with an ASCII folder, same session, same name: the report's
    # own control, repeated here so the two can be compared in one run.
    ascii_dir = os.path.join(ROOT, "ascii")
    os.makedirs(ascii_dir, exist_ok=True)
    plain = os.path.join(ascii_dir, "prb84_cn.CATPart")
    if os.path.isfile(plain):
        os.remove(plain)
    say("")
    say("  control: same call, ASCII folder")
    try:
        reply = document.execute("catia_new_part",
                                 {"name": "PRB84CN", "file_path": plain})
        say("  OK: " + str(reply).splitlines()[0][:200])
    except Exception as error:  # noqa: BLE001
        say(f"  REFUSED code={getattr(error, 'code', '')}: "
            + str(error).splitlines()[0][:200])
    say(f"  documents open after control: {conn.documents.Count}")


if __name__ == "__main__":
    main()
