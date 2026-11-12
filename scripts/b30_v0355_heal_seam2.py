"""Healing against a same-source seam, measured on the exported mesh, not via the tool.

First attempt taught two things the hard way:
  * the STL export carries every VISIBLE element, so creating heal C0, C1, C2 and
    leaving them all shown made each reading the sum of the previous ones
    (112 / 224 / 336 triangles). Each state has to be isolated;
  * closing "document 1" is not "closing my document". It shut the user's
    Product1.CATProduct. Only names starting with V0355_ are closed now.

The arrangement: three closed splines along Z, TWO lofts sharing the middle one,
joined. Each loft arrives at the shared section with its own cross-boundary
tangent, so the seam is "one geometry, two parametrisations" - the case the
0.3.53 report says healing should be tested on.

Every state is exported to its own STL and analysed here, so what is reported is
a mesh, not a tool's summary of it.
"""

from __future__ import annotations

import collections
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools

OUT = os.path.join(tempfile.gettempdir(), "b30_v0355_heal2")
PREFIX = "V0355_"


def say(text: str = "") -> None:
    print(text, flush=True)


def ring(radius: float, z: float, count: int = 36) -> list[list[float]]:
    return [[round(radius * math.cos(2 * math.pi * i / count), 4),
             round(radius * math.sin(2 * math.pi * i / count), 4), z]
            for i in range(count)]


def read_stl(path: str):
    faces, normal, verts = [], (0.0, 0.0, 0.0), []
    with open(path, "r", errors="replace") as handle:
        for line in handle:
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "facet":
                normal = (float(parts[2]), float(parts[3]), float(parts[4]))
                verts = []
            elif parts[0] == "vertex":
                verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
            elif parts[0] == "endfacet" and len(verts) == 3:
                faces.append((normal, verts))
    return faces


