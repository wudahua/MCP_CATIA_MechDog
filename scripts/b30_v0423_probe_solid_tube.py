"""catia_solid_tube, asked on B30 - one fresh document per case.

A  a 50 mm straight spine, r=5: pi*r^2*L = 3926.991 mm3
B  a 40 mm open spline arc, r=3, into an existing body that already holds a pad
C  a closed curve (circle) as the spine: must be refused, not half-built

    .venv/Scripts/python.exe scripts/b30_v0423_probe_solid_tube.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0423")
ROOT = os.path.join(_PROBE_ROOT, "tube")


def say(line: str = "") -> None:
    print(line, flush=True)


def close_ours(conn: CATIAConnection) -> None:
    for index in range(int(conn.documents.Count), 0, -1):
        document = conn.documents.Item(index)
        try:
            full = str(document.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).startswith(os.path.abspath(_PROBE_ROOT)):
            try:
                document.Close()
            except Exception:  # noqa: BLE001
                pass


def fresh(conn, documents, name):
    path = os.path.join(ROOT, f"{name}.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    for index in range(1, conn.documents.Count + 1):
        if conn.documents.Item(index).Name == f"{name}.CATPart":
            conn.documents.Item(index).Activate()


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)
    documents = DocumentTools(conn)
    gsd = GSDTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)

    # ── A: a 50 mm straight spine, r = 5 ─────────────────────────────────
    say("")
    say("── A: gsd_line (0,0,0)-(50,0,0), radius 5 ──")
    fresh(conn, documents, "TubeA")
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 0], "point2": [50, 0, 0], "name": "Spine"})
    try:
        say("  " + gsd.execute("catia_solid_tube",
                               {"curve": "Spine", "radius": 5, "name": "Neck"}))
    except Exception as error:  # noqa: BLE001
        say("  REFUSED " + " ".join(str(error).split())[:300])
    part = conn.get_active_part()
    say("  bodies: " + ", ".join(str(part.Bodies.Item(i).Name)
                                 for i in range(1, part.Bodies.Count + 1)))

    # ── B: an open spline, into an existing body ─────────────────────────
    say("")
    say("── B: open spline, r=3, body=零件几何体 (pad 40x40x4 already there) ──")
    fresh(conn, documents, "TubeB")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 40, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    say("  " + part_design.execute("catia_pad", {"height": 4})[:70])
    gsd.execute("catia_gsd_spline",
                {"points": [[0, 0, 20], [20, 10, 30], [40, 0, 20]], "name": "Arc"})
    try:
        say("  " + gsd.execute("catia_solid_tube",
                               {"curve": "Arc", "radius": 3, "body": "零件几何体",
                                "name": "Hand"}))
    except Exception as error:  # noqa: BLE001
        say("  REFUSED " + " ".join(str(error).split())[:300])
    part = conn.get_active_part()
    say("  bodies: " + ", ".join(str(part.Bodies.Item(i).Name)
                                 for i in range(1, part.Bodies.Count + 1)))
    say(f"  PartBody volume {part_design._body_volume_mm3(part, part.MainBody):.4f}")

    # ── C: a closed curve as the spine ───────────────────────────────────
    say("")
    say("── C: gsd_circle as the spine - must be refused ──")
    fresh(conn, documents, "TubeC")
    gsd.execute("catia_gsd_circle",
                {"center": [0, 0, 0], "radius": 20, "support_plane": "xy",
                 "name": "Ring"})
    try:
        say("  " + gsd.execute("catia_solid_tube",
                               {"curve": "Ring", "radius": 2, "name": "RingTube"}))
    except Exception as error:  # noqa: BLE001
        say("  REFUSED code=" + str(getattr(error, "code", "-")) + " "
            + " ".join(str(error).split())[:220])
    part = conn.get_active_part()
    say("  bodies after refusal: "
        + ", ".join(str(part.Bodies.Item(i).Name)
                    for i in range(1, part.Bodies.Count + 1)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
