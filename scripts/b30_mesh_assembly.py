"""Controlled reproduction of the 2026-11-01 report, on CATIA B30.

The report (`2026-11-01_CATIA_0.3.45_装配级mesh包络崩溃.md`, defect 45-1) says
that on an ASSEMBLY every `method='mesh'` route dies with

    TypeError: 'float' object is not subscriptable

after the STL export has already been paid for. It is a claim about a crash, so
the only way to settle it is to run the call and read the reply - which is what
0.3.45 forgot to do, having tested `_part_box_entry` on its own and never the
walk that consumes it.

Two parts, deliberately different kinds of solid:

    MESH_BOX   40 x 20 x 20 pad          - every extreme is a vertex
    MESH_DRUM  cylinder R12, h 10        - the extremes are on the curved face,
                                           where a vertex sweep is short by the
                                           whole radius

Then the same product is measured four ways and the numbers are printed, not
argued about:

    whole product, method='mesh'      (the reported crash)
    one component, method='mesh'      (the reported crash, slower)
    whole product, method='vertices'  (the control - never crashed)
    one component, method='vertices'

Every document it touches is CREATED here and closed at the end without saving.
Open documents are listed, never edited, and every geometry step goes through
`activate()` - the guard added after a probe run put three fillets into an
acceptance user's finished part (2026-10-30).

Usage, from the repo root, with the venv that has pywin32:
    .venv/Scripts/python.exe scripts/b30_mesh_assembly.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

findings: list[str] = []
made_documents: list[str] = []


def say(line: str) -> None:
    print(line)
    findings.append(line)


def activate(conn, name: str):
    """Make `name` the active document, or stop rather than write elsewhere."""
    for index in range(1, conn.documents.Count + 1):
        candidate = conn.documents.Item(index)
        if candidate.Name == name:
            candidate.Activate()
            active = conn.active_document.Name
            if active != name:
                raise RuntimeError(f"could not activate {name} (active: {active})")
            return candidate
    raise RuntimeError(f"{name} is not open")


def build_box(sketcher, part_design, path: str) -> None:
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": -20, "y1": -10, "x2": 20, "y2": 10})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 20})


def build_drum(sketcher, part_design, path: str) -> None:
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": 12})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})


def box_of(measurement, label: str, arguments: dict) -> dict:
    """Run one bounding-box call and print what came back, crashed or not."""
    try:
        raw = measurement.execute("catia_get_bounding_box", arguments)
        reply = json.loads(raw)
    except Exception as error:  # noqa: BLE001 - the failure IS the datum
        say(f"{label:34} CRASH  {str(error).splitlines()[0][:110]}")
        return {}
    dims = reply.get("dimensions") or {}
    low = reply.get("min") or {}
    high = reply.get("max") or {}
    source = reply.get("source")
    say(f"{label:34} ok     "
        f"x[{low.get('x')}, {high.get('x')}] "
        f"y[{low.get('y')}, {high.get('y')}] "
        f"z[{low.get('z')}, {high.get('z')}]  "
        f"dims={ {k: dims[k] for k in ('length_x', 'length_y', 'length_z') if k in dims} }"
        f"  source={source}")
    if "part_boxes_by_mesh" in reply:
        counts = (reply.get("part_boxes_by_mesh"), reply.get("part_boxes_by_vertices"),
                  reply.get("part_boxes"))
        say(f"{'':34} counters (by_mesh, by_vertices, part_boxes) = {counts}"
            f"  {'ADD UP' if counts[0] + counts[1] == counts[2] else 'DO NOT ADD UP'}")
    if reply.get("bounds"):
        say(f"{'':34} bounds: {reply['bounds'][:110]}")
    return reply


def main() -> int:
    conn = CATIAConnection()
    say(conn.connect())
    opened = [conn.documents.Item(i).Name
              for i in range(1, conn.documents.Count + 1)]
    say(f"open before: {', '.join(opened) or 'none'}")

    document = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    assembly = AssemblyTools(conn)
    measurement = MeasurementTools(conn)

    workdir = tempfile.mkdtemp(prefix="catia_mesh_repro_")
    box_path = os.path.join(workdir, "MESH_BOX.CATPart")
    drum_path = os.path.join(workdir, "MESH_DRUM.CATPart")
    asm_path = os.path.join(workdir, "MESH_ASM.CATProduct")

    try:
        document.execute("catia_new_part", {"name": "MESH_BOX",
                                           "file_path": box_path})
        made_documents.append("MESH_BOX.CATPart")
        activate(conn, "MESH_BOX.CATPart")
        build_box(sketcher, part_design, box_path)
        say("MESH_BOX built: 40 x 20 x 20 pad")

        document.execute("catia_new_part", {"name": "MESH_DRUM",
                                           "file_path": drum_path})
        made_documents.append("MESH_DRUM.CATPart")
        activate(conn, "MESH_DRUM.CATPart")
        build_drum(sketcher, part_design, drum_path)
        say("MESH_DRUM built: cylinder R12, h 10")

        document.execute("catia_new_product", {"name": "MESH_ASM",
                                               "file_path": asm_path})
        made_documents.append("MESH_ASM.CATProduct")
        activate(conn, "MESH_ASM.CATProduct")
        assembly.execute("catia_add_component", {"file_path": box_path})
        assembly.execute("catia_add_component", {"file_path": drum_path,
                                                 "count": 2})
        listing = json.loads(assembly.execute("catia_list_components",
                                              {"names_only": True}))
        names = listing.get("names") or [
            entry.get("name") for entry in listing.get("components", [])]
        say(f"assembly: {len(names)} instance(s) {names}")

        drum_instance = next((n for n in names if n.upper().startswith("MESH_DRUM")),
                            None)
        if drum_instance:
            assembly.execute("catia_set_component_transform", {
                "placements": [{"component_name": drum_instance,
                                "translation": [100.0, 0.0, 50.0]}]})
            say(f"{drum_instance} placed at (100, 0, 50)")
    except Exception as error:  # noqa: BLE001
        say(f"setup failed: {str(error).splitlines()[0][:160]}")
        return 1

    say("")
    say("── the reported crash, and the control that never crashed ──")
    whole_mesh = box_of(measurement, "whole product, method=mesh",
                        {"document": "MESH_ASM.CATProduct", "method": "mesh"})
    whole_vertices = box_of(measurement, "whole product, method=vertices",
                            {"document": "MESH_ASM.CATProduct",
                             "method": "vertices"})
    if drum_instance:
        box_of(measurement, f"component {drum_instance}, mesh",
               {"component": drum_instance, "method": "mesh"})
        box_of(measurement, f"component {drum_instance}, vertices",
               {"component": drum_instance, "method": "vertices"})

    say("")
    if whole_mesh and whole_vertices:
        say("VERDICT: the crash is gone and the control is unchanged")
    elif not whole_mesh:
        say("VERDICT: STILL CRASHING - the report stands")
    try:
        say("mesh - vertices (whole product): "
            + json.dumps({k: round(whole_mesh["dimensions"][k]
                                   - whole_vertices["dimensions"][k], 4)
                          for k in ("length_x", "length_y", "length_z")
                          if k in whole_mesh.get("dimensions", {})}))
    except Exception:  # noqa: BLE001 - only printed when both replies exist
        pass

    # ── cleanup: only the documents this script created ─────────────────
    closed = []
    for name in reversed(made_documents):
        try:
            for index in range(1, conn.documents.Count + 1):
                candidate = conn.documents.Item(index)
                if candidate.Name == name:
                    candidate.Close()
                    closed.append(name)
                    break
        except Exception as error:  # noqa: BLE001
            say(f"cleanup: could not close {name} ({str(error)[:80]})")
    say(f"closed {len(closed)}/{len(made_documents)} created document(s)")
    remaining = [conn.documents.Item(i).Name
                 for i in range(1, conn.documents.Count + 1)]
    say(f"open at the end: {', '.join(remaining) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
