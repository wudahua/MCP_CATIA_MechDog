"""0.3.84 probe 10: the whole Step-6 chain, on one patch.

Probe 9 established that a curve lying on a surface can cut it (a r=10 circle on
a 40x40 patch gave exactly pi*100 = 314.159 mm2). That single fact is what the
2026-11-09 report was missing, and it changes the shape of the problem:

  * its hole border is a wavy ring (five loft sections interpolated), so a
    precise circle never shares an edge with it - but after SPLITTING the base
    with that circle, the outer piece's inner border IS the circle, by
    construction;
  * so "fill the circle, then join" stops being a shared-edge gamble.

This runs the four calls in the order that works and checks the arithmetic at
each step, plus the final thickened solid. It is a flat patch on purpose: the
question here is the SEQUENCE, and a flat patch makes every area checkable.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402

ROOT = r"F:\catia_probe_v0384\chain"


def say(line: str = "") -> None:
    print(line, flush=True)


def area_mm2(conn, part, element) -> float | None:
    """Area in mm2. Measurable.Area answers in m2 on this build."""
    try:
        spa = conn.active_document.GetWorkbench("SPAWorkbench")
        return float(spa.GetMeasurable(
            part.CreateReferenceFromObject(element)).Area) * 1e6
    except Exception:  # noqa: BLE001 - Area is optional on this build
        return None


def volume_mm3(conn, part, element) -> float | None:
    try:
        spa = conn.active_document.GetWorkbench("SPAWorkbench")
        return float(spa.GetMeasurable(
            part.CreateReferenceFromObject(element)).Volume) * 1e9
    except Exception:  # noqa: BLE001
        return None


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)

    own_root = os.path.normcase(os.path.abspath(r"F:\catia_probe_v0384"))
    for index in range(conn.documents.Count, 0, -1):
        try:
            stale = conn.documents.Item(index)
            full = os.path.normcase(os.path.abspath(str(stale.FullName)))
        except Exception:  # noqa: BLE001
            continue
        if full.startswith(own_root):
            stale.Close()

    path = os.path.join(ROOT, "chain.CATPart")
    if os.path.isfile(path):
        os.remove(path)
    document.execute("catia_new_part", {"name": "CHAIN", "file_path": path})
    part = conn.get_active_part()

    say("=== 1. the base surface, with a hole in the middle ===")
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 0], "point2": [40, 0, 0], "name": "EDGE"})
    gsd.execute("catia_gsd_extrude",
                {"profile": "EDGE", "direction": [0, 1, 0], "limit1": 40,
                 "name": "PATCH"})
    part.Update()
    patch = part.FindObjectByName("PATCH")
    say(f"  PATCH area = {area_mm2(conn, part, patch):.3f} mm2 (expect 1600)")

    say("")
    say("=== 2. the precise circle that will become the cap's border ===")
    gsd.execute("catia_gsd_circle",
                {"center": [20, 20, 0], "radius": 6, "support_plane": "xy",
                 "name": "CAPRING"})

    say("")
    say("=== 3. split the base with that circle, keep the OUTER piece ===")
    try:
        say("  " + gsd.execute("catia_gsd_split",
                                {"element": "PATCH", "cutter": "CAPRING",
                                 "reverse": True,
                                 "name": "OUTER"}).splitlines()[0][:200])
    except Exception as error:  # noqa: BLE001
        say(f"  REFUSED {getattr(error, 'code', '')}: "
            + str(error).splitlines()[0][:200])
        return
    part.Update()
    outer = part.FindObjectByName("OUTER")
    say(f"  OUTER area = {area_mm2(conn, part, outer):.3f} mm2 "
        f"(1600 - pi*36 = 1487.0)")

    say("")
    say("=== 4. fill the circle and join it to the outer piece ===")
    try:
        say("  " + gsd.execute("catia_gsd_fill",
                                {"boundaries": ["CAPRING"],
                                 "name": "CAP"}).splitlines()[0][:160])
        cap = part.FindObjectByName("CAP")
        say(f"  CAP area = {area_mm2(conn, part, cap):.3f} mm2 (pi*36 = 113.1)")
    except Exception as error:  # noqa: BLE001
        say(f"  fill REFUSED {getattr(error, 'code', '')}: "
            + str(error).splitlines()[0][:200])
        return

    try:
        say("  " + gsd.execute("catia_gsd_join",
                                {"elements": ["OUTER", "CAP"],
                                 "name": "CLOSED"}).splitlines()[0][:200])
        closed = part.FindObjectByName("CLOSED")
        say(f"  CLOSED area = {area_mm2(conn, part, closed):.3f} mm2 "
            f"(1600: the circle area is back)")
    except Exception as error:  # noqa: BLE001
        say(f"  join REFUSED {getattr(error, 'code', '')}: "
            + str(error).splitlines()[0][:200])
        return

    say("")
    say("=== 5. thicken it: the step that failed for the report ===")
    try:
        say("  " + gsd.execute("catia_gsd_thick_surface",
                                {"surface": "CLOSED", "thickness1": 0.5,
                                 "thickness2": 0.0}).splitlines()[0][:200])
        solid = part.FindObjectByName("加厚曲面.1") or part.FindObjectByName(
            "ThickSurface.1")
        if solid is not None:
            say(f"  solid volume = {volume_mm3(conn, part, solid):.1f} mm3 "
                f"(~1600 * 0.5 = 800)")
    except Exception as error:  # noqa: BLE001
        say(f"  thick REFUSED {getattr(error, 'code', '')}: "
            + str(error).splitlines()[0][:240])


if __name__ == "__main__":
    main()
