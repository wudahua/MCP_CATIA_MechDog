"""Round 3: pin the rect_pattern parameter mapping, and finish user_pattern.

Round 2 established, one row at a time on a 4x4x10 pad at the origin:

    A default dir1  (3 copies, 20 mm) -> moved -Y
    B default dir2  (3 copies, 20 mm) -> moved -X
    C dir1_axis=x   -> moved -Y        (same as A: the axis form changes nothing)
    D dir2_axis=y   -> moved -X        (same as B)

So the reference passed as the FIRST direction reference governs the SECOND
count/spacing, and both directions run negative on this build. Two candidate
causes, and this round separates them:

    E  first axis y, second z, 3 copies on dir1   -> -Z means "roles swapped"
    F  first axis y, second z, 3 copies on dir2   -> -Y confirms the pair
    G  reverse_dir1=true                          -> +Y means "the sign is a flag"
    H  reverse_dir2=true                          -> +X likewise

Then user_pattern, whose sketch name this time comes from body.Sketches rather
than from catia_list_features (the earlier regex looked for "Sketch.N" and this
CATIA says "草图.N").
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
    return float(json.loads(measurement.execute("catia_get_inertia", {}))
                 .get("volume_mm3") or 0.0)


def extent(measurement, doc_name: str):
    payload = json.loads(measurement.execute(
        "catia_get_bounding_box", {"document": doc_name, "method": "vertices"}))
    return payload.get("min") or {}, payload.get("max") or {}


def fresh_pad(conn, document, sketcher, part_design, name, rect, height=10.0):
    path = os.path.join(tempfile.gettempdir(), f"{name}.CATPart")
    document.execute("catia_new_part", {"name": name, "file_path": path})
    made.append(f"{name}.CATPart")
    activate(conn, f"{name}.CATPart")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": rect[0], "y1": rect[1], "x2": rect[2], "y2": rect[3]})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": height})


def direction(low, high) -> str:
    """Which way the copies went, read off the box of a 4 mm seed."""
    dx = high["x"] - low["x"]
    dy = high["y"] - low["y"]
    dz = high["z"] - low["z"]
    if dx > max(dy, dz) * 1.5:
        return "+X" if high["x"] > 20 else "-X"
    if dy > max(dx, dz) * 1.5:
        return "+Y" if high["y"] > 20 else "-Y"
    if dz > max(dx, dy) * 1.5:
        return "+Z" if high["z"] > 20 else "-Z"
    return "?"


def main() -> int:
    conn = CATIAConnection()
    say(conn.connect())
    document, part_design = DocumentTools(conn), PartDesignTools(conn)
    sketcher, measurement = SketcherTools(conn), MeasurementTools(conn)

    say("")
    say("── rect_pattern: separating 'roles swapped' from 'sign reversed' ──")
    cases = [
        ("E y,z dir1 3x20      ", {"dir1_axis": "y", "dir2_axis": "z",
                                   "dir1_count": 3, "dir1_spacing": 20.0}),
        ("F y,z dir2 3x20      ", {"dir1_axis": "y", "dir2_axis": "z",
                                   "dir1_count": 1, "dir1_spacing": 20.0,
                                   "dir2_count": 3, "dir2_spacing": 20.0}),
        ("G x,y dir1 3x20 rev1 ", {"dir1_axis": "x", "dir2_axis": "y",
                                   "dir1_count": 3, "dir1_spacing": 20.0,
                                   "reverse_dir1": True}),
        ("H x,y dir2 3x20 rev2 ", {"dir1_axis": "x", "dir2_axis": "y",
                                   "dir1_count": 1, "dir1_spacing": 20.0,
                                   "dir2_count": 3, "dir2_spacing": 20.0,
                                   "reverse_dir2": True}),
        ("I x,y dir1 3x20 rev1+2", {"dir1_axis": "x", "dir2_axis": "y",
                                    "dir1_count": 3, "dir1_spacing": 20.0,
                                    "reverse_dir1": True, "reverse_dir2": True}),
    ]
    for index, (label, arguments) in enumerate(cases, start=1):
        name = f"R3_{index}"
        try:
            fresh_pad(conn, document, sketcher, part_design, name,
                      (0.0, 0.0, 4.0, 4.0))
            part_design.execute("catia_rect_pattern", arguments)
            low, high = extent(measurement, f"{name}.CATPart")
            say(f"{label} -> {direction(low, high):3}  "
                f"x[{low['x']}, {high['x']}] y[{low['y']}, {high['y']}] "
                f"z[{low['z']}, {high['z']}]")
        except Exception as error:  # noqa: BLE001
            say(f"{label} -> FAILED {str(error).splitlines()[0][:120]}")

    say("")
    say("── user_pattern: 100x60x10 plate, one 1000 mm3 pocket, 3 points ──")
    try:
        name = "U3"
        fresh_pad(conn, document, sketcher, part_design, name,
                  (-50.0, -30.0, 50.0, 30.0))
        plate = volume(measurement)
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": -35.0, "y1": -5.0, "x2": -25.0, "y2": 5.0})
        sketcher.execute("catia_close_sketch", {})
        part_design.execute("catia_pocket", {"through_all": True})
        per_copy = round(plate - volume(measurement), 1)
        say(f"plate {plate} mm3, one pocket removes {per_copy} mm3")

        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        for x in (-10.0, 10.0, 30.0):
            sketcher.execute("catia_sketch_point", {"x": x, "y": 0.0})
        sketcher.execute("catia_close_sketch", {})

        part = conn.get_active_part()
        sketches = part.MainBody.Sketches
        names = [str(sketches.Item(i).Name) for i in range(1, sketches.Count + 1)]
        say(f"sketches in the body: {names}")
        sketch_name = names[-1]

        reply = part_design.execute("catia_user_pattern",
                                    {"count": 3, "sketch_name": sketch_name})
        say(f"reply: {reply.splitlines()[0][:170]}")
        after = volume(measurement)
        removed = round(plate - after, 1)
        say(f"after {after} mm3 -> removed {removed} mm3 "
            f"= {round(removed / per_copy, 3)} x one pocket")
        say("           3 total -> 3000 | seed + 3 points -> 4000 | "
            "3 points counted twice -> 6000")
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
    say(f"closed {len(closed)}/{len(made)}; open now: "
        + (", ".join(str(conn.documents.Item(i).Name)
                     for i in range(1, conn.documents.Count + 1)) or "none"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
