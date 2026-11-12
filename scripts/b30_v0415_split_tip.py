"""Can an inclined plane cut a tip off a solid? (2026-11-12 report, P1-3)

The report asked for `catia_trim_solid_by_plane` because "catia_split_solid requires
the cutter to divide the body completely into two halves and only keeps one half
(capability table: split is NOT a local cut)". For cutting a bottle's mouth off,
a plane DOES divide the body completely and keeping one side IS the operation - so
the question is not whether a missing tool should exist, but whether the existing
one does the job.

Measured here on a 40 x 40 x 40 pad, cut by a plane tilted 20 degrees about Y, so
the answer is a number that can be checked by hand:

    z_plane(x) = 20 + x*tan(20 deg)
    V(below)   = 40 * integral(0..40) (20 + 0.36397 x) dx
               = 40 * (800 + 291.17) = 43 647 mm3
    V(above)   = 64 000 - 43 647 = 20 353 mm3

    .venv/Scripts/python.exe scripts/b30_v0415_split_tip.py
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
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0415")
SIDE = 40.0
HEIGHT = 40.0
TILT = 20.0


def say(line: str = "") -> None:
    print(line, flush=True)


def activate(conn: CATIAConnection, name: str) -> None:
    for index in range(1, conn.documents.Count + 1):
        candidate = conn.documents.Item(index)
        if candidate.Name == name:
            candidate.Activate()
            if conn.active_document.Name != name:
                raise RuntimeError(f"could not activate {name}")
            return
    raise RuntimeError(f"{name} is not open")


def close_ours(conn: CATIAConnection) -> None:
    for index in range(int(conn.documents.Count), 0, -1):
        document = conn.documents.Item(index)
        try:
            full = str(document.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).startswith(os.path.abspath(ROOT)):
            try:
                document.Close()
            except Exception:  # noqa: BLE001
                pass


def volume(conn) -> float | None:
    part = conn.get_active_part()
    return PartDesignTools(conn)._body_volume_mm3(part, part.MainBody)


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    gsd = GSDTools(conn)

    path = os.path.join(ROOT, "TIP.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part", {"name": "TIP", "file_path": path,
                                         "overwrite": True})
    activate(conn, "TIP.CATPart")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": SIDE, "y2": SIDE})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": HEIGHT})
    say(f"block 40x40x40, volume {volume(conn)}")

    rise = SIDE * math.tan(math.radians(TILT))
    below = SIDE * (HEIGHT / 2.0 * SIDE
                    + 0.5 * math.tan(math.radians(TILT)) * SIDE * SIDE)
    say(f"the plane: z = {HEIGHT / 2:.1f} + x*tan({TILT:g} deg), so at x=40 z="
        f"{HEIGHT / 2 + rise:.2f}")
    say(f"hand calculation: below = {below:.1f} mm3, above = "
        f"{SIDE * SIDE * HEIGHT - below:.1f} mm3")

    plane = gsd.execute("catia_gsd_plane_3points", {
        "point1": [0.0, 0.0, HEIGHT / 2],
        "point2": [SIDE, 0.0, HEIGHT / 2 + rise],
        "point3": [0.0, SIDE, HEIGHT / 2],
        "name": "CutPlane"})
    say("plane: " + " ".join(str(plane).split())[:160])

    for keep in ("front", "back"):
        try:
            reply = " ".join(str(part_design.execute(
                "catia_split_solid", {"cutter": "CutPlane", "keep": keep,
                                      "name": f"Tip_{keep}"})).split())
        except Exception as error:  # noqa: BLE001 - the refusal is the datum
            say(f"  keep={keep:5} REFUSED {str(error).splitlines()[0][:150]}")
            continue
        say(f"  keep={keep:5} volume {volume(conn)}  | {reply[:200]}")
        # Put the block back for the second orientation.
        try:
            part_design.execute("catia_delete_feature", {"name": f"Tip_{keep}"})
        except Exception:  # noqa: BLE001
            pass
    say(f"  block after the two splits: {volume(conn)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
