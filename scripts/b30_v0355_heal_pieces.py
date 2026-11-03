"""Healing the PIECES, not the join - which is what the operator is for.

Measured before this: AddNewHealing on an already-JOINED surface, with
AddBodyToHeal and MergingDistance set, produced a byte-identical mesh at
Continuity 0, 1 and 2 (112 triangles, 136 interior edges, 0.52 deg at the seam,
unchanged). The likely reason is that a join has already done the stitching, so
healing it has nothing left to do - and the 0.3.51 test had the same shape of
mistake twice over: no body registered at all.

So this asks the question the operator actually answers: given TWO separate
surfaces, does healing at Continuity 1/2 + a merging distance turn them into one
smooth surface, and does it change the fold across the shared edge?

  1. pieces that share a section exactly (0.5 deg tangent break, no gap);
  2. pieces with a 0.05 mm gap (a join refuses these - no shared edge);
  3. two walls at 90 degrees, where a G0 fold is the honest geometry.
"""

from __future__ import annotations

import collections
import math
import os
import sys
import tempfile

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools

OUT = os.path.join(tempfile.gettempdir(), "b30_v0355_pieces")
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


def summarise(path: str, band=None) -> dict:
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
    folds, band_folds, seam_edges = [], [], 0
    for edge, normals in edges.items():
        if len(normals) == 1:
            boundary += 1
            if band and band[0] <= (edge[0][2] + edge[1][2]) / 2.0 <= band[1]:
                seam_edges += 1
            continue
        if len(normals) > 2:
            multi += 1
            continue
        fold = angle(normals[0], normals[1])
        folds.append(fold)
        if band and band[0] <= (edge[0][2] + edge[1][2]) / 2.0 <= band[1]:
            band_folds.append(fold)
    folds.sort()
    band_folds.sort()
    at = lambda values, q: (None if not values
                            else round(values[min(len(values) - 1,
                                                  int(q * len(values)))], 2))
    return {"triangles": len(faces), "interior": len(folds), "boundary": boundary,
            "non_manifold": multi, "median": at(folds, 0.5),
            "max": round(folds[-1], 2) if folds else None,
            "band_seam_edges": seam_edges, "band_interior": len(band_folds),
            "band_median": at(band_folds, 0.5), "band_max": at(band_folds, 0.99)}


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
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

    def shapes(part):
        out = []
        for index in range(1, part.HybridBodies.Count + 1):
            body = part.HybridBodies.Item(index)
            try:
                collection = body.HybridShapes
            except Exception:  # noqa: BLE001
                continue
            for item in range(1, collection.Count + 1):
                out.append(collection.Item(item))
        return out

    def only(part, targets: tuple[str, ...]) -> None:
        for shape in shapes(part):
            wanted = str(shape.Name) in targets
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

    def snapshot(part, label: str, targets: tuple[str, ...], band=None) -> dict:
        only(part, targets)
        path = os.path.join(OUT, "state.stl")
        if os.path.exists(path):
            os.remove(path)
        conn.active_document.ExportData(os.path.normpath(path), "stl")
        stats = summarise(path, band)
        say(f"   {label:<28} tris {stats['triangles']:>5} "
            f"interior {stats['interior']:>5} boundary {stats['boundary']:>4}  "
            f"median {stats['median']} max {stats['max']}"
            + ("" if band is None else
               f"  | seam: {stats['band_seam_edges']} open edges, "
               f"{stats['band_interior']} interior, max {stats['band_max']}"))
        return stats

    def heal(part, patch_names: tuple[str, ...], continuity: int,
             distance: float, label: str, band=None) -> None:
        reference = part.CreateReferenceFromObject(
            part.FindObjectByName(patch_names[0]))
        healed = factory().AddNewHealing(reference)
        healed.Name = "H_" + label.replace(" ", "_").replace(".", "_")
        healed.Continuity = continuity
        for name in patch_names:
            healed.AddBodyToHeal(
                part.CreateReferenceFromObject(part.FindObjectByName(name)))
        try:
            healed.SetMergingDistance(distance)
        except Exception as error:  # noqa: BLE001
            say(f"   merging distance {distance} rejected: "
                f"{str(error).splitlines()[0][:60]}")
        gsd._get_geoset().AppendHybridShape(healed)
        part.UpdateObject(healed)
        say(f"   ({healed.NoOfBodiesToHeal} bodies registered, "
            f"Continuity stored {healed.Continuity})")
        snapshot(part, label, (str(healed.Name),), band)

    # ── 1. exact shared section ───────────────────────────────────────────
    say("=" * 78)
    say("1. two lofts sharing a section exactly - 0.5 deg tangent break, no gap")
    say("=" * 78)
    part = fresh("V0355_P1")
    for radius, z, name in ((10.0, 0.0, "P_S1"), (14.0, 20.0, "P_S2"),
                            (18.0, 40.0, "P_S3")):
        gsd.execute("catia_gsd_spline",
                    {"points": ring(radius, z), "closed": True, "name": name})
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["P_S1", "P_S2"], "name": "P_A"})
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["P_S2", "P_S3"], "name": "P_B"})
    band = (19.0, 21.0)
    say("\n   states:")
    snapshot(part, "two patches, not joined", ("P_A", "P_B"), band)
    gsd.execute("catia_gsd_join", {"elements": ["P_A", "P_B"], "name": "P_J"})
    snapshot(part, "joined S_P_A + P_B", ("P_J",), band)
    for continuity in (1, 2):
        try:
            heal(part, ("P_A", "P_B"), continuity, 0.05,
                 f"heal pieces C{continuity}", band)
        except Exception as error:  # noqa: BLE001
            say(f"   heal pieces C{continuity}: FAILED - "
                f"{str(error).splitlines()[0][:110]}")

    # ── 2. a 0.05 mm gap ──────────────────────────────────────────────────
    say()
    say("=" * 78)
    say("2. the same two lofts with a 0.05 mm GAP (a join refuses these)")
    say("=" * 78)
    part = fresh("V0355_P2")
    for radius, z, name in ((10.0, 0.0, "Q_S1"), (14.0, 20.0, "Q_S2a"),
                            (18.0, 40.0, "Q_S3")):
        gsd.execute("catia_gsd_spline",
                    {"points": ring(radius, z), "closed": True, "name": name})
    gsd.execute("catia_gsd_spline",
                {"points": ring(14.0, 20.05), "closed": True, "name": "Q_S2b"})
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["Q_S1", "Q_S2a"], "name": "Q_A"})
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["Q_S2b", "Q_S3"], "name": "Q_B"})
    say("\n   states:")
    snapshot(part, "two patches, 0.05 mm apart", ("Q_A", "Q_B"), band)
    for continuity, distance in ((1, 0.5), (2, 0.5), (1, 0.001)):
        try:
            heal(part, ("Q_A", "Q_B"), continuity, distance,
                 f"heal C{continuity} merge {distance:g}", band)
        except Exception as error:  # noqa: BLE001
            say(f"   heal C{continuity} merge {distance:g}: FAILED - "
                f"{str(error).splitlines()[0][:110]}")

    # ── 3. 90 degrees ─────────────────────────────────────────────────────
    say()
    say("=" * 78)
    say("3. two walls at 90 degrees - the 0.3.51 case")
    say("=" * 78)
    part = fresh("V0355_P3")
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [60, 0, 0],
                                   "name": "R_L1"})
    gsd.execute("catia_gsd_extrude", {"profile": "R_L1", "direction": [0, 0, 1],
                                      "limit1": 40.0, "name": "R_A"})
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [0, 60, 0],
                                   "name": "R_L2"})
    gsd.execute("catia_gsd_extrude", {"profile": "R_L2", "direction": [0, 0, 1],
                                      "limit1": 40.0, "name": "R_B"})
    say("\n   states:")
    snapshot(part, "two walls, not joined", ("R_A", "R_B"), None)
    for continuity in (1, 2):
        try:
            heal(part, ("R_A", "R_B"), continuity, 0.05,
                 f"heal walls C{continuity}", None)
        except Exception as error:  # noqa: BLE001
            say(f"   heal walls C{continuity}: FAILED - "
                f"{str(error).splitlines()[0][:110]}")

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
