"""Sketcher tools for CATIA V5.

2D sketch creation and editing: lines, circles, rectangles, arcs, splines, constraints.
All dimensions are in millimeters. CATIA COM API uses millimeters natively.
"""

from __future__ import annotations

import json
import math
from typing import Any, Sequence

from catia_mcp.connection import CATIAConnection
from catia_mcp.fonts import supported as font_charset
from catia_mcp.fonts import text_to_strokes
from catia_mcp.geometry2d import crossings, describe, segments_of
from catia_mcp.results import ArgumentRejected, ArgumentOutOfRange, CodedError


def _pair(value: Any, complaint: str) -> list[float]:
    """``[x, y]`` from whatever shape the caller sent, or a clear refusal."""
    if isinstance(value, dict):
        value = [value.get("x"), value.get("y")]
    if value is None:
        raise ArgumentRejected(f"{complaint}; got nothing.",
                               code="BAD_ARGUMENT", stage="validate")
    try:
        numbers = [float(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise ArgumentRejected(f"{complaint}; got {value!r}.",
                               code="BAD_ARGUMENT", stage="validate") from exc
    if len(numbers) != 2:
        raise ArgumentRejected(
            f"{complaint}; got {len(numbers)} number(s).",
            code="BAD_ARGUMENT", stage="validate")
    return numbers


def _on_circle(center: Sequence[float], radius: float,
               degrees: float) -> list[float]:
    """The point at ``degrees`` on the circle, counter-clockwise from +X."""
    radians = math.radians(degrees)
    return [center[0] + radius * math.cos(radians),
            center[1] + radius * math.sin(radians)]


def _point_gap(first: Sequence[float], second: Sequence[float]) -> float:
    return math.dist(first, second)


def _fmt_point(point: Sequence[float]) -> str:
    return f"({point[0]:.4f}, {point[1]:.4f})"


def _stroke_data(strokes: list[list[list[float]]], include: bool,
                 limit: int = 600) -> str:
    """The polylines as data, so a caller can put them somewhere else.

    ``catia_text_strokes`` draws into a sketch and used to answer with counts
    only, so the 2026-11-11 report had to import ``catia_mcp.fonts`` to get the
    same numbers - which works for a caller who can read this repository and not
    for one talking over MCP. The limit keeps a long title from turning a reply
    into a wall of numbers; what is left over is COUNTED, never dropped silently.
    """
    if not include:
        return ""
    printed: list[str] = []
    used = 0
    left = 0
    for stroke in strokes:
        if used + len(stroke) > limit:
            left += len(stroke)
            continue
        printed.append("[" + ", ".join(f"[{float(x):.4f}, {float(y):.4f}]"
                                       for x, y in stroke) + "]")
        used += len(stroke)
    text = (" POINTS (sketch mm, same coordinates the sketch got): "
            + ", ".join(printed) + ".")
    if left:
        text += (f" {left} further point(s) were not printed because this reply "
                 f"stops at {limit}; call catia_text_strokes on fewer characters or "
                 "read catia_mcp.fonts.text_to_strokes directly for all of them.")
    return text


def _stroke_data(strokes: list[list[list[float]]], include: bool,
                 limit: int = 600) -> str:
    """The polylines as data, so a caller can put them somewhere else.

    ``catia_text_strokes`` draws into a sketch and used to answer with counts
    only, so the 2026-11-11 report had to import ``catia_mcp.fonts`` to get the
    same numbers - which works for a caller who can read this repository and not
    for one talking over MCP. The limit keeps a long title from turning a reply
    into a wall of numbers; what is left over is COUNTED, never dropped silently.
    """
    if not include:
        return ""
    printed: list[str] = []
    used = 0
    left = 0
    for stroke in strokes:
        if used + len(stroke) > limit:
            left += len(stroke)
            continue
        printed.append("[" + ", ".join(f"[{float(x):.4f}, {float(y):.4f}]"
                                       for x, y in stroke) + "]")
        used += len(stroke)
    text = (" POINTS (sketch mm, same coordinates the sketch got): "
            + ", ".join(printed) + ".")
    if left:
        text += (f" {left} further point(s) were not printed because this reply "
                 f"stops at {limit}; call catia_text_strokes on fewer characters or "
                 "read catia_mcp.fonts.text_to_strokes directly for all of them.")
    return text
from catia_mcp.sketch_frames import plane_angle_frame, point_world
from catia_mcp.results import (
    ArgumentOutOfRange,
    ArgumentRejected,
    PreconditionRejected,
    operation_result,
)
from catia_mcp.topo_search import topology_reference


def _vector_text(values: list[float]) -> str:
    """``"(0, 0, 1)"`` - a direction as it would be typed back in."""
    return "(" + ", ".join(f"{float(c):g}" for c in values) + ")"


# Plane name mapping
PLANE_MAP = {
    "xy": "PlaneXY",
    "yz": "PlaneYZ",
    "zx": "PlaneZX",
    "xz": "PlaneZX",  # alias
}


class SketcherTools:
    """Tools for 2D sketch operations in CATIA V5."""

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection
        self._active_sketch: Any | None = None
        self._active_factory: Any | None = None
        # Endpoints of the profile as drawn, in sketch coordinates. Kept
        # because the COM API will not give them back: see _draw_arc.
        self._profile_ends: list[tuple[float, float]] = []
        # What this session drew into each sketch, by sketch name. The only
        # read-back that survives closing a sketch, because it comes from the
        # arguments rather than from CATIA (see the note in _get_geometry).
        self._drawn: dict[str, list[dict[str, Any]]] = {}

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "catia_create_sketch",
                "description": (
                    "Create a new 2D sketch and open it for editing. The reply names "
                    "the axes, because they are NOT the intuitive ones: xy is "
                    "H = X / V = Y / normal = Z, yz is H = Y / V = Z / normal = X, and "
                    "zx is H = Z / V = X / normal = Y. So a shaft revolved about the "
                    "sketch's V axis spins about world Y, Z and X respectively; put a "
                    "revolved part on the plane whose V axis is the world axis the "
                    "spec names. Note also that CATIA's 'top' plane is zx (normal Y) "
                    "while SolidWorks' Top is XZ - the same words point at different "
                    "planes. The support "
                    "is a main datum plane ('xy', 'yz', 'zx'), optionally offset "
                    "along its normal, or a planar face of the solid that already "
                    "exists. Close the sketch with catia_close_sketch before "
                    "creating 3D features."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "plane": {
                            "description": (
                                "Sketch support. Either a main plane name "
                                "('xy' front, 'yz' right, 'zx' top) together with "
                                "the flat 'offset', or one of "
                                "{'type': 'offset', 'base': 'xy', 'distance': 40} "
                                "and {'type': 'face', 'index': 3}. Use the "
                                "offset form for any face parallel to a main "
                                "plane - a spec that says 'on the z=530 face' is "
                                "plane='xy' with offset=530, and its sketch "
                                "coordinates are then the global ones. Use the "
                                "face form only for a face that is not parallel "
                                "to a main plane, or when the sketch must stay "
                                "attached to the solid and follow it; 'index' is "
                                "a 1-based Face.N from catia_list_faces, and the "
                                "coordinates you draw are in that face's own "
                                "frame, which cannot be read back - a wrong "
                                "guess cuts nothing rather than failing. Or "
                                "{'type': 'frame', 'origin': [x,y,z], 'u': [..], "
                                "'v': [..]} for a plane that is not parallel to any "
                                "main plane: the sketch's H axis becomes your u and "
                                "V becomes your v, so the (u, v) numbers from the "
                                "spec are used unchanged (measured, not "
                                "approximated: a frame sketch lands exactly on "
                                "origin + u*du + v*dv). Reach for this instead of "
                                "rotating a main plane - the rotation cannot be "
                                "read back, and 'nearly parallel' is not parallel "
                                "over a long part. A datum plane BY NAME is also "
                                "accepted - any name, not just the three main "
                                "planes: catia_gsd_plane_offset, "
                                "_plane_normal_to_curve, _plane_angle, _plane_"
                                "3points and _plane_2lines all produce one. The "
                                "name is looked up in the tree, and a name that "
                                "is not a plane is refused with the datum planes "
                                "this part does have. Why this matters: an angle "
                                "datum plane is the one support a hand-written "
                                "frame cannot replace cheaply - a frame has to be "
                                "recomputed from cos/sin every time the angle "
                                "changes, and the frame itself is not a tree "
                                "element anyone can see or reuse."
                            ),
                            "anyOf": [
                                {
                                    "type": "string",
                                    # Deliberately NOT an enum: an enum here made
                                    # the sentence above false for two releases.
                                    # The schema rejected 'Plane3_36' before the
                                    # server ever saw it - "must be equal to one
                                    # of the allowed values" - while the
                                    # implementation has resolved datum-plane
                                    # names since 0.3.83. A caller following the
                                    # description was told the description was a
                                    # lie (reported 2026-11-09, twice).
                                },
                                {
                                    "type": "object",
                                    "properties": {
                                        "type": {
                                            "type": "string",
                                            "enum": ["offset", "face", "frame"],
                                        },
                                        "origin": {
                                            "type": "array",
                                            "items": {"type": "number"},
                                            "minItems": 3,
                                            "maxItems": 3,
                                            "description": (
                                                "For type='frame': the plane origin "
                                                "[x, y, z], in GLOBAL coordinates "
                                                "(millimetres, the assembly/document "
                                                "system - not the sketch's own)."),
                                        },
                                        "u": {
                                            "type": "array",
                                            "items": {"type": "number"},
                                            "minItems": 3,
                                            "maxItems": 3,
                                            "description": (
                                                "For type='frame': the sketch's H "
                                                "direction, as a global vector. "
                                                "Does not have to be unit length "
                                                "(it is normalized)."),
                                        },
                                        "v": {
                                            "type": "array",
                                            "items": {"type": "number"},
                                            "minItems": 3,
                                            "maxItems": 3,
                                            "description": (
                                                "For type='frame': the sketch's V "
                                                "direction, as a global vector. The "
                                                "plane NORMAL is u x v, right-hand "
                                                "rule, and the sketch's H/V axes are "
                                                "u and v exactly as given - so a "
                                                "profile drawn at u 0..10, v 0..4 "
                                                "pads 2 mm along u x v. To put the "
                                                "material on the other side, reverse "
                                                "v (or u) rather than flipping the "
                                                "profile: a reversed v flips the "
                                                "normal, while a mirrored profile "
                                                "changes which side of the sketch "
                                                "the geometry occupies. Reported "
                                                "2026-11-01: a side lip landed at "
                                                "+Z where -Z was meant, and nothing "
                                                "in the schema said which way the "
                                                "normal pointed."),
                                        },
                                        "base": {
                                            "type": "string",
                                            "enum": ["xy", "yz", "zx"],
                                            "description": "For type='offset': the plane to offset from. Default 'xy'.",
                                        },
                                        "distance": {
                                            "type": "number",
                                            "description": "For type='offset': mm along the base plane normal. Negative goes the other way.",
                                        },
                                        "index": {
                                            "type": "integer",
                                            "description": "For type='face': 1-based Face.N from catia_list_faces.",
                                        },
                                    },
                                    "required": ["type"],
                                },
                            ],
                            "default": "xy",
                        },
                        "offset": {
                            "type": "number",
                            "description": (
                                "Offset from that plane along its normal, in mm. "
                                "Negative goes the opposite way. This is how a feature "
                                "at z=362.5 on an xy sketch is placed: plane='xy', "
                                "offset=362.5. Omit for the plane itself. Ignored "
                                "when 'plane' is an object."
                            ),
                        },
                    },
                },
            },
            {
                "name": "catia_close_sketch",
                "description": (
                    "Close the active sketch and return to Part Design. "
                    "Must be called after finishing sketch geometry before applying 3D features."
                    "The echo names EVERY loose endpoint and the gap width at each "
                    "one, not just the closest pair: point lists rounded to 0.001 mm "
                    "break at several joints at once, and CATIA's own error ('open "
                    "polyline or an arc chain with a gap') never says where. A gap "
                    "of 0.0007 mm is enough to kill the pad - make the shared "
                    "coordinate literally identical."
                    "SCOPE OF THE ECHO: the Profile line counts what THIS session drew in THIS document. After reopening a file it cannot report the sketch's contents (the coordinates are not readable back), so an empty echo is not an empty sketch - and catia_gsd_spline(element=...) refuses the same sketches for the same reason."
                    "Set structured=true to get the same information as data instead of as a sentence - data.breaks[] holds every loose pair with its gap in mm, and it cannot be truncated (see that argument's description). Default false returns the text, unchanged."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "structured": {
                            "type": "boolean",
                            "description": (
                                "Return a machine-readable envelope instead of "
                                "the sentence. data.breaks[] lists every loose "
                                "endpoint pair with its gap in mm, in full - the "
                                "sentence is what a client cuts off (1242 chars "
                                "on a six-arc outline, 2026-11-06), so the list "
                                "was unreadable exactly when it mattered. "
                                "data.breaks.closed is true (nothing loose), "
                                "false (see the list), or null (no coordinates "
                                "were recorded in this session - a sketch "
                                "reopened from disk - which says nothing about "
                                "the sketch). Default false keeps the text reply, "
                                "so existing callers are unaffected."
                            ),
                        },
                    },
                },
            },
            {
                "name": "catia_sketch_line",
                "description": (
                    "Draw a line in the active sketch from (x1, y1) to (x2, y2). "
                    "Coordinates in mm. Set construction=true to make it a "
                    "construction line - needed for a revolution axis, or any "
                    "reference geometry that must not become part of the profile."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "x1": {"type": "number", "description": "Start X coordinate (mm)"},
                        "y1": {"type": "number", "description": "Start Y coordinate (mm)"},
                        "x2": {"type": "number", "description": "End X coordinate (mm)"},
                        "y2": {"type": "number", "description": "End Y coordinate (mm)"},
                        "construction": {
                            "type": "boolean",
                            "description": (
                                "Draw as construction geometry (excluded from the "
                                "profile). Default false."
                            ),
                            "default": False,
                        },
                    },
                    "required": ["x1", "y1", "x2", "y2"],
                },
            },
            {
                "name": "catia_sketch_rectangle",
                "description": (
                    "Draw a rectangle in the active sketch defined by two opposite corners. "
                    "Creates 4 lines forming a closed profile. Coordinates in mm. "
                    "ONE call draws the COMPLETE rectangle - calling it twice draws a "
                    "second rectangle in the same sketch, and overlapping or nested "
                    "rectangles self-intersect (a rectangular frame is four "
                    "catia_sketch_line calls, not two rectangle calls). "
                    "catia_close_sketch warns when the closed loops it recorded overlap."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "x1": {"type": "number", "description": "First corner X (mm)"},
                        "y1": {"type": "number", "description": "First corner Y (mm)"},
                        "x2": {"type": "number", "description": "Opposite corner X (mm)"},
                        "y2": {"type": "number", "description": "Opposite corner Y (mm)"},
                    },
                    "required": ["x1", "y1", "x2", "y2"],
                },
            },
            {
                "name": "catia_sketch_centered_rectangle",
                "description": (
                    "Draw a rectangle centered at (cx, cy) with given width and height. "
                    "Coordinates and dimensions in mm."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "cx": {"type": "number", "description": "Center X (mm)", "default": 0},
                        "cy": {"type": "number", "description": "Center Y (mm)", "default": 0},
                        "width": {"type": "number", "description": "Width in mm"},
                        "height": {"type": "number", "description": "Height in mm"},
                    },
                    "required": ["width", "height"],
                },
            },
            {
                "name": "catia_sketch_circle",
                "description": "Draw a circle in the active sketch. Coordinates and radius in mm.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "cx": {"type": "number", "description": "Center X (mm)", "default": 0},
                        "cy": {"type": "number", "description": "Center Y (mm)", "default": 0},
                        "radius": {"type": "number", "description": "Radius in mm"},
                    },
                    "required": ["radius"],
                },
            },
            {
                "name": "catia_sketch_ellipse",
                "description": (
                    "Draw a full ellipse in the active sketch: centre, semi-major, "
                    "semi-minor, and the angle of the major axis. The 2026-11-11 "
                    "report built a slanted handle hole from a 28-point polyline "
                    "because 'no ellipse primitive exists' - it does: Factory2D has "
                    "CreateClosedEllipse(cx, cy, majorX, majorY, majorR, minorR) and "
                    "CreateEllipse with start/end parameters as well (37 members, "
                    "read on B30). This wraps the closed one, so the hole is one "
                    "curve instead of 28 flat walls."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "cx": {"type": "number", "description": "Centre X (mm)",
                               "default": 0},
                        "cy": {"type": "number", "description": "Centre Y (mm)",
                               "default": 0},
                        "major": {"type": "number",
                                  "description": "Semi-major radius in mm (the long "
                                                 "one)."},
                        "minor": {"type": "number",
                                  "description": "Semi-minor radius in mm (the short "
                                                 "one)."},
                        "angle": {"type": "number",
                                  "description": ("Direction of the MAJOR axis, in "
                                                  "degrees counter-clockwise from "
                                                  "+X. Default 0."),
                                  "default": 0},
                        "construction": {"type": "boolean", "default": False,
                                         "description": "Draw as construction "
                                                        "geometry."},
                    },
                    "required": ["major", "minor"],
                },
            },
            {
                "name": "catia_sketch_arc",
                "description": (
                    "Draw a circular arc. Give EITHER start_angle/end_angle (degrees, "
                    "counter-clockwise from +X) OR p1/p2 - the two endpoints - plus "
                    "direction. The endpoints form is the one reverse-engineered point "
                    "lists use: they give 'centre (cx, cy) R r, (x1,y1) -> (x2,y2)' and "
                    "no angles at all, and converting by hand is where a 240-degree "
                    "major arc gets drawn as a 120-degree minor one (reported "
                    "2026-10-25: 1 of 236 arcs in the source data was a 240-degree "
                    "arc, and treating it as minor put the boss on the wrong side). "
                    "The reply always states the SWEPT angle, because 'from 10 to 250' "
                    "does not tell a reader whether it swept 240 or -120 degrees. "
                    "BOTH FORMS HONOUR `direction` AND `major`, and without major "
                    "the SHORT way round wins: start_angle 90 to end_angle 0 draws "
                    "the 90-degree arc the clockwise way (not 270 degrees), and the "
                    "reply says which way it went. Pass major=true when the long "
                    "arc really is wanted. start_angle equal to end_angle is "
                    "refused with DEGENERATE_ARC - that pair is one point, not an "
                    "arc, and catia_sketch_circle is the tool for a full circle."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "cx": {"type": "number", "description": "Center X (mm)"},
                        "cy": {"type": "number", "description": "Center Y (mm)"},
                        "radius": {"type": "number", "description": "Radius (mm)"},
                        "start_angle": {"type": "number", "description": "Start angle (degrees)"},
                        "end_angle": {"type": "number", "description": "End angle (degrees)"},
                        "p1": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 2,
                            "maxItems": 2,
                            "description": (
                                "Start point [x, y] (mm). Use with p2 instead of "
                                "start_angle/end_angle."
                            ),
                        },
                        "p2": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 2,
                            "maxItems": 2,
                            "description": "End point [x, y] (mm). Use with p1.",
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["ccw", "cw"],
                            "default": "ccw",
                            "description": (
                                "Which way round the circle the arc runs, from p1 "
                                "to p2 OR from start_angle to end_angle - it "
                                "applies to BOTH forms (until 0.3.93 the angle "
                                "form silently ignored it, reported 2026-11-10). "
                                "Without major the short way round wins, so a "
                                "direction that would go the long way is "
                                "overridden and the reply says so."
                            ),
                        },
                        "major": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Allow an arc longer than a half circle. False by "
                                "default, and deliberately: with p1/p2 given, "
                                "'ccw' from a point at -71 deg to one at -75 deg "
                                "means 355.6 deg the long way round, which is "
                                "almost never what was meant - it is what produced "
                                "a 1608 mm arc where 20 mm was wanted, and the "
                                "shaft built on it failed three steps later with a "
                                "bare 'UpdateObject 失败' (reported 2026-11-01). "
                                "When this flips the arc, the reply says so and "
                                "names both sweeps. It applies to both forms: with "
                                "two angles it keeps the sweep you asked for instead "
                                "of flipping to the short way. It has NO effect on a "
                                "request that is already a half circle or less: the "
                                "sweep is the same either way, so setting it on a "
                                "short arc changes nothing (reported 2026-11-01 as a "
                                "possible defect, which it is not)."
                            ),
                        },
                    },
                    "required": ["cx", "cy", "radius"],
                },
            },
            {
                "name": "catia_sketch_spline",
                "description": (
                    "Draw a spline through a list of control points. "
                    "Each point is [x, y] in mm. With closed=true the result is ONE "
                    "curve, which is what makes a sketch usable as a loft section "
                    "(a polygon of separate lines is several curves and is not). "
                    "IT IS NOT A POLYLINE, and the difference is not small: the curve "
                    "bulges outward between the points, worst across the short side. "
                    "Measured on B30 2026-10-25: a closed spline through the corners "
                    "of a 20 x 12 rectangle padded to 24.04 x 21.46 (+20 % / +79 %); a "
                    "six-point aerofoil-like outline padded +29.3 % in volume against "
                    "+0.48 % for the same six points joined with straight lines. So: "
                    "if the profile is a SPECIFICATION (an area, a chord, a thickness), "
                    "draw it with catia_sketch_line segments and accept that it will "
                    "not loft - or draw the spline and treat the envelope as something "
                    "to re-measure, not something to assume. And the bulge can be "
                    "cancelled: scale the points toward their centroid by "
                    "k = sqrt(target area / measured area) and rebuild once - a "
                    "nine-section blade at k=0.76 landed within 2 % of its 11 382 mm3 "
                    "target, where the unscaled spline was +70 %. "
                    "WHAT THIS CANNOT DO, and what to use instead: one spline cannot "
                    "be straight over one stretch and curved over the next - it "
                    "interpolates everywhere, so a straight run through intermediate "
                    "points comes out bowed. A profile like that is drawn element by "
                    "element with catia_sketch_profile (lines where it is straight, "
                    "arcs where it is not): measured on B30 2026-11-12, a 60x40 "
                    "stadium of two lines and two half circles padded to 36566.37 mm3 "
                    "= the analytic area, with no bowing anywhere."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "points": {
                            "type": "array",
                            "items": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 2,
                                "maxItems": 2,
                            },
                            "description": "List of [x, y] control points in mm",
                            "minItems": 2,
                        },
                        "closed": {
                            "type": "boolean",
                            "description": (
                                "Close the spline into ONE continuous curve by repeating "
                                "its first point (default: false). This matters for lofts: "
                                "a multi-entity outline - several lines, or a spline plus "
                                "a closing line - is accepted by AddSectionToLoft and then "
                                "fails at update. It is NOT a periodic spline: the sketch "
                                "spline object ('Spline2D') has IsPeriodic read-only and "
                                "no SetClosing, so repeating the first point is the only "
                                "way to close one in a sketch (measured 2026-11-10). For a "
                                "genuinely closed curve use catia_gsd_spline(closed=true), "
                                "which calls HybridShapeSpline.SetClosing."
                            ),
                            "default": False,
                        },
                    },
                    "required": ["points"],
                },
            },
            {
                "name": "catia_sketch_point",
                "description": (
                    "Create a point in the active sketch. Coordinates in mm. "
                    "A point on its own carries no material: catia_pad and "
                    "catia_pocket need a closed outline, and catia_hole takes the "
                    "bore from a circle. Construction points are the usual "
                    "locating/axis aid."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number", "description": "X coordinate (mm)"},
                        "y": {"type": "number", "description": "Y coordinate (mm)"},
                        "construction": {
                            "type": "boolean", "default": False,
                            "description": (
                                "Draw as construction geometry. Default false - a "
                                "point that a user pattern should use as a "
                                "locating position has to be real geometry."
                            ),
                        },
                    },
                    "required": ["x", "y"],
                },
            },
            {
                "name": "catia_sketch_constraint",
                "description": (
                    "Add a dimensional constraint to the active sketch. "
                    "Supported types: distance, radius, angle, coincidence, tangent, "
                    "perpendicular, parallel, horizontal, vertical, fix."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "description": "Constraint type",
                            "enum": [
                                "distance", "radius", "angle",
                                "coincidence", "tangent", "perpendicular",
                                "parallel", "horizontal", "vertical", "fix",
                            ],
                        },
                        "value": {
                            "type": "number",
                            "description": "Constraint value (mm or degrees). Required for distance, radius, angle.",
                        },
                        "geometry_index_1": {
                            "type": "integer",
                            "description": "Index of first geometry element (1-based, from sketch geometry list)",
                        },
                        "geometry_index_2": {
                            "type": "integer",
                            "description": "Index of second geometry element (for relational constraints)",
                        },
                    },
                    "required": ["type"],
                },
            },
            {
                "name": "catia_sketch_get_geometry",
                "description": (
                    "List a sketch's geometry elements - index, name, type, "
                    "construction flag - whether or not the sketch is open. "
                    "Omitting sketch_name falls back to the open sketch, then to "
                    "the last sketch created in this session. Coordinates are NOT "
                    "returned: a CATIA Point2D exposes no readable X/Y through "
                    "automation on this build, so 'what did I draw' is answered by "
                    "the element list plus the profile summary that "
                    "catia_close_sketch echoes."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "sketch_name": {"type": "string", "description": "Sketch to inspect (e.g. '草图.1'). Omit for the open or last-created sketch."},
                        "hide_construction": {"type": "boolean", "default": True,
                                              "description": "Collapse construction geometry (the two construction points every line creates) into a count"},
                    },
                },
            },
            {
                "name": "catia_sketch_edit",
                "description": (
                    "Reopen a CLOSED sketch for editing so geometry tools append to "
                    "it, instead of deleting the sketch and drawing it again. "
                    "OpenEdition on a closed sketch is verified on V5-6R2020 B30 "
                    "(measured 2026-10-24): existing elements stay, new ones are "
                    "appended, and closing the sketch updates the feature that "
                    "consumes it."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "sketch_name": {"type": "string", "description": "Sketch to reopen (e.g. '草图.1'). Omit for the open or last-created sketch."},
                    },
                },
            },
            {
                "name": "catia_sketch_delete_element",
                "description": (
                    "Delete ONE geometry element from a sketch, by index from "
                    "catia_sketch_get_geometry. This is the tool for 'the third line "
                    "is wrong': the sketch stays, the rest of the profile stays, and "
                    "a pad/pocket built on it survives the repair. Index 1 is the "
                    "absolute axis and cannot be deleted. The reply states the "
                    "element count before and after, and the sketch is left OPEN "
                    "for editing, so a second delete does not need "
                    "catia_sketch_edit first. Indices after the deleted one each "
                    "move down by one: re-read catia_sketch_get_geometry between "
                    "deletes, or you will delete a curve you meant to keep."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "description": "Element index from catia_sketch_get_geometry"},
                        "sketch_name": {"type": "string", "description": "Sketch to modify. Omit for the open or last-created sketch."},
                    },
                    "required": ["index"],
                },
            },
            {
                "name": "catia_sketch_copy",
                "description": (
                    "Re-create a sketch's geometry on ANOTHER plane - the "
                    "'Ctrl+C the profile, Ctrl+V on the next plane' step, which this "
                    "server had no way to do: the alternative was redrawing the "
                    "profile point by point, and two hand-typed copies are never "
                    "coordinate-identical (a loft section is sensitive to that). "
                    "The 2D coordinates are carried over unchanged and reinterpreted "
                    "on the new plane, exactly as a paste onto a different face "
                    "does; 'offset' moves them within that plane. "
                    "WHERE THE COORDINATES COME FROM: this session's record of what "
                    "the sketch tools drew - the sketch itself cannot hand them back "
                    "(a Point2D exposes no readable X/Y on this build). So a sketch "
                    "that was reopened from a file, or built outside these tools, "
                    "cannot be copied and is refused rather than approximated. "
                    "A CONSTRAINT THAT MOVED A POINT is also not reproduced: the "
                    "copy holds the coordinates that were passed in, not the ones "
                    "CATIA solved to. If the source is fully constrained and was "
                    "edited afterwards, check the copy with catia_sketch_get_geometry "
                    "before making it a loft section."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "Existing sketch to copy (must have been drawn by these tools in this session)",
                        },
                        "plane": {
                            "description": "Plane for the copy: 'xy'/'yz'/'zx', or a frame "
                                           "{type:'frame', origin:[x,y,z], u:[..], v:[..]}, or "
                                           "the name of a datum plane",
                        },
                        "offset": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 2,
                            "maxItems": 2,
                            "description": "[dx, dy] shift of the 2D coordinates within the new plane (default [0, 0])",
                        },
                        "name": {
                            "type": "string",
                            "description": "Name for the new sketch (default: a unique 草图.N)",
                        },
                        "target_body": {
                            "type": "string",
                            "description": (
                                "Body to put the copy in. Defaults to the active body. "
                                "This is the 'draw the label on its own solid' case: "
                                "the profile goes where you say instead of into "
                                "whichever body happens to be active, and that body "
                                "is left as the active one so the next "
                                "pad/pocket lands on the copy."
                            ),
                        },
                    },
                    "required": ["source", "plane"],
                },
            },
            {
                "name": "catia_sketch_strokes",
                "description": (
                    "Draw MANY polylines in ONE call. A letter outline is 8-20 "
                    "segments and a word is several letters, so the one-element "
                    "call is the bottleneck: a 2026-11-10 report spent dozens of "
                    "calls per glyph computing control points by hand. This takes "
                    "the whole outline at once - the shape of the data any font or "
                    "DXF outline arrives in - and draws it as STRAIGHT segments. It "
                    "does NOT interpolate: catia_sketch_spline through the same "
                    "points rounds the corners (that is what turned a hand-built "
                    "'A' into an omega), so a glyph drawn here keeps its corners "
                    "exactly as given. Each stroke is a list of [x, y] points in "
                    "the sketch's own 2D coordinates; repeat the first point at the "
                    "end to close one. Handy companions: catia_sketch_rectangle for "
                    "a box, and catia_close_sketch reports the outline it built. "
                    "A profile that is straight in places and CURVED in others is "
                    "not this tool: every segment here is a chord, so a curve comes "
                    "out faceted - use catia_sketch_profile, which draws each "
                    "element with the primitive for its own kind."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "strokes": {
                            "type": "array",
                            "description": (
                                "One entry per polyline: [[x0,y0],[x1,y1],...]. Each "
                                "needs at least 2 points; repeat the first point at "
                                "the end to close the loop."
                            ),
                            "items": {
                                "type": "array",
                                "items": {
                                    "type": "array",
                                    "items": {"type": "number"},
                                    "minItems": 2,
                                    "maxItems": 2,
                                },
                                "minItems": 2,
                            },
                        },
                        "construction": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Draw as construction geometry: guides that do not "
                                "become part of the profile (excluded from the "
                                "outline echo and from pad/pocket)."
                            ),
                        },
                    },
                    "required": ["strokes"],
                },
            },
            {
                "name": "catia_sketch_profile",
                "description": (
                    "Draw a MIXED profile in one call: straight runs and arcs, each "
                    "element drawn with the primitive for its own kind. This answers "
                    "the 2026-11-11 report's P0 'a profile cannot be straight on one "
                    "side and curved on the other': both halves of that claim are "
                    "true (catia_sketch_spline rounds the straight parts, "
                    "catia_sketch_strokes leaves the curves faceted) and neither is "
                    "the only route. MEASURED on B30 2026-11-12, a 60x40 stadium as "
                    "two lines plus two r=20 half circles: catia_close_sketch "
                    "answered '2 arc(s), 2 line(s), outline 245.664 mm' and the "
                    "10 mm pad came out at 36566.37 mm3 - exactly 60*40*10 + "
                    "pi*20^2*10. The reply reports every junction: a profile with a "
                    "0.05 mm gap is caught here, with both coordinates named, before "
                    "the pad is attempted. Segments are drawn in the order given and "
                    "each 'arc' starts at start_angle (or p1) and ends at end_angle "
                    "(or p2), so give them in the order the outline runs. It sweeps "
                    "along 'direction' and takes the SHORT way round (<= 180 deg) "
                    "unless major=true - so a clockwise arc given as start=0, "
                    "end=90 with direction cw is drawn 90 deg the other way, not 270. "
                    "When the endpoints are what you know, pass p1/p2: they cannot "
                    "be read backwards the way a start/end pair can."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "segments": {
                            "type": "array",
                            "minItems": 1,
                            "description": (
                                "The outline, element by element. Each entry is "
                                "{\"type\": \"line\", \"p1\": [x, y], \"p2\": [x, y]} "
                                "or {\"type\": \"arc\", \"center\": [x, y], \"radius\": "
                                "r, \"start_angle\": a, \"end_angle\": b, "
                                "\"direction\": \"cw\"|\"ccw\", \"major\": false} - an "
                                "arc may give p1/p2 instead of the two angles. HOW AN "
                                "ARC IS PLACED: it STARTS at start_angle (or p1) and "
                                "SWEEPS toward end_angle (or p2) along 'direction'; it "
                                "takes the SHORT way round (<= 180 deg) unless "
                                "major=true, so a direction that would sweep more than "
                                "half a circle is reversed and the reply says so. "
                                "Angles may be negative or beyond 360. PREFER p1/p2 "
                                "for a profile: the endpoints are what you actually "
                                "know, and the angles are derived from them - the "
                                "2026-11-12 report opened 15 of 21 junctions by "
                                "swapping start/end on the clockwise arcs."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "type": {"type": "string",
                                             "enum": ["line", "arc"]},
                                    "p1": {"type": "array",
                                           "items": {"type": "number"},
                                           "minItems": 2, "maxItems": 2},
                                    "p2": {"type": "array",
                                           "items": {"type": "number"},
                                           "minItems": 2, "maxItems": 2},
                                    "center": {"type": "array",
                                               "items": {"type": "number"},
                                               "minItems": 2, "maxItems": 2},
                                    "radius": {"type": "number"},
                                    "start_angle": {"type": "number"},
                                    "end_angle": {"type": "number"},
                                    "direction": {"type": "string",
                                                  "enum": ["ccw", "cw"]},
                                    "major": {"type": "boolean"},
                                },
                                "required": ["type"],
                            },
                        },
                        "tolerance": {
                            "type": "number",
                            "default": 0.05,
                            "description": (
                                "A junction gap in mm above which the profile is "
                                "reported as NOT CLOSED, with both coordinates. It "
                                "does not change what is drawn - CATIA closes gaps of "
                                "a few hundredths at update more often than not."
                            ),
                        },
                    },
                    "required": ["segments"],
                },
            },
            {
                "name": "catia_text_strokes",
                "description": (
                    "Draw TEXT into the open sketch, from a font that ships with "
                    "this server. This closes the gap the 2026-11-11 report named: "
                    "catia_sketch_strokes takes a whole outline, and what was "
                    "missing was the outlines - every glyph had to be typed in as "
                    "point sets by hand. "
                    "The face is the HERSHEY SIMPLEX set, which is a SINGLE-LINE "
                    "font: one stroke per stem. That matters more than it sounds. "
                    "An outline face gives every stem two walls a fraction of a "
                    "millimetre apart, and a groove 0.4 mm deep cannot resolve two "
                    "walls that close - it comes out as one ragged slot. It is also "
                    "the ancestor of the SHX lettering CATIA's own drafting uses, so "
                    "the result reads as engineering lettering. "
                    "'height' is the CAP HEIGHT (an H), which is what a drawing "
                    "means by '10 mm text', and the baseline lands on 'origin'. "
                    "Everything is straight segments, one stroke per line of the "
                    "glyph, never a spline - a spline through the same points rounds "
                    "the corners, which is how an earlier hand-built 'A' became an "
                    "omega. "
                    "ASCII 32-126 ONLY. A character with no glyph is SKIPPED and "
                    "NAMED in the reply, never replaced by a box, because a box "
                    "would engrave as something you did not ask for. "
                    "Next steps for engraved text: catia_close_sketch, then "
                    "catia_gsd_project onto the face, then catia_surface_engrave."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": (
                                "The text. A newline starts a second line, whose "
                                "baseline sits 'line_spacing' below the first."
                            ),
                        },
                        "height": {
                            "type": "number",
                            "default": 10.0,
                            "description": (
                                "Cap height in millimetres - the height of an H. "
                                "Not the width, and not the point size of any font "
                                "you might be thinking of."
                            ),
                        },
                        "include_points": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Also return the polylines themselves, in mm, so a "
                                "caller can put them ON a surface ("
                                "catia_curve_on_surface) instead of guessing where "
                                "the glyph is. The 2026-11-11 report had to import "
                                "catia_mcp.fonts directly to get these; up to 600 "
                                "points are printed, then a count of the rest."
                            ),
                        },
                        "spacing": {
                            "type": "number",
                            "description": (
                                "Clear gap between one glyph's box and the next, in "
                                "mm. Default 20 % of 'height'; the bearings alone are "
                                "1-2 font units, which reads as letters touching."
                            ),
                        },
                        "origin": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 2,
                            "maxItems": 2,
                            "default": [0, 0],
                            "description": (
                                "Where the first baseline starts, as [x, y] in the "
                                "sketch's own 2D millimetres. The glyphs sit to the "
                                "RIGHT of it and rise ABOVE it."
                            ),
                        },
                        "line_spacing": {
                            "type": "number",
                            "description": (
                                "Baseline-to-baseline distance for a second line, in "
                                "mm. Default 1.6 x height."
                            ),
                        },
                        "construction": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Draw as construction geometry: visible guides that "
                                "are excluded from the outline and from pad/pocket. "
                                "Off for text you mean to cut."
                            ),
                        },
                    },
                    "required": ["text"],
                },
            },
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str | dict[str, Any]:
        match tool_name:
            case "catia_create_sketch":
                return self._create_sketch(
                    arguments.get("plane", "xy"), arguments.get("offset")
                )
            case "catia_close_sketch":
                return self._close_sketch(bool(arguments.get("structured", False)))
            case "catia_sketch_line":
                return self._draw_line(
                    arguments["x1"], arguments["y1"],
                    arguments["x2"], arguments["y2"],
                    arguments.get("construction", False),
                )
            case "catia_sketch_rectangle":
                return self._draw_rectangle(
                    arguments["x1"], arguments["y1"],
                    arguments["x2"], arguments["y2"],
                )
            case "catia_sketch_centered_rectangle":
                return self._draw_centered_rectangle(
                    arguments.get("cx", 0), arguments.get("cy", 0),
                    arguments["width"], arguments["height"],
                )
            case "catia_sketch_circle":
                return self._draw_circle(
                    arguments.get("cx", 0), arguments.get("cy", 0),
                    arguments["radius"],
                )
            case "catia_sketch_ellipse":
                return self._draw_ellipse(
                    arguments.get("cx", 0), arguments.get("cy", 0),
                    arguments["major"], arguments["minor"],
                    arguments.get("angle", 0),
                    bool(arguments.get("construction", False)))
            case "catia_sketch_arc":
                return self._draw_arc(
                    arguments["cx"], arguments["cy"], arguments["radius"],
                    arguments.get("start_angle"), arguments.get("end_angle"),
                    p1=arguments.get("p1"), p2=arguments.get("p2"),
                    direction=arguments.get("direction") or "ccw",
                    major=bool(arguments.get("major", False)),
                )
            case "catia_sketch_spline":
                return self._draw_spline(
                    arguments["points"], arguments.get("closed", False),
                )
            case "catia_sketch_point":
                return self._draw_point(arguments["x"], arguments["y"],
                                        bool(arguments.get("construction", False)))
            case "catia_sketch_constraint":
                return self._add_constraint(arguments)
            case "catia_sketch_get_geometry":
                return self._get_geometry(arguments.get("sketch_name"),
                                          bool(arguments.get("hide_construction", True)))
            case "catia_sketch_edit":
                return self._edit_sketch(arguments.get("sketch_name"))
            case "catia_sketch_delete_element":
                return self._delete_element(arguments["index"],
                                            arguments.get("sketch_name"))
            case "catia_sketch_copy":
                return self._copy_sketch(arguments)
            case "catia_sketch_strokes":
                return self._draw_strokes(arguments["strokes"],
                                          bool(arguments.get("construction", False)))
            case "catia_sketch_profile":
                return self._draw_profile(
                    arguments["segments"],
                    float(arguments.get("tolerance", 0.05)))
            case "catia_text_strokes":
                return self._draw_text(arguments)
            case _:
                raise ArgumentRejected(f"Unknown sketcher tool: {tool_name}")

    def _ensure_sketch_open(self) -> None:
        if self._active_sketch is None:
            raise PreconditionRejected(
                "No active sketch. Use catia_create_sketch first to open a sketch."
            )

    def _reference_plane(self, part: Any, body: Any, plane_key: str,
                         offset: float | None) -> Any:
        """Reference to the sketch support: a main plane, or one offset from it.

        The offset plane is inserted into the body, not into a geometrical set:
        AddNewPad refuses a sketch whose support lives outside the body (E_FAIL
        from the factory), which is not an error message that points anywhere
        useful. Without offsets at all, every feature that sits at z=0 can be
        modelled and nothing else can - which is the wall a spec like a cylinder
        with a counterbore at z=352.5 hits immediately.
        """
        origin = part.OriginElements
        ref = part.CreateReferenceFromObject(getattr(origin, PLANE_MAP[plane_key]))
        if offset is None or abs(offset) < 1e-9:
            return ref

        factory = part.HybridShapeFactory
        # Negative offsets go the other way along the plane normal.
        plane = factory.AddNewPlaneOffset(ref, abs(offset), offset < 0)
        plane.Name = f"Datum_{plane_key}_{offset:g}"
        body.InsertHybridShape(plane)
        part.UpdateObject(plane)
        return part.CreateReferenceFromObject(plane)

    _PLANE_LABELS = {"xy": "XY (front)", "yz": "YZ (right)", "zx": "ZX (top)"}

    # Which global axis each sketch axis is. Measured on V5-6R2020 B30: on zx
    # the H axis runs along global Z and V along X, so a profile drawn "x by z"
    # comes out rotated 90 degrees - reported 2026-10-23 after a barrel built
    # with x=63.42 / z=60.96 instead of x=60.96. Sketch axis names are not
    # negotiable, but they are worth saying out loud in the reply.
    _AXES = {
        "xy": "H = X, V = Y, normal = Z",
        "yz": "H = Y, V = Z, normal = X",
        "zx": "H = Z, V = X, normal = Y",
    }

    @classmethod
    def _axis_note(cls, plane: Any, placement: dict[str, Any] | None = None) -> str:
        """What H/V mean here - and for a rotated plane, the actual vectors.

        A reconstructed frame is known at creation time (0.3.87 recorded it), so
        answering "unknown for this support" here while `catia_gsd_spline` hands
        over a checked frame two calls later was this server contradicting
        itself: a caller read the first sentence and gave up on the readback that
        would have worked (reported twice, 2026-11-09).
        """
        if isinstance(placement, dict) and placement.get("derived"):
            u = [float(c) for c in placement.get("u") or (1.0, 0.0, 0.0)]
            v = [float(c) for c in placement.get("v") or (0.0, 1.0, 0.0)]
            normal = [u[1] * v[2] - u[2] * v[1],
                      u[2] * v[0] - u[0] * v[2],
                      u[0] * v[1] - u[1] * v[0]]
            return (
                f"H/V/normal: H = {_vector_text(u)}, V = {_vector_text(v)}, normal = "
                f"{_vector_text(normal)} - reconstructed from {placement['derived']} "
                "(this build cannot read a sketch frame back), and checked "
                "against a measured distance the first time coordinates are asked "
                "for")
        if isinstance(plane, str) and plane.lower() in cls._AXES:
            return "H/V/normal: " + cls._AXES[plane.lower()]
        if isinstance(plane, dict):
            kind = str(plane.get("type", "")).lower()
            if kind == "frame":
                return ("H/V/normal: H = your u, V = your v, normal = u x v")
            if kind == "face":
                return ("H/V/normal: the face's own frame, which automation cannot "
                        "read back - prefer an offset datum plane")
            base = str(plane.get("base", "")).lower()
            if base in cls._AXES:
                return "H/V/normal: " + cls._AXES[base]
        return "H/V/normal: unknown for this support (see catia_sketch_get_geometry)"

    def _frame_plane(self, part: Any, body: Any, plane: dict[str, Any]) -> Any:
        """A datum plane from origin + two directions, so (u, v) mean what they say.

        Measured on B30 2026-10-25: a sketch on such a plane takes its H axis from
        ``u`` and its V axis from ``v`` exactly - a rectangle drawn at
        u 0..10 / v 0..4 with origin (17.525, 185.862, 0), u (0.995584, -0.093875,
        0), v (0, 0, -1) padded to x 17.525..27.6686, y 184.9233..187.8532,
        z -4..0, which is O + 10u + 4v plus 2 mm along u x v. That is the point of
        the form: a spec's numbers go in unchanged, with no frame conversion.

        Two traps, both measured: ``HybridShapeFactory.AddNewPlane`` does not
        exist (the three-point form is ``AddNewPlane3Points``), and everything the
        plane depends on has to be inserted INTO THE BODY. A plane in a geometrical
        set still lets the sketch be drawn, but the pad built on it cannot be
        filleted afterwards (measured 2026-10-26), and on B32 SP6 the pad itself
        creates and then fails to update - so the points go into the body too.
        """
        origin = plane.get("origin")
        first = plane.get("u")
        second = plane.get("v")
        for label, value in (("origin", origin), ("u", first), ("v", second)):
            if not isinstance(value, (list, tuple)) or len(value) != 3:
                raise ArgumentRejected(
                    f"plane of type 'frame' needs '{label}' as [x, y, z], got {value!r}."
                )
        o = [float(v) for v in origin]
        u = [float(v) for v in first]
        v = [float(v) for v in second]
        if max(abs(c) for c in u) < 1e-9 or max(abs(c) for c in v) < 1e-9:
            raise ArgumentRejected("'u' and 'v' must be direction vectors, not zero.")
        cross = [u[1] * v[2] - u[2] * v[1],
                 u[2] * v[0] - u[0] * v[2],
                 u[0] * v[1] - u[1] * v[0]]
        if max(abs(c) for c in cross) < 1e-9:
            raise ArgumentRejected("'u' and 'v' are parallel, so they span no plane.")

        def unit(vector: list[float], length: float = 10.0) -> list[float]:
            norm = sum(component * component for component in vector) ** 0.5
            return [component / norm * length for component in vector]

        # The three points are O, O + 10*u-hat and O + 10*v-hat. Ten millimetres
        # rather than one: a plane built on a 1 mm baseline is numerically the
        # same plane, but the sketch frame derived from it is less stable.
        along_u, along_v = unit(u), unit(v)
        corners = [
            o,
            [o[i] + along_u[i] for i in range(3)],
            [o[i] + along_v[i] for i in range(3)],
        ]
        factory = part.HybridShapeFactory
        # Points, plane and sketch all live in the BODY. A geometrical set looks
        # tidier and is what 0.3.20 did, but it poisons everything built on the
        # sketch: measured on B30 2026-10-26, a pad on a frame sketch with its
        # points in a set built 400 mm3 and then REFUSED a R1 fillet on its own
        # edge ("AddNewSolidEdgeFilletWithConstantRadius failed"), while the same
        # pad with points and plane inserted into the body took the fillet
        # ([applied Edge.1@R1]). One tree means one update order, and the B32 SP6
        # report of a frame pad that creates and never updates is the same fault
        # one step earlier.
        part.InWorkObject = body
        features = []
        points = []
        for coords in corners:
            point = factory.AddNewPointCoord(*coords)
            body.InsertHybridShape(point)
            part.UpdateObject(point)
            features.append(point)
            points.append(part.CreateReferenceFromObject(point))
        plane_feature = factory.AddNewPlane3Points(points[0], points[1], points[2])
        # A name of its own, not the same string for every frame in the body. On
        # B30, P04 of the 2026-11-06 build held FOUR features called
        # 'Datum_frame': the name is the only handle a caller has, and
        # Collection.Item(name) cannot tell four identical names apart.
        plane_name = self._free_feature_name(body, "Datum_frame")
        plane_feature.Name = plane_name
        body.InsertHybridShape(plane_feature)
        part.UpdateObject(plane_feature)
        features.append(plane_feature)
        # Hide the scaffolding. A datum plane and its three points are drawn as a
        # white diamond and three crosses ON TOP of the solid, and a finished
        # part's screenshot then reads as if it had geometry defects. The
        # 2026-11-07 report could not get rid of them at all: kind='construction'
        # /'sketch'/'datum_plane' returned success and left the picture
        # byte-identical, because that walk covers sketches and the three origin
        # planes and this plane lives in the body's HybridShapes; hiding by name
        # answered ARGUMENT_REJECTED for '平面.1' (its name, on a Chinese build,
        # is the one this server set - and there were four of them). So the
        # features are hidden where they are created, and the reply names them so
        # catia_set_visibility can bring one back.
        hidden = self._hide_datum(part, features)
        note = (f"; its datum plane '{plane_name}' and the 3 construction points "
                "behind it are hidden (show one again with "
                f"catia_set_visibility(name='{plane_name}', show=true))"
                if hidden else
                "; its datum plane and the 3 construction points behind it stayed "
                "VISIBLE - this build refused the visibility call, so hide them "
                f"with the CATIA UI (catia_set_visibility(name='{plane_name}') "
                "may work once the part has been saved and reopened)")
        return part.CreateReferenceFromObject(plane_feature), note

    @staticmethod
    def _free_feature_name(body: Any, base: str) -> str:
        """``base``, or the first free ``base.N``, inside one body."""
        taken: set[str] = set()
        for collection in ("Shapes", "Sketches", "HybridShapes"):
            try:
                items = getattr(body, collection)
            except Exception:     # noqa: BLE001 - a body without that collection
                continue
            for index in range(1, int(getattr(items, "Count", 0)) + 1):
                try:
                    taken.add(str(items.Item(index).Name).casefold())
                except Exception:  # noqa: BLE001 - one unreadable member
                    continue
        if base.casefold() not in taken:
            return base
        suffix = 2
        while f"{base}.{suffix}".casefold() in taken:
            suffix += 1
        return f"{base}.{suffix}"

    def _hide_datum(self, part: Any, features: list[Any]) -> bool:
        """Hide datum features in the part's own document; True when it took.

        ``SetShow`` is 0 = show / 1 = hide on B30 (2 is accepted and does
        nothing, which is how a set of GSD surfaces once stayed on screen while
        the tool reported hiding them). The selection is the DOCUMENT's, so this
        works whoever is in front.
        """
        try:
            selection = self.conn.active_document.Selection
        except Exception:  # noqa: BLE001 - no selection, nothing to hide with
            return False
        done = 0
        try:
            for feature in features:
                try:
                    selection.Clear()
                    selection.Add(feature)
                    selection.VisProperties.SetShow(1)
                    done += 1
                except Exception:  # noqa: BLE001 - one feature, not the rest
                    continue
        finally:
            try:
                selection.Clear()
            except Exception:  # noqa: BLE001 - a selection left behind
                pass
        self.conn.refresh_display()
        return done > 0

    @staticmethod
    def _describe_frame(plane: dict[str, Any]) -> str:
        o = ", ".join(f"{float(value):g}" for value in plane["origin"])
        return (f"datum plane through ({o}) with H = your u, V = your v, "
                "normal = u x v - sketch (u, v) coordinates are the numbers you "
                "gave, unchanged")

    def _describe_plane(self, plane_key: str, offset: float | None) -> str:
        where = self._PLANE_LABELS.get(plane_key, plane_key)
        if offset is not None and abs(offset) > 1e-9:
            where += f" offset {offset:g} mm"
        return where

    @staticmethod
    def _named_element(part: Any, name: str) -> Any:
        """The part-tree element called ``name``, or None.

        ``FindObjectByName`` reaches into the geometrical sets, which is why the
        same call is used for name collisions in gsd.py: a name it could not see
        would never report as taken.
        """
        try:
            found = part.FindObjectByName(name)
        except Exception:  # noqa: BLE001 - no such name, or the call is unsupported
            return None
        return found

    @staticmethod
    def _element_type_name(element: Any) -> str | None:
        """The automation type behind a tree node, or None if it will not say."""
        try:
            info = element._oleobj_.GetTypeInfo()
            return str(info.GetDocumentation(-1)[0])
        except Exception:  # noqa: BLE001 - not every node exposes type information
            return None

    def _datum_plane_names(self, part: Any) -> list[str]:
        """Names of the datum planes in this part, for an error message.

        A caller who passed a name needs to see what names exist - "Unknown
        plane" alone sends him looking for a spelling mistake that is not there.
        """
        found: list[str] = []

        def collect(container: Any) -> None:
            """Every hybrid shape in one collection whose type is a plane."""
            try:
                count = container.Count
            except Exception:  # noqa: BLE001
                return
            for index in range(1, count + 1):
                try:
                    node = container.Item(index)
                except Exception:  # noqa: BLE001
                    continue
                name = str(getattr(node, "Name", ""))
                kind = self._element_type_name(node) or ""
                if "Plane" in kind and name:
                    found.append(name)

        def walk(geosets: Any, depth: int) -> None:
            if depth > 4:
                return
            try:
                count = geosets.Count
            except Exception:  # noqa: BLE001
                return
            for index in range(1, count + 1):
                try:
                    geoset = geosets.Item(index)
                except Exception:  # noqa: BLE001
                    continue
                # A datum plane is a HybridShape INSIDE the set, not a child set:
                # walking HybridBodies alone found nothing and the message said
                # "Datum planes here: none" for a part that held two of them.
                try:
                    collect(geoset.HybridShapes)
                except Exception:  # noqa: BLE001 - a set with no shapes
                    pass
                try:
                    walk(geoset.HybridBodies, depth + 1)
                except Exception:  # noqa: BLE001 - not a nested set
                    pass

        try:
            walk(part.HybridBodies, 1)
        except Exception:  # noqa: BLE001 - a part with no geosets
            return []
        return found

    def _resolve_support(self, part: Any, body: Any, plane: Any,
                         offset: float | None) -> tuple[Any, str]:
        """Turn the `plane` argument into (reference, human description).

        Two shapes are accepted, because two different needs show up in a
        spec: 'on the z=530 face' is really an offset from xy (robust, and the
        sketch survives a rebuild), while a face that is not parallel to a
        main plane can only be reached through the solid's own topology.
        """
        if isinstance(plane, str):
            key = plane.lower()
            if key in PLANE_MAP:
                return (
                    self._reference_plane(part, body, key, offset),
                    self._describe_plane(key, offset),
                )
            # A datum plane (or any planar element) by NAME. 0.3.82's
            # catia_sketch_copy advertised "or the name of a datum plane" and
            # refused every one of them - so the two tools added that day could
            # not be chained: plane_normal_to_curve built a plane, and
            # sketch_copy answered "Unknown plane", which reads as a typo
            # (reported 2026-11-09, P1). The lookup is the same one every other
            # tool uses for an element name.
            element = self._named_element(part, plane)
            if element is not None:
                type_name = self._element_type_name(element) or ""
                if "Plane" not in type_name:
                    raise ArgumentRejected(
                        f"'{plane}' is a {type_name or 'element'} in this part, not "
                        "a plane, so a sketch cannot be supported by it. Pass a "
                        "datum plane, or a frame {type:'frame', origin:[..], "
                        "u:[..], v:[..]}.",
                        code="SUPPORT_NOT_PLANAR", stage="validate",
                    )
                return (part.CreateReferenceFromObject(element),
                        f"the datum plane '{plane}'")
            planes = self._datum_plane_names(part)
            raise ArgumentRejected(
                f"Unknown plane '{plane}': not 'xy'/'yz'/'zx', and no datum plane "
                f"with that name is in this part. Datum planes here: "
                f"{planes or 'none'} - build one with catia_gsd_plane_offset, "
                "catia_gsd_plane_normal_to_curve or catia_gsd_plane_3points, then "
                "pass its name.",
                code="PLANE_NOT_FOUND", stage="validate",
            )

        if not isinstance(plane, dict):
            raise ArgumentRejected(
                "plane must be 'xy'/'yz'/'zx' or an object with a 'type' of "
                f"'offset' or 'face', got {type(plane).__name__}."
            )

        kind = str(plane.get("type", "")).strip().lower()

        if kind == "frame":
            reference, datum_note = self._frame_plane(part, body, plane)
            return reference, self._describe_frame(plane) + datum_note

        if kind == "face":
            index = plane.get("index")
            if isinstance(index, bool) or not isinstance(index, int):
                raise ArgumentRejected(
                    "plane of type 'face' needs an integer 'index' - a 1-based "
                    "Face.N from catia_list_faces. The face order is CATIA's "
                    "own, so read the list before choosing."
                )
            found = topology_reference(
                self.conn, body, "Face", index,
                hint="Call catia_list_faces on this body and pick one of its indices.",
            )
            # Coordinates drawn on a face are in that face's own frame, and the
            # frame cannot be read back through automation (measuring the
            # 2D axis origin fails). Say so, because a wrong guess does not
            # error - the pocket simply removes nothing.
            return found.reference, (
                f"Face.{index} of the solid (sketch coordinates are in that "
                "face's own frame, not global - prefer the offset form when the "
                "face is parallel to a datum plane)"
            )

        if kind == "offset":
            base = str(plane.get("base", "xy")).strip().lower()
            if base not in PLANE_MAP:
                raise ArgumentRejected(
                    f"Unknown offset base '{plane.get('base')}'. Use 'xy', 'yz', or 'zx'."
                )
            distance = plane.get("distance", plane.get("offset", 0))
            if isinstance(distance, bool) or not isinstance(distance, (int, float)):
                raise ArgumentRejected(
                    "plane of type 'offset' needs a numeric 'distance' in mm "
                    f"(it may be negative), got {distance!r}."
                )
            distance = float(distance)
            return (
                self._reference_plane(part, body, base, distance),
                self._describe_plane(base, distance),
            )

        raise ArgumentRejected(
            f"Unknown plane type {plane.get('type')!r}. Use 'offset', 'face', or "
            "pass plane='xy' | 'yz' | 'zx'."
        )

    def rebuild_lost_sketch(self, part: Any, body: Any,
                            sketch_name: str | None) -> tuple[str | None, str]:
        """Re-create a sketch from the record made when it was drawn.

        A failed pad/pocket/hole rolls back, and whether CATIA takes the input
        sketch with it depends on the document's history: three failures of
        *identical* geometry in one acceptance run left the sketch in place twice
        and deleted it once (reported 2026-10-27). Disclosing that is not enough -
        the same error code then has two different recoveries, and a retry with
        the same name answers ``SKETCH_NOT_FOUND`` instead of ``NO_MATERIAL``. So
        the sketch is redrawn here from its recorded placement and drawing calls,
        the old name is aliased onto the new one, and a retry behaves exactly as
        it does in the case where the sketch survived.

        Returns ``(new name, "")`` or ``(None, why not)``. Nothing is guessed: a
        sketch whose support is not a datum plane, or which held an element this
        server cannot redraw from numbers alone (an arc, whose start angle is not
        in the record), is left to the caller with the coordinates in the message.
        """
        name = str(sketch_name or "")
        try:
            document = str(self.conn.active_document.Name)
        except Exception:  # noqa: BLE001
            document = "?"
        if not name:
            return None, "which sketch was lost could not be read back"
        record = (getattr(self.conn, "sketch_outlines", None) or {}).get(
            f"{document}::{name}") or {}
        elements = record.get("elements") or []
        placement = record.get("placement")
        if not elements:
            return None, ("this session holds no drawing record for it - a sketch "
                          "that came from a reopened file has none")
        if not placement:
            return None, ("it was drawn on a support that is not a datum plane, and "
                          "whose frame this server cannot reproduce")
        for element in elements:
            kind = str(element.get("kind"))
            if kind == "spline":
                # Replayable only from 0.3.81 on, when the record started keeping
                # the control points. An older record says how many points the
                # spline had and nothing about where they were, and guessing a
                # profile is worse than saying it cannot be done.
                if len(element.get("pts") or []) < 2:
                    return None, ("it held a spline whose record kept the point "
                                  "COUNT but not the coordinates (records written "
                                  "before 0.3.81), so there is nothing to redraw it "
                                  "from")
                continue
            if kind not in ("line", "rectangle", "circle", "point"):
                return None, (f"it held a {kind}, which cannot be redrawn from a "
                              "record")
        try:
            if str(placement.get("base")) == "frame":
                # The record stores the frame as ``base: "frame"`` (the form the
                # GSD side replays), while _resolve_support reads ``type``. It
                # used to be forwarded as-is, so re-creating a frame sketch
                # answered "Unknown plane type None" about a sketch whose
                # creation had just been accepted - the recovery path refusing a
                # plane type the creating path documents (reported 2026-11-07,
                # P5, on a 109-step run where the side section was a frame).
                support_ref = self._resolve_support(part, body, {
                    "type": "frame",
                    "origin": placement.get("origin"),
                    "u": placement.get("u"),
                    "v": placement.get("v"),
                }, None)[0]
            else:
                support_ref = self._resolve_support(
                    part, body, placement["base"], placement.get("distance"))[0]
        except Exception as exc:  # noqa: BLE001 - nothing was changed
            return None, f"its plane could not be re-created: {str(exc)[:90]}"
        sketch = None
        try:
            sketch = body.Sketches.Add(support_ref)
            factory = sketch.OpenEdition()
            for element in elements:
                self._replay_element(factory, element)
            sketch.CloseEdition()
            # Update the SKETCH, not the whole part (0.3.93). This runs on a part
            # that has just failed a feature - the reason the sketch is missing -
            # so `part.Update()` can still fail on whatever else is wrong, and it
            # did: the 2026-11-10 reply said "it could NOT be re-created ...
            # 方法 Update 失败" about a sketch whose drawing calls had all worked.
            # The rebuild is worth keeping even when the tree is still broken; the
            # sketch's own update is the one that says whether IT is sound.
            try:
                part.UpdateObject(sketch)
            except Exception:  # noqa: BLE001 - the part may be in error elsewhere
                # Nothing to lose by asking for the whole update: if it succeeds
                # the sketch is updated as well, and if it fails the caller learns
                # that from the feature that broke, not from this rebuild.
                try:
                    part.Update()
                except Exception:  # noqa: BLE001 - reported by the caller
                    pass
        except Exception as exc:  # noqa: BLE001
            try:
                if sketch is not None:
                    sketch.CloseEdition()
            except Exception:  # noqa: BLE001
                pass
            return None, f"redrawing it failed: {str(exc).splitlines()[0][:90]}"
        new_name = str(sketch.Name)
        # Keep the record under the new name too, so a second rollback on the
        # rebuilt sketch can rebuild it again instead of running out of record.
        fresh = self._outline_record(self._sketch_key(new_name))
        if fresh is not None:
            fresh["placement"] = placement
            fresh["elements"] = [dict(element) for element in elements]
        # `_drawn` is a SECOND store, and it is the one catia_sketch_get_geometry
        # reads for drawn_this_session. It was not migrated, so after a rebuild
        # the record stayed under the dead name: the read tool found nothing, the
        # loft's per-section point count (which can only come from this record -
        # CATIA cannot read a spline's points back) silently stopped comparing,
        # and the 0.3.77 safeguard was off exactly on the sections that had been
        # through a rollback (reported 2026-11-08, M6).
        self._drawn[self._sketch_key(new_name)] = [dict(element)
                                                   for element in elements]
        # One writer, one rule: every name that pointed at the sketch CATIA just
        # deleted is re-pointed at the rebuild, so a caller that still holds
        # 草图.2 keeps resolving however many rebuilds happen (reported 2026-10-27:
        # the mapping only held one hop, so the third retry answered
        # SKETCH_NOT_FOUND while the sketch was in the tree as 草图.4).
        linker = getattr(self.conn, "note_sketch_alias", None)
        if linker is not None:
            try:
                linker(name, new_name, document=document)
            except Exception:  # noqa: BLE001 - the mapping is a convenience
                pass
        else:
            aliases = getattr(self.conn, "sketch_aliases", None)
            if aliases is None:
                aliases = {}
                try:
                    self.conn.sketch_aliases = aliases
                except Exception:  # noqa: BLE001
                    aliases = None
            if aliases is not None:
                aliases[f"{document}::{name}"] = new_name
        try:
            self.conn.last_sketch = sketch
        except Exception:  # noqa: BLE001
            pass
        return new_name, ""

    @staticmethod
    def _replay_element(factory: Any, element: dict[str, Any]) -> None:
        """Redraw one recorded element. Only the kinds the record is exact for."""
        kind = str(element.get("kind"))
        if kind == "line":
            factory.CreateLine(element["x1"], element["y1"],
                               element["x2"], element["y2"])
        elif kind == "rectangle":
            x1, y1 = element["x1"], element["y1"]
            x2, y2 = element["x2"], element["y2"]
            for ax, ay, bx, by in ((x1, y1, x2, y1), (x2, y1, x2, y2),
                                   (x2, y2, x1, y2), (x1, y2, x1, y1)):
                factory.CreateLine(ax, ay, bx, by)
        elif kind == "circle":
            factory.CreateClosedCircle(element["cx"], element["cy"],
                                       element["radius"])
        elif kind == "point":
            point = factory.CreatePoint(element["x"], element["y"])
            try:
                point.Construction = bool(element.get("construction", False))
            except Exception:  # noqa: BLE001 - construction is the default
                pass
        elif kind == "spline":
            control = [[float(x), float(y)] for x, y in element.get("pts") or []]
            if len(control) < 2:
                raise PreconditionRejected(
                    "the record holds no control points for this spline, so it "
                    "cannot be redrawn")
            # Same closure rule as the drawing call: repeating the first point
            # keeps it ONE curve. Appending a line back to the start makes two
            # entities, and a loft section has to be a single curve.
            if element.get("closed") and len(control) >= 3:
                control.append(list(control[0]))
            points = [factory.CreatePoint(x, y) for x, y in control]
            factory.CreateSpline(points)

    @staticmethod
    def _shift_element(element: dict[str, Any], dx: float, dy: float) -> dict[str, Any]:
        """A copy of a recorded element moved by (dx, dy) in the sketch plane."""
        moved = dict(element)
        if dx == 0 and dy == 0:
            return moved
        kind = str(moved.get("kind"))
        if kind in ("line", "rectangle"):
            moved["x1"] = float(moved["x1"]) + dx
            moved["y1"] = float(moved["y1"]) + dy
            moved["x2"] = float(moved["x2"]) + dx
            moved["y2"] = float(moved["y2"]) + dy
        elif kind == "circle":
            moved["cx"] = float(moved["cx"]) + dx
            moved["cy"] = float(moved["cy"]) + dy
        elif kind == "point":
            moved["x"] = float(moved["x"]) + dx
            moved["y"] = float(moved["y"]) + dy
        elif kind == "spline":
            moved["pts"] = [[float(x) + dx, float(y) + dy]
                            for x, y in moved.get("pts") or []]
        return moved

    def _copy_sketch(self, args: dict[str, Any]) -> str:
        """Replay one sketch's recorded geometry into a new sketch on a plane.

        The tutorial step this exists for is "copy the profile, paste it on the
        next plane, 72 degrees round". The coordinates have to come from the
        RECORD: a Point2D exposes no readable X/Y on this build, so the sketch
        cannot be interrogated, only replayed. That is also why the limits are
        stated in the reply instead of being discovered later - a reopened sketch
        has no record, and a constraint that moved a point is not reproduced.
        """
        self.conn.ensure_connected()
        source = str(args["source"])
        plane = args["plane"]
        raw_offset = args.get("offset") or [0, 0]
        dx, dy = float(raw_offset[0]), float(raw_offset[1])

        elements = self._drawn.get(self._sketch_key(source))
        if not elements:
            recorded = sorted({key.split("::", 1)[-1] for key in self._drawn
                               if self._drawn.get(key)})
            raise ArgumentRejected(
                f"No drawn geometry is recorded for '{source}', so there is nothing "
                "to replay. The record only covers sketches drawn by these tools in "
                "THIS session: a sketch reopened from disk cannot be read back (a "
                "Point2D has no readable coordinates on this build), and copying it "
                "would mean inventing the geometry. "
                f"Sketches with a record now: {recorded or 'none'}.",
                code="SKETCH_NOT_RECORDED", stage="validate",
            )

        # A sketch still open for editing has to be closed first: creating the
        # copy opens a new edition, and two simultaneous OpenEdition sessions in
        # one body are not something this server tracks (the second would take
        # over _active_sketch and the first would be left open).
        source_note = ""
        active = getattr(self, "_active_sketch", None)
        if active is not None:
            try:
                if str(active.Name) == source:
                    self._close_sketch()
                    source_note = (" (the source was open for editing and was "
                                   "closed first)")
            except Exception:  # noqa: BLE001 - the copy is still worth trying
                source_note = ""

        # WHERE THE COPY LANDS (0.3.94). _create_sketch builds in the ACTIVE body,
        # so copying a profile meant for another solid put it back in the one you
        # were already in - the "label on its own body" case needed a
        # catia_set_active_body detour whose side effect (which body is active
        # afterwards) the caller then had to track. Here the body is named up
        # front and read back after the fact.
        body_note = ""
        wanted_body = args.get("target_body")
        if wanted_body:
            part = self.conn.get_active_part()
            body = self._named_body(part, str(wanted_body))
            try:
                part.InWorkObject = body
            except Exception:  # noqa: BLE001 - the parent read below is the check
                pass
            self.conn.active_body = body
            body_note = " in body '%s'" % body.Name

        self._create_sketch(plane=plane)
        if wanted_body:
            # ASK THE BODY, NOT THE SKETCH (0.3.95). 0.3.94 read
            # `sketch.Parent.Name` and compared it to the body's - but on B30
            # `Sketch.Parent` is the **Sketches collection**, whose Name is
            # literally "Sketches" (measured 2026-11-10: 草图.1 -> Sketches ->
            # 零件几何体 -> Bodies -> Part -> document), so the comparison could
            # never match and every single call ended with "the copy is in the
            # wrong body" while the copy was exactly where it was asked to go
            # (reported 2/2, with the pad landing in the right body at the right
            # volume). The question is membership - "is it in THIS body" - so it
            # is asked of that body's own collection.
            owner = self._body_holding(self.conn.get_active_part(),
                                       self._active_sketch)
            if owner is not None and owner != str(body.Name):
                body_note = (
                    " in body '%s' - WARNING: the copy is in '%s' instead, so the "
                    "switch did not take; do not pad from it until it is moved"
                    % (body.Name, owner)
                )
        target = str(self._active_sketch.Name)
        rename_note = ""
        wanted = args.get("name")
        if wanted:
            before = target
            try:
                self._active_sketch.Name = str(wanted)
                target = str(self._active_sketch.Name)
                rename_note = self._make_sketch_name_unique(
                    self.conn.get_active_part(), self._active_sketch)
                target = str(self._active_sketch.Name)
                self._rekey_records(before, target)
            except Exception as error:  # noqa: BLE001 - the copy keeps its name
                rename_note = (f" Could not rename it to '{wanted}' "
                               f"({str(error).splitlines()[0][:60]}).")

        factory = self._active_factory
        count = 0
        for element in elements:
            moved = self._shift_element(dict(element), dx, dy)
            self._replay_element(factory, moved)
            # Record it again, so the copy can itself be copied and used as a
            # loft section - a record that stops at the original makes the second
            # copy impossible for no reason.
            self._note_element(**moved)
            count += 1

        closed = self._close_sketch()
        where = f"offset by ({dx:g}, {dy:g}) mm" if (dx or dy) else "unchanged"
        return (
            f"Sketch '{target}' created as a copy of '{source}' "
            f"({count} element(s), 2D coordinates {where}) on the new plane"
            f"{body_note}{source_note}{rename_note}. " + str(closed)
            + self._copy_placement_note(target)
            + " The copy holds the coordinates that were PASSED IN, not the ones "
            "CATIA solved to: constraints on the original are not carried over, so "
            "if the source was constrained after drawing, check the copy with "
            "catia_sketch_get_geometry before using it as a loft section."
        )

    def _copy_placement_note(self, target: str) -> str:
        """Where the copy landed in global XYZ, when the frame is known.

        The 2026-11-09 report had to derive this by hand - a copy onto a plane
        rotated by 72 degrees moves a point at radius r by 2*r*sin(36), and the
        measured 7.0534 mm was the only evidence the copy landed on the right
        side. Printing the coordinates is cheaper than that derivation, and the
        reply says which frame they came from, because a reconstructed frame is
        a claim that has to be checked (the GSD side does that with a probe
        point when it hands coordinates over).
        """
        outlines = getattr(self.conn, "sketch_outlines", None)
        if not isinstance(outlines, dict):
            return ""
        record = outlines.get(self._sketch_key(target)) or {}
        placement = record.get("placement")
        if not isinstance(placement, dict):
            return (" Its 2D coordinates cannot be turned into global XYZ here "
                    "(no frame for that plane), so check the placement by "
                    "measuring the copy against the original.")
        points: list[list[float]] = []
        for element in record.get("elements") or []:
            for x, y in self._element_points(dict(element))[:2]:
                world = point_world(placement, x, y)
                if world:
                    points.append(world)
            if len(points) >= 3:
                break
        if not points:
            return ""
        listed = "; ".join(
            "(%g, %g, %g)" % tuple(round(c, 4) for c in point) for point in points)
        derived = placement.get("derived")
        if derived:
            return (f" Where it landed, computed from {derived}: {listed} mm. "
                    "That frame is reconstructed from the plane's construction, "
                    "not read back (this build cannot read a sketch frame), so "
                    "check it with catia_measure_distance against the original "
                    "before relying on it.")
        return f" Key points in global XYZ: {listed} mm."

    def _create_sketch(self, plane: Any = "xy", offset: float | None = None) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self.conn.get_active_part_body()

        ref, where = self._resolve_support(part, body, plane, offset)

        # Create the sketch on the plane
        sketches = body.Sketches
        sketch = sketches.Add(ref)
        rename_note = self._make_sketch_name_unique(part, sketch)

        # Open the sketch for editing
        self._active_sketch = sketch
        self._sketch_part = part
        self._active_factory = sketch.OpenEdition()
        # Where this sketch sits, so GSD can place its outline in 3D later.
        # Keyed by document too, and reset here: a record that is not cleared
        # would carry the previous sketch's elements into this one's echo.
        key = self._sketch_key(str(sketch.Name))
        self._drawn[key] = []
        try:
            self._last_geom_count = int(sketch.GeometricElements.Count)
        except Exception:  # noqa: BLE001 - spans are then simply unknown
            self._last_geom_count = None
        placement = self._placement_of(plane, offset)
        if placement is None:
            placement = self._angled_placement(plane)
        record = self._outline_record(key)
        if record is not None:
            record["placement"] = placement
            record["elements"] = []
        # Remember it by identity: body.Sketches is ordered by tree position,
        # not by creation, so a later feature cannot find "the sketch I just
        # made" by index. Recording it here is what makes pad/pocket/shallow
        # features land on the intended profile in a multi-sketch part.
        self.conn.last_sketch = sketch
        self._profile_ends = []
        # How many CLOSED curves were drawn (a circle, or a spline closed on
        # itself). They have no free end, so they contribute nothing to the
        # pairing - which is why a sketch holding only one circle answered
        # closed:null ("nothing recorded") instead of "closed". Reported
        # 2026-11-08, P1-1, as the same "the pairing does not see this kind of
        # element" problem.
        self._profile_closed_curves = 0

        return (f"Sketch '{sketch.Name}' created on {where}. "
                f"{self._axis_note(plane, placement)}. Ready for geometry."
                + rename_note)

    @classmethod
    def _all_sketch_names(cls, part: Any) -> list[str]:
        """Every sketch name in the PART: all bodies, and nested geometrical sets."""
        names: list[str] = []

        def collect(holder: Any, depth: int = 0) -> None:
            if depth > 4:
                return
            for collection in ("Sketches", "HybridSketches"):
                try:
                    items = getattr(holder, collection)
                except Exception:  # noqa: BLE001 - the holder has no such list
                    continue
                for i in range(1, items.Count + 1):
                    try:
                        names.append(str(items.Item(i).Name))
                    except Exception:  # noqa: BLE001 - one item, not the rest
                        continue
            try:
                sets = holder.HybridBodies
            except Exception:  # noqa: BLE001 - no nested sets here
                return
            for i in range(1, sets.Count + 1):
                try:
                    collect(sets.Item(i), depth + 1)
                except Exception:  # noqa: BLE001 - one set, not the rest
                    continue

        try:
            for i in range(1, part.Bodies.Count + 1):
                collect(part.Bodies.Item(i))
            for i in range(1, part.HybridBodies.Count + 1):
                collect(part.HybridBodies.Item(i))
        except Exception:  # noqa: BLE001 - an unreadable tree answers "unknown"
            return names
        return names

    def _body_holding(self, part: Any, sketch: Any) -> str | None:
        """Name of the body whose ``Sketches`` collection holds this sketch.

        NOT ``sketch.Parent.Name``: that is the collection ("Sketches"), one level
        below the body. Membership is the question a caller's "which body did it
        land in" actually asks, so it is asked of each body's own collection -
        identity first, name second, because a part can hold two sketches with the
        same name after a rollback.

        None means "could not tell", which is not "wrong body": an unreadable
        answer must never turn into a warning.
        """
        try:
            wanted = str(sketch.Name)
        except Exception:  # noqa: BLE001
            return None
        try:
            bodies = part.Bodies
        except Exception:  # noqa: BLE001
            return None
        rows: list[tuple[str, Any]] = []
        for i in range(1, int(getattr(bodies, "Count", 0)) + 1):
            try:
                body = bodies.Item(i)
                sketches = body.Sketches
            except Exception:  # noqa: BLE001
                continue
            for j in range(1, int(getattr(sketches, "Count", 0)) + 1):
                try:
                    rows.append((str(body.Name), sketches.Item(j)))
                except Exception:  # noqa: BLE001
                    continue
        # IDENTITY FIRST, ACROSS ALL BODIES. A part can hold two sketches with the
        # same name after a rollback, and a name match taken in body order would
        # answer with whichever came first - the wrong body, confidently.
        for name, entry in rows:
            try:
                if entry == sketch:
                    return name
            except Exception:  # noqa: BLE001
                continue
        for name, entry in rows:
            try:
                if str(entry.Name) == wanted:
                    return name
            except Exception:  # noqa: BLE001
                continue
        return None

    def _named_body(self, part: Any, name: str) -> Any:
        """An existing solid body by name, or a refusal that lists them.

        Part design keeps its own copy of this: the two tool classes do not call
        into each other (a Sketcher that needed PartDesignTools to name a body
        would fail on any connection that built only one of them).
        """
        bodies = part.Bodies
        for i in range(1, int(getattr(bodies, "Count", 0)) + 1):
            candidate = bodies.Item(i)
            if str(candidate.Name) == name:
                return candidate
        try:
            return bodies.Item(name)
        except Exception:  # noqa: BLE001 - the list is the useful part
            names = ", ".join(str(bodies.Item(i).Name)
                              for i in range(1, int(getattr(bodies, "Count", 0)) + 1))
            raise ArgumentRejected(
                f"No body named '{name}', so the copy has nowhere to go. Bodies: "
                f"{names or 'none'}.",
                code="BODY_NOT_FOUND", stage="validate",
            ) from None

    def _make_sketch_name_unique(self, part: Any, sketch: Any) -> str:
        """Rename a new sketch whose name the PART already uses, and say so.

        CATIA numbers a new sketch from its own counter and does not check for a
        conflict: after rollbacks, body switches and document changes one part
        can end up holding two ``草图.1`` and two ``草图.2`` (reported 2026-11-08,
        N2 - six sketches listed as 草图.1 / 草图.3 / 草图.2 / 草图.4 / 草图.1 /
        草图.2). Everything downstream is BY NAME - loft sections, pad's
        ``feature_name``, delete_feature, sketch_edit - so a duplicate silently
        re-points at the other object mid-session, and the drawing record keyed
        by ``document::name`` merges the two profiles, which is why
        ``catia_sketch_get_geometry`` then described a 4-line rectangle as a
        7-point spline.

        The rename is measured to work on B30 (``Sketch.Name`` is settable, and
        CATIA goes on numbering from its own counter, so this check has to run
        every time rather than once).
        """
        try:
            names = self._all_sketch_names(part)
            current = str(sketch.Name)
        except Exception:  # noqa: BLE001 - nothing to rename by
            return ""
        if names.count(current) <= 1:
            return ""
        suffix = 2
        while f"{current}#{suffix}" in names:
            suffix += 1
        renamed = f"{current}#{suffix}"
        try:
            sketch.Name = renamed
            try:
                part.UpdateObject(sketch)
            except Exception:  # noqa: BLE001 - the rename already took
                pass
        except Exception:  # noqa: BLE001 - read-only on some builds
            return (f" WARNING: '{current}' is also the name of another node in "
                    "this part and renaming this one failed - every by-name tool "
                    "may reach either of them, so refer to it by index or rename "
                    "it by hand before building on it")
        return (f" It was renamed to '{renamed}': the part already held a "
                f"'{current}', and every by-name tool would otherwise reach one "
                "of the two at random")

    # What counts as "the same endpoint" and what counts as a gap that will
    # still break a pad. A 3.5 um gap did (reported 2026-10-23), so the second
    # number is deliberately generous.
    _END_TOL = 1e-4
    _NEAR_TOL = 0.05

    def _note_ends(self, *points: tuple[float, float]) -> None:
        self._profile_ends.extend((float(x), float(y)) for x, y in points)

    def _note_element(self, kind: str, **params: Any) -> None:
        """Remember what was drawn, for the profile echo at close time.

        Each entry also carries ``_span``: the indices of the LIVE elements that
        this one draw created. Without it the record cannot follow a delete - and
        it did not (found 2026-11-09 re-probing a report): two lines, delete the
        second, and the record still held both, so ``catia_sketch_copy`` replayed
        a line the sketch no longer had and the copy's outline came out exactly
        twice the source's. That is the shape of the "2 spline(s)" report.
        """
        params.pop("_span", None)          # a replayed entry carries the source's
        try:
            name = str(self._active_sketch.Name)
        except Exception:  # noqa: BLE001 - the record is still useful
            name = "?"
        key = self._sketch_key(name)
        entry = {"kind": kind, **params}
        span = self._live_span()
        if span is not None:
            entry["_span"] = span
        self._drawn.setdefault(key, []).append(entry)
        # Same record on the connection, where catia_gsd_spline can reach it:
        # GSD needs the outline's coordinates to build a 3D spline through a
        # polygon's corners, and the sketch itself cannot hand them back (a
        # Point2D exposes no readable X/Y through automation on this build).
        record = self._outline_record(key)
        if record is not None:
            record["elements"].append(dict(entry))

    def _live_span(self) -> list[int] | None:
        """The live element indices one draw just added, or None if unknown."""
        try:
            count = int(self._active_sketch.GeometricElements.Count)
        except Exception:  # noqa: BLE001 - no sketch in hand
            return None
        previous = getattr(self, "_last_geom_count", None)
        self._last_geom_count = count
        if previous is None or count <= previous:
            return None
        return [previous + 1, count]

    def _forget_deleted_element(self, sketch_name: str, index: int) -> str:
        """Keep the drawing record in step with ``catia_sketch_delete_element``.

        Two answers, both honest: an entry that produced exactly one live element
        is dropped (the common case - a line, a circle, a spline) and the rest of
        the record still describes the sketch; anything else (a rectangle is ONE
        entry over four elements) cannot be redrawn faithfully, so the record is
        dropped whole rather than left lying about a sketch it no longer matches.
        """
        key = self._sketch_key(sketch_name)
        drawn = self._drawn.get(key) or []
        if not drawn:
            return ""
        spans = [entry.get("_span") for entry in drawn]
        if all(isinstance(span, list) and len(span) == 2 for span in spans):
            hits = [position for position, span in enumerate(spans)
                    if int(span[0]) <= index <= int(span[1])]
            if len(hits) == 1 and int(spans[hits[0]][1]) == int(spans[hits[0]][0]):
                dropped = drawn.pop(hits[0])
                spans.pop(hits[0])
                for span in spans:
                    if int(span[0]) > index:
                        span[0] = int(span[0]) - 1
                        span[1] = int(span[1]) - 1
                record = self._outline_record(key)
                if record is not None:
                    record["elements"] = [entry for entry
                                          in record.get("elements") or []
                                          if entry.get("_span") != dropped.get("_span")]
                return (f" The drawing record dropped that {dropped.get('kind')} "
                        "too, so a copy of this sketch now reproduces what is left.")
        self._drawn[key] = []
        record = self._outline_record(key)
        if record is not None:
            record["elements"] = []
        return (" The session's drawing record for this sketch no longer matched it "
                f"after removing element {index} (that shape is made of several "
                "elements, and CATIA cannot read the remaining coordinates back), "
                "so the record was dropped: catia_sketch_copy will refuse this "
                "sketch until it is drawn again, and the profile echo will be "
                "empty. The sketch itself is intact.")

    def _rekey_records(self, before: str, after: str) -> None:
        """Carry what was recorded under a sketch's old name to its new one.

        A rename splits one sketch's record in two, because every store here is
        keyed by name: the copy records its placement under the auto name
        ('草图.2'), is renamed a moment later, and then records its elements under
        the chosen name - so the GSD side was told about a sketch whose frame was
        under a different key and refused it ("has no recorded frame here").
        One fact, two records, same shape as the 0.3.82 duplicate-recorder bug.
        """
        if not before or not after or before == after:
            return
        stores = [self._drawn, getattr(self.conn, "sketch_outlines", None)]
        for store in stores:
            if not isinstance(store, dict):
                continue
            old_key, new_key = self._sketch_key(before), self._sketch_key(after)
            if old_key not in store:
                continue
            moved = store.pop(old_key)
            if new_key not in store:
                store[new_key] = moved
                continue
            target = store[new_key]
            if isinstance(target, dict) and isinstance(moved, dict):
                if not target.get("placement") and moved.get("placement"):
                    target["placement"] = moved["placement"]
                if moved.get("elements") and not target.get("elements"):
                    target["elements"] = moved["elements"]
            elif isinstance(target, list) and isinstance(moved, list):
                target.extend(moved)

    def _outline_record(self, key: str) -> dict[str, Any] | None:
        """The shared outline record for one sketch (None with no connection)."""
        outlines = getattr(self.conn, "sketch_outlines", None)
        if outlines is None:
            return None
        return outlines.setdefault(key, {"placement": None, "elements": []})

    def _sketch_key(self, name: str) -> str:
        """Document-scoped key for a sketch name.

        Every part names its first sketch '草图.1', so a bare name is not an
        identity: with one key per name, the profile echo of a fresh part listed
        the rectangle drawn in the PREVIOUS part, and the outline record handed
        GSD another document's corners (reported 2026-10-25, reproduced on B30 -
        a new part holding one circle echoed "1 circle(s), 1 rectangle(s)").
        Scoping by the document name fixes both, and a reopened file simply has
        no record instead of somebody else's.
        """
        try:
            document = str(self.conn.active_document.Name)
        except Exception:  # noqa: BLE001 - record it anyway, under a catch-all
            document = "?"
        return f"{document}::{name}"

    @classmethod
    def _placement_of(cls, plane: Any, offset: float | None) -> dict[str, Any] | None:
        """Where a new sketch sits, in terms GSD can turn into global XYZ.

        Only the datum-plane forms are answered, because only those have a frame
        this server knows: xy is (u, v, offset), yz is (offset, u, v) and zx is
        (v, offset, u) - measured on B30 2026-10-25 by padding a 20 x 6 rectangle
        on each plane and reading the box back. A sketch on a face, or on an
        element that is not a datum plane, gets None and the GSD side refuses
        rather than guessing a frame.
        """
        if isinstance(plane, str) and plane.lower() in cls._AXES:
            return {"base": plane.lower(), "distance": float(offset or 0.0)}
        if isinstance(plane, dict) and str(plane.get("type", "")).lower() == "frame":
            try:
                return {"base": "frame",
                        "origin": [float(v) for v in plane["origin"]],
                        "u": [float(v) for v in plane["u"]],
                        "v": [float(v) for v in plane["v"]]}
            except Exception:  # noqa: BLE001 - unreadable frame, no record
                return None
        if isinstance(plane, dict) and str(plane.get("type", "")).lower() == "offset":
            base = str(plane.get("base", "xy")).lower()
            if base in ("xy", "yz", "zx"):
                distance = plane.get("distance", plane.get("offset", 0))
                try:
                    return {"base": base, "distance": float(distance)}
                except (TypeError, ValueError):
                    return None
        return None

    def _angled_placement(self, plane: Any) -> dict[str, Any] | None:
        """The frame of a sketch on a datum plane this server built by rotation.

        ``catia_gsd_plane_angle`` records what its plane was made from, because
        the plane's own axes cannot be read back (``GetFirstAxis`` and friends
        take by-ref out-parameters - 44-member typeinfo dump, 2026-11-09). With
        that record the frame is reconstructible, and the 2026-11-09 report is
        explicit about why it matters: a sketch on a 36/72-degree plane is the
        whole point of the tutorial's Step 3-4, and until now nothing could turn
        its 2D coordinates back into XYZ to show it landed there.

        The record is a CLAIM, not a reading, so the ``derived`` note travels with
        it and the GSD side checks it against a measured distance before it
        publishes coordinates.
        """
        if not isinstance(plane, str):
            return None
        store = getattr(self.conn, "datum_plane_angles", None)
        if not isinstance(store, dict):
            return None
        spec = store.get(plane.strip())
        if not isinstance(spec, dict):
            return None
        try:
            frame = plane_angle_frame(str(spec["base"]), list(spec["axis"]),
                                      float(spec["angle"]))
        except (KeyError, TypeError, ValueError):
            return None
        if frame is None:
            return None
        u, v = frame
        axis_name = spec.get("axis_name") or "the axis"
        return {
            "base": "frame",
            # A plane built by rotation about an axis through the origin passes
            # through the origin, so the origin is exact rather than assumed.
            "origin": [0.0, 0.0, 0.0],
            "u": [round(c, 12) for c in u],
            "v": [round(c, 12) for c in v],
            "derived": (f"'{plane.strip()}' = the {spec['base']} plane rotated "
                        f"{float(spec['angle']):g} degrees about {axis_name}"),
        }

    @staticmethod
    def _element_points(item: dict[str, Any]) -> list[tuple[float, float]]:
        """Every point of one recorded element that bounds its extent.

        The bbox used to be computed from ``_profile_ends`` - the endpoints that
        are PAIRED at close time. For a single closed spline both of those are
        the same coordinate (it repeats its first point), so a 6 x 6 square came
        back as ``bbox (10,10) .. (10,10)``: a point (reported 2026-11-09, P2).
        The extent has to come from the geometry, not from the pairing table.
        """
        import math

        kind = str(item.get("kind"))
        if kind == "line":
            return [(float(item["x1"]), float(item["y1"])),
                    (float(item["x2"]), float(item["y2"]))]
        if kind == "rectangle":
            x1, y1 = float(item["x1"]), float(item["y1"])
            x2, y2 = float(item["x2"]), float(item["y2"])
            return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        if kind == "circle":
            cx, cy, r = float(item["cx"]), float(item["cy"]), float(item["radius"])
            return [(cx - r, cy - r), (cx + r, cy + r)]
        if kind == "point":
            return [(float(item["x"]), float(item["y"]))]
        if kind == "spline":
            return [(float(x), float(y)) for x, y in item.get("pts") or []]
        if kind == "arc":
            # Endpoints alone can under-report: an arc that sweeps past 0, 90,
            # 180 or 270 degrees reaches further out than both of its ends.
            if "start_rad" not in item:
                return []
            cx, cy = float(item["cx"]), float(item["cy"])
            radius = float(item["radius"])
            start = float(item["start_rad"])
            sweep = float(item.get("sweep_rad") or 0.0)
            low, high = (start, start + sweep) if sweep >= 0 else (start + sweep, start)
            points = [
                (cx + radius * math.cos(start),
                 cy + radius * math.sin(start)),
                (cx + radius * math.cos(start + sweep),
                 cy + radius * math.sin(start + sweep)),
            ]
            for quarter in (0.0, math.pi / 2, math.pi, 3 * math.pi / 2):
                turned = quarter
                while turned < low:
                    turned += 2 * math.pi
                if turned <= high:
                    points.append((cx + radius * math.cos(quarter),
                                   cy + radius * math.sin(quarter)))
            # Rounded and de-duplicated: cos(pi/2) is 6.1e-16, not 0, so the
            # same corner arrived twice with two spellings and a caller comparing
            # coordinates saw a point that was "not there".
            rounded = [(round(x, 9) + 0.0, round(y, 9) + 0.0) for x, y in points]
            return list(dict.fromkeys(rounded))
        return []

    def _profile_summary(self, sketch_name: str | None = None) -> str:
        """Echo the drawn profile: element counts, size, total outline length.

        This is the counter to "the profile was wrong and nothing said so": the
        2026-10-24 report lost two segments of a revolved profile and saw it only
        as a 32 % volume error after 14 more features. The numbers come from the
        drawing calls, because CATIA's own coordinates cannot be read back.
        """
        import math

        ends = self._profile_ends
        if sketch_name is None:
            try:
                sketch_name = str(self._active_sketch.Name)
            except Exception:  # noqa: BLE001
                sketch_name = None
        drawn = (self._drawn.get(self._sketch_key(sketch_name), [])
                 if sketch_name else [])
        if not ends and not drawn:
            return ""
        counts: dict[str, int] = {}
        length = 0.0
        bounds: list[tuple[float, float]] = []
        straight_spline = False
        for item in drawn:
            counts[item["kind"]] = counts.get(item["kind"], 0) + 1
            bounds.extend(self._element_points(item))
            if item["kind"] == "line":
                length += math.hypot(item["x2"] - item["x1"], item["y2"] - item["y1"])
            elif item["kind"] in ("arc", "circle"):
                length += item["radius"] * abs(item["sweep_rad"])
            elif item["kind"] == "rectangle":
                length += 2 * (abs(item["x2"] - item["x1"])
                               + abs(item["y2"] - item["y1"]))
            elif item["kind"] == "spline":
                # Straight-line sum through the control points, and the closing
                # segment included: a closed 4-point spline has FOUR sides, and
                # leaving the last one out is what turned 24 mm into 18 mm
                # (reported 2026-11-09, P2 - 18.000 = 3 x 6).
                control = [[float(x), float(y)] for x, y in item.get("pts") or []]
                if item.get("closed") and len(control) >= 3:
                    control = control + [control[0]]
                length += sum(math.hypot(control[i + 1][0] - control[i][0],
                                         control[i + 1][1] - control[i][1])
                              for i in range(len(control) - 1))
                straight_spline = True
        parts = []
        if counts:
            parts.append(", ".join(f"{n} {kind}(s)"
                                   for kind, n in sorted(counts.items())))
        if bounds:
            xs = [p[0] for p in bounds]
            ys = [p[1] for p in bounds]
            parts.append("bbox (%.4f, %.4f) .. (%.4f, %.4f)"
                         % (min(xs), min(ys), max(xs), max(ys)))
        if length:
            parts.append("outline %.3f mm" % length)
        text = " Profile: " + "; ".join(parts) + "." if parts else ""
        if straight_spline and length:
            text += (" The outline counts a spline as the straight line through its "
                     "control points, so it is a LOWER BOUND - the curve bulges "
                     "outside it, and the bbox is the control polygon's, not the "
                     "curve's.")
        return text

    def _loop_warning(self, sketch_name: str | None = None) -> str:
        """Warn when one sketch holds several closed loops that overlap.

        Reported 2026-10-26: four ``catia_sketch_rectangle`` calls were used to
        draw ONE rectangular frame, which stacked four rectangles in one sketch.
        ``close_sketch`` reported "4 rectangle(s); outline 440.000 mm" and said
        nothing, and the pad failed at update - so the self-intersection was only
        discovered through a failed feature. Two separate rectangles are a
        legitimate profile (pad both); two that overlap are not, and that is what
        this checks, because the bounding boxes are known from the drawing calls.
        """
        import math

        if sketch_name is None:
            try:
                sketch_name = str(self._active_sketch.Name)
            except Exception:  # noqa: BLE001
                sketch_name = None
        drawn = self._drawn.get(self._sketch_key(sketch_name), [])
        loops: list[dict[str, Any]] = []
        for item in drawn:
            if item["kind"] == "rectangle":
                left, right = sorted((item["x1"], item["x2"]))
                bottom, top = sorted((item["y1"], item["y2"]))
                loops.append({
                    "kind": "rectangle",
                    "box": (left, bottom, right, top),
                    "area": abs((right - left) * (top - bottom)),
                })
            elif item["kind"] == "circle":
                r = item["radius"]
                loops.append({
                    "kind": "circle",
                    "box": (item["cx"] - r, item["cy"] - r, item["cx"] + r, item["cy"] + r),
                    "area": math.pi * r * r,
                })
        if len(loops) < 2:
            return ""

        def inside(inner: tuple[float, ...], outer: tuple[float, ...]) -> bool:
            return (inner[0] >= outer[0] and inner[2] <= outer[2]
                    and inner[1] >= outer[1] and inner[3] <= outer[3])

        overlapping: list[tuple[int, int]] = []
        nested: list[tuple[int, int]] = []
        for i in range(len(loops)):
            for j in range(i + 1, len(loops)):
                a, b = loops[i]["box"], loops[j]["box"]
                if not (a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]):
                    continue
                # One box inside the other is a HOLE (or a ring), which is the
                # most ordinary profile in CAD: a plate with a bore. It used to be
                # reported as "overlapping loops make a self-intersecting profile"
                # on a rectangle + inscribed circle whose pad then built exactly
                # the right solid - 30066.24 mm3, topology matching the drawing
                # (reported 2026-11-06, P1-2b). Only a partial intersection is a
                # self-intersection.
                if inside(a, b) or inside(b, a):
                    nested.append((i, j))
                else:
                    overlapping.append((i, j))
        notes = ""
        if nested and not overlapping:
            pairs = "; ".join(
                f"loop {j + 1} ({loops[j]['kind']}) sits inside loop {i + 1} "
                f"({loops[i]['kind']})"
                for i, j in nested[:3])
            notes = (f" Note: {pairs} - a loop inside another is a HOLE in that "
                     "face, not a self-intersection, and pad/pocket build it as one "
                     "profile. Total loop area below is therefore larger than the "
                     "profile area; that is expected.")
        if not overlapping:
            return notes
        pairs = "; ".join(
            f"loop {i + 1} ({loops[i]['kind']}, {loops[i]['area']:.1f} mm2) overlaps "
            f"loop {j + 1} ({loops[j]['kind']}, {loops[j]['area']:.1f} mm2)"
            for i, j in overlapping[:4]
        )
        total = sum(loop["area"] for loop in loops)
        return (
            f" WARNING: this sketch holds {len(loops)} closed loops and "
            f"{len(overlapping)} pair(s) of them partially overlap: {pairs}. Two "
            "loops that cross without one containing the other make a "
            "self-intersecting profile - the pad/pocket either fails at update or "
            "builds a solid nobody meant. Total loop area "
            f"{total:.1f} mm2 is NOT the profile area when they overlap. Note that "
            "catia_sketch_rectangle already draws a COMPLETE rectangle, so a "
            "rectangular frame is four LINES, not two rectangle calls."
        ) + notes

    def _profile_breaks(self) -> dict[str, Any]:
        """The loose endpoints and the gap at each one, as data.

        The same computation ``_profile_warning`` turns into a sentence, kept
        separate because the sentence is what a client cuts off: the 2026-11-06
        report saw the gap list truncated to ``... (1242 chars)`` on a part with
        six-arc outlines, which left nothing to check against and no way to ask
        for the rest (P1-2, third bullet: "put the full gap list in a structured
        field").

        ``closed`` answers three different questions and the difference is the
        point: ``True`` - every endpoint was matched, nothing to fix;
        ``False`` - at least one is loose and ``breaks`` / ``unpaired`` say
        where; ``None`` - no coordinates were recorded in this session (a sketch
        reopened from disk, whose points cannot be read back through
        automation), so this says nothing about the sketch at all and must not
        be mistaken for a clean bill of health.
        """
        import math

        ends = list(self._profile_ends)
        breaks: list[dict[str, Any]] = []
        unpaired: list[list[float]] = []
        report: dict[str, Any] = {
            "closed": None,
            "endpoints": len(ends),
            "endpoints_loose": 0,
            "breaks": breaks,
            "unpaired": unpaired,
            "smallest_gap_mm": None,
            "widest_gap_mm": None,
            "within_tolerance": 0,
            "tolerance_mm": self._NEAR_TOL,
            "endpoint_tolerance_mm": self._END_TOL,
            "measured_from": (
                "coordinates passed to the sketch tools in this session; "
                "CATIA's own points cannot be read back, so a constraint may "
                "have moved them"),
        }
        if not ends:
            if getattr(self, "_profile_closed_curves", 0):
                report["closed"] = True
                report["basis"] = ("every element drawn is a closed curve (a circle "
                                   "or a spline closed on itself), so there is no "
                                   "free end to pair")
            return report
        if len(ends) < 2:
            return report

        used = [False] * len(ends)
        loose: list[tuple[float, float]] = []
        for i, (x, y) in enumerate(ends):
            if used[i]:
                continue
            for j in range(i + 1, len(ends)):
                if used[j]:
                    continue
                if math.hypot(x - ends[j][0], y - ends[j][1]) <= self._END_TOL:
                    used[i] = used[j] = True
                    break
            else:
                used[i] = True
                loose.append((x, y))
        report["endpoints_loose"] = len(loose)
        if not loose:
            report["closed"] = True
            return report
        report["closed"] = False

        # Greedy nearest pairing among the leftovers: each pair is one
        # breakpoint, and the interesting number is the gap. The pairing is
        # arbitrary once three endpoints are loose, so it is kept exactly as the
        # sentence computed it - the 2026-11-06 report quotes these numbers, and
        # re-pairing them would contradict a measurement for no gain.
        remaining = list(range(len(loose)))
        pairs: list[tuple[float, int, int]] = []
        while len(remaining) > 1:
            best: tuple[float, int, int] | None = None
            for ai in range(len(remaining)):
                for bi in range(ai + 1, len(remaining)):
                    a, b = remaining[ai], remaining[bi]
                    gap = math.hypot(loose[a][0] - loose[b][0],
                                     loose[a][1] - loose[b][1])
                    if best is None or gap < best[0]:
                        best = (gap, a, b)
            if best is None:
                break
            pairs.append(best)
            remaining = [r for r in remaining if r not in (best[1], best[2])]
        for gap, a, b in sorted(pairs):
            breaks.append({
                "from": [round(loose[a][0], 6), round(loose[a][1], 6)],
                "to": [round(loose[b][0], 6), round(loose[b][1], 6)],
                "gap_mm": round(gap, 6),
            })
        unpaired.extend([round(loose[i][0], 6), round(loose[i][1], 6)]
                        for i in remaining)
        if pairs:
            gaps = [gap for gap, _, _ in pairs]
            report["smallest_gap_mm"] = round(min(gaps), 6)
            report["widest_gap_mm"] = round(max(gaps), 6)
            report["within_tolerance"] = sum(
                1 for gap in gaps if gap <= self._NEAR_TOL)
        return report

    def _self_crossing_report(self, sketch_name: str | None = None
                              ) -> tuple[str, list[dict[str, Any]], bool]:
        """Does the outline cross itself? Answered from the drawing record.

        The 2026-11-11 eighth report wrote a segment-intersection test in
        Python before daring to feed a 100-point outline to ``catia_pad``,
        because nothing in the toolchain warned when v2's hook curled into a
        loop - it surfaced as "the shape looks wrong", then as a feature that
        would not build. ``_loop_warning`` already catches several OVERLAPPING
        CLOSED LOOPS by bounding box; this is the general case, computed on the
        same record, so it also sees a polyline that doubles back through itself.

        The third value is ``checked``. It is False whenever the record is empty
        - a sketch reopened from disk, or one filled by something else - and the
        reply must not turn that into "no crossings found": the points CATIA
        holds cannot be read back on this build, so there is nothing to check.
        """
        if sketch_name is None:
            try:
                sketch_name = str(self._active_sketch.Name)
            except Exception:  # noqa: BLE001 - no sketch in hand
                sketch_name = None
        drawn = (self._drawn.get(self._sketch_key(sketch_name), [])
                 if sketch_name else [])
        if not drawn:
            return "", [], False
        segments = segments_of(drawn)
        if not segments:
            return "", [], False
        found = crossings(segments)
        return describe(found, len(segments)), found, True

    def _profile_warning(self) -> str:
        """Name every breakpoint that will make pad/pocket fail.

        The gap itself is invisible in CATIA's error ("open polyline or an arc
        chain with a gap"), and the coordinates cannot be read back afterwards,
        so this is computed from the numbers the sketch tools were given.

        It reports ALL loose endpoints, not just the closest pair (reported
        2026-10-25: point lists rounded to 0.001 mm produce 0.0002-0.0005 mm
        gaps at several joints at once, and a message that names one pair sends
        the caller round the loop once per joint). Where the loose point sits is
        the useful part: two points a few microns apart is a typo, two points
        12 mm apart is a profile that is open on purpose.
        """
        breaks = self._profile_breaks()
        if breaks["closed"] is not False:
            # True: every endpoint was matched, nothing to say. None: nothing
            # was recorded in this session (a sketch reopened from disk), which
            # is not the same as a closed sketch and must not be sold as one.
            return ""
        smallest = breaks["smallest_gap_mm"]
        widest = breaks["widest_gap_mm"]
        # A profile that is open on purpose (a sweep guide, say) must not be
        # nagged about, so the warning only fires when at least one gap is small
        # enough to be a rounding artefact - but then it names EVERY gap, not
        # just that one.
        if smallest is None or smallest > self._NEAR_TOL:
            return ""

        lines = [
            f"({item['from'][0]:.4f}, {item['from'][1]:.4f}) <-> "
            f"({item['to'][0]:.4f}, {item['to'][1]:.4f}) = {item['gap_mm']:.4f} mm"
            for item in breaks["breaks"]
        ]
        if breaks["unpaired"]:
            lines.append("unpaired at " + ", ".join(
                f"({x:.4f}, {y:.4f})" for x, y in breaks["unpaired"]))
        loose_count = breaks["endpoints_loose"]
        break_count = len(breaks["breaks"]) + (1 if breaks["unpaired"] else 0)
        # The summary and the advice come FIRST, the coordinate list last. The
        # list is what makes this message long enough to be cut off by a client
        # (1242 chars in the 2026-11-06 report), and cutting the tail used to
        # throw away the only actionable part - the sentence about rounding
        # artefacts - leaving a wall of coordinates that "cannot be worked
        # with" (reported 2026-11-06, P1-2, third bullet).
        text = (
            f" WARNING: the profile is not closed - {loose_count} loose "
            f"endpoint(s) in {break_count} break(s); "
            f"smallest gap {smallest:.4f} mm, widest {widest:.4f} mm."
        )
        if breaks["within_tolerance"]:
            # It used to say "CATIA still refuses the feature" as a fact. It is
            # not one: this server cannot know whether the update closes a gap of
            # 0.0002 mm, and on the runs measured so far (2026-11-06, three parts
            # with 6-arc outlines) the pad SUCCEEDED every time. Telling an AI
            # caller that a working sketch is broken costs it a rebuild
            # (reported 2026-11-06, P1-2c).
            text += (
                f" The smallest gap(s) are within {self._NEAR_TOL:g} mm: at that "
                "size they are usually a rounding artefact of how the endpoints "
                "were computed, and CATIA closes them at update more often than "
                "not - so close the sketch and try the feature before rebuilding "
                "anything. If pad/pocket do report 'open polyline or an arc chain "
                "with a gap', make the shared coordinate literally identical (the "
                "exact circle-circle intersection at a junction, and a line end "
                "projected onto the arc rather than recomputed)."
            )
        text += (
            " The gaps: " + "; ".join(lines) + "."
        )
        text += (
            " (Computed from the coordinates passed to the sketch tools; "
            "CATIA's own points cannot be read back through automation, so a "
            "constraint may have moved them.)"
        )
        return text

    def _close_sketch(self, structured: bool = False) -> str | dict[str, Any]:
        if self._active_sketch is None:
            # The cached handle does not survive a server restart while CATIA
            # keeps the sketch open in edition. Re-adopt the document's
            # in-work object when it looks like a sketch instead of failing.
            try:
                part = self.conn.get_active_part()
                candidate = part.InWorkObject
                getattr(candidate, "CloseEdition")
                self._active_sketch = candidate
            except Exception:
                pass
        self._ensure_sketch_open()
        sketch = self._active_sketch
        try:
            name = str(sketch.Name)
        except Exception:
            name = "sketch"
        try:
            sketch.CloseEdition()
            owner = getattr(self, "_sketch_part", None) or self.conn.get_active_part()
            owner.UpdateObject(sketch)
            # A reopened sketch that gained geometry leaves the features built on
            # it stale until the part rebuilds - the reporter saw 12000.0 instead
            # of 11214.6 and concluded the edit had not taken (2026-10-24).
            try:
                owner.Update()
            except Exception:  # noqa: BLE001 - the sketch itself is closed
                pass
        finally:
            # Clear cached state even when the close fails, so a broken
            # handle cannot poison subsequent calls.
            self._active_sketch = None
            self._active_factory = None
        self.conn.refresh_display()
        summary = self._profile_summary(name)
        # Read the breaks BEFORE the cache is cleared - and before anything
        # below could raise, so a structured reply still carries them.
        breaks = self._profile_breaks()
        warning = self._profile_warning()
        loops = self._loop_warning(name)
        crossing_text, crossing_data, crossing_checked = (
            self._self_crossing_report(name))
        drawn = self._drawn.get(self._sketch_key(name), [])
        self._profile_ends = []
        self._profile_closed_curves = 0
        message = (
            f"Sketch '{name}' closed. You can now apply Part Design features "
            "(pad, pocket, user pattern on its points, etc.)."
            + summary + warning + loops + crossing_text
        )
        if not structured:
            return message
        # `modified` is about the SKETCH, and it is knowable: geometry drawn in
        # this session changed it, a reopen-and-close did not. Claiming True
        # unconditionally would report a write for the second case, and False
        # unconditionally would hide the first.
        return operation_result(
            tool="catia_close_sketch",
            ok=True,
            code="SKETCH_CLOSED",
            message=message,
            data={
                "sketch": name,
                "breaks": breaks,
                "loops_warning": loops.strip() or None,
                "elements_drawn": len(drawn),
                # Checked from the drawing record, so only this session's
                # elements are covered - `checked` says which case this is
                # instead of letting an empty list read as "no crossings".
                "self_intersection_checked": crossing_checked,
                "self_intersections": crossing_data,
            },
            modified=bool(drawn),
        )

    def _draw_line(self, x1: float, y1: float, x2: float, y2: float,
                   construction: bool = False) -> str:
        self._ensure_sketch_open()
        factory = self._active_factory
        line = factory.CreateLine(x1, y1, x2, y2)
        if construction:
            line.Construction = True
        else:
            self._note_ends((x1, y1), (x2, y2))
            self._note_element("line", x1=x1, y1=y1, x2=x2, y2=y2)
        kind = "Construction line" if construction else "Line"
        return f"{kind} created from ({x1}, {y1}) to ({x2}, {y2}) mm"

    def _draw_strokes(self, strokes: list[list[list[float]]],
                      construction: bool = False) -> str:
        """Draw many polylines as straight segments, in one call.

        WHY THIS EXISTS: a glyph outline is 8-20 segments and the one-element
        tools force one call per segment, so a letter was dozens of calls of
        hand-computed control points (2026-11-10 report, §3.1). The report asked
        for a text tool; a text tool needs a font, and a hand-written font would
        be a guess dressed as a feature - this is the part that is certain: the
        whole outline arrives as data and goes into the sketch unchanged.

        STRAIGHT segments, not a spline: catia_sketch_spline through the same
        points interpolates and rounds every corner, which is exactly how their
        hand-built "A" turned into an omega. A stroke that repeats its first
        point comes out closed.

        Degenerate segments are refused BEFORE drawing anything, so a bad point
        list cannot leave half a letter in the sketch.
        """
        self._ensure_sketch_open()
        if not strokes:
            raise ArgumentRejected(
                "catia_sketch_strokes needs at least one stroke, e.g. "
                "[[[0,0],[10,0],[10,10]]].",
                code="BAD_ARGUMENT", stage="validate",
            )
        cleaned: list[list[tuple[float, float]]] = []
        for index, stroke in enumerate(strokes, start=1):
            if not isinstance(stroke, (list, tuple)) or len(stroke) < 2:
                raise ArgumentRejected(
                    f"Stroke {index} has {len(stroke) if hasattr(stroke, '__len__') else '?'} "
                    "point(s); a polyline needs at least 2. Nothing was drawn.",
                    code="BAD_ARGUMENT", stage="validate",
                )
            points: list[tuple[float, float]] = []
            for point in stroke:
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    raise ArgumentRejected(
                        f"Stroke {index} has a point that is not an [x, y] pair: "
                        f"{point!r}. Nothing was drawn.",
                        code="BAD_ARGUMENT", stage="validate",
                    )
                points.append((float(point[0]), float(point[1])))
            for (x1, y1), (x2, y2) in zip(points, points[1:]):
                if abs(x2 - x1) < 1e-9 and abs(y2 - y1) < 1e-9:
                    raise ArgumentRejected(
                        f"Stroke {index} repeats the point ({x1:g}, {y1:g}) on "
                        "consecutive entries, which draws a zero-length line - "
                        "CATIA accepts it and the profile then fails three steps "
                        "later. Nothing was drawn; remove the duplicate.",
                        code="DEGENERATE_SEGMENT", stage="validate",
                    )
            cleaned.append(points)

        factory = self._active_factory
        segments = 0
        for points in cleaned:
            for (x1, y1), (x2, y2) in zip(points, points[1:]):
                line = factory.CreateLine(x1, y1, x2, y2)
                if construction:
                    try:
                        line.Construction = True
                    except Exception:  # noqa: BLE001 - the line is still drawn
                        pass
                else:
                    # Recorded per SEGMENT, as a "line": that is the kind the
                    # replay path (catia_sketch_copy) and the outline echo already
                    # understand, so a batch-drawn glyph is copyable and its span
                    # bookkeeping stays exact. A new record kind here would have
                    # been an unhandled branch three functions away.
                    self._note_ends((x1, y1), (x2, y2))
                    self._note_element("line", x1=x1, y1=y1, x2=x2, y2=y2)
                segments += 1

        xs = [x for points in cleaned for x, _ in points]
        ys = [y for points in cleaned for _, y in points]
        closed = sum(1 for points in cleaned if points[0] == points[-1])
        return (
            f"{len(cleaned)} stroke(s) drawn as {segments} straight line(s)"
            + (" as construction geometry" if construction else "")
            + f", covering X {min(xs):g}..{max(xs):g} and Y {min(ys):g}..{max(ys):g} mm"
            + (f"; {closed} of them closed" if closed else "")
            + ". A stroke that repeats its first point is closed; nothing was "
            "smoothed, so corners stay corners."
        )

    def _draw_profile(self, segments: list[dict], tolerance: float = 0.05) -> str:
        """Draw a MIXED profile - straight runs and arcs - in one call.

        The gap this fills, from the 2026-11-11 eighth report §2.1: "a profile
        that is straight on one side and curved on the other has no workable
        combination", because ``catia_sketch_spline`` rounds the straight parts
        and ``catia_sketch_strokes`` leaves the curved parts faceted. Both are
        true, and neither is the only route: drawing each element with the
        primitive for its own kind works, and the report did not try it.

        MEASURED on B30 2026-11-12, a 60x40 stadium (two 60 mm lines and two
        r=20 half circles, four elements): ``catia_close_sketch`` answered
        "2 arc(s), 2 line(s) ... outline 245.664 mm", and the 10 mm pad came out
        at **36566.37 mm3, which is 60*40*10 + pi*20^2*10 exactly** - one
        command instead of four plus a close. A 0.05 mm gap at one junction is
        caught here before the pad, with the two coordinates named.

        Endpoints are taken from the arguments: a line's p1/p2, an arc's point
        at start_angle/p1 and at end_angle/p2. If the short way round flips an
        arc's sweep, ``catia_sketch_arc`` says so in its own reply and the
        junction report here will show the mismatch - read them together.
        """
        self._ensure_sketch_open()
        if not segments:
            raise ArgumentRejected(
                'catia_sketch_profile needs at least one segment, e.g. '
                '{"segments": [{"type": "line", "p1": [0, 0], "p2": [60, 0]}]}. '
                "Nothing was drawn.",
                code="BAD_ARGUMENT", stage="validate",
            )
        if tolerance <= 0:
            raise ArgumentOutOfRange(
                "tolerance is a junction gap in mm and has to be positive; got "
                f"{tolerance:g}. It decides what counts as 'joined', not what is "
                "drawn.",
                code="TOLERANCE_OUT_OF_RANGE", stage="validate")

        # Plan before drawing: a bad third segment must not leave the first two
        # in the sketch. Every endpoint this needs is arithmetic, so nothing has
        # to be probed to validate.
        plan = [self._plan_segment(index, raw)
                for index, raw in enumerate(segments, start=1)]

        for segment in plan:
            if segment["kind"] == "line":
                self._draw_line(*segment["p1"], *segment["p2"])
            else:
                # THE ENDPOINT FORM NEVER REACHED _draw_arc. _plan_segment turns
                # p1/p2 into angles and then clears them to None, and its result
                # carries no 'p1' key - so this call handed _draw_arc three
                # Nones and it answered "needs either start_angle and end_angle,
                # or p1 and p2". Measured 2026-11-12 on a 4-element stadium: the
                # documented endpoint form refused before drawing anything. The
                # plan's own 'start'/'end' are those endpoints.
                angle_form = (segment.get("start_angle") is not None
                              and segment.get("end_angle") is not None)
                self._draw_arc(
                    segment["center"][0], segment["center"][1],
                    segment["radius"],
                    segment.get("start_angle"), segment.get("end_angle"),
                    p1=None if angle_form else segment["start"],
                    p2=None if angle_form else segment["end"],
                    direction=segment["direction"],
                    major=segment["major"],
                )

        junctions: list[str] = []
        widest = 0.0
        loose = 0
        for index, segment in enumerate(plan):
            following = plan[(index + 1) % len(plan)]
            gap = _point_gap(segment["end"], following["start"])
            widest = max(widest, gap)
            if gap > tolerance:
                loose += 1
                # The two ends of the break: which segment ends here, and which
                # one was supposed to start here. Naming the same segment twice
                # (index % len + 1 on the second one) is how this read on the
                # first run of the audit.
                junctions.append(
                    f"segment {index + 1} ends at {_fmt_point(segment['end'])} "
                    f"but segment {(index + 1) % len(plan) + 1} starts at "
                    f"{_fmt_point(following['start'])}, {gap:.4f} mm apart")

        lines = sum(1 for segment in plan if segment["kind"] == "line")
        arcs = len(plan) - lines
        kinds = ", ".join(part for part in (
            f"{lines} line(s)" if lines else "",
            f"{arcs} arc(s)" if arcs else "") if part)
        head = f"Profile drawn in one call: {len(plan)} segment(s) - {kinds}."
        if loose:
            head += (f" WARNING: NOT CLOSED - {loose} of {len(plan)} junction(s) "
                     f"are wider than {tolerance:g} mm, widest {widest:.4f} mm: "
                     + "; ".join(junctions)
                     + ". CATIA closes gaps of a few hundredths of a millimetre "
                     "at update more often than not, so try the pad before "
                     "rebuilding anything - catia_close_sketch reports what the "
                     "sketch actually holds.")
        else:
            head += (f" Every one of the {len(plan)} junction(s) meets within "
                     f"{widest:.4f} mm, so it should close.")
        return head + " NEXT: catia_close_sketch, then catia_pad."

    def _plan_segment(self, number: int, raw: Any) -> dict[str, Any]:
        """Validate one segment and work out both of its endpoints."""
        if not isinstance(raw, dict):
            raise ArgumentRejected(
                f"Segment {number} must be an object with a 'type' of 'line' or "
                f"'arc'; got {raw!r}. Nothing was drawn.",
                code="BAD_ARGUMENT", stage="validate")
        kind = str(raw.get("type") or "").lower()
        if kind not in ("line", "arc"):
            raise ArgumentRejected(
                f"Segment {number} has type {raw.get('type')!r}; this tool draws "
                "'line' and 'arc' segments. For a polyline use "
                "catia_sketch_strokes, for a spline catia_sketch_spline. Nothing "
                "was drawn.",
                code="BAD_ARGUMENT", stage="validate")

        if kind == "line":
            p1 = _pair(raw.get("p1"), f"Segment {number} (line) needs p1 as [x, y]")
            p2 = _pair(raw.get("p2"), f"Segment {number} (line) needs p2 as [x, y]")
            if _point_gap(p1, p2) < 1e-9:
                raise ArgumentRejected(
                    f"Segment {number} is a line from {_fmt_point(p1)} to the "
                    "same point - zero length. Nothing was drawn.",
                    code="DEGENERATE_SEGMENT", stage="validate")
            return {"kind": "line", "p1": p1, "p2": p2,
                    "start": p1, "end": p2}

        center = _pair(raw.get("center"), f"Segment {number} (arc) needs center as [x, y]")
        try:
            radius = float(raw.get("radius"))
        except (TypeError, ValueError) as exc:
            raise ArgumentRejected(
                f"Segment {number} (arc) needs a numeric radius; got "
                f"{raw.get('radius')!r}. Nothing was drawn.",
                code="BAD_ARGUMENT", stage="validate") from exc
        if radius <= 0:
            raise ArgumentRejected(
                f"Segment {number} (arc) has radius {radius:g}; it has to be "
                "positive. Nothing was drawn.",
                code="BAD_ARGUMENT", stage="validate")
        direction = str(raw.get("direction") or "ccw").lower()
        if direction not in ("cw", "ccw"):
            raise ArgumentRejected(
                f"Segment {number} (arc) has direction {raw.get('direction')!r}; "
                "it is 'cw' or 'ccw' (catia_sketch_arc takes the same values). "
                "Nothing was drawn.",
                code="BAD_ARGUMENT", stage="validate")
        start_angle = raw.get("start_angle")
        end_angle = raw.get("end_angle")
        p1 = raw.get("p1")
        p2 = raw.get("p2")
        if start_angle is not None and end_angle is not None:
            first = _on_circle(center, radius, float(start_angle))
            second = _on_circle(center, radius, float(end_angle))
        elif p1 is not None and p2 is not None:
            first = _pair(p1, f"Segment {number} (arc) needs p1 as [x, y]")
            second = _pair(p2, f"Segment {number} (arc) needs p2 as [x, y]")
            start_angle = end_angle = None
        else:
            raise ArgumentRejected(
                f"Segment {number} (arc) needs either start_angle and end_angle "
                "or p1 and p2. Nothing was drawn.",
                code="BAD_ARGUMENT", stage="validate")
        if _point_gap(first, second) < 1e-9:
            raise ArgumentRejected(
                f"Segment {number} (arc) starts and ends at {_fmt_point(first)} - "
                "that is a full circle, not an arc; use catia_sketch_circle. "
                "Nothing was drawn.",
                code="DEGENERATE_SEGMENT", stage="validate")
        return {"kind": "arc", "center": center, "radius": radius,
                "start_angle": start_angle, "end_angle": end_angle,
                "direction": direction, "major": bool(raw.get("major", False)),
                "start": first, "end": second}

    def _draw_text(self, args: dict[str, Any]) -> str:
        """Text as strokes, from a font that is in the box rather than in CATIA.

        ``Factory2D`` has no ``CreateText`` (37 members, measured on B30
        2026-11-10), so a glyph can only arrive as geometry. ``_draw_strokes``
        already takes a whole outline; what this adds is the outlines, and it is
        pure arithmetic - no COM is touched until the strokes are handed over, so
        a string with nothing drawable in it fails before anything is drawn.

        The font is single-line on purpose: a groove a fraction of a millimetre
        deep cannot resolve two walls of one stem, so an outline face would come
        out as one ragged slot. See ``catia_mcp.fonts`` for the licence text.
        """
        self._ensure_sketch_open()
        raw = args.get("text")
        if not isinstance(raw, str) or not raw.strip():
            raise ArgumentRejected(
                'catia_text_strokes needs some text, e.g. {"text": "CATIA"}. '
                "Nothing was drawn.",
                code="BAD_ARGUMENT", stage="validate",
            )
        height = float(args.get("height", 10.0))
        if height <= 0:
            raise ArgumentRejected(
                f"height must be positive; got {height:g}. It is the CAP HEIGHT "
                "in millimetres (an H), not the width of the text. Nothing was "
                "drawn.",
                code="BAD_ARGUMENT", stage="validate",
            )
        origin = args.get("origin")
        if origin is None:
            origin = [0.0, 0.0]
        if not isinstance(origin, (list, tuple)) or len(origin) != 2:
            raise ArgumentRejected(
                "origin must be an [x, y] pair in the sketch's own mm; got "
                f"{origin!r}. Nothing was drawn.",
                code="BAD_ARGUMENT", stage="validate",
            )
        spacing = args.get("spacing")
        line_spacing = args.get("line_spacing")
        result = text_to_strokes(
            raw,
            height_mm=height,
            spacing_mm=None if spacing is None else float(spacing),
            origin=(float(origin[0]), float(origin[1])),
            line_spacing_mm=None if line_spacing is None else float(line_spacing),
        )
        if not result.strokes:
            named = (", ".join(repr(char) for char in result.missing)
                     if result.missing else "every character")
            raise PreconditionRejected(
                f"Not one character of {raw!r} has a glyph in this font "
                f"({named}). The face is Hershey simplex: ASCII 32-126 only, no "
                "CJK and no accented letters. Nothing was drawn - a placeholder "
                "box would engrave as something you did not ask for. Available: "
                f"{font_charset()}",
                code="TEXT_NO_GLYPHS", stage="execute", modified=False,
                next_actions=[
                    "Write the text in ASCII letters, digits and punctuation",
                    "If it must be another script, build the outlines yourself "
                    "and hand them to catia_sketch_strokes",
                ],
            )
        drawn = self._draw_strokes(result.strokes,
                                   bool(args.get("construction", False)))
        origin_x, origin_y = float(origin[0]), float(origin[1])
        note = (
            f"Text {raw!r}: {result.glyphs} glyph(s) in {result.lines} line(s), "
            f"{len(result.strokes)} stroke(s), {height:g} mm cap height, "
            f"{result.width_mm:.3f} mm wide, baseline at ({origin_x:g}, "
            f"{origin_y:g}) in this sketch's own mm."
        )
        if result.missing:
            note += (
                f" SKIPPED {len(result.missing)} character(s) with no glyph here: "
                + ", ".join(repr(char) for char in result.missing)
                + " - ASCII 32-126 only, so the text is that much shorter and "
                "nothing was drawn in their place."
            )
        return (
            note
            + " Hershey simplex, single-line: one stroke per stem, which is what a "
            "sub-millimetre groove can resolve. "
            + drawn
            + " BEFORE ENGRAVING THIS, one measured catch: projecting a glyph and "
            "then engraving the projection can refuse a parameter point. Measured "
            "on B30 2026-11-11 over 12 letters, 9 took the point and 3 ('O', '0', "
            "'D') did not - and the same 'O' passed at 57 % coverage and failed at "
            "81 %. So coverage, not the glyph, is what moves: if the engrave "
            "reports that the curve cannot carry a point, move the text so part of "
            "it runs off the face, or cut a closed letter as two open strokes. The "
            "numbers and what is NOT explained are in catia_diagnose's ceilings."
            + _stroke_data(result.strokes,
                           bool(args.get("include_points", False)))
        )

    def _draw_rectangle(self, x1: float, y1: float, x2: float, y2: float) -> str:
        self._ensure_sketch_open()
        factory = self._active_factory

        # Create 4 lines forming a closed rectangle
        factory.CreateLine(x1, y1, x2, y1)  # bottom
        factory.CreateLine(x2, y1, x2, y2)  # right
        factory.CreateLine(x2, y2, x1, y2)  # top
        factory.CreateLine(x1, y2, x1, y1)  # left
        for a, b in (((x1, y1), (x2, y1)), ((x2, y1), (x2, y2)),
                     ((x2, y2), (x1, y2)), ((x1, y2), (x1, y1))):
            self._note_ends(a, b)
        self._note_element("rectangle", x1=x1, y1=y1, x2=x2, y2=y2)

        return (
            f"Rectangle created from ({x1}, {y1}) to ({x2}, {y2}) mm "
            f"[{abs(x2-x1):.1f} x {abs(y2-y1):.1f} mm]"
        )

    def _draw_centered_rectangle(
        self, cx: float, cy: float, width: float, height: float
    ) -> str:
        hw, hh = width / 2, height / 2
        return self._draw_rectangle(cx - hw, cy - hh, cx + hw, cy + hh)

    def _draw_circle(self, cx: float, cy: float, radius: float) -> str:
        self._ensure_sketch_open()
        factory = self._active_factory
        factory.CreateClosedCircle(cx, cy, radius)
        import math
        self._profile_closed_curves = getattr(
            self, "_profile_closed_curves", 0) + 1
        self._note_element("circle", cx=cx, cy=cy, radius=radius,
                           sweep_rad=2 * math.pi)
        return f"Circle created at ({cx}, {cy}) with radius {radius} mm"

    def _draw_ellipse(self, cx: float, cy: float, major: float, minor: float,
                      angle: float, construction: bool) -> str:
        """A full ellipse: ``CreateClosedEllipse`` with the major axis direction.

        Why it is worth a tool: the 2026-11-11 report approximated a slanted handle
        hole with 28 straight segments, so the hole wall was 28 flats and the
        screenshots showed the facets. ``Factory2D`` has both
        ``CreateClosedEllipse(iCenterX, iCenterY, iMajorX, iMajorY, iMajorRadius,
        iMinorRadius)`` and ``CreateEllipse`` with start/end parameters - the
        argument ``(iMajorX, iMajorY)`` is the DIRECTION of the major axis, which is
        why an angle becomes ``(cos, sin)`` rather than being ignored.
        """
        self._ensure_sketch_open()
        try:
            cx, cy = float(cx), float(cy)
            major, minor = float(major), float(minor)
            angle = float(angle or 0.0)
        except (TypeError, ValueError) as error:
            raise ArgumentRejected(
                f"cx, cy, major, minor and angle have to be numbers; got "
                f"{cx!r}, {cy!r}, {major!r}, {minor!r}, {angle!r}.",
                code="BAD_ARGUMENT", stage="validate") from error
        if major <= 0 or minor <= 0:
            raise ArgumentOutOfRange(
                f"major and minor have to be positive radii in mm; got {major:g} "
                f"and {minor:g}. An ellipse with a zero radius is a line.",
                code="RADIUS_OUT_OF_RANGE", stage="validate")
        if major < minor:
            raise ArgumentRejected(
                f"major ({major:g}) is shorter than minor ({minor:g}); 'major' is "
                "the LONG semi-axis and it sets the direction of the ellipse. Pass "
                f"major={minor:g}, minor={major:g} if that is what you meant.",
                code="BAD_ARGUMENT", stage="validate")

        import math

        factory = self._active_factory
        radians = math.radians(angle)
        ux, uy = math.cos(radians), math.sin(radians)
        curve = factory.CreateClosedEllipse(cx, cy, ux, uy, major, minor)
        if construction:
            try:
                curve.Construction = True
            except Exception:  # noqa: BLE001 - drawn solid is better than nothing
                pass
        self._profile_closed_curves = getattr(
            self, "_profile_closed_curves", 0) + (0 if construction else 1)
        # Arc length of an ellipse is not elementary; the note keeps the axes so a
        # later caller can re-derive it instead of trusting a rounded number.
        self._note_element("ellipse", cx=cx, cy=cy, major=major, minor=minor,
                           angle=angle, construction=construction)
        where = " as construction geometry" if construction else ""
        return (f"Ellipse created at ({cx:g}, {cy:g}){where}: semi-major {major:g} mm "
                f"along {angle:g} deg from +X, semi-minor {minor:g} mm. One curve, "
                "not a polyline - a hole cut from it has one smooth wall.")

    def _draw_arc(
        self, cx: float, cy: float, radius: float,
        start_angle: float | None = None, end_angle: float | None = None,
        p1: list[float] | None = None, p2: list[float] | None = None,
        direction: str = "ccw", major: bool = False,
    ) -> str:
        self._ensure_sketch_open()
        factory = self._active_factory
        import math
        # Factory2D has no CreateArc in CATIA V5 (field-verified against the
        # R20 API reference). An arc is an open circle: CreateCircle takes
        # optional start/end parameters in radians that leave it open.
        notes: list[str] = []
        angle_form = False
        if p1 is not None or p2 is not None:
            if p1 is None or p2 is None:
                raise ArgumentRejected(
                    "catia_sketch_arc needs BOTH p1 and p2 (the two endpoints), "
                    "or start_angle and end_angle. Got only one endpoint."
                )
            a1 = math.atan2(float(p1[1]) - cy, float(p1[0]) - cx)
            a2 = math.atan2(float(p2[1]) - cy, float(p2[0]) - cx)
            # How far each given point is from the circle it is supposed to sit
            # on. A reverse-engineered point list rounds to 0.001 mm, so a
            # radius that does not match its endpoints by more than a few
            # microns is a data error, not noise - and it is invisible once the
            # arc is drawn, because the endpoints move onto the circle.
            off = max(
                abs(math.hypot(float(p1[0]) - cx, float(p1[1]) - cy) - radius),
                abs(math.hypot(float(p2[0]) - cx, float(p2[1]) - cy) - radius),
            )
            if off > 1e-3:
                notes.append(
                    f" WARNING: the endpoints are up to {off:.4f} mm off the "
                    f"R{radius} circle; the arc was drawn on the circle, so its "
                    "ends moved by that much. Check the centre/radius against the "
                    "point pair - this is how a profile ends up with a gap."
                )
            # How far it is to walk counter-clockwise from p1 to p2. Zero means
            # the two points are the same point on the circle, which is a
            # degenerate arc, not a full circle: CreateCircle is the tool for
            # that, and a shaft built on a 0-degree arc fails with "UpdateObject
            # 失败" three steps later (one of the three causes in the 2026-11-01
            # report, §补充 4).
            ccw = (a2 - a1) % (2 * math.pi)
            if ccw < 1e-9:
                raise ArgumentRejected(
                    f"catia_sketch_arc got two endpoints at the same angle "
                    f"({math.degrees(a1):.4f} deg) on the R{radius} circle around "
                    f"({cx}, {cy}), so the arc would sweep nothing. Use "
                    "catia_sketch_circle for a full circle, or move one endpoint.",
                    code="DEGENERATE_ARC")
            # `direction` picks which of the two ways round; `major` says whether
            # a sweep past half a circle is what the caller meant. It defaults to
            # no, because it almost never is: reported 2026-11-01, an arc whose
            # endpoints were 4.39 degrees apart answered "355.6117 degrees
            # counter-clockwise" (1608 mm) and the shaft built on it failed with
            # a bare "UpdateObject 失败" - the caller had said "ccw" without
            # meaning "the long way round", which is what ccw means when the two
            # points are 4 degrees apart.
            chosen_ccw = direction != "cw"
            sweep_rad = ccw if chosen_ccw else (2 * math.pi - ccw)
            flipped = False
            if not major and sweep_rad > math.pi + 1e-9:
                chosen_ccw = not chosen_ccw
                sweep_rad = 2 * math.pi - sweep_rad
                flipped = True
            if chosen_ccw:
                start_rad, end_rad = a1, a1 + sweep_rad
            else:
                start_rad, end_rad = a2, a2 + sweep_rad
            start_angle = math.degrees(start_rad)
            end_angle = math.degrees(end_rad)
            if flipped:
                notes.append(
                    f" Took the SHORT way round ({math.degrees(sweep_rad):.4f} "
                    f"deg): going {direction} from p1 to p2 the long way is "
                    f"{360 - math.degrees(sweep_rad):.4f} deg. Pass major=true if "
                    "that arc really is more than half a circle.")
            elif abs(math.degrees(sweep_rad) - 180) < 0.5:
                notes.append(
                    " The endpoints are 180 deg apart, so direction decides "
                    "nothing here - the arc is a half circle either way, and it "
                    "was drawn exactly as written.")
        elif start_angle is not None and end_angle is not None:
            start_rad = math.radians(start_angle)
            end_rad = math.radians(end_angle)
            # THE ANGLE FORM USED TO IGNORE direction AND major (0.3.93). The
            # points form has chosen the short way round since 0.3.45, but this
            # branch handed the pair straight to CreateCircle, whose only
            # requirement is start < end - so (90, 0) silently became the 270
            # degree arc instead of the 90 degree one, and `major` changed
            # nothing. Reported 2026-11-10: "I ask for 90 degrees and keep
            # getting 270". Same rule as the points form, and it says so.
            first_rad, second_rad = start_rad, end_rad
            span = (second_rad - first_rad) % (2 * math.pi)
            if span < 1e-9:
                raise ArgumentRejected(
                    f"catia_sketch_arc got start_angle {start_angle:g} and "
                    f"end_angle {end_angle:g}, which are the same point on the "
                    "circle, so the arc would sweep nothing. Use "
                    "catia_sketch_circle for a full circle.",
                    code="DEGENERATE_ARC")
            ccw_wanted = direction != "cw"
            sweep = span if ccw_wanted else (2 * math.pi - span)
            if not major and sweep > math.pi + 1e-9:
                ccw_wanted = not ccw_wanted
                sweep = 2 * math.pi - sweep
                notes.append(
                    f" Took the SHORT way round ({math.degrees(sweep):.4f} deg) "
                    f"between {start_angle:g} and {end_angle:g} deg: walking "
                    f"{direction} from the first angle to the second is "
                    f"{360 - math.degrees(sweep):.4f} deg. Pass major=true if that "
                    "arc really is more than half a circle.")
            if ccw_wanted:
                start_rad, end_rad = first_rad, first_rad + sweep
            else:
                start_rad, end_rad = second_rad, second_rad + sweep
            # THE REPLY DESCRIBED A PAIR THAT IS NOT AN ARC (0.3.95). It said
            # "from 90 deg to 0 deg - swept 90 counter-clockwise", and 90 -> 0
            # is clockwise; the two clauses contradicted each other in the same
            # sentence, while the geometry was right (reported 2026-11-10, P2-1).
            # The direction now comes from the arc that was actually drawn.
            angle_form = True
            angle_direction = ("counter-clockwise" if ccw_wanted else "clockwise")
            angle_from, angle_to = start_angle, end_angle
        else:
            raise ArgumentRejected(
                "catia_sketch_arc needs either start_angle and end_angle, or p1 "
                "and p2."
            )

        # CreateCircle requires endParam strictly greater than startParam;
        # normalize sweeps that wrap past 0 degrees.
        while end_rad <= start_rad:
            end_rad += 2 * math.pi
        factory.CreateCircle(cx, cy, radius, start_rad, end_rad)
        sweep = math.degrees(end_rad - start_rad)
        swept = (f"{sweep:.0f}deg MAJOR arc (more than a half circle)"
                 if sweep > 180.5 else
                 "180deg half circle" if abs(sweep - 180) <= 0.5 else
                 "minor arc")
        # CATIA stores an arc as a start angle plus a counter-clockwise sweep, so
        # a clockwise request is written as the SAME points in the reverse order.
        # The reply used to describe the stored form only ("from -105.1 to -68.8,
        # swept 36.3 counter-clockwise") and then add a bracket apologising for
        # it, which reads as "the endpoints were swapped" to anything that
        # stitches a profile by order (reported 2026-11-06, P3-7). Say both.
        if angle_form:
            span = (f"from {angle_from:g}° {angle_direction} to {angle_to:g}°")
        elif direction == "cw" and p1 is not None:
            span = (f"from p1 ({p1[0]:g}, {p1[1]:g}) to p2 ({p2[0]:g}, {p2[1]:g}), "
                    f"which is clockwise from p1 to p2, {abs(sweep):.4f}° (stored "
                    f"as {end_angle:.4f}° -> {start_angle:.4f}°, swept "
                    f"{abs(sweep):.4f}° counter-clockwise - the same arc)")
        else:
            span = (f"from {start_angle:.4f}° to {end_angle:.4f}° - swept "
                    f"{sweep:.4f}° counter-clockwise")
        # Remember the arc's own ends, computed from the numbers we were given.
        # Automation cannot read a Point2D's coordinates back on this build
        # (GetCoordinates needs a by-ref array it never fills), so this is the
        # only place the exact endpoint survives - and it is the number that
        # explains a pad failure caused by a 3.5 um gap.
        #
        # BEFORE the return, and that is the fix: these two calls used to sit
        # AFTER it, so they had not run once since they were written. An arc
        # therefore entered no drawing record at all - its ends were missing from
        # the endpoint pairing (a six-arc outline paired only its lines), the
        # profile echo counted 0 arcs, and the rebuild path had nothing to
        # replay. Found while fixing the spline ends the 2026-11-08 report called
        # missing (P1-1); same class, one function away, and no test looked.
        self._note_ends(
            (cx + radius * math.cos(start_rad), cy + radius * math.sin(start_rad)),
            (cx + radius * math.cos(end_rad), cy + radius * math.sin(end_rad)),
        )
        self._note_element("arc", cx=cx, cy=cy, radius=radius,
                           start_rad=start_rad, sweep_rad=end_rad - start_rad)
        return (
            f"Arc created at ({cx}, {cy}), radius={radius} mm, "
            f"{span} ({swept}), arc length "
            f"{radius * math.radians(abs(sweep)):.4f} mm"
            + "".join(notes)
        )

    def _draw_spline(self, points: list[list[float]], closed: bool = False) -> str:
        self._ensure_sketch_open()
        factory = self._active_factory

        # Closing the spline by appending a LINE back to the start produces two
        # entities, and a multi-entity outline cannot be used as a loft section
        # (CATIA accepts it in AddSectionToLoft and then fails at update). A
        # closed spline has to repeat its first point instead, so the result is
        # one continuous curve.
        control = [list(p) for p in points]
        if closed and len(control) >= 3:
            control.append(list(control[0]))
        # Factory2D.CreateSpline expects an array of 2D points.
        spline_pts = [factory.CreatePoint(p[0], p[1]) for p in control]
        spline = factory.CreateSpline(spline_pts)

        import math
        # Over the CONTROL list, so a closed spline includes its closing segment:
        # counting only the points that were passed in made a 4-sided closed
        # spline report three sides (18 mm instead of 24 mm, reported 2026-11-09).
        chord = sum(math.hypot(control[i + 1][0] - control[i][0],
                               control[i + 1][1] - control[i][1])
                    for i in range(len(control) - 1))
        # The control points themselves, not just how many there were. Without
        # them a spline is the ONE closed profile the rebuild path cannot replay -
        # and since 0.3.77 makes "one closed spline" the shape a loft section has
        # to be, the most common pad/pocket mistake (wrong direction) cost the
        # caller the whole section: CATIA takes the sketch with the rollback and
        # nothing could put it back (reported 2026-11-08, M3).
        self._note_element("spline", points=len(points), chord=chord,
                           pts=[[float(p[0]), float(p[1])] for p in points],
                           closed=bool(closed))
        # The two ends of the curve, for the endpoint pairing at close time. They
        # were never recorded, so a profile made of one spline plus lines was
        # paired only among the LINES: the spline's own ends stayed unmatched and
        # catia_close_sketch called a closed outline open, with a 29 mm "gap"
        # between two points that were the same numbers (reported 2026-11-08,
        # P1-1: "structured=true 的 breaks[] 恰恰是最可信的判据"). A closed
        # spline repeats its first point, so both ends are the same coordinate
        # and pair with each other - which is also correct: it has no free end.
        if closed and len(control) > len(points):
            self._profile_closed_curves = getattr(
                self, "_profile_closed_curves", 0) + 1
        self._note_ends((float(control[0][0]), float(control[0][1])),
                        (float(control[-1][0]), float(control[-1][1])))
        pts_str = ", ".join(f"({p[0]}, {p[1]})" for p in points)
        closed_note = ""
        if closed and len(control) > len(points):
            closed_note = (
                " (closed into a single curve by repeating the first point - this is "
                "NOT a periodic spline: the tangent does not match across the seam, "
                "so the curve can overshoot at the closure, and a project + split + "
                "thick_surface chain built on it can leave a zero-volume sliver that "
                "both bounding-box routes will then measure. Reported 2026-10-29, §8: "
                "an 81-point aerofoil closed this way left a 12.75 mm degenerate edge "
                "beyond every source surface, with the solid volume correct to "
                "+0.085 %. If the envelope is part of the specification, measure it "
                "after the solid exists, not before.)"
                " WHY THERE IS NO periodic OPTION HERE: the sketch spline object "
                "('Spline2D', 46 members on B30) exposes IsPeriodic / Period / "
                "Continuity as READ-ONLY and has no SetClosing, so a sketch spline "
                "cannot be closed any other way (measured 2026-11-10). "
                "catia_gsd_spline(closed=true) CAN: HybridShapeSpline.SetClosing(1) "
                "closes a 3D spline properly - 61.2161 mm open vs 77.8649 mm closed "
                "on four 20x15 rectangle corners - so use that when the curve has to "
                "be genuinely closed (a sweep guide, a loft section)."
            )
        return f"Spline created through {len(points)} points: {pts_str}" + closed_note

    def _draw_point(self, x: float, y: float, construction: bool = False) -> str:
        self._ensure_sketch_open()
        factory = self._active_factory
        point = factory.CreatePoint(x, y)
        self._note_element("point", x=x, y=y)
        # CreatePoint makes a CONSTRUCTION point, and user patterns
        # (AddFeatureToLocatePositions) ignore construction geometry - so the
        # default here is real geometry, field-validated on V5R20. The caller can
        # ask for construction, which is what a locator or axis aid usually wants.
        try:
            point.Construction = bool(construction)
        except Exception:
            pass
        kind = "Construction point" if construction else "Point"
        return f"{kind} created at ({x}, {y}) mm"

    # CatConstraintType values from the R20 automation reference
    # (MecModInterfaces, enum CatConstraintType): Reference=0, Distance=1,
    # On=2, Concentricity=3, Tangency=4, Length=5, Angle=6, PlanarAngle=7,
    # Parallelism=8, AxisParallelism=9, Horizontality=10, Perpendicularity=11,
    # AxisPerpendicularity=12, Verticality=13, Radius=14, Symmetry=15.
    _CST_REFERENCE = 0
    _CST_DISTANCE = 1
    _CST_ON = 2
    _CST_TANGENCY = 4
    _CST_LENGTH = 5
    _CST_ANGLE = 6
    _CST_PARALLELISM = 8
    _CST_HORIZONTALITY = 10
    _CST_PERPENDICULARITY = 11
    _CST_VERTICALITY = 13
    _CST_RADIUS = 14

    def _add_constraint(self, args: dict[str, Any]) -> str:
        self._ensure_sketch_open()
        sketch = self._active_sketch
        constraint_type = args["type"]
        value = args.get("value")
        idx1 = args.get("geometry_index_1")
        idx2 = args.get("geometry_index_2")

        constraints = sketch.Constraints
        geom = sketch.GeometricElements
        part = self.conn.get_active_part()

        # AddMonoEltCst/AddBiEltCst take Reference arguments; passing raw
        # geometry elements raises DISP_E_TYPEMISMATCH (field-verified).
        def _ref(index: int, label: str) -> Any:
            if index < 1 or index > geom.Count:
                raise ArgumentOutOfRange(
                    f"{label} is {index}, but this sketch has {geom.Count} "
                    "element(s). Use catia_sketch_get_geometry - it lists the real "
                    "indices."
                )
            element = geom.Item(index)
            if self._is_absolute_axis(element, index):
                # Constraining the absolute axis fails with CATIA's bare
                # "AddMonoEltCst failed" - a message that names neither the axis
                # nor the index. The delete path has refused index 1 for a while;
                # this one did not, so "my first line" (which is index 2) looked
                # broken (measured on B30 2026-11-05: '绝对轴' at 1, '直线.1' at 2).
                raise ArgumentRejected(
                    f"{label} is {index}, which is the sketch's ABSOLUTE AXIS "
                    f"('{element.Name}'), not geometry you drew. The first element "
                    "you draw is index 2. Use the index from "
                    "catia_sketch_get_geometry, where that entry is marked "
                    "absolute_axis: true."
                )
            return part.CreateReferenceFromObject(element)

        # Dimensional constraints (need a geometry reference + value)
        if constraint_type in ("distance", "radius", "angle"):
            if value is None:
                raise ArgumentRejected(f"Constraint type '{constraint_type}' requires a 'value' parameter.")
            if idx1 is None:
                raise ArgumentRejected(f"Constraint type '{constraint_type}' requires 'geometry_index_1'.")

            if constraint_type == "distance" and idx2 is not None:
                cst = constraints.AddBiEltCst(
                    self._CST_DISTANCE,
                    _ref(idx1, "geometry_index_1"),
                    _ref(idx2, "geometry_index_2"))
            elif constraint_type == "distance":
                # A single-element distance is a length dimension in CATIA.
                cst = constraints.AddMonoEltCst(
                    self._CST_LENGTH, _ref(idx1, "geometry_index_1"))
            elif constraint_type == "radius":
                cst = constraints.AddMonoEltCst(
                    self._CST_RADIUS, _ref(idx1, "geometry_index_1"))
            else:  # angle
                if idx2 is None:
                    raise ArgumentRejected("Angle constraint requires 'geometry_index_2'.")
                cst = constraints.AddBiEltCst(
                    self._CST_ANGLE,
                    _ref(idx1, "geometry_index_1"),
                    _ref(idx2, "geometry_index_2"))
            cst.Dimension.Value = value

            unit = "deg" if constraint_type == "angle" else "mm"
            return f"{constraint_type.capitalize()} constraint '{cst.Name}' added: {value} {unit}"

        # Geometric constraints (no value needed)
        cst_type_map = {
            "coincidence": self._CST_ON,
            "tangent": self._CST_TANGENCY,
            "perpendicular": self._CST_PERPENDICULARITY,
            "parallel": self._CST_PARALLELISM,
            "horizontal": self._CST_HORIZONTALITY,
            "vertical": self._CST_VERTICALITY,
            "fix": self._CST_REFERENCE,
        }

        cst_code = cst_type_map.get(constraint_type)
        if cst_code is None:
            raise ArgumentRejected(f"Unknown constraint type: {constraint_type}")

        if constraint_type in ("horizontal", "vertical", "fix"):
            if idx1 is None:
                raise ArgumentRejected(f"Constraint '{constraint_type}' requires 'geometry_index_1'.")
            cst = constraints.AddMonoEltCst(
                cst_code, _ref(idx1, "geometry_index_1"))
        else:
            if idx1 is None or idx2 is None:
                raise ArgumentRejected(
                    f"Constraint '{constraint_type}' requires both 'geometry_index_1' and 'geometry_index_2'."
                )
            cst = constraints.AddBiEltCst(
                cst_code,
                _ref(idx1, "geometry_index_1"),
                _ref(idx2, "geometry_index_2"))

        return f"{constraint_type.capitalize()} constraint '{cst.Name}' added"

    def _locate_sketch(self, sketch_name: str | None) -> tuple[Any, str]:
        """(sketch, how-it-was-found) for the read/edit tools.

        Three sources, in order: the name given, the sketch currently open in the
        editor, the last sketch this session created. The old code only knew the
        open one, so a closed sketch - which is every sketch a feature has
        consumed - answered "No active sketch. Use catia_create_sketch first" and
        there was no way back (reported 2026-10-24).
        """
        body = self.conn.get_active_part_body()
        if sketch_name:
            try:
                return body.Sketches.Item(sketch_name), "by name"
            except Exception:
                # A sketch this server rebuilt answers to the name the caller used
                # before the rollback. Part Design resolved that (0.3.31) and the
                # read/edit tools did not, so the same live sketch was readable by
                # catia_pad and unknown to catia_sketch_get_geometry - which is
                # exactly the tool a caller reaches for after a rollback
                # (reported 2026-10-27).
                alias = self._sketch_alias(sketch_name)
                if alias is not None:
                    try:
                        # `alias` is the name it was rebuilt AS; the caller asked
                        # for `sketch_name`. Printing the target twice answered
                        # "by alias '草图.4'" to a request for 草图.3, which reads
                        # as "found 草图.4 by the alias 草图.4" (reported
                        # 2026-11-08, M5).
                        return (body.Sketches.Item(alias),
                                f"by alias '{sketch_name}' -> '{alias}'")
                    except Exception:  # noqa: BLE001 - fall through to the error
                        pass
                names = self._sketch_names(body)
                hint = ""
                if alias:
                    hint = (f" '{sketch_name}' was re-created as '{alias}', which "
                            "is no longer in the tree either.")
                raise CodedError(
                    f"No sketch named '{sketch_name}' in the active body. "
                    f"Sketches: {names or 'none'}.{hint}",
                    code="SKETCH_NOT_FOUND",
                    modified=False,
                    next_actions=[
                        "Use one of the names in 'Sketches', or call "
                        "catia_list_features to see them with their aliases; "
                        "nothing was created or edited by this call"
                    ], stage="validate",
                ) from None
        if self._active_sketch is not None:
            return self._active_sketch, "open in the editor"
        remembered = getattr(self.conn, "last_sketch", None)
        if remembered is not None:
            try:
                _ = remembered.Name
                return remembered, "last sketch created in this session"
            except Exception:
                self.conn.last_sketch = None
        names = self._sketch_names(body)
        raise PreconditionRejected(
            "No sketch is open and none was created in this session, so there is "
            "nothing to default to. Pass sketch_name explicitly. Sketches in the "
            f"active body: {names or 'none'}."
        )

    def _sketch_alias(self, sketch_name: str) -> str | None:
        """What this sketch name was re-created as, from the connection's table."""
        resolve = getattr(self.conn, "sketch_alias", None)
        return resolve(sketch_name) if resolve is not None else None

    def _sketch_names(self, body: Any) -> str:
        """The live sketches, with every alias that still resolves to each."""
        try:
            finder = getattr(self.conn, "sketch_aliases_of", None)
            names: list[str] = []
            for i in range(1, body.Sketches.Count + 1):
                live = str(body.Sketches.Item(i).Name)
                olds = finder(live) if finder is not None else []
                names.append(f"{live} (alias: {', '.join(olds)})" if olds else live)
            return ", ".join(names)
        except Exception:  # noqa: BLE001 - a listing must not mask the error
            return ""

    @staticmethod
    def _is_absolute_axis(element: Any, index: int) -> bool:
        """True for the sketch's absolute axis, which sits at item 1.

        Measured on B30 2026-11-05: ``GeometricElements`` reads item 1 =
        '绝对轴', item 2 = the first line drawn, item 3 = the second. It is the
        sketch's reference frame, not profile geometry - constraining it fails
        and deleting it breaks the sketch - so every caller-facing index has to
        make that visible. Both the name and the position are checked; either
        alone is an assumption about the build's language or its ordering.
        """
        name = str(getattr(element, "Name", ""))
        if "绝对轴" in name or "AbsoluteAxis" in name:
            return True
        return index == 1

    def _get_geometry(self, sketch_name: str | None = None,
                      hide_construction: bool = True) -> str:
        """List a sketch's elements, open or closed.

        Coordinates are deliberately absent: a CATIA Point2D exposes no readable
        X/Y through automation on this build (GetCoordinates needs a by-ref array
        it never fills), so promising them would be a lie. The element list plus
        the profile echo at close time is what can actually be checked.
        """
        sketch, how = self._locate_sketch(sketch_name)
        geom = sketch.GeometricElements

        elements: list[dict[str, Any]] = []
        construction_count = 0
        for i in range(1, geom.Count + 1):
            elem = geom.Item(i)
            info: dict[str, Any] = {
                "index": i,
                "name": str(elem.Name),
                "absolute_axis": self._is_absolute_axis(elem, i),
            }
            try:
                info["type"] = str(elem.GeometricType)
            except Exception:
                pass
            is_construction = False
            try:
                is_construction = bool(elem.Construction)
            except Exception:
                pass
            info["construction"] = is_construction
            if is_construction:
                construction_count += 1
                if hide_construction:
                    continue
            elements.append(info)

        # A sketch reopened and edited can enumerate fewer elements right after
        # the rebuild than this session drew into it (reported 2026-10-24: 14
        # drawn, 13 listed). The live enumeration is the truth for indices, so
        # the drawn record travels beside it under its own key rather than being
        # spliced in and shifting indices.

        payload: dict[str, Any] = {
            "sketch": str(sketch.Name),
            "document": str(getattr(self.conn.active_document, "Name", "")),
            "found": how,
            "count": len(elements),
            "construction_collapsed": construction_count if hide_construction else 0,
            "elements": elements,
            "note": (
                "Coordinates cannot be read back from a closed sketch: CATIA's "
                "Point2D exposes no readable X/Y through automation on this build. "
                "Use catia_sketch_edit to reopen it and append, or "
                "catia_sketch_delete_element to remove one element. "
                "absolute_axis: true marks the sketch's own reference frame at "
                "index 1 - it is not geometry you drew, and the first element you "
                "draw is index 2; constraints and deletes refuse that index."
            ),
        }
        drawn = self._drawn.get(self._sketch_key(str(sketch.Name)))
        if drawn:
            payload["drawn_this_session"] = drawn
            # Say WHICH sketch this record belongs to. The record is keyed by
            # document::name, so when two sketches share a name it describes
            # whichever was drawn last - and the reply then presented one
            # sketch's profile next to another sketch's geometry ("drawn_this_
            # session 8 points" beside a 4-line rectangle, reported 2026-11-08,
            # N2). The key cannot tell them apart; naming it lets the caller see
            # that the record is shared instead of believing it belongs here.
            payload["drawn_this_session_key"] = self._sketch_key(str(sketch.Name))
            duplicates = sum(
                1 for name in self._all_sketch_names(self.conn.get_active_part())
                if name == str(sketch.Name))
            if duplicates > 1:
                payload["drawn_this_session_note"] = (
                    f"{duplicates} sketches in this part are called "
                    f"'{sketch.Name}', and this session's drawing record is keyed "
                    "by that name - so 'drawn_this_session' may describe a "
                    "different sketch with the same name. The geometry above was "
                    "read from THIS sketch; the record was not. Rename one of them "
                    "(catia_list_features now reports the conflict).")
        return json.dumps(payload, indent=2)

    def _edit_sketch(self, sketch_name: str | None = None) -> str:
        """Reopen a closed sketch for editing; later draws append to it."""
        self.conn.ensure_connected()
        sketch, how = self._locate_sketch(sketch_name)
        name = str(sketch.Name)
        try:
            factory = sketch.OpenEdition()
        except Exception as exc:  # noqa: BLE001 - reported with context
            raise RuntimeError(
                f"CATIA refused to reopen sketch '{name}' ({how}): {exc}. The "
                "sketch may be consumed by a feature that is currently in error - "
                "fix or delete that feature first."
            ) from exc
        self._active_sketch = sketch
        self._active_factory = factory
        self._sketch_part = self.conn.get_active_part()
        self.conn.last_sketch = sketch
        self._profile_ends = []
        # Reopening a sketch wipes the coordinates (CATIA cannot read them back),
        # so the closed-curve count goes with them - a circle drawn from here on
        # is the only thing this session can vouch for.
        self._profile_closed_curves = 0
        try:
            self._last_geom_count = int(sketch.GeometricElements.Count)
        except Exception:  # noqa: BLE001 - spans are then simply unknown
            self._last_geom_count = None
        count = 0
        try:
            count = sketch.GeometricElements.Count
        except Exception:
            pass
        return (
            f"Sketch '{name}' reopened for editing ({how}) - it holds {count} "
            "element(s), and geometry added now is appended to it. Call "
            "catia_close_sketch when done."
        )

    def _delete_element(self, index: int, sketch_name: str | None = None) -> str:
        """Delete one geometric element from a sketch, keeping everything else."""
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        # Volume before the delete, so the reply can show what the profile change
        # actually cost downstream. Without it the caller has to call get_inertia
        # again to know whether anything moved (reported 2026-10-24: 10681.4 ->
        # 10053.1 was left for them to find).
        before = self._solid_volume(part)
        sketch, how = self._locate_sketch(sketch_name)
        name = str(sketch.Name)
        was_open = self._active_sketch is sketch
        if not was_open:
            sketch.OpenEdition()
        total_before = 0
        try:
            geom = sketch.GeometricElements
            total_before = geom.Count
            if index < 1 or index > geom.Count:
                raise ArgumentOutOfRange(
                    f"Element index {index} is out of range: sketch '{name}' has "
                    f"{geom.Count} element(s). Use catia_sketch_get_geometry."
                )
            axis_element = geom.Item(index)
            if self._is_absolute_axis(axis_element, index):
                raise ArgumentRejected(
                    "Element 1 is the sketch's absolute axis, not a profile "
                    "element - deleting it would break the sketch. Pick another "
                    "index from catia_sketch_get_geometry."
                )
            element_name = str(axis_element.Name)
            sel = self.conn.hso
            try:
                sel.Clear()
                sel.Add(geom.Item(index))
                sel.Delete()
            finally:
                try:
                    sel.Clear()
                except Exception:
                    pass
            left = sketch.GeometricElements.Count
            # The record has to follow the delete, or a copy reproduces geometry
            # the sketch no longer has (measured 2026-11-09: two lines, delete the
            # second, the record still held both and the copy's outline was twice
            # the source's - the shape of the "2 spline(s)" report).
            record_note = self._forget_deleted_element(name, index)
            self._last_geom_count = left
        finally:
            record_note = locals().get("record_note", "")
            if not was_open:
                try:
                    sketch.CloseEdition()
                except Exception:  # noqa: BLE001 - best effort
                    pass
        rebuild_error = None
        try:
            part.UpdateObject(sketch)
            # UpdateObject(sketch) rebuilds the sketch, not the features built on
            # it: the reporter's pad stayed at the old volume while this reply
            # claimed the profile had been rebuilt (2026-10-24). A full part
            # update is what makes the claim true.
            part.Update()
        except Exception as exc:  # noqa: BLE001 - the delete itself worked
            rebuild_error = str(exc).splitlines()[0][:140]
        self.conn.refresh_display()
        # Reopen the edition session and leave it open - on BOTH paths. Deleting
        # three elements in a row meant three catia_sketch_edit calls (reported
        # 2026-10-25); and after a failed rebuild the caller needs the sketch
        # open more than ever, to put back what the pad cannot live without.
        reopened = False
        try:
            self._active_factory = sketch.OpenEdition()
            self._active_sketch = sketch
            self._sketch_part = part
            self.conn.last_sketch = sketch
            reopened = True
        except Exception:  # noqa: BLE001 - the delete stands either way
            self._active_sketch = None
            self._active_factory = None
        if rebuild_error is not None:
            return (
                f"Deleted element {index} ('{element_name}') from sketch '{name}', "
                f"{total_before} -> {left} element(s), but the rebuild afterwards "
                f"failed ({rebuild_error}) - features built on this sketch may be "
                "in error."
                + (" The sketch is still open, so you can append the replacement "
                   "geometry directly." if reopened else
                   " Call catia_sketch_edit to repair it.")
                + record_note
            )
        after = self._solid_volume(part)
        change = ""
        if before is not None and after is not None:
            change = (f" Volume {before:.4f} -> {after:.4f} mm3 "
                      f"(delta {after - before:+.4f}).")
        return (
            f"Deleted element {index} ('{element_name}') from sketch '{name}': "
            f"{total_before} -> {left} element(s). Features that consume this "
            "sketch were rebuilt with the new profile." + change
            + (" The sketch is still open for editing, so the next draw appends "
               "to it; call catia_close_sketch before building a feature."
               if reopened else
               " The sketch was closed afterwards; call catia_sketch_edit to "
               "continue drawing.")
            + (" Every element AFTER the deleted one moved down by one, so "
               "re-read catia_sketch_get_geometry before deleting another - an "
               "index reused from before this call removes the wrong curve."
               if left else "")
            + record_note
        )

    @staticmethod
    def _solid_volume(part: Any) -> float | None:
        """Main-body volume in mm3, or None when it cannot be read."""
        try:
            body = part.MainBody
            if body.Shapes.Count == 0:
                return 0.0
            spa = part.Parent.GetWorkbench("SPAWorkbench")
            measurable = spa.GetMeasurable(part.CreateReferenceFromObject(body))
            return measurable.Volume * 1e9
        except Exception:  # noqa: BLE001 - the delta is a courtesy, not the result
            return None
