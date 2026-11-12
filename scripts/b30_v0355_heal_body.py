"""Healing, driven properly this time: AddBodyToHeal + MergingDistance + Continuity.

0.3.51 concluded "AddNewHealing does not soften a G0 fold" from a feature that
had a Continuity and NOTHING TO HEAL: HybridShapeHealing exposes
AddBodyToHeal(iBody), plus SetMergingDistance / SetTangencyAngle /
SetDistanceObjective, and with no body registered the feature is a pass-through.
The probe in this repository made the same mistake twice - Continuity 0, 1 and 2
produced byte-identical meshes - before the type info showed what was missing.

So this measures both cases with a body registered:

  A. same-source seam: three closed splines, two lofts sharing the middle one,
     joined. The seam is "one geometry, two parametrisations", 0.5 deg.
  B. the 0.3.51 case: two planar walls meeting at 90 degrees, where a G0 fold is
     honest geometry and smoothing it would be a shape change.

Each state is exported to its own STL and analysed locally, with only the element
under test visible.
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

OUT = os.path.join(tempfile.gettempdir(), "b30_v0355_heal4")
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


def summarise(path: str, band: tuple[float, float] | None = None) -> dict:
    faces = read_stl(path)

    def key(point):
        return (round(point[0], 3), round(point[1], 3), round(point[2], 3))

    edges = collections.defaultdict(list)
    for normal, verts in faces:
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
    band_folds = []
    for edge, normals in edges.items():
        if len(normals) == 1:
            boundary += 1
            continue
        if len(normals) > 2:
            multi += 1
            continue
        fold = angle(normals[0], normals[1])
        folds.append(fold)
        if band is None:
            continue
        if band[0] <= (edge[0][2] + edge[1][2]) / 2.0 <= band[1]:
            band_folds.append(fold)
    folds.sort()
    band_folds.sort()
    at = lambda values, q: (None if not values
                            else round(values[min(len(values) - 1,
                                                  int(q * len(values)))], 2))
    return {"triangles": len(faces), "interior": len(folds), "boundary": boundary,
            "non_manifold": multi, "median": at(folds, 0.5),
            "max": round(folds[-1], 2) if folds else None,
            "band_edges": len(band_folds), "band_median": at(band_folds, 0.5),
            "band_max": round(band_folds[-1], 2) if band_folds else None}


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    # No document is open yet, so ask for the factory lazily.
    factory = lambda: gsd._factory()

    def fresh(name: str):
        for index in range(conn.documents.Count, 0, -1):
            try:
                existing = str(conn.documents.Item(index).Name)
            except Exception:  # noqa: BLE001
                continue
            if existing.startswith(PREFIX):
                conn.documents.Item(index).Activate()
                document.execute("catia_close_document", {"save": False})
        document.execute("catia_new_part",
                         {"name": name,
                          "file_path": os.path.join(OUT, f"{name}.CATPart")})
        if str(conn.active_document.Name) != f"{name}.CATPart":
            raise RuntimeError(f"wrong active document: {conn.active_document.Name}")
        return conn.get_active_part()

    def all_shapes(part):
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

    def only(part, target: str) -> None:
        for shape in all_shapes(part):
            wanted = str(shape.Name) == target
            try:
                conn.hso.Clear()
                conn.hso.Add(shape)
                conn.hso.VisProperties.SetShow(0 if wanted else 1)
            except Exception:  # noqa: BLE001
                pass
            finally:
                try:
                    conn.hso.Clear()
                except Exception:  # noqa: BLE001
                    pass

    def snapshot(part, label: str, target: str,
                 band: tuple[float, float] | None) -> dict:
        only(part, target)
        path = os.path.join(OUT, f"{target}.stl")
        if os.path.exists(path):
            os.remove(path)
        conn.active_document.ExportData(os.path.normpath(path), "stl")
        stats = summarise(path, band)
        say(f"   {label:<26} tris {stats['triangles']:>5} "
            f"interior {stats['interior']:>5} boundary {stats['boundary']:>4} "
            f"median {stats['median']} max {stats['max']}  "
            f"| band edges {stats['band_edges']:>3} "
            f"median {stats['band_median']} max {stats['band_max']}")
        return stats

    # ── A. same-source seam ───────────────────────────────────────────────
    say("=" * 78)
    say("A. two lofts sharing one closed spline, joined - a 0.5 deg seam")
    say("=" * 78)
    part = fresh("V0355_SEAM")
    for radius, z, name in ((10.0, 0.0, "A_S1"), (14.0, 20.0, "A_S2"),
                            (18.0, 40.0, "A_S3")):
        gsd.execute("catia_gsd_spline",
                    {"points": ring(radius, z), "closed": True, "name": name})
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["A_S1", "A_S2"], "name": "A_P1"})
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["A_S2", "A_S3"], "name": "A_P2"})
    gsd.execute("catia_gsd_join", {"elements": ["A_P1", "A_P2"], "name": "A_J"})
    seam_band = (19.0, 21.0)
    say("\n   states (only the element under test is visible):")
    before = snapshot(part, "joined, not healed", "A_J", seam_band)

    for continuity in (0, 1, 2):
        for distance in (1e-3, 0.05):
            try:
                target = part.FindObjectByName("A_J")
                reference = part.CreateReferenceFromObject(target)
                healed = factory().AddNewHealing(reference)
                healed.Name = f"A_H_{continuity}_{distance}".replace(".", "_")
                healed.Continuity = continuity
                # THE MISSING CALL: without a body to heal the feature does
                # nothing at all, whatever Continuity says.
                healed.AddBodyToHeal(reference)
                try:
                    healed.SetMergingDistance(distance)
                except Exception as error:  # noqa: BLE001
                    say(f"   merging distance rejected: "
                        f"{str(error).splitlines()[0][:60]}")
                gsd._get_geoset().AppendHybridShape(healed)
                part.UpdateObject(healed)
                stored = healed.Continuity
                bodies = healed.NoOfBodiesToHeal
                snapshot(part, f"C{continuity} merge {distance:g} "
                               f"({bodies} body)", f"A_H_{continuity}_{distance}".replace(".", "_"),
                         seam_band)
                if stored != continuity:
                    say(f"   NOTE: Continuity asked {continuity}, stored {stored}")
            except Exception as error:  # noqa: BLE001
                say(f"   C{continuity} merge {distance:g}: FAILED - "
                    f"{str(error).splitlines()[0][:110]}")

    say(f"\n   baseline: {before['triangles']} tris, {before['interior']} interior "
        f"edges, seam {before['band_edges']} edges at "
        f"median {before['band_median']} / max {before['band_max']} deg")

    # ── B. the 0.3.51 case, driven properly ───────────────────────────────
    say()
    say("=" * 78)
    say("B. two planar walls at 90 degrees (the 0.3.51 case, no body registered)")
    say("=" * 78)
    part = fresh("V0355_CORNER")
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [60, 0, 0],
                                   "name": "C_L1"})
    gsd.execute("catia_gsd_extrude", {"profile": "C_L1", "direction": [0, 0, 1],
                                      "limit1": 40.0, "name": "C_W1"})
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [0, 60, 0],
                                   "name": "C_L2"})
    gsd.execute("catia_gsd_extrude", {"profile": "C_L2", "direction": [0, 0, 1],
                                      "limit1": 40.0, "name": "C_W2"})
    gsd.execute("catia_gsd_join", {"elements": ["C_W1", "C_W2"], "name": "C_J"})
    say("\n   states:")
    before_b = snapshot(part, "joined, not healed", "C_J", None)
    for continuity in (1, 2):
        try:
            reference = part.CreateReferenceFromObject(part.FindObjectByName("C_J"))
            healed = factory().AddNewHealing(reference)
            healed.Name = f"C_H{continuity}"
            healed.Continuity = continuity
            healed.AddBodyToHeal(reference)
            try:
                healed.SetMergingDistance(0.05)
            except Exception:  # noqa: BLE001
                pass
            gsd._get_geoset().AppendHybridShape(healed)
            part.UpdateObject(healed)
            snapshot(part, f"C{continuity} with body", f"C_H{continuity}", None)
        except Exception as error:  # noqa: BLE001
            say(f"   C{continuity}: FAILED - {str(error).splitlines()[0][:110]}")
    say(f"\n   baseline: {before_b['triangles']} tris, max {before_b['max']} deg")

    # ── C. CONTROL: a real gap, to prove the feature is not inert ─────────
    say()
    say("=" * 78)
    say("C. control - two lofts with a 0.05 mm GAP at the seam")
    say("=" * 78)
    part = fresh("V0355_GAP")
    for radius, z, name in ((10.0, 0.0, "G_S1"), (14.0, 20.0, "G_S2a"),
                            (18.0, 40.0, "G_S3")):
        gsd.execute("catia_gsd_spline",
                    {"points": ring(radius, z), "closed": True, "name": name})
    # The upper patch starts 0.05 mm above where the lower one ends: same
    # radius, same shape, a gap a heel should be able to close.
    gsd.execute("catia_gsd_spline",
                {"points": ring(14.0, 20.05), "closed": True, "name": "G_S2b"})
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["G_S1", "G_S2a"], "name": "G_P1"})
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["G_S2b", "G_S3"], "name": "G_P2"})
    gsd.execute("catia_gsd_join", {"elements": ["G_P1", "G_P2"], "name": "G_J"})
    say("\n   states:")
    base = snapshot(part, "joined, gap 0.05", "G_J", (19.0, 21.0))
    for continuity, distance in ((1, 0.5), (1, 0.001)):
        try:
            reference = part.CreateReferenceFromObject(part.FindObjectByName("G_J"))
            healed = factory().AddNewHealing(reference)
            healed.Name = f"G_H_{continuity}_{distance}".replace(".", "_")
            healed.Continuity = continuity
            healed.AddBodyToHeal(reference)
            try:
                healed.SetMergingDistance(distance)
            except Exception:  # noqa: BLE001
                pass
            gsd._get_geoset().AppendHybridShape(healed)
            part.UpdateObject(healed)
            snapshot(part, f"C{continuity} merge {distance:g}",
                     f"G_H_{continuity}_{distance}".replace(".", "_"), (19.0, 21.0))
        except Exception as error:  # noqa: BLE001
            say(f"   C{continuity} merge {distance:g}: FAILED - "
                f"{str(error).splitlines()[0][:110]}")
    say(f"\n   baseline: {base['triangles']} tris, {base['boundary']} boundary "
        f"edges - a heal that closes the gap must reduce that number")

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
