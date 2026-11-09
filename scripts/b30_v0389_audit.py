"""0.3.89 acceptance: the two 0.3.88 reports, measured.

  1  `catia_new_part` into the REPORTED path shape - ASCII ancestor folder with a
     SPACE in it + a Chinese subfolder - is staged, and the probe's spies prove
     a bare `doc.Save()` is never the call (two `save_as`, zero `save`)
  2  `catia_gsd_split` with a circle 3 mm above the patch is refused BEFORE
     anything is built, with 3.0000 mm in the message (SPLIT_CUTTER_MISSES)
  3  a circle lying ON the patch still splits (the control - the new pre-check
     must not break the case that works)
  4  a circle that only CROSSES the patch (45-degree support plane) fails at
     update as SPLIT_FAILED, and the refusal carries 0.0000 mm, both candidate
     causes and the cap_center route - this is the reporter's case, reproduced

Everything lives in this script's own folder; the only documents closed are the
ones with a FullName under it, and the one probe file it created is removed.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools import document as document_module  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402

ROOT = r"F:\catia_probe_v0389\audit"
# The reported shape: "WokrBuddy Catia Space" has a SPACE, "饮料瓶" is Chinese.
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

    own = [os.path.normcase(os.path.abspath(ROOT)),
           os.path.normcase(os.path.abspath(CJK))]
    for index in range(conn.documents.Count, 0, -1):
        try:
            full = str(conn.documents.Item(index).FullName)
        except Exception:  # noqa: BLE001
            continue
        name = os.path.basename(full).lower()
        if os.path.normcase(os.path.abspath(full)).startswith(own[0]) or \
                name.startswith("verify_0389"):
            say(f"closing leftover {os.path.basename(full)}")
            conn.documents.Item(index).Close()

    # ── 1 the reported path shape, with the save functions watched ───────
    say("--- 1. new_part into 'ASCII ancestor with a SPACE' + Chinese subfolder ---")
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

    target = os.path.join(CJK, "verify_0389_audit.CATPart")
    if os.path.isfile(target):
        os.remove(target)
    try:
        reply = document.execute("catia_new_part",
                                 {"name": "AUDIT89", "file_path": target})
        staged = "through an ASCII staging file" in reply
        landed = os.path.isfile(target) and os.path.getsize(target) > 1000
        size = os.path.getsize(target) if os.path.isfile(target) else 0
        bare = [where for kind, where in calls if kind == "save"]
        save_as_calls = [where for kind, where in calls if kind == "save_as"]
        check(1, "the reported path shape is saved through the staged route",
              staged and landed and not bare and len(save_as_calls) == 2,
              f"staged={staged}, file={size} B, save_as calls={len(save_as_calls)}, "
              f"bare save calls={bare or 'none'}")
    except Exception as error:  # noqa: BLE001
        check(1, "the reported path shape is saved through the staged route", False,
              f"{type(error).__name__}: {str(error).splitlines()[0][:200]}")
    finally:
        document_module.dialogs.save = real_save
        document_module.dialogs.save_as = real_save_as

    # ── a clean part for the split checks ────────────────────────────────
    say("")
    say("--- building a 40x40 patch and three circles ---")
    path = os.path.join(ROOT, "split89.CATPart")
    if os.path.isfile(path):
        os.remove(path)
    document.execute("catia_new_part", {"name": "AUDIT89S", "file_path": path})
    part = conn.get_active_part()
    corners = [(-20, -20, 0), (20, -20, 0), (20, 20, 0), (-20, 20, 0)]
    for index in range(4):
        gsd.execute("catia_gsd_line",
                    {"point1": list(corners[index]),
                     "point2": list(corners[(index + 1) % 4]),
                     "name": f"A89_{index}"})
    gsd.execute("catia_gsd_fill",
                {"boundaries": [f"A89_{i}" for i in range(4)], "name": "A89_PATCH"})
    gsd.execute("catia_gsd_circle",
                {"center": [0, 0, 3], "support_plane": "xy", "radius": 6,
                 "name": "A89_ABOVE3"})
    gsd.execute("catia_gsd_circle",
                {"center": [0, 0, 0], "support_plane": "xy", "radius": 6,
                 "name": "A89_ON"})
    gsd.execute("catia_gsd_plane_angle",
                {"plane": "xy", "axis": "x", "angle": 45, "name": "A89_TILT45"})
    gsd.execute("catia_gsd_circle",
                {"center": [0, 0, 0], "support_plane": "A89_TILT45", "radius": 6,
                 "name": "A89_TILTED"})
    part.Update()
    say("  patch and circles built")

    # ── 2 a cutter that never reaches the element ────────────────────────
    say("")
    say("--- 2. circle 3 mm above the patch ---")
    try:
        reply = gsd.execute("catia_gsd_split",
                            {"element": "A89_PATCH", "cutter": "A89_ABOVE3",
                             "name": "A89_MISS_SPLIT"})
        check(2, "a cutter that misses is refused with its distance", False,
              f"the split was ACCEPTED: {reply.splitlines()[0][:150]}")
    except Exception as error:  # noqa: BLE001
        code = getattr(error, "code", "")
        text = str(error)
        left = part.FindObjectByName("A89_MISS_SPLIT") is not None
        check(2, "a cutter that misses is refused with its distance",
              code == "SPLIT_CUTTER_MISSES" and "3.0000 mm" in text and not left,
              f"code={code}, element left in tree={left}, "
              f"message: {text.splitlines()[0][:150]}")

    # ── 3 the control: a circle lying on the patch ───────────────────────
    say("")
    say("--- 3. circle lying on the patch (control) ---")
    try:
        reply = gsd.execute("catia_gsd_split",
                            {"element": "A89_PATCH", "cutter": "A89_ON",
                             "name": "A89_ON_SPLIT"})
        check(3, "a circle lying on the patch still splits",
              "keeping the side" in reply,
              reply.splitlines()[0][:150])
    except Exception as error:  # noqa: BLE001
        check(3, "a circle lying on the patch still splits", False,
              f"{getattr(error, 'code', '')}: {str(error).splitlines()[0][:180]}")

    # ── 4 the reporter's case: a circle that only crosses the patch ──────
    say("")
    say("--- 4. circle on a 45-degree support plane: touches, does not lie ---")
    try:
        reply = gsd.execute("catia_gsd_split",
                            {"element": "A89_PATCH", "cutter": "A89_TILTED",
                             "name": "A89_TILT_SPLIT"})
        check(4, "a cutter that only crosses is reported as SPLIT_FAILED", False,
              f"the split was ACCEPTED: {reply.splitlines()[0][:150]}")
    except Exception as error:  # noqa: BLE001
        code = getattr(error, "code", "")
        text = str(error)
        wanted = ("0.0000 mm", "will not pretend to know which", "LIE ON",
                  "JOIN or array result", "cap_center=true", "OWN border shape")
        missing = [probe for probe in wanted if probe not in text]
        check(4, "a cutter that only crosses is reported as SPLIT_FAILED",
              code == "SPLIT_FAILED" and not missing,
              f"code={code}, missing={missing or 'none'}, "
              f"message: {text.splitlines()[0][:120]}")

    say("")
    passed = sum(1 for _n, _l, ok, _d in RESULTS if ok)
    say(f"{passed}/{len(RESULTS)} checks passed")

    # ── cleanup: this script's documents and its own probe file only ─────
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
        name = os.path.basename(full).lower()
        if os.path.normcase(os.path.abspath(full)).startswith(
                os.path.normcase(os.path.abspath(ROOT))) or \
                name.startswith("verify_0389"):
            try:
                doc.Close()
            except Exception:  # noqa: BLE001
                pass
    for leftover in (target,):
        try:
            if os.path.isfile(leftover):
                os.remove(leftover)
        except OSError:  # noqa: BLE001
            pass
    say("still open: " + ", ".join(str(conn.documents.Item(i).Name)
                                  for i in range(1, conn.documents.Count + 1)))


if __name__ == "__main__":
    main()
