"""Is the one orientation that updates an actual rolling-ball fillet?

b30_v0353_corner.py settled "which call survives UpdateObject" - one pair out of
four on a 90 deg corner (o1 = -1, o2 = +1) - and 0.3.53 wrote "what it builds is
unverified" into README. That sentence is the debt this script pays. Surviving
an update is not the same as being the right surface: a fillet could be built on
the wrong side of a wall, with the wrong radius, or as something else entirely.

Two phases, because they need different hygiene:

  PHASE 1  the orientation matrix at three wedge angles. Cheap: build, update,
           read Area. Failed calls leave broken elements behind and a later
           part.Update() then fails on them (measured here: the first version of
           this script died exactly that way), so nothing calls Update in phase 1
           and the document is thrown away afterwards.

  PHASE 2  geometry of the winner, one FRESH document per case. Only the fillet
           is visible, so the STL carries the fillet alone - the mistake 0.3.55
           made with healing was exporting three features at once and reading the
           union as one.

Three independent readings, because one alone can be fooled:

1. AREA. A fillet of radius r along a straight edge of length L, between walls
   meeting at interior angle theta, is a cylindrical band of arc (pi - theta):
       area = (pi - theta) * r * L
   90 deg, r=5, L=40 -> 314.159 mm2. (Measurable.Area DOES read this correctly
   for a fillet surface - the standing note "Area is always 0.0 for a surface"
   is wrong, at least here; 0.3.56 corrects it.)

2. RADIAL DISTANCE from the rolling-ball axis. The ball centre for a wedge sits
   on the bisector at d = r / sin(theta/2); every point of the fillet is exactly
   r from the axis through that point. This is the check that cannot be
   satisfied by accident - it pins radius AND position.

3. REACH. The band has to touch both walls (min distance to each wall = 0),
   otherwise it is a floating tube near the corner and not a fillet.

Nothing here creates a tool. It answers whether one is owed.
"""

from __future__ import annotations

import math
import os
import sys
import tempfile

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.measurement import MeasurementTools, _stl_triangles
from catia_mcp.tools.part_design import PartDesignTools

OUT_DIR = os.path.join(tempfile.gettempdir(), "b30_v0356")
LENGTH = 40.0
ANGLES = ("90", "60", "120")


def say(text: str = "") -> None:
    print(text, flush=True)


def mesh_area(triangles) -> float:
    """Sum of triangle areas - the independent reading of the STL."""
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


def build_wedge(gsd, prefix: str, angle: float) -> tuple[str, str]:
    """Two extruded walls sharing the Z axis, interior angle = angle."""
    a = math.radians(angle)
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [60, 0, 0],
                                   "name": f"{prefix}_LA"})
    gsd.execute("catia_gsd_extrude", {"profile": f"{prefix}_LA",
                                      "direction": [0, 0, 1], "limit1": LENGTH,
                                      "name": f"{prefix}_A"})
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0],
                                   "point2": [60 * math.cos(a), 60 * math.sin(a), 0],
                                   "name": f"{prefix}_LB"})
    gsd.execute("catia_gsd_extrude", {"profile": f"{prefix}_LB",
                                      "direction": [0, 0, 1], "limit1": LENGTH,
                                      "name": f"{prefix}_B"})
    return f"{prefix}_A", f"{prefix}_B"


def phase_matrix(conn, document, gsd, measurement) -> dict:
    """Which (o1, o2, trim) survive UpdateObject, at each wedge angle."""
    winners: dict[tuple[str, float], list] = {}
    prefix = "FMAT"
    document.execute("catia_new_part",
                     {"name": prefix, "file_path": os.path.join(OUT_DIR, f"{prefix}.CATPart")})
    assert str(conn.active_document.Name).startswith(prefix), conn.active_document.Name
    part = conn.get_active_part()
    factory = gsd._factory()
    geoset = gsd._get_geoset()
    for angle_text in ANGLES:
        angle = float(angle_text)
        name_a, name_b = build_wedge(gsd, f"{prefix}{angle_text}", angle)
        ref_a = part.CreateReferenceFromObject(part.FindObjectByName(name_a))
        ref_b = part.CreateReferenceFromObject(part.FindObjectByName(name_b))
        say(f"\n===== PHASE 1: wedge {angle_text} deg "
            f"(band would be {(math.pi - math.radians(angle)) * 5 * LENGTH:.3f} mm2 at r=5) =====")
        for radius in (2.0, 5.0):
            for o1, o2 in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
                for trim in (0, 1):
                    try:
                        fillet = factory.AddNewFilletBiTangent(
                            ref_a, ref_b, radius, o1, o2, trim, 0)
                        geoset.AppendHybridShape(fillet)
                        fillet.Name = f"F_{angle_text}_{int(radius)}_{o1}_{o2}_{trim}"
                        part.UpdateObject(fillet)
                    except Exception as error:  # noqa: BLE001
                        say(f"  r={radius} o={o1},{o2} trim={trim}: "
                            f"{str(error).splitlines()[0][:60]}")
                        continue
                    # No Area here on purpose: on this same element it read
                    # 125.664 mm2 in one document and 0.0 in another (see the
                    # phase-2 printout), so it is not a signal this phase can
                    # use. Survival of UpdateObject is all phase 1 asks.
                    say(f"  r={radius} o={o1},{o2} trim={trim}: BUILT + updated")
                    winners.setdefault((angle_text, radius), []).append(
                        (o1, o2, trim))
    for index in range(conn.documents.Count, 0, -1):
        try:
            if str(conn.documents.Item(index).Name).startswith(prefix):
                conn.documents.Item(index).Activate()
                document.execute("catia_close_document", {"save": False})
        except Exception:  # noqa: BLE001
            pass
    return winners


