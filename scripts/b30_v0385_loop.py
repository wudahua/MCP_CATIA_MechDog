"""0.3.85 probe, part 4: the loop selector on a surface an array would produce.

Part 1 measured ``AddNewBoundary(edge, surface, 1)`` returning ONE loop on a
revolve. Two things still decide whether a wrapper is honest:

  * A joined surface's border circle is made of TWO feature edges. Does
    propagation walk across that seam and come back with the whole circle
    (62.832), or stop at the seam (31.416)? A five-lobe array is exactly this
    case, five times over, so a selector that stops at seams is useless there.
  * Edges of WIREFRAME elements sit in the same search and are the same length as
    border circles (a GSD line's edge measured 50 mm here). Picking "the edge of
    length 62.832" therefore finds the right number on the wrong element. The NLS
    path is the only cheap discriminator: ``Selection_BorderREdge`` is a surface
    border, ``Selection_WireREdge`` is a line's own edge - and the feature NAME
    the caller knows ('MY_CONE') is nowhere in it (CATIA writes GSMRevol.1), so
    name matching cannot be used.

All edges are taken BEFORE any feature is created: every successful boundary adds
elements to the set, which renumbers what the next search returns.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.topo_search import search_topology  # noqa: E402

BOTTOM = 2 * math.pi * 10
TOP = 2 * math.pi * 20
HALF_BOTTOM = math.pi * 10
HALF_TOP = math.pi * 20
LATERAL = math.pi * (10 + 20) * math.sqrt(10 ** 2 + 30 ** 2)


def say(line: str = "") -> None:
    print(line, flush=True)


def length_of(conn, part, reference) -> float | None:
    try:
        spa = conn.active_document.GetWorkbench("SPAWorkbench")
        return float(spa.GetMeasurable(reference).Length)
    except Exception:  # noqa: BLE001
        return None


def area_mm2(conn, part, reference) -> float | None:
    try:
        spa = conn.active_document.GetWorkbench("SPAWorkbench")
        return float(spa.GetMeasurable(reference).Area) * 1e6
    except Exception:  # noqa: BLE001
        return None


def distance_to(conn, part, reference, other) -> float | None:
    try:
        spa = conn.active_document.GetWorkbench("SPAWorkbench")
        return float(spa.GetMeasurable(reference).GetMinimumDistance(other))
    except Exception:  # noqa: BLE001
        return None


def build(conn, halves: bool):
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

    if halves:
        cones = []
        for name in ("HALF_A", "HALF_B"):
            cone = hsf.AddNewRevol(part.CreateReferenceFromObject(line), 180.0,
                                   0.0,
                                   part.CreateReferenceFromObject(axis))
            geoset.AppendHybridShape(cone)
            cone.Name = name
            part.UpdateObject(cone)
            cones.append(cone)
        surface = hsf.AddNewJoin(part.CreateReferenceFromObject(cones[0]),
                                 part.CreateReferenceFromObject(cones[1]))
    else:
        surface = hsf.AddNewRevol(part.CreateReferenceFromObject(line), 360.0,
                                  0.0,
                                  part.CreateReferenceFromObject(axis))
    geoset.AppendHybridShape(surface)
    surface.Name = "SURF"
    part.UpdateObject(surface)
    part.Update()
    return doc, part, hsf, geoset, part.FindObjectByName("SURF"), axis


def border_edges(conn) -> list:
    """Surface-border edges only, with their references kept alive."""
    sel = conn.hso
    sel.Clear()
    sel.Add(conn.get_active_part().HybridBodies.Item("GS"))
    search_topology(sel, "Edge")
    found = []
    for i in range(1, int(sel.Count) + 1):
        try:
            item = sel.Item(i)
            nls = str(item.Value.Name)
        except Exception:  # noqa: BLE001
            continue
        if not nls.startswith("Selection_BorderREdge"):
            continue      # a line's own edge, or a datum plane border
        found.append((i, item.Reference, nls))
    return found


def main() -> None:
    conn = CATIAConnection()
    conn.connect()

    for halves in (False, True):
        label = "JOIN of two 180 deg halves" if halves else "single revolved cone"
        say(f"=== {label} ===")
        doc, part, hsf, geoset, surface, axis = build(conn, halves)
        if halves:
            say(f"  joined area = {area_mm2(conn, part, part.CreateReferenceFromObject(surface)):.3f}"
                f" (lateral {LATERAL:.3f})")
        surface_ref = part.CreateReferenceFromObject(surface)
        axis_ref = part.CreateReferenceFromObject(axis)

        whole_ref = None
        try:
            whole = hsf.AddNewBoundaryOfSurface(surface_ref)
            geoset.AppendHybridShape(whole)
            whole.Name = "WHOLE"
            part.UpdateObject(whole)
            part.Update()
            whole_ref = part.CreateReferenceFromObject(whole)
            say(f"  boundary(surface): length "
                f"{length_of(conn, part, whole_ref):.4f}, distance to axis "
                f"{distance_to(conn, part, whole_ref, axis_ref):.4f}")
        except Exception as error:  # noqa: BLE001 - a joined face may refuse it
            say(f"  boundary(surface) REFUSED {str(error).splitlines()[0][:130]}")
            try:
                part.Update()
            except Exception:  # noqa: BLE001
                pass

        # Every border edge is taken FIRST; creating features renumbers the rest.
        edges = border_edges(conn)
        say(f"  {len(edges)} surface-border edge(s): "
            + ", ".join(f"Edge.{i} ({length_of(conn, part, r):.3f})"
                        for i, r, _n in edges))
        for i, reference, _nls in edges:
            length = length_of(conn, part, reference)
            try:
                loop = hsf.AddNewBoundary(reference, surface_ref, 1)
                geoset.AppendHybridShape(loop)
                loop.Name = f"LOOP_{i}"
                part.UpdateObject(loop)
                part.Update()
                got = length_of(conn, part,
                                part.CreateReferenceFromObject(loop))
                say(f"  AddNewBoundary(Edge.{i}, SURF, 1): {got:.4f} mm "
                    f"(edge was {length:.4f})"
                    + ("  <- WHOLE loop" if got and length and
                       abs(got - length) < 0.05 and
                       abs(got - HALF_BOTTOM) > 0.05 and
                       abs(got - HALF_TOP) > 0.05 else ""))
                try:
                    fill = hsf.AddNewFill()
                    fill.AddBound(part.CreateReferenceFromObject(loop))
                    geoset.AppendHybridShape(fill)
                    fill.Name = f"FILL_{i}"
                    part.UpdateObject(fill)
                    part.Update()
                    say(f"      fill of it: "
                        f"{area_mm2(conn, part, part.CreateReferenceFromObject(fill)):.3f} mm2")
                except Exception as error:  # noqa: BLE001
                    say(f"      fill REFUSED {str(error).splitlines()[0][:110]}")
            except Exception as error:  # noqa: BLE001 - not this surface's edge
                say(f"  AddNewBoundary(Edge.{i}, SURF, 1): REFUSED "
                    f"{str(error).splitlines()[0][:110]}")
        doc.Close()
        say("")


if __name__ == "__main__":
    main()
