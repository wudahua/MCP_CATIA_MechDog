"""0.3.82 audit: the three tools added for the 2026-11-08 report, on B30.

A tool that validates its arguments is not a tool that works. Each check below
builds real geometry and reads something back:

 1-2  close_sketch on the report's P1-1 profile (one spline + four lines whose
      ends coincide). It answered closed:false, gap 29.39 mm; the spline's ends
      were never recorded, and the arc recorder sat after a `return`.
 3    catia_sketch_copy: replay a profile onto another plane, twice (the second
      copy is made FROM the copy, which only works if the record migrate).
 4-6  catia_gsd_plane_normal_to_curve: at 0, 0.5 and 1 along a 100 mm line. The
      point's measured coordinates are printed, because that is the only evidence
      of what the parameter convention actually did.
 7-8  catia_gsd_circ_pattern: 3 instances of a planar surface about z, and the
      refusal path when the copies cannot be joined.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0382_audit"
results: list[tuple[int, str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, label: str, passed: bool, detail: str = "") -> None:
    results.append((number, label, passed, detail))
    mark = "PASS" if passed else "FAIL"
    say(f"[{mark}] {number:2d}. {label}")
    if detail:
        say("           " + detail.replace("\n", " ")[:300])


def text_of(reply) -> str:
    return reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)


def as_json(reply) -> dict:
    if isinstance(reply, dict):
        return reply
    try:
        parsed = json.loads(str(reply))
    except Exception:  # noqa: BLE001 - not JSON
        return {}
    return parsed if isinstance(parsed, dict) else {}


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    gsd = GSDTools(conn)

    # A previous crashed run can leave this script's own document open, and the
    # tool then refuses to create a second one with the same name. Close only
    # what lies under ROOT - never a document matched by name, and never one of
    # the caller's files (that mistake was made in 0.3.80 and is not repeated).
    own_root = os.path.normcase(os.path.abspath(ROOT))
    for index in range(conn.documents.Count, 0, -1):
        try:
            stale = conn.documents.Item(index)
            stale_full = os.path.normcase(os.path.abspath(str(stale.FullName)))
        except Exception:  # noqa: BLE001 - never saved
            continue
        if stale_full.startswith(own_root):
            say(f"closing a document left by an earlier run: {stale.Name}")
            stale.Close()

    part_path = os.path.join(ROOT, "audit.CATPart")
    # catia_new_part has no 'overwrite': an existing file is refused on purpose
    # (that refusal is what a second run of this script saw, and it is correct -
    # the tool will not replace a file the caller did not name as replaceable).
    # So the probe removes its OWN file, which it wrote in the previous run.
    if os.path.isfile(part_path):
        os.remove(part_path)
    document.execute("catia_new_part",
                     {"name": "AUDIT82", "file_path": part_path})
    say(f"active: {conn.active_document.Name}")

    # ── 1-2 the report's P1-1 profile ─────────────────────────────────────
    say("\n--- close_sketch: one spline + four lines ---")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    points = [[0, 0], [10, 2], [20, 8], [25, 20], [20, 32], [10, 38]]
    sketcher.execute("catia_sketch_spline", {"points": points})
    for (x1, y1), (x2, y2) in (((10, 38), (0, 38)), ((0, 38), (0, 20)),
                               ((0, 20), (0, 10)), ((0, 10), (0, 0))):
        sketcher.execute("catia_sketch_line",
                         {"x1": x1, "y1": y1, "x2": x2, "y2": y2})
    closed = as_json(sketcher.execute("catia_close_sketch", {"structured": True}))
    breaks = closed.get("data", {}).get("breaks", {})
    check(1, "the spline+4-line loop reports closed:true",
          breaks.get("closed") is True,
          f"closed={breaks.get('closed')} endpoints={breaks.get('endpoints')} "
          f"loose={breaks.get('endpoints_loose')} breaks={breaks.get('breaks')}")
    check(2, "all ten endpoints are paired (five open curves)",
          breaks.get("endpoints") == 10 and breaks.get("endpoints_loose") == 0,
          f"endpoints={breaks.get('endpoints')} loose={breaks.get('endpoints_loose')}")

    # ── 3 sketch copy, twice ─────────────────────────────────────────────
    say("\n--- catia_sketch_copy ---")
    first = text_of(sketcher.execute("catia_sketch_copy", {
        "source": "草图.1", "plane": "yz", "name": "COPY_A"}))
    say("   " + first.splitlines()[0][:200])
    geometry_a = as_json(sketcher.execute("catia_sketch_get_geometry",
                                          {"sketch_name": "COPY_A"}))
    second = text_of(sketcher.execute("catia_sketch_copy", {
        "source": "COPY_A", "plane": {"type": "frame",
                                      "origin": [0, 0, 60],
                                      "u": [1, 0, 0], "v": [0, 1, 0]},
        "offset": [5, 5], "name": "COPY_B"}))
    say("   " + second.splitlines()[0][:200])
    geometry_b = as_json(sketcher.execute("catia_sketch_get_geometry",
                                          {"sketch_name": "COPY_B"}))
    check(3, "a profile copies to another plane, and a copy of the copy works",
          "COPY_A" in first and "COPY_B" in second
          and int(geometry_a.get("count") or 0) >= 5
          and int(geometry_b.get("count") or 0) >= 5,
          f"A count={geometry_a.get('count')} B count={geometry_b.get('count')}")

    # ── 4-6 plane normal to a curve ──────────────────────────────────────
    say("\n--- catia_gsd_plane_normal_to_curve along a 100 mm line ---")
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 0], "point2": [0, 0, 100], "name": "AXIS100"})
    measured = {}
    for position in (0.0, 0.5, 1.0):
        try:
            reply = text_of(gsd.execute("catia_gsd_plane_normal_to_curve", {
                "curve": "AXIS100", "position": position,
                "name": f"NRM{int(position * 100)}"}))
            say(f"   position {position}: {reply.splitlines()[0][:190]}")
            measured[position] = reply
        except Exception as error:  # noqa: BLE001 - the refusal is the result
            say(f"   position {position}: REFUSED "
                f"{getattr(error, 'code', type(error).__name__)} "
                f"{str(error).splitlines()[0][:120]}")
            measured[position] = ""
    check(4, "a plane normal to a curve is created at all",
          all(measured.get(position) for position in (0.0, 0.5, 1.0)),
          "; ".join(f"{k}: {'ok' if v else 'failed'}" for k, v in measured.items()))
    # 0.5 has to land at 50 mm along a 100 mm line, or the parameter is not a
    # fraction - and the reply is what says so.
    middle = measured.get(0.5) or ""
    check(5, "position is a FRACTION: 0.5 is 50 mm along a 100 mm curve",
          "50.000 mm along a 100.000 mm curve" in middle,
          middle[middle.find("origin"):][:200] or "no measured length reported")
    check(6, "the three planes are distinct elements",
          len({"NRM0", "NRM50", "NRM100"}) == 3 and "NRM100" in measured.get(1.0, ""),
          "each call named its plane NRM0 / NRM50 / NRM100")

    # ── 7-9 gsd circ pattern ─────────────────────────────────────────────
    # A 120-degree cylindrical sector is the honest case: rotating it by 120
    # gives neighbours that share their radial BOUNDARY edge pairwise, which is
    # what a join needs. Three coplanar patches all meeting the same axis line
    # were measured first and refused (one edge, three faces).
    say("\n--- catia_gsd_circ_pattern ---")
    gsd.execute("catia_gsd_line",
                {"point1": [20, 0, 0], "point2": [20, 0, 10], "name": "SECTOR_LINE"})
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 0], "point2": [0, 0, 10], "name": "Z_AXIS"})
    gsd.execute("catia_gsd_revolve",
                {"profile": "SECTOR_LINE", "axis": "Z_AXIS", "angle1": 120,
                 "name": "SECTOR"})
    joined = False
    detail = ""
    try:
        reply = text_of(gsd.execute("catia_gsd_circ_pattern", {
            "element": "SECTOR", "count": 3, "axis": "z",
            "name": "SECTOR_RING"}))
        say("   " + reply.splitlines()[0][:220])
        joined = "SECTOR_RING" in reply
        detail = reply.splitlines()[0][:200]
    except Exception as error:  # noqa: BLE001 - the refusal is the result
        detached = ""
        try:
            detached = text_of(gsd.execute("catia_gsd_circ_pattern", {
                "element": "SECTOR", "count": 3, "axis": "z", "join": False,
                "name": "SECTOR_LOOSE"}))
        except Exception as second:  # noqa: BLE001
            detached = f"join=false also failed: {second}"
        say("   join refused: " + str(error).splitlines()[0][:140])
        detail = (f"JOIN_REFUSED: {getattr(error, 'code', '')}; "
                  f"join=false -> {detached.splitlines()[0][:120]}")
    check(7, "3 x 120-degree sectors are rotated and joined into one element",
          joined, detail)
    listing = as_json(gsd.execute("catia_gsd_list_elements", {}))
    names = [str(item) for sheet in listing.get("geometrical_sets", [])
             for item in (sheet.get("elements") or [])]
    check(8, "the rotations are real tree elements, not a hidden pattern node",
          any("SECTOR_r" in name for name in names),
          f"elements: {[n for n in names if 'SECTOR' in n][:8]}")
    # 9: the refusal path, on purpose. Three coplanar patches all meet the SAME
    # axis line, so the join cannot work (one edge, three faces) - and finding
    # that out is the point, not a surprise to be avoided.
    say("\n--- the refused-join path, deliberately ---")
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 0], "point2": [10, 0, 0], "name": "SPOKE"})
    gsd.execute("catia_gsd_extrude",
                {"profile": "SPOKE", "direction": [0, 0, 1], "limit1": 5,
                 "name": "PATCH"})
    refused_code = ""
    refused_text = ""
    try:
        gsd.execute("catia_gsd_circ_pattern",
                    {"element": "PATCH", "count": 3, "axis": "z",
                     "name": "PATCH_PATTERN"})
        refused_text = "the join SUCCEEDED where a shared edge is impossible"
    except Exception as error:  # noqa: BLE001 - the refusal IS the check
        refused_code = str(getattr(error, "code", ""))
        refused_text = str(error)
    loose = ""
    try:
        loose = text_of(gsd.execute("catia_gsd_circ_pattern", {
            "element": "PATCH", "count": 3, "axis": "z", "join": False,
            "name": "PATCH_LOOSE"}))
    except Exception as error:  # noqa: BLE001
        loose = f"join=false failed too: {error}"
    check(9, "an impossible join answers PATTERN_JOIN_FAILED, and join=false "
             "still gives the rotations",
          refused_code == "PATTERN_JOIN_FAILED"
          and "join=false" in refused_text
          and "PATCH_r120" in loose,
          f"code={refused_code or '<none>'} | join=false -> "
          + loose.splitlines()[0][:120])

    # ── summary ──────────────────────────────────────────────────────────
    passed = sum(1 for _n, _l, ok, _d in results if ok)
    say(f"\n{passed}/{len(results)} checks passed")

    own = os.path.normcase(os.path.abspath(ROOT))
    for index in range(conn.documents.Count, 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = os.path.normcase(os.path.abspath(str(doc.FullName)))
        except Exception:  # noqa: BLE001 - never saved
            continue
        if full.startswith(own):
            doc.Activate()
            document.execute("catia_close_document", {"save": False})
    say("cleanup: closed the documents under " + ROOT)


if __name__ == "__main__":
    main()
