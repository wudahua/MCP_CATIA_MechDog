"""0.3.121 acceptance on B30: a view that is square-on, faces that know where
they are, and a tube in one call.

Nine checks, one fresh document per case. Every number below was measured first
by the probes of the same day (scripts/b30_v0423_probe_view_faces.py,
_view2.py, _faces.py, _face_verts.py, _solid_tube.py).

    1  set_view('front') forces PARALLEL projection and reads the camera back
    2  a custom direction looks where it is told (up defaulted off the sight)
    3  list_faces gives every face of a 60x40x40 block its own centroid
    4  the top and bottom faces are 40 mm apart in z, i.e. they are not the body
    5  a planar face has a plane origin and a unit normal; a curved one has none
    6  geometry=false still lists, and carries no centroid
    7  catia_solid_tube on a 50 mm line with r=5 gives pi*25*50 = 3926.9908 mm3
    8  a tube put into a body that already holds 6400 mm3 adds to it (union)
    9  a closed spine is refused and leaves nothing behind
   10  two lofts in one body say UNION and CAPS
   11  three rings built by ONE catia_gsd_splines call, then a loft through them
   12  a bad entry is refused at validation: nothing is built, not even entry 1
   13  a curve CATIA refuses mid-chain leaves the earlier one, named
   14  a repeated closing point on a closed ring is dropped AND counted

    .venv/Scripts/python.exe scripts/b30_v0423_audit.py
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0423")
ROOT = os.path.join(_PROBE_ROOT, "audit")
RESULTS: list[tuple[int, str, bool, str]] = []
TUBE_R5_50MM = math.pi * 25.0 * 50.0          # 3926.9908


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, title: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, title, bool(passed), detail))
    say(f"  [{'PASS' if passed else 'FAIL'}] {number}. {title}"
        + (f"  ({detail})" if detail else ""))


def close_ours(conn: CATIAConnection) -> None:
    for index in range(int(conn.documents.Count), 0, -1):
        document = conn.documents.Item(index)
        try:
            full = str(document.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).startswith(os.path.abspath(_PROBE_ROOT)):
            try:
                document.Close()
            except Exception:  # noqa: BLE001
                pass


def fresh(conn, documents, name: str):
    path = os.path.join(ROOT, f"{name}.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    for index in range(1, conn.documents.Count + 1):
        if conn.documents.Item(index).Name == f"{name}.CATPart":
            conn.documents.Item(index).Activate()
    return conn.get_active_part()


def block(conn, documents, name, sketcher, part_design, height=40):
    part = fresh(conn, documents, name)
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 60, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": height})
    return part


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)
    documents = DocumentTools(conn)
    export = ExportTools(conn)
    gsd = GSDTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)

    # ── 1-2: the view ────────────────────────────────────────────────────
    say("")
    say("── 1-2: set_view, and what the camera really holds ──")
    block(conn, documents, "View", sketcher, part_design, height=4)
    reply = export.execute("catia_set_view", {"view": "front"})
    say("  " + " ".join(str(reply).split())[:300])
    check(1, "front comes back parallel with the camera read back",
          "parallel projection" in reply and "the camera took them" in reply
          and "sight [0.0, 1.0, 0.0]" in reply, "ProjectionMode forced to 1")
    viewpoint = conn.active_window.ActiveViewer.Viewpoint3D
    check(1, "ProjectionMode is 1, not CATIA's default 0", viewpoint.ProjectionMode == 1,
          f"ProjectionMode={viewpoint.ProjectionMode}")
    reply = export.execute("catia_set_view", {"direction": [0, 0, -1]})
    say("  " + " ".join(str(reply).split())[:300])
    check(2, "a custom direction is honoured and gets a usable up vector",
          "sight [0.0, 0.0, -1.0]" in reply and "up [0.0, 1.0, 0.0]" in reply
          and "the camera took them" in reply, "direction/up")

    # ── 3-6: faces that know where they are ──────────────────────────────
    say("")
    say("── 3-6: list_faces on a 60 x 40 x 40 block ──")
    block(conn, documents, "FaceBox", sketcher, part_design)
    payload = json.loads(part_design.execute("catia_list_faces", {}))
    faces = payload["faces"]
    with_centroid = [item for item in faces if "centroid" in item]
    check(3, "every face carries its own centroid",
          len(faces) == 6 and len(with_centroid) == 6,
          f"{len(with_centroid)}/{len(faces)} faces")
    zs = sorted(item["centroid"][2] for item in with_centroid)
    flat = [item for item in with_centroid
            if abs(item["centroid"][2]) < 1e-6
            or abs(item["centroid"][2] - 40) < 1e-6]
    check(4, "the two flat faces are 40 mm apart in z, both centred at x=30 y=20",
          abs(zs[0]) < 1e-6 and abs(zs[-1] - 40) < 1e-6 and len(flat) == 2
          and all(abs(item["centroid"][0] - 30) < 1e-6
                  and abs(item["centroid"][1] - 20) < 1e-6 for item in flat),
          f"z {zs[0]:.1f}..{zs[-1]:.1f}, {len(flat)} flat")
    normals = [item["normal"] for item in faces if "normal" in item]
    unit = all(abs(math.sqrt(sum(c * c for c in n)) - 1.0) < 1e-3 for n in normals)
    check(5, "all six planar faces give a unit normal and a plane origin",
          len(normals) == 6 and unit and all("plane_origin" in item for item in faces),
          f"{len(normals)} normals")
    quick = json.loads(part_design.execute("catia_list_faces", {"geometry": False}))
    check(6, "geometry=false still lists six faces, with no centroid",
          quick["count"] == 6
          and all("centroid" not in item for item in quick["faces"]),
          "fast listing")

    # ── 7: a tube in one call ────────────────────────────────────────────
    say("")
    say("── 7: catia_solid_tube on a 50 mm line, r=5 ──")
    part = fresh(conn, documents, "TubeOne")
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 0], "point2": [50, 0, 0], "name": "Spine"})
    reply = gsd.execute("catia_solid_tube",
                        {"curve": "Spine", "radius": 5, "name": "Neck"})
    say("  " + " ".join(str(reply).split())[:300])
    volume = part_design._body_volume_mm3(part, part.Bodies.Item("Neck.Body"))
    check(7, f"the tube is {TUBE_R5_50MM:.4f} mm3 (pi*r^2*length)",
          volume is not None and abs(volume - TUBE_R5_50MM) < 0.01,
          f"{volume:.4f} mm3" if volume is not None else "unreadable")
    check(7, "the reply judges the volume instead of printing it",
          "constant-radius tube" in reply and "1.000x" in reply, "ratio")

    # ── 8: into a body that already holds material ───────────────────────
    say("")
    say("── 8: a tube unioned into a body that already holds 6400 mm3 ──")
    part = fresh(conn, documents, "TubeMerge")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 40, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 4})
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 20], "point2": [50, 0, 20], "name": "Neck2"})
    reply = gsd.execute("catia_solid_tube",
                        {"curve": "Neck2", "radius": 3, "body": "零件几何体",
                         "name": "Spout"})
    say("  " + " ".join(str(reply).split())[:260])
    total = part_design._body_volume_mm3(part, part.MainBody)
    predicted = 6400.0 + math.pi * 9.0 * 50.0
    check(8, f"the body totals {predicted:.1f} mm3, the pad plus the tube",
          total is not None and abs(total - predicted) < 0.05,
          f"{total:.4f} mm3" if total is not None else "unreadable")

    # ── 9: a closed spine is refused and leaves nothing ──────────────────
    say("")
    say("── 9: a circle as the spine ──")
    part = fresh(conn, documents, "TubeClosed")
    gsd.execute("catia_gsd_circle",
                {"center": [0, 0, 0], "radius": 20, "support_plane": "xy",
                 "name": "Ring"})
    before = part.Bodies.Count
    refused = ""
    try:
        gsd.execute("catia_solid_tube", {"curve": "Ring", "radius": 2})
    except Exception as error:  # noqa: BLE001
        refused = str(getattr(error, "code", "")) + " " + " ".join(str(error).split())
    say("  " + refused[:240])
    after_bodies = ", ".join(str(part.Bodies.Item(i).Name)
                            for i in range(1, part.Bodies.Count + 1))
    check(9, "the closed spine is refused and no tube body appeared",
          "ENGRAVE_CURVE_UNPARAMETRISABLE" in refused
          and part.Bodies.Count == before
          and "Body" not in after_bodies,
          after_bodies)

    # ── 10: solid_loft says the two things it never said ─────────────────
    say("")
    say("── 10: two lofts in one body - the reply has to say UNION and CAPS ──")
    part = fresh(conn, documents, "LoftTalk")
    for z, radius, name in ((0.0, 8.0, "S1"), (40.0, 16.0, "S2")):
        gsd.execute("catia_gsd_circle",
                    {"center": [0, 0, z], "radius": radius, "support_plane": "xy",
                     "name": name})
    part_design.execute("catia_solid_loft", {"sections": ["S1", "S2"], "name": "Vase"})
    for z, radius, name in ((40.0, 16.0, "S3"), (70.0, 6.0, "S4")):
        gsd.execute("catia_gsd_circle",
                    {"center": [0, 0, z], "radius": radius, "support_plane": "xy",
                     "name": name})
    reply = part_design.execute("catia_solid_loft", {"sections": ["S3", "S4"], "name": "Neck"})
    say("  " + " ".join(str(reply).split())[:320])
    check(10, "the reply says the second loft UNIONED into the body",
          "UNIONED" in reply and "no catia_boolean" in reply, "auto-union")
    check(10, "the reply says the first and last section planes are the end faces",
          "FIRST and LAST section's plane" in reply, "end caps")

    # ── 11: three rings, one call ────────────────────────────────────────
    say("")
    say("── 11: catia_gsd_splines - three rings, one call, then a loft ──")
    part = fresh(conn, documents, "BatchSplines")

    def ring(z, radius, count=18):
        return [[round(radius * math.cos(2 * math.pi * i / count), 4),
                 round(radius * math.sin(2 * math.pi * i / count), 4), z]
                for i in range(count)]

    reply = gsd.execute("catia_gsd_splines", {"entries": [
        {"name": "S01", "points": ring(0, 10), "closed": True},
        {"name": "S02", "points": ring(30, 14), "closed": True},
        {"name": "S03", "points": ring(60, 6), "closed": True}]})
    say("  " + " ".join(str(reply).split())[:240])
    present = [name for name in ("S01", "S02", "S03")
               if part.FindObjectByName(name) is not None]
    check(11, "one call built three rings and they are in the tree",
          len(present) == 3 and "Built 3 spline(s)" in reply, ", ".join(present))
    loft = part_design.execute("catia_solid_loft",
                               {"sections": ["S01", "S02", "S03"], "name": "Cone"})
    volume = part_design._body_volume_mm3(part, part.MainBody)
    check(11, "a solid loft through them measures 27945.3 mm3",
          volume is not None and abs(volume - 27945.3) < 2.0,
          f"{volume:.1f} mm3" if volume is not None else "unreadable")

    # ── 12: a bad entry, refused before anything is built ────────────────
    say("")
    say("── 12: entry 2 has one point ──")
    part = fresh(conn, documents, "BatchBad")
    refused = ""
    try:
        gsd.execute("catia_gsd_splines", {"entries": [
            {"name": "T01", "points": ring(0, 10), "closed": True},
            {"name": "T02", "points": [[0, 0, 10]], "closed": True}]})
    except Exception as error:  # noqa: BLE001
        refused = str(getattr(error, "code", "")) + " " + " ".join(str(error).split())
    say("  " + refused[:200])
    check(12, "the bad entry stops the whole list before CATIA is touched",
          "BAD_ENTRY" in refused
          and part.FindObjectByName("T01") is None
          and "Nothing was created" in refused, "T01 absent")

    # ── 13: CATIA refuses one curve mid-chain ────────────────────────────
    say("")
    say("── 13: entry 2 repeats a point, so CATIA refuses THAT curve ──")
    part = fresh(conn, documents, "BatchMid")
    refused = ""
    data = {}
    try:
        gsd.execute("catia_gsd_splines", {"entries": [
            {"name": "U01", "points": ring(0, 10), "closed": True},
            {"name": "U02", "points": [[0, 0, 0], [0, 0, 0], [10, 0, 0],
                                       [10, 10, 0]], "closed": False}]})
    except Exception as error:  # noqa: BLE001
        refused = str(getattr(error, "code", "")) + " " + " ".join(str(error).split())
        data = dict(getattr(error, "data", {}) or {})
    say("  " + refused[:240])
    check(13, "the earlier curve stays, is named, and nothing is rolled back",
          "SPLINES_PARTIAL" in refused and data.get("created") == ["U01"]
          and part.FindObjectByName("U01") is not None
          and "NOTHING WAS ROLLED BACK" in refused, str(data.get("created")))

    # ── 14: a repeated closing point is dropped and counted ──────────────
    say("")
    say("── 14: a closed ring whose last point repeats the first ──")
    part = fresh(conn, documents, "BatchRepeat")
    ring19 = ring(0, 10) + [ring(0, 10)[0]]
    reply = gsd.execute("catia_gsd_splines",
                        {"entries": [{"name": "R01", "points": ring19,
                                      "closed": True}]})
    say("  " + " ".join(str(reply).split())[:240])
    check(14, "19 points go in, 18 go on the curve, and the reply says so",
          "'R01' (18 point(s), closed)" in reply
          and "1 closing point(s)" in reply, "18 on the curve")

    say("")
    failed = [row for row in RESULTS if not row[2]]
    say(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    for number, title, _, detail in failed:
        say(f"  FAILED {number}: {title} ({detail})")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
