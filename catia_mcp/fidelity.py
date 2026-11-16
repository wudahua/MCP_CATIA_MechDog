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

from catia_mcp.results import ArgumentRejected

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
        # The container goes too, and it goes through the SELECTION: measured on
        # B30 2026-11-15, `HybridBodies.Remove` and `block.Parent.Remove` do not
        # exist (`<unknown>.Remove`, both), while selection.Add(set) + Delete()
        # removes an empty set (1 -> 0). 0.3.136 tried Remove, the exception was
        # swallowed, and every part it measured was left holding an empty
        # 'MCP_Probes' (report N5).
        if not _delete(conn, holder):
            left += 1
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
        tail += (f" {int(deviation['left_behind'])} helper element(s) (probe "
                 "points, or the set that held them) could not be removed again "
                 "and are still in the tree; they reference nothing.")
    return head + tail


def aligned(first: Sequence[float], second: Sequence[float],
            axis: int, tolerance: float = 1e-6) -> bool:
    """Do two 3D boxes cover the same range on one axis?"""
    return (math.isclose(first[axis], second[axis], abs_tol=tolerance)
            and math.isclose(first[3 + axis], second[3 + axis], abs_tol=tolerance))


# ── sampling a surface at points (report round 14, 2026-11-16) ──────────────
#
# The report asked for "signed distance + normal + closest point on the surface"
# and said there was no entry and no equivalent. The equivalent was already
# here, one call away: ``Measurable.GetMinimumDistancePoints`` returns the
# closest point on each side, and it is exact - measured on B30 2026-11-16 on a
# cone (r30 at z=0, r12 at z=60): from (0,-25,30) it answered
# (-0.0000, -21.3303, 28.8991), whose radius 21.3303 IS the cone's radius at
# z=28.8991 (30 - 0.3 z = 21.3303) to six decimals. Two stations, two zeros.
#
# What it does NOT do, measured in the same session, so nobody re-derives it:
#   * AddNewProject(point, surface) projects along the support's own normal and
#     landed 0.4128 mm off the true closest point - a projection is not a foot.
#   * AddNewIntersection(line, surface) builds, but reading its coordinates
#     raises inside SystemService.Evaluate: a ray cast cannot be read back.
#   * PointCoord.X/Y/Z are NOT writable ("Property 'AddNewPointCoord.Y' can not
#     be set"), so one point cannot serve N probes - each probe is one point.
#   * A point INSIDE a solid body reads 0.0000 mm and its "closest point" is the
#     query point itself, so a body target cannot tell "inside" from "on the
#     surface". A face or a surface is the target that answers.
#
# The normal is free and exact for a nearest reading: the vector from the foot to
# the query point IS the surface normal there, which is the definition of a foot
# point. No stencil, no finite differences.

def _norm(vector: Sequence[float]) -> float:
    return math.sqrt(sum(float(value) * float(value) for value in vector))


def _unit(vector: Sequence[float]) -> list[float] | None:
    length = _norm(vector)
    if length < 1e-12:
        return None
    return [float(value) / length for value in vector]


def nearest_rows(points: Sequence[Sequence[float]], nearest: Any,
                 inside: Any = None) -> list[dict[str, Any]]:
    """Closest point, distance and normal for each query point. Pure.

    ``nearest(where) -> (closest_point, api_distance)`` is the only CATIA
    dependency, so the arithmetic below is testable against an analytic surface.
    The distance is reported from BOTH sides: the API's own number and the length
    of the vector it returned. They agree or the reading is not trusted, and
    saying which is which is the point of ``agrees``.

    ``inside(where) -> bool | None`` is optional and answers "is this query point
    inside the part's solid". It is what turns "0.0000 mm" from a shrug into a
    statement: against a BODY the two cases are indistinguishable (measured on a
    40x30x20 box), but against a FACE an inside point is a normal reading with a
    normal distance - and the caller has no way to see that from the number.
    """
    rows: list[dict[str, Any]] = []
    for where in points:
        at = [float(value) for value in where]
        try:
            closest, reported = nearest(at)
        except Exception as error:  # noqa: BLE001 - one bad probe, not the call
            rows.append({"at": at, "error": str(error).splitlines()[0][:120]})
            continue
        offset = [at[i] - float(closest[i]) for i in range(3)]
        length = _norm(offset)
        normal = _unit(offset)
        row: dict[str, Any] = {
            "at": [round(value, 6) for value in at],
            "surface_point": [round(float(value), 6) for value in closest],
            "distance_mm": round(length, 6),
            "offset": [round(value, 6) for value in offset],
            "normal": [round(value, 6) for value in normal] if normal else None,
            "agrees": abs(length - float(reported)) <= 1e-4,
            "api_distance_mm": round(float(reported), 6),
        }
        # A body target answers 0.0000 for a point that is inside it AND for one
        # that is on it, and hands back the query point as the "closest point".
        # Naming that state is the difference between a reading and a lie.
        if length <= 1e-6:
            row["on_surface_or_inside"] = True
        _mark_inside(row, inside, at, length)
        rows.append(row)
    return rows


