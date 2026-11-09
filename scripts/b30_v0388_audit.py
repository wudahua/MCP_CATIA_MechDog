"""0.3.88 acceptance: the three follow-ups from the 0.3.87 reports.

  1  catia_new_part into a NON-ASCII folder takes the staged route FIRST (the one
     the reporter measures working) instead of spending six refused attempts on
     the direct SaveAs - and the file really lands in that folder
  2  catia_create_sketch reports the H/V of a rotated datum plane instead of
     "unknown for this support", which contradicted the readback two calls later
  3  a sketch that IS one spline still hands its global coordinates over with the
     refusal - that was the only readback path the tutorial's sections had

Everything lives in this script's own folder, plus one file in the reporter's
own Chinese folder; the only documents it closes are ones whose FullName is under
one of those two.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0388\audit"
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

    own = [os.path.normcase(os.path.abspath(ROOT)),
           os.path.normcase(os.path.abspath(CJK))]
    for index in range(conn.documents.Count, 0, -1):
        try:
            full = str(conn.documents.Item(index).FullName)
        except Exception:  # noqa: BLE001
            continue
        # Only this script's own files: under ROOT, or a 'verify_0388_*' file in
        # the reporter's folder. Their own parts live in the same folder and must
        # not be closed.
        mine = (os.path.normcase(os.path.abspath(full)).startswith(own[0])
                or os.path.basename(full).lower().startswith("verify_0388"))
        if mine:
            print("closing leftover", os.path.basename(full))
            conn.documents.Item(index).Close()

    # ── 1 a Chinese folder: staged route first, file on disk ────────────
    say("--- 1. new_part into the reporter's own Chinese folder ---")
    target = os.path.join(CJK, "verify_0388_cn.CATPart")
    if os.path.isfile(target):
        os.remove(target)
    try:
        reply = document.execute("catia_new_part",
                                 {"name": "AUDIT88_CN", "file_path": target})
        staged = "through an ASCII staging file" in reply
        landed = os.path.isfile(target) and os.path.getsize(target) > 1000
        size = os.path.getsize(target) if os.path.isfile(target) else 0
        check(1, "new_part into a Chinese folder saves through the staged route",
              staged and landed,
              f"staged route announced={staged}, file={size} bytes at the target; "
              f"reply: {reply.splitlines()[0][:180]}")
    except Exception as error:  # noqa: BLE001
        check(1, "new_part into a Chinese folder saves through the staged route",
              False, f"{type(error).__name__}: {str(error).splitlines()[0][:200]}")

    # ── 2 create_sketch reports H/V for a rotated plane ─────────────────
    say("")
    say("--- 2. create_sketch on a rotated plane reports H/V ---")
    path = os.path.join(ROOT, "sketch88.CATPart")
    if os.path.isfile(path):
        os.remove(path)
    document.execute("catia_new_part", {"name": "AUDIT88", "file_path": path})
    part = conn.get_active_part()
    gsd.execute("catia_gsd_plane_angle",
                {"plane": "zx", "axis": "z", "angle": 72, "name": "P72"})
    try:
        reply = sketcher.execute("catia_create_sketch", {"plane": "P72"})
        has_vectors = ("H = (0, 0, 1)" in reply and "0.309017" in reply
                       and "unknown for this support" not in reply)
        check(2, "the sketch says what H and V are on a rotated plane",
              has_vectors, reply.splitlines()[0][:220])
    except Exception as error:  # noqa: BLE001
        check(2, "the sketch says what H and V are on a rotated plane", False,
              f"{type(error).__name__}: {str(error).splitlines()[0][:200]}")
        reply = ""

    # ── 3 a single-spline sketch still gives its coordinates ────────────
    say("")
    say("--- 3. gsd_spline(element=) on a single-spline sketch ---")
    sketcher.execute("catia_sketch_spline",
                     {"points": [[0, 0], [2, 3], [6, 4], [9, 0]]})
    sketcher.execute("catia_close_sketch", {})
    part.Update()
    try:
        answer = gsd.execute("catia_gsd_spline", {"element": "草图.1"})
        check(3, "a one-spline sketch reports its coordinates with the refusal",
              False, f"it built a spline instead: {answer.splitlines()[0][:160]}")
    except Exception as error:  # noqa: BLE001
        text = str(error)
        # The frame is zx rotated 72: (h, v) -> v*(cos72, sin72, 0) + h*Z, so the
        # second control point (2, 3) has to land at 3*(cos72, sin72) + 2*Z.
        wants = ["(0, 0, 0)", "(0.9271, 2.8532, 2)", "Nothing was created"]
        check(3, "a one-spline sketch reports its coordinates with the refusal",
              all(want in text for want in wants) and "CHECKED" in text,
              text.splitlines()[0][:240])

    say("")
    passed = sum(1 for _n, _l, ok, _d in RESULTS if ok)
    say(f"{passed}/{len(RESULTS)} checks passed")
    document.execute("catia_close_document", {"save": False})


if __name__ == "__main__":
    main()
