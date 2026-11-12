"""One-shot spline benchmark, one condition per CATIA session.

The first attempt at this measured both conditions inside one CATIA session and
got 2.35 s and 13.65 s for the SAME work (no per-point update) one round apart -
CATIA slows down as the session fills up, so a within-session A/B is worthless.
This script takes the condition on the command line, starts from a fresh CATIA
process, builds one 90-point closed spline, prints the phases and quits.

    python scripts/b30_spline_bench.py slow   # per-point UpdateObject
    python scripts/b30_spline_bench.py fast   # points left to the spline update
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "fast"
    conn = CATIAConnection()
    print(conn.connect(), flush=True)
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    path = os.path.join(tempfile.gettempdir(), "b30_v0349", f"BENCH_{mode}.CATPart")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    document.execute("catia_new_part", {"name": f"BENCH_{mode}", "file_path": path})

    part = conn.get_active_part()
    factory = gsd._factory()
    geoset = gsd._get_geoset()
    points = [[30 * math.cos(2 * math.pi * i / 90),
               30 * math.sin(2 * math.pi * i / 90), 0.0] for i in range(90)]

    start = time.perf_counter()
    refs = []
    for spec in points:
        point = factory.AddNewPointCoord(spec[0], spec[1], spec[2])
        geoset.AppendHybridShape(point)
        if mode == "slow":
            part.UpdateObject(point)
        refs.append(part.CreateReferenceFromObject(point))
    split = time.perf_counter()
    spline = factory.AddNewSpline()
    for ref in refs:
        spline.AddPoint(ref)
    spline.SetClosing(1)
    geoset.AppendHybridShape(spline)
    part.UpdateObject(spline)
    end = time.perf_counter()

    print(f"{mode:<5}: points {split - start:.2f} s, spline {end - split:.2f} s, "
          f"total {end - start:.2f} s", flush=True)

    document.execute("catia_close_document", {"save": False})
    conn.app.Quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
