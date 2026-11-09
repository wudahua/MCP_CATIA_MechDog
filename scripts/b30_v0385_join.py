"""0.3.85 probe, part 5: does propagation cross a JOIN seam?

Part 4 showed propagation=1 returning a whole loop on a single revolve. On a
JOIN of two 180-degree halves the same border is made of two feature edges, and
an array is exactly that case five times over - so the question is whether the
selector stops at the seam (31.416 on the r=10 border) or walks the whole ring
(62.832).

Two measurements per result: LENGTH and DISTANCE TO AXIS. Length alone is
ambiguous here (a full r=10 ring and half an r=20 ring are both 62.832); the
distance says which radius the loop is at, so 62.832 at 10.0000 and 125.664 at
20.0000 are the two "whole ring" answers.

Every combination runs in its own document. Part 4's run ended with eight
identical UpdateObject failures because the first one left the part in an error
state - one document per question is the only way to read more than one answer.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.topo_search import search_topology  # noqa: E402

FULL_BOTTOM = 2 * math.pi * 10
FULL_TOP = 2 * math.pi * 20
HALF_BOTTOM = math.pi * 10
HALF_TOP = math.pi * 20
SEAM = math.sqrt(10 ** 2 + 30 ** 2)


def say(line: str = "") -> None:
    print(line, flush=True)


def length_of(conn, part, reference):
    try:
        spa = conn.active_document.GetWorkbench("SPAWorkbench")
        return float(spa.GetMeasurable(reference).Length)
    except Exception:  # noqa: BLE001
        return None


def area_mm2(conn, part, reference):
    try:
        spa = conn.active_document.GetWorkbench("SPAWorkbench")
        return float(spa.GetMeasurable(reference).Area) * 1e6
    except Exception:  # noqa: BLE001
        return None


def distance_to(conn, part, reference, other):
    try:
        spa = conn.active_document.GetWorkbench("SPAWorkbench")
        return float(spa.GetMeasurable(reference).GetMinimumDistance(other))
    except Exception:  # noqa: BLE001
        return None


def build_joined(conn):
    doc = conn.documents.Add("Part")
    part = doc.Part
    hsf = part.HybridShapeFactory
    geoset = part.HybridBodies.Add()
    geoset.Name = "GS"

    p_bottom = hsf.AddNewPointCoord(10.0, 0.0, 0.0)
    p_top = hsf.AddNewPointCoord(20.0, 0.0, 30.0)
    a_bottom = hsf.AddNewPointCoord(0.0, 0.0, -10.0)
    a_top = hsf.AddNewPointCoord(0.0, 0.0, 40.0)
    for point in (p_bottom, p_top, a_bottom, a_top):
        geoset.AppendHybridShape(point)
    line = hsf.AddNewLinePtPt(part.CreateReferenceFromObject(p_bottom),
                              part.CreateReferenceFromObject(p_top))
    geoset.AppendHybridShape(line)
    line.Name = "SPINE"
    part.UpdateObject(line)
    axis = hsf.AddNewLinePtPt(part.CreateReferenceFromObject(a_bottom),
                              part.CreateReferenceFromObject(a_top))
    geoset.AppendHybridShape(axis)
    axis.Name = "AXIS"
    part.UpdateObject(axis)

    cones = []
    for name in ("HALF_A", "HALF_B"):
        cone = hsf.AddNewRevol(part.CreateReferenceFromObject(line), 180.0, 0.0,
                               part.CreateReferenceFromObject(axis))
        geoset.AppendHybridShape(cone)
        cone.Name = name
        part.UpdateObject(cone)
        cones.append(cone)
    surface = hsf.AddNewJoin(part.CreateReferenceFromObject(cones[0]),
                             part.CreateReferenceFromObject(cones[1]))
    geoset.AppendHybridShape(surface)
    surface.Name = "SURF"
    part.UpdateObject(surface)
    part.Update()
    return doc, part, hsf, geoset, part.FindObjectByName("SURF"), axis


def border_edges(conn, part) -> list:
    sel = conn.hso
    sel.Clear()
    sel.Add(part.HybridBodies.Item("GS"))
    search_topology(sel, "Edge")
    found = []
    for i in range(1, int(sel.Count) + 1):
        try:
            item = sel.Item(i)
            nls = str(item.Value.Name)
        except Exception:  # noqa: BLE001
            continue
        if not nls.startswith("Selection_BorderREdge"):
            continue
        found.append((i, item.Reference))
    return found


def main() -> None:
    conn = CATIAConnection()
    conn.connect()

    # What the joined surface exposes, without creating anything that could
    # disturb it: one document, read only.
    doc, part, hsf, geoset, surface, axis = build_joined(conn)
    axis_ref = part.CreateReferenceFromObject(axis)
    surface_ref = part.CreateReferenceFromObject(surface)
    edges = border_edges(conn, part)
    inventory = []
    for index, reference in edges:
        inventory.append((index, length_of(conn, part, reference),
                          distance_to(conn, part, reference, axis_ref)))
    say("joined-surface border edges (index, length, distance to axis):")
    for index, length, distance in inventory:
        say(f"  Edge.{index}: {length:.4f} mm, {distance:.4f} mm from the axis")
    doc.Close()

    # One document per (edge, propagation): a refusal leaves the part unusable.
    picks = [row for row in inventory
             if row[2] is not None and abs(row[2] - 10.0) < 0.01] or inventory
    for index, length, distance in picks[:4]:
        for propagation in (1, 2):
            doc, part, hsf, geoset, surface, axis = build_joined(conn)
            axis_ref = part.CreateReferenceFromObject(axis)
            surface_ref = part.CreateReferenceFromObject(surface)
            target = None
            for i, reference in border_edges(conn, part):
                if i == index:
                    target = reference
                    break
            if target is None:
                say(f"  Edge.{index} propagation={propagation}: edge gone")
                doc.Close()
                continue
            try:
                loop = hsf.AddNewBoundary(target, surface_ref, propagation)
                geoset.AppendHybridShape(loop)
                loop.Name = "LOOP"
                part.UpdateObject(loop)
                part.Update()
                loop_ref = part.CreateReferenceFromObject(loop)
                got_len = length_of(conn, part, loop_ref)
                got_dist = distance_to(conn, part, loop_ref, axis_ref)
                verdict = "?"
                if got_len and got_dist is not None:
                    if abs(got_len - FULL_BOTTOM) < 0.05 and abs(got_dist - 10) < 0.05:
                        verdict = "WHOLE r=10 ring"
                    elif abs(got_len - FULL_TOP) < 0.05 and abs(got_dist - 20) < 0.05:
                        verdict = "WHOLE r=20 ring"
                    elif abs(got_len - HALF_BOTTOM) < 0.05:
                        verdict = "stopped at the seam (half r=10)"
                    elif abs(got_len - HALF_TOP) < 0.05:
                        verdict = "stopped at the seam (half r=20)"
                    else:
                        verdict = "neither"
                say(f"  Edge.{index} propagation={propagation}: "
                    f"{got_len:.4f} mm at {got_dist:.4f} -> {verdict}")
                try:
                    fill = hsf.AddNewFill()
                    fill.AddBound(loop_ref)
                    geoset.AppendHybridShape(fill)
                    fill.Name = "FILL"
                    part.UpdateObject(fill)
                    part.Update()
                    say(f"      fill area {area_mm2(conn, part, part.CreateReferenceFromObject(fill)):.3f}"
                        " mm2 (r=10 disc 314.159, r=20 disc 1256.637)")
                except Exception as error:  # noqa: BLE001
                    say(f"      fill REFUSED {str(error).splitlines()[0][:110]}")
            except Exception as error:  # noqa: BLE001
                say(f"  Edge.{index} propagation={propagation}: REFUSED "
                    f"{str(error).splitlines()[0][:130]}")
            finally:
                try:
                    doc.Close()
                except Exception:  # noqa: BLE001
                    pass


if __name__ == "__main__":
    main()
