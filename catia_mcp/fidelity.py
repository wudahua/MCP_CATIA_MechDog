"""How far a curve strayed from the outline it replaced - measured, not claimed.

The 2026-11-15 report (R2) asked for a `deviation_mm` beside the
polyline-to-spline conversion, because the tool itself says the spline
"interpolates, so it bulges outward between the corners" and then leaves the
caller with no way to find out by how much.

What can be measured here was established on B30 2026-11-15 by
``scripts/b30_v0437_probe_deviation_views.py`` and
``scripts/b30_v0438_probe_targets.py``:

* A SKETCH exposes NOTHING to SPAWorkbench - ``Length``, ``Area``, ``Volume``,
  ``GetPoint``, ``GetCOG``, ``GetBoundingBox`` and ``GetPlane`` all answer
  「方法 … 失败」 on a sketch reference. So "where did this sketch land" cannot
  be read back, and the multi-view import proves its alignment another way.
* A point-on-curve CANNOT be placed on a ``Line2D`` inside a sketch, nor on a
  CLOSED spline: ``AddNewPointOnCurveFromPercent`` + ``UpdateObject`` answers
  「方法 UpdateObject 失败」 for both. That is why this module probes with
  COORDINATE points (``AddNewPointCoord``) instead of sampling the curve.
* ``GetMeasurable(<3D point>).GetMinimumDistance(<target>)`` works with a 3D
  gsd spline, with a ``Spline2D`` inside a sketch, and with a whole sketch as
  the target; it reads 0.0000 at a corner the curve passes through, which is the
  calibration reading every report here carries.
* A 2D sketch spline and a 3D gsd spline through the SAME four corners read
  DIFFERENT deviations at the same probe - 33.1387 mm vs 25.6325 mm at the side
  midpoint of a 100 x 40 rectangle. They are different interpolants, so a number
  is only ever reported for the object it was measured on; the two are never
  transferred from one to the other.

The probes are the MIDPOINTS of the replaced outline, plus a few of its corners:
a corner that the conversion passes through reads 0.0000 mm and proves the
reading is calibrated, while the midpoints are where an interpolating curve
bulges.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

# A probe that was placed exactly ON the curve still reads a little above zero
# (CATIA's own tolerance, and the fact that the corner is a point while the
# curve is a wire). One thousandth of a millimetre is the line between "the
# reading is calibrated" and "the numbers below are not a deviation".
CALIBRATION_MM = 0.001

PROBE_GEOSET = "MCP_Probes"


def _holder(part: Any) -> tuple[Any, bool]:
    """A geometrical set to park probe points in, and whether this call made it.

    An existing set is reused rather than a new one created, because a part full
    of one-element sets is exactly the clutter 0.3.135's report complained about.
    """
    sets = part.HybridBodies
    if int(sets.Count) > 0:
        return sets.Item(1), False
    block = sets.Add()
    try:
        block.Name = PROBE_GEOSET
    except Exception:  # noqa: BLE001 - the name is decoration
        pass
    return block, True


def _delete(conn: Any, shape: Any) -> bool:
    """Remove one element this module created. True when the call went through."""
    try:
        selection = conn.active_document.Selection
        selection.Clear()
        selection.Add(shape)
        selection.Delete()
        return True
    except Exception:  # noqa: BLE001 - reported through the count that follows
        return False
    finally:
        try:
            conn.active_document.Selection.Clear()
        except Exception:  # noqa: BLE001
            pass


def deviation_summary(probes: Sequence[float | None],
                      calibration: Sequence[float | None] = ()) -> dict[str, Any]:
    """Turn probe readings into the numbers a reply quotes. Pure.

    ``probes`` are the distances the outline's own midpoints ended up from the
    curve; ``calibration`` are the distances of corners the curve is supposed to
    pass THROUGH, so anything but ~0 there means the probe is not measuring what
    it claims and the maximum below must not be quoted as a deviation.
    """
    read = [float(value) for value in probes if value is not None]
    missed = sum(1 for value in probes if value is None)
    cal = [float(value) for value in calibration if value is not None]
    summary: dict[str, Any] = {
        "samples": len(probes),
        "read": len(read),
        "unreadable": missed,
        "max_mm": round(max(read), 4) if read else None,
        "mean_mm": round(sum(read) / len(read), 4) if read else None,
        "calibration_max_mm": round(max(cal), 4) if cal else None,
        "calibration_ok": None,
    }
    if cal:
        summary["calibration_ok"] = max(cal) <= CALIBRATION_MM
    # Named so the reply cannot print a maximum taken over nothing: a run where
    # every probe failed has max_mm None, which is a different sentence from
    # "0.0000 mm".
    summary["measured"] = bool(read)
    return summary


def probe_points(conn: Any, target: Any,
                 points: Sequence[Sequence[float]],
                 calibration: Sequence[Sequence[float]] = ()
                 ) -> dict[str, Any]:
    """Distances from COORDINATE points to ``target``, one point per probe.

    ``target`` is any COM element a measurable accepts: a gsd spline, a
    ``Spline2D`` inside a sketch, or a whole sketch. Every probe point is created,
    updated and measured, then deleted again - they are this module's own
    scaffolding and nothing references them.

    Returns the same dict ``deviation_summary`` builds, plus
    ``calibration_max_mm`` measured on ``calibration`` points, plus how many
    probes could not be removed (``left_behind``).
    """
    part = conn.get_active_part()
    document = conn.active_document
    spa = document.GetWorkbench("SPAWorkbench")
    factory = document.Part.HybridShapeFactory
    # The caller may hand in either an element or an already-made reference.
    try:
        target_ref = part.CreateReferenceFromObject(target)
    except Exception:  # noqa: BLE001 - then it already WAS a reference
        target_ref = target

    holder, created = _holder(part)
    readings: list[float | None] = []
    calibration_readings: list[float | None] = []
    made: list[Any] = []

    def measure(where: Sequence[float]) -> float | None:
        try:
            point = factory.AddNewPointCoord(float(where[0]), float(where[1]),
                                             float(where[2]))
            holder.AppendHybridShape(point)
            # Recorded only once it is IN the tree: a point that failed to be
            # appended was never created, and counting it as "left behind" would
            # report clutter that does not exist.
            made.append(point)
            part.UpdateObject(point)
            return float(spa.GetMeasurable(
                part.CreateReferenceFromObject(point)).GetMinimumDistance(target_ref))
        except Exception:  # noqa: BLE001 - one probe, not the whole reading
            return None

    for where in points:
        readings.append(measure(where))
    for where in calibration:
        calibration_readings.append(measure(where))

    left = sum(0 if _delete(conn, point) else 1 for point in made)
    if created:
        try:
            part.HybridBodies.Remove(holder)
        except Exception:  # noqa: BLE001 - an empty set is clutter, not damage
            pass
    summary = deviation_summary(readings, calibration_readings)
    summary["left_behind"] = left
    return summary


def outline_probes(corners: Sequence[Sequence[float]],
                   limit: int = 24) -> tuple[list[list[float]], list[list[float]]]:
    """Midpoint probes and corner calibration points for a closed outline.

    Points may be 2D (sketch coordinates) or 3D (world) - the width is taken
    from the first point, so the same rule serves both sides of the conversion.

    ``limit`` caps the number of midpoint probes: every probe is a CATIA element
    built, updated and measured, so the resolution is the cost of the call. When
    the outline has more sides than the limit the probes are spread evenly over
    it rather than taken from its beginning - a bulge anywhere matters.
    """
    loop = [list(corner) for corner in corners]
    if len(loop) < 2:
        return [], loop
    width = len(loop[0])
    midpoints: list[list[float]] = []
    for first, second in zip(loop, loop[1:] + loop[:1]):
        midpoints.append([(first[i] + second[i]) / 2.0 for i in range(width)])
    if limit and len(midpoints) > limit:
        step = len(midpoints) / float(limit)
        picked = [midpoints[int(index * step)] for index in range(limit)]
        midpoints = picked
    # Three corners, spread out: a calibration that only ever probes the first
    # corner is 0.0000 on a curve through that corner and says nothing about the
    # rest of the reading.
    calibration = [loop[0], loop[len(loop) // 3], loop[(2 * len(loop)) // 3]]
    return midpoints, calibration


def deviation_sentence(deviation: dict[str, Any] | None, *,
                       remedy: str = "catia_gsd_spline on the same outline "
                                     "measures it against the 3D spline "
                                     "instead") -> str:
    """What the conversion cost, in mm - or why the number is not a deviation.

    The tool descriptions have promised since 0.3.135 that a converted spline
    "rounds every corner" and quoted a 20 x 12 rectangle as evidence. That is
    somebody else's measurement; this sentence carries this sketch's, and it says
    so when it could not be taken - a maximum whose own calibration failed is not
    a smaller deviation, it is a reading that cannot be trusted.
    """
    if not deviation:
        return ""
    error = deviation.get("error")
    if error:
        return (f" deviation_mm could NOT be measured ({error}). The curve is "
                f"still interpolating - what is missing is the number, not the "
                f"warning; {remedy}.")
    if not deviation.get("measured"):
        return (" deviation_mm could not be read: none of the "
                f"{deviation.get('samples')} probe(s) answered, so nothing is "
                "claimed about this conversion's shape change.")
    worst = float(deviation["max_mm"])
    mean = deviation.get("mean_mm")
    samples = int(deviation.get("read") or 0)
    calibration = deviation.get("calibration_max_mm")
    contours = int(deviation.get("contours") or 1)
    head = (" DEVIATION of the spline from the outline it replaced: max "
            f"{worst:.4f} mm"
            + (f", mean {float(mean):.4f} mm" if mean is not None else "")
            + f" over {samples} side-midpoint probe(s)"
            + (f", worst of {contours} contour(s)" if contours > 1 else "") + ".")
    if deviation.get("calibration_ok"):
        tail = (" Three corners the spline passes THROUGH read "
                f"{float(calibration):.4f} mm, so the reading is calibrated.")
    elif calibration is None:
        tail = (" The corner probes did not answer, so this maximum is NOT "
                "calibrated - treat it as an estimate of the bulge, not a "
                "measurement.")
    else:
        tail = (f" BUT the corners read {float(calibration):.4f} mm, where the "
                "spline is supposed to pass through them: the reading is NOT "
                "calibrated, so do not quote this maximum as the shape change.")
    if deviation.get("left_behind"):
        tail += (f" {int(deviation['left_behind'])} probe point(s) could not be "
                 "removed again and are still in the tree; they reference "
                 "nothing.")
    return head + tail


def aligned(first: Sequence[float], second: Sequence[float],
            axis: int, tolerance: float = 1e-6) -> bool:
    """Do two 3D boxes cover the same range on one axis?"""
    return (math.isclose(first[axis], second[axis], abs_tol=tolerance)
            and math.isclose(first[3 + axis], second[3 + axis], abs_tol=tolerance))
