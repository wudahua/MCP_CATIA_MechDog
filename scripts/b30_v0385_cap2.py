"""0.3.85 probe, part 7: cap_center without a loop selector.

Part 6 settled two things: on a JOIN of five patches, ``gsd_boundary(surface)``
works (border length 205.725 mm) while ``AddNewBoundary(edge, joined, 1)``
refuses at UpdateObject for every one of the 30 border edges. So a loop selector
cannot be the way the report's Step 6 is closed - the geometry it has to work on
is exactly the joined one.

What can be read off the JOIN is the border and its distance to the axis, and
that gives the missing number: the inner hole's radius. From there the 0.3.84
chain applies to a REAL five-lobe base rather than a flat patch with a drilled
circle:

    circle(r = distance to axis)
    split(BASE, circle)   -> the outer piece, whose inner border IS the circle
    fill(circle)          -> the cap
    join(BASE_outer, cap) -> closed base

The arithmetic is checkable: 5 sectors of 416.087 = 2080.436, the cap is
pi*r^2, and the closed result must come back to 2080.436.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402

ROOT = r"F:\catia_probe_v0385\cap2"
INNER = 5.0
OUTER = 30.0
STEP = 72.0
SECTOR = 0.5 * (OUTER ** 2 - INNER ** 2) * math.sin(math.radians(STEP))


def say(line: str = "") -> None:
    print(line, flush=True)


def area_mm2(conn, part, element):
    try:
        spa = conn.active_document.GetWorkbench("SPAWorkbench")
        return float(spa.GetMeasurable(
            part.CreateReferenceFromObject(element)).Area) * 1e6
    except Exception:  # noqa: BLE001
        return None


def length_of(conn, part, element):
    try:
        spa = conn.active_document.GetWorkbench("SPAWorkbench")
        return float(spa.GetMeasurable(
            part.CreateReferenceFromObject(element)).Length)
    except Exception:  # noqa: BLE001
        return None


def distance_to(conn, part, element, other):
    try:
        spa = conn.active_document.GetWorkbench("SPAWorkbench")
        return float(spa.GetMeasurable(
            part.CreateReferenceFromObject(element)).GetMinimumDistance(other))
    except Exception:  # noqa: BLE001
        return None


def point(radius: float, angle_deg: float) -> list[float]:
    angle = math.radians(angle_deg)
    return [radius * math.cos(angle), radius * math.sin(angle), 0.0]


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)

    own_root = os.path.normcase(os.path.abspath(r"F:\catia_probe_v0385"))
    for index in range(conn.documents.Count, 0, -1):
        try:
            stale = conn.documents.Item(index)
            full = os.path.normcase(os.path.abspath(str(stale.FullName)))
        except Exception:  # noqa: BLE001
            continue
        if full.startswith(own_root):
            stale.Close()

    path = os.path.join(ROOT, "cap2.CATPart")
    if os.path.isfile(path):
        os.remove(path)
    document.execute("catia_new_part", {"name": "CAP85B", "file_path": path})
    part = conn.get_active_part()

    corners = [point(INNER, 0), point(OUTER, 0), point(OUTER, STEP),
               point(INNER, STEP)]
    names = ["L1", "L2", "L3", "L4"]
    for i, name in enumerate(names):
        gsd.execute("catia_gsd_line", {"point1": corners[i],
                                       "point2": corners[(i + 1) % 4],
                                       "name": name})
    gsd.execute("catia_gsd_fill", {"boundaries": names, "name": "SECTOR"})
    part.Update()
    say(f"SECTOR {area_mm2(conn, part, part.FindObjectByName('SECTOR')):.3f} mm2"
        f" (expect {SECTOR:.3f})")

    gsd.execute("catia_gsd_circ_pattern",
                {"element": "SECTOR", "count": 5, "total_angle": 360,
                 "join": True, "axis": "z", "name": "BASE"})
    part.Update()
    base = part.FindObjectByName("BASE")
    base_area = area_mm2(conn, part, base)
    say(f"BASE {base_area:.3f} mm2 (expect {5 * SECTOR:.3f})")

    say("")
    say("=== 1. the inner radius, read off the border ===")
    gsd.execute("catia_gsd_boundary", {"surface": "BASE", "name": "BORDER"})
    border = part.FindObjectByName("BORDER")
    axis = part.FindObjectByName("SECTOR_axis_z") or part.FindObjectByName(
        "BASE_axis_z")
    if axis is None:
        say("  no axis element found - the pattern's axis has a different name")
        return
    axis_ref = part.CreateReferenceFromObject(axis)
    border_length = length_of(conn, part, border)
    radius = distance_to(conn, part, border, axis_ref)
    say(f"  BORDER length {border_length:.3f} mm, nearest point to the axis "
        f"{radius:.4f} mm (the inner hole is at r={INNER})")
    if radius is None:
        return

    say("")
    say("=== 2. split the base with that circle ===")
    gsd.execute("catia_gsd_circle",
                {"center": [0, 0, 0], "radius": radius, "support_plane": "xy",
                 "name": "CAPCIRCLE"})
    part.Update()
    gsd.execute("catia_gsd_split",
                {"element": "BASE", "cutter": "CAPCIRCLE", "name": "BASE_OUTER"})
    part.Update()
    outer = part.FindObjectByName("BASE_OUTER")
    outer_area = area_mm2(conn, part, outer)
    say(f"  BASE_OUTER {outer_area:.3f} mm2 (expect "
        f"{base_area - math.pi * radius ** 2:.3f})")

    say("")
    say("=== 3. fill the circle, join it back ===")
    gsd.execute("catia_gsd_fill", {"boundaries": ["CAPCIRCLE"], "name": "CAP"})
    part.Update()
    cap = part.FindObjectByName("CAP")
    say(f"  CAP {area_mm2(conn, part, cap):.3f} mm2 "
        f"(expect {math.pi * radius ** 2:.3f})")
    gsd.execute("catia_gsd_join",
                {"elements": ["BASE_OUTER", "CAP"], "name": "CLOSED"})
    part.Update()
    closed = part.FindObjectByName("CLOSED")
    closed_area = area_mm2(conn, part, closed)
    say(f"  CLOSED {closed_area:.3f} mm2 (expect {base_area:.3f})")
    if closed_area:
        say(f"  difference from the uncapped base: "
            f"{closed_area - base_area:+.4f} mm2")


if __name__ == "__main__":
    main()
