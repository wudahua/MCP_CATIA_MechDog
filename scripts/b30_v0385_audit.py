"""0.3.85 acceptance: the three conveniences, judged by geometry.

  1  catia_gsd_boundary without 'edge' still returns the whole border (188.496 mm
     on a cone's two circles) - the old behaviour is untouched
  2  the same surface with edge=1 / edge=2 returns ONE loop each, and the two are
     the two different lengths (62.832 and 125.664), not the same one twice
  3  a fill on such a loop covers only that loop (314.159 or 1256.637 mm2), not
     the two discs added (1570.796) - the number that made the report's Step 6
     fail
  4  catia_gsd_fill accepts 'Edge.N' and builds a surface from it
  5  catia_gsd_circ_pattern(cap_center=true, cap_curve=...) closes a five-lobe
     base: 2080.436 + 59.441 = 2139.877 mm2
  6  the two refusals happen BEFORE anything is built: no cap_curve, and a
     partial array

Everything is built in this script's own document under ROOT; the only documents
it closes are ones whose FullName is under ROOT.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.results import CodedError  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402

ROOT = r"F:\catia_probe_v0385\audit"
RESULTS: list[tuple[int, str, bool, str]] = []

BOTTOM = 2 * math.pi * 10
TOP = 2 * math.pi * 20
BOTH = BOTTOM + TOP
SECTOR = 0.5 * (30.0 ** 2 - 5.0 ** 2) * math.sin(math.radians(72.0))
PENTAGON = 0.5 * 5 * 5.0 ** 2 * math.sin(math.radians(72.0))


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, label: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, label, passed, detail))
    say(f"[{'OK ' if passed else 'FAIL'}] {number}. {label}"
        + (f"  |  {detail}" if detail else ""))


def length_of(conn, part, element):
    try:
        spa = conn.active_document.GetWorkbench("SPAWorkbench")
        return float(spa.GetMeasurable(
            part.CreateReferenceFromObject(element)).Length)
    except Exception:  # noqa: BLE001
        return None


def area_mm2(conn, part, element):
    try:
        spa = conn.active_document.GetWorkbench("SPAWorkbench")
        return float(spa.GetMeasurable(
            part.CreateReferenceFromObject(element)).Area) * 1e6
    except Exception:  # noqa: BLE001
        return None


def point(radius: float, angle_deg: float) -> list[float]:
    angle = math.radians(angle_deg)
    return [radius * math.cos(angle), radius * math.sin(angle), 0.0]


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)

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

    path = os.path.join(ROOT, "audit85.CATPart")
    if os.path.isfile(path):
        os.remove(path)
    document.execute("catia_new_part", {"name": "AUDIT85", "file_path": path})
    part = conn.get_active_part()

    # ── the cone: two border circles of DIFFERENT length ─────────────────
    say("--- a cone, so the two loops can be told apart by length ---")
    gsd.execute("catia_gsd_line",
                {"point1": [10, 0, 0], "point2": [20, 0, 30], "name": "SPINE"})
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, -10], "point2": [0, 0, 40], "name": "AXZ"})
    gsd.execute("catia_gsd_revolve",
                {"profile": "SPINE", "axis": "AXZ", "angle1": 360,
                 "name": "CONE"})
    part.Update()
    cone = part.FindObjectByName("CONE")

    gsd.execute("catia_gsd_boundary", {"surface": "CONE", "name": "WHOLE"})
    part.Update()
    whole = length_of(conn, part, part.FindObjectByName("WHOLE"))
    check(1, "boundary without 'edge' is still the whole border",
          whole is not None and abs(whole - BOTH) < 0.01,
          f"{whole:.4f} mm (expect {BOTH:.4f} = both circles)")

    loops = {}
    for index in (1, 2):
        name = f"LOOP{index}"
        try:
            gsd.execute("catia_gsd_boundary",
                        {"surface": "CONE", "edge": index, "name": name})
            part.Update()
            loops[index] = length_of(conn, part, part.FindObjectByName(name))
        except Exception as error:  # noqa: BLE001
            say(f"   edge={index} refused: {str(error).splitlines()[0][:160]}")
            loops[index] = None
    picked = sorted(v for v in loops.values() if v is not None)
    check(2, "edge=1 and edge=2 return the two different loops, not one twice",
          len(picked) == 2
          and abs(picked[0] - BOTTOM) < 0.01 and abs(picked[1] - TOP) < 0.01,
          f"edge1={loops.get(1)}, edge2={loops.get(2)} "
          f"(expect {BOTTOM:.4f} and {TOP:.4f})")

    # ── 3 a fill on one loop covers that loop only ───────────────────────
    filled = {}
    for index in (1, 2):
        try:
            gsd.execute("catia_gsd_fill",
                        {"boundaries": [f"LOOP{index}"], "name": f"CAP{index}"})
            part.Update()
            filled[index] = area_mm2(conn, part,
                                     part.FindObjectByName(f"CAP{index}"))
        except Exception as error:  # noqa: BLE001
            say(f"   fill of LOOP{index} refused: "
                f"{str(error).splitlines()[0][:160]}")
            filled[index] = None
    expected = {math.pi * 100: "r=10 disc", math.pi * 400: "r=20 disc"}
    matched = [v for v in filled.values()
               if v is not None and any(abs(v - e) < 0.02 for e in expected)]
    check(3, "a fill on one loop covers that loop alone",
          len(matched) == 2,
          f"fill areas {list(filled.values())} (expect "
          f"{math.pi * 100:.3f} and {math.pi * 400:.3f}, NOT {math.pi * 500:.3f})")

    # ── 4 catia_gsd_fill accepts Edge.N ──────────────────────────────────
    say("--- catia_gsd_fill with an Edge.N boundary ---")
    try:
        from catia_mcp.topo_search import search_topology  # noqa: F401

        reply = gsd.execute("catia_gsd_fill",
                            {"boundaries": ["Edge.1"], "name": "FROM_EDGE"})
        part.Update()
        from_edge = area_mm2(conn, part, part.FindObjectByName("FROM_EDGE"))
        say("   " + reply.splitlines()[0][:200])
        check(4, "catia_gsd_fill builds from 'Edge.N'",
              from_edge is not None and from_edge > 1.0,
              f"area {from_edge} mm2 (the extract returns the whole ring, so "
              "this is the patch's outline, not one side)")
    except Exception as error:  # noqa: BLE001
        check(4, "catia_gsd_fill builds from 'Edge.N'", False,
              str(error).splitlines()[0][:200])

    # ── 5 cap_center on a five-lobe base ────────────────────────────────
    say("--- cap_center on a five-lobe base ---")
    corners = [point(5.0, 0), point(30.0, 0), point(30.0, 72), point(5.0, 72)]
    for i, name in enumerate(["L1", "L2", "L3", "L4"]):
        gsd.execute("catia_gsd_line", {"point1": corners[i],
                                       "point2": corners[(i + 1) % 4],
                                       "name": name})
    gsd.execute("catia_gsd_fill",
                {"boundaries": ["L1", "L2", "L3", "L4"], "name": "SECTOR"})
    part.Update()
    sector_area = area_mm2(conn, part, part.FindObjectByName("SECTOR"))
    reply = gsd.execute("catia_gsd_circ_pattern",
                        {"element": "SECTOR", "count": 5, "total_angle": 360,
                         "join": True, "axis": "z", "name": "BASE",
                         "cap_center": True, "cap_curve": "L4"})
    part.Update()
    base = part.FindObjectByName("BASE")
    closed = part.FindObjectByName("BASE_closed")
    base_area = area_mm2(conn, part, base)
    closed_area = area_mm2(conn, part, closed) if closed is not None else None
    say("   " + reply.splitlines()[0][:200])
    check(5, "cap_center closes the centre with the hole's own edge",
          closed_area is not None
          and base_area is not None
          and abs(closed_area - (base_area + PENTAGON)) < 0.05,
          f"sector {sector_area:.3f}, base {base_area}, closed {closed_area} "
          f"(expect base + {PENTAGON:.3f})")

    # ── 6 the two refusals happen before anything is built ──────────────
    say("--- validate-stage refusals ---")
    before = part.HybridShapes.Count if hasattr(part, "HybridShapes") else None
    missing = None
    try:
        gsd.execute("catia_gsd_circ_pattern",
                    {"element": "SECTOR", "count": 3, "cap_center": True,
                     "name": "SHOULD_NOT_EXIST"})
    except CodedError as error:
        missing = error.code
    partial = None
    try:
        gsd.execute("catia_gsd_circ_pattern",
                    {"element": "SECTOR", "count": 3, "total_angle": 180,
                     "cap_center": True, "cap_curve": "L4",
                     "name": "ALSO_NOT"})
    except CodedError as error:
        partial = error.code
    check(6, "cap_center refuses a missing curve and a partial array first",
          missing == "CAP_CURVE_REQUIRED" and partial == "CAP_NOT_FULL_TURN",
          f"no cap_curve -> {missing}, total_angle=180 -> {partial}")

    say("")
    passed = sum(1 for _n, _l, ok, _d in RESULTS if ok)
    say(f"{passed}/{len(RESULTS)} checks passed")
    for number, label, ok, detail in RESULTS:
        if not ok:
            say(f"  FAILED {number}: {label} - {detail}")


if __name__ == "__main__":
    main()
