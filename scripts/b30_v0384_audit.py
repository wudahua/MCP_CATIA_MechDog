"""0.3.84 acceptance: the four calls the two 2026-11-09 reports asked for.

Checked against geometry, not against the reply's wording where that is possible:

  1  catia_gsd_plane_angle puts a plane where the angle says (distance 0.0000 mm
     from a point on the rotated plane, and NOT 0 from the source plane)
  2  the same call with the axis perpendicular to the reference plane is refused
     with PLANE_ANGLE_REJECTED, says why, and leaves nothing in the tree
  3  catia_diagnose reports typeinfo_members for both factories, and
     ShapeFactory's AddNew* list contains AddNewGSDCircPattern
  4  catia_gsd_boundary's reply names the split-first route for capping a hole
  5  hiding a geometrical set that CARRIES the solid warns that the body has
     nothing to show back

Everything is built in this script's own document under ROOT; the only documents
it closes are ones whose FullName is under ROOT.
"""
from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.diagnostics import DiagnosticsTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402

ROOT = r"F:\catia_probe_v0384\audit"
RESULTS: list[tuple[int, str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, label: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, label, passed, detail))
    say(f"[{'OK ' if passed else 'FAIL'}] {number}. {label}"
        + (f"  |  {detail}" if detail else ""))


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    design = PartDesignTools(conn)
    diagnose = DiagnosticsTools(conn)

    own_root = os.path.normcase(os.path.abspath(ROOT))
    for index in range(conn.documents.Count, 0, -1):
        try:
            stale = conn.documents.Item(index)
            full = os.path.normcase(os.path.abspath(str(stale.FullName)))
        except Exception:  # noqa: BLE001
            continue
        if full.startswith(own_root):
            say(f"closing a document left by an earlier run: {stale.Name}")
            stale.Close()

    path = os.path.join(ROOT, "audit84.CATPart")
    if os.path.isfile(path):
        os.remove(path)
    document.execute("catia_new_part", {"name": "AUDIT84", "file_path": path})
    part = conn.get_active_part()
    hsf = part.HybridShapeFactory

    # ── 1-2 angle datum plane ────────────────────────────────────────────
    say("--- catia_gsd_plane_angle ---")
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 0], "point2": [0, 0, 40], "name": "AXZ"})
    part.Update()
    reply = gsd.execute("catia_gsd_plane_angle",
                        {"plane": "yz", "axis": "z", "angle": 72,
                         "name": "PLANE72"})
    say("   " + reply.splitlines()[0][:220])
    plane = part.FindObjectByName("PLANE72")
    distance = None
    behind = None
    if plane is not None:
        radians = math.radians(72.0)
        # The rotated plane's normal is (cos72, sin72, 0), so a point ON it is
        # 20 * (-sin72, cos72, 0). A point on the SOURCE plane is 20 * (0, 1, 0).
        spa = conn.active_document.GetWorkbench("SPAWorkbench")
        measurable = spa.GetMeasurable(part.CreateReferenceFromObject(plane))
        geoset = part.HybridBodies.Item(1)
        spots = {}
        for tag, coords in (
                # Rotation happens in XY, so z plays no part: the point (0,20,0)
                # of the source plane moves to (-20 sin72, 20 cos72, 0).
                ("on_72", (-20 * math.sin(radians), 20 * math.cos(radians), 0.0)),
                ("on_source", (0.0, 20.0, 0.0))):
            probe = hsf.AddNewPointCoord(*coords)
            geoset.AppendHybridShape(probe)
            probe.Name = f"P_{tag}"
            part.UpdateObject(probe)
            try:
                spots[tag] = float(measurable.GetMinimumDistance(
                    part.CreateReferenceFromObject(probe)))
            except Exception as error:  # noqa: BLE001
                spots[tag] = f"unreadable ({str(error).splitlines()[0][:40]})"
        distance = spots.get("on_72")
        behind = spots.get("on_source")
    check(1, "72 degrees about z from the yz plane lands ON the 72-degree plane",
          isinstance(distance, float) and abs(distance) < 0.01
          and isinstance(behind, float) and behind > 1.0,
          f"0.000 -> {distance} mm from the 72-degree point, "
          f"{behind} mm from the source plane")

    refused_code = ""
    refused_text = ""
    try:
        gsd.execute("catia_gsd_plane_angle",
                    {"plane": "xy", "axis": "z", "angle": 36,
                     "name": "DEGENERATE"})
    except Exception as error:  # noqa: BLE001 - the refusal is the check
        refused_code = str(getattr(error, "code", ""))
        refused_text = str(error)
    left_behind = part.FindObjectByName("DEGENERATE") is not None
    check(2, "axis perpendicular to the plane is refused, explained, and cleaned up",
          refused_code == "PLANE_ANGLE_REJECTED"
          and "LIE IN" in refused_text and not left_behind,
          f"code={refused_code or '<none>'}; element left behind={left_behind}")

    # ── 3 diagnose method tables ─────────────────────────────────────────
    say("")
    say("--- catia_diagnose: both factories' own tables ---")
    raw = diagnose.execute("catia_diagnose", {})
    report = raw if isinstance(raw, dict) else json.loads(str(raw))
    factories = (report or {}).get("factories") or {}
    shape = factories.get("shape_factory") or {}
    hybrid = factories.get("hybrid_shape_factory") or {}
    shape_members = shape.get("typeinfo_members") or {}
    hybrid_members = hybrid.get("typeinfo_members") or {}
    check(3, "typeinfo_members reports both counts, and the GSD pattern is on "
             "ShapeFactory",
          bool(shape_members.get("count")) and bool(hybrid_members.get("count"))
          and any("Pattern" in name for name in (shape.get("typeinfo_addnew") or []))
          and not any("Pattern" in name
                      for name in (hybrid.get("typeinfo_addnew") or [])),
          f"shape {shape_members.get('count')} members/"
          f"{shape_members.get('addnew')} AddNew, hybrid "
          f"{hybrid_members.get('count')}/{hybrid_members.get('addnew')}")

    # ── 4 boundary reply names the split route ───────────────────────────
    say("")
    say("--- catia_gsd_boundary on a surface, with the route named ---")
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 50], "point2": [40, 0, 50], "name": "E50"})
    gsd.execute("catia_gsd_extrude",
                {"profile": "E50", "direction": [0, 1, 0], "limit1": 40,
                 "name": "P50"})
    boundary = gsd.execute("catia_gsd_boundary",
                           {"surface": "P50", "name": "B50"})
    check(4, "the boundary reply says one curve carries every loop and names "
             "the split-first route",
          "EVERY loop" in boundary and "catia_gsd_split" in boundary
          and "catia_gsd_fill" in boundary,
          boundary.splitlines()[0][:150])

    # ── 5 hiding a set that carries the solid ────────────────────────────
    say("")
    say("--- catia_set_visibility on a set holding the solid ---")
    thick = gsd.execute("catia_gsd_thick_surface",
                        {"surface": "P50", "thickness1": 0.5, "thickness2": 0.0})
    say("   " + thick.splitlines()[0][:160])
    hidden = design.execute("catia_set_visibility",
                            {"name": "GSD_Set", "show": False})
    say("   " + hidden.splitlines()[0][:240])
    check(5, "hiding a set says the solid is a separate branch, and where to "
             "look if the view empties anyway",
          "separate branch from the body" in hidden
          and "3778" in hidden and "show the set again" in hidden,
          hidden.splitlines()[0][:200])
    design.execute("catia_set_visibility", {"name": "GSD_Set", "show": True})

    say("")
    passed = sum(1 for item in RESULTS if item[2])
    say(f"{passed}/{len(RESULTS)} checks passed")
    for number, label, ok, detail in RESULTS:
        if not ok:
            say(f"  FAILED {number}: {label} | {detail}")


if __name__ == "__main__":
    main()
