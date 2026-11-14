"""Can a CLOSED SPLINE stand in for an ellipse as a loft section?

P0-4 wants elliptic sweep sections. GSD has no AddNewEllipse (measured 2026-11-14:
the factory answers "no" to that name, while AddNewConic and AddNewSpline answer
yes), so the two candidate routes are a spline approximation and a sketch ellipse
on a normal plane. This measures the first one, which needs nothing unknown:
64 points on the ellipse, one closed spline, then a two-section solid loft whose
volume is compared with the analytic elliptic frustum.

An ellipse drawn with 64 points is the one shape where an interpolating spline is
the RIGHT primitive - the earlier caution about splines ("a closed spline through
the corners of a 20 x 12 rectangle measured 24.04 x 21.46") is about polygons,
where the corners are a specification and the interpolation bulges across them.
Here the points sit ON the curve, 5.6 degrees apart, so there is nothing to bulge
across.

    .venv/Scripts/python.exe scripts/b30_v0428_probe_ellipse_spline.py
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
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0428")
ROOT = os.path.join(_PROBE_ROOT, "spline")


def say(line: str = "") -> None:
    print(line, flush=True)


def fresh(conn: CATIAConnection, documents: DocumentTools, name: str):
    path = os.path.join(ROOT, name + ".CATPart")
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
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    for index in range(1, int(conn.documents.Count) + 1):
        if conn.documents.Item(index).Name == name + ".CATPart":
            conn.documents.Item(index).Activate()
    return conn.get_active_part()


def ellipse_points(rx: float, ry: float, z: float, count: int = 64) -> list[list[float]]:
    return [[rx * math.cos(2 * math.pi * i / count),
             ry * math.sin(2 * math.pi * i / count), z] for i in range(count)]


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    documents = DocumentTools(conn)
    gsd = GSDTools(conn)
    part_design = PartDesignTools(conn)
    measurement = MeasurementTools(conn)

    part = fresh(conn, documents, "SplineEllipse")
    say("── two closed splines standing in for ellipses ──")
    for name, rx, ry, z in (("Ell0", 10.0, 6.0, 0.0), ("Ell1", 5.0, 3.0, 10.0)):
        reply = str(gsd.execute("catia_gsd_spline",
                                {"points": ellipse_points(rx, ry, z),
                                 "closed": True, "name": name}))
        say(f"  {name}: " + " ".join(reply.split())[:190])

    # Does the curve SIT ON the ellipse? Its length is the honest check: an exact
    # 10 x 6 ellipse is 51.05 mm (Ramanujan), and the spline must agree to well
    # under a percent or the loft is being fed a different shape.
    spa = conn.active_document.GetWorkbench("SPAWorkbench")
    for name, expected in (("Ell0", 51.0543), ("Ell1", 25.5271)):
        try:
            ref = part.CreateReferenceFromObject(part.HybridBodies.Item(1)
                                                 .HybridShapes.Item(name))
            length = float(spa.GetMeasurable(ref).Length)
            say(f"  {name} length = {length:.4f} mm against an exact "
                f"{expected:.4f} ({(length - expected) / expected * 100:+.3f} %)")
        except Exception as error:  # noqa: BLE001
            say(f"  {name} length: {type(error).__name__} {str(error)[:120]}")

    say("")
    say("── solid loft through them ──")
    try:
        reply = str(part_design.execute("catia_solid_loft",
                                        {"sections": ["Ell0", "Ell1"],
                                         "name": "EllLoft"}))
        say("  " + " ".join(reply.split())[:400])
    except Exception as error:  # noqa: BLE001
        say(f"  LOFT REFUSED: {type(error).__name__}: {str(error)[:400]}")
        return

    # Analytic volume of a loft between linearly-interpolated ellipses:
    #   V = pi * h * (f0 + 4*fmid + f1) / 6,  f = rx*ry
    f0, fmid, f1 = 10 * 6, 7.5 * 4.5, 5 * 3
    expected = math.pi * 10.0 * (f0 + 4 * fmid + f1) / 6.0
    try:
        inertia = json.loads(measurement.execute("catia_get_inertia", {}))
        volume = float(inertia.get("volume_mm3") or 0)
        say(f"  volume {volume:.2f} mm3 against an analytic {expected:.2f} "
            f"({(volume - expected) / expected * 100:+.3f} %)")
    except Exception as error:  # noqa: BLE001
        say(f"  volume: {type(error).__name__} {str(error)[:160]}")

    # The bounding box is the shape check that a volume can hide: an ellipse
    # 10 x 6 has to be 20 x 12 x 10, not 22 x 21 x 10.
    try:
        box = json.loads(part_design.execute("catia_get_bounding_box", {}))
        say(f"  bounding box {json.dumps(box)[:300]}")
    except Exception as error:  # noqa: BLE001
        say(f"  bounding box: {type(error).__name__} {str(error)[:160]}")


if __name__ == "__main__":
    main()
