"""Can a face's own bounding box be had at all?

Measurable exposes 44 members and no bounding box (measured 2026-11-13); Inertia
on a face reference has none either. The remaining route is the face's own
VERTICES - and the note on record says a vertex reference answers 'GetPoint
failed' (2026-10-25, read the plain late-bound way). read_measurable_array exists
exactly because by-ref arrays do not marshal late-bound, so the question is
re-opened here with the VBS route, on a 60 x 40 x 40 block whose faces have known
corners.

    .venv/Scripts/python.exe scripts/b30_v0423_probe_face_verts.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402
from catia_mcp.tools.measurement import read_measurable_array  # noqa: E402
from catia_mcp.topo_search import search_topology, topology_cells  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0423")
ROOT = os.path.join(_PROBE_ROOT, "verts")


def say(line: str = "") -> None:
    print(line, flush=True)


def close_ours(conn: CATIAConnection) -> None:
    for index in range(int(conn.documents.Count), 0, -1):
        document = conn.documents.Item(index)
        try:
            full = str(document.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).startswith(os.path.abspath(_PROBE_ROOT)):
            try:
                document.Close()
            except Exception:  # noqa: BLE001
                pass


_VBS_POINT = (
    "Public Function catia_mcp_point(oMeasurable)\n"
    "    Dim p(2)\n"
    "    oMeasurable.GetPoint p\n"
    "    catia_mcp_point = p\n"
    "End Function\n"
)


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)

    path = os.path.join(ROOT, "Block.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": "Block", "file_path": path, "overwrite": True})
    for index in range(1, conn.documents.Count + 1):
        if conn.documents.Item(index).Name == "Block.CATPart":
            conn.documents.Item(index).Activate()
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 60, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 40})

    part = conn.get_active_part()
    body = part.MainBody
    spa = part.Parent.GetWorkbench("SPAWorkbench")
    cells = topology_cells(conn, body, "Face")
    sel = conn.hso
    for i, cell in enumerate(cells[:2], start=1):
        sel.Clear()
        sel.Add(cell.Value)
        try:
            search_topology(sel, "Vertex")
            say(f"  Face.{i}: {sel.Count} vertex/vertices under it")
            for j in range(1, min(sel.Count, 4) + 1):
                vertex = sel.Item(j)
                ref = vertex.Reference
                measurable = spa.GetMeasurable(ref)
                try:
                    raw = conn.app.SystemService.Evaluate(
                        _VBS_POINT, 0, "catia_mcp_point", [measurable])
                    say(f"    vertex {j} via VBS GetPoint = "
                        f"{[round(float(v), 3) for v in raw]}")
                except Exception as error:  # noqa: BLE001
                    say(f"    vertex {j} VBS GetPoint -> "
                        + str(error).splitlines()[0][:90])
                try:
                    raw = read_measurable_array(conn.app, measurable, "GetPoint", 3)
                    say(f"    vertex {j} via read_measurable_array = "
                        f"{[round(v, 3) for v in raw]}")
                except Exception as error:  # noqa: BLE001
                    say(f"    vertex {j} read_measurable_array -> "
                        + str(error).splitlines()[0][:90])
        except Exception as error:  # noqa: BLE001
            say(f"  Face.{i} vertex search -> " + str(error).splitlines()[0][:90])
        finally:
            sel.Clear()

    # a face's edges, and points along them
    sel.Clear()
    sel.Add(cells[0].Value)
    try:
        search_topology(sel, "Edge")
        say(f"  Face.1: {sel.Count} edge(s) under it")
        last = cells[0]
        sel.Clear()
        sel.Add(last.Value)
        search_topology(sel, "Edge")
        if sel.Count:
            edge_ref = sel.Item(1).Reference
            edge_measurable = spa.GetMeasurable(edge_ref)
            say(f"    first edge length = {float(edge_measurable.Length):.3f} mm")
            try:
                raw = read_measurable_array(conn.app, edge_measurable,
                                            "GetPointsOnCurve", 2)
                say(f"    GetPointsOnCurve(2) = {raw}")
            except Exception as error:  # noqa: BLE001
                say("    GetPointsOnCurve -> " + str(error).splitlines()[0][:100])
    except Exception as error:  # noqa: BLE001
        say("  face edge search -> " + str(error).splitlines()[0][:90])
    finally:
        sel.Clear()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
