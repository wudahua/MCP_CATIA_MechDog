"""N7's actual fork: a spline has no dimension of its own (measured), but its
CONTROL POINTS are separate sketch elements. A bounding-box constraint would be
"distance from this control point to this construction line", so the question
is whether AddBiEltCst(_CST_DISTANCE, point, line) works there - and whether
driving it moves the geometry or only the number.

    .venv/Scripts/python.exe -u scripts/b30_v0439d_probe_spline_bbox.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0439d")


def say(line: str = "") -> None:
    print(line, flush=True)


def fresh(conn, docs, name: str):
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, name + ".CATPart")
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
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    return conn.get_active_part()


def volume(measurement) -> float | None:
    import json

    try:
        return json.loads(measurement.execute("catia_get_inertia", {})).get("volume_mm3")
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    parts = PartDesignTools(conn)
    measurement = MeasurementTools(conn)
    say(f"probe v0.3.138 N7 fork on {conn.app.Name}")

    # baseline: the same spline padded with NO constraint
    part = fresh(conn, docs, "ProbeSplineBboxBase")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_spline",
                     {"points": [[0, 0], [100, 0], [100, 40], [0, 40]],
                      "closed": True, "name": "Outline"})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 20.0})
    base = volume(measurement)
    say(f"  baseline volume {base}")

    part = fresh(conn, docs, "ProbeSplineBbox")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_spline",
                     {"points": [[0, 0], [100, 0], [100, 40], [0, 40]],
                      "closed": True, "name": "Outline"})
    sketcher.execute("catia_sketch_line",
                     {"x1": 100, "y1": -20, "x2": 100, "y2": 60,
                      "construction": True})
    sketch = conn.last_sketch
    points, lines = [], []
    for index in range(1, int(sketch.GeometricElements.Count) + 1):
        element = sketch.GeometricElements.Item(index)
        name = str(getattr(element, "Name", "?"))
        say(f"  element {index}: {name}")
        if name.startswith("点"):
            points.append((name, element))
        elif name.startswith("直线"):
            lines.append((name, element))

    if not points or not lines:
        say("  [FAIL] no control point or no construction line to constrain")
        return 1
    # the point that sits ON the construction line: distance 0 to start with
    reference = part.CreateReferenceFromObject(lines[0][1])
    target = points[1] if len(points) > 1 else points[0]
    say(f"  constraining {target[0]} against {lines[0][0]}")
    try:
        constraint = sketch.Constraints.AddBiEltCst(
            sketcher._CST_DISTANCE, part.CreateReferenceFromObject(target[1]),
            reference)
        value = float(constraint.Dimension.Value)
        say(f"  [OK  ] distance constraint '{constraint.Name}' = {value:g} mm")
    except Exception as error:  # noqa: BLE001
        say("  [FAIL] AddBiEltCst(DISTANCE, point, construction line): "
            + str(error).splitlines()[0][:90])
        sketcher.execute("catia_close_sketch", {})
        return 1

    drove = False
    for wanted in (20.0, -20.0):
        try:
            constraint.Dimension.Value = wanted
            # No GetAbsoluteAxisData here: that call failed and took the rest of
            # the try with it, so "did the value stick" was never measured.
            try:
                conn.get_active_part().UpdateObject(sketch)
            except Exception:  # noqa: BLE001 - the read-back below decides
                pass
            read = float(constraint.Dimension.Value)
            say(f"  drove {target[0]} to {wanted:g}: reads {read:g}")
            drove = True
        except Exception as error:  # noqa: BLE001
            say(f"  [FAIL] driving to {wanted:g}: "
                + str(error).splitlines()[0][:80])
    try:
        sketcher.execute("catia_close_sketch", {})
    except Exception as error:  # noqa: BLE001
        say("  (close failed: " + str(error).splitlines()[0][:70] + ")")
    parts.execute("catia_pad", {"height": 20.0})
    after = volume(measurement)
    say(f"  constrained volume {after}  (baseline {base})")
    if base and after:
        say(f"  [{'OK  ' if abs(after - base) > 1.0 else 'FAIL'}] the constraint "
            f"MOVED the geometry: {after - base:+.3f} mm3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
