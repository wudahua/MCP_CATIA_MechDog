"""Controlled test of report 2026-10-30 §1: does a GSD write poison edge_anchors?

The reporter's own sample could not be used as-is: its anchor [0, 10, 20] lands
31.6 mm from the nearest edge of the file as saved, so their call is refused by
the anchor matcher before the factory is reached. This builds the same shape
from scratch, takes the anchor from an edge's own `point_on_edge` (so the
matching stage cannot be the excuse), and repeats one fillet at three moments:

    1. pure document                       anchors / name
    2. after one catia_gsd_point           anchors / name
    3. after deleting that point again     anchors / name

Every fillet that succeeds is deleted again, so each row starts from the same
solid. A fresh document is used; it is closed at the end without saving.

Usage:
    .venv/Scripts/python.exe scripts/fillet_anchor_matrix.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402


def verdict(reply: str) -> str:
    first = str(reply).splitlines()[0]
    if "Rejected" in reply or "0/" in first:
        return "REFUSED  " + first[:100]
    if "Fillet created" in reply or "edge(s) done" in reply:
        return "CREATED  " + first[:100]
    return "OTHER    " + first[:100]


def activate(conn, name: str):
    """Make `name` the active document, or stop.

    `catia_new_part` does not reliably leave the new part active in this
    session, and a mutation that runs while another document is active lands in
    THAT document. One probe run put three fillets into an acceptance user's
    finished part this way (2026-10-30). Every step that changes geometry now
    goes through here.
    """
    for index in range(1, conn.documents.Count + 1):
        candidate = conn.documents.Item(index)
        if candidate.Name == name:
            candidate.Activate()
            active = conn.active_document.Name
            if active != name:
                raise RuntimeError(f"could not activate {name} (active: {active})")
            return candidate
    raise RuntimeError(f"{name} is not open")


def safe_delete(tools, name: str) -> None:
    """Delete a feature if it is there; the numbering depends on what succeeded."""
    try:
        tools.execute("catia_delete_feature", {"feature_name": name})
    except Exception:  # noqa: BLE001 - absent is the normal case
        pass


def one_fillet(tools, arguments, label: str) -> str:
    try:
        reply = tools.execute("catia_fillet", arguments)
    except Exception as error:  # noqa: BLE001
        return f"RAISED   {label}: {str(error).splitlines()[0][:120]}"
    return verdict(reply)


def main() -> int:
    conn = CATIAConnection()
    print(conn.connect())
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    gsd = GSDTools(conn)

    workdir = tempfile.mkdtemp(prefix="fillet_anchor_")
    path = os.path.join(workdir, "AnchorMatrix.CATPart")
    documents.execute("catia_new_part", {"name": "AnchorMatrix", "file_path": path})
    activate(conn, os.path.basename(path))
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": -20, "y1": -10, "x2": 20, "y2": 10})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 20})

    listing = json.loads(part_design.execute("catia_list_edges", {"coords": True}))
    edges = listing.get("edges") or []
    usable = [e for e in edges if e.get("point_on_edge") and not e.get("point_on_edge_unknown")]
    if not usable:
        print("no edge reported point_on_edge; cannot test the anchor path")
        return 1
    target = usable[0]
    anchor = target["point_on_edge"]
    name = target.get("name")
    print(f"anchor taken from {name} point_on_edge = {anchor}")

    rows = []

    def record(label: str, outcome: str) -> None:
        rows.append((label, outcome))
        print(f"{label:38} {outcome}")
    record("1 pure document, anchors", one_fillet(
        part_design, {"radius": 5.0, "edge_anchors": [anchor]}, "anchors"))
    record("1 pure document, name", one_fillet(
        part_design, {"radius": 5.0, "edge_name": name}, "name"))
    for leftover in ("倒圆角.1", "倒圆角.2", "倒圆角.3", "倒圆角.4"):
        safe_delete(part_design, leftover)

    gsd.execute("catia_gsd_point", {"x": 100.0, "y": 0.0, "z": 0.0})
    record("2 after one gsd_point, anchors", one_fillet(
        part_design, {"radius": 5.0, "edge_anchors": [anchor]}, "anchors"))
    record("2 after one gsd_point, name", one_fillet(
        part_design, {"radius": 5.0, "edge_name": name}, "name"))
    for leftover in ("倒圆角.1", "倒圆角.2", "倒圆角.3", "倒圆角.4"):
        safe_delete(part_design, leftover)

    for element in ("点.1", "Point.1"):
        try:
            part_design.execute("catia_delete_feature", {"feature_name": element})
            break
        except Exception:  # noqa: BLE001
            continue
    rows.append(("3 after deleting the point, anchors", one_fillet(
        part_design, {"radius": 5.0, "edge_anchors": [anchor]}, "anchors")))
    rows.append(("3 after deleting the point, name", one_fillet(
        part_design, {"radius": 5.0, "edge_name": name}, "name")))

    print()
    for label, outcome in rows:
        print(f"{label:38} {outcome}")

    try:
        conn.active_document.Close()
    except Exception as error:  # noqa: BLE001
        print(f"(could not close: {str(error)[:90]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
