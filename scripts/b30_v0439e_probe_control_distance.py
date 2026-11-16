"""N7, the decisive cell: a distance between two CONTROL POINTS of the spline.

The point-to-construction-line constraint in v0439d took a value and read it
back while the volume stayed at 165856.7071 - the solver moved the LINE (which
the pad never sees) instead of the point. Two control points have no such third
body, and their distance IS the bounding-box width, so this is the constraint
N7 actually asked for.

    .venv/Scripts/python.exe -u scripts/b30_v0439e_probe_control_distance.py
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

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0439e")


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


def build(conn, docs, sketcher, parts, measurement, name: str,
          wanted: float | None):
    part = fresh(conn, docs, name)
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_spline",
                     {"points": [[0, 0], [100, 0], [100, 40], [0, 40]],
                      "closed": True, "name": "Outline"})
    sketch = conn.last_sketch
    points = []
    for index in range(1, int(sketch.GeometricElements.Count) + 1):
        element = sketch.GeometricElements.Item(index)
        name_of = str(getattr(element, "Name", "?"))
        if name_of.startswith("点"):
            points.append(element)
    constraint = None
    if wanted is not None and len(points) >= 3:
        constraint = sketch.Constraints.AddBiEltCst(
            sketcher._CST_DISTANCE,
            part.CreateReferenceFromObject(points[0]),
            part.CreateReferenceFromObject(points[2]))
        say(f"  constraint '{constraint.Name}' = "
            f"{float(constraint.Dimension.Value):g} mm "
            f"({points[0].Name} .. {points[2].Name})")
        constraint.Dimension.Value = wanted
        say(f"  drove it to {wanted:g}: reads "
            f"{float(constraint.Dimension.Value):g}")
    try:
        sketcher.execute("catia_close_sketch", {})
    except Exception as error:  # noqa: BLE001
        say("  (close failed: " + str(error).splitlines()[0][:70] + ")")
    try:
        part.Update()
    except Exception:  # noqa: BLE001
        pass
    parts.execute("catia_pad", {"height": 20.0})
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
    say(f"probe v0.3.138 control-point distance on {conn.app.Name}")
    base = build(conn, docs, sketcher, parts, measurement, "ProbeCtrlBase", None)
    say(f"  baseline volume {base}")
    wide = build(conn, docs, sketcher, parts, measurement, "ProbeCtrlWide", 140.0)
    say(f"  driven to 140 mm: volume {wide}")
    narrow = build(conn, docs, sketcher, parts, measurement, "ProbeCtrlNarrow", 60.0)
    say(f"  driven to 60 mm: volume {narrow}")
    if base and wide and narrow:
        say(f"  [{'OK  ' if wide > base + 1 and narrow < base - 1 else 'FAIL'}] "
            f"the constraint MOVES the geometry: 60 -> {narrow - base:+.1f}, "
            f"140 -> {wide - base:+.1f} mm3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
