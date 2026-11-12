"""Second round on the 0.3.48 report: the four things round 1 left open.

Round 1 (scripts/b30_loft_surface_claims.py) settled, on B30:
  * the failing trio fails identically as circles, as 12-point closed splines
    and as 90-point closed splines, while the same radii in a monotone stack
    build - so the report's "circle sections are fragile, splines are not" is a
    property of the ARRANGEMENT, not of the curve type;
  * a datum point is ACCEPTED by AddSectionToLoft and only then fails the
    update;
  * the mesh bounding box reads 35 mm with a visible surface at z=30..35 over a
    10 mm pad, and 10 mm once that set is hidden;
  * catia_gsd_spline 90 points: 5.21 s as shipped vs 0.97 s without the
    per-point UpdateObject;
  * set_view -> screenshot was NOT stale here (three views, three digests).

This round answers:
    G  split: which side does orientation +1 keep? (unequal halves, measured)
    H  is a SOLID VERTEX usable as a loft section, unlike a datum point?
    I  can the mesh route export the solid only? (Body vs Part ExportData)
    J  is the fast spline geometrically the same curve? (length 2*pi*r)
    K  where a pywin32 build number can actually be read from
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
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
    for index in range(1, conn.documents.Count + 1):
        if str(conn.documents.Item(index).Name) == f"{name}.CATPart":
            activate(conn, f"{name}.CATPart")
            document.execute("catia_close_document", {"save": False})
    path = os.path.join(OUT, f"{name}.CATPart")
    document.execute("catia_new_part", {"name": name, "file_path": path})
    activate(conn, f"{name}.CATPart")


def main() -> int:
    conn = CATIAConnection()
    say(conn.connect())
    document = DocumentTools(conn)
    part_design = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    measurement = MeasurementTools(conn)
    gsd = GSDTools(conn)

    spa = conn.app.ActiveDocument.GetWorkbench("SPAWorkbench") if conn.documents.Count else None

    def area_of(name: str) -> str:
        part = conn.get_active_part()
        obj = part.FindObjectByName(name)
        try:
            measurable = spa.GetMeasurable(part.CreateReferenceFromObject(obj))
            return f"area {float(measurable.Area):.1f}"
        except Exception as error:  # noqa: BLE001
            return f"area n/a ({str(error).splitlines()[0][:60]})"

    # ── G. which side does orientation +1 keep? ──────────────────────────
    say("\n── G. split orientation, unequal halves (cylinder r10 z0..40, cut z=10) ──")
    fresh("V0348_G", document, conn)
    spa = conn.app.ActiveDocument.GetWorkbench("SPAWorkbench")
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": 10.0, "name": "G_C"})
    gsd.execute("catia_gsd_extrude", {"profile": "G_C", "direction": [0, 0, 1],
                                     "limit1": 40.0, "name": "G_CYL"})
    gsd.execute("catia_gsd_plane_offset", {"base_plane": "xy", "offset": 10.0,
                                           "name": "G_MID"})
    say(f"G0 full cylinder           : {area_of('G_CYL')}  (analytic 2513.3)")
    part = conn.get_active_part()
    for label, reverse in (("G1 default (reverse=False)", False),
                           ("G2 reverse=True         ", True)):
        name = "G_CUT" + ("R" if reverse else "N")
        gsd.execute("catia_gsd_split", {"element": "G_CYL", "cutter": "G_MID",
                                        "reverse": reverse, "name": name})
        cut = part.FindObjectByName(name)
        try:
            orientation = cut.Orientation
        except Exception:  # noqa: BLE001
            orientation = "?"
        say(f"{label}: {area_of(name)}  Orientation read back {orientation}"
            f"  (above the cut = {(40 - 10) * 2 * math.pi * 10:.1f}, "
            f"below = {10 * 2 * math.pi * 10:.1f})")

    # ── H. a solid vertex as a section ───────────────────────────────────
    say("\n── H. solid VERTEX as a loft section (datum point was refused) ──")
    fresh("V0348_H", document, conn)
    spa = conn.app.ActiveDocument.GetWorkbench("SPAWorkbench")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": -5, "y1": -5, "x2": 5, "y2": 5})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10.0})
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 25], "support_plane": "xy",
                                     "radius": 8.0, "name": "H_C"})
    for label, sections in (("H1 [Vertex.1, circle]", ["Vertex.1", "H_C"]),
                            ("H2 [circle, Vertex.1]", ["H_C", "Vertex.1"])):
        try:
            gsd.execute("catia_gsd_multi_section_surface",
                        {"sections": sections,
                         "name": f"H_LOFT{label[1]}"})
            say(f"{label}: built")
        except Exception as error:  # noqa: BLE001
            say(f"{label}: {str(error).splitlines()[0][:150]}")

    # ── I. can the export be limited to the solid? ───────────────────────
    say("\n── I. solid-only STL: does Body export, or only Part? ──")
    part = conn.get_active_part()
    body = part.MainBody
    stl = os.path.join(OUT, "solid_only.stl")
    for label, target in (("I1 doc.ExportData", conn.active_document),
                          ("I2 body.ExportData", body),
                          ("I3 part.ExportData", part)):
        path = stl.replace("solid_only", f"solid_only_{label[1]}")
        if os.path.exists(path):
            os.remove(path)
        try:
            target.ExportData(path, "stl")
            size = os.path.getsize(path) if os.path.exists(path) else "no file"
            say(f"{label}: ok, {size} bytes")
        except Exception as error:  # noqa: BLE001
            say(f"{label}: {str(error).splitlines()[0][:110]}")

    # ── J. is the fast spline the same curve? ────────────────────────────
    say("\n── J. fast-path spline: same geometry? (r=30 -> 188.5 mm) ──")
    fresh("V0348_J", document, conn)
    spa = conn.app.ActiveDocument.GetWorkbench("SPAWorkbench")
    points = [[30 * math.cos(2 * math.pi * i / 90),
               30 * math.sin(2 * math.pi * i / 90), 0.0] for i in range(90)]
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
    spline.Name = "J_FAST"
    geoset.AppendHybridShape(spline)
    part.UpdateObject(spline)
    elapsed = time.perf_counter() - start
    measurable = spa.GetMeasurable(part.CreateReferenceFromObject(spline))
    say(f"J1 built in {elapsed:.2f} s, length {float(measurable.Length):.3f} mm "
        f"(analytic 188.496), geoset holds {geoset.HybridShapes.Count} elements")

    # ── K. pywin32 build number, from every source that has one ──────────
    say("\n── K. pywin32 build number ──")
    import win32com
    say(f"K1 win32com.__build__      : {getattr(win32com, '__build__', 'ABSENT')}")
    try:
        import pywintypes
        say(f"K2 pywintypes.__version__  : {getattr(pywintypes, '__version__', 'ABSENT')}")
    except Exception as error:  # noqa: BLE001
        say(f"K2 pywintypes             : {error}")
    try:
        import win32api
        info = win32api.GetFileVersionInfo(win32api.__file__, "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        say(f"K3 win32api.dll file ver   : "
            f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}")
    except Exception as error:  # noqa: BLE001
        say(f"K3 win32api               : {str(error).splitlines()[0][:90]}")

    say("\nDocuments open: " + str(conn.documents.Count))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
