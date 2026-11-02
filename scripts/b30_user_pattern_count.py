"""user_pattern: does `count` bound the copies, or do the points decide?

Round 3 measured one case: a 1000 mm3 pocket, a sketch with 3 points, count=3 ->
4000 mm3 removed = 4 pockets (the seed plus one per point). That is not the "2x"
the report described, but it IS off by one against "3 instances" as most callers
read it. This round separates the two possible rules:

    count=1 with 3 points -> 2 copies means the POINTS decide (count ignored)
                           -> 4 copies means count is a floor, not a cap
    count=5 with 3 points -> 6 copies means count is honoured past the points
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


def main() -> int:
    conn = CATIAConnection()
    say(conn.connect())
    document, part_design = DocumentTools(conn), PartDesignTools(conn)
    sketcher, measurement = SketcherTools(conn), MeasurementTools(conn)

    for index, count in enumerate((1, 3, 5), start=1):
        name = f"UP_{index}"
        try:
            path = os.path.join(tempfile.gettempdir(), f"{name}.CATPart")
            document.execute("catia_new_part", {"name": name, "file_path": path})
            made.append(f"{name}.CATPart")
            activate(conn, f"{name}.CATPart")
            sketcher.execute("catia_create_sketch", {"plane": "xy"})
            sketcher.execute("catia_sketch_rectangle",
                             {"x1": -50.0, "y1": -30.0, "x2": 50.0, "y2": 30.0})
            sketcher.execute("catia_close_sketch", {})
            part_design.execute("catia_pad", {"height": 10})
            plate = volume(measurement)

            sketcher.execute("catia_create_sketch", {"plane": "xy"})
            sketcher.execute("catia_sketch_rectangle",
                             {"x1": -35.0, "y1": -5.0, "x2": -25.0, "y2": 5.0})
            sketcher.execute("catia_close_sketch", {})
            part_design.execute("catia_pocket", {"through_all": True})
            per_copy = round(plate - volume(measurement), 1)

            sketcher.execute("catia_create_sketch", {"plane": "xy"})
            for x in (-10.0, 10.0, 30.0):
                sketcher.execute("catia_sketch_point", {"x": x, "y": 0.0})
            sketcher.execute("catia_close_sketch", {})
            part = conn.get_active_part()
            sketches = part.MainBody.Sketches
            sketch_name = str(sketches.Item(sketches.Count).Name)

            reply = part_design.execute("catia_user_pattern",
                                        {"count": count, "sketch_name": sketch_name})
            removed = round(plate - volume(measurement), 1)
            copies = round(removed / per_copy, 3) if per_copy else float("nan")
            say(f"count={count}, 3 points -> {removed} mm3 removed = {copies} pockets"
                f"  ({str(reply).splitlines()[0][:90]})")
        except Exception as error:  # noqa: BLE001
            say(f"count={count}: FAILED {str(error).splitlines()[0][:160]}")

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
