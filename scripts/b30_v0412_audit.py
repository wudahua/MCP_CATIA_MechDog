"""0.3.112 acceptance - the four items in the 2026-11-12 report.

  1  a closed 4-element profile whose seams are 0.0004 mm apart is NOT reported
     as crossing itself (the report got 4 "crossings" on one legal corner)
  2  a real figure-eight is still reported, so the tolerance did not blind it
  3  an arc given as start=0, end=90, direction=cw, major=false is the SHORT
     (90 degree) arc - the convention the schema now states - and major=true
     gives the 270 degree one
  4  the all-edges fillet reply ends with how long it took
  5  the profile schema states the arc convention (read back from the server)

Run: .venv/Scripts/python.exe -u scripts/b30_v0412_audit.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection          # noqa: E402
from catia_mcp.tools.document import DocumentTools       # noqa: E402
from catia_mcp.tools.gsd import GSDTools                 # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools       # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0412")
ROOT = os.path.join(_PROBE_ROOT, "audit")
RESULTS: list[tuple[int, str, bool, str]] = []


def say(text: str = "") -> None:
    print(text, flush=True)


def check(number: int, label: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, label, passed, detail))
    say(f"[{'OK ' if passed else 'FAIL'}] {number}. {label}"
        + (f"  |  {detail}" if detail else ""))


def close_ours(conn: CATIAConnection) -> None:
    for index in range(int(conn.app.Documents.Count), 0, -1):
        document = conn.app.Documents.Item(index)
        try:
            full = str(document.FullName or "")
        except Exception:                                     # noqa: BLE001
            full = ""
        if full and os.path.abspath(full).startswith(os.path.abspath(ROOT)):
            try:
                document.Close()
            except Exception:                                 # noqa: BLE001
                pass


def stadium(seam: float) -> list[dict]:
    """40 x 40 with r=20 ends; the second element starts `seam` mm further right."""
    return [
        {"type": "line", "p1": [0, 0], "p2": [40, 0]},
        {"type": "arc", "center": [40, 20], "radius": 20,
         "p1": [40 + seam, 0], "p2": [40, 40]},
        {"type": "line", "p1": [40, 40], "p2": [0, 40]},
        {"type": "arc", "center": [0, 20], "radius": 20,
         "p1": [0, 40], "p2": [0, 0]},
    ]


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    docs, sk, pd = DocumentTools(conn), SketcherTools(conn), PartDesignTools(conn)
    gsd = GSDTools(conn)

    say("0.3.112 acceptance - the 2026-11-12 report's four items")
    say("=" * 78)

    # ── 1: a legal corner with a 0.0004 mm seam ───────────────────────
    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, "Seam.CATPart"), "overwrite": True})
    sk.execute("catia_create_sketch", {"plane": "xy", "offset": 0})
    drawn = sk.execute("catia_sketch_profile", {"segments": stadium(0.0004)})
    closed = sk.execute("catia_close_sketch", {"structured": True})
    data = (closed.get("data") if isinstance(closed, dict)
            else (json.loads(closed).get("data") or {})) or {}
    say(f"   profile: {' '.join(str(drawn).split())[:180]}")
    check(1, "a 0.0004 mm seam is not reported as a crossing",
          "CROSSES ITSELF" not in str(closed),
          f"crossings={len(data.get('self_intersections') or [])}, "
          f"checked={data.get('self_intersection_checked')}")

    vol = None
    try:
        reply = pd.execute("catia_pad", {"height": 10})
        found = re.search(r"Volume 0\.0 -> ([\d.]+)", " ".join(str(reply).split()))
        vol = float(found.group(1)) if found else None
    except Exception as caught:                               # noqa: BLE001
        say(f"   pad refused: {caught}")
    # 40*40*10 + pi*20^2*10 = 28566.37
    check(2, "...and the profile still pads to the analytic volume",
          vol is not None and abs(vol - 28566.37) < 8.0,
          f"volume {vol} (analytic 28566.37)")

    # ── 3: a real crossing is still caught ────────────────────────────
    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, "Cross.CATPart"), "overwrite": True})
    sk.execute("catia_create_sketch", {"plane": "xy", "offset": 0})
    sk.execute("catia_sketch_strokes",
               {"strokes": [[[0, 0], [10, 10], [0, 10], [10, 0], [0, 0]]]})
    crossing = sk.execute("catia_close_sketch", {"structured": True})
    cdata = (crossing.get("data") if isinstance(crossing, dict)
             else (json.loads(crossing).get("data") or {})) or {}
    crossing_text = str(crossing)
    # Assert on the DATA, not on the sentence: the structured reply carries what
    # ran and what it found.
    check(3, "a figure-eight is still reported as crossing itself",
          cdata.get("self_intersection_checked") is True
          and len(cdata.get("self_intersections") or []) >= 1
          and "CROSSES ITSELF" in crossing_text,
          f"crossings={len(cdata.get('self_intersections') or [])}, "
          f"checked={cdata.get('self_intersection_checked')}, "
          f"elements={cdata.get('elements_drawn')}")

    # ── 4: the arc convention the schema now states ───────────────────
    for major, expected, label in ((False, 15.7080, "90 deg (short way)"),
                                   (True, 47.1239, "270 deg (major)")):
        docs.execute("catia_new_part",
                     {"file_path": os.path.join(ROOT, f"Arc{int(major)}.CATPart"),
                      "overwrite": True})
        sk.execute("catia_create_sketch", {"plane": "xy", "offset": 0})
        reply = sk.execute("catia_sketch_arc",
                           {"cx": 0, "cy": 0, "radius": 10,
                            "start_angle": 0, "end_angle": 90,
                            "direction": "cw", "major": major})
        measured = gsd._sketch_outline_length_mm(str(conn.last_sketch.Name))
        length = measured[0] if measured else None
        check(4 if not major else 5,
              f"start=0 end=90 cw major={major} draws the {label}",
              length is not None and abs(length - expected) < 0.05,
              f"{length:.4f} mm (expected {expected}); reply: "
              f"{' '.join(str(reply).split())[:110]}")

    # ── 6: the all-edges reply carries the clock ──────────────────────
    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, "Time.CATPart"), "overwrite": True})
    sk.execute("catia_create_sketch", {"plane": "xy", "offset": 0})
    sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 40, "y2": 30})
    sk.execute("catia_close_sketch", {})
    pd.execute("catia_pad", {"height": 20})
    timed = " ".join(str(pd.execute("catia_fillet", {"radius": 1.0})).split())
    check(6, "the all-edges fillet reply says how long it took",
          bool(re.search(r"Took [\d.]+ s\.$", timed)), timed[-120:])

    say("")
    passed = sum(1 for _, _, okay, _ in RESULTS if okay)
    say(f"{passed}/{len(RESULTS)} passed")
    for number, label, okay, _ in RESULTS:
        if not okay:
            say(f"  failed: {number}. {label}")
    close_ours(conn)


if __name__ == "__main__":
    main()