def _mark_inside(row: dict[str, Any], inside: Any,
                 at: Sequence[float], target_distance: float) -> None:
    """Put ``inside_solid`` on a row when the caller can answer it.

    ``None`` means "not known", and is written as null rather than left out: a
    field that is absent reads as "asked and false" to anyone parsing JSON, which
    would be a claim this module cannot make when the part has no solid.
    """
    if inside is None:
        return
    try:
        verdict = inside(at)
    except Exception:  # noqa: BLE001 - an unanswerable check is not a failure
        verdict = None
    if verdict is None:
        row["inside_solid"] = None
        return
    # "Inside" and "on the target face" are different facts, and a point on the
    # face is inside the solid's closure too: when the target distance is zero the
    # honest answer is "cannot tell", not "inside".
    row["inside_solid"] = (True if target_distance > 1e-6 else None) if verdict \
        else False


def _solve_axis(where: Sequence[float], unit: Sequence[float], nearest: Any,
                tolerance: float, max_iterations: int) -> dict[str, Any]:
    """P (t) = where + t*unit, solved until P lies ON the surface.

    CONVERGENCE IS THE WHOLE GAP, not its component along the axis. The first
    version tested ``|dot(gap, unit)|`` and reported ``converged: true`` while a
    tangential part of the gap remained - which is how a start point inside a
    bottle produced ``offset 0, converged true`` next to a 2.4 mm gap (report
    round 15). A step that stops changing is not a hit.

    Returns the iterate, the last surface point, the gap, and whether it met the
    tolerance.
    """
    t = 0.0
    step = 0.0
    gap_size = 0.0
    closest: list[float] | None = None
    iterations = 0
    converged = False
    error = ""
    for iterations in range(1, max_iterations + 1):
        probe = [float(where[i]) + t * float(unit[i]) for i in range(3)]
        try:
            closest, _reported = nearest(probe)
        except Exception as failure:  # noqa: BLE001 - one bad probe
            error = str(failure).splitlines()[0][:120]
            closest = None
            break
        gap = [float(closest[i]) - probe[i] for i in range(3)]
        step = sum(gap[i] * float(unit[i]) for i in range(3))
        gap_size = _norm(gap)
        t += step
        if gap_size <= tolerance:
            converged = True
            break
        if abs(step) <= tolerance and gap_size > tolerance:
            # The axial component vanished but the surface point is somewhere
            # else entirely: the line does not meet this surface. Stopping here
            # is right; calling it converged is what produced round 15's fake
            # hits.
            break
    return {"t": t, "step": step, "gap": gap_size, "closest": closest,
            "iterations": iterations, "converged": converged, "error": error}


