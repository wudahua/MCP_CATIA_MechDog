"""Where a sketch's own (x, y) sits in the part - one table, three callers.

The sketcher records a placement when it creates a sketch, the GSD side turns a
recorded 2D outline into global XYZ, and part design measures distances inside
the sketch plane. All three need the same answer, and having it twice is how the
sketch copy and the plane-normal tool ended up not fitting each other
(2026-11-09). So the table lives here.

Measured on B30 2026-10-25 by padding a 20 x 6 rectangle on each datum plane and
reading the resulting box back: xy -> (0..20, 0..6, 25), yz -> (25, 0..20, 0..6),
zx -> (0..6, 25, 0..20). That is what fixes the axis order of each base - zx maps
H to Z and V to X, which is the mapping everybody gets wrong once.

A plane built by ``catia_gsd_plane_angle`` has no readable frame on this build
(``GetFirstAxis`` / ``GetSecondAxis`` / ``GetPosition`` all answer 「方法 … 失败」:
they take by-ref out-parameters, the same wall as ``Sketch.GetAbsoluteAxisData`` -
measured 2026-11-09 on a 44-member typeinfo dump). Its frame is therefore
RECONSTRUCTED from the arguments it was built with: the reference plane's own
frame, rotated about the axis by the angle. That reconstruction is a claim, not a
reading, so callers that publish coordinates check it against a measurement
before trusting it.
"""
from __future__ import annotations

import math

Vector = tuple[float, float, float]

# base -> (u, v, normal).  origin = normal * distance for the main planes.
BASES: dict[str, tuple[Vector, Vector, Vector]] = {
    "xy": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "yz": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
    "zx": ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
}

ORIGIN_AXIS: dict[str, Vector] = {
    "x": (1.0, 0.0, 0.0),
    "y": (0.0, 1.0, 0.0),
    "z": (0.0, 0.0, 1.0),
}


def rotate(vector: Vector, axis: Vector, degrees: float) -> Vector:
    """Rodrigues rotation of ``vector`` about ``axis`` by ``degrees``."""
    length = math.sqrt(sum(c * c for c in axis))
    if length == 0:
        return vector
    kx, ky, kz = (c / length for c in axis)
    cosine, sine = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    dot = kx * vector[0] + ky * vector[1] + kz * vector[2]
    cross = (ky * vector[2] - kz * vector[1],
             kz * vector[0] - kx * vector[2],
             kx * vector[1] - ky * vector[0])
    return (vector[0] * cosine + cross[0] * sine + kx * dot * (1 - cosine),
            vector[1] * cosine + cross[1] * sine + ky * dot * (1 - cosine),
            vector[2] * cosine + cross[2] * sine + kz * dot * (1 - cosine))


def plane_angle_frame(base: str, axis: Vector, degrees: float
                      ) -> tuple[Vector, Vector] | None:
    """``(u, v)`` of a plane that is ``base`` rotated by ``degrees`` about ``axis``.

    None when the base is not one of the three main planes - a rotation of an
    unknown frame is an unknown frame.
    """
    frame = BASES.get(str(base).strip().lower())
    if frame is None:
        return None
    u, v, _normal = frame
    return rotate(u, axis, degrees), rotate(v, axis, degrees)


def frame_of(placement: object) -> tuple[list[float], list[float], list[float]] | None:
    """``(origin, u, v)`` in world mm for a recorded placement, or None."""
    if not isinstance(placement, dict):
        return None
    base = str(placement.get("base") or "").lower()
    if base in BASES:
        u, v, normal = BASES[base]
        try:
            distance = float(placement.get("distance") or 0.0)
        except (TypeError, ValueError):
            return None
        return [c * distance for c in normal], list(u), list(v)
    if base == "frame":
        try:
            origin = [float(c) for c in placement["origin"]]
            u = [float(c) for c in placement["u"]]
            v = [float(c) for c in placement["v"]]
        except (KeyError, TypeError, ValueError):
            return None
        if len(origin) != 3 or len(u) != 3 or len(v) != 3:
            return None
        return origin, u, v
    return None


def point_world(placement: object, x: float, y: float) -> list[float] | None:
    """World XYZ of the sketch coordinate ``(x, y)``, or None when unknown."""
    frame = frame_of(placement)
    if frame is None:
        return None
    origin, u, v = frame
    return [origin[i] + u[i] * float(x) + v[i] * float(y) for i in range(3)]
