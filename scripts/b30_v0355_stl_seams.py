"""What the five-petal bottle's STL says about its five seams - no CATIA needed.

B30 cannot open these CATParts: the model was built on V5-6R2022 B32 SP6 and this
machine runs B30, so `Documents.Open` fails. The STL is not version-bound and
carries exactly the tessellated geometry the surface check reads, so the seam
question can be answered from it:

  * a "parametrisation break" (two patches meeting tangentially, only the edge
    doubling) reads ~0 deg across the seam;
  * two lofts meeting at a SHARED section, each with its own cross-boundary
    tangent, read whatever those tangents differ by - a real, if small, crease.

The seams are rings, so creases cluster at a handful of radii. This prints that
histogram for v5 and v6 and the worst creases in each cluster.
"""

from __future__ import annotations

import collections
import math
import sys

STLS = (
    (r"F:\WorkBuddy Space\2026-10-20-13-31-14\他人反馈的问题\CA建模-可乐瓶五爪花瓣瓶底"
     r"\ColaBottle_PetalBase_v5.stl", "v5 (22 deg valley)"),
    (r"F:\WorkBuddy Space\2026-10-20-13-31-14\他人反馈的问题\CA建模-可乐瓶五爪花瓣瓶底"
     r"\ColaBottle_PetalBase_v6.stl", "v6 (8.50 mm valley, R3)"),
)


def triangles(path: str):
    """ASCII STL -> [(normal, [v0, v1, v2])]. CATIA writes ASCII 'solid CATIA STL'."""
    faces = []
    normal = (0.0, 0.0, 0.0)
    verts = []
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


def analyse(path: str, label: str) -> None:
    faces = triangles(path)
    print(f"\n{'=' * 74}\n{label}: {len(faces)} triangles\n{'=' * 74}", flush=True)

    def key(point):
        return (round(point[0], 3), round(point[1], 3), round(point[2], 3))

    edges = collections.defaultdict(list)
    for normal, verts in faces:
        centroid = [sum(v[i] for v in verts) / 3.0 for i in range(3)]
        radius = math.hypot(centroid[0], centroid[1])
        n_r = ((normal[0] * centroid[0] + normal[1] * centroid[1]) / radius
               if radius > 1e-9 else 0.0)
        side = "outer" if n_r >= 0.5 else "inner" if n_r <= -0.5 else "axial"
        for a, b in ((0, 1), (1, 2), (2, 0)):
            ka, kb = key(verts[a]), key(verts[b])
            edges[(min(ka, kb), max(ka, kb))].append((normal, side))

    def angle(n1, n2):
        dot = sum(x * y for x, y in zip(n1, n2))
        na = math.sqrt(sum(x * x for x in n1)) or 1.0
        nb = math.sqrt(sum(x * x for x in n2)) or 1.0
        return math.degrees(math.acos(max(-1.0, min(1.0, dot / (na * nb)))))

    boundary = multi = 0
    folds = []
    creases = []
    for edge, entries in edges.items():
        if len(entries) == 1:
            boundary += 1
            continue
        if len(entries) > 2:
            multi += 1
            continue
        fold = angle(entries[0][0], entries[1][0])
        mid = [(edge[0][i] + edge[1][i]) / 2.0 for i in range(3)]
        radius = math.hypot(mid[0], mid[1])
        sides = {entries[0][1], entries[1][1]}
        side = sides.pop() if len(sides) == 1 else "mixed"
        folds.append(fold)
        creases.append({"fold": fold, "r": radius, "z": mid[2],
                        "theta": math.degrees(math.atan2(mid[1], mid[0])) % 360.0,
                        "side": side})

    folds.sort()
    at = lambda q: folds[min(len(folds) - 1, int(q * len(folds)))]
    print(f"interior {len(folds)}  boundary {boundary}  non-manifold {multi}")
    print(f"fold deg: median {at(0.5):.2f}  p90 {at(0.9):.2f}  "
          f"p99 {at(0.99):.2f}  max {folds[-1]:.2f}")

    by_side = collections.defaultdict(list)
    for crease in creases:
        by_side[crease["side"]].append(crease["fold"])
    for side, values in sorted(by_side.items()):
        ordered = sorted(values)
        print(f"   {side:<6} edges {len(ordered):>6}  median "
              f"{ordered[len(ordered) // 2]:>6.2f}  max {ordered[-1]:>7.2f}")

    # Creases are where a caller would look, and they cluster on the seam rings.
    print("\ncreases >= 25 deg, per 0.5 mm radius band (top 12 bands):")
    bands = collections.Counter()
    for crease in creases:
        if crease["fold"] >= 25.0:
            bands[round(crease["r"] * 2) / 2.0] += 1
    for radius, count in bands.most_common(12):
        print(f"   r {radius:6.1f} mm: {count:>5}")

    print("\ncreases in the 1..25 deg range, per 0.5 mm radius band (top 12):")
    soft = collections.Counter()
    for crease in creases:
        if 1.0 <= crease["fold"] < 25.0:
            soft[round(crease["r"] * 2) / 2.0] += 1
    for radius, count in soft.most_common(12):
        worst = max((c for c in creases
                     if 1.0 <= c["fold"] < 25.0
                     and round(c["r"] * 2) / 2.0 == radius),
                    key=lambda c: c["fold"])
        print(f"   r {radius:6.1f} mm: {count:>5}   worst {worst['fold']:6.2f} deg "
              f"at z {worst['z']:8.2f}, theta {worst['theta']:7.2f}, {worst['side']}")


def main() -> None:
    for path, label in STLS:
        try:
            analyse(path, label)
        except Exception as error:  # noqa: BLE001
            print(f"{label}: failed - {str(error).splitlines()[0][:120]}")


if __name__ == "__main__":
    main()