def along_rows(points: Sequence[Sequence[float]],
               direction: Sequence[float], nearest: Any,
               tolerance: float = 0.001, max_iterations: int = 12,
               inside: Any = None) -> list[dict[str, Any]]:
    """Where a line through each point meets the surface. Pure.

    The nearest reading alone answers "how far is the surface", not "at what
    height along MY axis", and on a sloped face the two differ: on the B30 cone,
    a query at (0,-25,30) looked straight along -y at a surface whose closest
    point sat 1.10 mm away in z. The fix is a projection iteration, not a new
    CATIA call: move the query along ``direction`` by the component of the gap
    that lies along it, and repeat until the point is ON the surface. Each step is
    one more nearest reading (a new point each time - PointCoord is not movable).

    ``converged`` is true only when the WHOLE gap met ``tolerance``; a row with a
    tangential remainder says false and carries ``gap_mm`` so the caller can see
    by how much. ``inside_solid`` describes the QUERY point (where the ray
    starts), never the hit.
    """
    unit = _unit(direction)
    if unit is None:
        # A zero axis would make every step zero and report a converged hit at the
        # query point itself. Coded, not a bare ValueError: this module's callers
        # branch on codes and a bare raise answers COM_ERROR for a call that never
        # reached CATIA (tests/test_argument_envelopes.py).
        raise ArgumentRejected(
            "direction is the zero vector, so there is no line to meet the surface "
            "along. Nothing was read.", code="BAD_ARGUMENT", stage="validate")
    rows: list[dict[str, Any]] = []
    for where in points:
        at = [float(value) for value in where]
        solved = _solve_axis(at, unit, nearest, tolerance, max_iterations)
        t = float(solved["t"])
        row: dict[str, Any] = {
            "at": [round(value, 6) for value in at],
            "offset_mm": round(t, 6),
            "iterations": int(solved["iterations"]),
            "converged": bool(solved["converged"]),
            "residual_mm": round(abs(float(solved["step"])), 6),
        }
        closest = solved["closest"]
        if closest is not None:
            hit = [at[i] + t * unit[i] for i in range(3)]
            row["surface_point"] = [round(float(value), 6) for value in closest]
            row["along_point"] = [round(value, 6) for value in hit]
            row["gap_mm"] = round(_norm([hit[i] - float(closest[i])
                                         for i in range(3)]), 6)
        if solved["error"]:
            row["error"] = solved["error"]
        _mark_inside(row, inside, at, 1.0 if solved["converged"] else 0.0)
        rows.append(row)
    return rows


def calibration_row(row: dict[str, Any], nearest: Any,
                    stencil_mm: float = 0.5) -> dict[str, Any]:
    """Feed a known answer back in: step off the surface and re-read.

    Every report in this project carries one reading whose right answer is known
    in advance. Here it is free: take the foot point of the first probe, move
    ``stencil_mm`` along its normal, and ask again. A calibrated reading returns
    the same foot point and a distance of ``stencil_mm``; anything else means the
    numbers above are not surface distances and must not be quoted as such.

    THIS IS THE NEAREST-MODE CALIBRATION. It needs a normal, which an along-mode
    row does not have - and the first version called it anyway, so every
    `direction` call answered ``ok: null`` with the reason "no surface point",
    which was not even the right reason (report round 15, P1). Along mode has its
    own in ``calibration_along``.
    """
    normal = row.get("normal")
    if not row.get("surface_point"):
        return {"ok": None, "mode": "nearest",
                "reason": "the first probe returned no surface point, so there is "
                          "nothing to step off"}
    if not normal:
        return {"ok": None, "mode": "nearest",
                "reason": "the first probe's foot is the query point itself (zero "
                          "offset), so it has no normal to step along"}
    base = [float(value) for value in row["surface_point"]]
    moved = [base[i] + stencil_mm * float(normal[i]) for i in range(3)]
    try:
        closest, reported = nearest(moved)
    except Exception as error:  # noqa: BLE001 - reported, not raised
        return {"ok": None, "mode": "nearest",
                "reason": str(error).splitlines()[0][:120]}
    drift = _norm([float(closest[i]) - base[i] for i in range(3)])
    return {"ok": abs(float(reported) - stencil_mm) <= 1e-3 and drift <= 1e-3,
            "mode": "nearest",
            "stepped_mm": stencil_mm,
            "read_mm": round(float(reported), 6),
            "foot_drift_mm": round(drift, 6),
            "reason": ""}


def calibration_along(row: dict[str, Any], direction: Sequence[float], nearest: Any,
                      tolerance: float, max_iterations: int,
                      stencil_mm: float = 0.5) -> dict[str, Any]:
    """The same idea, for a height: start the SAME ray `stencil` mm earlier.

    The line is unchanged - only its origin moves back along the axis - so the
    surface point it meets is the same one and the offset must grow by exactly
    ``stencil_mm``. That is an exact expectation on any surface, at any curvature,
    which is why it can be checked to 1e-3 instead of "looks consistent".

    Measured on B30 2026-11-16: from (20,15,12) along +Z at the top face of a box
    the offset is 8.0000; from (20,15,11.5) it is 8.5000, same hit (20,15,20).
    """
    unit = _unit(direction)
    if unit is None:
        return {"ok": None, "mode": "along",
                "reason": "the axis is degenerate, so there is no ray to move"}
    if not row.get("converged") or row.get("surface_point") is None:
        return {"ok": None, "mode": "along",
                "reason": "the first probe did not converge on the surface, so "
                          "there is no known hit to check a step against"}
    at = [float(value) for value in row["at"]]
    base_offset = float(row["offset_mm"])
    base_hit = [float(value) for value in row["surface_point"]]
    moved = [at[i] - stencil_mm * float(unit[i]) for i in range(3)]
    solved = _solve_axis(moved, unit, nearest, tolerance, max_iterations)
    if not solved["converged"] or solved["closest"] is None:
        return {"ok": False, "mode": "along",
                "reason": "the ray moved back by the stencil did NOT converge, so "
                          "the reading does not survive a 0.5 mm change of origin",
                "stepped_mm": stencil_mm}
    drift = _norm([float(solved["closest"][i]) - base_hit[i] for i in range(3)])
    grown = float(solved["t"]) - base_offset
    return {"ok": abs(grown - stencil_mm) <= 1e-3 and drift <= 1e-3,
            "mode": "along",
            "stepped_mm": stencil_mm,
            "read_mm": round(grown, 6),
            "foot_drift_mm": round(drift, 6),
            "reason": ""}


