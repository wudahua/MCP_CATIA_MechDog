"""catia_solid_sweep_variable on B30, one fresh document per case.

A  a 50 mm line, r 10 -> 7 -> 4 at t=0/0.5/1: the ruled sum is 8167.0 mm3
B  the bare-radius form [8, 8, 8]: a cylinder, pi*64*50 = 10053.1 mm3
C  a CLOSED spine (a gsd circle): refused, and nothing left in the tree
D  an open spline as the spine: it must build, with a volume near the ruled sum
E  body= into a body that already holds material: the union is reported

    .venv/Scripts/python.exe scripts/b30_v0425_probe_sweep.py
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

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0425")
ROOT = os.path.join(_PROBE_ROOT, "sweep")


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
    for index in range(1, int(conn.documents.Count) + 1):
        if conn.documents.Item(index).Name == f"{name}.CATPart":
            conn.documents.Item(index).Activate()
    return conn.get_active_part()


def tree(part) -> list[str]:
    names = []
    for collection in ("Bodies", "HybridBodies"):
        items = getattr(part, collection)
        for index in range(1, int(items.Count) + 1):
            container = items.Item(index)
            names.append(str(container.Name))
            try:
                shapes = container.Shapes if collection == "Bodies" else container.HybridShapes
                for inner in range(1, int(shapes.Count) + 1):
                    names.append("  " + str(shapes.Item(inner).Name))
            except Exception:  # noqa: BLE001
                pass
    return names


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    documents = DocumentTools(conn)
    gsd = GSDTools(conn)
    part_design = PartDesignTools(conn)
    sketcher = SketcherTools(conn)

    # ── A: the three-station cone ────────────────────────────────────────
    say("=" * 74)
    say("A: 50 mm line, r 10 -> 7 -> 4")
    say("=" * 74)
    part = fresh(conn, documents, "SweepA")
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [50, 0, 0],
                                   "name": "Spine"})
    try:
        say("  " + " ".join(str(gsd.execute(
            "catia_solid_sweep_variable",
            {"spine": "Spine", "name": "Cone",
             "sections": [[0, 10], [0.5, 7], [1, 4]]})).split())[:700])
    except Exception as error:  # noqa: BLE001
        say("  REFUSED " + str(getattr(error, "code", "-")) + ": "
            + " ".join(str(error).split())[:400])
    body = part.Bodies.Item(int(part.Bodies.Count))
    volume = part_design._body_volume_mm3(part, body)
    say(f"  body '{body.Name}' volume "
        + (f"{volume:.1f} mm3" if volume is not None else "unreadable"))
    say(f"  ruled sum = {math.pi * 25 / 3 * 219 + math.pi * 25 / 3 * 93:.1f} mm3")

    # ── B: the bare-radius form, a cylinder ──────────────────────────────
    say("")
    say("=" * 74)
    say("B: 50 mm line, sections [8, 8, 8]")
    say("=" * 74)
    part = fresh(conn, documents, "SweepB")
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [50, 0, 0],
                                   "name": "Spine"})
    try:
        say("  " + " ".join(str(gsd.execute(
            "catia_solid_sweep_variable",
            {"spine": "Spine", "name": "Rod",
             "sections": [8, 8, 8]})).split())[:500])
    except Exception as error:  # noqa: BLE001
        say("  REFUSED " + str(getattr(error, "code", "-")) + ": "
            + " ".join(str(error).split())[:300])
    body = part.Bodies.Item(int(part.Bodies.Count))
    volume = part_design._body_volume_mm3(part, body)
    say(f"  volume " + (f"{volume:.1f} mm3" if volume is not None else "unreadable")
        + f"; pi*64*50 = {math.pi * 64 * 50:.1f}")

    # ── C: a closed spine ────────────────────────────────────────────────
    say("")
    say("=" * 74)
    say("C: a gsd circle as the spine")
    say("=" * 74)
    part = fresh(conn, documents, "SweepC")
    sketcher.execute("catia_create_sketch", {"plane": "yz"})
    sketcher.execute("catia_sketch_point", {"x": 0, "y": 0})
    sketcher.execute("catia_close_sketch", {})
    gsd.execute("catia_gsd_point", {"x": 0, "y": 0, "z": 0, "name": "O"})
    gsd.execute("catia_gsd_circle", {"center": "O", "support_plane": "yz",
                                     "radius": 20, "name": "Ring"})
    before = tree(part)
    try:
        say("  " + " ".join(str(gsd.execute(
            "catia_solid_sweep_variable",
            {"spine": "Ring", "name": "Bad",
             "sections": [5, 5]})).split())[:300])
    except Exception as error:  # noqa: BLE001
        say("  REFUSED " + str(getattr(error, "code", "-")) + ": "
            + " ".join(str(error).split())[:420])
    after = tree(part)
    say(f"  tree before {len(before)} entries, after {len(after)}: "
        + ("unchanged" if before == after else f"CHANGED -> {after[len(before):]}"))

    # ── D: an open spline ────────────────────────────────────────────────
    say("")
    say("=" * 74)
    say("D: an open spline spine")
    say("=" * 74)
    part = fresh(conn, documents, "SweepD")
    points = [[0, 0, 0], [15, 8, 0], [30, 8, 10], [45, 0, 20]]
    gsd.execute("catia_gsd_spline", {"points": points, "name": "Arc"})
    try:
        say("  " + " ".join(str(gsd.execute(
            "catia_solid_sweep_variable",
            {"spine": "Arc", "name": "Horn",
             "sections": [[0, 6], [0.5, 9], [1, 3]]})).split())[:600])
    except Exception as error:  # noqa: BLE001
        say("  REFUSED " + str(getattr(error, "code", "-")) + ": "
            + " ".join(str(error).split())[:400])
    body = part.Bodies.Item(int(part.Bodies.Count))
    volume = part_design._body_volume_mm3(part, body)
    say(f"  volume " + (f"{volume:.1f} mm3" if volume is not None else "unreadable"))

    # ── E: into a body that already holds material ───────────────────────
    say("")
    say("=" * 74)
    say("E: body= into a body holding a 40x40x10 pad")
    say("=" * 74)
    part = fresh(conn, documents, "SweepE")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 40, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})
    gsd.execute("catia_gsd_line", {"point1": [20, 20, 10], "point2": [20, 20, 40],
                                   "name": "Up"})
    try:
        say("  " + " ".join(str(gsd.execute(
            "catia_solid_sweep_variable",
            {"spine": "Up", "name": "Boss", "body": "零件几何体",
             "sections": [[0, 8], [1, 4]]})).split())[:600])
    except Exception as error:  # noqa: BLE001
        say("  REFUSED " + str(getattr(error, "code", "-")) + ": "
            + " ".join(str(error).split())[:400])
    volume = part_design._body_volume_mm3(part, part.MainBody)
    say(f"  main body volume "
        + (f"{volume:.1f} mm3" if volume is not None else "unreadable")
        + f"; 16000 + ruled 3520.0 = 19520.0 expected")

    say("")
    say("done")
    close_ours(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
