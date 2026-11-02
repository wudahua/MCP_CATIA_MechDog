"""Two open questions, settled by measurement on CATIA B30.

1. P12_Fan: can the part rebuild at all, now that it is only the file on disk?
   The 0.3.41 incident left this unanswered - my probe had added three R5 fillets
   in memory (never saved), and the part's Update() failed afterwards. The file's
   mtime is later than that session, so this cannot prove whether MY run dirtied
   it; it can prove whether the part rebuilds today. Opened, updated, closed
   without saving - the file is not written.

2. The three array tools from the 2026-10-31 report, marked "to be reproduced":
     #5 rect_pattern: instance positions / spacing semantics
     #6 user_pattern: volume removed ~ 2x the expected
     #7 circ_pattern: rotation direction, and no way to ask for the other one

   Each is measured on a part this script builds, against arithmetic that can be
   checked by hand - not against "looks right".

Every geometry step asserts that the document this script created is the active
one (a probe run on 2026-10-30 put three fillets into a finished user part by
skipping that check). Every document created here is closed at the end.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

P12 = r"F:\WokrBuddy Catia Space\文本建模-风力仿生兽机构\CATIA_Out\P12_Fan.CATPart"
findings: list[str] = []
made: list[str] = []


def say(line: str) -> None:
    print(line, flush=True)
    findings.append(line)


def activate(conn, name: str):
    for index in range(1, conn.documents.Count + 1):
        candidate = conn.documents.Item(index)
        if str(candidate.Name) == name:
            candidate.Activate()
            active = str(conn.active_document.Name)
            if active != name:
                raise RuntimeError(f"could not activate {name} (active: {active})")
            return candidate
    raise RuntimeError(f"{name} is not open")


def volume(measurement) -> float:
    """The active part's volume, from the same route the tool publishes."""
    payload = json.loads(measurement.execute("catia_get_inertia", {}))
    for key in ("volume_mm3",):
        if key in payload:
            return float(payload[key])
    data = payload.get("data") or {}
    return float(data.get("volume_mm3") or 0.0)


def newest_sketch(part_design) -> str:
    """The highest-numbered sketch in the tree - the one just drawn."""
    import re

    listing = part_design.execute("catia_list_features", {})
    names = re.findall(r"Sketch\.(\d+)", str(listing))
    if not names:
        raise RuntimeError("no sketch name found in catia_list_features")
    return f"Sketch.{max(int(n) for n in names)}"


def box(measurement, doc_name: str) -> dict:
    payload = json.loads(measurement.execute(
        "catia_get_bounding_box", {"document": doc_name, "method": "vertices"}))
    return {"min": payload.get("min") or {}, "max": payload.get("max") or {},
            "dims": payload.get("dimensions") or {},
            "volume": payload.get("volume_mm3")}


