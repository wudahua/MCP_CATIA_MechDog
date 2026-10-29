"""Does a GSD write really poison `catia_fillet(edge_anchors=...)`?

Report 2026-10-30 §1 claims the anchor path fails permanently once the document
has seen any HybridShapeFactory write, while `edge_name` still works, and that
the failure survives deleting the GSD elements and reopening the file. That
contradicts the 0.3.40 note ("not reproduced"), so it gets measured twice in the
SAME document: once with the GSD set present, once after deleting it, plus the
two tool paths as callers actually reach them.

The reply text matters here: `catia_fillet` reports a refused edge in its
message ("0/1 edge(s) done ... Rejected"), it does not raise, so "no exception"
is not "worked".

Usage:
    .venv/Scripts/python.exe scripts/fillet_anchor_probe.py "<part.CATPart>"
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.topo_search import topology_cells  # noqa: E402

ANCHOR = [0.0, 10.0, 20.0]


def geosets(part) -> list[str]:
    names = []
    try:
        for index in range(1, part.HybridBodies.Count + 1):
            names.append(str(part.HybridBodies.Item(index).Name))
    except Exception as error:  # noqa: BLE001
        names.append(f"<unreadable: {str(error)[:60]}>")
    return names


def factory_attempt(conn, part) -> str:
    """One raw factory call with a freshly enumerated reference."""
    cells = topology_cells(conn, part.MainBody, "Edge")
    if not cells:
        return "no edges"
    try:
        feature = part.ShapeFactory.AddNewSolidEdgeFilletWithConstantRadius(
            cells[0].Reference, 0, 5.0)
        part.UpdateObject(feature)
    except Exception as error:  # noqa: BLE001
        return "E_FAIL: " + str(error).splitlines()[0][:90]
    try:
        sel = conn.hso
        sel.Clear()
        sel.Add(feature)
        sel.Delete()
        sel.Clear()
        part.Update()
    except Exception as error:  # noqa: BLE001
        return f"created {feature.Name} but rollback failed ({str(error)[:60]})"
    return f"created {feature.Name} and rolled back"


def tool_attempt(tools, arguments) -> str:
    try:
        reply = tools.execute("catia_fillet", arguments)
    except Exception as error:  # noqa: BLE001
        return "raised: " + str(error).splitlines()[0][:120]
    return str(reply).splitlines()[0][:150]


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: fillet_anchor_probe.py <part.CATPart>")
        return 2
    path = os.path.abspath(sys.argv[1])
    conn = CATIAConnection()
    print(conn.connect())
    # Use the document if it is already open: opening a modified document that
    # is already in the session raises CATIA's "打开" dialog and blocks COM.
    document = None
    wanted = os.path.basename(path)
    for index in range(1, conn.documents.Count + 1):
        candidate = conn.documents.Item(index)
        if candidate.Name == wanted:
            document = candidate
            break
    if document is None:
        document = conn.documents.Open(path)
    document.Activate()
    documents = DocumentTools(conn)
    tools = PartDesignTools(conn)
    part = conn.get_active_part()

    print(f"\n== opened {document.Name} ==")
    print(f"geometrical sets: {geosets(part) or 'none'}")
    print(f"factory, GSD present   : {factory_attempt(conn, part)}")
    print(f"tool edge_anchors      : {tool_attempt(tools, {'radius': 5.0, 'edge_anchors': [ANCHOR]})}")
    print(f"tool edge_name Edge.1  : {tool_attempt(tools, {'radius': 5.0, 'edge_name': 'Edge.1'})}")

    removed = []
    for name in geosets(part):
        try:
            tools.execute("catia_delete_feature", {"feature_name": name})
            removed.append(name)
        except Exception as error:  # noqa: BLE001
            print(f"   could not delete {name}: {str(error)[:90]}")

    print(f"\n== after deleting {removed or 'nothing'} ==")
    print(f"geometrical sets: {geosets(part) or 'none'}")
    print(f"factory, GSD deleted   : {factory_attempt(conn, part)}")
    print(f"tool edge_anchors      : {tool_attempt(tools, {'radius': 5.0, 'edge_anchors': [ANCHOR]})}")
    print(f"tool edge_name Edge.1  : {tool_attempt(tools, {'radius': 5.0, 'edge_name': 'Edge.1'})}")

    # Left open on purpose: closing a modified document raises a save prompt
    # that blocks every later COM call. The probe changes are rolled back.
    print(f"\nleft {document.Name} open (probe changes rolled back)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
