"""Petaloid bottle base - phase 5: assemble a watertight shell and thicken it.

Facts this build depends on, all measured on B30 2026-11-04:

1. `AddNewRevol(profile, angle1, angle2, axis)`: angle1 is the SWEEP and angle2
   sweeps the other way from the profile's own plane - (72, 72) is a 144 degree
   surface from -72 to +72, not a sector starting at 72. So each 72 degree sector
   is aimed by rotating the PROFILE.
2. A surface that a closed tube has already cut cannot be cut again: the second
   split/trim fails UpdateObject in every reverse combination, while the same
   cut on a fresh surface succeeds and chained PLANE cuts succeed.
3. On a small sector the `reverse=False` side of that split is the plug inside
   the tube (it does not touch the rim, so it cannot join the skirt); the sector
   with its foot hole is `reverse=True`.

Run with -u. Owns only PETAL_* documents.
"""
import json
import math
import sys

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.export import ExportTools
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.measurement import MeasurementTools
from catia_mcp.tools.part_design import PartDesignTools

TMP = r"C:\Users\ADMINI~1\AppData\Local\Temp\bottle059"
R_BODY = 33.0
FEET = 5
SECTOR = 360.0 / FEET
FOOT_HALF = 25.0
TREAD_Z = -33.8
ROOT_Z = -12.0

DISH_KEYS = [(33.0, 0.0), (32.0, -8.0), (30.5, -12.0), (28.0, -15.0),
             (24.0, -16.5), (18.0, -17.0), (12.0, -17.0), (8.0, -17.0)]
# the centre starts with a flat lead-in, so the dome does not leave the dish
# vertically (a 90 deg crease there measured 77 deg on the first build)
CENTRE_KEYS = [(8.0, -17.0), (7.6, -16.75), (6.8, -15.4), (5.6, -13.0),
               (4.2, -11.2), (3.2, -9.9), (3.0, -9.42), (3.0, -7.42),
               (1.5, -7.42), (0.0, -7.42)]


def say(text: str = "") -> None:
    print(text, flush=True)


def rotated(keys, angle_deg):
    turn = math.radians(angle_deg)
    cos, sin = math.cos(turn), math.sin(turn)
    return [[r * cos, r * sin, z] for r, z in keys]


def section(angle_deg: float, z: float, r_in: float, r_out: float,
            half_deg: float = FOOT_HALF) -> list[list[float]]:
    """A 50 deg annular sector with the two outer corners chamfered.

    The phase-5 version put a single point at each corner and the loft turned so
    hard there that the tightest radius measured 0.191 mm - below the 0.3 mm
    offset, so ThickSurface refused (B30 2026-11-04). Two extra points on each
    outer corner spread that turn over about 6 mm, which is what the check now
    reads as a radius above one millimetre.
    """
    a = math.radians(half_deg)
    mid = (r_in + r_out) / 2.0
    pts = [
        [r_in * math.cos(-a), r_in * math.sin(-a), z],
        [r_in, 0.0, z],
        [r_in * math.cos(a), r_in * math.sin(a), z],
        [mid * math.cos(a * 1.04), mid * math.sin(a * 1.04), z],
        [r_out * math.cos(a * 0.92), r_out * math.sin(a * 0.92), z],
        [r_out * math.cos(a * 0.62), r_out * math.sin(a * 0.62), z],
        [r_out, 0.0, z],
        [r_out * math.cos(a * 0.62), -r_out * math.sin(a * 0.62), z],
        [r_out * math.cos(a * 0.92), -r_out * math.sin(a * 0.92), z],
        [mid * math.cos(-a * 1.04), mid * math.sin(-a * 1.04), z],
    ]
    turn = math.radians(angle_deg)
    cos, sin = math.cos(turn), math.sin(turn)
    return [[x * cos - y * sin, x * sin + y * cos, zz] for x, y, zz in pts]