def main() -> int:
    conn = CATIAConnection()
    say(conn.connect())
    opened_before = [str(conn.documents.Item(i).Name)
                     for i in range(1, conn.documents.Count + 1)]
    say(f"open before: {', '.join(opened_before) or 'none'}")

    document = DocumentTools(conn)
    part_design = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    measurement = MeasurementTools(conn)
    workdir = tempfile.mkdtemp(prefix="catia_arrays_")

    # ────────────────────────────────────────────────────────────────────
    # 1. P12_Fan: does it rebuild?
    # ────────────────────────────────────────────────────────────────────
    say("")
    say("── 1. P12_Fan, opened from disk and updated (nothing saved) ──")
    p12_doc = None
    try:
        if not os.path.exists(P12):
            raise RuntimeError(f"missing: {P12}")
        p12_doc = conn.documents.Open(P12)
        p12_doc.Activate()
        active = str(conn.active_document.Name)
        say(f"opened {p12_doc.Name} -> active: {active}")
        if active != str(p12_doc.Name):
            raise RuntimeError(f"opened but NOT active (active: {active}); refusing")
        part = conn.get_active_part()
        body = part.MainBody
        shapes = body.Shapes
        names = []
        for index in range(1, min(shapes.Count, 40) + 1):
            names.append(str(shapes.Item(index).Name))
        say(f"features in {body.Name}: {shapes.Count} -> {', '.join(names[:12])}"
            + (" ..." if shapes.Count > 12 else ""))
        try:
            part.Update()
            say("Update(): OK - the part rebuilds")
        except Exception as error:  # noqa: BLE001 - the failure IS the answer
            say(f"Update(): FAILED - {str(error).splitlines()[0][:200]}")
    except Exception as error:  # noqa: BLE001
        say(f"P12 INCONCLUSIVE: {str(error).splitlines()[0][:200]}")
    finally:
        if p12_doc is not None:
            try:
                p12_doc.Saved = True      # discard the dirty marker, write nothing
                p12_doc.Close()
                say("closed P12 without saving")
            except Exception as error:  # noqa: BLE001
                say(f"could not close P12: {str(error).splitlines()[0][:120]}")

    # ────────────────────────────────────────────────────────────────────
    # 2. rect_pattern: where do the copies go?
    # ────────────────────────────────────────────────────────────────────
    say("")
    say("── 2. rect_pattern: a 4x4x10 pad, 3x2 at 20 x 15 mm ──")
    try:
        path = os.path.join(workdir, "ARR_RECT.CATPart")
        document.execute("catia_new_part", {"name": "ARR_RECT", "file_path": path})
        made.append("ARR_RECT.CATPart")
        activate(conn, "ARR_RECT.CATPart")
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0.0, "y1": 0.0, "x2": 4.0, "y2": 4.0})
        sketcher.execute("catia_close_sketch", {})
        part_design.execute("catia_pad", {"height": 10})
        before = box(measurement, "ARR_RECT.CATPart")
        say(f"seed box: x[{before['min']['x']}, {before['max']['x']}] "
            f"y[{before['min']['y']}, {before['max']['y']}]")
        reply = part_design.execute("catia_rect_pattern", {
            "dir1_count": 3, "dir1_spacing": 20.0,
            "dir2_count": 2, "dir2_spacing": 15.0})
        say(f"reply: {reply.splitlines()[0][:150]}")
        after = box(measurement, "ARR_RECT.CATPart")
        spanx = round(after["max"]["x"] - after["min"]["x"], 3)
        spany = round(after["max"]["y"] - after["min"]["y"], 3)
        say(f"after   : x[{after['min']['x']}, {after['max']['x']}] "
            f"y[{after['min']['y']}, {after['max']['y']}]")
        say(f"spans   : x {spanx} (3 copies at 20 mm -> 44 expected), "
            f"y {spany} (2 copies at 15 mm -> 19 expected)")
        ok = abs(spanx - 44.0) < 0.01 and abs(spany - 19.0) < 0.01
        say("VERDICT : " + ("spacing and placement match the arithmetic"
                            if ok else "DOES NOT MATCH the arithmetic (report #5)"))
    except Exception as error:  # noqa: BLE001
        say(f"rect_pattern INCONCLUSIVE: {str(error).splitlines()[0][:180]}")

    # ────────────────────────────────────────────────────────────────────
    # 3. circ_pattern: which way does it turn?
    # ────────────────────────────────────────────────────────────────────
    say("")
    say("── 3. circ_pattern: a 4x4x10 pad at (30, 0), 2 copies at 90 deg about Z ──")
    try:
        path = os.path.join(workdir, "ARR_CIRC.CATPart")
        document.execute("catia_new_part", {"name": "ARR_CIRC", "file_path": path})
        made.append("ARR_CIRC.CATPart")
        activate(conn, "ARR_CIRC.CATPart")
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 28.0, "y1": -2.0, "x2": 32.0, "y2": 2.0})
        sketcher.execute("catia_close_sketch", {})
        part_design.execute("catia_pad", {"height": 10})
        reply = part_design.execute("catia_circ_pattern", {
            "axis": "z", "center": [0.0, 0.0, 0.0], "count": 2,
            "angular_spacing": 90.0})
        say(f"reply: {reply.splitlines()[0][:150]}")
        after = box(measurement, "ARR_CIRC.CATPart")
        low_y, high_y = after["min"]["y"], after["max"]["y"]
        say(f"after   : x[{after['min']['x']}, {after['max']['x']}] "
            f"y[{low_y}, {high_y}]")
        # Seed occupies y -2..2 at x 28..32. A second copy 90 deg away lands on
        # +Y (counter-clockwise) or -Y (clockwise) - the box says which.
        if high_y > 20 and low_y > -3:
            say("VERDICT : the copy landed on +Y -> counter-clockwise (as expected)")
        elif low_y < -20 and high_y < 3:
            say("VERDICT : the copy landed on -Y -> CLOCKWISE, i.e. the other way "
                "(report #7); the tool exposes no 'reverse' argument, only this "
                "documented default")
        else:
            say(f"VERDICT : neither +Y nor -Y clearly - y span {low_y}..{high_y}")
    except Exception as error:  # noqa: BLE001
        say(f"circ_pattern INCONCLUSIVE: {str(error).splitlines()[0][:180]}")

    # ────────────────────────────────────────────────────────────────────
    # 4. user_pattern: how much material does it actually remove?
    # ────────────────────────────────────────────────────────────────────
    say("")
    say("── 4. user_pattern: 100x60x10 plate, one 10x10 through pocket, 3 points ──")
    try:
        path = os.path.join(workdir, "ARR_USER.CATPart")
        document.execute("catia_new_part", {"name": "ARR_USER", "file_path": path})
        made.append("ARR_USER.CATPart")
        activate(conn, "ARR_USER.CATPart")
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": -50.0, "y1": -30.0, "x2": 50.0, "y2": 30.0})
        sketcher.execute("catia_close_sketch", {})
        part_design.execute("catia_pad", {"height": 10})
        plate = volume(measurement)
        say(f"plate volume: {plate} mm3 (100x60x10 = 60000)")

        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": -35.0, "y1": -5.0, "x2": -25.0, "y2": 5.0})
        sketcher.execute("catia_close_sketch", {})
        part_design.execute("catia_pocket", {"depth": 10, "through_all": True})
        cut = volume(measurement)
        per_copy = round(plate - cut, 1)
        say(f"after one pocket: {cut} mm3 -> the pocket is {per_copy} mm3 "
            "(10x10 through 10)")

        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        for x in (-10.0, 10.0, 30.0):
            sketcher.execute("catia_sketch_point", {"x": x, "y": 0.0})
        sketcher.execute("catia_close_sketch", {})
        sketch_name = newest_sketch(part_design)
        say(f"points sketch: {sketch_name}")

        reply = part_design.execute("catia_user_pattern", {
            "count": 3, "sketch_name": sketch_name})
        say(f"reply: {reply.splitlines()[0][:160]}")
        patterned = volume(measurement)
        removed = round(plate - patterned, 1)
        implied = round(removed / per_copy, 3) if per_copy else float("nan")
        say(f"after user_pattern: {patterned} mm3 -> removed {removed} mm3 "
            f"= {implied} x one pocket")
        say("           count=3 with 3 points: 3 instances total -> 3000, "
            "4 instances (seed + 3) -> 4000, 6 -> 6000")
    except Exception as error:  # noqa: BLE001
        say(f"user_pattern INCONCLUSIVE: {str(error).splitlines()[0][:180]}")

    # ────────────────────────────────────────────────────────────────────
    say("")
    closed = []
    for name in reversed(made):
        try:
            doc = activate(conn, name)
            doc.Saved = True
            doc.Close()
            closed.append(name)
        except Exception as error:  # noqa: BLE001
            say(f"cleanup: could not close {name} ({str(error)[:90]})")
    remaining = [str(conn.documents.Item(i).Name)
                 for i in range(1, conn.documents.Count + 1)]
    say(f"closed {len(closed)}/{len(made)} created document(s); "
        f"open now: {', '.join(remaining) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
