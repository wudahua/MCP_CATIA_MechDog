"""Does catia_gsd_healing soften a G0 seam? (v2 - the first attempt was wrong twice)

Fixed here:
  * the fold angle across an interior edge is the angle between the two outward
    normals, NOT 180 minus it - v1 reported 180 deg for every edge including the
    flat ones, which is why nothing ever moved;
  * ExportData writes every VISIBLE element, so measuring "the joined surface"
    also measured the two source surfaces stacked on top of it. Every capture
    now hides the inputs and shows only the object under test.

    A  join of two planes at 90 deg            -> baseline
    B  the same join, healed with Continuity=1
    C  the same join, healed with Continuity=2
    D  two coaxial cylinders r=10 / r=16, joined (a step, not a fold)
"""

from __future__ import annotations

import json
import math
import os
import struct
import sys
import tempfile

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402

OUT = os.path.join(tempfile.gettempdir(), "b30_v0351")


def say(line: str = "") -> None:
    print(line, flush=True)


def activate(conn, name: str):
    for index in range(1, conn.documents.Count + 1):
        candidate = conn.documents.Item(index)
        if str(candidate.Name) == name:
            candidate.Activate()
            if str(conn.active_document.Name) != name:
                raise RuntimeError(f"could not activate {name}")
            return candidate
    raise RuntimeError(f"{name} is not open")


def close_probes(document, conn) -> None:
    while True:
        names = []
        for index in range(1, conn.documents.Count + 1):
            try:
                name = str(conn.documents.Item(index).Name)
            except Exception:  # noqa: BLE001
                continue
            if name.startswith("PROBE_"):
                names.append(name)
        if not names:
            return
        activate(conn, names[0])
        document.execute("catia_close_document", {"save": False})


def triangles(path: str):
    blob = open(path, "rb").read()
    if len(blob) >= 84:
        count = struct.unpack("<I", blob[80:84])[0]
        if count and len(blob) == 84 + 50 * count:
            out = []
            for index in range(count):
                values = struct.unpack("<12f", blob[84 + 50 * index:84 + 50 * index + 48])
                out.append(((values[0], values[1], values[2]),
                            [tuple(values[3 + 3 * k:6 + 3 * k]) for k in range(3)]))
            return out
    out = []
    normal = (0.0, 0.0, 0.0)
    verts: list = []
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


def key(point):
    return (round(point[0], 3), round(point[1], 3), round(point[2], 3))


