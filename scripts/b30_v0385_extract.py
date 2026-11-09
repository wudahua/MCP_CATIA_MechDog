"""0.3.85 probe, part 9: can the inner edge be found without being told its name?

Part 8 closed the hole exactly (pentagon border 29.3893, cap 59.441, closed base
2139.877 = 2080.436 + 59.441), but it was given the name 'L4'. A caller building
a bottle base does not know which of its four sketch lines is the inner one, so
cap_center has to find it, and the rule has to be geometric:

    the border edge whose nearest point to the axis is SMALLEST

On that sector the four edges measure 5.000, 24.271, 5.000, 4.045 from the axis.
The inner CHORD is the only 4.045 (the middle of a chord is closer than its ends),
so the rule picks it out - but only if a single edge can be extracted at all.

That is the question here: ``AddNewExtract(edge)`` with PropagationType=1 on an
edge that belongs to a geometrical set (not to a solid body, which is the only
case ``_topology_reference`` handles today). PropagationType=1 means "this edge
only"; anything else walks the tangent chain and hands back the whole ring.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.topo_search import search_topology  # noqa: E402

ROOT = r"F:\catia_probe_v0385\ext"
INNER = 5.0
OUTER = 30.0
STEP = 72.0


def say(line: str = "") -> None:
    print(line, flush=True)


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

    path = os.path.join(ROOT, "ext.CATPart")
    if os.path.isfile(path):
        os.remove(path)
    document.execute("catia_new_part", {"name": "EXT85", "file_path": path})
    part = conn.get_active_part()
    hsf = part.HybridShapeFactory

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
    say(f"SECTOR ready; its four border edges should measure "
        f"{OUTER - INNER:.3f}, {OUTER * 2 * math.sin(math.radians(36)):.3f}, "
        f"{OUTER - INNER:.3f}, {INNER * 2 * math.sin(math.radians(36)):.3f}")

    sel = conn.hso
    sel.Clear()
    house = part.HybridBodies.Item(1)
    sel.Add(house)
    search_topology(sel, "Edge")
    edges = []
    for i in range(1, int(sel.Count) + 1):
        try:
            item = sel.Item(i)
            nls = str(item.Value.Name)
        except Exception:  # noqa: BLE001
            continue
        if nls.startswith("Selection_BorderREdge"):
            edges.append((i, item.Reference))
    say(f"{len(edges)} border edge(s) in '{house.Name}'")

    axis = part.CreateReferenceFromObject(part.FindObjectByName("L1"))
    spa = conn.active_document.GetWorkbench("SPAWorkbench")
    house = part.HybridBodies.Item(1)

    say("")
    say("=== extract each edge with PropagationType=1 ===")
    best = None
    for index, reference in edges:
        try:
            extract = hsf.AddNewExtract(reference)
            extract.PropagationType = 1
            house.AppendHybridShape(extract)
            extract.Name = f"EX_{index}"
            part.UpdateObject(extract)
            part.Update()
            extract_ref = part.CreateReferenceFromObject(extract)
            length = float(spa.GetMeasurable(extract_ref).Length)
            # Distance to the z axis, built as a line so Measurable can take it.
            distance = float(spa.GetMeasurable(extract_ref)
                             .GetMinimumDistance(axis))
            say(f"  EX_{index}: {length:.4f} mm, {distance:.4f} from the axis")
            if best is None or distance < best[1]:
                best = (extract.Name, distance, length)
        except Exception as error:  # noqa: BLE001
            say(f"  EX_{index}: REFUSED {str(error).splitlines()[0][:130]}")
            try:
                part.Update()
            except Exception:  # noqa: BLE001
                pass

    say("")
    if best:
        say(f"nearest to the axis: {best[0]} at {best[1]:.4f} mm, "
            f"length {best[2]:.4f} (the inner chord is "
            f"{INNER * 2 * math.sin(math.radians(36)):.4f} at "
            f"{INNER * math.cos(math.radians(36)):.4f})")
    else:
        say("nothing could be extracted - cap_center will need a named curve")


if __name__ == "__main__":
    main()
