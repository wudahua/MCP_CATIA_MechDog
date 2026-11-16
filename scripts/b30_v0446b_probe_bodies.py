"""Round 16, follow-up: what does `inside_solid` measure, and which body?

The report's 7 inside stations came back `null x4 + false x3`, and the nulls are
explained by the hedge (reproduced in v0446 cell B). The three `false` rows are
not: the same closure answers both modes, so `false` means the check itself said
"outside". The one mechanism that would do that to a point that IS in the
material is checking the WRONG BODY - `part.MainBody` is not "the part's solid",
it is one body, and a part can hold several.

So: build a part with two solid bodies, put a station inside the SECOND one, and
ask. If `inside_solid` says false there, then every row of the tool answers
"inside the MAIN body" and says "inside the part".

.venv/Scripts/python.exe -u scripts/b30_v0446b_probe_bodies.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402
from catia_mcp.topo_search import search_topology  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0446b")


def say(line: str = "") -> None:
    print(line, flush=True)


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    gsd = GSDTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, "ProbeV0446bTwoBodies.CATPart")
    for index in range(int(conn.documents.Count), 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = str(doc.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).lower().startswith(
                os.path.abspath(ROOT).lower()):
            try:
                doc.Close()
            except Exception:  # noqa: BLE001
                pass
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": "ProbeV0446bTwoBodies",
                                    "file_path": path})
    part = conn.get_active_part()

    # body 1 (the main body): a 40 x 30 x 20 box
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0.0, "y1": 0.0,
                                                "x2": 40.0, "y2": 30.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 20.0})
    say(f"body 1 is '{part.MainBody.Name}', {int(part.MainBody.Shapes.Count)} "
        f"shape(s)")

    # body 2: another box, 20 mm higher, in its own body
    say(str(parts.execute("catia_new_body", {"name": "Upper"}))[:200])
    conn.get_active_part()
    for position in range(1, int(part.Bodies.Count) + 1):
        body = part.Bodies.Item(position)
        say(f"  body {position}: '{body.Name}', "
            f"{int(body.Shapes.Count)} shape(s)")
    sketcher.execute("catia_create_sketch", {
        "plane": {"type": "offset", "base": "xy", "distance": 100.0}})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0.0, "y1": 0.0,
                                                "x2": 40.0, "y2": 30.0})
    sketcher.execute("catia_close_sketch", {})
    say(str(parts.execute("catia_pad", {"height": 10.0}))[:200])
    for position in range(1, int(part.Bodies.Count) + 1):
        body = part.Bodies.Item(position)
        say(f"  body {position}: '{body.Name}', "
            f"{int(body.Shapes.Count)} shape(s)")
    say(f"  MainBody is still '{part.MainBody.Name}'")

    # which faces does the ACTIVE body offer?
    active = conn.get_active_part()
    say(f"  active body for the probe: '{conn.get_active_part_body().Name}'")
    sel = conn.hso
    sel.Clear()
    sel.Add(conn.get_active_part_body())
    search_topology(sel, "Face")
    total = int(sel.Count)
    sel.Clear()
    say(f"  that body exposes {total} faces")

    # a station inside body 2 (z = 100..110) and one inside body 1 (z = 0..20)
    for label, point in (("inside body 2", [20.0, 15.0, 105.0]),
                         ("inside body 1", [20.0, 15.0, 10.0])):
        for mode in ("nearest", "along"):
            args = {"surface": "Face.1", "points": [point]}
            if mode == "along":
                args["direction"] = [0, -1, 0]
            try:
                reply = json.loads(gsd.execute("catia_surface_probe", args))
            except Exception as error:  # noqa: BLE001
                say(f"  {label}, {mode}: FAILED {str(error).splitlines()[0][:100]}")
                continue
            row = reply["probes"][0]
            say(f"  {label}, {mode}: inside_solid={row.get('inside_solid')!r} "
                f"inside_checked_against={reply.get('inside_checked_against')!r} "
                f"distance={row.get('distance_mm') or row.get('start_distance_mm')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
