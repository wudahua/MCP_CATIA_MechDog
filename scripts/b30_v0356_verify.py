"""End-to-end check of the two new tools, through the tool layer.

The probes proved the COM calls. This drives catia_gsd_fillet_surface and
catia_gsd_spine exactly as an agent would, and then measures the result the same
way the probes did, because a wrapper can lose the geometry the probe found:

  1. 90 deg wedge, r=5        -> one fillet, every vertex 5.0000 mm from the axis
  2. disjoint cylinders       -> refused, and nothing left in the tree
  3. disc + standing cylinder -> refused (two surfaces), tree left clean
  4. spine across tilted rings-> Length 37.9224 mm, same as the raw call
  5. spine with one section   -> refused before anything is created
"""

from __future__ import annotations

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection
from catia_mcp.results import ArgumentRejected, CodedError
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.measurement import MeasurementTools, _stl_triangles
from catia_mcp.tools.part_design import PartDesignTools

OUT_DIR = os.path.join(tempfile.gettempdir(), "b30_v0356")


def say(text: str = "") -> None:
    print(text, flush=True)


def fresh(conn, document, tag: str):
    document.execute("catia_new_part",
                     {"name": tag, "file_path": os.path.join(OUT_DIR, f"{tag}.CATPart")})
    assert str(conn.active_document.Name).startswith(tag), conn.active_document.Name
    return conn.get_active_part()


def close_tag(conn, document, tag: str) -> None:
    for index in range(conn.documents.Count, 0, -1):
        try:
            if str(conn.documents.Item(index).Name).startswith(tag):
                conn.documents.Item(index).Activate()
                document.execute("catia_close_document", {"save": False})
        except Exception:  # noqa: BLE001
            pass


def mesh(part, measurement, part_design, hide: list) -> list:
    for node in hide:
        try:
            part_design.execute("catia_set_visibility", {"name": node, "show": False})
        except Exception:  # noqa: BLE001
            pass
    part.Update()
    box: dict = {}

    def visit(path: str) -> None:
        box["triangles"] = _stl_triangles(path, 400000)

    measurement._mesh_export(part, limit=None, visit=visit)
    vertices = []
    for _normal, tri in box.get("triangles") or []:
        vertices.extend(tri)
    return vertices


def names_in_set(part) -> list:
    out = []
    for hb_index in range(1, part.HybridBodies.Count + 1):
        body = part.HybridBodies.Item(hb_index)
        try:
            for index in range(1, body.HybridShapes.Count + 1):
                out.append(str(body.HybridShapes.Item(index).Name))
        except Exception:  # noqa: BLE001
            pass
    return out


def case_fillet_wedge(conn, document, gsd, measurement, part_design) -> None:
    tag = "VWEDGE"
    say("\n===== 1. 90 deg wedge through catia_gsd_fillet_surface =====")
    part = fresh(conn, document, tag)
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [60, 0, 0],
                                   "name": f"{tag}_LA"})
    gsd.execute("catia_gsd_extrude", {"profile": f"{tag}_LA", "direction": [0, 0, 1],
                                      "limit1": 40.0, "name": f"{tag}_A"})
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [0, 60, 0],
                                   "name": f"{tag}_LB"})
    gsd.execute("catia_gsd_extrude", {"profile": f"{tag}_LB", "direction": [0, 0, 1],
                                      "limit1": 40.0, "name": f"{tag}_B"})
    reply = gsd.execute("catia_gsd_fillet_surface",
                        {"surface1": f"{tag}_A", "surface2": f"{tag}_B",
                         "radius": 5.0, "name": f"{tag}_FILLET"})
    say("  " + reply[:400])
    vertices = mesh(part, measurement, part_design,
                    [f"{tag}_A", f"{tag}_B", f"{tag}_LA", f"{tag}_LB"])
    radial = [math.hypot(x - 5.0, y - 5.0) for x, y, _z in vertices]
    zs = [z for _x, _y, z in vertices]
    say(f"  vertices {len(vertices)}, radius from (5,5): "
        f"min {min(radial):.4f} max {max(radial):.4f}  (asked 5)")
    say(f"  z span {min(zs):.2f}..{max(zs):.2f} (edge 0..40)")
    say(f"  tree: {names_in_set(part)}")
    close_tag(conn, document, tag)