def main() -> None:
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    for index in range(conn.documents.Count, 0, -1):
        open_doc = conn.documents.Item(index)
        name = str(open_doc.Name)
        if name.startswith("PETAL_"):
            open_doc.Activate()
            document.execute("catia_close_document", {"save": False})
            say(f"closed leftover {name}")
        else:
            raise SystemExit(f"'{name}' is open and is not mine; close it first")

    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)
    part_design = PartDesignTools(conn)
    export = ExportTools(conn)

    document.execute("catia_new_part",
                     {"name": "PETAL_P6",
                      "file_path": rf"{TMP}\PETAL_P6.CATPart"})
    assert str(conn.active_document.Name).startswith("PETAL_P6"), \
        conn.active_document.Name

    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": R_BODY, "name": "C_RIM"})
    gsd.execute("catia_gsd_extrude", {"profile": "C_RIM", "direction": [0, 0, 1],
                                      "limit1": 100.0, "name": "S_SKIRT"})
    gsd.execute("catia_gsd_line", {"point1": [0, 0, -40], "point2": [0, 0, 60],
                                   "name": "L_AXIS"})
    gsd.execute("catia_gsd_spline",
                {"points": rotated(DISH_KEYS, 0.0), "name": "SP_DISH"})
    gsd.execute("catia_gsd_revolve", {"profile": "SP_DISH", "axis": "L_AXIS",
                                      "angle1": 360.0, "name": "S_DISH"})
    for k in range(FEET):
        gsd.execute("catia_gsd_spline",
                    {"points": rotated(DISH_KEYS, SECTOR * k),
                     "name": f"SP_SEC{k}"})
    say("skirt + dish profiles")

    cells, foot_parts = [], []
    for k in range(FEET):
        angle = SECTOR * k + SECTOR / 2.0
        lo, hi = f"F{k}_LO", f"F{k}_HI"
        tube, tread = f"F{k}_TUBE", f"F{k}_TREAD"
        sector, cell = f"SEC{k}", f"C{k}"

        gsd.execute("catia_gsd_spline",
                    {"points": section(angle, TREAD_Z, 12.0, 31.0),
                     "closed": True, "name": lo})
        gsd.execute("catia_gsd_spline",
                    {"points": section(angle, ROOT_Z, 14.0, 28.0),
                     "closed": True, "name": hi})
        gsd.execute("catia_gsd_multi_section_surface",
                    {"sections": [lo, hi], "ruled": False, "name": tube})
        gsd.execute("catia_gsd_fill", {"boundaries": [lo], "name": tread})
        gsd.execute("catia_gsd_revolve",
                    {"profile": f"SP_SEC{k}", "axis": "L_AXIS",
                     "angle1": SECTOR, "name": sector})
        gsd.execute("catia_gsd_split", {"element": sector, "cutter": tube,
                                        "reverse": True, "name": cell})
        cells.append(cell)

        # the foot's wall: the part of the tube BELOW the dish. Which reverse
        # that is gets decided by trying to join it to the tread (they share
        # the tube's bottom section), so it cannot be guessed wrong.
        wall = None
        for flag in (False, True):
            candidate = f"F{k}_W{int(flag)}"
            try:
                gsd.execute("catia_gsd_split", {"element": tube,
                                                "cutter": "S_DISH",
                                                "reverse": flag,
                                                "name": candidate})
                gsd.execute("catia_gsd_join",
                            {"elements": [candidate, tread],
                             "name": f"F{k}_PART{int(flag)}"})
                wall = f"F{k}_PART{int(flag)}"
                say(f"  foot {k}: wall with reverse={flag}")
                break
            except Exception:  # noqa: BLE001
                continue
        if wall is None:
            raise SystemExit(f"foot {k}: neither side of the tube joins its tread")
        foot_parts.append(wall)

    gsd.execute("catia_gsd_spline",
                {"points": rotated(CENTRE_KEYS, 0.0), "name": "SP_CENTRE"})
    gsd.execute("catia_gsd_revolve", {"profile": "SP_CENTRE", "axis": "L_AXIS",
                                      "angle1": 360.0, "name": "S_CENTRE"})
    say("centre dome + boss")

    shell = "S_SKIRT"
    for index, name in enumerate(cells + ["S_CENTRE"] + foot_parts):
        try:
            gsd.execute("catia_gsd_join", {"elements": [shell, name],
                                           "name": f"J{index}"})
            shell = f"J{index}"
        except Exception as error:  # noqa: BLE001
            say(f"  join {name} FAILED: {str(error).splitlines()[0][:90]}")
    say(f"shell = {shell}")

    # The STL carries every VISIBLE element, so the construction surfaces have
    # to go before anything is measured or offset - otherwise the check reports
    # thousands of non-manifold edges that belong to the tools, not the shell.
    keep = {shell, "S_ALL"} | {f"C{k}" for k in range(FEET)}
    hidden = 0
    try:
        elements = json.loads(gsd.execute("catia_gsd_list_elements", {}))
        for group in elements.get("geometrical_sets", []):
            for name in group.get("elements", []):
                if name in keep or name.startswith("J"):
                    continue
                try:
                    part_design.execute("catia_set_visibility",
                                        {"name": name, "show": False})
                    hidden += 1
                except Exception:  # noqa: BLE001
                    pass
    except Exception as error:  # noqa: BLE001
        say(f"  visibility pass skipped: {str(error)[:60]}")
    say(f"  hidden {hidden} construction element(s)")

    report = json.loads(measurement.execute("catia_check_surface",
                                            {"crease_deg": 20, "top_creases": 8}))
    say("\n--- surface check ---")
    say(f"triangles {report['triangles']}  interior {report['interior_edges']}"
        f"  boundary {report['boundary_edges']}"
        f"  non-manifold {report['non_manifold_edges']}")
    say(f"fold median {report['fold_deg']['median']}  p90 {report['fold_deg']['p90']}"
        f"  max {report['fold_deg']['max']}")
    for crease in report["worst_creases"][:8]:
        say(f"  crease {crease['fold_deg']:7.2f} deg  r={crease['r']:6.2f}"
            f"  theta={crease['theta_deg']:6.1f}  z={crease['z']:8.2f}"
            f"  side={crease['side']}")

    for view in ("bottom", "isometric", "front"):
        export.execute("catia_set_view", {"view": view})
        export.execute("catia_screenshot", {"file_path": rf"{TMP}\p6_{view}.jpg"})
    say("screenshots written")

    try:
        reply = gsd.execute("catia_gsd_thick_surface",
                            {"surface": shell, "thickness1": 0.3})
        say("\nthicken 0.3: " + reply.splitlines()[0][:150])
        box = measurement.execute("catia_get_bounding_box", {})
        say("solid bbox: " + box.replace("\n", " ")[:220])
    except Exception as error:  # noqa: BLE001
        say("\nthicken FAILED: " + str(error)[:900])

    say("left open: PETAL_P6")


main()
