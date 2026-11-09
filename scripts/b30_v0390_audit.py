"""0.3.90 acceptance: the record that did not follow a delete, and the route.

  1  `new_part` into the REPORTED folder (ASCII ancestor with a space + Chinese
     subfolder) is staged, the file lands, and the spies prove no bare
     `doc.Save()` is ever the call - the 0.3.89 report's 3/3 failure
  2  two lines, delete the second, `sketch_copy`: the copy holds ONE line and a
     20 mm outline, not two lines and 30 mm (measured 2026-11-09 before the fix:
     the record still held the deleted line, and the copy's outline was twice the
     source's - the shape of the report's "2 spline(s)")
  3  a `SPLIT_FAILED` refusal carries next_actions that name the cutter check,
     the join/piece split and `cap_center`, instead of the generic template
  4  the same refusal still reports the measured gap and `modified: false`

Everything lives in this script's own folder plus one probe file in the reporter's
folder; only documents with a FullName under those are closed, and only the files
this script created are removed.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools import document as document_module  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0390\audit"
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
    sketcher = SketcherTools(conn)

    own_root = os.path.normcase(os.path.abspath(ROOT))
    for index in range(conn.documents.Count, 0, -1):
        try:
            full = str(conn.documents.Item(index).FullName)
        except Exception:  # noqa: BLE001
            continue
        name = os.path.basename(full).lower()
        if (os.path.normcase(os.path.abspath(full)).startswith(own_root)
                or name.startswith("verify_0390")):
            say(f"closing leftover {os.path.basename(full)}")
            conn.documents.Item(index).Close()

    # ── 1 the reported folder, with the save functions watched ───────────
    say("--- 1. new_part into the reported folder ---")
    calls: list[tuple[str, str]] = []
    real_save = document_module.dialogs.save
    real_save_as = document_module.dialogs.save_as

    def spy_save(doc, *args, **kwargs):
        calls.append(("save", str(getattr(doc, "Name", "?"))))
        return real_save(doc, *args, **kwargs)

    def spy_save_as(doc, path, *args, **kwargs):
        calls.append(("save_as", str(path)))
        return real_save_as(doc, path, *args, **kwargs)

    document_module.dialogs.save = spy_save
    document_module.dialogs.save_as = spy_save_as
    target = os.path.join(CJK, "verify_0390_audit.CATPart")
    try:
        if os.path.isfile(target):
            os.remove(target)
        reply = document.execute("catia_new_part",
                                 {"name": "AUDIT90", "file_path": target})
        staged = "through an ASCII staging file" in reply
        landed = os.path.isfile(target) and os.path.getsize(target) > 1000
        bare = [where for kind, where in calls if kind == "save"]
        check(1, "the reported folder saves through the staged route",
              staged and landed and not bare,
              f"staged={staged}, file={os.path.getsize(target) if landed else 0} B, "
              f"bare save calls={bare or 'none'}")
    except Exception as error:  # noqa: BLE001
        check(1, "the reported folder saves through the staged route", False,
              f"{type(error).__name__}: {str(error).splitlines()[0][:200]}")
    finally:
        document_module.dialogs.save = real_save
        document_module.dialogs.save_as = real_save_as

    # ── a clean part for the sketch and split checks ─────────────────────
    say("")
    path = os.path.join(ROOT, "audit90.CATPart")
    if os.path.isfile(path):
        os.remove(path)
    document.execute("catia_new_part", {"name": "AUDIT90S", "file_path": path})
    part = conn.get_active_part()

    # ── 2 the record follows a delete ────────────────────────────────────
    say("--- 2. two lines, delete one, copy ---")
    try:
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_line", {"x1": 0, "y1": 0, "x2": 20, "y2": 0})
        sketcher.execute("catia_sketch_line",
                         {"x1": 20, "y1": 0, "x2": 20, "y2": 10})
        sketcher.execute("catia_close_sketch", {})
        geometry = json.loads(sketcher.execute("catia_sketch_get_geometry",
                                               {"sketch_name": "草图.1"}))
        last = geometry["elements"][-1]["index"]
        deleted = sketcher.execute("catia_sketch_delete_element", {"index": last})
        copy = sketcher.execute("catia_sketch_copy",
                                {"source": "草图.1", "plane": "yz"})
        one_line = "1 element(s)" in copy and "1 line(s)" in copy
        twenty = "outline 20.000 mm" in copy
        check(2, "a copy of a sketch after a delete holds only what is left",
              one_line and twenty and "dropped that" in deleted,
              f"deleted: {'dropped that' in deleted}; copy: "
              f"{[line for line in copy.split('.') if 'line(s)' in line or 'element(s)' in line][:2]}")
    except Exception as error:  # noqa: BLE001
        check(2, "a copy of a sketch after a delete holds only what is left",
              False, f"{type(error).__name__}: {str(error).splitlines()[0][:200]}")

    # ── 3 + 4 a split refusal that says what to do ──────────────────────
    say("")
    say("--- 3/4. a split refusal: measured gap, modified false, next_actions ---")
    corners = [(-20, -20, 0), (20, -20, 0), (20, 20, 0), (-20, 20, 0)]
    for index in range(4):
        gsd.execute("catia_gsd_line",
                    {"point1": list(corners[index]),
                     "point2": list(corners[(index + 1) % 4]),
                     "name": f"A90_{index}"})
    gsd.execute("catia_gsd_fill",
                {"boundaries": [f"A90_{i}" for i in range(4)], "name": "A90_PATCH"})
    gsd.execute("catia_gsd_circle",
                {"center": [0, 0, 3], "support_plane": "xy", "radius": 6,
                 "name": "A90_ABOVE3"})
    gsd.execute("catia_gsd_plane_angle",
                {"plane": "xy", "axis": "x", "angle": 45, "name": "A90_TILT45"})
    gsd.execute("catia_gsd_circle",
                {"center": [0, 0, 0], "support_plane": "A90_TILT45", "radius": 6,
                 "name": "A90_TILTED"})
    part.Update()

    try:
        gsd.execute("catia_gsd_split",
                    {"element": "A90_PATCH", "cutter": "A90_TILTED",
                     "name": "A90_BAD"})
        check(3, "the split refusal carries actionable next_actions", False,
              "the split was accepted")
        check(4, "the split refusal reports its gap and modified:false", False,
              "the split was accepted")
    except Exception as error:  # noqa: BLE001
        actions = list(getattr(error, "next_actions", []) or [])
        text = " ".join(actions)
        check(3, "the split refusal carries actionable next_actions",
              len(actions) >= 3 and "catia_measure_distance" in text
              and "cap_center" in text and "join" in text,
              f"{len(actions)} action(s): {actions[0][:80] if actions else 'none'}")
        check(4, "the split refusal reports its gap and modified:false",
              getattr(error, "code", "") == "SPLIT_FAILED"
              and getattr(error, "modified", None) is False
              and "0.0000 mm" in str(error),
              f"code={getattr(error, 'code', '')}, "
              f"modified={getattr(error, 'modified', '?')}")

    try:
        gsd.execute("catia_gsd_split",
                    {"element": "A90_PATCH", "cutter": "A90_ABOVE3"})
    except Exception as error:  # noqa: BLE001
        actions = list(getattr(error, "next_actions", []) or [])
        say(f"    miss refusal: code={getattr(error, 'code', '')}, "
            f"{len(actions)} action(s)")

    say("")
    passed = sum(1 for _n, _l, ok, _d in RESULTS if ok)
    say(f"{passed}/{len(RESULTS)} checks passed")

    try:
        conn.active_document.Save()
    except Exception:  # noqa: BLE001
        pass
    for index in range(conn.documents.Count, 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = str(doc.FullName)
        except Exception:  # noqa: BLE001
            continue
        if (os.path.normcase(os.path.abspath(full)).startswith(own_root)
                or os.path.basename(full).lower().startswith("verify_0390")):
            try:
                doc.Close()
            except Exception:  # noqa: BLE001
                pass
    try:
        if os.path.isfile(target):
            os.remove(target)
    except OSError:  # noqa: BLE001
        pass
    say("still open: " + ", ".join(str(conn.documents.Item(i).Name)
                                  for i in range(1, conn.documents.Count + 1)))


if __name__ == "__main__":
    main()