def report(path: str, label: str) -> dict:
    faces = triangles(path)
    edges: dict = {}
    for normal, verts in faces:
        for a, b in ((0, 1), (1, 2), (2, 0)):
            ka, kb = key(verts[a]), key(verts[b])
            edges.setdefault((min(ka, kb), max(ka, kb)), []).append(normal)

    def angle(n1, n2) -> float:
        dot = sum(x * y for x, y in zip(n1, n2))
        na = math.sqrt(sum(x * x for x in n1)) or 1.0
        nb = math.sqrt(sum(x * x for x in n2)) or 1.0
        return math.degrees(math.acos(max(-1.0, min(1.0, dot / (na * nb)))))

    angles, boundary, extra = [], 0, 0
    worst = (0.0, None)
    for edge, normals in edges.items():
        if len(normals) == 1:
            boundary += 1
            continue
        if len(normals) > 2:
            extra += 1
            continue
        fold = angle(normals[0], normals[1])
        angles.append(fold)
        if fold > worst[0]:
            worst = (fold, edge)
    angles.sort()

    def at(q):
        return round(angles[min(len(angles) - 1, int(q * len(angles)))], 2) if angles else None

    say(f"{label}")
    say(f"    {len(faces)} triangles | interior edges {len(angles)} | boundary {boundary} "
        f"| >2-face edges {extra}")
    say(f"    fold deg: median {at(0.5)}  p90 {at(0.9)}  p99 {at(0.99)}  "
        f"max {round(worst[0], 2)}")
    return {"faces": len(faces), "max": round(worst[0], 2), "p99": at(0.99),
            "median": at(0.5), "boundary": boundary, "multi": extra}


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    close_probes(document, conn)

    def fresh(name):
        path = os.path.join(OUT, f"{name}.CATPart")
        for index in range(1, conn.documents.Count + 1):
            if str(conn.documents.Item(index).Name) == f"{name}.CATPart":
                activate(conn, f"{name}.CATPart")
                document.execute("catia_close_document", {"save": False})
        if os.path.exists(path):
            os.remove(path)
        document.execute("catia_new_part", {"name": name, "file_path": path})
        activate(conn, f"{name}.CATPart")
        return path

    def show_only(keep: list[str], hide: list[str]) -> None:
        part = conn.get_active_part()
        sel = conn.hso
        for name in hide:
            try:
                sel.Clear()
                sel.Add(part.FindObjectByName(name))
                sel.VisProperties.SetShow(1)
                sel.Clear()
            except Exception as error:  # noqa: BLE001
                say(f"    hide {name} FAILED {str(error).splitlines()[0][:70]}")
        for name in keep:
            try:
                sel.Clear()
                sel.Add(part.FindObjectByName(name))
                sel.VisProperties.SetShow(0)
                sel.Clear()
            except Exception as error:  # noqa: BLE001
                say(f"    show {name} FAILED {str(error).splitlines()[0][:70]}")

    def capture(label) -> dict:
        path = os.path.join(OUT, f"{label}.stl")
        if os.path.exists(path):
            os.remove(path)
        conn.get_active_part().Update()
        conn.active_document.ExportData(os.path.normpath(path), "stl")
        say(f"    ({os.path.getsize(path)} bytes)")
        return report(path, label.split("_", 1)[-1])

    results = {}

    def build(base: str, continuity: int | None) -> None:
        fresh(f"PROBE_{base}")
        gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [40, 0, 0],
                                       "name": f"{base}_L"})
        gsd.execute("catia_gsd_extrude", {"profile": f"{base}_L",
                                          "direction": [0, 1, 0],
                                          "limit1": 40.0, "name": f"{base}_FLAT"})
        gsd.execute("catia_gsd_extrude", {"profile": f"{base}_L",
                                          "direction": [0, 0, 1],
                                          "limit1": 40.0, "name": f"{base}_UP"})
        gsd.execute("catia_gsd_join", {"elements": [f"{base}_FLAT", f"{base}_UP"],
                                       "name": f"{base}_J"})
        show_only([f"{base}_J"], [f"{base}_FLAT", f"{base}_UP", f"{base}_L"])
        results[f"{base}_join"] = capture(f"{base}_join")

        if continuity is None:
            return
        part = conn.get_active_part()
        factory = gsd._factory()
        geoset = gsd._get_geoset()
        joined = part.CreateReferenceFromObject(part.FindObjectByName(f"{base}_J"))
        healing = factory.AddNewHealing(joined)
        try:
            healing.Continuity = continuity
        except Exception as error:  # noqa: BLE001
            say(f"    Continuity={continuity} refused: {str(error).splitlines()[0][:70]}")
        geoset.AppendHybridShape(healing)
        healing.Name = f"{base}_H"
        part.InWorkObject = healing
        part.UpdateObject(healing)
        for field, value in (("Distance", 0.5), ("Distance Tolerence", 0.5),
                             ("DistanceTolerance", 0.5)):
            try:
                setattr(healing, field, value)
                part.UpdateObject(healing)
                say(f"    {field} = {value} accepted")
                break
            except Exception:  # noqa: BLE001
                continue
        show_only([f"{base}_H"], [f"{base}_J"])
        results[f"{base}_healed"] = capture(f"{base}_healed")

    say("\n── A. two planes at 90 deg, joined, not healed ──")
    build("A", None)
    for continuity, base in ((1, "B"), (2, "C")):
        say(f"\n── {base}. the same join, healed Continuity={continuity} ──")
        build(base, continuity)

    say("\n── summary ──")
    say(json.dumps(results, indent=2))
    close_probes(document, conn)
    say(f"\ndocuments open: {conn.documents.Count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
