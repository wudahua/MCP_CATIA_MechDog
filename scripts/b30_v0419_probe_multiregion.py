"""Does an open curve in the sketch break catia_pocket? The 2026-11-13 report's P0.

Its claim: a sketch holding ONE closed region plus two open B-splines (the curves
that divide the region into three) makes pocket answer NO_MATERIAL, and CATIA then
deletes the sketch with the rollback.

Two cells answer the first half, and they differ in exactly one thing - the open
spline - so the comparison is controlled:

  A  a closed circle alone on the top face            expect: cuts
  B  the same circle plus an open spline through it   the report's case
  C  the same circle plus an open spline that stays INSIDE it (no division)

Each cell is a new document. If B fails where A succeeds, the claim is reproduced
and the reply can name the cause before CATIA does; if B succeeds, the claim is
not reproducible on this geometry and the report's failure came from somewhere
else.

    .venv/Scripts/python.exe scripts/b30_v0419_probe_multiregion.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0419")
ROOT = os.path.join(_PROBE_ROOT, "multiregion")


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
        if full and os.path.abspath(full).startswith(os.path.abspath(_PROBE_ROOT)):
            try:
                document.Close()
            except Exception:  # noqa: BLE001
                pass


def volume(part_design, conn):
    try:
        part = conn.get_active_part()
        return part_design._body_volume_mm3(part, part.MainBody)
    except Exception:  # noqa: BLE001
        return None


def sketches(conn) -> list[str]:
    try:
        body = conn.get_active_part().MainBody
        return [str(body.Sketches.Item(i).Name)
                for i in range(1, int(body.Sketches.Count) + 1)]
    except Exception:  # noqa: BLE001
        return []


def cell(documents, sketcher, part_design, conn, name: str,
         extra: str | None, depth: float = 3.0) -> None:
    say("")
    say(f"── {name}: {'circle + ' + extra if extra else 'circle alone'} ──")
    path = os.path.join(ROOT, f"{name}.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    activate(conn, f"{name}.CATPart")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 60, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})
    before = volume(part_design, conn)

    sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 10.0})
    sketcher.execute("catia_sketch_circle", {"cx": 30, "cy": 20, "radius": 8})
    if extra == "spline-crossing":
        # From below the circle to above it: this is what divides the region.
        sketcher.execute("catia_sketch_spline",
                         {"points": [[30, 0], [30, 20], [30, 40]]})
    elif extra == "spline-inside":
        sketcher.execute("catia_sketch_spline",
                         {"points": [[27, 17], [30, 20], [33, 23]]})
    elif extra == "line-crossing":
        sketcher.execute("catia_sketch_line",
                         {"x1": 30, "y1": 5, "x2": 30, "y2": 35})
    closed = sketcher.execute("catia_close_sketch", {"structured": True})
    say("  close: " + str(closed.get("data", {}).get("breaks", {}).get("closed"))
        + ", self_intersections="
        + str(len(closed.get("data", {}).get("self_intersections") or [])))
    try:
        answer = part_design.execute("catia_pocket",
                                      {"depth": depth, "direction": "normal"})
        say("  pocket: " + " ".join(str(answer).split())[:200])
    except Exception as error:  # noqa: BLE001
        say("  pocket RAISED: " + " ".join(str(error).split())[:300])
    after = volume(part_design, conn)
    say(f"  volume {before} -> {after}")
    say(f"  sketches now: {sketches(conn)}")
    return


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)

    cell(documents, sketcher, part_design, conn, "MR_A", None)
    cell(documents, sketcher, part_design, conn, "MR_B", "spline-crossing")
    cell(documents, sketcher, part_design, conn, "MR_C", "spline-inside")
    # A straight open curve, so the rule can be stated as "open", not "spline".
    cell(documents, sketcher, part_design, conn, "MR_D", "line-crossing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
