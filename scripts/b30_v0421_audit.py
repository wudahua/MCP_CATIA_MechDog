"""0.3.119 acceptance on B30: does the deviation tool measure what it claims?

The probe (b30_v0421_probe_deviation.py) established the primitive: a point on a
50 mm ramp, updated, measured 0.750000 mm at t=0.25 against 0.750000 expected.
This script checks the shipped tool end to end, one fresh document per cell.

    1  a 0-3 mm ramp, 5 samples      max 3.0000 mm at t=1.000
    2  the same curve's own minimum  0.0000 mm - the number that cannot answer
    3  tolerance                     missed at 1 mm, met at 4 mm
    4  a spline with a bump          max >= 1.4 mm, on the control point
    5  the sample points are gone    element count back to where it started
    6  keep_points=true              they are still there, by name

    .venv/Scripts/python.exe scripts/b30_v0421_audit.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0421")
ROOT = os.path.join(_PROBE_ROOT, "audit")
RESULTS: list[tuple[int, str, bool, str]] = []


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


def block(conn, documents, sketcher, part_design, name: str) -> None:
    path = os.path.join(ROOT, f"{name}.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    for index in range(1, conn.documents.Count + 1):
        candidate = conn.documents.Item(index)
        if candidate.Name == f"{name}.CATPart":
            candidate.Activate()
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 60, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})


def element_names(gsd) -> list[str]:
    """Every wireframe/surface element name, across all geometrical sets.

    The reply is {"geometrical_sets": [{"name":..., "elements": [{"name":...}]}]},
    so counting it by substring (or expecting a top-level "elements") reads an
    empty list and would call a working tool broken.
    """
    try:
        payload = gsd.execute("catia_gsd_list_elements", {})
        if not isinstance(payload, dict):
            payload = json.loads(payload)
        names: list[str] = []
        for group in payload.get("geometrical_sets") or []:
            for item in group.get("elements") or []:
                names.append(str(item.get("name") if isinstance(item, dict)
                                 else item))
        return names
    except Exception as error:  # noqa: BLE001
        say("  (element list unreadable: " + str(error).splitlines()[0][:80] + ")")
        return []


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    gsd = GSDTools(conn)

    # ── 1 + 2: the ramp ─────────────────────────────────────────────────
    say("")
    say("── 1: a 50 mm line from the face up to 3 mm above it ──")
    block(conn, documents, sketcher, part_design, "DevA")
    gsd.execute("catia_gsd_line",
                {"point1": [5, 20, 10], "point2": [55, 20, 13], "name": "Ramp"})
    before = element_names(gsd)
    reply = gsd.execute("catia_measure_deviation",
                        {"curve": "Ramp", "samples": 5})
    text = str(reply)
    say(text[:700])
    check(1, "five samples, max 3.0000 mm at t=1.000",
          "t=1.000  3.0000 mm" in text and "MAXIMUM, 3.0000 mm at t=1.000" in text,
          "the ramp rises exactly 3 mm")

    check(2, "the reply contrasts it with the curve's own minimum (0.0000)",
          "GetMinimumDistance to the same target is 0.0000 mm" in text
          and "that is a MINIMUM" in text,
          "a minimum is 0 for any curve that touches once")

    # ── 3: tolerance, both ways ─────────────────────────────────────────
    say("")
    say("── 3: the same curve judged at 1 mm and at 4 mm ──")
    missed = str(gsd.execute("catia_measure_deviation",
                             {"curve": "Ramp", "samples": 5, "tolerance": 1.0}))
    met = str(gsd.execute("catia_measure_deviation",
                          {"curve": "Ramp", "samples": 5, "tolerance": 4.0}))
    say("  at 1 mm: " + [line for line in missed.splitlines()
                        if "WARNING" in line][0][:180] if "WARNING" in missed
        else "  at 1 mm: NO WARNING")
    say("  at 4 mm: " + [line for line in met.splitlines()
                        if "Within" in line][0][:160] if "Within" in met
        else "  at 4 mm: no 'Within'")
    check(3, "1 mm is missed and 4 mm is met",
          "WARNING" in missed and "exceed the 1 mm" in missed
          and "Within the 4 mm" in met and "WARNING" not in met,
          "worst sample is 3.0000 mm")

    # ── 5: the scaffolding is gone again ────────────────────────────────
    after = element_names(gsd)
    check(5, "the sample points were removed again",
          len(after) == len(before)
          and not any(name.startswith("Ramp_t") for name in after),
          f"{len(before)} -> {len(after)} elements, "
          f"{sum(1 for n in after if n.startswith('Ramp_t'))} Ramp_t* left")

    # ── 6: keep_points leaves them, by name ─────────────────────────────
    say("")
    say("── 6: keep_points=true ──")
    kept = str(gsd.execute("catia_measure_deviation",
                           {"curve": "Ramp", "samples": 3,
                            "keep_points": True}))
    flat = element_names(gsd)
    kept_points = [name for name in flat if name.startswith("Ramp_t")]
    say(f"  kept: {kept_points}")
    check(6, "three named points stay in the tree",
          "were KEPT" in kept and len(kept_points) == 3, str(kept_points))

    # ── 4: a spline with a known bump ───────────────────────────────────
    say("")
    say("── 4: a spline through z = 10, 10.8, 10.2, 11.4 ──")
    block(conn, documents, sketcher, part_design, "DevE")
    gsd.execute("catia_gsd_spline",
                {"points": [[5, 20, 10], [20, 20, 10.8], [35, 20, 10.2],
                            [50, 20, 11.4]], "name": "Bump"})
    profile = str(gsd.execute("catia_measure_deviation",
                              {"curve": "Bump", "samples": 9}))
    say(profile[:700])
    worst = 0.0
    for line in profile.splitlines():
        if line.strip().startswith("t="):
            try:
                worst = max(worst, float(line.split()[-2]))
            except (ValueError, IndexError):
                continue
    check(4, "the spline's own bump is found (max >= 1.4 mm at the last point)",
          worst >= 1.4 - 1e-6 and "t=1.000  1.4000 mm" in profile,
          f"max {worst:.4f} mm")

    say("")
    failed = [row for row in RESULTS if not row[2]]
    say(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    for number, title, _, detail in failed:
        say(f"  FAILED {number}: {title} ({detail})")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
