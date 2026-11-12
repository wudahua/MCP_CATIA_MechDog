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


def _near_any_end(point: tuple[float, float], segment: dict[str, Any],
                  tol: float) -> bool:
    return any(math.hypot(point[0] - end[0], point[1] - end[1]) <= tol
               for end in (segment["a"], segment["b"]))


def crossings(segments: list[dict[str, Any]], tol: float = 1e-3,
              limit: int = 20) -> list[dict[str, Any]]:
    """Every place two segments meet apart from a shared corner.

    Each entry carries ``kind``:

    ``"crossing"``
        The intersection is in the INTERIOR of both segments - the outline
        really crosses over itself. This is the defect.
    ``"touch"``
        The intersection sits at an ENDPOINT of one of them: a T junction, or a
        segment ending a hair past where the next one starts. Nothing is crossed
        over; material simply joins there. Normal for text strokes (the serif
        bar of an ``I`` ends on its stem), and not wrong for a profile either -
        but a T junction makes the outline branched, so it is not one closed
        region and a pad on it does something other than what was drawn.

    Why the two are separated (2026-11-12 report): all of its four "crossings"
    were legal seams at one shoulder corner, and the letter ``I`` in ``CATIA``
    reported ``line #21 crosses line #22`` where the serif bar ends on the stem.
    One category called "crosses itself" cannot describe both, and the hard
    sentence ("a self-crossing profile has no single inside") is false for the
    second.

    ``tol`` is the distance under which a point counts as being AT an endpoint.
    It was 1e-6, which the same report caught: the seam there was 0.0043 mm and a
    computed arc/arc intersection lands ~1e-4 from the endpoint the record
    holds, so a legal G0 seam read as a crossing. 1e-3 mm is two and a half times
    the widest seam measured when this was set (0.0004 mm) and far below any real
    T junction's own error - in a real T the point IS an endpoint, to within the
    arithmetic, so widening it cannot hide one.

    THE PRICE OF THAT WIDTH, said out loud (2026-11-12 acceptance, P3-B): a real
    crossing that happens to land within ``tol`` of a segment end is downgraded to
    ``touch``, or dropped altogether when it is that close to an end of BOTH
    segments. At the default 1e-3 mm the window is 1 micron of geometry, but the
    caller can widen ``tol`` and must then read this sentence as a warning rather
    than a footnote: at tol = 1 mm, any crossing within 1 mm of an end is no
    longer reported as a crossing. The rule that keeps this honest is that ``tol``
    must stay well under the smallest feature in the profile - it is a noise
    threshold, not a design tolerance.
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
            for point in points:
                at_one = _near_any_end(point, one, tol)
                at_other = _near_any_end(point, other, tol)
                if at_one and at_other:
                    # A shared corner: this is what a chain looks like.
                    continue
                found.append({
                    "a": one["source"], "b": other["source"],
                    "label_a": one["label"], "label_b": other["label"],
                    "point": (round(point[0], 4), round(point[1], 4)),
                    "kind": "touch" if (at_one or at_other) else "crossing",
                })
                if len(found) >= limit:
                    return found
    return found


def describe(found: list[dict[str, Any]], segment_count: int,
             limit: int = 20) -> str:
    """One sentence per case, keeping crossings and T junctions apart."""
    real = [item for item in found if item.get("kind") == "crossing"]
    touches = [item for item in found if item.get("kind") == "touch"]

    parts: list[str] = []
    if not real and not touches:
        return (f" The outline does not cross itself ({segment_count} "
                "segment(s) checked, from the elements this session drew).")
    if real:
        listed = [
            f"'{item['label_a']}' crosses '{item['label_b']}' at "
            f"({item['point'][0]:.4f}, {item['point'][1]:.4f})"
            for item in real[:6]
        ]
        more = "" if len(real) <= 6 else f"; and {len(real) - 6} more"
        parts.append(
            f" WARNING: THE OUTLINE CROSSES ITSELF in {len(real)} place(s): "
            + "; ".join(listed) + more
            + ". A self-crossing profile has no single inside, so pad and pocket "
            "either refuse it or build something that is not what was drawn - fix "
            "the crossing before the feature, not after.")
    if touches:
        listed = [
            f"'{item['label_a']}' ends on '{item['label_b']}' at "
            f"({item['point'][0]:.4f}, {item['point'][1]:.4f})"
            for item in touches[:6]
        ]
        more = "" if len(touches) <= 6 else f"; and {len(touches) - 6} more"
        parts.append(
            f" NOTE: {len(touches)} place(s) where one element ENDS on another: "
            + "; ".join(listed) + more
            + ". That is a T junction (or an element ending a hair past the next "
            "one's start), not a self-crossing - nothing is crossed over, so this "
            "is not why a pad would fail. It does mean the outline is BRANCHED "
            "rather than one closed loop, which is fine for engraving along the "
            "strokes and is not fine as a pad profile.")
    return "".join(parts) + (" (Checked from the elements this session drew; "
                             "CATIA cannot hand sketch coordinates back.)")
