"""0.3.77 probes, measured on B30 before anything is changed.

Three questions from the 2026-11-07 report that cannot be answered by reading
code:

A  P3  which AXIS a plane reference actually drives in AddNewRectPattern, for
      x / y / z and for reverse_dir1 - the tool currently admits it does not
      know ("dir1_axis='x' moved the copies along -Y") and only warns.
B  P4  does `through_all` + direction='both' cut anything on a FRAME sketch,
      where an explicit depth does?
C  P1  does a loft over a sketch of four separate lines really fail, and does
      any other reference form build it?

Everything is created under F:\\catia_probe_v0377 and closed again.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0377"


def say(line: str) -> None:
    print(line, flush=True)


def bbox(measure: MeasurementTools, part) -> dict:
    return json.loads(measure._part_bbox(part, "mesh"))


def grew(before: dict, after: dict) -> str:
    """Which axis the copies moved along, in human terms."""
    out = []
    for axis in ("x", "y", "z"):
        delta = (after["dimensions"][f"length_{axis}"]
                 - before["dimensions"][f"length_{axis}"])
        if abs(delta) > 1.0:
            out.append(f"{axis}{'+' if after['min'][axis] < before['min'][axis] else '-'}"
                       f"({delta:+.1f})")
    return ", ".join(out) or "nothing moved"


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    measure = MeasurementTools(conn)

    try:
        before_active = str(conn.app.ActiveDocument.Name)
    except Exception:  # noqa: BLE001
        before_active = ""
    existing = {str(conn.documents.Item(i).Name)
                for i in range(1, conn.documents.Count + 1)}
    os.makedirs(ROOT, exist_ok=True)
    say(f"session before: {conn.documents.Count} document(s)")

    path = os.path.join(ROOT, "probe377.CATPart")
    try:
        # ── A: which axis does a plane reference drive? ────────────────────
        say("\nA  rect_pattern direction, plane references")
        docs.execute("catia_new_part", {"name": "Probe377", "file_path": path})
        part = conn.get_active_part()
        body = conn.get_active_part_body()
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 10, "y2": 10})
        sketcher.execute("catia_close_sketch", {})
        part_design.execute("catia_pad", {"height": 5})
        base = bbox(measure, part)
        say(f"   base box: {base['dimensions']}")

        for axis in ("x", "y", "z"):
            for reverse in (False, True):
                args = {"dir1_axis": axis, "dir1_count": 3, "dir1_spacing": 100}
                if reverse:
                    args["reverse_dir1"] = True
                try:
                    reply = part_design.execute("catia_rect_pattern", args)
                except Exception as error:  # noqa: BLE001
                    say(f"   dir1_axis={axis} reverse={reverse}: REFUSED "
                        f"{type(error).__name__}: {str(error).splitlines()[0][:110]}")
                    continue
                after = bbox(measure, part)
                say(f"   dir1_axis={axis} reverse={reverse}: moved {grew(base, after)}")
                # Delete the pattern so the next run starts from the same box.
                for index in range(body.Shapes.Count, 0, -1):
                    shape = body.Shapes.Item(index)
                    if "Pattern" in str(shape.Name) or "阵列" in str(shape.Name):
                        try:
                            body.Remove(shape)
                            part.Update()
                        except Exception as error:  # noqa: BLE001
                            say(f"      (could not delete {shape.Name}: {error})")
                        break
                else:
                    say(f"      (reply was: {str(reply)[:80]})")
                base = bbox(measure, part)

        # ── B: through_all + both on a frame sketch ────────────────────────
        say("\nB  pocket through_all + direction='both' on a frame sketch")
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 40, "y2": 40})
        sketcher.execute("catia_close_sketch", {})
        part_design.execute("catia_pad", {"height": 40})
        block = bbox(measure, part)
        say(f"   block box: {block['dimensions']}")
        sketch_reply = sketcher.execute("catia_create_sketch", {
            "plane": {"type": "frame", "origin": [20, 20, 20],
                      "u": [1, 0, 0], "v": [0, 0, 1]}})
        say(f"   frame sketch: {sketch_reply[:110]}")
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": -5, "y1": -5, "x2": 5, "y2": 5})
        close_reply = sketcher.execute("catia_close_sketch", {})
        name = str(conn.last_sketch.Name)
        for label, args in (
            ("through_all + both", {"sketch_name": name, "through_all": True,
                                    "direction": "both"}),
            ("depth 100 + both", {"sketch_name": name, "depth": 100,
                                  "direction": "both"}),
        ):
            try:
                reply = part_design.execute("catia_pocket", args)
                say(f"   {label}: {str(reply)[:150]}")
            except Exception as error:  # noqa: BLE001
                say(f"   {label}: REFUSED {type(error).__name__}: "
                    f"{str(error).splitlines()[0][:130]}")
            break
        say(f"   (close said: {close_reply[:100]})")

        # ── C: a loft over four separate sketch lines ──────────────────────
        say("\nC  loft over a multi-element sketch section")
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 20, "y2": 10})
        sketcher.execute("catia_close_sketch", {})
        first = str(conn.last_sketch.Name)
        sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 30})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 20, "y2": 10})
        sketcher.execute("catia_close_sketch", {})
        second = str(conn.last_sketch.Name)
        try:
            reply = part_design.execute("catia_solid_loft",
                                        {"sections": [first, second]})
            say(f"   lines loft: BUILT {str(reply)[:160]}")
        except Exception as error:  # noqa: BLE001
            say(f"   lines loft: REFUSED {type(error).__name__}: "
                f"{str(error).splitlines()[0][:200]}")
            # Variant: one spline per section (the documented workaround).
            sketcher.execute("catia_create_sketch", {"plane": "xy"})
            sketcher.execute("catia_sketch_spline", {
                "points": [[0, 0], [20, 0], [20, 10], [0, 10]], "closed": True})
            sketcher.execute("catia_close_sketch", {})
            third = str(conn.last_sketch.Name)
            sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 30})
            sketcher.execute("catia_sketch_spline", {
                "points": [[0, 0], [20, 0], [20, 10], [0, 10]], "closed": True})
            sketcher.execute("catia_close_sketch", {})
            fourth = str(conn.last_sketch.Name)
            try:
                reply = part_design.execute("catia_solid_loft",
                                            {"sections": [third, fourth]})
                say(f"   spline loft: BUILT {str(reply)[:160]}")
            except Exception as error:  # noqa: BLE001
                say(f"   spline loft: REFUSED {type(error).__name__}: "
                    f"{str(error).splitlines()[0][:160]}")

    except Exception as error:  # noqa: BLE001
        say(f"ABORTED: {type(error).__name__}: {error}")
    finally:
        say("\ncleanup")
        for name in sorted({str(conn.documents.Item(i).Name)
                            for i in range(1, conn.documents.Count + 1)}
                           - existing, reverse=True):
            for i in range(1, conn.documents.Count + 1):
                if str(conn.documents.Item(i).Name) == name:
                    try:
                        conn.documents.Item(i).Close()
                        say(f"   closed {name}")
                    except Exception as error:  # noqa: BLE001
                        say(f"   (could not close {name}: {error})")
                    break
        for stale in os.listdir(ROOT):
            try:
                os.remove(os.path.join(ROOT, stale))
            except OSError:
                pass
        try:
            os.rmdir(ROOT)
        except OSError:
            pass
        if before_active:
            for i in range(1, conn.documents.Count + 1):
                if str(conn.documents.Item(i).Name) == before_active:
                    try:
                        conn.documents.Item(i).Activate()
                    except Exception:  # noqa: BLE001
                        pass
                    break
        say(f"session after: {conn.documents.Count} document(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
