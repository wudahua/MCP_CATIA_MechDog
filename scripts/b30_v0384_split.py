"""0.3.84 probe 9: can a surface be cut by a curve that lies on it?

The 2026-11-09 report's Step 6 has no way through: its base surface has a r~2 mm
hole whose border is a wavy ring (five loft sections interpolated), so
  * gsd_boundary returns ONE curve carrying both loops, and filling it rebuilt
    the whole base (1714 non-manifold edges), and
  * a precise circle has no edge in common with the wavy ring, so the join was
    refused.
Both failures are the same missing operation: get a curve that IS the hole's
border, then cut the surface with it. Then the inner piece is the cap and it
shares its edge by construction - no join, no shared-edge condition.

This asks whether AddNewHybridSplit(surface, curve, orientation) accepts a curve
that lies ON the surface, which is the part that was never measured. Two
orientations are tried, and the result is judged by AREA: a split into two pieces
must give two areas that add up to the original, not one piece equal to it.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402

ROOT = r"F:\catia_probe_v0384\split"


def say(line: str = "") -> None:
    print(line, flush=True)


def area_of(conn, part, element) -> float | None:
    try:
        spa = conn.active_document.GetWorkbench("SPAWorkbench")
        value = spa.GetMeasurable(
            part.CreateReferenceFromObject(element)).Area
        return float(value)
    except Exception:  # noqa: BLE001 - Area is optional on this build
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

    path = os.path.join(ROOT, "split.CATPart")
    if os.path.isfile(path):
        os.remove(path)
    document.execute("catia_new_part", {"name": "SPLIT", "file_path": path})
    part = conn.get_active_part()
    hsf = part.HybridShapeFactory
    geoset = part.HybridBodies.Add()
    geoset.Name = "GS"

    say("=== a 40 x 40 flat patch on z=0 ===")
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 0], "point2": [40, 0, 0], "name": "EDGE"})
    gsd.execute("catia_gsd_extrude",
                {"profile": "EDGE", "direction": [0, 1, 0], "limit1": 40,
                 "name": "PATCH"})
    part.Update()
    patch = part.FindObjectByName("PATCH")
    say(f"  patch area = {area_of(conn, part, patch)} mm2 (expect 1600)")

    say("")
    say("=== a circle drawn straight on it (z=0, r=10) ===")
    gsd.execute("catia_gsd_circle",
                {"center": [20, 20, 0], "radius": 10, "support_plane": "xy",
                 "name": "RING"})
    part.Update()
    ring = part.FindObjectByName("RING")
    try:
        spa = conn.active_document.GetWorkbench("SPAWorkbench")
        length = float(spa.GetMeasurable(
            part.CreateReferenceFromObject(ring)).Length)
        say(f"  circle length = {length:.4f} mm (expect 62.832)")
    except Exception as error:  # noqa: BLE001
        say(f"  circle length unreadable: {str(error).splitlines()[0][:70]}")

    say("")
    say("=== AddNewHybridSplit(PATCH, RING, orientation) ===")
    for orientation in (0, 1):
        try:
            split = hsf.AddNewHybridSplit(
                part.CreateReferenceFromObject(patch),
                part.CreateReferenceFromObject(ring), orientation)
            geoset.AppendHybridShape(split)
            split.Name = f"PIECE_{orientation}"
            part.UpdateObject(split)
            part.Update()
            inner = area_of(conn, part, split)
            say(f"  orientation {orientation}: updated, area {inner} mm2"
                + (f" (outer piece would be {1600 - inner:.1f})"
                   if inner is not None else ""))
        except Exception as error:  # noqa: BLE001 - the refusal is the answer
            say(f"  orientation {orientation}: REFUSED "
                f"{str(error).splitlines()[0][:130]}")
            try:
                part.Update()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    main()
