"""Where a 2D outline crosses itself, from the drawing record alone.

Why a separate module: the check has to run on a closed sketch, where CATIA
hands no coordinates back (``Point2D`` exposes no readable X/Y on this build),
so the geometry comes from the record of what was drawn - the same store
``catia_close_sketch`` already uses for its outline echo and its break list.
Keeping the arithmetic out of the 3000-line sketcher module means every case
below is a unit test instead of a probe.

WHAT THIS CANNOT SEE, stated once and repeated in the reply: a sketch that was
not drawn in this session has no record, so nothing is checked. That is the
0.3.101 lesson - a guard that only works in the session that built the curve is
still worth having, but it must not claim more than it knows.

Segments become two primitives only - a line and a circular arc - and every
other element kind is decomposed into one of them (a rectangle into four lines,
a spline into its point chain, an ellipse into a fine polygon). Two segments
that SHARE AN ENDPOINT are not a crossing: that is what a closed outline is.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

TAU = 2.0 * math.pi


def _seg(kind: str, source: int, label: str, **values: Any) -> dict[str, Any]:
    return {"kind": kind, "source": source, "label": label, **values}


def _arc(source: int, label: str, cx: float, cy: float, radius: float,
         start: float, sweep: float) -> dict[str, Any]:
    return _seg("arc", source, label, c=(cx, cy), r=radius,
                start=start, sweep=sweep,
                a=_on_circle(cx, cy, radius, start),
                b=_on_circle(cx, cy, radius, start + sweep))


def _line(source: int, label: str, x1: float, y1: float,
          x2: float, y2: float) -> dict[str, Any]:
    return _seg("line", source, label, a=(x1, y1), b=(x2, y2))


def _on_circle(cx: float, cy: float, radius: float,
               angle: float) -> tuple[float, float]:
    return (cx + radius * math.cos(angle), cy + radius * math.sin(angle))


def _chain(source: int, label: str, points: list[tuple[float, float]],
           closed: bool) -> list[dict[str, Any]]:
    """A point chain as straight segments; consecutive points are one segment."""
    out: list[dict[str, Any]] = []
    pairs = list(zip(points, points[1:]))
    if closed and len(points) > 2:
        pairs.append((points[-1], points[0]))
    for index, ((x1, y1), (x2, y2)) in enumerate(pairs, start=1):
        if math.hypot(x2 - x1, y2 - y1) < 1e-12:
            continue
        out.append(_line(source, f"{label} segment {index}", x1, y1, x2, y2))
    return out


def segments_of(entries: Iterable[dict[str, Any]],
                curve_steps: int = 48) -> list[dict[str, Any]]:
    """Turn drawing-record entries into line and arc primitives.

    ``source`` is the 1-based position of the entry in the record, which is the
    order the caller drew them - it is the only handle CATIA lets us quote back.
    """
    out: list[dict[str, Any]] = []
    for position, entry in enumerate(entries, start=1):
        kind = str(entry.get("kind") or "")
        label = f"{kind} #{position}"
        if kind == "line":
            out.append(_line(position, label, float(entry["x1"]),
                             float(entry["y1"]), float(entry["x2"]),
                             float(entry["y2"])))
        elif kind == "rectangle":
            left, right = sorted((float(entry["x1"]), float(entry["x2"])))
            bottom, top = sorted((float(entry["y1"]), float(entry["y2"])))
            corners = [(left, bottom), (right, bottom),
                       (right, top), (left, top)]
            for index in range(4):
                (x1, y1), (x2, y2) = corners[index], corners[(index + 1) % 4]
                out.append(_line(position, f"{label} edge {index + 1}",
                                 x1, y1, x2, y2))
        elif kind == "arc":
            out.append(_arc(position, label, float(entry["cx"]),
                            float(entry["cy"]), float(entry["radius"]),
                            float(entry["start_rad"]), float(entry["sweep_rad"])))
        elif kind == "circle":
            out.append(_arc(position, label, float(entry["cx"]),
                            float(entry["cy"]), float(entry["radius"]),
                            0.0, TAU))
        elif kind == "ellipse":
            cx, cy = float(entry["cx"]), float(entry["cy"])
            major, minor = float(entry["major"]), float(entry["minor"])
            angle = math.radians(float(entry.get("angle") or 0.0))
            points = []
            for step in range(curve_steps):
                t = TAU * step / curve_steps
                u, v = major * math.cos(t), minor * math.sin(t)
                points.append((cx + u * math.cos(angle) - v * math.sin(angle),
                               cy + u * math.sin(angle) + v * math.cos(angle)))
            out.extend(_chain(position, label, points, True))
        elif kind == "spline":
            raw = entry.get("pts") or []
            points = [(float(point[0]), float(point[1])) for point in raw]
            if len(points) >= 2:
                out.extend(_chain(position, label, points,
                                  bool(entry.get("closed"))))
        # Anything else (a point, a constraint) has no area to cross.
    return out


def _cross(first: tuple[float, float], second: tuple[float, float]) -> float:
    return first[0] * second[1] - first[1] * second[0]


def _sub(first: tuple[float, float],
         second: tuple[float, float]) -> tuple[float, float]:
    return (first[0] - second[0], first[1] - second[1])


def _line_line(p1: tuple[float, float], p2: tuple[float, float],
               q1: tuple[float, float], q2: tuple[float, float],
               eps: float = 1e-9) -> list[tuple[float, float]]:
    d1 = _sub(p2, p1)
    d2 = _sub(q2, q1)
    denominator = _cross(d1, d2)
    if abs(denominator) < eps:
        # Parallel or collinear. An overlap is a real defect too, but telling it
        # apart needs interval arithmetic on both lines; it is not reported here
        # rather than reported wrongly.
        return []
    offset = _sub(q1, p1)
    t = _cross(offset, d2) / denominator
    u = _cross(offset, d1) / denominator
    if -eps <= t <= 1.0 + eps and -eps <= u <= 1.0 + eps:
        return [(p1[0] + t * d1[0], p1[1] + t * d1[1])]
    return []


def _on_arc(point: tuple[float, float], center: tuple[float, float],
            radius: float, start: float, sweep: float,
            eps: float = 1e-7) -> bool:
    angle = math.atan2(point[1] - center[1], point[0] - center[0])
    if sweep >= 0:
        delta = (angle - start) % TAU
        return delta <= sweep + eps or delta >= TAU - eps
    delta = (start - angle) % TAU
    return delta <= -sweep + eps or delta >= TAU - eps


def _line_arc(p1: tuple[float, float], p2: tuple[float, float],
              center: tuple[float, float], radius: float,
              start: float, sweep: float) -> list[tuple[float, float]]:
    """Segment against the whole circle, then keep what lies on the arc."""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    fx, fy = p1[0] - center[0], p1[1] - center[1]
    a = dx * dx + dy * dy
    if a < 1e-18:
        return []
    b = 2.0 * (fx * dx + fy * dy)
    c = fx * fx + fy * fy - radius * radius
    discriminant = b * b - 4.0 * a * c
    if discriminant < 0.0:
        return []
    root = math.sqrt(discriminant)
    hits: list[tuple[float, float]] = []
    for t in ((-b - root) / (2.0 * a), (-b + root) / (2.0 * a)):
        if -1e-9 <= t <= 1.0 + 1e-9:
            point = (p1[0] + t * dx, p1[1] + t * dy)
            if _on_arc(point, center, radius, start, sweep):
                hits.append(point)
    return hits


def _arc_arc(one: dict[str, Any], other: dict[str, Any]) -> list[tuple[float, float]]:
    (x1, y1), r1 = one["c"], one["r"]
    (x2, y2), r2 = other["c"], other["r"]
    dx, dy = x2 - x1, y2 - y1
    distance = math.hypot(dx, dy)
    if distance < 1e-12:
        # Concentric: either the same circle (overlap, not reported) or none.
        return []
    if distance > r1 + r2 + 1e-9 or distance < abs(r1 - r2) - 1e-9:
        return []
    along = (r1 * r1 - r2 * r2 + distance * distance) / (2.0 * distance)
    height_sq = r1 * r1 - along * along
    if height_sq < 0.0:
        return []
    height = math.sqrt(height_sq)
    mid = (x1 + along * dx / distance, y1 + along * dy / distance)
    offset = (-dy * height / distance, dx * height / distance)
    hits = []
    for point in ((mid[0] + offset[0], mid[1] + offset[1]),
                  (mid[0] - offset[0], mid[1] - offset[1])):
        if (_on_arc(point, one["c"], r1, one["start"], one["sweep"])
                and _on_arc(point, other["c"], r2, other["start"], other["sweep"])):
            hits.append(point)
    return hits


def _shared_end(one: dict[str, Any], other: dict[str, Any],
                tol: float) -> bool:
    for left in (one["a"], one["b"]):
        for right in (other["a"], other["b"]):
            if math.hypot(left[0] - right[0], left[1] - right[1]) <= tol:
                return True
    return False


def _near_any_end(point: tuple[float, float], segment: dict[str, Any],
                  tol: float) -> bool:
    return any(math.hypot(point[0] - end[0], point[1] - end[1]) <= tol
               for end in (segment["a"], segment["b"]))


def crossings(segments: list[dict[str, Any]], tol: float = 1e-3,
              limit: int = 20) -> list[dict[str, Any]]:
    """Every place two segments cross, excluding where they simply meet.

    ``tol`` is the distance under which an endpoint counts as shared - an
    outline is a chain, so touching ends are the normal case. A crossing AT an
    endpoint of only one of the two (a T junction) is kept: that is a real
    defect and it is how a callout line through a face reads.

    WHY ``tol`` IS 1e-3 AND NOT 1e-6. It was 1e-6, and the 2026-11-12 report got
    four "crossings" that all sat on ONE point, (84.0001, 25.9999) - the shared
    shoulder-to-hook corner of a 21-element profile whose largest junction gap
    was 0.0004 mm. Two effects put ~1e-4 between the computed intersection and
    the endpoint the record holds: the junction itself is not exact, and the
    point comes out of an arc/arc solve rather than out of the same arithmetic
    that produced the endpoints. At 1e-6 a legal G0 seam therefore read as a
    crossing. 1e-3 mm is two and a half times the widest seam measured and far
    below any real T junction (a segment ending ON another segment, not beside
    it), so it separates the two cases instead of guessing between them.
    """
    found: list[dict[str, Any]] = []
    for i in range(len(segments)):
        one = segments[i]
        for j in range(i + 1, len(segments)):
            other = segments[j]
            if one["kind"] == "line" and other["kind"] == "line":
                points = _line_line(one["a"], one["b"], other["a"], other["b"])
            elif one["kind"] == "line":
                points = _line_arc(one["a"], one["b"], other["c"], other["r"],
                                   other["start"], other["sweep"])
            elif other["kind"] == "line":
                points = _line_arc(other["a"], other["b"], one["c"], one["r"],
                                   one["start"], one["sweep"])
            else:
                points = _arc_arc(one, other)
            if not points:
                continue
            shared = _shared_end(one, other, tol)
            for point in points:
                if shared and (_near_any_end(point, one, tol)
                               or _near_any_end(point, other, tol)):
                    continue
                found.append({
                    "a": one["source"], "b": other["source"],
                    "label_a": one["label"], "label_b": other["label"],
                    "point": (round(point[0], 4), round(point[1], 4)),
                })
                if len(found) >= limit:
                    return found
    return found


def describe(found: list[dict[str, Any]], segment_count: int,
             limit: int = 20) -> str:
    """One sentence per crossing, or a statement that there were none."""
    if not found:
        return (f" The outline does not cross itself ({segment_count} "
                "segment(s) checked, from the elements this session drew).")
    parts = [
        f"'{item['label_a']}' crosses '{item['label_b']}' at "
        f"({item['point'][0]:.4f}, {item['point'][1]:.4f})"
        for item in found[:6]
    ]
    more = "" if len(found) <= 6 else f"; and {len(found) - 6} more"
    return (
        f" WARNING: THE OUTLINE CROSSES ITSELF in {len(found)} place(s): "
        + "; ".join(parts) + more
        + ". A self-crossing profile has no single inside, so pad and pocket "
        "either refuse it or build something that is not what was drawn - fix "
        "the crossing before the feature, not after. (Checked from the elements "
        "this session drew; CATIA cannot hand sketch coordinates back.)"
    )
