"""Can a curve be SAMPLED on this build? That decides the deviation tool.

A 2026-11-09 note inside catia_gsd_split says "there is no way to sample a curve
on this build (AddNewPointOnCurveFromPercent does not update)", and that claim is
why no curve-to-surface deviation tool exists. But _plane_normal_to_curve uses the
same primitive, calls UpdateObject on it, and the whole engrave depends on the
result - so at least one of the two is wrong. This probe asks the question
directly, with numbers known by construction.

Cell A  a LINE that leaves the top face: z runs 10 -> 13 over 50 mm, so the
        distance to the body at ratio t is exactly 3*t. Five fresh points.
Cell B  the whole curve's GetMinimumDistance - the number that CANNOT answer.
Cell C  can ONE point be re-used (set its ratio, update, re-measure)?
Cell D  can the sample points be deleted again, and does the part still update?
Cell E  a SPLINE with a known bump, nine samples.

    .venv/Scripts/python.exe scripts/b30_v0421_probe_deviation.py
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
    tempfile.gettempdir(), "catia_probe_v0421")
ROOT = os.path.join(_PROBE_ROOT, "deviation")


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


def block(conn, documents, sketcher, part_design, name: str) -> None:
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


def spa_of(conn):
    return conn.active_document.GetWorkbench("SPAWorkbench")


def gap_to_body(conn, gsd, element: str) -> float:
    """Minimum distance from one element to the active body, in mm."""
    part = conn.get_active_part()
    body_ref = part.CreateReferenceFromObject(part.MainBody)
    return float(spa_of(conn).GetMeasurable(gsd._ref(element))
                 .GetMinimumDistance(body_ref))


def add_point(conn, gsd, curve: str, ratio: float):
    """One point on the curve at `ratio`, appended, named, updated."""
    part = conn.get_active_part()
    factory = gsd._factory()
    geoset = gsd._get_geoset()
    curve_ref = gsd._ref(curve)
    point = factory.AddNewPointOnCurveFromPercent(curve_ref, ratio, 0)
    geoset.AppendHybridShape(point)
    name = f"{curve}_s{int(round(ratio * 100)):03d}"
    try:
        point.Name = name
    except Exception:  # noqa: BLE001
        name = str(point.Name)
    part.UpdateObject(point)
    return point, name


def sample(conn, gsd, curve: str, ratio: float) -> tuple[float, str]:
    """Distance from the curve's point at `ratio` to the body, in mm."""
    part = conn.get_active_part()
    body_ref = part.CreateReferenceFromObject(part.MainBody)
    point, name = add_point(conn, gsd, curve, ratio)
    reference = part.CreateReferenceFromObject(point)
    distance = float(spa_of(conn).GetMeasurable(reference)
                     .GetMinimumDistance(body_ref))
    return distance, name


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    gsd = GSDTools(conn)

    # ── A: a line that leaves the face, five samples ─────────────────────
    say("")
    say("── A: line (5,20,10) -> (55,20,13); distance should be exactly 3t ──")
    block(conn, documents, sketcher, part_design, "DevA")
    gsd.execute("catia_gsd_line",
                {"point1": [5, 20, 10], "point2": [55, 20, 13], "name": "Ramp"})
    ratios = (0.0, 0.25, 0.5, 0.75, 1.0)
    measured: list[float] = []
    for ratio in ratios:
        try:
            distance, name = sample(conn, gsd, "Ramp", ratio)
            measured.append(distance)
            say(f"  t={ratio:<5g} point '{name}' -> {distance:.6f} mm "
                f"(expected {3.0 * ratio:.6f})")
        except Exception as error:  # noqa: BLE001
            say(f"  t={ratio:<5g} FAILED {str(error).splitlines()[0][:150]}")

    # ── B: the one number a minimum gives ────────────────────────────────
    say("")
    say("── B: GetMinimumDistance of the WHOLE curve to the body ──")
    try:
        say(f"  whole curve -> {gap_to_body(conn, gsd, 'Ramp'):.6f} mm "
            "(the start is ON the face, so this is 0 even though the far end "
            f"is {3.0:.1f} mm off)")
    except Exception as error:  # noqa: BLE001
        say("  FAILED " + str(error).splitlines()[0][:150])

    # ── C: can ONE point serve every sample? ─────────────────────────────
    say("")
    say("── C: re-use one point (change its ratio, update, re-measure) ──")
    try:
        point, name = add_point(conn, gsd, "Ramp", 0.25)
        part = conn.get_active_part()
        body_ref = part.CreateReferenceFromObject(part.MainBody)
        reference = part.CreateReferenceFromObject(point)
        first = float(spa_of(conn).GetMeasurable(reference)
                      .GetMinimumDistance(body_ref))
        say(f"  at 0.25: {first:.6f} mm")
        moved = None
        for attribute in ("Ratio", "Percent", "Position", "Parameter"):
            if hasattr(point, attribute):
                say(f"  has attribute '{attribute}' = "
                    f"{getattr(point, attribute)!r}")
                if attribute == "Ratio":
                    try:
                        point.Ratio = 0.75
                        part.UpdateObject(point)
                        moved = float(spa_of(conn).GetMeasurable(reference)
                                      .GetMinimumDistance(body_ref))
                        say(f"  after Ratio=0.75 -> {moved:.6f} mm "
                            "(2.25 expected)")
                    except Exception as error:  # noqa: BLE001
                        say("  setting Ratio FAILED "
                            + str(error).splitlines()[0][:120])
        if moved is None:
            say("  no re-usable parameter found - N samples need N points")
    except Exception as error:  # noqa: BLE001
        say("  FAILED " + str(error).splitlines()[0][:150])

    # ── D: can the sample points be deleted again? ───────────────────────
    say("")
    say("── D: delete the five sample points, then update ──")
    for ratio in ratios:
        name = f"Ramp_s{int(round(ratio * 100)):03d}"
        try:
            answer = part_design.execute("catia_delete_feature",
                                          {"feature_name": name})
            say(f"  delete '{name}': " + " ".join(str(answer).split())[:90])
        except Exception as error:  # noqa: BLE001
            say(f"  delete '{name}' REFUSED " + str(error).splitlines()[0][:90])
    try:
        part = conn.get_active_part()
        part.Update()
        volume = part_design._body_volume_mm3(part, part.MainBody)
        say(f"  part.Update() OK, volume {volume}")
    except Exception as error:  # noqa: BLE001
        say("  part.Update() RAISED " + str(error).splitlines()[0][:150])

    # ── E: a spline with a known bump, nine samples ──────────────────────
    say("")
    say("── E: spline through z = 10, 10.8, 10.2, 11.4 at x = 5, 20, 35, 50 ──")
    block(conn, documents, sketcher, part_design, "DevE")
    gsd.execute("catia_gsd_spline",
                {"points": [[5, 20, 10], [20, 20, 10.8], [35, 20, 10.2],
                            [50, 20, 11.4]], "name": "Bump"})
    profile = []
    for step in range(9):
        ratio = step / 8
        try:
            distance, _ = sample(conn, gsd, "Bump", ratio)
            profile.append((ratio, distance))
            say(f"  t={ratio:.3f} -> {distance:.4f} mm")
        except Exception as error:  # noqa: BLE001
            say(f"  t={ratio:.3f} FAILED " + str(error).splitlines()[0][:120])
    if profile:
        worst = max(profile, key=lambda row: row[1])
        say(f"  max {worst[1]:.4f} mm at t={worst[0]:.3f} "
            f"(the last control point is 1.4 mm up, so >= 1.4 is right)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
