"""Petaloid bottle base - acceptance pass: measure, verify, save.

Runs on the part phase 6 left open:
  * hides everything except the thickened solid (so the STL is the part, not the
    construction surfaces)
  * checks the shell's continuity from its mesh
  * measures volume and area, and wall thickness as V/A
  * measures the lowest point in each of the five 72 degree sectors
  * saves a copy next to the task documents and exports an STL

Run with -u. Owns only PETAL_* documents.
"""
import json
import math
import os
import sys

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.export import ExportTools
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.measurement import MeasurementTools, _stl_triangles
from catia_mcp.tools.part_design import PartDesignTools

DELIVER = (r"F:\WorkBuddy Space\2026-10-20-13-31-14\他人反馈的问题"
           r"\可乐瓶五爪花瓣瓶底_交付")
TMP = r"C:\Users\ADMINI~1\AppData\Local\Temp\bottle059"


def say(text: str = "") -> None:
    print(text, flush=True)


def main() -> None:
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)
    part_design = PartDesignTools(conn)
    export = ExportTools(conn)

    names = [str(conn.documents.Item(i).Name)
             for i in range(1, conn.documents.Count + 1)]
    say("open: " + ", ".join(names))
    if not any(name.startswith("PETAL_P6") for name in names):
        raise SystemExit("PETAL_P6 is not open; run b30_bottle_p6.py first")

    for index in range(1, conn.documents.Count + 1):
        doc = conn.documents.Item(index)
        if str(doc.Name).startswith("PETAL_P6"):
            doc.Activate()
    assert str(conn.active_document.Name).startswith("PETAL_P6"), \
        conn.active_document.Name

    # ---- hide everything but the solid that the thicken created
    keep = {"加厚曲面.1", "S_SKIRT"}
    try:
        elements = json.loads(gsd.execute("catia_gsd_list_elements", {}))
        shown = 0
        for group in elements.get("geometrical_sets", []):
            for name in group.get("elements", []):
                keep_now = name.startswith("加厚") or name in keep
                if not keep_now:
                    try:
                        part_design.execute("catia_set_visibility",
                                            {"name": name, "show": False})
                    except Exception:  # noqa: BLE001
                        pass
    except Exception as error:  # noqa: BLE001
        say("visibility pass skipped: " + str(error)[:80])
    say("construction geometry hidden")

    # ---- measure the solid
    bodies = json.loads(part_design.execute("catia_list_bodies", {}))
    say("bodies: " + json.dumps(bodies, ensure_ascii=False)[:200])

    volume = area = None
    try:
        inertia = json.loads(measurement.execute("catia_get_inertia", {}))
        volume = inertia.get("volume")
        area = inertia.get("area") or inertia.get("wetted_area")
        say("inertia: " + json.dumps(inertia, ensure_ascii=False)[:320])
    except Exception as error:  # noqa: BLE001
        say("inertia failed: " + str(error)[:160])

    report = json.loads(measurement.execute("catia_check_surface",
                                            {"crease_deg": 20, "top_creases": 5}))
    say("\n--- mesh of the solid ---")
    say(f"triangles {report['triangles']}  interior {report['interior_edges']}"
        f"  boundary {report['boundary_edges']}"
        f"  non-manifold {report['non_manifold_edges']}")
    say(f"fold median {report['fold_deg']['median']}  p90 {report['fold_deg']['p90']}"
        f"  max {report['fold_deg']['max']}")
    tight = report.get("curvature") or {}
    say(f"curvature: min {tight.get('min_radius_mm')}"
        f"  smooth {tight.get('tightest_smooth_radius_mm')}"
        f"  tighter-than-0.3 {((tight.get('thickness_check') or {}).get('edges_tighter_than_thickness'))}")
    if report.get("skin_note"):
        say("  skin_note: " + report["skin_note"][:120])

    # ---- five-sector foot heights, straight from the mesh
    boxes = {}

    def visit(path: str) -> None:
        faces = _stl_triangles(path, 400000)
        lowest = {}
        for _normal, verts in faces:
            for vertex in verts:
                angle = math.degrees(math.atan2(vertex[1], vertex[0])) % 360.0
                sector = int(angle // 72.0) % 5
                lowest[sector] = min(lowest.get(sector, 9e9), vertex[2])
        boxes["feet"] = {k: round(v, 3) for k, v in sorted(lowest.items())}
        boxes["triangles"] = len(faces)

    measurement._mesh_export(conn.get_active_part(), None, visit)
    feet = boxes.get("feet") or {}
    say("\nlowest z in each 72 deg sector: " + json.dumps(feet))
    if feet:
        values = list(feet.values())
        say(f"  spread {max(values) - min(values):.4f} mm over {len(values)} sectors")

    # area counts BOTH faces of a shell, so the wall is 2 x (V / A)
    say("\nwall from V/A: "
        + (f"{2.0 * volume / area:.4f} mm  (V {volume:.1f} mm3, A {area:.1f} mm2)"
           if volume and area else "not available"))

    # ---- save the deliverable
    os.makedirs(DELIVER, exist_ok=True)
    target = os.path.join(DELIVER, "ColaBottle_PetalBase.CATPart")
    try:
        document.execute("catia_save_document", {"file_path": target})
        say("saved: " + target + f"  ({os.path.getsize(target)} bytes)")
    except Exception as error:  # noqa: BLE001
        say("save failed: " + str(error)[:200])

    stl = os.path.join(DELIVER, "ColaBottle_PetalBase.stl")
    try:
        export.execute("catia_export", {"file_path": stl, "format": "stl"})
        say("exported: " + stl + f"  ({os.path.getsize(stl)} bytes)")
    except Exception as error:  # noqa: BLE001
        say("stl export failed: " + str(error)[:200])

    for view, tag in (("bottom", "bottom"), ("isometric", "iso"),
                      ("front", "front")):
        export.execute("catia_set_view", {"view": view})
        export.execute("catia_fit_all", {})
        export.execute("catia_screenshot",
                       {"file_path": os.path.join(DELIVER, f"petal_{tag}.jpg")})
    say("screenshots written next to the part")


main()
