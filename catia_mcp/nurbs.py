"""NURBS / B-spline evaluation, for sketching a curve CATIA cannot define.

WHY THIS FILE EXISTS: a B30 measurement (2026-11-18, probe v0454) settled it -
``Factory2D.CreateSpline`` takes point OBJECTS and interpolates THROUGH them;
the sketch spline it returns ('Spline2D', 46 members) exposes no control points,
no degree and no knot vector, and there is no other sketch-spline constructor.
So a control-point B-spline (what a CAD interchange file usually specifies)
cannot be handed to CATIA as such. It can only be SAMPLED here and drawn as an
interpolating spline through the samples - which is what the 2026-11-17 report
did by hand: control points used as interpolation points gave +4.20 % volume
error, 17 sampled points gave -0.00027 %.

Everything in this module is plain arithmetic: no COM, no CATIA, so it is
testable without a session. The distance between the exact curve and the
sampling polyline is computed here; the distance between the exact curve and
the curve CATIA actually drew is measured in the session (see
``SketcherTools._measure_drawn_curve``) - two different numbers, and a reply
that reports the first as if it were the second would be the proxy-quantity
mistake of 0.3.141.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

from catia_mcp.results import ArgumentRejected

Point = tuple[float, float]


def _bad(message: str) -> ArgumentRejected:
    """A refusal of the caller's own arguments, with the code the server needs.

    ``nurbs`` raises these for everything it cannot represent, and they have to
    arrive as ``ARGUMENT_REJECTED`` / ``stage: validate`` rather than as the
    server's COM_ERROR for a call CATIA never saw (the envelope rule pinned by
    tests/test_argument_envelopes.py). ``ArgumentRejected`` is a ValueError too,
    so callers and tests that catch the plain type keep working.
    """
    return ArgumentRejected(message, code="BAD_ARGUMENT", stage="validate")


def clamped_knots(count: int, degree: int) -> list[float]:
    """The clamped uniform knot vector for ``count`` control points.

    Clamped means the first and last knots repeat ``degree + 1`` times, so the
    curve starts at the first control point and ends at the last - which is what
    a Bezier is, and what the report's SP15/SP16 rows specify:

        count=3, degree=2 -> 0,0,0,1,1,1
        count=4, degree=3 -> 0,0,0,0,1,1,1,1
    """
    if degree < 1:
        raise _bad("degree must be at least 1")
    if count < degree + 1:
        raise _bad(
            f"a degree-{degree} spline needs at least {degree + 1} control "
            f"points, got {count}")
    inner = count - degree - 1
    knots = [0.0] * (degree + 1)
    if inner > 0:
        knots.extend(index / (inner + 1) for index in range(1, inner + 1))
    knots.extend([1.0] * (degree + 1))
    return knots


def check_knots(knots: Sequence[float], count: int, degree: int) -> list[float]:
    """Validate a caller-supplied knot vector and return it as floats."""
    values = [float(value) for value in knots]
    expected = count + degree + 1
    if len(values) != expected:
        raise _bad(
            f"{count} control points of degree {degree} need "
            f"{expected} knots (count + degree + 1), got {len(values)}")
    for index in range(1, len(values)):
        if values[index] < values[index - 1]:
            raise _bad(
                f"knots must not decrease: knots[{index - 1}]="
                f"{values[index - 1]:g} > knots[{index}]={values[index]:g}")
    if values[degree] >= values[count]:
        raise _bad(
            "the knot vector spans no interval: knots[degree] must be less "
            f"than knots[count] (got {values[degree]:g} and {values[count]:g})")
    span = count - degree          # number of non-empty parameter spans
    distinct = len({value for value in values[degree:count + 1]})
    if distinct < span:
        # Not fatal in theory, but a span nothing can reach means the caller
        # expects a shape this vector cannot produce - say so instead of
        # drawing a curve with a silently different parameterisation.
        raise _bad(
            f"{count} control points of degree {degree} span {span} interval(s), "
            f"but the knot vector has only {distinct} distinct knot value(s) "
            "between knots[degree] and knots[count]")
    return values


def _find_span(u: float, knots: Sequence[float], count: int,
               degree: int) -> int:
    """Index of the knot span holding ``u`` (clamped to the real domain)."""
    n = count - 1
    if u >= knots[n + 1]:
        return n
    if u <= knots[degree]:
        return degree
    low, high = degree, n + 1
    while high - low > 1:
        middle = (low + high) // 2
        if u < knots[middle]:
            high = middle
        else:
            low = middle
    return low


def evaluate(control: Sequence[Sequence[float]], degree: int, u: float,
             knots: Sequence[float] | None = None,
             weights: Sequence[float] | None = None) -> Point:
    """The point at parameter ``u`` (de Boor, in homogeneous coordinates)."""
    count = len(control)
    knot_vector = (clamped_knots(count, degree) if knots is None
                   else list(knots))
    w = ([1.0] * count if weights is None else [float(value) for value in weights])
    low, high = knot_vector[degree], knot_vector[count]
    if u < low:
        u = low
    elif u > high:
        u = high
    span = _find_span(u, knot_vector, count, degree)
    # d[j] carries (w*x, w*y, w); a rational curve needs the division at the end.
    d = []
    for index in range(span - degree, span + 1):
        weight = w[index]
        d.append([control[index][0] * weight, control[index][1] * weight, weight])
    for r in range(1, degree + 1):
        for j in range(degree, r - 1, -1):
            index = j + span - degree
            denominator = (knot_vector[index + degree + 1 - r]
                           - knot_vector[index])
            alpha = 0.0 if denominator == 0 else (u - knot_vector[index]) / denominator
            for k in range(3):
                d[j][k] = (1.0 - alpha) * d[j - 1][k] + alpha * d[j][k]
    weight = d[degree][2]
    if abs(weight) < 1e-12:
        raise _bad(f"the curve's weight at u={u:g} is zero")
    return (d[degree][0] / weight, d[degree][1] / weight)


def spans(count: int, degree: int) -> int:
    """How many non-empty parameter intervals the curve has."""
    return count - degree


def sample(control: Sequence[Sequence[float]], degree: int,
           knots: Sequence[float] | None = None,
           weights: Sequence[float] | None = None,
           per_span: int = 16) -> list[Point]:
    """``per_span`` samples per interval, endpoints included, seam points once.

    For a 3- or 4-control-point Bezier this is 17 points at ``per_span=16`` -
    the sampling the report validated (0.9 %-4.2 % volume error down to
    0.001 %).
    """
    count = len(control)
    knot_vector = (clamped_knots(count, degree) if knots is None
                   else list(knots))
    low, high = knot_vector[degree], knot_vector[count]
    total = spans(count, degree) * max(1, int(per_span))
    points: list[Point] = []
    for index in range(total + 1):
        u = low + (high - low) * index / total
        point = evaluate(control, degree, u, knot_vector, weights)
        points.append((float(point[0]), float(point[1])))
    return points


def _distance_to_polyline(point: Point, polyline: Sequence[Point]) -> float:
    """Shortest distance from ``point`` to the polyline (segment-wise)."""
    best = math.inf
    px, py = point
    for index in range(len(polyline) - 1):
        ax, ay = polyline[index]
        bx, by = polyline[index + 1]
        dx, dy = bx - ax, by - ay
        length_squared = dx * dx + dy * dy
        if length_squared <= 0.0:
            distance = math.hypot(px - ax, py - ay)
        else:
            t = ((px - ax) * dx + (py - ay) * dy) / length_squared
            t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
            distance = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
        if distance < best:
            best = distance
    return best


def polyline_deviation(control: Sequence[Sequence[float]], degree: int,
                       knots: Sequence[float] | None = None,
                       weights: Sequence[float] | None = None,
                       per_span: int = 16) -> float:
    """Largest distance from the EXACT curve to the sampling polyline, in mm.

    This is the sampling error: how much the polyline misses the curve by. It is
    arithmetic, and it is NOT the error of the curve CATIA draws - the drawn
    curve interpolates the samples and generally lies closer to the exact curve
    than the polyline does. Both numbers are reported; confusing them is the
    mistake this module's docstring warns about.

    THE PROBE POINTS SIT INSIDE THE SAMPLING SEGMENTS, at 1/4, 1/2 and 3/4 of
    each - not on a grid of their own. A probe grid that happens to be aligned
    with the sampling grid measures 0.000000 mm on a curve that is visibly
    bowed (measured while writing this: 64 samples probed at 32 gave exactly 0),
    and a 0 that comes from the probe positions is worse than no number at all.
    """
    count = len(control)
    knot_vector = (clamped_knots(count, degree) if knots is None
                   else list(knots))
    polyline = sample(control, degree, knot_vector, weights, per_span)
    low, high = knot_vector[degree], knot_vector[count]
    segments = spans(count, degree) * max(1, int(per_span))
    worst = 0.0
    for index in range(segments):
        for fraction in (0.25, 0.5, 0.75):
            u = low + (high - low) * (index + fraction) / segments
            point = evaluate(control, degree, u, knot_vector, weights)
            worst = max(worst, _distance_to_polyline(point, polyline))
    return worst


def fit_samples(control: Sequence[Sequence[float]], degree: int,
                knots: Sequence[float] | None = None,
                weights: Sequence[float] | None = None,
                tolerance: float = 0.01, start: int = 8,
                limit: int = 256) -> dict[str, float]:
    """Smallest per-span sample count whose polyline meets ``tolerance``.

    The cost of more samples is real: every sample is a point element in the
    sketch, and ``GeometricElements`` has no Remove, so a curve drawn with 257
    points leaves 257 of them in the tree. So the count is chosen to just meet
    the caller's tolerance rather than fixed, and the reply says which number it
    landed on and what the deviation actually is - including when the limit was
    hit and the tolerance was NOT met.
    """
    per_span = max(1, int(start))
    limit = max(per_span, int(limit))
    while True:
        deviation = polyline_deviation(control, degree, knots, weights, per_span)
        if deviation <= tolerance or per_span >= limit:
            return {"per_span": per_span,
                    "deviation_mm": deviation,
                    "tolerance_mm": float(tolerance),
                    "met": deviation <= tolerance}
        per_span = min(limit, per_span * 2)


def describe(control: Sequence[Sequence[float]], degree: int,
             knots: Sequence[float] | None = None,
             weights: Sequence[float] | None = None,
             per_span: int = 16) -> dict[str, Any]:
    """Everything a reply needs about the exact curve, as plain data."""
    count = len(control)
    knot_vector = (clamped_knots(count, degree) if knots is None
                   else list(knots))
    samples = sample(control, degree, knot_vector, weights, per_span)
    return {
        "degree": degree,
        "control_points": count,
        "rational": weights is not None,
        "knots": [round(value, 12) for value in knot_vector],
        "spans": spans(count, degree),
        "samples": len(samples),
        "sample_points": [[round(x, 9), round(y, 9)] for x, y in samples],
        "polyline_deviation_mm": round(
            polyline_deviation(control, degree, knot_vector, weights, per_span), 9),
    }


def check_control_points(control: Any, degree: Any) -> tuple[list[Point], int]:
    """Validate the pair as the tool arguments arrive, with readable refusals."""
    if not isinstance(control, (list, tuple)) or len(control) < 2:
        raise _bad("control_points must be a list of at least 2 [x, y] pairs")
    points: list[Point] = []
    for entry in control:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise _bad(f"control point {entry!r} is not an [x, y] pair")
        try:
            points.append((float(entry[0]), float(entry[1])))
        except (TypeError, ValueError) as error:
            raise _bad(
                f"control point {entry!r} is not numeric") from error
    if degree is None:
        degree = 3
    try:
        degree = int(degree)
    except (TypeError, ValueError) as error:
        raise _bad(f"degree {degree!r} is not an integer") from error
    if degree < 1:
        raise _bad(f"degree must be at least 1, got {degree}")
    if len(points) < degree + 1:
        raise _bad(
            f"a degree-{degree} spline needs at least {degree + 1} control "
            f"points, got {len(points)}")
    return points, degree
