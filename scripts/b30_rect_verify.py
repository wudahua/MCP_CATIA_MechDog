"""Verify the fixed rect_pattern call on real CATIA: six pairs, both reverses.

Before the fix (measured 2026-11-02): the first direction reference drove the
SECOND count/spacing, and a reference from OriginElements pointed along the
negative axis. The call now passes the references in CATIA's order and inverts
the reverse flags.

What has to be true afterwards, for every axis pair:

    dir1_axis=a, 3 copies at 20 mm  ->  copies along +a
    dir2_axis=b, 3 copies at 10 mm  ->  copies along +b
    reverse_dir1=true               ->  copies along -a instead

A 4x4x10 pad at the origin makes every case readable straight off the bounding
box: +X ends at x = 44, -X starts at x = -40, and so on.

The internal "swap the axes when the pair is left-handed" logic was measured
against the OLD argument order, so it is checked here too: if (x, z) now builds
and puts dir1 on X, that logic has to go.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

made: list[str] = []
problems: list[str] = []


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


def extent(measurement, doc_name):
    payload = json.loads(measurement.execute(
        "catia_get_bounding_box", {"document": doc_name, "method": "vertices"}))
    return payload.get("min") or {}, payload.get("max") or {}


def fresh(conn, document, sketcher, part_design, name):
    path = os.path.join(tempfile.gettempdir(), f"{name}.CATPart")
    document.execute("catia_new_part", {"name": name, "file_path": path})
    made.append(f"{name}.CATPart")
    activate(conn, f"{name}.CATPart")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 4.0, "y2": 4.0})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10 })


def axis_of(low, high, step=20.0) -> str:
    """Which axis grew, and on which side, for a single-direction pattern."""
    spans = {"x": high["x"] - low["x"], "y": high["y"] - low["y"],
             "z": high["z"] - low["z"]}
    axis = max(spans, key=lambda k: spans[k])
    if spans[axis] < 4.0 + step * 0.5:
        return "none"
    return {"x": "+X" if high["x"] > 20 else "-X",
            "y": "+Y" if high["y"] > 20 else "-Y",
            "z": "+Z" if high["z"] > 20 else "-Z"}[axis]


def main() -> int:
    conn = CATIAConnection()
    say(conn.connect())
    document, part_design = DocumentTools(conn), PartDesignTools(conn)
    sketcher, measurement = SketcherTools(conn), MeasurementTools(conn)

    say("")
    say("── one direction at a time: does dir1 land on the axis it names, +side? ──")
    index = 0
    for first, second in (("x", "y"), ("y", "z"), ("z", "x"),
                          ("x", "z"), ("z", "y"), ("y", "x")):
        index += 1
        name = f"RV_{index}"
        want = "+" + first.upper()
        try:
            fresh(conn, document, sketcher, part_design, name)
            part_design.execute("catia_rect_pattern", {
                "dir1_axis": first, "dir2_axis": second,
                "dir1_count": 3, "dir1_spacing": 20.0})
            low, high = extent(measurement, f"{name}.CATPart")
            got = axis_of(low, high)
            ok = got == want
            say(f"({first},{second}) 3x20 -> {got:3} want {want:3} "
                f"{'ok' if ok else 'MISMATCH'}   x[{low['x']}, {high['x']}] "
                f"y[{low['y']}, {high['y']}] z[{low['z']}, {high['z']}]")
            if not ok:
                problems.append(f"({first},{second}) dir1 landed {got}")
        except Exception as error:  # noqa: BLE001
            say(f"({first},{second}) 3x20 -> FAILED {str(error).splitlines()[0][:110]}")
            problems.append(f"({first},{second}) failed to build")

    say("")
    say("── the reverses: reverse_dir1 must move the copies the other way ──")
    for label, extra, want in (
        ("reverse_dir1", {"reverse_dir1": True}, "-X"),
        ("reverse_dir2", {"reverse_dir2": True}, "-Y"),
        ("both", {"reverse_dir1": True, "reverse_dir2": True}, "-X"),
    ):
        index += 1
        name = f"RV_{index}"
        try:
            fresh(conn, document, sketcher, part_design, name)
            arguments = {"dir1_axis": "x", "dir2_axis": "y",
                         "dir1_count": 3, "dir1_spacing": 20.0, **extra}
            if label == "reverse_dir2":
                # dir2 alone, so the -Y has an axis to be read on
                arguments = {"dir1_axis": "x", "dir2_axis": "y",
                             "dir1_count": 1, "dir1_spacing": 20.0,
                             "dir2_count": 3, "dir2_spacing": 20.0, **extra}
            part_design.execute("catia_rect_pattern", arguments)
            low, high = extent(measurement, f"{name}.CATPart")
            got = axis_of(low, high)
            ok = got == want
            say(f"{label:14} -> {got:3} want {want:3} {'ok' if ok else 'MISMATCH'}")
            if not ok:
                problems.append(f"{label} landed {got}")
        except Exception as error:  # noqa: BLE001
            say(f"{label:14} -> FAILED {str(error).splitlines()[0][:110]}")
            problems.append(f"{label} failed")

    say("")
    say("── a real grid: 3x2 at 20 x 15 mm on a 4x4 pad ──")
    index += 1
    name = f"RV_{index}"
    try:
        fresh(conn, document, sketcher, part_design, name)
        part_design.execute("catia_rect_pattern", {
            "dir1_axis": "x", "dir2_axis": "y",
            "dir1_count": 3, "dir1_spacing": 20.0,
            "dir2_count": 2, "dir2_spacing": 15.0})
        low, high = extent(measurement, f"{name}.CATPart")
        spanx = round(high["x"] - low["x"], 3)
        spany = round(high["y"] - low["y"], 3)
        want = (44.0, 19.0)
        ok = abs(spanx - want[0]) < 0.01 and abs(spany - want[1]) < 0.01
        say(f"grid spans x {spanx} y {spany}; want x 44 (3 at 20) y 19 (2 at 15)"
            f" -> {'ok' if ok else 'MISMATCH'}")
        say(f"volume {volume(measurement)} mm3 (6 copies x 160 = 960 expected)")
        if not ok:
            problems.append(f"grid spans {spanx} x {spany}")
    except Exception as error:  # noqa: BLE001
        say(f"grid -> FAILED {str(error).splitlines()[0][:130]}")
        problems.append("grid failed")

    say("")
    closed = []
    for name in reversed(made):
        for i in range(1, conn.documents.Count + 1):
            candidate = conn.documents.Item(i)
            if str(candidate.Name) == name:
                try:
                    candidate.Activate()
                    document.execute("catia_close_document", {"save": False})
                    closed.append(name)
                except Exception as error:  # noqa: BLE001
                    say(f"cleanup: {name}: {str(error).splitlines()[0][:90]}")
                break
    say(f"closed {len(closed)}/{len(made)}")
    say("PROBLEMS: " + ("; ".join(problems) if problems else "none"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
