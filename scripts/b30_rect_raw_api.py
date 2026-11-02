"""What does AddNewRectPattern actually do with its arguments? Raw COM, no tool.

The tool's own call is now suspect: swapping the two direction references made
every pattern fail at update, which says CATIA cares about the ORDER of the pair
(independently of which axes they represent). So this probe calls the factory
directly and reads the resulting bounding box, one combination per fresh part:

    order   which reference goes first
    rev1    the flag in the first reverse slot
    rev2    the flag in the second reverse slot

Six combinations, each on a 4x4x10 pad at the origin, 3 copies at 20 mm in the
first slot only. Every number printed here is the answer to "which axis moved,
and which way", read from the box - it is the only way to stop guessing about a
12-argument COM call whose documentation this project has never had.
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


def activate(conn, name):
    for i in range(1, conn.documents.Count + 1):
        candidate = conn.documents.Item(i)
        if str(candidate.Name) == name:
            candidate.Activate()
            if str(conn.active_document.Name) != name:
                raise RuntimeError(f"could not activate {name}")
            return candidate
    raise RuntimeError(f"{name} is not open")


def fresh(conn, document, sketcher, part_design, name):
    path = os.path.join(tempfile.gettempdir(), f"{name}.CATPart")
    document.execute("catia_new_part", {"name": name, "file_path": path})
    made.append(f"{name}.CATPart")
    activate(conn, f"{name}.CATPart")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 4.0, "y2": 4.0})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})


def bbox(measurement, name):
    payload = json.loads(measurement.execute(
        "catia_get_bounding_box", {"document": name, "method": "vertices"}))
    return payload.get("min") or {}, payload.get("max") or {}


def moved(low, high) -> str:
    spans = {"x": high["x"] - low["x"], "y": high["y"] - low["y"],
             "z": high["z"] - low["z"]}
    axis = max(spans, key=lambda k: spans[k])
    if spans[axis] < 14.0:
        return "none"
    positive = {"x": high["x"] > 20, "y": high["y"] > 20, "z": high["z"] > 20}[axis]
    return ("+" if positive else "-") + axis.upper()


def main() -> int:
    conn = CATIAConnection()
    say(conn.connect())
    document, part_design = DocumentTools(conn), PartDesignTools(conn)
    sketcher, measurement = SketcherTools(conn), MeasurementTools(conn)

    combinations = [
        ("ref1,ref2  rev F,F", "first", False, False),
        ("ref2,ref1  rev F,F", "swapped", False, False),
        ("ref1,ref2  rev T,F", "first", True, False),
        ("ref1,ref2  rev F,T", "first", False, True),
        ("ref1,ref2  rev T,T", "first", True, True),
        ("ref2,ref1  rev T,F", "swapped", True, False),
        ("ref2,ref1  rev F,T", "swapped", False, True),
    ]

    for index, (label, order, rev1, rev2) in enumerate(combinations, start=1):
        name = f"RAW_{index}"
        try:
            fresh(conn, document, sketcher, part_design, name)
            part = conn.get_active_part()
            body = part.MainBody
            if index == 1:
                say("origin planes: " + ", ".join(sorted(conn.get_origin_elements())))
            planes = conn.get_origin_elements()
            part.InWorkObject = body
            yz = part.CreateReferenceFromObject(planes["yz"])
            zx = part.CreateReferenceFromObject(planes["zx"])
            refs = (yz, zx) if order == "first" else (zx, yz)
            seed = body.Shapes.Item(body.Shapes.Count)
            try:
                pattern = part.ShapeFactory.AddNewRectPattern(
                    seed, 3, 1, 20.0, 0.0, 1, 1, refs[0], refs[1], rev1, rev2, 0.0)
                part.UpdateObject(pattern)
                low, high = bbox(measurement, f"{name}.CATPart")
                say(f"{label}: built, dir1 -> {moved(low, high):3}  "
                    f"x[{low['x']}, {high['x']}] y[{low['y']}, {high['y']}]")
            except Exception as error:  # noqa: BLE001
                say(f"{label}: REJECTED ({str(error).splitlines()[0][:70]})")
        except Exception as error:  # noqa: BLE001
            say(f"{label}: setup failed ({str(error).splitlines()[0][:90]})")

    say("")
    closed = []
    for name in reversed(made):
        for i in range(1, conn.documents.Count + 1):
            candidate = conn.documents.Item(i)
            if str(candidate.Name) == name:
                try:
                    candidate.Activate()
                    document.execute("catia_close_document", {"save": False})
                    closed.append(name)
                except Exception as error:  # noqa: BLE001
                    say(f"cleanup: {name}: {str(error).splitlines()[0][:80]}")
                break
    say(f"closed {len(closed)}/{len(made)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
