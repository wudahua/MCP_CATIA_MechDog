"""0.3.84 probe 8: does hiding a geometrical set hide the solid too?

The 0.3.83 report followed this server's own advice ("hide the geometrical set
before taking a screenshot or exporting an STL") and the whole view went empty.
Hiding the set again brought the solid back, but also every construction curve
and a yellow highlight.

Three readings per step, because each one can lie on its own:

  * the show flag of the BODY, read through its own Selection (2-tuple on this
    build, the flag is element [1])
  * the show flag of the solid FEATURE inside it
  * the triangle count of the exported STL, which is what a measurement tool
    sees - geometry that is hidden is NOT in it, and a body that reads visible
    but exports nothing is the failure mode this is looking for

A screenshot is not taken here; the STL is the objective half, and the flag is
the one a caller can act on.
"""
from __future__ import annotations

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0384\vis"


def say(line: str = "") -> None:
    print(line, flush=True)


def triangles(path: str) -> int:
    """Triangle count of a binary STL, or -1 when it is not one."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return -1
    if size < 84:
        return -1
    with open(path, "rb") as handle:
        handle.seek(80)
        count = struct.unpack("<I", handle.read(4))[0]
    return count if 84 + count * 50 <= size + 50 else -1


def show_flag(selection, node) -> object:
    try:
        selection.Clear()
        selection.Add(node)
        value = selection.VisProperties.GetShow()
        selection.Clear()
        if isinstance(value, (tuple, list)):
            return tuple(value)
        return value
    except Exception as error:  # noqa: BLE001 - unreadable is an answer too
        return f"unreadable: {str(error).splitlines()[0][:50]}"


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    design = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    exporter = ExportTools(conn)

    own_root = os.path.normcase(os.path.abspath(r"F:\catia_probe_v0384"))
    for index in range(conn.documents.Count, 0, -1):
        try:
            stale = conn.documents.Item(index)
            full = os.path.normcase(os.path.abspath(str(stale.FullName)))
        except Exception:  # noqa: BLE001
            continue
        if full.startswith(own_root):
            stale.Close()

    path = os.path.join(ROOT, "vis.CATPart")
    if os.path.isfile(path):
        os.remove(path)
    document.execute("catia_new_part", {"name": "VIS", "file_path": path})
    part = conn.get_active_part()
    body = conn.get_active_part_body()
    selection = conn.active_document.Selection

    say("=== a solid, plus a geometrical set holding one surface ===")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 40, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    say("  " + design.execute("catia_pad", {"height": 10}).splitlines()[0][:120])
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 30], "point2": [40, 0, 30], "name": "EDGE30"})
    gsd.execute("catia_gsd_extrude",
                {"profile": "EDGE30", "direction": [0, 1, 0], "limit1": 40,
                 "name": "SURF30"})
    part.Update()

    stl = os.path.join(ROOT, "vis.stl")
    if os.path.isfile(stl):
        os.remove(stl)
    exporter.execute("catia_export", {"file_path": stl, "format": "stl"})

    pad = part.FindObjectByName("凸台.1") or part.FindObjectByName("Pad.1")
    say("")
    say("=== before hiding anything ===")
    say(f"  body flag          {show_flag(selection, body)}")
    say(f"  pad feature flag   {show_flag(selection, pad) if pad else 'no pad found'}")
    say("  STL triangles      %s (size %d B)" % (
        triangles(stl), os.path.getsize(stl) if os.path.isfile(stl) else 0))

    say("")
    say("=== set_visibility('GSD_Set', show=False) ===")
    say("  " + design.execute("catia_set_visibility",
                                {"name": "GSD_Set", "show": False}).splitlines()[0][:200])
    part.Update()
    say(f"  body flag          {show_flag(selection, body)}")
    say(f"  pad feature flag   {show_flag(selection, pad) if pad else 'n/a'}")
    if os.path.isfile(stl):
        os.remove(stl)
    exporter.execute("catia_export", {"file_path": stl, "format": "stl"})
    say("  STL triangles      %s (size %d B)" % (
        triangles(stl), os.path.getsize(stl) if os.path.isfile(stl) else 0))

    say("")
    say("=== set_visibility('零件几何体', show=True) - the reported dead end ===")
    try:
        reply = design.execute("catia_set_visibility",
                                 {"name": "零件几何体", "show": True})
        say("  " + reply.splitlines()[0][:200])
    except Exception as error:  # noqa: BLE001 - a refusal is a measurement
        say(f"  REFUSED {getattr(error, 'code', '')}: "
            + str(error).splitlines()[0][:160])
    part.Update()
    say(f"  body flag          {show_flag(selection, body)}")
    say(f"  pad feature flag   {show_flag(selection, pad) if pad else 'n/a'}")
    if os.path.isfile(stl):
        os.remove(stl)
    exporter.execute("catia_export", {"file_path": stl, "format": "stl"})
    say("  STL triangles      %s (size %d B)" % (
        triangles(stl), os.path.getsize(stl) if os.path.isfile(stl) else 0))

    say("")
    say("=== what the geometrical set's own children look like now ===")
    try:
        geoset = part.FindObjectByName("GSD_Set")
        for index in range(1, geoset.HybridShapes.Count + 1):
            shape = geoset.HybridShapes.Item(index)
            say(f"  {shape.Name:12s} {show_flag(selection, shape)}")
    except Exception as error:  # noqa: BLE001
        say(f"  unreadable: {str(error).splitlines()[0][:80]}")


if __name__ == "__main__":
    main()
