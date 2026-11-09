"""0.3.85 probe, part 8: cap a five-lobe hole with the hole's OWN border.

Parts 6 and 7 closed two doors and left one open:

  * ``AddNewBoundary(edge, joined, 1)`` refuses at UpdateObject for all 30 border
    edges of the joined base, so a loop selector cannot work on an array result.
  * ``split`` by a circle ALSO fails there, and the reason is arithmetic worth
    keeping: the border's nearest point to the axis is 4.0451, not 5.0 - a
    five-sided hole's closest point is the middle of an EDGE (5*cos36), so a
    circle at that radius lies wholly inside the hole and cuts nothing.

What is left is the tutorial's own move, with the inner border itself as the
cap's border. On the array's SOURCE patch the inner edge is a real curve with a
name; rotating it count-1 times and joining the copies rebuilds exactly the hole's
border - no circle, no near miss, and the fill shares the edge by construction.

Measured here on the shrunk five-lobe base (r 5..30, 72 deg sectors):
    5 sectors of 416.087 = 2080.436 mm2
    the hole is a regular pentagon of side 2*5*sin36, area 5*25*sin72/2 = 59.441
    so a closed base must come back to 2080.436 + ... or replace the hole:
    BASE already excludes the hole, so CLOSED = BASE + pentagon = 2139.877
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402

ROOT = r"F:\catia_probe_v0385\cap3"
INNER = 5.0
OUTER = 30.0
STEP = 72.0
SECTOR = 0.5 * (OUTER ** 2 - INNER ** 2) * math.sin(math.radians(STEP))
PENTAGON = 0.5 * 5 * INNER ** 2 * math.sin(math.radians(STEP))


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

    path = os.path.join(ROOT, "cap3.CATPart")
    if os.path.isfile(path):
        os.remove(path)
    document.execute("catia_new_part", {"name": "CAP85C", "file_path": path})
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
    say(f"SECTOR {area_mm2(conn, part, part.FindObjectByName('SECTOR')):.3f} mm2")
    say(f"  L4 is the inner edge: {point(INNER, STEP)} -> {point(INNER, 0)},"
        f" length {INNER * 2 * math.sin(math.radians(STEP / 2)):.4f} mm")

    gsd.execute("catia_gsd_circ_pattern",
                {"element": "SECTOR", "count": 5, "total_angle": 360,
                 "join": True, "axis": "z", "name": "BASE"})
    part.Update()
    base = part.FindObjectByName("BASE")
    say(f"BASE {area_mm2(conn, part, base):.3f} mm2")

    say("")
    say("=== rotate the inner edge five times and join the copies ===")
    copies = ["L4"]
    for k in range(1, 5):
        name = f"L4_r{int(STEP * k)}"
        try:
            gsd.execute("catia_gsd_rotate",
                        {"element": "L4", "axis": "z", "angle": STEP * k,
                         "name": name})
            copies.append(name)
        except Exception as error:  # noqa: BLE001
            say(f"  rotate {k}: REFUSED {str(error).splitlines()[0][:110]}")
    part.Update()
    for name in copies:
        element = part.FindObjectByName(name)
        say(f"  {name}: length {length_of(conn, part, element)}")

    say("")
    say("=== join the five inner edges into the hole's border ===")
    try:
        gsd.execute("catia_gsd_join", {"elements": copies, "name": "HOLEBORDER"})
        part.Update()
        hole = part.FindObjectByName("HOLEBORDER")
        say(f"  HOLEBORDER length {length_of(conn, part, hole):.4f} mm "
            f"(pentagon perimeter {5 * 2 * INNER * math.sin(math.radians(36)):.4f})")
    except Exception as error:  # noqa: BLE001
        say(f"  REFUSED {str(error).splitlines()[0][:200]}")
        return

    say("")
    say("=== fill it, then join onto the base ===")
    try:
        gsd.execute("catia_gsd_fill",
                    {"boundaries": ["HOLEBORDER"], "name": "CAP"})
        part.Update()
        cap = part.FindObjectByName("CAP")
        say(f"  CAP {area_mm2(conn, part, cap):.3f} mm2 "
            f"(pentagon {PENTAGON:.3f})")
    except Exception as error:  # noqa: BLE001
        say(f"  fill REFUSED {str(error).splitlines()[0][:200]}")
        return
    try:
        gsd.execute("catia_gsd_join",
                    {"elements": ["BASE", "CAP"], "name": "CLOSED"})
        part.Update()
        closed = part.FindObjectByName("CLOSED")
        closed_area = area_mm2(conn, part, closed)
        say(f"  CLOSED {closed_area:.3f} mm2 "
            f"(BASE + pentagon = {area_mm2(conn, part, base) + PENTAGON:.3f})")
    except Exception as error:  # noqa: BLE001
        say(f"  join REFUSED {str(error).splitlines()[0][:200]}")


if __name__ == "__main__":
    main()
