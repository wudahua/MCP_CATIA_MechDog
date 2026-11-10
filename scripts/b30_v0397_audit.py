"""0.3.97 acceptance: the five leftovers from the two 2026-11-10 reports.

Everything here is a reply that used to say something the measurement in front of
it did not support:

  1  `catia_sketch_arc(90 -> 0)` describes the arc it actually drew (clockwise) -
     the schema now says so too, including DEGENERATE_ARC
  2  `catia_gsd_offset_surface` reports WHERE the offset went, as a measured
     centroid displacement - and refuses to call it inside/outside, because a
     surface alone cannot answer that
  3  the SAME sketch projects cleanly onto `yz`
  4  ...and fails onto `xy` with `PROJECT_FAILED`, a measured distance and the
     parallel-plane rule - not the bare `方法 UpdateObject 失败` of 0.3.94
  5  `catia_diagnose` publishes `capability_ceilings`, so "declared" and "can be
     updated" stop being the same word

Order matters: 1 and 2 run BEFORE the deliberate failure in 4, because once an
update fails in a document, new elements there refuse to update too (measured
2026-11-09) - putting the failure last is what makes the rest trustworthy.

Everything happens in this script's own folder; only documents whose FullName is
under it are closed, and only files this script created are removed.
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.results import CodedError, FeatureRejected  # noqa: E402
from catia_mcp.tools.diagnostics import DiagnosticsTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0397\audit"
RESULTS: list[tuple[int, str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, label: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, label, passed, detail))
    say(f"[{'OK ' if passed else 'FAIL'}] {number}. {label}"
        + (f"  |  {detail}" if detail else ""))


def close_ours(conn: CATIAConnection) -> None:
    """Close documents whose FILE is in this script's folder, and delete them."""
    for i in range(int(conn.app.Documents.Count), 0, -1):
        document = conn.app.Documents.Item(i)
        try:
            full = str(document.FullName or "")
        except Exception:  # noqa: BLE001
            full = ""
        if full and os.path.abspath(full).startswith(os.path.abspath(ROOT)):
            document.Close()
            say(f"closed {os.path.basename(full)}")
    if os.path.isdir(ROOT):
        for name in os.listdir(ROOT):
            if name.lower().endswith(".catpart"):
                try:
                    os.remove(os.path.join(ROOT, name))
                    say(f"removed {name}")
                except OSError:
                    pass


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    target = os.path.join(ROOT, "probe_0397.CATPart")

    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    pd = PartDesignTools(conn)
    gsd = GSDTools(conn)
    dx = DiagnosticsTools(conn)

    say("0.3.97 acceptance - messages that match their own measurement")
    say("=" * 78)

    docs.execute("catia_new_part", {"file_path": target})
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": -50, "y1": -50,
                                                "x2": 50, "y2": 50})
    sketcher.execute("catia_close_sketch", {})
    pd.execute("catia_pad", {"height": 10})
    block = pd._body_volume_mm3(conn.get_active_part(), conn.get_active_part_body())
    check(0, "block built", block is not None and abs(block - 100000) < 5,
          f"{block:.1f} mm3")

    # ── 1: the arc says which way it went ───────────────────────────────
    sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 10})
    try:
        reply = sketcher.execute("catia_sketch_arc",
                                 {"cx": 0, "cy": 0, "radius": 5,
                                  "start_angle": 90, "end_angle": 0})
        check(1, "90 -> 0 is described as clockwise",
              "from 90° clockwise to 0°" in reply,
              reply.split("Arc created")[-1][:90] if "Arc created" in reply
              else reply[:90])
    except (CodedError, FeatureRejected) as error:
        check(1, "90 -> 0 is described as clockwise", False,
              f"{getattr(error, 'code', None)}: {str(error).splitlines()[0][:110]}")
        sketcher.execute("catia_close_sketch", {})

    # ── 2: what the offset can and cannot say ───────────────────────────
    try:
        listed = json.loads(pd.execute("catia_list_faces", {}))
        biggest = max(listed["faces"], key=lambda f: f.get("area_mm2") or 0)
        gsd.execute("catia_gsd_extract",
                    {"element": biggest["name"], "name": "Skin"})
        reply = gsd.execute("catia_gsd_offset_surface",
                            {"surface": "Skin", "offset": 0.4,
                             "name": "Skin_Out"})
        gap = re.search(r"measure (\d+\.\d{4}) mm apart", reply)
        check(2, "the offset reports the measured gap",
              gap is not None and abs(float(gap.group(1)) - 0.4) < 0.02,
              f"{gap.group(1) if gap else 'no number'} mm on {biggest['name']}")
        check(3, "the offset says the side is not readable, and why",
              "cannot be read from here" in reply
              and "resolves to the enclosing SOLID" in reply,
              "names the measurement that failed")
        check(4, "the offset does NOT invent a side or a centroid",
              "inside" not in reply.lower() and "outside" not in reply.lower()
              and "catia_boolean(remove)" in reply,
              "points at the volume check instead")
    except (CodedError, FeatureRejected) as error:
        for number in (2, 3, 4):
            check(number, "offset note", False,
                  f"{getattr(error, 'code', None)}: {str(error).splitlines()[0][:110]}")

    # ── 5: a support the element actually crosses works ─────────────────
    sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 40})
    sketcher.execute("catia_sketch_rectangle", {"x1": -20, "y1": -20,
                                                "x2": 20, "y2": 20})
    sketcher.execute("catia_close_sketch", {})
    parallel_sketch = str(conn.last_sketch.Name)
    tilted_ok = False
    try:
        gsd.execute("catia_gsd_plane_angle", {"plane": "xy", "axis": "x",
                                              "angle": 45, "name": "Tilted45"})
        reply = gsd.execute("catia_gsd_project",
                            {"element": parallel_sketch, "support": "Tilted45",
                             "name": "On_Tilted"})
        tilted_ok = "created" in reply
        check(5, "the same sketch projects onto a plane skewed 45 deg",
              tilted_ok, reply.split(". ")[0][:100])
    except (CodedError, FeatureRejected) as error:
        check(5, "the same sketch projects onto a plane skewed 45 deg", False,
              f"{getattr(error, 'code', None)}: {str(error).splitlines()[0][:110]}")

    # ── 6: onto a PARALLEL plane it names the pair, with a number ───────
    try:
        reply = gsd.execute("catia_gsd_project",
                            {"element": parallel_sketch, "support": "xy",
                             "name": "On_XY"})
        check(6, "a parallel pair is refused as PROJECT_FAILED", False,
              f"it was accepted instead: {reply[:100]}")
        check(7, "...and the message carries the measured distance", False,
              "no refusal to read")
    except (CodedError, FeatureRejected) as error:
        message = str(error)
        number = re.search(r"(\d+\.\d{4}) mm", message)
        lowered = message.lower()
        check(6, "a parallel pair is refused as PROJECT_FAILED",
              getattr(error, "code", None) == "PROJECT_FAILED",
              f"{getattr(error, 'code', None)} | {message.splitlines()[0][:88]}")
        check(7, "...and the message states BOTH degenerate cases, with a number",
              number is not None and "parallel" in lowered
              and "perpendicular" in lowered,
              f"{number.group(0) if number else 'no number'} | "
              f"{'both cases' if 'perpendicular' in lowered else 'one case only'}")

    # ── 8: diagnose separates declared from updating ────────────────────
    try:
        report = json.loads(dx.execute("catia_diagnose", {}))
        ceilings = report.get("capability_ceilings") or []
        statuses = {entry["member"]: entry["status"] for entry in ceilings}
        check(8, "diagnose publishes capability_ceilings with measured statuses",
              len(ceilings) >= 5
              and statuses.get("AddNewSewSurface") == "UNSUPPORTED_ON_B30"
              and statuses.get("AddNewWrapCurve") == "UNSUPPORTED_ON_B30",
              f"{len(ceilings)} entries | {sorted(set(statuses.values()))}")
        check(9, "diagnose still reports the version",
              (report.get("mcp") or {}).get("version") == "0.3.97",
              f"{report.get('mcp', {}).get('version')} / "
              f"{report.get('mcp', {}).get('tool_count')} tools")
    except (CodedError, FeatureRejected) as error:
        check(8, "diagnose publishes capability_ceilings", False,
              f"{getattr(error, 'code', None)}: {str(error).splitlines()[0][:110]}")
        check(9, "diagnose still reports the version and tool count", False, "skipped")

    say("")
    passed = sum(1 for _, _, ok, _ in RESULTS if ok)
    say(f"{passed}/{len(RESULTS)} checks passed")
    failed = [f"{n}. {label}" for n, label, ok, _ in RESULTS if not ok]
    if failed:
        say("FAILED: " + "; ".join(failed))

    try:
        close_ours(conn)
    except Exception as error:  # noqa: BLE001
        say(f"cleanup: {error}")


if __name__ == "__main__":
    main()
