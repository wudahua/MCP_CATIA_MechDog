"""Does a FACE answer geometry questions of its own?

list_faces publishes area only. The reason on record is that
``GetMeasurable(<hybrid reference>)`` measures the SOLID that carries the
reference (measured 2026-11-10: an offset surface's COG read (0,0,5), the block's
centre). A TOPOLOGICAL face may behave differently, and that has never been
asked. One 60 x 40 x 40 block: body COG is (30,20,20), the bottom face's own
centroid is (30,20,0), so the two answers cannot be confused.

    .venv/Scripts/python.exe scripts/b30_v0423_probe_faces.py
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
from catia_mcp.topo_search import topology_cells  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0423")
ROOT = os.path.join(_PROBE_ROOT, "faces")


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


def members(obj) -> list[str]:
    try:
        info = obj._oleobj_.GetTypeInfo()
        count = info.GetTypeAttr().cFuncs
    except Exception as error:  # noqa: BLE001
        return ["typelib unreadable: " + str(error).splitlines()[0][:60]]
    out = []
    for index in range(count):
        try:
            desc = info.GetFuncDesc(index)
            out.append(info.GetNames(desc.memid)[0])
        except Exception:  # noqa: BLE001
            continue
    return out


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
    say(part_design.execute("catia_pad", {"height": 40})[:80])

    part = conn.get_active_part()
    body = part.MainBody
    spa = part.Parent.GetWorkbench("SPAWorkbench")
    body_ref = part.CreateReferenceFromObject(body)
    say("  body COG = " + str(read_measurable_array(
        conn.app, spa.GetMeasurable(body_ref), "GetCOG", 3)))
    measurable_names = members(spa.GetMeasurable(body_ref))
    say(f"  Measurable members ({len(measurable_names)}): {measurable_names}")

    cells = topology_cells(conn, body, "Face")
    say(f"  {len(cells)} face(s)")
    for i, cell in enumerate(cells, start=1):
        measurable = spa.GetMeasurable(cell.Reference)
        try:
            area = f"{float(measurable.Area) * 1e6:.1f}"
        except Exception as error:  # noqa: BLE001
            area = "ERR " + str(error).splitlines()[0][:40]
        try:
            cog = [round(v, 3) for v in read_measurable_array(
                conn.app, measurable, "GetCOG", 3)]
        except Exception as error:  # noqa: BLE001
            cog = "ERR " + str(error).splitlines()[0][:50]
        try:
            plane = [round(v, 3) for v in read_measurable_array(
                conn.app, measurable, "GetPlane", 9)]
        except Exception as error:  # noqa: BLE001
            plane = "ERR " + str(error).splitlines()[0][:50]
        say(f"  Face.{i} area={area} cog={cog}")
        say(f"          plane={plane}")
    # bounding box routes, asked once on the first face
    first = spa.GetMeasurable(cells[0].Reference)
    for route, obj in (("measurable", first),):
        try:
            box = read_measurable_array(conn.app, obj, "GetBoundingBox", 6)
            say(f"  {route}.GetBoundingBox = {[round(v, 3) for v in box]}")
        except Exception as error:  # noqa: BLE001
            say(f"  {route}.GetBoundingBox -> " + str(error).splitlines()[0][:80])
    try:
        inertia = spa.Inertias.Add(cells[0].Reference)
        box = read_measurable_array(conn.app, inertia, "GetBoundingBox", 6)
        say(f"  Inertia(face).GetBoundingBox = {[round(v, 3) for v in box]}")
    except Exception as error:  # noqa: BLE001
        say("  Inertia(face).GetBoundingBox -> " + str(error).splitlines()[0][:80])
    try:
        sel = conn.hso
        sel.Clear()
        sel.Add(cells[0].Value)
        sel.Search("Topology.Vertex,sel")
        say(f"  vertices found under Face.1: {sel.Count}")
        sel.Clear()
    except Exception as error:  # noqa: BLE001
        say("  vertex search -> " + str(error).splitlines()[0][:80])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
