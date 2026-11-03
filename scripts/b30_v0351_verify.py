"""After-the-change check for 0.3.51, on B30.

    1  catia_gsd_spline default (create_points false): the tree grows by ONE
       element, the curve is exact, and the part survives save + reopen;
    2  create_points=true still builds the Point features;
    3  deleting that one tree element leaves nothing behind (no cascade needed,
       because there were no orphan points to cascade to);
    4  catia_check_surface on a smooth cylinder, a folded join and a box - the
       median has to be the chording level, and the fold has to stand out;
    5  catia_delete_feature names[] + cascade over 90 points in one call, with
       the elapsed time (it was 49.9 s at 0.55 s per point before 0.3.51).
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402

OUT = os.path.join(tempfile.gettempdir(), "b30_v0351")
os.makedirs(OUT, exist_ok=True)


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


def close_prefix(document, conn, prefix="V0351_") -> None:
    while True:
        names = []
        for index in range(1, conn.documents.Count + 1):
            try:
                name = str(conn.documents.Item(index).Name)
            except Exception:  # noqa: BLE001
                continue
            if name.startswith(prefix):
                names.append(name)
        if not names:
            return
        activate(conn, names[0])
        document.execute("catia_close_document", {"save": False})


def main() -> int:
    conn = CATIAConnection()
    say(conn.connect())
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    part_design = PartDesignTools(conn)
    measurement = MeasurementTools(conn)
    close_prefix(document, conn)

    def fresh(name: str):
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

    def ring(radius, count, z=0.0):
        return [[radius * math.cos(2 * math.pi * i / count),
                 radius * math.sin(2 * math.pi * i / count), z] for i in range(count)]

    # ── 1. the default route ─────────────────────────────────────────────
    say("\n── 1. spline with create_points unset ──")
    path = fresh("V0351_A")
    geoset = gsd._get_geoset()
    say(f"1 tree before: {geoset.HybridShapes.Count}")
    started = time.perf_counter()
    say("1 " + gsd.execute("catia_gsd_spline",
                           {"points": ring(30.0, 90), "closed": True, "name": "A_SP"}))
    say(f"1 elapsed {time.perf_counter() - started:.2f} s")
    geoset = gsd._get_geoset()
    say(f"1 tree after: {geoset.HybridShapes.Count} element(s): "
        + ", ".join(str(geoset.HybridShapes.Item(i).Name)
                    for i in range(1, geoset.HybridShapes.Count + 1)))
    part = conn.get_active_part()

    def length_of(name):
        ref = part.CreateReferenceFromObject(part.FindObjectByName(name))
        return float(conn.active_document.GetWorkbench("SPAWorkbench")
                     .GetMeasurable(ref).Length)

    say(f"1 length {length_of('A_SP'):.3f} mm (analytic 188.496)")

    # ── 1b. save + reopen ────────────────────────────────────────────────
    say("1b save, close, reopen")
    document.execute("catia_save_document", {})
    activate(conn, "V0351_A.CATPart")
    document.execute("catia_close_document", {"save": False})
    document.execute("catia_open_document", {"file_path": path})
    activate(conn, "V0351_A.CATPart")
    part = conn.get_active_part()
    geoset = gsd._get_geoset()
    say(f"1b tree after reopen: {geoset.HybridShapes.Count}: "
        + ", ".join(str(geoset.HybridShapes.Item(i).Name)
                    for i in range(1, geoset.HybridShapes.Count + 1)))
    say(f"1b length after reopen {length_of('A_SP'):.3f} mm")
    part.Update()
    say(f"1c length after part.Update {length_of('A_SP'):.3f} mm")

    # ── 2. the opt-in route ──────────────────────────────────────────────
    say("\n── 2. create_points=true ──")
    fresh("V0351_B")
    geoset = gsd._get_geoset()
    say("2 " + gsd.execute("catia_gsd_spline",
                           {"points": ring(20.0, 12), "closed": True, "name": "B_SP",
                            "create_points": True}))
    say(f"2 tree after: {gsd._get_geoset().HybridShapes.Count} element(s) "
        f"(1 spline + 12 points expected)")

    # ── 3. deleting the tree-less spline leaves nothing ──────────────────
    say("\n── 3. delete the tree-less spline ──")
    activate(conn, "V0351_A.CATPart")
    say("3 " + part_design.execute("catia_delete_feature", {"feature_name": "A_SP"}))
    say(f"3 tree after delete: {gsd._get_geoset().HybridShapes.Count}")

    # ── 4. catia_check_surface ──────────────────────────────────────────
    say("\n── 4. catia_check_surface ──")
    fresh("V0351_C")
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": 20.0, "name": "C_C"})
    gsd.execute("catia_gsd_extrude", {"profile": "C_C", "direction": [0, 0, 1],
                                      "limit1": 40.0, "name": "C_CYL"})
    report = json.loads(measurement.execute("catia_check_surface", {}))
    say(f"4a smooth cylinder: {report['triangles']} tris, "
        f"median {report['fold_deg']['median']} deg, max {report['fold_deg']['max']}, "
        f"creases {report['creases']}, boundary {report['boundary_edges']}")
    say(f"4a scope: {report['scope']}")

    fresh("V0351_D")
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [40, 0, 0],
                                   "name": "D_L"})
    gsd.execute("catia_gsd_extrude", {"profile": "D_L", "direction": [0, 1, 0],
                                      "limit1": 40.0, "name": "D_FLAT"})
    gsd.execute("catia_gsd_extrude", {"profile": "D_L", "direction": [0, 0, 1],
                                      "limit1": 40.0, "name": "D_UP"})
    gsd.execute("catia_gsd_join", {"elements": ["D_FLAT", "D_UP"], "name": "D_J"})
    part = conn.get_active_part()
    sel = conn.hso
    for name in ("D_FLAT", "D_UP", "D_L"):
        sel.Clear()
        sel.Add(part.FindObjectByName(name))
        sel.VisProperties.SetShow(1)
        sel.Clear()
    report = json.loads(measurement.execute("catia_check_surface", {}))
    say(f"4b 90 deg join: {report['triangles']} tris, "
        f"median {report['fold_deg']['median']} deg, max {report['fold_deg']['max']}, "
        f"creases {report['creases']}, boundary {report['boundary_edges']}")
    if report["worst_creases"]:
        say(f"4b worst crease: {report['worst_creases'][0]}")

    # ── 5. batch delete of 90 points ────────────────────────────────────
    say("\n── 5. delete 90 orphan points in one call ──")
    fresh("V0351_E")
    gsd.execute("catia_gsd_spline", {"points": ring(18.0, 90), "closed": True,
                                     "name": "E_SP", "create_points": True})
    geoset = gsd._get_geoset()
    total = geoset.HybridShapes.Count
    names = [str(geoset.HybridShapes.Item(i).Name) for i in range(1, total + 1)]
    say(f"5 tree holds {total} element(s)")
    started = time.perf_counter()
    reply = part_design.execute("catia_delete_feature",
                                {"names": names, "cascade": True})
    elapsed = time.perf_counter() - started
    say(f"5 one names[] call over {len(names)} elements: {elapsed:.1f} s "
        f"(0.3.50 measured 49.9 s for 91)")
    say("5 reply head: " + reply.split("\n")[0][:120])
    say(f"5 tree now: {gsd._get_geoset().HybridShapes.Count}")

    close_prefix(document, conn)
    say(f"\ndocuments open: {conn.documents.Count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
