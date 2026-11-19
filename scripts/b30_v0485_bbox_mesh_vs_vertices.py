"""Vertices vs mesh on a heavy part, both cold (v0485)

v0484: cold measurables cost ~260 ms each (first 677), the 582-cell sweep 73 797
ms, and the warm sweep 2 345 ms.  Cold is a per-cell surcharge on resolving each
reference, not one big charge.

That makes the other reader worth pricing on the SAME part, cold, in its own
document: the mesh route exports once and triangulates, so it cannot be slower
than 582 path resolutions if the export is 12-17 s as measured on other parts.
Both boxes are read so the comparison is about the answer as well as the price.
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
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("TEMP", "."), "catia_probe_v0485")
os.makedirs(ROOT, exist_ok=True)


def say(text: str) -> None:
    print(text, flush=True)


def outline(n: int = 194) -> list[list[float]]:
    points = []
    for i in range(n):
        angle = 2 * math.pi * i / n
        radius = 40 + 8 * math.cos(5 * angle)
        points.append([round(radius * math.cos(angle), 4),
                       round(radius * math.sin(angle), 4)])
    points.append(points[0])
    return points


def call(measurement, method: str) -> None:
    reply = measurement.execute("catia_get_bounding_box", {"method": method})
    data = reply if isinstance(reply, dict) else json.loads(reply)
    say(f"  {method}: min={data.get('min')} max={data.get('max')}")
    say(f"    exact={data.get('exact')} code={data.get('bounds_code')} "
        f"elapsed={data.get('elapsed_ms')} ms  method={data.get('method')}")
    say(f"    points_route={data.get('points_route')} "
        f"vertices_used={data.get('vertices_used')} "
        f"cells_scanned={data.get('cells_scanned')} "
        f"first_measure={data.get('first_measure')} "
        f"cold_reason={data.get('cold_reason')}")
    say(f"    exact_note: {' '.join(str(data.get('exact_note', '')).split())[:200]}")
    say(f"    elapsed_note: {' '.join(str(data.get('elapsed_note', '')).split())[:220]}")
    for entry in data.get("parts", []):
        say(f"    part: {json.dumps(entry, ensure_ascii=False)[:220]}")


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    parts = PartDesignTools(conn)
    measurement = MeasurementTools(conn)
    say(f"probe v0485 on {conn.app.Name}")

    for method in ("vertices", "mesh"):
        try:
            while True:
                docs.execute("catia_close_document", {"save": False})
        except Exception:  # noqa: BLE001
            pass
        path = os.path.join(ROOT, f"Heavy_{method}.CATPart")
        if os.path.exists(path):
            os.remove(path)
        docs.execute("catia_new_part", {"name": f"Heavy_{method}",
                                        "file_path": path})
        sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "OUT"})
        sketcher.execute("catia_sketch_strokes", {"strokes": [outline()]})
        sketcher.execute("catia_close_sketch", {})
        parts.execute("catia_pad", {"sketch_name": "OUT", "height": 6})
        docs.execute("catia_save_document", {})
        say(f"\nCOLD {method} (fresh document):")
        call(measurement, method)
        say(f"  second, warm:")
        call(measurement, method)

    say("\ndone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
