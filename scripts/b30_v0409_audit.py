"""0.3.109 acceptance: the outline's self-crossings, named at close time.

The 2026-11-11 eighth report wrote its own intersection test in Python before
trusting a 100-point outline, because a hook that curled into a loop only showed
up as a shape that looked wrong and then as a feature that would not build.

Cells, one new document each:
  1  a figure-eight polyline: close_sketch names the crossing and its point
  2  the same sketch's pad is then refused or reported unverified - the binding
     does not silently pick one lobe
  3  a clean stadium (0.3.108's profile) is reported as not crossing, with the
     segment count that was checked
  4  the structured reply carries the crossings as data, and says whether the
     check ran at all
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection          # noqa: E402
from catia_mcp.tools.document import DocumentTools       # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools       # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0409")
ROOT = os.path.join(_PROBE_ROOT, "audit")
RESULTS: list[tuple[int, str, bool, str]] = []

# Two diagonals of a 10x10 square, drawn as one closed chain: they cross at (5, 5).
FIGURE_EIGHT = [[0, 0], [10, 10], [0, 10], [10, 0], [0, 0]]

STADIUM = [
    {"type": "line", "p1": [0, 20], "p2": [60, 20]},
    {"type": "arc", "center": [60, 0], "radius": 20,
     "start_angle": 90, "end_angle": -90, "direction": "cw"},
    {"type": "line", "p1": [60, -20], "p2": [0, -20]},
    {"type": "arc", "center": [0, 0], "radius": 20,
     "start_angle": -90, "end_angle": 90, "direction": "cw"},
]


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

    say("0.3.109 acceptance - self-crossings named at close time")
    say("=" * 78)

    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, "Cross.CATPart"),
                  "overwrite": True})
    sk.execute("catia_create_sketch", {"plane": "xy", "offset": 0})
    sk.execute("catia_sketch_strokes", {"strokes": [FIGURE_EIGHT]})
    ok, closed = call(sk, "catia_close_sketch", {})
    check(1, "a figure-eight is reported as crossing itself",
          ok and "CROSSES ITSELF" in closed, closed[closed.find("WARNING"):][:220])
    check(2, "and the crossing point is the one the geometry implies",
          "(5.0000, 5.0000)" in closed, "(5.0000, 5.0000) expected")
    check(3, "the sentence says the check came from this session's elements",
          "this session drew" in closed, closed[-150:] if "session" in closed else "missing")

    ok, padded = call(pd, "catia_pad", {"height": 5})
    part = conn.get_active_part()
    volume = pd._body_volume_mm3(part, part.MainBody)
    check(4, "the pad that follows is not claimed as a measured solid",
          (not ok) or "UNVERIFIED" in padded or volume is None,
          f"ok={ok} volume={volume} reply={padded[:120]}")

    say("")
    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, "Clean.CATPart"),
                  "overwrite": True})
    sk.execute("catia_create_sketch", {"plane": "xy", "offset": 0})
    sk.execute("catia_sketch_profile", {"segments": STADIUM})
    ok, closed = call(sk, "catia_close_sketch", {})
    check(5, "a clean stadium is reported as NOT crossing, with the count checked",
          ok and "does not cross itself" in closed and "segment(s) checked" in closed,
          closed[closed.find("does not"):][:160])
    ok, padded = call(pd, "catia_pad", {"height": 10})
    volume = pd._body_volume_mm3(conn.get_active_part(), conn.get_active_part().MainBody)
    check(6, "and it still pads to the analytic volume (36566.37 mm3)",
          ok and volume is not None and abs(volume - 36566.3706) < 0.5,
          f"volume {volume}")

    say("")
    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, "Data.CATPart"),
                  "overwrite": True})
    sk.execute("catia_create_sketch", {"plane": "xy", "offset": 0})
    sk.execute("catia_sketch_strokes", {"strokes": [FIGURE_EIGHT]})
    checked = crossings = None
    ok = True
    try:
        # The structured form comes back as an object, not a string: keeping it
        # is the point of the cell.
        result = sk.execute("catia_close_sketch", {"structured": True})
        data = (result.get("data") if isinstance(result, dict) else {}) or {}
        checked = data.get("self_intersection_checked")
        crossings = data.get("self_intersections")
    except Exception as error:                              # noqa: BLE001
        ok = False
        crossings = f"{type(error).__name__}: {error}"[:120]
    check(7, "the structured reply says the check RAN and carries the crossings",
          ok and checked is True and isinstance(crossings, list) and len(crossings) >= 1,
          f"checked={checked} crossings={crossings}")

    say("")
    passed = sum(1 for _, _, okay, _ in RESULTS if okay)
    say(f"{passed}/{len(RESULTS)} checks passed")
    for number, label, okay, detail in RESULTS:
        if not okay:
            say(f"  FAILED {number}: {label} | {detail}")


if __name__ == "__main__":
    main()
