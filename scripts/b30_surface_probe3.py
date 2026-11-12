"""Third round on the 0.3.48 report: three loose ends.

    L  what a Measurable will actually report for a section curve, a datum point
       and a split SURFACE - round 2 showed Area answers 0.0 for a surface, so
       "which side was kept" cannot be read from Area.
    M  the kept side, ground truth: the STL of the split result. The mesh route
       exports visible surfaces (that is the defect in D), which makes it a
       usable ruler here: min/max z says above-the-cut or below it.
    N  join after split: does cutting a surface first really break the join with
       the neighbour that shared that edge?
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

OUT = os.path.join(tempfile.gettempdir(), "b30_loft_claims")
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


def fresh(name: str, document: DocumentTools, conn):
    # Snapshot the names FIRST: closing a document renumbers the collection,
    # and Item(i) on the stale count answers "方法 Item 失败".
    names = []
    for index in range(1, conn.documents.Count + 1):
        try:
            names.append(str(conn.documents.Item(index).Name))
        except Exception:  # noqa: BLE001
            pass
    if f"{name}.CATPart" in names:
        activate(conn, f"{name}.CATPart")
        document.execute("catia_close_document", {"save": False})
    path = os.path.join(OUT, f"{name}.CATPart")
    document.execute("catia_new_part", {"name": name, "file_path": path})
    activate(conn, f"{name}.CATPart")


def stl_z(path: str) -> tuple[float, float]:
    low, high = 1e9, -1e9
    with open(path, "r", errors="ignore") as handle:
        for line in handle:
            line = line.strip()
            if not line.startswith("vertex"):
                continue
            z = float(line.split()[3])
            low, high = min(low, z), max(high, z)
    return round(low, 3), round(high, 3)


def main() -> int:
    conn = CATIAConnection()
    say(conn.connect())
    document = DocumentTools(conn)
    gsd = GSDTools(conn)

    # ── L. what a Measurable answers, per kind of element ────────────────
    say("\n── L. Measurable on a curve, a point, a split surface ──")
    fresh("V0348_L", document, conn)
    spa = conn.app.ActiveDocument.GetWorkbench("SPAWorkbench")
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": 10.0, "name": "L_C"})
    gsd.execute("catia_gsd_point", {"x": 0, "y": 0, "z": 5, "name": "L_P"})
    gsd.execute("catia_gsd_extrude", {"profile": "L_C", "direction": [0, 0, 1],
                                     "limit1": 40.0, "name": "L_CYL"})
    gsd.execute("catia_gsd_plane_offset", {"base_plane": "xy", "offset": 10.0,
                                           "name": "L_MID"})
    gsd.execute("catia_gsd_split", {"element": "L_CYL", "cutter": "L_MID",
                                    "name": "L_UPPER"})
    part = conn.get_active_part()
    for name in ("L_C", "L_P", "L_CYL", "L_UPPER"):
        measurable = spa.GetMeasurable(part.CreateReferenceFromObject(
            part.FindObjectByName(name)))
        values = []
        for attribute in ("Length", "Area", "Volume"):
            try:
                values.append(f"{attribute} {float(getattr(measurable, attribute)):.2f}")
            except Exception:  # noqa: BLE001
                values.append(f"{attribute} n/a")
        cog = "n/a"
        for attribute in ("Point", "Point2", "CenterOfGravity"):
            try:
                point = getattr(measurable, attribute)
                cog = f"{attribute} [{float(point[0]):.1f}, {float(point[1]):.1f}, " \
                      f"{float(point[2]):.1f}]"
                break
            except Exception:  # noqa: BLE001
                continue
        say(f"L {name:<8}: {', '.join(values)}  cog {cog}")

    # ── M. keot side, ground truth from the STL of the split result ──────
    say("\n── M. which side does orientation +1 keep? (STL z range) ──")
    fresh("V0348_M", document, conn)
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": 10.0, "name": "M_C"})
    gsd.execute("catia_gsd_extrude", {"profile": "M_C", "direction": [0, 0, 1],
                                     "limit1": 40.0, "name": "M_CYL"})
    gsd.execute("catia_gsd_plane_offset", {"base_plane": "xy", "offset": 10.0,
                                           "name": "M_MID"})
    part_design = PartDesignTools(conn)
    for reverse in (False, True):
        tag = "R" if reverse else "N"
        gsd.execute("catia_gsd_split", {"element": "M_CYL", "cutter": "M_MID",
                                        "reverse": reverse, "name": f"M_CUT{tag}"})
        # Show only the cut: hide the full cylinder, then read the STL.
        part_design.execute("catia_set_visibility", {"name": "M_CYL", "show": False})
        path = os.path.join(OUT, f"side_{tag}.stl")
        if os.path.exists(path):
            os.remove(path)
        conn.active_document.ExportData(path, "stl")
        part_design.execute("catia_set_visibility", {"name": "M_CYL", "show": True})
        part_design.execute("catia_set_visibility", {"name": f"M_CUT{tag}", "show": False})
        say(f"M reverse={reverse!s:<5}: kept z {stl_z(path)} "
            f"(above the cut is 10..40, below is 0..10)")

    # ── N. join after split ──────────────────────────────────────────────
    say("\n── N. join after split: does cutting first break the shared edge? ──")
    fresh("V0348_N", document, conn)
    # Two coaxial surfaces that really DO share an edge: both start from the
    # same r10 circle at z=20. Round 1 used r10 and r12 there - different
    # circles do not share an edge, so that run proved nothing.
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": 10.0, "name": "N_C0"})
    gsd.execute("catia_gsd_extrude", {"profile": "N_C0", "direction": [0, 0, 1],
                                      "limit1": 20.0, "name": "N_LOW"})
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 20], "support_plane": "xy",
                                     "radius": 10.0, "name": "N_C1"})
    gsd.execute("catia_gsd_extrude", {"profile": "N_C1", "direction": [0, 0, 1],
                                      "limit1": 20.0, "name": "N_HIGH"})
    try:
        gsd.execute("catia_gsd_join", {"elements": ["N_LOW", "N_HIGH"], "name": "N_J1"})
        say("N1 join(LOW, HIGH) untouched   : ok")
    except Exception as error:  # noqa: BLE001
        say(f"N1 join(LOW, HIGH) untouched   : {str(error).splitlines()[0][:130]}")
    # Other order: split LOW first. Its top edge at z=20 is cut away from the
    # kept piece, so nothing is left to join with HIGH.
    gsd.execute("catia_gsd_plane_offset", {"base_plane": "xy", "offset": 10.0,
                                           "name": "N_MID"})
    gsd.execute("catia_gsd_split", {"element": "N_LOW", "cutter": "N_MID",
                                    "name": "N_LOW_TOP"})
    try:
        gsd.execute("catia_gsd_join", {"elements": ["N_LOW_TOP", "N_HIGH"],
                                       "name": "N_J2"})
        say("N2 join(split first, HIGH)     : ok")
    except Exception as error:  # noqa: BLE001
        say(f"N2 join(split first, HIGH)     : {str(error).splitlines()[0][:130]}")
    # And the order that works: join, then split - what the report switched to.
    try:
        gsd.execute("catia_gsd_join", {"elements": ["N_LOW", "N_HIGH"], "name": "N_J3"})
        gsd.execute("catia_gsd_split", {"element": "N_J3", "cutter": "N_MID",
                                        "name": "N_J3_TOP"})
        say("N3 join first, then split      : ok")
    except Exception as error:  # noqa: BLE001
        say(f"N3 join first, then split      : {str(error).splitlines()[0][:130]}")

    # ── O. the report's exact view sequence, including fit_all ───────────
    say("\n── O. set_view -> fit_all -> screenshot (the report's own order) ──")
    import hashlib

    from catia_mcp.tools.export import ExportTools  # noqa: E402
    export = ExportTools(conn)
    fresh("V0348_O", document, conn)
    sketcher = SketcherTools(conn)
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": -30, "y1": -8, "x2": 30, "y2": 8})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 40.0})
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 40], "support_plane": "xy",
                                     "radius": 6.0, "name": "O_C"})
    gsd.execute("catia_gsd_extrude", {"profile": "O_C", "direction": [0, 0, 1],
                                      "limit1": 25.0, "name": "O_TOWER"})
    seen = {}
    for view in ("front", "bottom", "isometric"):
        export.execute("catia_set_view", {"view": view})
        export.execute("catia_fit_all", {})
        path = os.path.join(OUT, f"o_{view}.jpg")
        if os.path.exists(path):
            os.remove(path)
        export.execute("catia_screenshot", {"file_path": path})
        with open(path, "rb") as handle:
            seen[view] = hashlib.md5(handle.read()).hexdigest()[:12]
        say(f"O {view:<10}: {seen[view]}  {os.path.getsize(path)} B")
    say(f"O distinct captures: {len(set(seen.values()))} of {len(seen)}")

    say("\nDocuments open: " + str(conn.documents.Count))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