def case_fillet_disjoint(conn, document, gsd) -> None:
    tag = "VDISJ"
    say("\n===== 2. two disjoint cylinders: must be refused =====")
    part = fresh(conn, document, tag)
    for x, name in ((0, f"{tag}_C1"), (30, f"{tag}_C2")):
        gsd.execute("catia_gsd_circle", {"center": [x, 0, 0], "support_plane": "xy",
                                         "radius": 10.0, "name": name})
        gsd.execute("catia_gsd_extrude", {"profile": name, "direction": [0, 0, 1],
                                          "limit1": 30.0, "name": name + "_S"})
    try:
        gsd.execute("catia_gsd_fillet_surface",
                    {"surface1": f"{tag}_C1_S", "surface2": f"{tag}_C2_S",
                     "radius": 5.0})
        say("  UNEXPECTED: it built something")
    except (RuntimeError, CodedError) as error:
        say(f"  refused: {str(error).splitlines()[0][:110]}")
        say(f"  refused with {len(str(error).splitlines())} lines of diagnostics")
    say(f"  tree: {names_in_set(part)}")
    try:
        part.Update()
        say("  part.Update() still OK - no wreckage left behind")
    except Exception as error:  # noqa: BLE001
        say(f"  part.Update() FAILS: {str(error).splitlines()[0][:70]}")
    close_tag(conn, document, tag)


def case_fillet_ambiguous(conn, document, gsd) -> None:
    tag = "VAMBIG"
    say("\n===== 3. disc + standing cylinder: two surfaces, must refuse =====")
    part = fresh(conn, document, tag)
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": 30.0, "name": f"{tag}_C30"})
    gsd.execute("catia_gsd_fill", {"boundaries": [f"{tag}_C30"],
                                   "name": f"{tag}_DISC"})
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": 10.0, "name": f"{tag}_C10"})
    gsd.execute("catia_gsd_extrude", {"profile": f"{tag}_C10",
                                      "direction": [0, 0, 1], "limit1": 40.0,
                                      "name": f"{tag}_BOSS"})
    try:
        gsd.execute("catia_gsd_fillet_surface",
                    {"surface1": f"{tag}_DISC", "surface2": f"{tag}_BOSS",
                     "radius": 5.0})
        say("  UNEXPECTED: it picked one")
    except ArgumentRejected as error:
        say(f"  refused (ArgumentRejected): {str(error).splitlines()[0][:110]}")
        say(f"  ... {str(error).splitlines()[-1][:110]}")
    except CodedError as error:
        say(f"  refused (CodedError): {str(error).splitlines()[0][:110]}")
    names = names_in_set(part)
    say(f"  tree: {names}")
    say(f"  fillet attempts left behind: "
        f"{[n for n in names if 'FilletTry' in n or 'fillet' in n.lower()]}")
    try:
        part.Update()
        say("  part.Update() still OK")
    except Exception as error:  # noqa: BLE001
        say(f"  part.Update() FAILS: {str(error).splitlines()[0][:70]}")
    close_tag(conn, document, tag)


def case_spine(conn, document, gsd) -> None:
    tag = "VSPINE"
    say("\n===== 4. spine across tilted rings through catia_gsd_spine =====")
    fresh(conn, document, tag)
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": 10.0, "name": f"{tag}_C1"})
    for index, point in enumerate(([0, 0, 40], [20, 0, 40], [0, 20, 60]), start=1):
        gsd.execute("catia_gsd_point", {"x": point[0], "y": point[1], "z": point[2],
                                        "name": f"{tag}_PP{index}"})
    gsd.execute("catia_gsd_plane_3points", {"point1": f"{tag}_PP1",
                                            "point2": f"{tag}_PP2",
                                            "point3": f"{tag}_PP3",
                                            "name": f"{tag}_PL"})
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 40], "support_plane": f"{tag}_PL",
                                     "radius": 7.0, "name": f"{tag}_C2"})
    reply = gsd.execute("catia_gsd_spine",
                        {"sections": [f"{tag}_C1", f"{tag}_C2"],
                         "name": f"{tag}_SP"})
    say("  " + reply[:400])
    say(f"  tree: {names_in_set(part=conn.get_active_part())}")
    close_tag(conn, document, tag)


def case_spine_one(conn, document, gsd) -> None:
    tag = "VSPONE"
    say("\n===== 5. spine with one section: must refuse before creating =====")
    part = fresh(conn, document, tag)
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": 10.0, "name": f"{tag}_C1"})
    try:
        gsd.execute("catia_gsd_spine", {"sections": [f"{tag}_C1"]})
        say("  UNEXPECTED: it created something")
    except ArgumentRejected as error:
        say(f"  refused: {str(error).splitlines()[0][:110]}")
    say(f"  tree: {names_in_set(part)}")
    close_tag(conn, document, tag)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)
    part_design = PartDesignTools(conn)
    case_fillet_wedge(conn, document, gsd, measurement, part_design)
    case_fillet_disjoint(conn, document, gsd)
    case_fillet_ambiguous(conn, document, gsd)
    case_spine(conn, document, gsd)
    case_spine_one(conn, document, gsd)
    say(f"\ndocuments open now: {conn.documents.Count}")


if __name__ == "__main__":
    main()
