"""0.3.83 audit: the four items the 0.3.82 acceptance report raised, on B30.

 1-2  catia_sketch_copy onto a DATUM PLANE BY NAME. 0.3.82's own description
      promised it and the resolver refused every name with "Unknown plane", so
      plane_normal_to_curve and sketch_copy could not be chained. The report's
      two cases: a plane from plane_normal_to_curve, and one from plane_offset.
 3    close_sketch's profile summary for a CLOSED SPLINE: bbox used to collapse
      to a point and outline to 3 sides of a 4-sided loop.
 4    The join refusal for copies that TOUCH but are refused (measured 0.0000):
      the old message claimed "copies that all touch one common line", which was
      a cause invented from one experiment.
 5    The same refusal for copies that do NOT touch: the other branch, where the
      distance is the answer.
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

ROOT = r"F:\catia_probe_v0383"
results: list[tuple[int, str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, label: str, passed: bool, detail: str = "") -> None:
    results.append((number, label, passed, detail))
    say(f"[{'PASS' if passed else 'FAIL'}] {number:2d}. {label}")
    if detail:
        say("           " + detail.replace("\n", " ")[:320])


def text_of(reply) -> str:
    return reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)


def as_json(reply) -> dict:
    if isinstance(reply, dict):
        return reply
    try:
        parsed = json.loads(str(reply))
    except Exception:  # noqa: BLE001
        return {}
    return parsed if isinstance(parsed, dict) else {}


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    gsd = GSDTools(conn)

    own_root = os.path.normcase(os.path.abspath(ROOT))
    for index in range(conn.documents.Count, 0, -1):
        try:
            stale = conn.documents.Item(index)
            stale_full = os.path.normcase(os.path.abspath(str(stale.FullName)))
        except Exception:  # noqa: BLE001
            continue
        if stale_full.startswith(own_root):
            say(f"closing a document left by an earlier run: {stale.Name}")
            stale.Close()

    part_path = os.path.join(ROOT, "audit83.CATPart")
    if os.path.isfile(part_path):
        os.remove(part_path)
    document.execute("catia_new_part",
                     {"name": "AUDIT83", "file_path": part_path})
    say(f"active: {conn.active_document.Name}")

    # ── the source profile: one closed spline, a 6 x 6 square ────────────
    say("\n--- close_sketch on a closed spline ---")
    sketcher.execute("catia_create_sketch", {"plane": "xz"})
    sketcher.execute("catia_sketch_spline", {
        "points": [[10, 10], [16, 10], [16, 16], [10, 16]], "closed": True})
    closed = as_json(sketcher.execute("catia_close_sketch", {"structured": True}))
    message = str(closed.get("message") or "")
    say("   " + message[:260])
    check(3, "a closed spline reports the whole square, not a degenerate point",
          "bbox (10.0000, 10.0000) .. (16.0000, 16.0000)" in message
          and "outline 24.000 mm" in message,
          message[message.find("Profile:"):][:200])
    check(4, "the spline outline is labelled a straight-line lower bound",
          "LOWER BOUND" in message,
          message[message.find("LOWER"):][:120] if "LOWER" in message else "missing")

    # ── a datum plane from a curve, then a copy onto it BY NAME ──────────
    say("\n--- sketch_copy onto a datum plane by name ---")
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 0], "point2": [0, 0, 100], "name": "AXIS83"})
    plane_reply = text_of(gsd.execute("catia_gsd_plane_normal_to_curve",
                                      {"curve": "AXIS83", "position": 0.5,
                                       "name": "Plane_Norm"}))
    say("   " + plane_reply.splitlines()[0][:160])
    first = ""
    try:
        first = text_of(sketcher.execute("catia_sketch_copy", {
            "source": "草图.1", "plane": "Plane_Norm", "name": "ON_NORMAL"}))
    except Exception as error:  # noqa: BLE001 - the refusal is the measurement
        first = f"REFUSED {getattr(error, 'code', '')}: {error}"
    say("   " + first.splitlines()[0][:200])
    check(1, "a sketch copies onto the plane plane_normal_to_curve just built",
          "ON_NORMAL" in first and "REFUSED" not in first, first[:200])

    gsd.execute("catia_gsd_plane_offset",
                {"base_plane": "xy", "offset": 20, "name": "Plane_Off20"})
    second = ""
    try:
        second = text_of(sketcher.execute("catia_sketch_copy", {
            "source": "草图.1", "plane": "Plane_Off20", "name": "ON_OFFSET"}))
    except Exception as error:  # noqa: BLE001
        second = f"REFUSED {getattr(error, 'code', '')}: {error}"
    say("   " + second.splitlines()[0][:200])
    check(2, "a sketch copies onto an offset datum plane by name",
          "ON_OFFSET" in second and "REFUSED" not in second, second[:200])

    # ── an unknown name has to list what does exist ──────────────────────
    unknown = ""
    try:
        sketcher.execute("catia_sketch_copy",
                         {"source": "草图.1", "plane": "Plane_Typo"})
    except Exception as error:  # noqa: BLE001
        unknown = f"{getattr(error, 'code', '')} | {error}"
    check(5, "a name that is not a plane lists the datum planes that exist",
          "PLANE_NOT_FOUND" in unknown and "Plane_Norm" in unknown,
          unknown[:220])

    # ── the join refusal, both measured branches ─────────────────────────
    say("\n--- the join refusal ---")
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 0], "point2": [10, 0, 0], "name": "SPOKE83"})
    gsd.execute("catia_gsd_extrude",
                {"profile": "SPOKE83", "direction": [0, 0, 1], "limit1": 5,
                 "name": "PATCH83"})
    touching = ""
    try:
        gsd.execute("catia_gsd_circ_pattern",
                    {"element": "PATCH83", "count": 3, "axis": "z",
                     "name": "PATCH83_PAT"})
    except Exception as error:  # noqa: BLE001
        touching = str(error)
    say("   touching case: " + touching.replace("\n", " ")[:260])
    check(6, "copies that touch are reported as touching, with the number",
          "touch (0.0000 mm apart)" in touching,
          touching[:240])
    check(7, "the refusal warns that the copies are visible and how to delete them",
          "VISIBLE" in touching and "catia_delete_feature(names=" in touching,
          touching[touching.find("The copies"):][:200]
          if "The copies" in touching else "missing")

    gsd.execute("catia_gsd_line",
                {"point1": [40, 0, 0], "point2": [50, 0, 0], "name": "FAR83"})
    gsd.execute("catia_gsd_extrude",
                {"profile": "FAR83", "direction": [0, 0, 1], "limit1": 5,
                 "name": "PATCH_FAR"})
    apart = ""
    try:
        gsd.execute("catia_gsd_circ_pattern",
                    {"element": "PATCH_FAR", "count": 2, "total_angle": 360,
                     "axis": "z", "name": "FAR_PAT"})
    except Exception as error:  # noqa: BLE001
        apart = str(error)
    say("   apart case: " + apart.replace("\n", " ")[:260])
    check(8, "copies that do NOT touch report the measured gap instead of a cause",
          "mm apart, so they do not touch" in apart,
          apart[:240])

    passed = sum(1 for _n, _l, ok, _d in results if ok)
    say(f"\n{passed}/{len(results)} checks passed")

    for index in range(conn.documents.Count, 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = os.path.normcase(os.path.abspath(str(doc.FullName)))
        except Exception:  # noqa: BLE001
            continue
        if full.startswith(own_root):
            doc.Activate()
            document.execute("catia_close_document", {"save": False})
    say("cleanup: closed the documents under " + ROOT)


if __name__ == "__main__":
    main()
