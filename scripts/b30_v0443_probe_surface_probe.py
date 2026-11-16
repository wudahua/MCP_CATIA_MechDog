"""Report round 14 (0.3.139) - what the surface-probe primitives do on B30.

P0 of that report: "sample a free-form surface at a point" has no entry AND no
equivalent. Before writing one, enumerate every catalogue primitive that could
serve, on a surface with an ANALYTIC answer instead of a guessed one.

Test surface: a cone from two circles (r30 at z=0, r12 at z=60).
    r(z) = 30 - 0.3*z          z=30 -> r=21
    front point at x=0, z=30 -> y = -21.0000 exactly
So every reading below has a right answer to compare against, and a reading that
returns 0.0000 for the wrong reason is visible.

    B1 AddNewProject(point, surface)        -> type, coordinates readable?
    B2 AddNewIntersection(line, surface)    -> type, coordinates readable?
    B3 GetMinimumDistance(point -> surface) -> does it reach 0 on the surface?
    B4 PointCoord.X/Y/Z writable?           -> can one point serve N probes?
    B5 read_min_distance_points(surface, point) -> the CLOSEST POINT on the
       surface, which is the thing P0 says cannot be had (this API already
       exists in catia_mcp/tools/measurement.py and is proven in part_design)

P1 of the same report: catia_gsd_spline from COORDINATES - does it leave Point
elements behind? Two tool descriptions say opposite things.

    .venv/Scripts/python.exe -u scripts/b30_v0443_probe_surface_probe.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.measurement import (read_measurable_array,  # noqa: E402
                                         read_min_distance_points)

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0443")
CELLS: list[tuple[str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def cell(name: str, ok: bool, detail: str = "") -> None:
    CELLS.append((name, ok, detail))
    say(f"  [{'OK ' if ok else 'NO '}] {name}" + (f"  <- {detail}" if detail else ""))


def fresh(conn, docs, name: str):
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, name + ".CATPart")
    for index in range(int(conn.documents.Count), 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = str(doc.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).lower().startswith(
                os.path.abspath(ROOT).lower()):
            try:
                doc.Close()
            except Exception:  # noqa: BLE001
                pass
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    return conn.get_active_part()


def set_names(part, set_name: str) -> list[str]:
    try:
        shapes = part.HybridBodies.Item(set_name).HybridShapes
    except Exception:  # noqa: BLE001
        return []
    names = []
    for index in range(1, int(shapes.Count) + 1):
        try:
            names.append(str(shapes.Item(index).Name))
        except Exception:  # noqa: BLE001
            continue
    return names


def type_name(obj) -> str:
    try:
        return str(obj._oleobj_.GetTypeInfo().GetDocumentation(-1)[0])
    except Exception:  # noqa: BLE001
        return "<unreadable type>"


# ── P1: point ownership ─────────────────────────────────────────────────────
def probe_points_ownership(conn, docs, gsd) -> None:
    say("=" * 78)
    say("P1 - catia_gsd_spline from COORDINATES: what is left in the tree?")
    part = fresh(conn, docs, "ProbePointsOwn")
    gsd.execute("catia_gsd_create_geoset", {"name": "G_Set"})
    before = set_names(part, "G_Set")
    say(f"  G_Set before: {before}")
    gsd.execute("catia_gsd_spline", {
        "points": [[0, 0, 0], [10, 0, 4], [20, 0, -3], [30, 0, 6],
                   [40, 0, 0], [50, 0, 2]],
        "name": "S_coords"})
    after = set_names(part, "G_Set")
    say(f"  G_Set after:  {after}")
    cell("A1 a 6-point coordinate spline adds ONE element, not seven",
         len(after) == len(before) + 1, f"{before} -> {after}")
    try:
        listed = gsd.execute("catia_gsd_list_elements", {})
        say(f"  gsd_list_elements -> {listed[:400]}")
    except Exception as error:  # noqa: BLE001
        say("  gsd_list_elements failed: " + str(error).splitlines()[0][:90])
    try:
        target = part.HybridBodies.Item("G_Set").HybridShapes.Item(1)
        conn.hso.Clear()
        conn.hso.Add(target)
        conn.hso.Delete()
        left = set_names(part, "G_Set")
        cell("A2 deleting it leaves nothing behind (no orphan Points)",
             len(left) == len(before), f"after delete: {left}")
    except Exception as error:  # noqa: BLE001
        cell("A2 deleting it leaves nothing behind (no orphan Points)", False,
             str(error).splitlines()[0][:90])
    finally:
        try:
            conn.hso.Clear()
        except Exception:  # noqa: BLE001
            pass


# ── P0: primitives against a cone ──────────────────────────────────────────
def build_cone(conn, docs, gsd):
    part = fresh(conn, docs, "ProbeSurfCone")
    gsd.execute("catia_gsd_create_geoset", {"name": "SurfSet"})
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": 30, "name": "C_Bottom"})
    gsd.execute("catia_gsd_plane_offset", {"base_plane": "xy", "offset": 60,
                                           "name": "Plane_Z60"})
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 60],
                                     "support_plane": "Plane_Z60",
                                     "radius": 12, "name": "C_Top"})
    gsd.execute("catia_gsd_multi_section_surface", {
        "sections": ["C_Bottom", "C_Top"], "name": "Cone"})
    return part


def probe_primitives(conn, docs, gsd) -> None:
    part = build_cone(conn, docs, gsd)
    app = conn.app
    document = conn.active_document
    factory = part.HybridShapeFactory
    spa = document.GetWorkbench("SPAWorkbench")
    part.Update()
    try:
        surf = part.HybridBodies.Item("SurfSet").HybridShapes.Item("Cone")
    except Exception as error:  # noqa: BLE001
        cell("B0 the cone surface exists", False, str(error).splitlines()[0][:90])
        return
    surf_ref = part.CreateReferenceFromObject(surf)
    cell("B0 the cone surface exists", True, f"{surf.Name}")
    try:
        area = float(spa.GetMeasurable(surf_ref).Area) * 1e6
        analytic = math.pi * (30 + 12) * math.hypot(18, 60)
        say(f"  lateral area {area:.3f} mm2 vs analytic {analytic:.3f} mm2")
    except Exception as error:  # noqa: BLE001
        say("  Area unreadable: " + str(error).splitlines()[0][:70])

    holder = part.HybridBodies.Item("SurfSet")
    made: list = []

    def coord(x, y, z):
        point = factory.AddNewPointCoord(x, y, z)
        holder.AppendHybridShape(point)
        part.UpdateObject(point)
        made.append(point)
        return point

    say("")
    say("  B3 - GetMinimumDistance from a coordinate point to the surface")
    for y in (-25.0, -21.0, -18.0):
        try:
            point = coord(0.0, y, 30.0)
            distance = float(spa.GetMeasurable(
                part.CreateReferenceFromObject(point)).GetMinimumDistance(surf_ref))
            say(f"    point (0,{y:6.1f},30) -> {distance:9.4f} mm   "
                f"(analytic 0 at y=-21)")
        except Exception as error:  # noqa: BLE001
            say(f"    point (0,{y:6.1f},30) -> FAILED "
                f"{str(error).splitlines()[0][:60]}")

    say("")
    say("  B5 - read_min_distance_points(surface, point): the CLOSEST POINT")
    for where in ((0.0, -25.0, 30.0), (8.0, -24.0, 40.0)):
        try:
            point = coord(*where)
            on_surface, on_point = read_min_distance_points(
                app, spa.GetMeasurable(surf_ref),
                part.CreateReferenceFromObject(point))
            radius = math.hypot(on_surface[0], on_surface[1])
            expected = 30 - 0.3 * on_surface[2]
            say(f"    from {where} -> surface point "
                f"({on_surface[0]:.4f}, {on_surface[1]:.4f}, {on_surface[2]:.4f})"
                f"  radius {radius:.4f} vs cone {expected:.4f}"
                f"  | echo {on_point}")
            cell(f"B5 closest point on the surface from {where}",
                 abs(radius - expected) < 0.01,
                 f"radius error {abs(radius - expected):.4f} mm")
        except Exception as error:  # noqa: BLE001
            cell(f"B5 closest point on the surface from {where}", False,
                 str(error).splitlines()[0][:110])

    say("")
    say("  B4 - is ONE PointCoord re-usable? (X/Y/Z assignment + UpdateObject)")
    try:
        point = coord(0.0, -25.0, 30.0)
        first = float(spa.GetMeasurable(
            part.CreateReferenceFromObject(point)).GetMinimumDistance(surf_ref))
        point.Y = -21.0
        part.UpdateObject(point)
        second = float(spa.GetMeasurable(
            part.CreateReferenceFromObject(point)).GetMinimumDistance(surf_ref))
        back = float(point.Y)
        cell("B4a PointCoord.Y is writable and reads back",
             math.isclose(back, -21.0), f"Y={back}")
        cell("B4b moving it changes the measurement",
             abs(second - first) > 1e-6, f"{first:.4f} -> {second:.4f} mm")
    except Exception as error:  # noqa: BLE001
        cell("B4a PointCoord.Y is writable and reads back", False,
             str(error).splitlines()[0][:90])

    say("")
    say("  B1 - AddNewProject(point, surface)")
    try:
        probe = coord(0.0, -26.0, 30.0)
        projection = factory.AddNewProject(
            part.CreateReferenceFromObject(probe), surf_ref)
        holder.AppendHybridShape(projection)
        part.UpdateObject(projection)
        cell("B1a AddNewProject creates an element", True,
             f"type={type_name(projection)}")
        try:
            coords = read_measurable_array(
                app, spa.GetMeasurable(
                    part.CreateReferenceFromObject(projection)), "GetPoint", 3)
            error_mm = abs(float(coords[1]) + 21.0)
            cell("B1b its coordinates read back (Evaluate GetPoint)",
                 error_mm < 0.01,
                 f"({coords[0]:.4f}, {coords[1]:.4f}, {coords[2]:.4f}) "
                 f"error {error_mm:.4f} mm vs analytic -21")
        except Exception as error:  # noqa: BLE001
            cell("B1b its coordinates read back (Evaluate GetPoint)", False,
                 str(error).splitlines()[0][:90])
    except Exception as error:  # noqa: BLE001
        cell("B1a AddNewProject creates an element", False,
             str(error).splitlines()[0][:110])

    say("")
    say("  B2 - AddNewIntersection(line along y, surface)")
    try:
        line = factory.AddNewLinePtPt(
            part.CreateReferenceFromObject(coord(0.0, -40.0, 30.0)),
            part.CreateReferenceFromObject(coord(0.0, 40.0, 30.0)))
        holder.AppendHybridShape(line)
        part.UpdateObject(line)
        intersection = factory.AddNewIntersection(
            part.CreateReferenceFromObject(line), surf_ref)
        holder.AppendHybridShape(intersection)
        part.UpdateObject(intersection)
        cell("B2a AddNewIntersection(line, surface) creates an element", True,
             f"type={type_name(intersection)}")
        try:
            coords = read_measurable_array(
                app, spa.GetMeasurable(
                    part.CreateReferenceFromObject(intersection)), "GetPoint", 3)
            cell("B2b it reads back as a point", True,
                 f"({coords[0]:.4f}, {coords[1]:.4f}, {coords[2]:.4f})")
        except Exception as error:  # noqa: BLE001
            cell("B2b it reads back as a point", False,
                 str(error).splitlines()[0][:90])
    except Exception as error:  # noqa: BLE001
        cell("B2a AddNewIntersection(line, surface) creates an element", False,
             str(error).splitlines()[0][:110])

    say("")
    say("  cost: one reusable point, 5 probes (move + UpdateObject + read pair)")
    try:
        point = coord(0.0, -25.0, 30.0)
        start = time.time()
        for step in range(5):
            point.Y = -25.0 + step
            part.UpdateObject(point)
            read_min_distance_points(app, spa.GetMeasurable(surf_ref),
                                     part.CreateReferenceFromObject(point))
        say(f"    5 probes in {time.time() - start:.2f} s")
    except Exception as error:  # noqa: BLE001
        say("  timing failed: " + str(error).splitlines()[0][:80])


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    gsd = GSDTools(conn)
    say(f"probe v0443 on {conn.app.Name}")
    probe_points_ownership(conn, docs, gsd)
    probe_primitives(conn, docs, gsd)
    say("")
    passed = sum(1 for _n, ok, _d in CELLS if ok)
    say("-" * 78)
    say(f"{passed}/{len(CELLS)} cells reported a capability")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
