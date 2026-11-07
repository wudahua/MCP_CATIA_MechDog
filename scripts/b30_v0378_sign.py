"""0.3.78 probe: the SIGN of every rect_pattern direction.

0.3.77 fixed the axis (x no longer lands on Y) and audited it by comparing
`dimensions` - which cannot see a sign: -X grows length_x by the same 200 mm as
+X. The 2026-11-08 acceptance measured the ENDPOINTS instead and found the
labels inverted: `axis x+` moved the copies to -X.

This probe measures min/max for dir1 and dir2 separately, with and without
reverse, on a pad whose base box is asymmetric so a mirror is visible:

    pad   x 0..40, y 0..10, z 0..5
    dir1 3 copies at 100 mm  ->  the box's min or max on that axis moves
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

ROOT = r"F:\catia_probe_v0378"


def say(line: str) -> None:
    print(line, flush=True)


def box(measure, part) -> dict:
    return json.loads(measure._part_bbox(part, "mesh"))


def moved(before: dict, after: dict) -> str:
    """Which ENDPOINT moved, and which way - the thing dimensions cannot see."""
    out = []
    for axis in ("x", "y", "z"):
        lo = after["min"][axis] - before["min"][axis]
        hi = after["max"][axis] - before["max"][axis]
        if abs(lo) < 1.0 and abs(hi) < 1.0:
            continue
        if abs(hi) > 1.0 and abs(lo) < 1.0:
            out.append(f"{axis}{'+' if hi > 0 else '-'}")
        elif abs(lo) > 1.0 and abs(hi) < 1.0:
            out.append(f"{axis}{'-' if lo < 0 else '+'}")
        else:
            out.append(f"{axis}both")
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

    path = os.path.join(ROOT, "sign.CATPart")
    try:
        docs.execute("catia_new_part", {"name": "ProbeSign", "file_path": path})
        part = conn.get_active_part()
        body = conn.get_active_part_body()
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 40, "y2": 10})
        sketcher.execute("catia_close_sketch", {})
        pad_reply = part_design.execute("catia_pad", {"height": 5})
        # The seed has to be the PAD every time: rect_pattern with no
        # feature_name takes the LAST shape, which after the first run is the
        # previous pattern - that is how a sign probe turned into a spiral.
        import re as _re
        quoted = _re.findall(r"'([^']*)'", str(pad_reply))
        pad_name = quoted[0] if quoted else None
        say(f"pad: {pad_name} | {str(pad_reply)[:90]}")
        base = box(measure, part)
        say(f"base: min {base['min']}  max {base['max']}")

        cases = []
        for axis in ("x", "y", "z"):
            cases.append((f"dir1_axis={axis}", {"dir1_axis": axis,
                                               "dir1_count": 3, "dir1_spacing": 100}))
            cases.append((f"dir1_axis={axis} reverse", {"dir1_axis": axis,
                                                        "dir1_count": 3,
                                                        "dir1_spacing": 100,
                                                        "reverse_dir1": True}))
        cases.append(("no axis", {"dir1_count": 3, "dir1_spacing": 100}))
        cases.append(("no axis reverse", {"dir1_count": 3, "dir1_spacing": 100,
                                          "reverse_dir1": True}))
        # dir2 alone: dir1_count=1, dir2_count=3, and dir1_axis pinned so the
        # handedness swap does not move the spacing between slots.
        cases.append(("default dir2", {"dir1_count": 1, "dir1_spacing": 0,
                                       "dir2_count": 3, "dir2_spacing": 100}))
        # dir2 in every ordered pair: the second slot does NOT follow the same
        # rule as the first (asked y, moved z), so the whole 3x2 matrix is
        # measured instead of assumed.
        for a, b in (("x", "y"), ("x", "z"), ("y", "x"),
                     ("y", "z"), ("z", "x"), ("z", "y")):
            cases.append((f"dir2 {a}/{b}", {"dir1_axis": a, "dir1_count": 1,
                                            "dir1_spacing": 0, "dir2_axis": b,
                                            "dir2_count": 3, "dir2_spacing": 100}))
            cases.append((f"dir2 {a}/{b} rev2", {"dir1_axis": a, "dir1_count": 1,
                                                 "dir1_spacing": 0,
                                                 "dir2_axis": b, "dir2_count": 3,
                                                 "dir2_spacing": 100,
                                                 "reverse_dir2": True}))

        for label, args in cases:
            args = dict(args, feature_name=pad_name)
            try:
                reply = part_design.execute("catia_rect_pattern", args)
            except Exception as error:  # noqa: BLE001
                say(f"{label:26} REFUSED {type(error).__name__}: "
                    f"{str(error).splitlines()[0][:90]}")
                continue
            after = box(measure, part)
            label_reply = str(reply).split("directions:")[-1].split(".")[0].strip()
            say(f"{label:26} moved {moved(base, after):10}  reply says: {label_reply[:70]}")
            # Remove the pattern by name from the reply, then rebuild the base
            # box from the solid that is left.
            import re as _re
            quoted = _re.findall(r"'([^']*)'", str(reply))
            name = quoted[0] if quoted else None
            # Delete through the tool, not body.Remove: Body has no Remove on
            # this build (AttributeError "<unknown>.Remove"), and a pattern left
            # behind poisons every later box measurement.
            try:
                part_design.execute("catia_delete_feature",
                                    {"feature_name": name})
            except Exception as error:  # noqa: BLE001
                say(f"   (delete failed for {name}: {str(error).splitlines()[0][:90]})")
            try:
                base = box(measure, part)
            except Exception:  # noqa: BLE001
                base = base

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
