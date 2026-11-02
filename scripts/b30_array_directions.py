"""Pin down the two array defects the 2026-10-31 report only described.

Round 1 (scripts/b30_open_questions.py) already showed:
  * rect_pattern 3x2 at 20x15 gave x span 19 and y span 44 - the counts AND the
    spacings landed on the opposite axis from the one the reply named, and both
    copies went to the negative side;
  * circ_pattern, +90 deg about Z, put the copy at -Y (clockwise);
  * P12_Fan rebuilds (Update OK).

This round isolates WHY, one variable at a time, on a fresh 4x4x10 pad each time:

    A  default directions, one row along dir1
    B  default directions, one row along dir2
    C  explicit dir1_axis=x / dir2_axis=y, one row along dir1
    D  explicit dir1_axis=x / dir2_axis=y, one row along dir2

A one-row pattern is the only shape that can answer this: a 2x2 grid with equal
spacing is symmetric under every swap, which is why the original "field-proven
2x2 boss grid" proved nothing about roles or signs.

Then circ_pattern at +90 and -90, and the user_pattern volume question on a part
whose sketch names come back in Chinese (the earlier run looked for "Sketch.N"
and this machine says "草图.N").
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

made: list[str] = []


def say(line: str) -> None:
    print(line, flush=True)


def activate(conn, name: str):
    for index in range(1, conn.documents.Count + 1):
        candidate = conn.documents.Item(index)
        if str(candidate.Name) == name:
            candidate.Activate()
            if str(conn.active_document.Name) != name:
                raise RuntimeError(f"could not activate {name}")
            return candidate
    raise RuntimeError(f"{name} is not open")


def volume(measurement) -> float:
    payload = json.loads(measurement.execute("catia_get_inertia", {}))
    return float(payload.get("volume_mm3") or 0.0)


def extent(measurement, doc_name: str) -> dict:
    payload = json.loads(measurement.execute(
        "catia_get_bounding_box", {"document": doc_name, "method": "vertices"}))
    return payload.get("min") or {}, payload.get("max") or {}


def fresh_pad(conn, document, sketcher, part_design, name: str, rect, height=10.0):
    path = os.path.join(tempfile.gettempdir(), f"{name}.CATPart")
    document.execute("catia_new_part", {"name": name, "file_path": path})
    made.append(f"{name}.CATPart")
    activate(conn, f"{name}.CATPart")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": rect[0], "y1": rect[1], "x2": rect[2], "y2": rect[3]})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": height})


def where(low: dict, high: dict) -> str:
    return (f"x[{low['x']}, {high['x']}] y[{low['y']}, {high['y']}]")


def classify(low: dict, high: dict, step: float) -> str:
    """Which axis grew, and on which side - the fact the reply should have named."""
    dx = high["x"] - low["x"]
    dy = high["y"] - low["y"]
    if dx > dy:                                   # dir moved along X
        return "+X" if high["x"] > 4.0 + step * 0.5 else "-X"
    return "+Y" if high["y"] > 4.0 + step * 0.5 else "-Y"


def main() -> int:
    conn = CATIAConnection()
    say(conn.connect())
    document = DocumentTools(conn)
    part_design = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    measurement = MeasurementTools(conn)

    say("")
    say("── rect_pattern: which axis and which side, one row at a time ──")
    cases = [
        ("A default dir1", {"dir1_count": 3, "dir1_spacing": 20.0}, "dir1"),
        ("B default dir2", {"dir1_count": 1, "dir1_spacing": 20.0,
                            "dir2_count": 3, "dir2_spacing": 20.0}, "dir2"),
        ("C axis  dir1  ", {"dir1_axis": "x", "dir2_axis": "y",
                            "dir1_count": 3, "dir1_spacing": 20.0}, "dir1(x)"),
        ("D axis  dir2  ", {"dir1_axis": "x", "dir2_axis": "y",
                            "dir1_count": 1, "dir1_spacing": 20.0,
                            "dir2_count": 3, "dir2_spacing": 20.0}, "dir2(y)"),
    ]
    index = 0
    for label, arguments, which in cases:
        index += 1
        name = f"RECT_{index}"
        try:
            fresh_pad(conn, document, sketcher, part_design, name, (0.0, 0.0, 4.0, 4.0))
            reply = part_design.execute("catia_rect_pattern", arguments)
            low, high = extent(measurement, f"{name}.CATPart")
            moved = classify(low, high, 20.0)
            say(f"{label}: asked {which} 3x20 mm  -> moved {moved}  {where(low, high)}")
            if "swapped" in reply:
                say("             reply says the axes were swapped internally")
        except Exception as error:  # noqa: BLE001
            say(f"{label}: FAILED {str(error).splitlines()[0][:140]}")

    say("")
    say("── circ_pattern: +90 vs -90 about Z, seed at (30, 0) ──")
    for index, spacing in enumerate((90.0, -90.0), start=1):
        name = f"CIRC_{index}"
        try:
            fresh_pad(conn, document, sketcher, part_design, name, (28.0, -2.0, 32.0, 2.0))
            part_design.execute("catia_circ_pattern", {
                "axis": "z", "center": [0.0, 0.0, 0.0], "count": 2,
                "angular_spacing": spacing})
            low, high = extent(measurement, f"{name}.CATPart")
            side = "+Y" if high["y"] > 20 else ("-Y" if low["y"] < -20 else "?")
            say(f"spacing {spacing:+.0f} deg -> copy at {side}  {where(low, high)}")
        except Exception as error:  # noqa: BLE001
            say(f"spacing {spacing:+.0f}: FAILED {str(error).splitlines()[0][:140]}")

    say("")
    say("── user_pattern: 100x60x10 plate, one 1000 mm3 pocket, 3 points ──")
    try:
        name = "USER_1"
        fresh_pad(conn, document, sketcher, part_design, name,
                  (-50.0, -30.0, 50.0, 30.0), height=10.0)
        plate = volume(measurement)
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": -35.0, "y1": -5.0, "x2": -25.0, "y2": 5.0})
        sketcher.execute("catia_close_sketch", {})
        part_design.execute("catia_pocket", {"through_all": True})
        cut = volume(measurement)
        per_copy = round(plate - cut, 1)
        say(f"plate {plate} mm3, one pocket removes {per_copy} mm3")

        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        for x in (-10.0, 10.0, 30.0):
            sketcher.execute("catia_sketch_point", {"x": x, "y": 0.0})
        sketcher.execute("catia_close_sketch", {})
        listing = str(part_design.execute("catia_list_features", {}))
        import re
        names = re.findall(r'"([^"]*?(?:Sketch|草图)\.\d+)"', listing)
        if not names:
            names = re.findall(r"((?:Sketch|草图)\.\d+)", listing)
        sketch_name = max(names, key=lambda n: int(n.split(".")[-1])) if names else None
        say(f"points sketch: {sketch_name}  (candidates: {sorted(set(names))[:6]})")
        reply = part_design.execute("catia_user_pattern",
                                    {"count": 3, "sketch_name": sketch_name})
        say(f"reply: {reply.splitlines()[0][:160]}")
        after = volume(measurement)
        removed = round(plate - after, 1)
        say(f"after: {after} mm3 -> removed {removed} = {round(removed / per_copy, 3)} "
            f"x one pocket (3 total -> 3000, seed+3 -> 4000, 6 -> 6000)")
    except Exception as error:  # noqa: BLE001
        say(f"user_pattern INCONCLUSIVE: {str(error).splitlines()[0][:200]}")

    say("")
    closed = []
    for name in reversed(made):
        for index in range(1, conn.documents.Count + 1):
            candidate = conn.documents.Item(index)
            if str(candidate.Name) == name:
                try:
                    candidate.Activate()
                    document.execute("catia_close_document", {"save": False})
                    closed.append(name)
                except Exception as error:  # noqa: BLE001
                    say(f"cleanup: {name}: {str(error).splitlines()[0][:90]}")
                break
    say(f"closed {len(closed)}/{len(made)} created document(s)")
    say("open now: " + (", ".join(str(conn.documents.Item(i).Name)
                                 for i in range(1, conn.documents.Count + 1)) or "none"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
