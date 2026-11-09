"""0.3.87 acceptance: the three things left open by the 2026-11-09 reports.

  1  catia_gsd_fill MEASURES what came back. An open boundary used to produce a
     silent degenerate face while the Edge.N route was refused for not being
     closed - two routes, two answers, no number. Now the area is published and a
     degenerate result is taken back out (FILL_DEGENERATE).
  2  a sketch on a datum plane built by catia_gsd_plane_angle can be turned back
     into global XYZ, and the reconstructed frame is CHECKED against a measured
     distance before any coordinate is handed over.
  3  catia_sketch_copy says where the copy landed in global XYZ (the reporter had
     to derive 2*r*sin(A/2) by hand to prove the landing).

Everything lives in this script's own folder; the only documents it closes are
ones whose FullName is under ROOT.
"""
from __future__ import annotations

import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0387\audit"
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

    own = os.path.normcase(os.path.abspath(ROOT))
    for index in range(conn.documents.Count, 0, -1):
        try:
            name = str(conn.documents.Item(index).FullName)
        except Exception:  # noqa: BLE001
            continue
        if os.path.normcase(os.path.abspath(name)).startswith(own):
            conn.documents.Item(index).Close()

    path = os.path.join(ROOT, "fill87.CATPart")
    if os.path.isfile(path):
        os.remove(path)
    document.execute("catia_new_part", {"name": "AUDIT87", "file_path": path})
    part = conn.get_active_part()

    # ── 1 an OPEN boundary must not become a silent face ────────────────
    say("--- 1. a fill on an open boundary ---")
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [25, 0, 0],
                                   "name": "OPEN_EDGE"})
    part.Update()
    try:
        reply = gsd.execute("catia_gsd_fill", {"boundaries": ["OPEN_EDGE"],
                                               "name": "BAD_FILL"})
        check(1, "a fill on one open 25 mm line is refused, not reported done",
              False, f"it said: {reply.splitlines()[0][:150]}")
    except Exception as error:  # noqa: BLE001
        text = str(error)
        code = getattr(error, "code", "")
        survived = None
        try:
            survived = part.FindObjectByName("BAD_FILL")
        except Exception:  # noqa: BLE001
            survived = "?"
        # Two refusals are both correct: CATIA itself may refuse the update
        # (FILL_NOT_CLOSED) or accept it and hand back a face with no area
        # (FILL_DEGENERATE). What must never happen again is "Fill surface
        # created" for an open boundary, or a dead face left in the tree.
        check(1, "a fill on one open 25 mm line is refused, not reported done",
              code in ("FILL_NOT_CLOSED", "FILL_DEGENERATE")
              and survived in (None, ""),
              f"code={code}, degenerate face left in the tree={survived!r}, "
              f"message: {text.splitlines()[0][:150]}")

    # ── 2 a CLOSED boundary still builds, and its area is published ─────
    say("")
    say("--- 2. a fill on a closed loop ---")
    for index, (a, b) in enumerate((([60, 0, 0], [70, 0, 0]),
                                    ([70, 0, 0], [70, 10, 0]),
                                    ([70, 10, 0], [60, 10, 0]),
                                    ([60, 10, 0], [60, 0, 0]))):
        gsd.execute("catia_gsd_line", {"point1": a, "point2": b,
                                       "name": f"SIDE{index}"})
    part.Update()
    try:
        reply = gsd.execute("catia_gsd_fill",
                            {"boundaries": ["SIDE0", "SIDE1", "SIDE2", "SIDE3"],
                             "name": "GOOD_FILL"})
        measured = None
        for token in reply.replace(",", " ").split():
            if token.replace(".", "").isdigit() and float(token) > 1:
                measured = float(token)
        check(2, "a closed 10 x 10 loop fills and its area is measured",
              measured is not None and abs(measured - 100.0) < 1.0,
              f"area read from the reply: {measured} mm2 (expected 100.0)")
    except Exception as error:  # noqa: BLE001
        check(2, "a closed 10 x 10 loop fills and its area is measured", False,
              str(error).splitlines()[0][:180])

    # ── 3 a sketch on a 72-degree plane gives global XYZ, checked ───────
    say("")
    say("--- 3. a sketch on a plane built by catia_gsd_plane_angle ---")
    gsd.execute("catia_gsd_plane_angle",
                {"plane": "zx", "axis": "z", "angle": 72, "name": "P72"})
    sketcher.execute("catia_create_sketch", {"plane": "P72"})
    # 6 mm along the sketch's V axis, because V is the direction the rotation
    # moves: on zx, H is Z (a rotation about Z leaves it alone) and V is X, so
    # (h=0, v=6) has to land at 6*(cos72, sin72, 0). Measuring along H would
    # "pass" for the unrotated plane too - an assertion nobody can fail proves
    # nothing.
    sketcher.execute("catia_sketch_line",
                     {"x1": 0, "y1": 0, "x2": 0, "y2": 6})
    sketcher.execute("catia_close_sketch", {})
    part.Update()
    try:
        reply = gsd.execute("catia_gsd_spline", {"element": "草图.1"})
        near_zero = "CHECKED" in reply and "0.00" in reply
        # The line's far end has to land at (6cos72, 6sin72, 0).
        want = (6 * math.cos(math.radians(72)), 6 * math.sin(math.radians(72)))
        numbers = [float(token) for token in re.findall(r"-?\d+\.\d+", reply)]
        hits = all(any(abs(value - target) < 0.01 for value in numbers)
                   for target in want)
        check(3, "the outline of a sketch on P72 comes back in global XYZ, "
                 "with the reconstructed frame checked by measurement",
              near_zero and hits,
              f"checked-note={near_zero}, coordinates match 6*cos72/6*sin72={hits}: "
              f"{reply.splitlines()[0][:200]}")
    except Exception as error:  # noqa: BLE001
        check(3, "the outline of a sketch on P72 comes back in global XYZ, "
                 "with the reconstructed frame checked by measurement", False,
              f"{type(error).__name__}: {str(error).splitlines()[0][:200]}")

    # ── 4 the copy onto P72 reports where it landed ─────────────────────
    say("")
    say("--- 4. catia_sketch_copy onto P72 ---")
    try:
        reply = sketcher.execute("catia_sketch_copy",
                                 {"source": "草图.1", "plane": "P72",
                                  "name": "COPY_ON_P72"})
        says_where = "Where it landed" in reply or "Key points in global XYZ" in reply
        check(4, "the copy reply says where the copy landed, in mm",
              says_where,
              reply.splitlines()[0][:240])
    except Exception as error:  # noqa: BLE001
        check(4, "the copy reply says where the copy landed, in mm", False,
              f"{type(error).__name__}: {str(error).splitlines()[0][:200]}")

    # ── 5 the report's own two-route case: boundary curve vs Edge.N ──────
    say("")
    say("--- 5. a border CURVE and an Edge.N must answer alike ---")
    for index, (a, b) in enumerate((([0, 40, 0], [20, 40, 0]),
                                    ([20, 40, 0], [20, 60, 0]),
                                    ([20, 60, 0], [0, 60, 0]),
                                    ([0, 60, 0], [0, 40, 0]))):
        gsd.execute("catia_gsd_line", {"point1": a, "point2": b,
                                       "name": f"T{index}"})
    gsd.execute("catia_gsd_fill",
                {"boundaries": ["T0", "T1", "T2", "T3"], "name": "TRAP"})
    part.Update()
    try:
        boundary = gsd.execute("catia_gsd_boundary",
                               {"surface": "TRAP", "edge": 1, "name": "ONE_SIDE"})
        say(f"    boundary reply: {boundary.splitlines()[0][:160]}")
    except Exception as error:  # noqa: BLE001
        say(f"    boundary refused: {str(error).splitlines()[0][:160]}")
    curve_answer = ""
    try:
        curve_answer = gsd.execute("catia_gsd_fill",
                                   {"boundaries": ["TRAP"], "name": "ON_BORDER"})
    except Exception as error:  # noqa: BLE001
        curve_answer = f"REFUSED {getattr(error, 'code', '')}: {error}"
    edge_answer = ""
    try:
        edge_answer = gsd.execute("catia_gsd_fill",
                                  {"boundaries": ["Edge.1"], "name": "ON_EDGE"})
    except Exception as error:  # noqa: BLE001
        edge_answer = f"REFUSED {getattr(error, 'code', '')}: {error}"
    # Neither route is allowed to answer "created" without an area.
    silent = [name for name, answer in (("curve", curve_answer),
                                        ("Edge.N", edge_answer))
              if "created" in answer.lower() and "area" not in answer.lower()]
    check(5, "neither route reports a fill 'created' without saying its area",
          not silent,
          f"silent route(s): {silent or 'none'}; curve: "
          f"{curve_answer.splitlines()[0][:90]!r}; Edge.N: "
          f"{edge_answer.splitlines()[0][:90]!r}")

    say("")
    passed = sum(1 for _n, _l, ok, _d in RESULTS if ok)
    say(f"{passed}/{len(RESULTS)} checks passed")
    try:
        document.execute("catia_close_document", {"save": False})
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