def phase_geometry(conn, document, gsd, measurement, part_design, angle: float,
                   radius: float, o1: int, o2: int, trim: int) -> None:
    """Build ONE fillet in a fresh document and read its mesh geometry."""
    tag = f"FG{int(angle)}_{int(radius)}"
    say(f"\n===== PHASE 2: {tag}  o={o1},{o2} trim={trim} =====")
    document.execute("catia_new_part",
                     {"name": tag, "file_path": os.path.join(OUT_DIR, f"{tag}.CATPart")})
    assert str(conn.active_document.Name).startswith(tag), conn.active_document.Name
    part = conn.get_active_part()
    factory = gsd._factory()
    geoset = gsd._get_geoset()
    name_a, name_b = build_wedge(gsd, tag, angle)
    ref_a = part.CreateReferenceFromObject(part.FindObjectByName(name_a))
    ref_b = part.CreateReferenceFromObject(part.FindObjectByName(name_b))
    fillet = factory.AddNewFilletBiTangent(ref_a, ref_b, radius, o1, o2, trim, 0)
    geoset.AppendHybridShape(fillet)
    fillet.Name = f"{tag}_F"
    part.UpdateObject(fillet)

    def read_area(label: str) -> None:
        try:
            measurable = (conn.active_document.GetWorkbench("SPAWorkbench")
                          .GetMeasurable(part.CreateReferenceFromObject(fillet)))
            raw = float(measurable.Area)
            say(f"  Measurable.Area ({label}): {raw:.9f}  "
                f"= {raw * 1e6:.3f} mm2 (MKS: the API returns m2)")
        except Exception as error:  # noqa: BLE001
            say(f"  Measurable.Area ({label}): {str(error).splitlines()[0][:70]}")

    read_area("right after UpdateObject")

    # Hide the walls: the STL includes every VISIBLE element, and a radial
    # reading over walls-plus-fillet is meaningless. `Item(i).Visible = False`
    # is refused by the dynamic dispatch ("Property 'Item.Visible' can not be
    # set"), and catia_set_visibility lives on the part-design tools, not on
    # DocumentTools - calling it there just says "Unknown document tool".
    for node in (f"{tag}_A", f"{tag}_B", f"{tag}_LA", f"{tag}_LB"):
        try:
            part_design.execute("catia_set_visibility", {"name": node, "show": False})
        except Exception as error:  # noqa: BLE001
            say(f"  could not hide {node}: {str(error).splitlines()[0][:70]}")
    part.Update()
    read_area("after part.Update")

    box: dict = {}

    def visit(path: str) -> None:
        triangles = _stl_triangles(path, 400000)
        vertices = []
        for _normal, tri in triangles:
            vertices.extend(tri)
        box["triangles"] = len(triangles)
        box["vertices"] = vertices
        box["area"] = mesh_area(triangles)

    measurement._mesh_export(part, limit=None, visit=visit)
    vertices = box.get("vertices") or []
    if not vertices:
        say("  STL carried no triangles")
    else:
        theta = math.radians(angle)
        d = radius / math.sin(theta / 2)
        axis = (d * math.cos(theta / 2), d * math.sin(theta / 2))
        radial = [math.hypot(x - axis[0], y - axis[1]) for x, y, _z in vertices]
        wall_a = min(abs(y) for _x, y, _z in vertices)
        wall_b = min(abs(-math.sin(theta) * x + math.cos(theta) * y)
                     for x, y, _z in vertices)
        zs = [z for _x, _y, z in vertices]
        expected = (math.pi - theta) * radius * LENGTH
        say(f"  triangles {box['triangles']}, vertices {len(vertices)}")
        say(f"  mesh area {box['area']:.3f}  vs band {expected:.3f}  "
            f"({(box['area'] / expected - 1) * 100:+.3f} %, faceting reads low)")
        say(f"  radius from axis ({axis[0]:.3f},{axis[1]:.3f}): "
            f"min {min(radial):.4f}  max {max(radial):.4f}  (asked {radius})")
        say(f"  distance to wall A {wall_a:.4f}, wall B {wall_b:.4f}  "
            f"(tangency means 0)")
        say(f"  z span {min(zs):.3f} .. {max(zs):.3f}  (walls 0 .. {LENGTH})")

    for index in range(conn.documents.Count, 0, -1):
        try:
            if str(conn.documents.Item(index).Name).startswith(tag):
                conn.documents.Item(index).Activate()
                document.execute("catia_close_document", {"save": False})
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)
    part_design = PartDesignTools(conn)

    winners = phase_matrix(conn, document, gsd, measurement)
    say("\n===== what survived, by angle =====")
    for key in sorted(winners):
        angle_text, radius = key
        expected = (math.pi - math.radians(float(angle_text))) * radius * LENGTH
        say(f"  {angle_text} deg, r={radius} (band {expected:.3f}): "
            f"{winners[key]}")

    for angle_text in ANGLES:
        radius = 5.0
        rows = winners.get((angle_text, radius)) or []
        untrimmed = [row for row in rows if row[2] == 0]
        if not untrimmed:
            say(f"\n  {angle_text} deg skipped: nothing survived at r=5")
            continue
        o1, o2, trim = untrimmed[0]
        phase_geometry(conn, document, gsd, measurement, part_design,
                       float(angle_text), radius, o1, o2, trim)

    say(f"\ndocuments open now: {conn.documents.Count}")


if __name__ == "__main__":
    main()
