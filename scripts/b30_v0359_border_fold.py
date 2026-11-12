"""What a 180 deg dihedral at an OPEN BORDER actually is.

Reported 2026-11-04 on 0.3.58: a failed ThickSurface reported its tightest
radius as 0.034 mm at r=32.99, z=123.04, which is the top border of a skirt
surface - not a bend. This probe builds open patches on B30 and looks at the
edges that carry a fold near 180 deg, so the filter is written against what
they are rather than against a guess.

Run with -u. Closes only its own OPEN_* documents.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.measurement import MeasurementTools, _stl_triangles

TMP = r"C:\Users\ADMINI~1\AppData\Local\Temp\b30_v0359"


def say(text: str = "") -> None:
    print(text, flush=True)


def key(point):
    return (round(point[0], 3), round(point[1], 3), round(point[2], 3))


def angle(n1, n2):
    dot = sum(x * y for x, y in zip(n1, n2))
    na = sum(x * x for x in n1) ** 0.5 or 1.0
    nb = sum(x * x for x in n2) ** 0.5 or 1.0
    return math.degrees(math.acos(max(-1.0, min(1.0, dot / (na * nb)))))


def analyse(path: str, label: str) -> None:
    faces = _stl_triangles(path, 400000)
    edges = {}
    for index, (normal, verts) in enumerate(faces):
        for a, b in ((0, 1), (1, 2), (2, 0)):
            ka, kb = key(verts[a]), key(verts[b])
            edges.setdefault((min(ka, kb), max(ka, kb)), []).append(index)
    singles = sum(1 for e in edges.values() if len(e) == 1)
    doubles = sum(1 for e in edges.values() if len(e) == 2)
    multis = sum(1 for e in edges.values() if len(e) > 2)
    say(f"\n== {label} == triangles {len(faces)} edges {len(edges)} "
        f"1-tri {singles} 2-tri {doubles} >2 {multis}")
    folds = []
    for edge, ids in edges.items():
        if len(ids) != 2:
            continue
        fold = angle(faces[ids[0]][0], faces[ids[1]][0])
        mid = tuple((edge[0][i] + edge[1][i]) / 2.0 for i in range(3))
        folds.append((fold, edge, ids, mid))
    folds.sort(key=lambda row: -row[0])
    say(f"  fold: max {folds[0][0]:.2f}  min {folds[-1][0]:.2f}  "
        f"p50 {sorted(f[0] for f in folds)[len(folds)//2]:.2f}")
    for fold, edge, ids, mid in folds[:6]:
        length = math.dist(edge[0], edge[1])
        # Are BOTH endpoints on the border? An endpoint is "on the border" if
        # one of its edges has a single triangle - a cheap stand-in for a
        # vertex-valence test.
        on_border = 0
        for point in (edge[0], edge[1]):
            for other, other_ids in edges.items():
                if point in other and len(other_ids) == 1:
                    on_border += 1
                    break
        say(f"    fold {fold:8.3f} len {length:7.3f} "
            f"r={math.hypot(mid[0], mid[1]):7.3f} z={mid[2]:8.3f} "
            f"endpoints_on_border={on_border}/2")
        say(f"      n0 {[round(v, 3) for v in faces[ids[0]][0]]} "
            f"n1 {[round(v, 3) for v in faces[ids[1]][0]]}")
        say(f"      tri0 {[[round(c, 2) for c in p] for p in faces[ids[0]][1]]}")
        say(f"      tri1 {[[round(c, 2) for c in p] for p in faces[ids[1]][1]]}")
    high = [f for f in folds if f[0] >= 170.0]
    say(f"  edges with fold >= 170: {len(high)}")
    if high:
        zs = [row[3][2] for row in high]
        say(f"    their z range {min(zs):.3f} .. {max(zs):.3f}; "
            f"fold values {sorted(round(r[0], 1) for r in high)[:8]}")


def main() -> None:
    conn = CATIAConnection()
    conn.connect()
    if conn.documents.Count:
        raise SystemExit(
            "a document is already open; close it first so this probe owns the "
            "session")
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)

    def own(tag: str) -> None:
        document.execute("catia_new_part",
                         {"name": tag, "file_path": rf"{TMP}\{tag}.CATPart"})
        assert str(conn.active_document.Name).startswith(tag), \
            conn.active_document.Name

    def exported(part, label: str) -> None:
        captured = {}

        def visit(path: str) -> None:
            captured["path"] = path
            analyse(path, label)

        measurement._mesh_export(part, None, visit)

    try:
        # A. A flat patch: one open line extruded. Two of its four borders are
        #    the ends of the extrusion, two are the line's own ends.
        own("OPEN_FLAT")
        part = conn.get_active_part()
        gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [40, 0, 0],
                                       "name": "LE"})
        gsd.execute("catia_gsd_extrude", {"profile": "LE",
                                          "direction": [0, 0, 1],
                                          "limit1": 60.0, "name": "FLAT"})
        part.Update()
        exported(part, "flat patch, extruded open line")

        document.execute("catia_close_document", {"save": False})

        # B. An open tube: a circle extruded, no caps. Borders at both rims.
        own("OPEN_TUBE")
        part = conn.get_active_part()
        gsd.execute("catia_gsd_circle", {"center": [0, 0, 0],
                                         "support_plane": "xy", "radius": 10.0,
                                         "name": "CT"})
        gsd.execute("catia_gsd_extrude", {"profile": "CT",
                                          "direction": [0, 0, 1],
                                          "limit1": 30.0, "name": "TUBE"})
        part.Update()
        exported(part, "open tube, circle extruded")

        document.execute("catia_close_document", {"save": False})

        # D. TWO COINCIDENT PATCHES. A double-sided / duplicated sheet is the
        #    candidate for a family of 180 deg rows: two copies of the same
        #    surface with opposite orientation share no vertices, so each edge
        #    may end up with two triangles facing opposite ways.
        own("OPEN_DOUBLE")
        part = conn.get_active_part()
        gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [40, 0, 0],
                                       "name": "LD"})
        gsd.execute("catia_gsd_extrude", {"profile": "LD",
                                          "direction": [0, 0, 1],
                                          "limit1": 60.0, "name": "D1"})
        gsd.execute("catia_gsd_extrude", {"profile": "LD",
                                          "direction": [0, 0, -1],
                                          "limit1": -60.0, "name": "D2"})
        part.Update()
        exported(part, "two coincident patches")
        document.execute("catia_close_document", {"save": False})

        # E. Two coincident TUBES: same, but curved, so the chording normals
        #    disagree by 22.5 deg instead of being identical.
        own("OPEN_DTUBE")
        part = conn.get_active_part()
        gsd.execute("catia_gsd_circle", {"center": [0, 0, 0],
                                         "support_plane": "xy", "radius": 10.0,
                                         "name": "CD"})
        gsd.execute("catia_gsd_extrude", {"profile": "CD",
                                          "direction": [0, 0, 1],
                                          "limit1": 30.0, "name": "E1"})
        gsd.execute("catia_gsd_extrude", {"profile": "CD",
                                          "direction": [0, 0, 1],
                                          "limit1": 30.0, "name": "E2"})
        part.Update()
        exported(part, "two coincident tubes")
        document.execute("catia_close_document", {"save": False})

        # C. A cylinder WITH caps (fill both rims) - a closed shell, so a
        #    border fold has nowhere to come from.
        own("OPEN_CAPPED")
        part = conn.get_active_part()
        gsd.execute("catia_gsd_circle", {"center": [0, 0, 0],
                                         "support_plane": "xy", "radius": 10.0,
                                         "name": "CC"})
        gsd.execute("catia_gsd_extrude", {"profile": "CC",
                                          "direction": [0, 0, 1],
                                          "limit1": 30.0, "name": "CY"})
        part.Update()
        exported(part, "open tube before capping")
        document.execute("catia_close_document", {"save": False})
    finally:
        for index in range(conn.documents.Count, 0, -1):
            doc = conn.documents.Item(index)
            name = str(doc.Name)
            if name.startswith("OPEN_"):
                doc.Activate()
                document.execute("catia_close_document", {"save": False})
                say(f"closed {name}")
        say(f"left {conn.documents.Count} document(s)")


main()
