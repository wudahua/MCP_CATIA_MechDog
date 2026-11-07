"""0.3.77 acceptance on B30: the five fixes, measured on the real thing.

1  dir1_axis='x' moves the copies along +X (it moved +Y before this release).
2  pocket(direction='normal') flips when the material is on the other side.
3  a loft whose sections hold 59 and 60 points warns instead of staying silent.
4  a loft over a 4-line sketch names that section as the cause.
5  catia_new_part with a non-ASCII file name closes the empty part again.

Everything is created under F:\\catia_probe_v0377d and closed again; the
documents that were open before the run are left alone.
"""

from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0377d"
RESULTS: list[tuple[str, bool, str]] = []


def say(line: str) -> None:
    print(line, flush=True)


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    say(f"   {'OK  ' if ok else 'FAIL'} {label}" + (f"  [{detail[:200]}]" if detail else ""))


def dims(measure: MeasurementTools, part) -> dict:
    return json.loads(measure._part_bbox(part, "mesh"))["dimensions"]


def ellipse(count: int, rx: float = 40.0, ry: float = 6.0) -> list[list[float]]:
    return [[round(rx * math.cos(2 * math.pi * i / count), 4),
             round(ry * math.sin(2 * math.pi * i / count), 4)]
            for i in range(count)]


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
    say(f"session before: {conn.documents.Count} document(s)")
    os.makedirs(ROOT, exist_ok=True)
    for stale in os.listdir(ROOT):
        try:
            os.remove(os.path.join(ROOT, stale))
        except OSError:
            pass

    path = os.path.join(ROOT, "audit377.CATPart")
    try:
        docs.execute("catia_new_part", {"name": "Audit377", "file_path": path})
        part = conn.get_active_part()
        body = conn.get_active_part_body()

        # ── 1: the direction table ─────────────────────────────────────────
        say("\n1  dir1_axis='x' moves +X")
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 10, "y2": 10})
        sketcher.execute("catia_close_sketch", {})
        part_design.execute("catia_pad", {"height": 5})
        base = dims(measure, part)
        for axis, wanted in (("x", "length_x"), ("y", "length_y"), ("z", "length_z")):
            reply = part_design.execute("catia_rect_pattern", {
                "dir1_axis": axis, "dir1_count": 3, "dir1_spacing": 100})
            after = dims(measure, part)
            grew = {key: round(after[key] - base[key], 3) for key in after
                    if abs(after[key] - base[key]) > 1.0}
            check(f"dir1_axis='{axis}' grows {wanted} only",
                  list(grew) == [wanted] and grew.get(wanted, 0) > 150,
                  f"{grew} | {str(reply)[:90]}")
            check(f"dir1_axis='{axis}' reply no longer warns about direction",
                  "WARNING" not in str(reply), str(reply)[:120])
            for index in range(body.Shapes.Count, 0, -1):
                shape = body.Shapes.Item(index)
                if "Pattern" in str(shape.Name) or "阵列" in str(shape.Name):
                    try:
                        body.Remove(shape)
                        part.Update()
                    except Exception as error:  # noqa: BLE001
                        say(f"      (could not delete {shape.Name}: {error})")
                    break
            base = dims(measure, part)

        # ── 2: pocket(direction='normal') flips ────────────────────────────
        say("\n2  pocket(direction='normal') on the air side flips and cuts")
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 40, "y2": 40})
        sketcher.execute("catia_close_sketch", {})
        part_design.execute("catia_pad", {"height": 40})
        # Material lives at y 0..40 and this plane (u=X, v=Z) has normal -Y, so
        # "normal" points into the air below y=0 exactly like the reporter's
        # frame sketch: the cut only happens if the tool flips it.
        sketcher.execute("catia_create_sketch", {
            "plane": {"type": "frame", "origin": [20, 0, 20],
                      "u": [1, 0, 0], "v": [0, 0, 1]}})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": -5, "y1": -5, "x2": 5, "y2": 5})
        sketcher.execute("catia_close_sketch", {})
        try:
            reply = part_design.execute("catia_pocket", {
                "depth": 100, "direction": "normal"})
            check("the cut happened and says it flipped",
                  "auto-flipped" in str(reply), str(reply)[:200])
        except Exception as error:  # noqa: BLE001
            check("the cut happened and says it flipped", False,
                  f"{type(error).__name__}: {error}")

        # ── 3: a point-count mismatch warns ────────────────────────────────
        say("\n3  loft 59 vs 60 points warns")
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_spline",
                         {"points": ellipse(59), "closed": True})
        sketcher.execute("catia_close_sketch", {})
        low = str(conn.last_sketch.Name)
        sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 200})
        sketcher.execute("catia_sketch_spline",
                         {"points": ellipse(60), "closed": True})
        sketcher.execute("catia_close_sketch", {})
        high = str(conn.last_sketch.Name)
        try:
            reply = str(part_design.execute("catia_solid_loft",
                                            {"sections": [low, high]}))
            check("the reply warns about the point counts",
                  "WARNING: the sections do not hold the same number of points" in reply,
                  reply[-260:])
            check("it names both counts",
                  f"'{low}' 59" in reply and f"'{high}' 60" in reply, reply[-260:])
        except Exception as error:  # noqa: BLE001
            check("the loft built so the warning could be judged", False,
                  f"{type(error).__name__}: {str(error)[:160]}")

        # ── 4: a multi-element section is named ────────────────────────────
        say("\n4  a 4-line section is named as the cause")
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 20, "y2": 10})
        sketcher.execute("catia_close_sketch", {})
        lines_a = str(conn.last_sketch.Name)
        sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 300})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 20, "y2": 10})
        sketcher.execute("catia_close_sketch", {})
        lines_b = str(conn.last_sketch.Name)
        try:
            part_design.execute("catia_solid_loft", {"sections": [lines_a, lines_b]})
            check("the multi-element loft is refused", False, "it built")
        except Exception as error:  # noqa: BLE001
            message = str(error)
            check("the error names the section and its element count",
                  f"'{lines_a}' holds 4 separate curve(s)" in message,
                  message[:240])

        # ── 5: a failed save leaves nothing open ───────────────────────────
        say("\n5  new_part with a non-ASCII file name closes the empty part")
        count_before = conn.documents.Count
        bad = os.path.join(ROOT, "中文探针.CATPart")
        try:
            docs.execute("catia_new_part", {"name": "ProbeZh", "file_path": bad})
            check("the save was refused", False, "it saved")
        except Exception as error:  # noqa: BLE001
            message = str(error)
            check("SAVE_FAILED and the part was closed",
                  "COULD NOT BE SAVED" in message
                  and "closed again" in message, message[:200])
            check("no document was left behind",
                  conn.documents.Count == count_before,
                  f"{count_before} -> {conn.documents.Count}")

    except Exception as error:  # noqa: BLE001
        check("no COM error during the run", False, f"{type(error).__name__}: {error}")
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

    failed = [label for label, ok, _ in RESULTS if not ok]
    say(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        say("FAILED: " + "; ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
