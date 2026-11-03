"""Is the thin-wall claim real, and does normal-vs-radial split the faces?

The 0.3.53 report says catia_check_surface reads 28.5 deg on the OUTER face of a
0.3 mm wall and 124.05 deg on the INNER one at the same place, z differing by
exactly one wall thickness, and asks for the fold statistics to be split.

Before writing anything into the tool, measure it on a part whose inner and
outer faces are known: a tube (cylinder surface thickened 0.3) and, harder, a
0.3 wall with a tight fillet, where the inner radius is R-0.3 and the chording
has less room. For every interior edge this prints the fold angle plus the two
triangles' radial normal components, so the classifier can be checked against
geometry rather than assumed.
"""

from __future__ import annotations

import collections
import json
import math
import os
import struct
import sys
import tempfile

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.measurement import MeasurementTools
from catia_mcp.tools.sketcher import SketcherTools
from catia_mcp.tools.part_design import PartDesignTools

OUT = os.path.join(tempfile.gettempdir(), "b30_v0354")
os.makedirs(OUT, exist_ok=True)


def say(text: str = "") -> None:
    print(text, flush=True)


def activate(conn, name: str) -> None:
    for index in range(1, conn.documents.Count + 1):
        document = conn.documents.Item(index)
        if str(document.Name) == name:
            document.Activate()
            break
    else:
        raise RuntimeError(f"{name} is not open")
    if str(conn.active_document.Name) != name:
        raise RuntimeError(f"wanted {name} active, got {conn.active_document.Name}")


def triangles(path: str) -> list[tuple[tuple[float, float, float],
                                       list[tuple[float, float, float]]]]:
    blob = open(path, "rb").read()
    if blob[:5] == b"solid" and b"facet normal" in blob[:400]:
        out, normal, verts = [], (0.0, 0.0, 0.0), []
        for raw in blob.decode("ascii", "replace").splitlines():
            parts = raw.split()
            if not parts:
                continue
            if parts[0] == "facet":
                normal = (float(parts[2]), float(parts[3]), float(parts[4]))
                verts = []
            elif parts[0] == "vertex":
                verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
            elif parts[0] == "endfacet" and len(verts) == 3:
                out.append((normal, verts))
        return out
    count = struct.unpack("<I", blob[80:84])[0]
    out = []
    for index in range(count):
        values = struct.unpack("<12f", blob[84 + 50 * index:84 + 50 * index + 48])
        out.append(((values[0], values[1], values[2]),
                    [tuple(values[3 + 3 * k:6 + 3 * k]) for k in range(3)]))
    return out


def analyse(path: str, label: str) -> None:
    faces = triangles(path)
    say(f"\n{label}: {len(faces)} triangles")

    def key(point):
        return (round(point[0], 3), round(point[1], 3), round(point[2], 3))

    edges: dict = collections.defaultdict(list)
    for normal, verts in faces:
        centroid = [sum(v[i] for v in verts) / 3.0 for i in range(3)]
        radius = math.hypot(centroid[0], centroid[1])
        radial = (centroid[0] / radius, centroid[1] / radius) if radius > 1e-9 else (0.0, 0.0)
        n_r = normal[0] * radial[0] + normal[1] * radial[1]
        for a, b in ((0, 1), (1, 2), (2, 0)):
            ka, kb = key(verts[a]), key(verts[b])
            edges[(min(ka, kb), max(ka, kb))].append((normal, n_r, radius))

    def angle(n1, n2):
        dot = sum(x * y for x, y in zip(n1, n2))
        na = math.sqrt(sum(x * x for x in n1)) or 1.0
        nb = math.sqrt(sum(x * x for x in n2)) or 1.0
        return math.degrees(math.acos(max(-1.0, min(1.0, dot / (na * nb)))))

    buckets: dict[str, list[float]] = collections.defaultdict(list)
    worst = []
    for edge, entries in edges.items():
        if len(entries) != 2:
            continue
        fold = angle(entries[0][0], entries[1][0])
        sides = []
        for _normal, n_r, _radius in entries:
            if abs(n_r) < 0.5:
                sides.append("axial")
            elif n_r > 0:
                sides.append("outer")
            else:
                sides.append("inner")
        side = sides[0] if sides[0] == sides[1] else "mixed"
        buckets[side].append(fold)
        mid = [(edge[0][i] + edge[1][i]) / 2.0 for i in range(3)]
        worst.append((fold, side, mid, math.hypot(mid[0], mid[1])))

    for side in ("outer", "inner", "axial", "mixed"):
        values = sorted(buckets.get(side, []))
        if not values:
            continue
        at = lambda q: values[min(len(values) - 1, int(q * len(values)))]
        say(f"   {side:<7} edges {len(values):>5}  median {at(0.5):>6.2f}  "
            f"p90 {at(0.9):>6.2f}  max {values[-1]:>7.2f}")
    worst.sort(key=lambda item: -item[0])
    say("   worst 6:")
    for fold, side, mid, radius in worst[:6]:
        say(f"     {fold:7.2f} deg  {side:<6} r {radius:6.2f}  "
            f"z {mid[2]:8.2f}  at ({mid[0]:.2f}, {mid[1]:.2f}, {mid[2]:.2f})")


