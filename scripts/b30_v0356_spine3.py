"""Does the spine obey the sections' ORIENTATION, and does AddGuide work?

Established: AddSection increments GetNumberOfSections and UpdateObject succeeds.
But with three parallel rings the spine measured 40.0000 mm and sat exactly on
the Z axis - distance 0 to the first and last centres, exactly 30 mm to a centre
that had been offset 30 mm sideways. So parallel sections do not bend it: a
spine is the curve normal to the sections, not a curve through their centres, and
that measurement could not tell "uses the sections" from "connects the first and
last centre".

Tilted sections settle it. Section 1 is a circle in XY at z=0 (normal +Z);
section 2 is a circle sitting in a plane tilted 45 deg about X (normal
(0,-1,1)/sqrt2). A spine normal to both has to turn 45 deg between them:

    honours the sections  -> Length > 40 and the curve leaves the Z axis
    ignores the second    -> Length = 40.0000 and every point on it is at
                             distance 0 from the Z axis

Also measured here: a spine with ONE section (does it update?), and AddGuide with
a straight line (does the guide count move, does update still work).
"""

from __future__ import annotations

import math
import os
import sys
import tempfile

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools

OUT_DIR = os.path.join(tempfile.gettempdir(), "b30_v0356")


def say(text: str = "") -> None:
    print(text, flush=True)


def fresh(conn, document, tag: str):
    document.execute("catia_new_part",
                     {"name": tag, "file_path": os.path.join(OUT_DIR, f"{tag}.CATPart")})
    assert str(conn.active_document.Name).startswith(tag), conn.active_document.Name
    return conn.get_active_part()


def close_tag(conn, document, tag: str) -> None:
    for index in range(conn.documents.Count, 0, -1):
        try:
            if str(conn.documents.Item(index).Name).startswith(tag):
                conn.documents.Item(index).Activate()
                document.execute("catia_close_document", {"save": False})
        except Exception:  # noqa: BLE001
            pass


def measurable_of(conn, part, element):
    return (conn.active_document.GetWorkbench("SPAWorkbench")
            .GetMeasurable(part.CreateReferenceFromObject(element)))


def case_tilted(conn, document, gsd) -> None:
    tag = "SPTILT"
    say(f"\n===== tilted sections: spine has to turn 45 deg =====")
    part = fresh(conn, document, tag)
    factory = gsd._factory()
    geoset = gsd._get_geoset()
    # section 1: XY plane at z = 0
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": 10.0, "name": f"{tag}_C1"})
    # section 2: plane through (0,0,40),(20,0,40),(0,20,60) - normal (0,-1,1)/sqrt2
    for index, point in enumerate(([0, 0, 40], [20, 0, 40], [0, 20, 60]), start=1):
        gsd.execute("catia_gsd_point", {"x": point[0], "y": point[1], "z": point[2],
                                        "name": f"{tag}_PP{index}"})
    gsd.execute("catia_gsd_plane_3points", {"point1": f"{tag}_PP1",
                                            "point2": f"{tag}_PP2",
                                            "point3": f"{tag}_PP3",
                                            "name": f"{tag}_PL"})
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 40], "support_plane": f"{tag}_PL",
                                     "radius": 7.0, "name": f"{tag}_C2"})
    for z in (10, 20, 30):
        gsd.execute("catia_gsd_point", {"x": 0, "y": 0, "z": float(z),
                                        "name": f"{tag}_AX{int(z)}"})

    spine = factory.AddNewSpine()
    geoset.AppendHybridShape(spine)
    spine.Name = f"{tag}_SP"
    for name in (f"{tag}_C1", f"{tag}_C2"):
        ref = part.CreateReferenceFromObject(part.FindObjectByName(name))
        spine.AddSection(ref)
        say(f"  AddSection({name}) -> {spine.GetNumberOfSections()}")
    try:
        part.UpdateObject(spine)
        say("  UpdateObject OK")
    except Exception as error:  # noqa: BLE001
        say(f"  UpdateObject FAILED: {str(error).splitlines()[0][:70]}")
        close_tag(conn, document, tag)
        return

    measurable = measurable_of(conn, part, spine)
    say(f"  Length = {float(measurable.Length):.4f} mm  "
        f"(straight span 40.00; a 45 deg turn is longer)")
    for z in (10, 20, 30):
        name = f"{tag}_AX{int(z)}"
        point_ref = part.CreateReferenceFromObject(part.FindObjectByName(name))
        say(f"  distance from curve to (0,0,{z}): "
            f"{float(measurable.GetMinimumDistance(point_ref)):.4f} mm  "
            f"(0 = still on the Z axis)")
    close_tag(conn, document, tag)


def case_one_section(conn, document, gsd) -> None:
    tag = "SPONE"
    say("\n===== one section only =====")
    part = fresh(conn, document, tag)
    factory = gsd._factory()
    geoset = gsd._get_geoset()
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": 10.0, "name": f"{tag}_C1"})
    spine = factory.AddNewSpine()
    geoset.AppendHybridShape(spine)
    spine.Name = f"{tag}_SP"
    spine.AddSection(part.CreateReferenceFromObject(
        part.FindObjectByName(f"{tag}_C1")))
    say(f"  sections {spine.GetNumberOfSections()}")
    try:
        part.UpdateObject(spine)
        say("  UpdateObject OK with one section")
    except Exception as error:  # noqa: BLE001
        say(f"  UpdateObject FAILED with one section: "
            f"{str(error).splitlines()[0][:70]}")
    close_tag(conn, document, tag)


def case_guide(conn, document, gsd) -> None:
    tag = "SPGUIDE"
    say("\n===== two sections plus a straight guide =====")
    part = fresh(conn, document, tag)
    factory = gsd._factory()
    geoset = gsd._get_geoset()
    for index, z in enumerate((0.0, 40.0), start=1):
        gsd.execute("catia_gsd_circle", {"center": [0, 0, z], "support_plane": "xy",
                                         "radius": 10.0, "name": f"{tag}_C{index}"})
    gsd.execute("catia_gsd_line", {"point1": [40, 0, 0], "point2": [40, 0, 40],
                                   "name": f"{tag}_G"})
    spine = factory.AddNewSpine()
    geoset.AppendHybridShape(spine)
    spine.Name = f"{tag}_SP"
    for name in (f"{tag}_C1", f"{tag}_C2"):
        spine.AddSection(part.CreateReferenceFromObject(part.FindObjectByName(name)))
    try:
        spine.AddGuide(part.CreateReferenceFromObject(
            part.FindObjectByName(f"{tag}_G")))
        say(f"  AddGuide -> sections {spine.GetNumberOfSections()}, "
            f"guides {spine.GetNumberOfGuides()}")
    except Exception as error:  # noqa: BLE001
        say(f"  AddGuide FAILED: {str(error).splitlines()[0][:70]}")
    try:
        part.UpdateObject(spine)
        measurable = measurable_of(conn, part, spine)
        say(f"  UpdateObject OK, Length {float(measurable.Length):.4f} mm")
    except Exception as error:  # noqa: BLE001
        say(f"  UpdateObject FAILED with the guide: {str(error).splitlines()[0][:70]}")
    close_tag(conn, document, tag)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    case_tilted(conn, document, gsd)
    case_one_section(conn, document, gsd)
    case_guide(conn, document, gsd)
    say(f"\ndocuments open now: {conn.documents.Count}")


if __name__ == "__main__":
    main()
