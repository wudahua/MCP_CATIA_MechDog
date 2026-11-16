"""Calibration for N7: is AddMonoEltCst broken on the SPLINE, or on my call?

Round two had every spline dimension fail with CATIA's bare
"AddMonoEltCst failed". A bare failure is not a verdict, so the same call is
made here on a circle AND on the closed spline IN THE SAME SKETCH: the circle
is the known-good control. If the circle takes a radius and the spline does not
take a length, the refusal belongs to the spline - and "a spline has no native
dimension" is a measurement rather than a guess.

    .venv/Scripts/python.exe -u scripts/b30_v0439c_probe_spline_calibration.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0439c")


def say(line: str = "") -> None:
    print(line, flush=True)


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    path = os.path.join(ROOT, "ProbeCstCalibration.CATPart")
    # a failed earlier run leaves the document OPEN; closing it first is what
    # makes this script rerunnable (learned the hard way: DOCUMENT_ALREADY_OPEN)
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
    docs.execute("catia_new_part", {"name": "ProbeCstCalibration",
                                    "file_path": path})
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    # a LINE is the control, not the circle: a circle arrives with its own
    # radius constraint already, so a second one is over-constrained and proves
    # nothing (measured: the sketch then refuses UpdateObject on close).
    sketcher.execute("catia_sketch_line", {"x1": 0, "y1": 0, "x2": 40, "y2": 0})
    sketcher.execute("catia_sketch_spline",
                     {"points": [[30, 0], [70, 0], [70, 40], [30, 40]],
                      "closed": True, "name": "Outline"})
    sketch = conn.last_sketch
    found: dict[str, object] = {}
    for index in range(1, int(sketch.GeometricElements.Count) + 1):
        element = sketch.GeometricElements.Item(index)
        name = str(getattr(element, "Name", "?"))
        say(f"  element {index}: {name}")
        if "样条" in name or "Spline" in name:
            found["spline"] = element
        elif "直线" in name or "Line" in name:
            found["line"] = element
    # the circle's own radius constraint is added at draw time; ask for a
    # SECOND one on purpose with the same call shape the spline got
    for key in ("line", "spline"):
        element = found.get(key)
        if element is None:
            say(f"  [SKIP] no {key} found")
            continue
        for kind, label in ((sketcher._CST_LENGTH, "LENGTH"),
                            (sketcher._CST_RADIUS, "RADIUS")):
            try:
                constraint = sketch.Constraints.AddMonoEltCst(kind, element)
                say(f"  [OK  ] {key} + {label}: '{constraint.Name}'")
            except Exception as error:  # noqa: BLE001
                say(f"  [FAIL] {key} + {label}: {str(error).splitlines()[0][:70]}")
    try:
        sketcher.execute("catia_close_sketch", {})
    except Exception as error:  # noqa: BLE001
        say("  (close failed: " + str(error).splitlines()[0][:70] + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
