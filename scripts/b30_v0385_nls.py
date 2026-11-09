"""0.3.85 probe, part 3: how an edge says which feature it belongs to.

An edge picked out of a geometrical-set search is a position, not an identity:
Edge.N is renumbered by every feature. For a loop selector to be usable, the
reply has to be able to say "these are the edges of THIS surface", and the only
cheap handle CATIA offers is the NLS path, which names the feature that produced
the element. This prints it, and checks whether a renamed feature appears in it
by the name the caller knows or by CATIA's own numbering.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.topo_search import search_topology  # noqa: E402


def say(line: str = "") -> None:
    print(line, flush=True)


def main() -> None:
    conn = CATIAConnection()
    conn.connect()
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
    cone = hsf.AddNewRevol(part.CreateReferenceFromObject(line), 360.0, 0.0,
                           part.CreateReferenceFromObject(axis))
    geoset.AppendHybridShape(cone)
    cone.Name = "MY_CONE"
    part.UpdateObject(cone)
    part.Update()

    sel = conn.hso
    sel.Clear()
    sel.Add(geoset)
    search_topology(sel, "Edge")
    say(f"{sel.Count} edge(s):")
    for i in range(1, int(sel.Count) + 1):
        item = sel.Item(i)
        try:
            nls = str(item.Value.Name)
        except Exception as error:  # noqa: BLE001
            nls = f"<no name: {str(error).splitlines()[0][:40]}>"
        try:
            length = float(conn.active_document.GetWorkbench("SPAWorkbench")
                           .GetMeasurable(item.Reference).Length)
        except Exception:  # noqa: BLE001
            length = None
        say(f"  Edge.{i}  len={length}")
        say(f"           nls={nls[:220]}")
        say(f"           has MY_CONE = {'MY_CONE' in nls}"
            f", has Revol = {'Revol' in nls}")
    sel.Clear()
    doc.Close()


if __name__ == "__main__":
    main()
