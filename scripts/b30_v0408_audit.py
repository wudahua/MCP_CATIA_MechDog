"""0.3.108 acceptance: a mixed profile in one call.

The 2026-11-11 eighth report's P0 was "a profile cannot be straight on one side
and curved on the other". Drawing each element with its own primitive works -
measured on B30 2026-11-12 before the tool existed - so catia_sketch_profile
does exactly that in one call and reports the junctions.

Cells, one new document each:
  1  a 60x40 stadium as two lines + two half circles: ONE call, close, pad, and
     the volume has to be 36566.37 mm3 = 60*40*10 + pi*20^2*10
  2  the same with a 0.06 mm gap: the reply names it before the pad, with both
     coordinates, and the pad is then reported honestly
  3  a bad third segment leaves NOTHING drawn - the sketch is empty afterwards
     and still usable
  4  the sketch's own outline echo agrees with what the reply claimed
"""
from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection          # noqa: E402
from catia_mcp.tools.document import DocumentTools       # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools       # noqa: E402

ROOT = r"F:\catia_probe_v0408\audit"
EXPECTED_MM3 = (60 * 40 + math.pi * 20 * 20) * 10
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


def stadium(gap: float = 0.0) -> list[dict]:
    return [
        {"type": "line", "p1": [0, 20], "p2": [60, 20]},
        {"type": "arc", "center": [60, 0], "radius": 20,
         "start_angle": 90, "end_angle": -90, "direction": "cw"},
        {"type": "line", "p1": [60 + gap, -20], "p2": [0, -20]},
        {"type": "arc", "center": [0, 0], "radius": 20,
         "start_angle": -90, "end_angle": 90, "direction": "cw"},
    ]


def call(tools: object, name: str, args: dict) -> tuple[bool, str]:
    try:
        return True, str(tools.execute(name, args))          # type: ignore[attr-defined]
    except Exception as error:                              # noqa: BLE001
        return False, f"[{getattr(error, 'code', type(error).__name__)}] {error}"


def element_count(sk: SketcherTools) -> int:
    try:
        payload = json.loads(str(sk.execute("catia_sketch_get_geometry", {})))
        return len(payload.get("elements") or payload.get("geometry") or [])
    except Exception:                                       # noqa: BLE001
        return -1


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

    say("0.3.108 acceptance - a mixed profile in one call")
    say("=" * 78)

    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, "Stadium.CATPart"),
                  "overwrite": True})
    sk.execute("catia_create_sketch", {"plane": "xy", "offset": 0})
    ok, profile = call(sk, "catia_sketch_profile", {"segments": stadium()})
    check(1, "one call draws 2 lines and 2 arcs and says every junction meets",
          ok and "4 segment(s)" in profile and "2 line(s), 2 arc(s)" in profile
          and "meets within 0.0000 mm" in profile,
          profile[:200])
    ok, closed = call(sk, "catia_close_sketch", {})
    check(2, "close_sketch agrees with the claim: 2 arcs, 2 lines",
          ok and "2 arc(s), 2 line(s)" in closed, closed[:220])
    ok, padded = call(pd, "catia_pad", {"height": 10})
    part = conn.get_active_part()
    volume = pd._body_volume_mm3(part, part.MainBody)
    check(3, "the pad is the analytic area: 36566.37 mm3",
          ok and volume is not None and abs(volume - EXPECTED_MM3) < 0.5,
          f"volume {volume} vs {EXPECTED_MM3:.2f}")

    say("")
    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, "Gap.CATPart"),
                  "overwrite": True})
    sk.execute("catia_create_sketch", {"plane": "xy", "offset": 0})
    ok, profile = call(sk, "catia_sketch_profile", {"segments": stadium(0.06)})
    check(4, "a 0.06 mm gap is named before the pad, with both coordinates",
          ok and "NOT CLOSED" in profile and "0.0600 mm" in profile
          and "(60.0000, -20.0000)" in profile and "(60.0600, -20.0000)" in profile,
          profile[:260])
    sk.execute("catia_close_sketch", {})
    ok, padded = call(pd, "catia_pad", {"height": 10})
    volume = pd._body_volume_mm3(conn.get_active_part(), conn.get_active_part().MainBody)
    check(5, "the pad that follows an unclosed profile is not claimed as measured",
          ("UNVERIFIED" in padded) or (ok and volume is None) or not ok,
          f"pad reply: {padded[:160]}")

    say("")
    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, "Refused.CATPart"),
                  "overwrite": True})
    sk.execute("catia_create_sketch", {"plane": "xy", "offset": 0})
    broken = stadium()[:2] + [{"type": "line", "p1": [60, -20]}, stadium()[3]]
    before = element_count(sk)
    ok, refused = call(sk, "catia_sketch_profile", {"segments": broken})
    check(6, "a bad third segment is refused", not ok and "p2" in refused,
          refused[:160])
    # A fresh sketch already holds one element - its absolute axis - so the
    # claim is "nothing was added", not "the sketch is empty".
    after = element_count(sk)
    check(7, "and NOTHING was added to the sketch", after == before,
          f"elements before {before}, after {after} (the absolute axis is one)")
    ok, again = call(sk, "catia_sketch_profile",
                     {"segments": [{"type": "line", "p1": [0, 0], "p2": [10, 0]}]})
    check(8, "the sketch is still usable after the refusal",
          ok and "1 segment(s)" in again, again[:160])

    say("")
    passed = sum(1 for _, _, okay, _ in RESULTS if okay)
    say(f"{passed}/{len(RESULTS)} checks passed")
    for number, label, okay, detail in RESULTS:
        if not okay:
            say(f"  FAILED {number}: {label} | {detail}")


if __name__ == "__main__":
    main()
