"""0.3.123 acceptance on B30: the 2026-11-13 report's two P0s and two messages.

Six checks, one fresh document per case.

    1  a batch delete of a mid-plane pocket pair updates ONCE and the part is healthy
    2  its reply carries no "delete those too" and no list of healthy casualties
    3  a delete that really breaks the next update says TERMINAL, not "delete those"
    4  set_view('isometric') reads back normalised and is NOT an ignored Put
    5  a non-unit direction= is not an ignored Put either
    6  a fillet refused on a segmented profile gets the "one closed spline" route

    .venv/Scripts/python.exe scripts/b30_v0424_audit.py
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0424")
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


def fresh(conn, documents, name):
    path = os.path.join(ROOT, f"{name}.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    for index in range(1, conn.documents.Count + 1):
        if conn.documents.Item(index).Name == f"{name}.CATPart":
            conn.documents.Item(index).Activate()
    return conn.get_active_part()


def features(part) -> list[str]:
    shapes = part.MainBody.Shapes
    return [str(shapes.Item(i).Name) for i in range(1, shapes.Count + 1)]


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)
    documents = DocumentTools(conn)
    export = ExportTools(conn)
    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)

    # ── 1-2: the reporter's pair, deleted in one batch ───────────────────
    say("")
    say("── 1-2: a mid-plane pocket pair (凹槽.1 + 凹槽.2) in ONE batch ──")
    part = fresh(conn, documents, "DelPair")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 40, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 5})
    sketcher.execute("catia_sketch_circle", {"cx": 20, "cy": 20, "radius": 5})
    sketcher.execute("catia_close_sketch", {})
    say("  " + part_design.execute(
        "catia_pocket", {"depth": 6, "direction": "both"})[:140])
    names = features(part)
    pair = [name for name in names if name.startswith("凹槽")]
    say("  pair: " + ", ".join(pair))
    reply = " ".join(str(part_design.execute(
        "catia_delete_feature", {"names": pair})).split())
    say("  " + reply[:320])
    volume = part_design._body_volume_mm3(part, part.MainBody)
    healthy = "Part updated successfully" in str(
        measurement.execute("catia_update_part", {}))
    check(1, "one update for the batch and the part is healthy afterwards",
          "ONE update at the end" in reply and healthy
          and volume is not None and abs(volume - 16000.0) < 0.01,
          f"{volume:.1f} mm3" if volume is not None else "unreadable")
    check(2, "no 'delete those too' and no healthy feature blamed",
          "delete those too" not in reply
          and "fails on its own" not in reply
          and "stops there" not in reply,
          "clean reply")

    # ── 3: a delete that really breaks the next update ───────────────────
    say("")
    say("── 3: delete the surface a CloseSurface references ──")
    fresh(conn, documents, "DelBreak")
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 0], "point2": [50, 0, 0], "name": "Spine"})
    gsd.execute("catia_solid_tube",
                {"curve": "Spine", "radius": 5, "name": "Neck"})
    reply = " ".join(str(part_design.execute(
        "catia_delete_feature", {"feature_name": "Neck_T"})).split())
    say("  " + reply[:300])
    check(3, "the failure is called TERMINAL and orders nothing",
          "TERMINAL" in reply and "delete those too" not in reply
          and "DESTRUCTIVE" in reply, "terminal wording")

    # ── 4-5: the view read-back, normalised ──────────────────────────────
    say("")
    say("── 4-5: set_view with a diagonal and a non-unit direction ──")
    fresh(conn, documents, "ViewIso")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 40, "y2": 20})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})
    iso = " ".join(str(export.execute("catia_set_view",
                                      {"view": "isometric"})).split())
    say("  " + iso[:280])
    check(4, "isometric reads back normalised and the camera took it",
          "the camera took them" in iso and "0.00 deg" in iso
          and "[-0.5774, -0.5774, -0.5774]" in iso, "not an ignored Put")
    skew = " ".join(str(export.execute(
        "catia_set_view", {"direction": [0.45, 1.0, -0.18],
                           "up": [0, 0, 1]})).split())
    say("  " + skew[:280])
    check(5, "a non-unit direction is not an ignored Put either",
          "the camera took them" in skew and "0.00 deg" in skew,
          "sight [0.4049, 0.8999, -0.162]")

    # ── 6: the fillet advice on a segmented profile ──────────────────────
    say("")
    say("── 6: fillet a hole rim cut from a 6-segment profile ──")
    part = fresh(conn, documents, "FilletSeg")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 60, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 20})
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    hexagon = []
    for index in range(6):
        a = 2 * math.pi * index / 6
        b = 2 * math.pi * (index + 1) / 6
        hexagon.append({"type": "line",
                        "p1": [round(30 + 10 * math.cos(a), 4),
                               round(20 + 10 * math.sin(a), 4)],
                        "p2": [round(30 + 10 * math.cos(b), 4),
                               round(20 + 10 * math.sin(b), 4)]})
    say("  " + sketcher.execute("catia_sketch_profile",
                               {"segments": hexagon})[:140])
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pocket", {"depth": 5})
    # The hexagon's side is 10 mm and the pad's own edges are 20/40/60, so the
    # rim is the only 10 mm set - no guessing by face name.
    listing = json.loads(part_design.execute(
        "catia_list_edges", {"min_length": 9.5, "max_length": 10.5}))
    rim = [edge["name"] for edge in listing.get("edges", [])]
    say(f"  rim candidates: {rim[:8]} ({len(rim)} total)")
    refusal = ""
    try:
        refusal = " ".join(str(part_design.execute(
            "catia_fillet", {"radius": 1.0, "edge_names": rim,
                             "on_anchor_miss": "skip"})).split())
    except Exception as error:  # noqa: BLE001
        refusal = " ".join(str(error).split())
    say("  " + refusal[:420])
    check(6, "the refusal names the segmented profile and the one-spline route",
          "come from the same profile" in refusal
          and "catia_sketch_spline(closed=true)" in refusal,
          "redraw route")

    say("")
    failed = [row for row in RESULTS if not row[2]]
    say(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    for number, title, _, detail in failed:
        say(f"  FAILED {number}: {title} ({detail})")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
