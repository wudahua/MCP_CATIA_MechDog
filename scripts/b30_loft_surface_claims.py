"""Check the four measurable claims in the 2026-11-03 (0.3.48) report.

The report is a surface-modelling session: 10 concentric wavy rings lofted into
one NURBS face. Its seven findings split into things a script can settle and
things that are message-wording only. This script settles the measurable ones,
one variable at a time, on fresh parts:

    A  smooth loft, 3 sections: circles vs closed splines at the SAME radii and
       heights. The report says circles fail where splines succeed and concludes
       "circle sections are fragile". If a 90-point spline (geometrically a
       circle to 1e-3 mm) fails the same way, the difference is the section
       ARRANGEMENT, not the curve type.
    B  a Point as a loft section: refused by AddSectionToLoft, or accepted and
       then failed at update? The two mean different things for the docs.
    C  HybridSplit: can a kept side be read back at all? Probes the object's
       orientation-ish properties, then area + centre of gravity of the result
       for orientation +1 and -1.
    D  bounding box with a VISIBLE construction surface above the solid: does
       the number move when the surface is hidden, and on which route?
    E  set_view -> screenshot: are two different views byte-identical (stale
       capture), and does view.Update() before the capture change that?
    F  catia_gsd_spline 90 points: what share of the time is the per-point
       UpdateObject inside _point_ref?

Every geometry step asserts the active document first (a probe once edited the
user's P12_Fan because CATIA had switched documents underneath it).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import tempfile
import time

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

OUT = os.path.join(tempfile.gettempdir(), "b30_loft_claims")
os.makedirs(OUT, exist_ok=True)
made: list[str] = []


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
    path = os.path.join(OUT, f"{name}.CATPart")
    if os.path.exists(path):
        os.remove(path)
    document.execute("catia_new_part", {"name": name, "file_path": path})
    made.append(f"{name}.CATPart")
    activate(conn, f"{name}.CATPart")


def ring(radius: float, z: float, count: int) -> list[list[float]]:
    return [[radius * math.cos(2 * math.pi * i / count),
             radius * math.sin(2 * math.pi * i / count), z] for i in range(count)]


def loft(gsd: GSDTools, sections: list[str], **extra) -> tuple[bool, str]:
    args = {"sections": sections, "name": f"Loft_{int(time.time() * 1000) % 100000}"}
    args.update(extra)
    try:
        gsd.execute("catia_gsd_multi_section_surface", args)
        return True, "built"
    except Exception as error:  # noqa: BLE001 - the failure IS the datum
        first = str(error).splitlines()[0]
        return False, first[:180]


def main() -> int:
    conn = CATIAConnection()
    say(conn.connect())
    document = DocumentTools(conn)
    part_design = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    measurement = MeasurementTools(conn)
    gsd = GSDTools(conn)
    export = ExportTools(conn)

    # This script owns every V0348_* document; close leftovers from a previous
    # partial run so `fresh()` cannot collide with its own path.
    for index in range(conn.documents.Count, 0, -1):
        name = str(conn.documents.Item(index).Name)
        if name.startswith("V0348_"):
            activate(conn, name)
            document.execute("catia_close_document", {"save": False})
            say(f"closed leftover {name}")

    # ── A. circles vs closed splines, one arrangement at a time ──────────
    say("\n── A. smooth loft: is it the curve type or the arrangement? ──")
    fresh("V0348_LOFT", document, conn)

    # The report's failing trio: r 2.9 -> 3.0 -> 8 while z goes -19 -> -17.58
    # -> -25, i.e. the middle section sits ABOVE both neighbours (fold-back).
    trio = [(2.9, -19.0), (3.0, -17.584), (8.0, -25.0)]
    for index, (radius, z) in enumerate(trio, start=1):
        gsd.execute("catia_gsd_circle",
                    {"center": [0, 0, z], "support_plane": "xy",
                     "radius": radius, "name": f"FA_C{index}"})
    ok, why = loft(gsd, ["FA_C1", "FA_C2", "FA_C3"])
    say(f"A1 report trio, CIRCLES        : {'OK ' if ok else 'FAIL'} {why}")

    # Same radii/heights as closed splines through 12 points ON those circles.
    for index, (radius, z) in enumerate(trio, start=1):
        gsd.execute("catia_gsd_spline",
                    {"points": ring(radius, z, 12), "closed": True,
                     "name": f"FA_S{index}"})
    ok, why = loft(gsd, ["FA_S1", "FA_S2", "FA_S3"])
    say(f"A2 report trio, SPLINE 12 pt  : {'OK ' if ok else 'FAIL'} {why}")

    # A 90-point spline is a circle to <0.001 mm. If the trio still fails, the
    # arrangement is what CATIA rejected, not the fact that sections are circles.
    for index, (radius, z) in enumerate(trio, start=1):
        gsd.execute("catia_gsd_spline",
                    {"points": ring(radius, z, 90), "closed": True,
                     "name": f"FA_F{index}"})
    ok, why = loft(gsd, ["FA_F1", "FA_F2", "FA_F3"])
    say(f"A3 report trio, SPLINE 90 pt  : {'OK ' if ok else 'FAIL'} {why}")

    # Control: a monotone stack of the same three radii, circles only.
    for index, (radius, z) in enumerate([(2.9, -25.0), (3.0, -20.0), (8.0, -17.0)],
                                        start=1):
        gsd.execute("catia_gsd_circle",
                    {"center": [0, 0, z], "support_plane": "xy",
                     "radius": radius, "name": f"FA_M{index}"})
    ok, why = loft(gsd, ["FA_M1", "FA_M2", "FA_M3"])
    say(f"A4 monotone trio, CIRCLES     : {'OK ' if ok else 'FAIL'} {why}")

    # Control 2: fold-back geometry, but only 2 sections (no middle bulge).
    ok, why = loft(gsd, ["FA_C1", "FA_C3"])
    say(f"A5 outer pair only, CIRCLES   : {'OK ' if ok else 'FAIL'} {why}")

    # ── B. a Point as a loft section ─────────────────────────────────────
    say("\n── B. point section: refused on add, or accepted then failed? ──")
    gsd.execute("catia_gsd_point", {"x": 0, "y": 0, "z": -40, "name": "FB_P"})
    gsd.execute("catia_gsd_circle",
                {"center": [0, 0, -17], "support_plane": "xy",
                 "radius": 8.0, "name": "FB_C"})
    ok, why = loft(gsd, ["FB_P", "FB_C"])
    say(f"B1 [point, circle]            : {'OK ' if ok else 'FAIL'} {why}")
    ok, why = loft(gsd, ["FB_C", "FB_P"])
    say(f"B2 [circle, point]            : {'OK ' if ok else 'FAIL'} {why}")
    # What did the raw setter say, if it got that far?
    part = conn.get_active_part()
    try:
        loft_obj = gsd._factory().AddNewLoft()
        loft_obj.AddSectionToLoft(
            part.CreateReferenceFromObject(part.FindObjectByName("FB_P")), 1, None)
        say("B3 AddSectionToLoft(point)    : accepted (no exception)")
    except Exception as error:  # noqa: BLE001
        say(f"B3 AddSectionToLoft(point)    : raised {str(error).splitlines()[0][:150]}")

    # ── C. HybridSplit: can the kept side be read? ───────────────────────
    say("\n── C. split: which side was kept, and is it readable? ──")
    fresh("V0348_SPLIT", document, conn)
    gsd.execute("catia_gsd_circle",
                {"center": [0, 0, 0], "support_plane": "xy",
                 "radius": 10.0, "name": "FC_C"})
    gsd.execute("catia_gsd_extrude",
                {"profile": "FC_C", "direction": [0, 0, 1], "limit1": 40.0,
                 "name": "FC_CYL"})
    gsd.execute("catia_gsd_plane_offset",
                {"base_plane": "xy", "offset": 20.0, "name": "FC_MID"})

    part = conn.get_active_part()
    spa = conn.active_document.GetItem("SPAWorkbench")

    def side_report(label: str, reverse: bool) -> None:
        gsd.execute("catia_gsd_split",
                    {"element": "FC_CYL", "cutter": "FC_MID", "reverse": reverse,
                     "name": "FC_CUT" + ("R" if reverse else "N")})
        cut = part.FindObjectByName("FC_CUT" + ("R" if reverse else "N"))
        # dir() on a CDispatch of this build raises inside GetTypeInfoCount, so
        # ask for the candidates by name instead.
        props: dict[str, str] = {}
        for attribute in ("Orientation", "Orientation1", "Side", "Element1",
                          "Element2", "Volume"):
            try:
                props[attribute] = str(getattr(cut, attribute))
            except Exception:  # noqa: BLE001 - absent is an answer
                continue
        area = cog_z = None
        try:
            measurable = spa.GetMeasurable(part.CreateReferenceFromObject(cut))
            area = round(float(measurable.Area), 2)
            try:
                cog = measurable.Point2
                cog_z = round(float(cog[2]), 3)
            except Exception:  # noqa: BLE001
                cog_z = "no Point2"
        except Exception as error:  # noqa: BLE001
            area = f"n/a ({str(error).splitlines()[0][:60]})"
        say(f"C{'2' if reverse else '1'} reverse={reverse!s:<5}: area {area}  cog z {cog_z}  "
            f"props {props}")
        try:
            part_design.execute("catia_delete_feature",
                                {"feature_name": f"FC_CUT{'R' if reverse else 'N'}"})
        except Exception:  # noqa: BLE001
            pass

    side_report("default", False)
    side_report("reversed", True)

    # ── D. bounding box vs a visible construction surface ────────────────
    say("\n── D. bounding box with a visible surface 20 mm above the solid ──")
    fresh("V0348_BOX", document, conn)
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": -5, "y1": -5, "x2": 5, "y2": 5})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10.0})
    gsd.execute("catia_gsd_circle",
                {"center": [0, 0, 30], "support_plane": "xy",
                 "radius": 4.0, "name": "FD_C"})
    gsd.execute("catia_gsd_extrude",
                {"profile": "FD_C", "direction": [0, 0, 1], "limit1": 5.0,
                 "name": "FD_SURF"})

    def box(label: str) -> None:
        payload = json.loads(measurement.execute("catia_get_bounding_box", {}))
        top = (payload.get("max") or {}).get("z")
        say(f"{label:<26}: z max {top}  route {payload.get('points_route')}  "
            f"{str(payload.get('method'))[:70]}")

    box("D1 surface visible")
    part_design.execute("catia_set_visibility", {"name": "GSD_Set", "show": False})
    box("D2 geoset hidden")
    # And the explicit mesh route, the one that exports an STL of the document.
    part_design.execute("catia_set_visibility", {"name": "GSD_Set", "show": True})
    payload = json.loads(measurement.execute("catia_get_bounding_box", {"method": "mesh"}))
    say(f"D3 mesh route, visible    : z max {(payload.get('max') or {}).get('z')}  "
        f"route {payload.get('points_route')}")

    # ── E. set_view -> screenshot staleness ──────────────────────────────
    say("\n── E. set_view then screenshot: same bytes for two views? ──")
    digests = {}

    def shot(tag: str) -> str:
        path = os.path.join(OUT, f"shot_{tag}.jpg")
        if os.path.exists(path):
            os.remove(path)
        export.execute("catia_screenshot", {"file_path": path})
        with open(path, "rb") as handle:
            digest = hashlib.md5(handle.read()).hexdigest()[:12]
        digests[tag] = digest
        say(f"E {tag:<10}: {digest}  {os.path.getsize(path)} B")
        return digest

    export.execute("catia_set_view", {"view": "front"})
    shot("front")
    export.execute("catia_set_view", {"view": "bottom"})
    shot("bottom")
    viewer = conn.active_window.ActiveViewer
    try:
        viewer.Update()
        say("E viewer.Update()            : returned")
    except Exception as error:  # noqa: BLE001
        say(f"E viewer.Update()            : raised {str(error).splitlines()[0][:80]}")
    shot("bottom_updated")
    say(f"E stale? front==bottom {digests['front'] == digests['bottom']}; "
        f"bottom==after Update {digests['bottom'] == digests['bottom_updated']}")

    # ── F. spline cost per point ─────────────────────────────────────────
    say("\n── F. catia_gsd_spline 90 points: where the time goes ──")
    fresh("V0348_SPLINE", document, conn)
    points = ring(30.0, 0.0, 90)
    start = time.perf_counter()
    gsd.execute("catia_gsd_spline", {"points": points, "closed": True, "name": "FF_A"})
    say(f"F1 tool as shipped, 90 pts   : {time.perf_counter() - start:.2f} s")

    # Same points, same spline, minus the per-point UpdateObject that
    # _point_ref issues for every coordinate.
    part = conn.get_active_part()
    factory = gsd._factory()
    geoset = gsd._get_geoset()
    start = time.perf_counter()
    refs = []
    for spec in points:
        point = factory.AddNewPointCoord(spec[0], spec[1], spec[2])
        geoset.AppendHybridShape(point)
        refs.append(part.CreateReferenceFromObject(point))
    spline = factory.AddNewSpline()
    for ref in refs:
        spline.AddPoint(ref)
    spline.SetClosing(1)
    geoset.AppendHybridShape(spline)
    part.UpdateObject(spline)
    say(f"F2 same, no per-point update : {time.perf_counter() - start:.2f} s")
    say(f"F elements in geoset         : {geoset.HybridShapes.Count}")

    say("\nOpen documents: " + ", ".join(made))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
