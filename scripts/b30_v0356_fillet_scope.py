"""The scope of AddNewFilletBiTangent: is (-1,+1) universal, and do curved supports work?

b30_v0356_fillet_geom.py proved the one orientation that updates builds a TRUE
rolling-ball fillet on a straight edge: every mesh vertex at exactly r from the
bisector axis, area exactly (pi - theta) * r * L, touching both walls. Three
angles, 60/90/120.

Two questions decide whether a tool can be written at all:

  A. ORIENTATION. The working pair was (-1, +1) for two extrusions built from
     lines at the origin going +X and +angle. Is that pair a property of the
     call, or of how the supports happen to be built? Test: the textbook
     floor-and-wall corner (z = 0 and x = 0, sharing an edge along Y), built in
     a completely different direction. If (-1, +1) wins again it is the call; if
     another pair wins, a tool has to search.

  B. CURVED SUPPORT. Straight edges are the easy case. A boss standing on a
     floor (cylinder + disc) needs a torus, and CATIA's own Edge Fillet does it
     without complaint. Expected torus area = 2*pi*(R + r) * (pi/2)*r.

Hygiene, learned the hard way in the previous script: a failed UpdateObject
leaves an element in ERROR in the tree, and any later part.Update() on that
document then fails too. So the orientation matrix runs in a throwaway document
and never calls Update, and the geometry is measured in a second, fresh document
that holds one fillet only.
"""

from __future__ import annotations

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.measurement import MeasurementTools, _stl_triangles
from catia_mcp.tools.part_design import PartDesignTools

OUT_DIR = os.path.join(tempfile.gettempdir(), "b30_v0356")
ORIENTATIONS = ((1, 1), (-1, 1), (1, -1), (-1, -1))


def say(text: str = "") -> None:
    print(text, flush=True)


def mesh_area(triangles) -> float:
    total = 0.0
    for _normal, tri in triangles:
        (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = tri
        ux, uy, uz = x2 - x1, y2 - y1, z2 - z1
        vx, vy, vz = x3 - x1, y3 - y1, z3 - z1
        cx = uy * vz - uz * vy
        cy = uz * vx - ux * vz
        cz = ux * vy - uy * vx
        total += 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)
    return total


def fresh_part(conn, document, tag: str):
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


def matrix(conn, document, gsd, tag: str, build, radius: float) -> list:
    """Which (o1, o2, trim) survive. Throwaway document, no part.Update()."""
    part = fresh_part(conn, document, tag + "M")
    factory = gsd._factory()
    geoset = gsd._get_geoset()
    name_a, name_b = build(gsd, tag + "M")
    ref_a = part.CreateReferenceFromObject(part.FindObjectByName(name_a))
    ref_b = part.CreateReferenceFromObject(part.FindObjectByName(name_b))
    winners = []
    for o1, o2 in ORIENTATIONS:
        for trim in (0, 1):
            try:
                fillet = factory.AddNewFilletBiTangent(ref_a, ref_b, radius,
                                                       o1, o2, trim, 0)
                geoset.AppendHybridShape(fillet)
                fillet.Name = f"{tag}M_{o1}_{o2}_{trim}"
                part.UpdateObject(fillet)
                say(f"    o={o1},{o2} trim={trim}: BUILT + updated")
                winners.append((o1, o2, trim))
            except Exception as error:  # noqa: BLE001
                say(f"    o={o1},{o2} trim={trim}: {str(error).splitlines()[0][:45]}")
    close_tag(conn, document, tag + "M")
    return winners


def geometry(conn, document, gsd, measurement, part_design, tag: str, build,
             radius: float, o1: int, o2: int, trim: int, hide: list,
             measure) -> None:
    """One fillet in a fresh document, walls hidden, then `measure(vertices, area)`."""
    part = fresh_part(conn, document, tag)
    factory = gsd._factory()
    geoset = gsd._get_geoset()
    name_a, name_b = build(gsd, tag)
    ref_a = part.CreateReferenceFromObject(part.FindObjectByName(name_a))
    ref_b = part.CreateReferenceFromObject(part.FindObjectByName(name_b))
    fillet = factory.AddNewFilletBiTangent(ref_a, ref_b, radius, o1, o2, trim, 0)
    geoset.AppendHybridShape(fillet)
    fillet.Name = f"{tag}_F"
    part.UpdateObject(fillet)
    for node in hide:
        try:
            part_design.execute("catia_set_visibility", {"name": node, "show": False})
        except Exception as error:  # noqa: BLE001
            say(f"    could not hide {node}: {str(error).splitlines()[0][:60]}")
    part.Update()

    box: dict = {}

    def visit(path: str) -> None:
        box["triangles"] = _stl_triangles(path, 400000)
        box["area"] = mesh_area(box["triangles"])

    measurement._mesh_export(part, limit=None, visit=visit)
    vertices = []
    for _normal, tri in box.get("triangles") or []:
        vertices.extend(tri)
    say(f"    triangles {len(box.get('triangles') or [])}, vertices {len(vertices)}")
    measure(vertices, box.get("area", 0.0))
    close_tag(conn, document, tag)


