"""0.3.86 acceptance: the five things the three 2026-11-09 reports turned up.

  1  catia_create_sketch accepts a datum plane BY NAME - the schema used to
     answer "must be equal to one of the allowed values" for 'Plane72' while the
     description said names were fine
  2  the same plane still takes a sketch COPY (regression: that path worked)
  3  catia_gsd_boundary(edge=N) reports its share of the border instead of
     calling a 25 mm straight side "THIS loop only"
  4  catia_new_part saves into the reporter's own Chinese folder, through save_as
     and never through the bare Save whose message the reports quote
  5  hiding the construction set FIRST and a geometrical set SECOND does not take
     the solid's triangles out of the STL (their observation, measured)

Everything lives in this script's own folder; the only documents it closes are
ones whose FullName is under ROOT.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp import dialogs  # noqa: E402
from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0386\audit"
CJK = r"F:\WokrBuddy Catia Space\饮料瓶瓶底曲面建模"
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
    sketcher = SketcherTools(conn)
    export = ExportTools(conn)

    own_root = os.path.normcase(os.path.abspath(ROOT))
    for index in range(conn.documents.Count, 0, -1):
        try:
            stale = conn.documents.Item(index)
            full = os.path.normcase(os.path.abspath(str(stale.FullName)))
        except Exception:  # noqa: BLE001
            continue
        if full.startswith(own_root):
            stale.Close()

    cjk_target = CJK + r"\_probe86_audit.CATPart"
    for path in (os.path.join(ROOT, "audit86.CATPart"), cjk_target):
        if os.path.isfile(path):
            os.remove(path)

    # ── 4 first: the reporter's own Chinese folder, instrumented ─────────
    say("--- 4. catia_new_part into the reporter's Chinese folder ---")
    calls: list[str] = []
    original_save = dialogs.save
    original_save_as = dialogs.save_as

    def spy_save(doc, tries=3):
        calls.append("save")
        return original_save(doc, tries)

    def spy_save_as(doc, path, tries=6, wait=0.6):
        calls.append("save_as")
        return original_save_as(doc, path, tries, wait)

    dialogs.save = spy_save
    dialogs.save_as = spy_save_as
    try:
        reply = document.execute("catia_new_part",
                                 {"name": "AUDIT86_CN",
                                  "file_path": cjk_target})
        ok = os.path.isfile(cjk_target)
        check(4, "new_part saves into the Chinese folder, via save_as only",
              ok and calls == ["save_as"] and "Save failed" not in reply,
              f"file={ok}, calls={calls}, "
              f"size={os.path.getsize(cjk_target) if ok else 0} B")
    except Exception as error:  # noqa: BLE001
        check(4, "new_part saves into the Chinese folder, via save_as only",
              False, str(error).splitlines()[0][:220])
    finally:
        dialogs.save = original_save
        dialogs.save_as = original_save_as

    # Put the audit part somewhere else and leave the CJK one alone from here.
    try:
        for index in range(conn.documents.Count, 0, -1):
            doc = conn.documents.Item(index)
            if str(doc.Name).startswith("_probe86_audit"):
                doc.Close()
    except Exception:  # noqa: BLE001
        pass
    if os.path.isfile(cjk_target):
        os.remove(cjk_target)

    document.execute("catia_new_part",
                     {"name": "AUDIT86", "file_path": os.path.join(
                         ROOT, "audit86.CATPart")})
    part = conn.get_active_part()

    # ── 1-2 the angle datum plane as a sketch support ───────────────────
    say("--- 1-2. an angle datum plane as a sketch support ---")
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 0], "point2": [0, 0, 40], "name": "AXZ"})
    gsd.execute("catia_gsd_plane_angle",
                {"plane": "yz", "axis": "z", "angle": 72, "name": "Plane72"})
    part.Update()
    try:
        reply = sketcher.execute("catia_create_sketch", {"plane": "Plane72"})
        sketch_ok = "Plane72" in reply or "created" in reply.lower()
        detail = reply.splitlines()[0][:180]
    except Exception as error:  # noqa: BLE001
        sketch_ok = False
        detail = str(error).splitlines()[0][:220]
    check(1, "create_sketch accepts a datum plane by name",
          sketch_ok, detail)

    try:
        sketcher.execute("catia_sketch_circle",
                       {"cx": 0, "cy": 0, "radius": 5})
        sketcher.execute("catia_close_sketch", {})
        part.Update()
        reply = sketcher.execute("catia_sketch_copy",
                               {"source": "草图.1", "plane": "Plane72",
                                "name": "Copy72"})
        copy_ok = part.FindObjectByName("Copy72") is not None
        detail = reply.splitlines()[0][:180]
    except Exception as error:  # noqa: BLE001
        copy_ok = False
        detail = str(error).splitlines()[0][:220]
    check(2, "sketch_copy onto the same named plane still works",
          copy_ok, detail)

    # ── 3 the loop reply on a flat four-sided sector ────────────────────
    say("--- 3. gsd_boundary(edge=1) on a flat sector ---")
    corners = []
    for radius, angle in ((5.0, 0.0), (30.0, 0.0), (30.0, 72.0), (5.0, 72.0)):
        radians = math.radians(angle)
        corners.append([radius * math.cos(radians),
                        radius * math.sin(radians), 0.0])
    for i in range(4):
        gsd.execute("catia_gsd_line",
                    {"point1": corners[i], "point2": corners[(i + 1) % 4],
                     "name": f"S{i}"})
    gsd.execute("catia_gsd_fill",
                {"boundaries": ["S0", "S1", "S2", "S3"], "name": "SECTOR"})
    part.Update()
    # Two honest outcomes are possible here, and the point of the check is that
    # neither of them calls a straight side a "loop":
    #   * the selector works -> the reply gives its share of the border;
    #   * it does not      -> the refusal names BOTH situations instead of
    #                         asserting the caller passed a join result.
    try:
        reply = gsd.execute("catia_gsd_boundary",
                            {"surface": "SECTOR", "edge": 1, "name": "ONELOOP"})
        honest = ("PART of the border" in reply
                  or "WHOLE border" in reply) and "THIS loop only" not in reply
        detail = reply.splitlines()[0][:220]
    except Exception as error:  # noqa: BLE001
        text = str(error)
        honest = ("will not pretend to know which" in text
                  and "loft" in text and "THIS loop only" not in text)
        detail = text.splitlines()[0][:220]
    check(3, "the loop reply measures its share, or names both reasons honestly",
          honest, detail)

    # ── 5 hiding construction first, then the set ───────────────────────
    say("--- 5. hide construction, then the geometrical set ---")
    # A fresh document with exactly TWO things in it - one solid in the body,
    # one surface in a set - because a part carrying three surfaces cannot say
    # which triangles left the STL. The number to compare against is the one
    # taken BEFORE the surface existed.
    vis_path = os.path.join(ROOT, "vis.CATPart")
    if os.path.isfile(vis_path):
        os.remove(vis_path)
    document.execute("catia_new_part", {"name": "AUDIT86_VIS",
                                        "file_path": vis_path})
    part = conn.get_active_part()
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": -15, "y1": -10, "x2": 15, "y2": 10})
    sketcher.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"height": 5})
    part.Update()

    def triangles() -> int:
        """Triangle count of an STL of the whole part - the honest visible test."""
        target = os.path.join(ROOT, "vis.stl")
        if os.path.isfile(target):
            os.remove(target)
        try:
            export.execute("catia_export",
                           {"file_path": target, "format": "stl"})
        except Exception:  # noqa: BLE001
            return -1
        if not os.path.isfile(target):
            return -1
        size = os.path.getsize(target)
        # Binary STL: 84 byte header + 50 bytes per triangle.
        return max(0, (size - 84) // 50) if size > 84 else 0

    solid_only = triangles()
    gsd.execute("catia_gsd_line",
                {"point1": [0, -35, 0], "point2": [40, -35, 0], "name": "EDGE"})
    gsd.execute("catia_gsd_extrude",
                {"profile": "EDGE", "direction": [0, 1, 0], "limit1": 20,
                 "name": "PATCH"})
    part.Update()
    with_surface = triangles()
    design.execute("catia_set_visibility",
                   {"kind": "construction", "show": False})
    part.Update()
    after_construction = triangles()
    design.execute("catia_set_visibility",
                   {"name": "GSD_Set", "show": False})
    part.Update()
    after_set = triangles()
    check(5, "hiding construction then the set leaves the solid's triangles",
          solid_only > 0 and after_set == solid_only and with_surface > solid_only,
          f"solid alone={solid_only}, +surface={with_surface}, "
          f"after construction hide={after_construction}, "
          f"after set hide={after_set} (must equal 'solid alone')")

    say("")
    passed = sum(1 for _n, _l, ok, _d in RESULTS if ok)
    say(f"{passed}/{len(RESULTS)} checks passed")
    for number, label, ok, detail in RESULTS:
        if not ok:
            say(f"  FAILED {number}: {label} - {detail}")


if __name__ == "__main__":
    main()
