"""0.3.85 probe, part 6: the report's Step 6, shrunk to a flat five-lobe base.

This is the geometry the report actually failed on, in the smallest form that
still has the property that matters: a ring of five patches whose inner borders
form a closed five-sided hole. Points at radius 5 and 30, angle 72 degrees:

    A(5,0) -> B(30,0) -> C(30@72) -> D(5@72) -> A     one sector
    repeated five times about z                        the base, with a hole

Then the three questions the 0.3.85 work depends on, in this order:

  1. Does catia_gsd_boundary still work on the JOIN result? (0.3.84's two-half
     cone refused it, and that is either a quirk of a fully closed cone or a
     real limit on joins - the difference decides how the wrap is written.)
  2. Can AddNewBoundary(edge, joined, propagation) pick the INNER loop only?
     Length and distance to the axis both matter: the inner five-sided border is
     ~10*sin(36)/sin(72) ... not a circle, so the distingufishing number is the
     distance to the axis (~4.05 inner against ~29.4 outer).
  3. Can that inner loop be filled and joined, giving the tutorial's Step 6?
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402

ROOT = r"F:\catia_probe_v0385\cap"
INNER = 5.0
OUTER = 30.0
STEP = 72.0


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

    path = os.path.join(ROOT, "cap.CATPart")
    if os.path.isfile(path):
        os.remove(path)
    document.execute("catia_new_part", {"name": "CAP85", "file_path": path})
    part = conn.get_active_part()

    say("=== one 72-degree sector: a flat quadrilateral ===")
    corners = [point(INNER, 0), point(OUTER, 0), point(OUTER, STEP),
               point(INNER, STEP)]
    names = ["L1", "L2", "L3", "L4"]
    for i, name in enumerate(names):
        gsd.execute("catia_gsd_line", {"point1": corners[i],
                                       "point2": corners[(i + 1) % 4],
                                       "name": name})
    gsd.execute("catia_gsd_fill", {"boundaries": names, "name": "SECTOR"})
    part.Update()
    sector = part.FindObjectByName("SECTOR")
    say(f"  SECTOR area = {area_mm2(conn, part, sector):.3f} mm2"
        f" (expect {0.5 * (OUTER ** 2 - INNER ** 2) * math.radians(STEP):.3f})")

    say("")
    say("=== five of them about z ===")
    reply = gsd.execute("catia_gsd_circ_pattern",
                        {"element": "SECTOR", "count": 5, "total_angle": 360,
                         "join": True, "axis": "z", "name": "BASE"})
    say("  " + reply.replace("\n", "\n  ")[:600])
    part.Update()
    base = part.FindObjectByName("BASE")
    if base is None:
        say("  BASE missing - stopping")
        return
    say(f"  BASE area = {area_mm2(conn, part, base):.3f} mm2"
        f" (5 sectors = {5 * 0.5 * (OUTER ** 2 - INNER ** 2) * math.radians(STEP):.3f})")

    say("")
    say("=== 1. boundary of the JOIN result ===")
    try:
        reply = gsd.execute("catia_gsd_boundary",
                            {"surface": "BASE", "name": "BORDER"})
        say("  " + reply.replace("\n", "\n  ")[:400])
        border = part.FindObjectByName("BORDER")
        say(f"  BORDER length = {length_of(conn, part, border):.3f} mm")
    except Exception as error:  # noqa: BLE001
        say(f"  REFUSED {str(error).splitlines()[0][:200]}")

    say("")
    say("=== 2. inner loop through AddNewBoundary(edge, BASE, propagation) ===")
    from catia_mcp.topo_search import search_topology

    sel = conn.hso
    sel.Clear()
    house = part.HybridBodies.Item(1)
    say(f"  geometrical set: '{house.Name}'")
    sel.Add(house)
    search_topology(sel, "Edge")
    edges = []
    axis = None
    for i in range(1, int(sel.Count) + 1):
        try:
            item = sel.Item(i)
            nls = str(item.Value.Name)
        except Exception:  # noqa: BLE001
            continue
        if not nls.startswith("Selection_BorderREdge"):
            continue
        edges.append((i, item.Reference))
    say(f"  {len(edges)} surface-border edge(s)")

    # The axis for the distance reading: reuse the one the pattern built.
    axis_names = [n for n in ("SECTOR_axis_z", "BASE_axis_z")
                  if part.FindObjectByName(n) is not None]
    if axis_names:
        axis = part.CreateReferenceFromObject(
            part.FindObjectByName(axis_names[0]))
        say(f"  using axis '{axis_names[0]}'")

    hsf = part.HybridShapeFactory
    chosen = []
    for index, reference in edges:
        try:
            loop = hsf.AddNewBoundary(reference,
                                      part.CreateReferenceFromObject(base), 1)
            part.HybridBodies.Item(1).AppendHybridShape(loop)
            loop.Name = f"LOOP_{index}"
            part.UpdateObject(loop)
            part.Update()
            length = length_of(conn, part, loop)
            distance = None
            if axis is not None:
                try:
                    spa = conn.active_document.GetWorkbench("SPAWorkbench")
                    distance = float(
                        spa.GetMeasurable(part.CreateReferenceFromObject(loop))
                        .GetMinimumDistance(axis))
                except Exception:  # noqa: BLE001
                    pass
            say(f"  Edge.{index}: loop {length:.3f} mm, "
                f"{distance if distance is None else f'{distance:.3f}'} from axis")
            chosen.append((loop.Name, length, distance))
        except Exception as error:  # noqa: BLE001
            say(f"  Edge.{index}: REFUSED {str(error).splitlines()[0][:120]}")
            try:
                part.Update()
            except Exception:  # noqa: BLE001
                pass

    say("")
    say("=== 3. fill the innermost loop and join it ===")
    if chosen:
        innermost = min(chosen, key=lambda row: (row[2] is None, row[2] or 0))
        say(f"  innermost: {innermost}")
        try:
            reply = gsd.execute("catia_gsd_fill",
                                {"boundaries": [innermost[0]], "name": "CAP"})
            say("  " + reply.replace("\n", "\n  ")[:300])
            cap = part.FindObjectByName("CAP")
            say(f"  CAP area = {area_mm2(conn, part, cap):.3f} mm2"
                f" (hole area ~{0.5 * 5 * INNER ** 2 * math.sin(math.radians(72)):.3f})")
            reply = gsd.execute("catia_gsd_join",
                                {"elements": ["BASE", "CAP"], "name": "CLOSED"})
            say("  " + reply.replace("\n", "\n  ")[:300])
            closed = part.FindObjectByName("CLOSED")
            say(f"  CLOSED area = {area_mm2(conn, part, closed):.3f} mm2")
        except Exception as error:  # noqa: BLE001
            say(f"  REFUSED {str(error).splitlines()[0][:200]}")


if __name__ == "__main__":
    main()
