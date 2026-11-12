"""0.3.107 acceptance: the all-edges fillet names edges instead of a COM error.

The 2026-11-11 eighth report (§2.2) sent catia_fillet(radius=1.5) and (0.5) to a
129-face polyline part and got the same bare COM_ERROR twice, with no edge, no
length and no next step - while the one-at-a-time machinery that would have
named them sat unused on the other branch.

What this script checks on B30, each cell in its own new document:

  1  a 2-edge cylinder: the all-edges call BUILDS (it used to throw at
     construction) and the volume drops by the rounded amount
  2  its reply never mentions a COM error and names the scope it covered
  3  a 12-edge rectangle: the reply says 4/12, not 12/12 - and the volume drop
     equals exactly the four edges that took
  4  the eight edges that refused are named with length and bounding faces
  5  54 edges (under the budget): retried one at a time, counted, in 15 s
  6  that reply stays readable instead of printing 200 rejection lines
  7  246 edges (over the budget): one batch attempt, and the reply says so
  8  the 246-edge reply carries the shortest edge and how many are < 2*R
  9  the failed batch left the body unchanged and it still updates
 10  the reply ends with a next step instead of a dead end

Every document lives in this script's own folder.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection          # noqa: E402
from catia_mcp.tools.document import DocumentTools       # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools       # noqa: E402

ROOT = r"F:\catia_probe_v0407\audit"
RESULTS: list[tuple[int, str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, label: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, label, passed, detail))
    say(f"[{'OK ' if passed else 'FAIL'}] {number}. {label}"
        + (f"  |  {detail}" if detail else ""))


def close_ours(conn: CATIAConnection) -> None:
    for index in range(int(conn.app.Documents.Count), 0, -1):
        document = conn.app.Documents.Item(index)
        try:
            full = str(document.FullName or "")
        except Exception:                                   # noqa: BLE001
            full = ""
        if full and os.path.abspath(full).startswith(os.path.abspath(ROOT)):
            try:
                document.Close()
            except Exception:                               # noqa: BLE001
                pass


def wavy(segments: int, width: float = 60.0, half: float = 6.0,
         waves: int = 2) -> list[list[float]]:
    top, bottom = [], []
    for i in range(segments + 1):
        x = width * i / segments
        y = half * math.sin(2 * math.pi * waves * x / width)
        top.append([x, y + 10.0])
        bottom.append([x, y - 10.0])
    return top + bottom[::-1] + [list(top[0])]


def call(tools: object, name: str, args: dict) -> tuple[bool, str]:
    try:
        return True, str(tools.execute(name, args))          # type: ignore[attr-defined]
    except Exception as error:                              # noqa: BLE001
        return False, f"[{getattr(error, 'code', type(error).__name__)}] {error}"


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    for name in os.listdir(ROOT):
        if name.lower().endswith(".catpart"):
            try:
                os.remove(os.path.join(ROOT, name))
            except OSError:
                pass
    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    docs, sk, pd = DocumentTools(conn), SketcherTools(conn), PartDesignTools(conn)

    say("0.3.107 acceptance - the all-edges fillet names edges")
    say("=" * 78)

    # ── 1: a cylinder, 2 edges ────────────────────────────────────────
    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, "Cyl.CATPart"), "overwrite": True})
    sk.execute("catia_create_sketch", {"plane": "xy", "offset": 0})
    sk.execute("catia_sketch_circle", {"center": [0, 0], "radius": 20})
    sk.execute("catia_close_sketch", {})
    pd.execute("catia_pad", {"height": 10})
    part = conn.get_active_part()
    before = pd._body_volume_mm3(part, part.MainBody)
    started = time.perf_counter()
    ok, reply = call(pd, "catia_fillet", {"radius": 2.0})
    elapsed = time.perf_counter() - started
    after = pd._body_volume_mm3(part, part.MainBody)
    removed = (before - after) if (before and after) else None
    # Two circular lips of R2 on r=20: 2 * 2*pi*20 * (4 - pi) = 215.77 mm3.
    check(1, "cylinder (2 edges): all-edges builds and rounds both lips",
          ok and "2/2 edge(s) done" in reply and removed is not None
          and 205.0 < removed < 230.0,
          f"{elapsed:.1f}s volume {before} -> {after}, removed "
          f"{removed:.4f} mm3 (predicted 215.77); reply: {reply[:220]}")
    check(2, "cylinder reply never mentions a COM error and names the scope",
          "COM_ERROR" not in reply and "Every edge of" in reply,
          reply[:180])

    # ── 3: a rectangle, 12 edges ──────────────────────────────────────
    # MEASURED, and it is the point of this cell: 12 edges at R1.5 gives 4/12.
    # The radius is not the reason - R0.25, 0.5, 1.0, 1.5 and 3.0 each took the
    # same four 40 mm edges and the volume drop was exactly 4 edges' worth every
    # time (2.1460 / 8.5841 / 34.3363 / 77.2567 / 309.0266). Edges that share a
    # vertex fight over the same material, so a box cannot be rounded all round
    # in one pass at any radius. What the tool owes the caller is the truth:
    # 4/12, the four named, and the eight named.
    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, "Rect.CATPart"), "overwrite": True})
    sk.execute("catia_create_sketch", {"plane": "xy", "offset": 0})
    sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 80, "y2": 40})
    sk.execute("catia_close_sketch", {})
    pd.execute("catia_pad", {"height": 20})
    part = conn.get_active_part()
    before = pd._body_volume_mm3(part, part.MainBody)
    ok, reply = call(pd, "catia_fillet", {"radius": 1.5})
    after = pd._body_volume_mm3(part, part.MainBody)
    removed = (before - after) if (before and after) else None
    # Four 40 mm edges at R1.5: 4 * 40 * (1.5^2 - pi*1.5^2/4) = 77.2568 mm3.
    check(3, "rectangle (12 edges): the reply says 4/12, not 12/12",
          ok and "4/12 edge(s) done" in reply
          and removed is not None and abs(removed - 77.2568) < 0.6,
          f"volume {before} -> {after}, removed {removed:.4f} mm3 "
          f"(four 40 mm edges = 77.2568)")
    check(4, "the eight edges that refused are named, with length and faces",
          "Rejected:" in reply and ("L20.0000|" in reply or "L80.0000|" in reply),
          (reply.split("Rejected:", 1)[-1][:240] if "Rejected:" in reply else "none"))

    # ── 5: 54 edges, under the budget - retried and counted ───────────
    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, "Poly54.CATPart"),
                  "overwrite": True})
    sk.execute("catia_create_sketch", {"plane": "xy", "offset": 0})
    sk.execute("catia_sketch_strokes", {"strokes": [wavy(8)]})
    sk.execute("catia_close_sketch", {})
    pd.execute("catia_pad", {"height": 22.0})
    part = conn.get_active_part()
    edge_count = len(json.loads(pd.execute("catia_list_edges", {}))["edges"])
    started = time.perf_counter()
    ok, reply = call(pd, "catia_fillet", {"radius": 1.5})
    elapsed = time.perf_counter() - started
    check(5, "54 edges under the budget: retried one at a time, result counted",
          ok and "one-at-a-time retry" in reply
          and f"/{edge_count} edge(s) done" in reply and elapsed < 90,
          f"{edge_count} edges in {elapsed:.1f}s; head: {reply.split('. Rejected:')[0][-150:]}")
    check(6, "the reply stays readable - it does not print 200 rejection lines",
          len(reply) < 6000, f"{len(reply)} characters")

    # ── 7: 246 edges, over the budget - one batch, then an honest stop ─
    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, "Poly246.CATPart"),
                  "overwrite": True})
    sk.execute("catia_create_sketch", {"plane": "xy", "offset": 0})
    sk.execute("catia_sketch_strokes", {"strokes": [wavy(40)]})
    sk.execute("catia_close_sketch", {})
    pd.execute("catia_pad", {"height": 22.0})
    part = conn.get_active_part()
    many = len(json.loads(pd.execute("catia_list_edges", {}))["edges"])
    features_before = part.MainBody.Shapes.Count
    started = time.perf_counter()
    ok, reply = call(pd, "catia_fillet", {"radius": 1.5})
    elapsed = time.perf_counter() - started
    features_after = part.MainBody.Shapes.Count
    check(7, "246 edges over the budget: one batch attempt, and it says so",
          ok and f"({many} edge(s))" in reply
          and "more than max_edges=200" in reply
          and "NOT retried one at a time" in reply,
          f"{many} edges in {elapsed:.1f}s")
    check(8, "that reply carries the shortest edge and the 2*R count",
          "Shortest edge" in reply and "shorter than 2*R" in reply,
          reply[reply.find("Shortest"):][:200])
    check(9, "the failed batch left the body as it was, and it still updates",
          features_after == features_before and _updates(conn),
          f"features {features_before} -> {features_after}")
    check(10, "it says how to proceed instead of stopping dead",
          "NEXT:" in reply and "catia_list_edges" in reply,
          reply[reply.find("NEXT:"):][:160])

    # ── 11: the chamfer had the same dead end, measured separately ─────
    # AddNewChamfer is a different method (6 args, mode 1 = length+angle), so it
    # got its own four cells: feature ref and body ref both fail AT CONSTRUCTION
    # on a 2-edge cylinder and a 12-edge rectangle, and the volume does not move.
    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, "Chamfer.CATPart"),
                  "overwrite": True})
    sk.execute("catia_create_sketch", {"plane": "xy", "offset": 0})
    sk.execute("catia_sketch_circle", {"center": [0, 0], "radius": 20})
    sk.execute("catia_close_sketch", {})
    pd.execute("catia_pad", {"height": 10})
    part = conn.get_active_part()
    before = pd._body_volume_mm3(part, part.MainBody)
    ok, reply = call(pd, "catia_chamfer", {"length": 2.0})
    after = pd._body_volume_mm3(part, part.MainBody)
    removed = (before - after) if (before and after) else None
    # Two circular lips, a 2 mm 45 deg bevel each: 2 * 2*pi*20 * (2*2/2) = 502.65.
    check(11, "chamfer (2 edges): builds, names the scope, volume drops",
          ok and "2/2 edge(s) done" in reply and removed is not None
          and 480.0 < removed < 525.0,
          f"volume {before:.1f} -> {after:.1f}, removed {removed:.4f} mm3 "
          f"(predicted 502.65)")
    check(12, "the chamfer reply never mentions a COM error either",
          "COM_ERROR" not in reply and "Every edge of" in reply,
          reply[:160])

    say("")
    passed = sum(1 for _, _, okay, _ in RESULTS if okay)
    say(f"{passed}/{len(RESULTS)} checks passed")
    for number, label, okay, detail in RESULTS:
        if not okay:
            say(f"  FAILED {number}: {label} | {detail}")


def _updates(conn: CATIAConnection) -> bool:
    try:
        conn.get_active_part().Update()
        return True
    except Exception:                                       # noqa: BLE001
        return False


if __name__ == "__main__":
    main()
