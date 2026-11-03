"""Spline cost with and without tree points, one condition per CATIA session.

The 0.3.49 round learned this the hard way: measuring both conditions inside one
session gave 2.35 s and 13.65 s for identical work, so a within-session A/B is
worthless for a ratio. This script therefore takes ONE condition, runs it in a
fresh CATIA process, prints the phases, and quits.

    python scripts/b30_v0351_bench.py in-tree     # create_points=true
    python scripts/b30_v0351_bench.py tree-less   # create_points=false (default)
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
import time

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402

OUT = os.path.join(tempfile.gettempdir(), "b30_v0351")


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "tree-less"
    in_tree = mode == "in-tree"
    os.makedirs(OUT, exist_ok=True)

    conn = CATIAConnection()
    print(conn.connect(), flush=True)
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    document.execute("catia_new_part",
                     {"name": "BENCH", "file_path": os.path.join(OUT, "BENCH.CATPart")})
    if str(conn.active_document.Name) != "BENCH.CATPart":
        raise SystemExit("wrong active document")

    points = [[30 * math.cos(2 * math.pi * i / 90),
               30 * math.sin(2 * math.pi * i / 90), 0.0] for i in range(90)]

    start = time.perf_counter()
    reply = gsd.execute("catia_gsd_spline",
                        {"points": points, "closed": True, "name": "BENCH_SP",
                         "create_points": in_tree})
    built = time.perf_counter()
    part = conn.get_active_part()
    part.Update()
    updated = time.perf_counter()

    geoset = gsd._get_geoset()
    ref = part.CreateReferenceFromObject(part.FindObjectByName("BENCH_SP"))
    length = float(conn.active_document.GetWorkbench("SPAWorkbench")
                   .GetMeasurable(ref).Length)

    print(f"{mode:<9}: tool {built - start:.2f} s, part.Update {updated - built:.2f} s, "
          f"total {updated - start:.2f} s, tree {geoset.HybridShapes.Count}, "
          f"length {length:.3f} mm", flush=True)
    print("reply: " + reply.split(". ")[0], flush=True)

    document.execute("catia_close_document", {"save": False})
    conn.app.Quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