def build_floor_wall(gsd, tag: str) -> tuple:
    """Floor z=0 (x 0..60) and wall x=0 (z 0..40), sharing the Y axis edge."""
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [0, 60, 0],
                                   "name": f"{tag}_LF"})
    gsd.execute("catia_gsd_extrude", {"profile": f"{tag}_LF", "direction": [1, 0, 0],
                                      "limit1": 60.0, "name": f"{tag}_FLOOR"})
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [0, 60, 0],
                                   "name": f"{tag}_LW"})
    gsd.execute("catia_gsd_extrude", {"profile": f"{tag}_LW", "direction": [0, 0, 1],
                                      "limit1": 40.0, "name": f"{tag}_WALL"})
    return f"{tag}_FLOOR", f"{tag}_WALL"


def build_boss(gsd, tag: str) -> tuple:
    """Disc on z=0 (R=30) with a cylinder R=10 standing on it."""
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": 30.0, "name": f"{tag}_C30"})
    gsd.execute("catia_gsd_fill", {"boundaries": [f"{tag}_C30"],
                                   "name": f"{tag}_DISC"})
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": 10.0, "name": f"{tag}_C10"})
    gsd.execute("catia_gsd_extrude", {"profile": f"{tag}_C10",
                                      "direction": [0, 0, 1], "limit1": 40.0,
                                      "name": f"{tag}_BOSS"})
    return f"{tag}_DISC", f"{tag}_BOSS"


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)
    part_design = PartDesignTools(conn)

    say("===== A: floor z=0 + wall x=0, edge along Y, r=5 =====")
    expected_a = (math.pi / 2) * 5.0 * 60.0
    say(f"  a quarter cylinder about (x=5, z=5) along Y is {expected_a:.3f} mm2")
    winners_a = matrix(conn, document, gsd, "FSCOPEA", build_floor_wall, 5.0)
    for o1, o2, trim in winners_a:
        say(f"  --- geometry o={o1},{o2} trim={trim} ---")

        def measure_a(vertices, area, tag="FSCOPEG1"):
            if not vertices:
                say("    STL empty")
                return
            radial = [math.hypot(x - 5.0, z - 5.0) for x, _y, z in vertices]
            ys = [y for _x, y, _z in vertices]
            say(f"    area {area:.3f} (band {expected_a:.3f})")
            say(f"    radius from (x=5,z=5): min {min(radial):.4f} "
                f"max {max(radial):.4f}  (asked 5)")
            say(f"    y span {min(ys):.1f}..{max(ys):.1f} (edge 0..60)")

        geometry(conn, document, gsd, measurement, part_design, "FSCOPEG1",
                 build_floor_wall, 5.0, o1, o2, trim,
                 ["FSCOPEG1_FLOOR", "FSCOPEG1_WALL", "FSCOPEG1_LF", "FSCOPEG1_LW"],
                 measure_a)

    say("\n===== B: disc + standing cylinder (curved support), r=5 =====")
    expected_b = 2 * math.pi * (10 + 5) * (math.pi / 2) * 5
    say(f"  a torus of major radius 15 and minor radius 5 is {expected_b:.3f} mm2")
    winners_b = matrix(conn, document, gsd, "FSCOPEB", build_boss, 5.0)
    for o1, o2, trim in winners_b:
        say(f"  --- geometry o={o1},{o2} trim={trim} ---")

        def measure_b(vertices, area, tag="FSCOPEG2"):
            if not vertices:
                say("    STL empty")
                return
            deviation = [abs(math.hypot(math.hypot(x, y) - 15.0, z - 5.0))
                         for x, y, z in vertices]
            say(f"    area {area:.3f} (torus {expected_b:.3f})")
            say(f"    |distance to the torus|: min {min(deviation):.4f} "
                f"max {max(deviation):.4f}  (0 = exactly that torus)")

        geometry(conn, document, gsd, measurement, part_design, "FSCOPEG2",
                 build_boss, 5.0, o1, o2, trim,
                 ["FSCOPEG2_DISC", "FSCOPEG2_BOSS", "FSCOPEG2_C30", "FSCOPEG2_C10"],
                 measure_b)

    say(f"\ndocuments open now: {conn.documents.Count}")


if __name__ == "__main__":
    main()