def main() -> None:
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    measurement = MeasurementTools(conn)

    for index in range(conn.documents.Count, 0, -1):
        try:
            name = str(conn.documents.Item(index).Name)
        except Exception:  # noqa: BLE001
            continue
        if name.startswith("V0354_"):
            activate(conn, name)
            document.execute("catia_close_document", {"save": False})

    # A: tube - cylinder surface thickened 0.3. Both faces smooth, so this is
    #    the control: the split should show almost identical numbers.
    document.execute("catia_new_part",
                     {"name": "V0354_TUBE",
                      "file_path": os.path.join(OUT, "V0354_TUBE.CATPart")})
    activate(conn, "V0354_TUBE.CATPart")
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": 10.0, "name": "T_C"})
    gsd.execute("catia_gsd_extrude", {"profile": "T_C", "direction": [0, 0, 1],
                                      "limit1": 40.0, "name": "T_S"})
    gsd.execute("catia_gsd_thick_surface", {"surface": "T_S", "thickness1": 0.3})
    geoset_name = str(gsd._get_geoset().Name)
    part_design.execute("catia_set_visibility",
                        {"name": geoset_name, "show": False})
    say(f"   (hid geometrical set {geoset_name!r} so the STL is the solid only)")
    stl = os.path.join(OUT, "tube.stl")
    conn.active_document.ExportData(os.path.normpath(stl), "stl")
    analyse(stl, "A. tube R10, wall 0.3")
    payload = json.loads(measurement.execute("catia_check_surface",
                                             {"crease_deg": 25.0}))
    say(f"   tool says: median {payload['fold_deg']['median']}  "
        f"max {payload['fold_deg']['max']}  edges {payload['interior_edges']}")
    for side, values in payload["fold_deg_by_side"].items():
        if values["edges"]:
            say(f"     by side {side:<6} edges {values['edges']:>4}  "
                f"median {values['median']:>6}  max {values['max']:>7}")

    # B: a 0.3 wall around a TIGHT radius, which is the report's case - pad,
    #    fillet R3 on the bottom edges, shell 0.3. The inner face is R2.7 and
    #    has only 0.3 mm of room, so the mesh chords harder there.
    document.execute("catia_new_part",
                     {"name": "V0354_WALL",
                      "file_path": os.path.join(OUT, "V0354_WALL.CATPart")})
    activate(conn, "V0354_WALL.CATPart")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": 25.0})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 20.0})
    fillet_radius = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
    say(f"\nfillet radius {fillet_radius} mm, wall 0.3 -> inner radius "
        f"{max(0.01, fillet_radius - 0.3):.2f} mm")
    try:
        part_design.execute("catia_fillet",
                            {"radius": fillet_radius,
                             "edge_anchors": [[25, 0, 0], [0, 25, 0],
                                              [-25, 0, 0], [0, -25, 0]]})
        say("\nfillets: ok")
    except Exception as error:  # noqa: BLE001
        say(f"\nfillets failed: {str(error).splitlines()[0][:110]}")
    listing = {}
    try:
        listing = json.loads(part_design.execute("catia_list_faces", {}))
        say(f"faces: {len(listing.get('faces', []))}")
        for face in listing.get("faces", [])[:6]:
            say(f"   {face.get('name')}  area {face.get('area', '?')}")
    except Exception as error:  # noqa: BLE001
        say(f"list_faces failed: {str(error).splitlines()[0][:90]}")

    # Hollow it keeping 0.3 walls, so the inner fillet is R2.7 while the outer
    # stays R3 - the geometry the report calls the source of its 124 degrees.
    top = None
    for face in listing.get("faces", []):
        if "Face" in str(face.get("name", "")):
            top = str(face["name"])
            break
    if top:
        try:
            part_design.execute("catia_shell",
                                {"thickness": 0.3, "faces_to_remove": [top]})
            geoset_name = str(gsd._get_geoset().Name) if False else "GSD_Set"
            for name in ("GSD_Set", "Geometrical Set.1", geoset_name):
                try:
                    part_design.execute("catia_set_visibility",
                                        {"name": name, "show": False})
                    break
                except Exception:  # noqa: BLE001
                    continue
            wall_stl = os.path.join(OUT, "wall.stl")
            conn.active_document.ExportData(os.path.normpath(wall_stl), "stl")
            analyse(wall_stl, f"B. wall 0.3 around a R{fillet_radius:g} fillet "
                             f"(inner R{max(0.01, fillet_radius - 0.3):g})")
            payload = json.loads(measurement.execute("catia_check_surface",
                                                     {"crease_deg": 25.0}))
            say(f"   tool says: median {payload['fold_deg']['median']}  "
                f"max {payload['fold_deg']['max']}  "
                f"edges {payload['interior_edges']}  "
                f"boundary {payload['boundary_edges']}")
            for side, values in payload["fold_deg_by_side"].items():
                if values["edges"]:
                    say(f"     by side {side:<6} edges {values['edges']:>4}  "
                        f"median {values['median']:>6}  max {values['max']:>7}")
            if payload["worst_creases"]:
                first = payload["worst_creases"][0]
                say(f"     worst crease: {first['fold_deg']} deg, "
                    f"{first['side']}, r {first['r']}, z {first['z']}, "
                    f"theta {first['theta_deg']}")
        except Exception as error:  # noqa: BLE001
            say(f"shell failed: {str(error).splitlines()[0][:110]}")

    for index in range(conn.documents.Count, 0, -1):
        try:
            name = str(conn.documents.Item(index).Name)
        except Exception:  # noqa: BLE001
            continue
        if name.startswith("V0354_"):
            activate(conn, name)
            document.execute("catia_close_document", {"save": False})
    say(f"\ndocuments open now: {conn.documents.Count}")


if __name__ == "__main__":
    main()