def _delete_all(conn: Any, shapes: Sequence[Any]) -> int:
    """Remove this module's probe points in ONE selection. Returns how many stayed."""
    shapes = [shape for shape in shapes if shape is not None]
    if not shapes:
        return 0
    selection = conn.active_document.Selection
    try:
        selection.Clear()
        for shape in shapes:
            selection.Add(shape)
        selection.Delete()
    except Exception:  # noqa: BLE001 - fall back to one at a time
        left = 0
        for shape in shapes:
            if not _delete(conn, shape):
                left += 1
        return left
    finally:
        try:
            selection.Clear()
        except Exception:  # noqa: BLE001
            pass
    return 0


def surface_probe(conn: Any, target: Any,
                  points: Sequence[Sequence[float]],
                  direction: Sequence[float] | None = None,
                  tolerance: float = 0.001, max_iterations: int = 12,
                  solid: Any = None) -> dict[str, Any]:
    """Run ``nearest_rows``/``along_rows`` against a real CATIA element.

    Probe points are created, updated, read and deleted: they are this module's
    own scaffolding and nothing references them (measured 2026-11-14 on the same
    pattern in ``probe_points``). ``left_behind`` counts the ones CATIA kept.

    ``solid`` is a reference to the part's solid body, or None when the target IS
    that body - in which case "inside" cannot be told from "on the surface" and
    every ``inside_solid`` is null (measured on a 40x30x20 box: an inside point
    against the body reads 0.0000 and hands back itself as the closest point).
    """
    from catia_mcp.tools.measurement import read_min_distance_points

    app = conn.app
    part = conn.get_active_part()
    document = conn.active_document
    spa = document.GetWorkbench("SPAWorkbench")
    factory = part.HybridShapeFactory
    measurable = spa.GetMeasurable(target)

    holder, created_holder = _holder(part)
    made: list[Any] = []

    def point(where: Sequence[float]) -> Any:
        # One point per probe: PointCoord.X/Y/Z are not writable (2026-11-16).
        created = factory.AddNewPointCoord(float(where[0]), float(where[1]),
                                           float(where[2]))
        holder.AppendHybridShape(created)
        made.append(created)
        part.UpdateObject(created)
        return created

    def nearest(where: Sequence[float]) -> tuple[list[float], float]:
        created = point(where)
        closest, _echo = read_min_distance_points(
            app, measurable, part.CreateReferenceFromObject(created))
        return closest, _norm([float(where[i]) - float(closest[i])
                               for i in range(3)])

    inside = None
    if solid is not None:
        # GetMinimumDistance takes a REFERENCE, not a Measurable - passing the
        # Measurable answers "type mismatch" and the flag would silently be null
        # for every row (measured 2026-11-16 while re-testing round 15).
        def inside(where: Sequence[float]) -> bool | None:  # noqa: F811 - closure
            created = point(where)
            distance = float(spa.GetMeasurable(
                part.CreateReferenceFromObject(created)).GetMinimumDistance(solid))
            return distance <= 1e-6

    if direction is None:
        rows = nearest_rows(points, nearest, inside=inside)
    else:
        rows = along_rows(points, direction, nearest, tolerance, max_iterations,
                          inside=inside)

    calibration: dict[str, Any] = {}
    if rows and "surface_point" in rows[0]:
        if direction is None:
            calibration = calibration_row(rows[0], nearest)
        else:
            calibration = calibration_along(rows[0], direction, nearest,
                                            tolerance, max_iterations)

    left = _delete_all(conn, made)
    if created_holder and not _delete(conn, holder):
        left += 1
    return {"rows": rows, "calibration": calibration, "left_behind": left,
            "probes_created": len(made)}