def summarise(path: str) -> dict:
    faces = read_stl(path)

    def key(point):
        return (round(point[0], 3), round(point[1], 3), round(point[2], 3))

    edges = collections.defaultdict(list)
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for normal, verts in faces:
        for vertex in verts:
            for axis in range(3):
                lo[axis] = min(lo[axis], vertex[axis])
                hi[axis] = max(hi[axis], vertex[axis])
        for a, b in ((0, 1), (1, 2), (2, 0)):
            ka, kb = key(verts[a]), key(verts[b])
            edges[(min(ka, kb), max(ka, kb))].append(normal)

    def angle(n1, n2):
        dot = sum(x * y for x, y in zip(n1, n2))
        na = math.sqrt(sum(x * x for x in n1)) or 1.0
        nb = math.sqrt(sum(x * x for x in n2)) or 1.0
        return math.degrees(math.acos(max(-1.0, min(1.0, dot / (na * nb)))))

    boundary = multi = 0
    folds = []
    seam = []
    for edge, normals in edges.items():
        if len(normals) == 1:
            boundary += 1
            continue
        if len(normals) > 2:
            multi += 1
            continue
        fold = angle(normals[0], normals[1])
        folds.append(fold)
        mid_z = (edge[0][2] + edge[1][2]) / 2.0
        if 19.0 <= mid_z <= 21.0:
            seam.append(fold)
    folds.sort()
    seam.sort()
    at = lambda values, q: (None if not values
                            else round(values[min(len(values) - 1,
                                                  int(q * len(values)))], 2))
    return {
        "triangles": len(faces),
        "interior": len(folds),
        "boundary": boundary,
        "non_manifold": multi,
        "median": at(folds, 0.5), "max": round(folds[-1], 2) if folds else None,
        "seam_edges": len(seam),
        "seam_median": at(seam, 0.5),
        "seam_max": round(seam[-1], 2) if seam else None,
        "extent": [round(hi[i] - lo[i], 3) for i in range(3)],
    }


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)

    for index in range(conn.documents.Count, 0, -1):
        try:
            name = str(conn.documents.Item(index).Name)
        except Exception:  # noqa: BLE001
            continue
        if name.startswith(PREFIX):
            conn.documents.Item(index).Activate()
            document.execute("catia_close_document", {"save": False})

    document.execute("catia_new_part",
                     {"name": "V0355_HEAL2",
                      "file_path": os.path.join(OUT, "V0355_HEAL2.CATPart")})
    if str(conn.active_document.Name) != "V0355_HEAL2.CATPart":
        raise RuntimeError(f"wrong active document: {conn.active_document.Name}")
    part = conn.get_active_part()

    for radius, z, name in ((10.0, 0.0, "H_S1"), (14.0, 20.0, "H_S2"),
                            (18.0, 40.0, "H_S3")):
        gsd.execute("catia_gsd_spline",
                    {"points": ring(radius, z), "closed": True, "name": name})
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["H_S1", "H_S2"], "name": "H_A"})
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["H_S2", "H_S3"], "name": "H_B"})
    gsd.execute("catia_gsd_join", {"elements": ["H_A", "H_B"], "name": "H_J"})
    say("built: 3 splines, 2 lofts sharing H_S2, joined as H_J")

    def all_shapes() -> list:
        shapes = []
        for index in range(1, part.HybridBodies.Count + 1):
            body = part.HybridBodies.Item(index)
            try:
                collection = body.HybridShapes
            except Exception:  # noqa: BLE001
                continue
            for item in range(1, collection.Count + 1):
                shapes.append(collection.Item(item))
        return shapes

    def only(target: str) -> None:
        """Show ``target`` and hide every other GSD element: SetShow(0)=show, (1)=hide."""
        for shape in all_shapes():
            wanted = str(shape.Name) == target
            try:
                conn.hso.Clear()
                conn.hso.Add(shape)
                conn.hso.VisProperties.SetShow(0 if wanted else 1)
            except Exception:  # noqa: BLE001 - visibility is best effort here
                pass
            finally:
                try:
                    conn.hso.Clear()
                except Exception:  # noqa: BLE001
                    pass

    def snapshot(label: str, target: str) -> dict:
        only(target)
        path = os.path.join(OUT, f"{target}.stl")
        if os.path.exists(path):
            os.remove(path)
        conn.active_document.ExportData(os.path.normpath(path), "stl")
        stats = summarise(path)
        say(f"   {label:<18} tris {stats['triangles']:>5}  interior {stats['interior']:>5}  "
            f"boundary {stats['boundary']:>4}  median {stats['median']}  max {stats['max']}  "
            f"| seam(z19-21) {stats['seam_edges']:>3} edges, median "
            f"{stats['seam_median']}, max {stats['seam_max']}  | box "
            f"{stats['extent'][0]} x {stats['extent'][1]} x {stats['extent'][2]}")
        return stats

    say("\n── states, each measured with only that element visible ──")
    before = snapshot("join H_J", "H_J")

    factory = gsd._factory()
    geoset = gsd._get_geoset()
    for continuity in (0, 1, 2):
        try:
            healed = factory.AddNewHealing(
                part.CreateReferenceFromObject(part.FindObjectByName("H_J")))
            geoset.AppendHybridShape(healed)
            healed.Name = f"H_HEAL_C{continuity}"
            try:
                healed.Continuity = continuity
            except Exception as error:  # noqa: BLE001
                say(f"   Continuity={continuity} not writable: "
                    f"{str(error).splitlines()[0][:70]}")
            part.UpdateObject(healed)
            # Read it back: "I set Continuity" is not evidence that CATIA kept
            # it, and the whole conclusion rests on the three values differing.
            try:
                stored = healed.Continuity
            except Exception as error:  # noqa: BLE001
                stored = f"unreadable ({str(error).splitlines()[0][:40]})"
            try:
                stored_type = healed.GetTypeInfo().GetDocumentation(-1)[0]
            except Exception:  # noqa: BLE001
                stored_type = "?"
            say(f"   heal C{continuity}: Continuity asked {continuity}, "
                f"stored {stored}, COM type {stored_type}")
            snapshot(f"heal C{continuity}", f"H_HEAL_C{continuity}")
        except Exception as error:  # noqa: BLE001
            say(f"   heal C{continuity}: FAILED - {str(error).splitlines()[0][:110]}")

    say("\n── does the healed surface still cover the same shape? ──")
    say(f"   join: triangles {before['triangles']}, extent {before['extent']}")
    say("   (a heal that closed the open ends would change the extent or the "
        "triangle count without reporting anything)")

    for index in range(conn.documents.Count, 0, -1):
        try:
            name = str(conn.documents.Item(index).Name)
        except Exception:  # noqa: BLE001
            continue
        if name.startswith(PREFIX):
            conn.documents.Item(index).Activate()
            document.execute("catia_close_document", {"save": False})
    say(f"\ndocuments open now: {conn.documents.Count}")


if __name__ == "__main__":
    main()
