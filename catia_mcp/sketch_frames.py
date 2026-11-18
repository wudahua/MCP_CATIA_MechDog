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


def _unit(vector: list[float]) -> list[float] | None:
    """The unit vector along ``vector``, or None when it has no length."""
    norm = math.sqrt(sum(component * component for component in vector))
    if not norm > 1e-12:
        return None
    return [component / norm for component in vector]


def frame_of(placement: object) -> tuple[list[float], list[float], list[float]] | None:
    """``(origin, u, v)`` in world mm for a recorded placement, or None.

    ``u`` and ``v`` come back as UNIT vectors whatever length was passed in.
    They are directions, and ``catia_create_sketch`` builds the datum plane from
    their unit forms (``_frame_plane`` normalises, then uses a 10 mm baseline), so
    a caller's ``u=[1000, 0, 0]`` - a perfectly reasonable way to say "along +X"
    - describes the same plane either way and CATIA draws the same sketch.

    Not normalising here made every consumer of this function use u as if it were
    a displacement: ``point_world`` multiplied the sketch coordinate by 1000, and
    the drawn-error measurement of ``catia_sketch_spline`` then placed its probe
    points a thousand times too far away and reported the distance to them -
    99 900 mm for a quarter circle whose radius is 100 (2026-11-18 report, P0,
    reproduced on B30 2026-11-18: the same curve on a unit-u frame reports
    0.0000 mm). The sketch itself was always correct; only the arithmetic around
    it was wrong, which is why the pad still measured 78 539.8 mm3 -0.00002 %.
    """
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
        unit_u, unit_v = _unit(u), _unit(v)
        if unit_u is None or unit_v is None:
            return None
        return origin, unit_u, unit_v
    return None


def point_world(placement: object, x: float, y: float) -> list[float] | None:
    """World XYZ of the sketch coordinate ``(x, y)``, or None when unknown."""
    frame = frame_of(placement)
    if frame is None:
        return None
    origin, u, v = frame
    return [origin[i] + u[i] * float(x) + v[i] * float(y) for i in range(3)]


_AXES_SCRIPT = (
    # Getting a sketch's own axis system. ``Sketch.GetAbsoluteAxisData`` needs a
    # by-ref out array, so pywin32's late-bound call cannot use it: it answers
    # 「方法 GetAbsoluteAxisData 失败」 and ``Sketch.AbsoluteAxis`` 「找不到成员」
    # (measured on B30 2026-11-18, on an open and a closed sketch, one built on a
    # frame and one on the xy datum). The CATScript engine can, and answers
    # [origin, H, V] in part coordinates.
    #
    # ONE SKETCH PER CALL, on purpose. The obvious batch form - an array argument
    # and ``ReDim out(9 * n - 1)`` - fails on this build with a bare 「执行脚本
    # ... 发生意外」 (measured 2026-11-18): the array of objects arrives, but the
    # ReDim/assign on a variant array does not survive the round trip. A Selection
    # argument does work, and was measured too; it is not used here because the
    # call happens inside a delete, where the selection is the caller's tool.
    "Public Function catia_mcp_axis(sketch)\n"
    "    Dim a(8)\n"
    "    sketch.GetAbsoluteAxisData a\n"
    "    catia_mcp_axis = a\n"
    "End Function\n"
)


def read_absolute_axes(app: object, sketches: list[object]
                       ) -> list[tuple[list[float], list[float], list[float]]] | None:
    """``(origin, H, V)`` in part coordinates for each sketch, or None.

    All-or-nothing on purpose: a partial answer would let a caller compare three
    sketches and silently skip the fourth, and the question this answers ("is
    another sketch standing on this plane") must not be answered half-wise.

    Measured on B30 2026-11-18: a sketch built on a frame plane at
    origin (0, 0, 1000) reports ``[0, 0, 1000, 1, 0, 0, 0, 1, 0]``, and a second
    sketch built ON that frame by name reports the identical array - which is the
    only way this build can be asked where a sketch sits.
    """
    if not sketches:
        return []
    answer: list[tuple[list[float], list[float], list[float]]] = []
    for sketch in sketches:
        try:
            flat = list(app.SystemService.Evaluate(_AXES_SCRIPT, 0,
                                                   "catia_mcp_axis", [sketch]))
        except Exception:  # noqa: BLE001 - the caller decides what unknown means
            return None
        if len(flat) != 9:
            return None
        values = [float(value) for value in flat]
        answer.append((values[0:3], values[3:6], values[6:9]))
    return answer


def plane_signature(axes: tuple[list[float], list[float], list[float]]
                    ) -> tuple[list[float], list[float]] | None:
    """``(point, unit normal)`` for one sketch's axis system, or None."""
    origin, h, v = axes
    normal = [h[1] * v[2] - h[2] * v[1],
              h[2] * v[0] - h[0] * v[2],
              h[0] * v[1] - h[1] * v[0]]
    return (origin, unit) if (unit := _unit(normal)) is not None else None


def same_plane(first: tuple[list[float], list[float],
                            list[float]] | None,
               second: tuple[list[float], list[float], list[float]] | None,
               tolerance_mm: float = 1e-4) -> bool | None:
    """Whether two sketches sit on the SAME plane, or None when that is unknown.

    Same plane = parallel normals and no offset between them. The H axis is not
    compared: a sketch rotated about the normal is still on the same plane, and
    still loses its support if that plane goes.

    ``None`` (an axis system could not be read) is not ``False``: a caller that
    deletes anything on the strength of this answer must treat unknown as "might
    be", so the returned None has to survive every ``if`` on the way.
    """
    if first is None or second is None:
        return None
    first_point, first_normal = first
    second_point, second_normal = second
    dot = sum(a * b for a, b in zip(first_normal, second_normal))
    if abs(dot) < 1.0 - 1e-9:
        return False
    offset = sum((b - a) * n for a, b, n in zip(first_point, second_point,
                                                first_normal))
    return abs(offset) <= tolerance_mm
