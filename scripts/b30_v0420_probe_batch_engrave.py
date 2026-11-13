"""Can N tubes become ONE cutter? That decides what a batch engrave is worth.

The 2026-11-12 report engraved 10 strokes of one word and got 10 booleans, ~19
elements each. If the tubes can be joined into one shell, the same word costs ONE
CloseSurface and ONE boolean; if they cannot, a batch tool is only a loop and the
element count does not move.

Cell A  three separate 30 mm traces -> three tubes -> join -> CloseSurface ->
        one boolean. Removed volume against three times the single-tube figure.
Cell B  the same three traces engraved one at a time (catia_surface_engrave),
        as the control - the numbers have to agree, or the join changed the cut.

Each cell is its own document.

    .venv/Scripts/python.exe scripts/b30_v0420_probe_batch_engrave.py
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
    tempfile.gettempdir(), "catia_probe_v0420")
ROOT = os.path.join(_PROBE_ROOT, "batch")


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


def block(documents, sketcher, part_design, conn, name: str) -> None:
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


TRACES = ((5.0, 55.0, 8.0), (5.0, 55.0, 20.0), (5.0, 55.0, 32.0))


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    gsd = GSDTools(conn)

    # ── A: three tubes, joined, one boolean ────────────────────────────────
    say("")
    say("── A: three traces, three tubes, ONE boolean ──")
    block(documents, sketcher, part_design, conn, "BA")
    before = volume(part_design, conn)
    names = []
    for index, (x1, x2, y) in enumerate(TRACES, start=1):
        name = f"T{index}"
        gsd.execute("catia_gsd_line",
                    {"point1": [x1, y, 10], "point2": [x2, y, 10], "name": name})
        names.append(name)
    tubes = []
    for index, name in enumerate(names, start=1):
        try:
            reply = gsd.execute("catia_gsd_plane_normal_to_curve",
                                {"curve": name, "position": 0.5,
                                 "name": f"B{index}_N"})
            import re

            origin = re.search(r"origin '([^']+)'", " ".join(str(reply).split()))
            centre = origin.group(1) if origin else f"{name}_at0.5"
            gsd.execute("catia_gsd_circle",
                        {"center": centre, "support_plane": f"B{index}_N",
                         "radius": 0.4, "name": f"B{index}_P"})
            gsd.execute("catia_gsd_sweep",
                        {"profile": f"B{index}_P", "guide": name,
                         "name": f"B{index}_T"})
            tubes.append(f"B{index}_T")
            say(f"  tube {index}: {centre} -> {f'B{index}_T'}")
        except Exception as error:  # noqa: BLE001
            say(f"  tube {index} FAILED: " + str(error).splitlines()[0][:120])
    try:
        joined = gsd.execute("catia_gsd_join", {"elements": tubes, "name": "Word"})
        say("  join: " + " ".join(str(joined).split())[:180])
    except Exception as error:  # noqa: BLE001
        say("  join RAISED: " + " ".join(str(error).split())[:260])
    try:
        part_design.execute("catia_new_body", {"name": "WordBody"})
        closed = gsd.execute("catia_gsd_close_surface", {"surface": "Word"})
        say("  close: " + " ".join(str(closed).split())[:160])
    except Exception as error:  # noqa: BLE001
        say("  close RAISED: " + " ".join(str(error).split())[:260])
    try:
        part_design.execute("catia_set_active_body", {"name": "零件几何体"})
        answer = part_design.execute("catia_boolean",
                                      {"operation": "remove",
                                       "tool_body": "WordBody",
                                       "target_body": "零件几何体"})
        say("  boolean: " + " ".join(str(answer).split())[:220])
    except Exception as error:  # noqa: BLE001
        say("  boolean RAISED: " + " ".join(str(error).split())[:260])
    after = volume(part_design, conn)
    say(f"  volume {before} -> {after}  (3 x 12.566 = 37.70 expected)")

    # ── B: the same three traces, one at a time ────────────────────────────
    say("")
    say("── B: control - three separate engraves ──")
    block(documents, sketcher, part_design, conn, "BB")
    before = volume(part_design, conn)
    total = 0.0
    for index, (x1, x2, y) in enumerate(TRACES, start=1):
        gsd.execute("catia_gsd_line",
                    {"point1": [x1, y, 10], "point2": [x2, y, 10],
                     "name": f"S{index}"})
        answer = gsd.execute("catia_surface_engrave",
                             {"curve": f"S{index}", "depth": 0.4,
                              "name": f"S{index}"})
        import re

        found = re.search(r"Removed ([\d.]+) mm3", str(answer))
        if found:
            total += float(found.group(1))
    after = volume(part_design, conn)
    say(f"  volume {before} -> {after}  (sum of the replies: {total:.3f})")

    # ── C: three CloseSurfaces in ONE body, then one boolean ───────────────
    # Join refuses disjoint tubes (cell A), so the only way to one boolean is a
    # tool body holding three lumps. Whether CATIA allows that in Part Design is
    # a measurement, not a guess.
    say("")
    say("── C: three CloseSurfaces in one body, one boolean ──")
    block(documents, sketcher, part_design, conn, "BC")
    before = volume(part_design, conn)
    part = conn.get_active_part()
    for index, (x1, x2, y) in enumerate(TRACES, start=1):
        gsd.execute("catia_gsd_line",
                    {"point1": [x1, y, 10], "point2": [x2, y, 10],
                     "name": f"U{index}"})
        reply = gsd.execute("catia_gsd_plane_normal_to_curve",
                            {"curve": f"U{index}", "position": 0.5,
                             "name": f"C{index}_N"})
        import re

        origin = re.search(r"origin '([^']+)'", " ".join(str(reply).split()))
        centre = origin.group(1) if origin else f"U{index}_at0.5"
        gsd.execute("catia_gsd_circle",
                    {"center": centre, "support_plane": f"C{index}_N",
                     "radius": 0.4, "name": f"C{index}_P"})
        gsd.execute("catia_gsd_sweep",
                    {"profile": f"C{index}_P", "guide": f"U{index}",
                     "name": f"C{index}_T"})
    part_design.execute("catia_new_body", {"name": "WordBody"})
    part_design.execute("catia_set_active_body", {"name": "WordBody"})
    for index in (1, 2, 3):
        try:
            answer = gsd.execute("catia_gsd_close_surface",
                                 {"surface": f"C{index}_T"})
            say(f"  close {index}: " + " ".join(str(answer).split())[:120])
        except Exception as error:  # noqa: BLE001
            say(f"  close {index} RAISED: " + " ".join(str(error).split())[:200])
    try:
        body = part.Bodies.Item(part.Bodies.Count)
        say(f"  tool body '{body.Name}' volume: "
            f"{part_design._body_volume_mm3(part, body)}")
    except Exception as error:  # noqa: BLE001
        say("  tool body volume unreadable: " + str(error).splitlines()[0][:100])
    try:
        part_design.execute("catia_set_active_body", {"name": "零件几何体"})
        answer = part_design.execute("catia_boolean",
                                      {"operation": "remove",
                                       "tool_body": "WordBody",
                                       "target_body": "零件几何体"})
        say("  boolean: " + " ".join(str(answer).split())[:220])
    except Exception as error:  # noqa: BLE001
        say("  boolean RAISED: " + " ".join(str(error).split())[:260])
    after = volume(part_design, conn)
    say(f"  volume {before} -> {after}  (3 x 12.566 = 37.70 expected)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
