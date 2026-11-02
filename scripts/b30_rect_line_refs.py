"""Do LINE references give rect_pattern an unambiguous direction? Raw COM.

The plane references are not a usable direction: measured today, the same plane
gave Y in one pair and Z in another, because CATIA derives something from the
pair rather than from each plane's normal. A line is not ambiguous - its
direction is p1 -> p2 - and circ_pattern already builds its rotation axis that
way, so this probe asks the same question for the two rect directions:

    line X then line Y, 3 copies at 20 mm  -> which axis moved, which way?
    line Y then line X                     -> same question, other order
    line along Z                           -> does Z work at all?

If the answer is "the first line's axis, positive", the fix is to stop using
planes for direction and build two lines, exactly as the circular pattern does.
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


def moved(measurement, name) -> str:
    payload = json.loads(measurement.execute(
        "catia_get_bounding_box", {"document": name, "method": "vertices"}))
    low, high = payload.get("min") or {}, payload.get("max") or {}
    spans = {"x": high["x"] - low["x"], "y": high["y"] - low["y"],
             "z": high["z"] - low["z"]}
    axis = max(spans, key=lambda k: spans[k])
    if spans[axis] < 14.0:
        return "none"
    positive = {"x": high["x"] > 20, "y": high["y"] > 20, "z": high["z"] > 20}[axis]
    return ("+" if positive else "-") + axis.upper()


def direction_line(part, tools, axis: str, length=80.0):
    """A line through the origin along +axis, in a geometrical set of its own."""
    hsf = part.HybridShapeFactory
    geoset = tools._pattern_geoset(part)
    d = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[axis]
    p1 = hsf.AddNewPointCoord(0.0, 0.0, 0.0)
    p2 = hsf.AddNewPointCoord(length * d[0], length * d[1], length * d[2])
    geoset.AppendHybridShape(p1)
    geoset.AppendHybridShape(p2)
    line = hsf.AddNewLinePtPt(part.CreateReferenceFromObject(p1),
                              part.CreateReferenceFromObject(p2))
    geoset.AppendHybridShape(line)
    part.Update()
    return part.CreateReferenceFromObject(line)


def main() -> int:
    conn = CATIAConnection()
    say(conn.connect())
    document, part_design = DocumentTools(conn), PartDesignTools(conn)
    sketcher, measurement = SketcherTools(conn), MeasurementTools(conn)

    cases = [("line X + line Y", "x", "y"),
             ("line Y + line X", "y", "x"),
             ("line X + line Z", "x", "z"),
             ("line Z + line X", "z", "x")]
    for index, (label, first, second) in enumerate(cases, start=1):
        name = f"LINE_{index}"
        try:
            fresh(conn, document, sketcher, part_design, name)
            part = conn.get_active_part()
            body = part.MainBody
            part.InWorkObject = body
            r1 = direction_line(part, part_design, first)
            r2 = direction_line(part, part_design, second)
            seed = body.Shapes.Item(body.Shapes.Count)
            try:
                pattern = part.ShapeFactory.AddNewRectPattern(
                    seed, 3, 1, 20.0, 0.0, 1, 1, r1, r2, False, False, 0.0)
                part.UpdateObject(pattern)
                say(f"{label}: built, dir1 -> {moved(measurement, name + '.CATPart')}"
                    f"   (asked {first.upper()}+)")
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
