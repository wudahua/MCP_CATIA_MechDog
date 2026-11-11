"""Generative Shape Design (GSD) tools for CATIA V5.

Wireframe and surface creation via Part.HybridShapeFactory:
points, lines, planes, 3D splines, circles, projections, intersections,
multi-sections surface (loft), sweep, extrude, revolve, fill, blend, offset,
join, split, trim, symmetry — plus ThickSurface/CloseSurface to turn
surfaces back into Part Design solids.

All GSD features live in a Geometrical Set (HybridBody). Elements are
addressed by their tree name; most creation tools accept an optional
'name' to rename the result so later tools can reference it.
All dimensions are in millimeters, angles in degrees.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from catia_mcp.connection import CATIAConnection
from catia_mcp.sketch_frames import ORIGIN_AXIS, plane_angle_frame, point_world
from catia_mcp.results import (ArgumentOutOfRange, ArgumentRejected,
                               CodedError, PreconditionRejected)
from catia_mcp.topo_search import search_topology

# Origin plane aliases (same convention as sketcher.py)
PLANE_MAP = {
    "xy": "PlaneXY",
    "yz": "PlaneYZ",
    "zx": "PlaneZX",
    "xz": "PlaneZX",  # alias
}

# The same idea under a different key, tool by tool: "the plane this sits on" is
# support_plane in catia_gsd_circle, support in catia_gsd_project and base_plane
# in catia_gsd_plane_offset. A wrong spelling does not fail loudly - it lands in
# **args, the tool asks for its own key, and the caller gets
# "KeyError: 'support_plane'", which reads like a server bug rather than a typo
# (cost on 2026-11-04: one debugging round, writing a probe by hand).
#
# catia_gsd_intersection is here for the opposite reason and it matters more:
# its second element is `element2`, so a caller who wrote `cutter` - the name
# catia_gsd_split uses for the same job - got a KeyError that sat inside a
# try/except and was reported as "the surfaces do not intersect". A wrong
# ANSWER, not an error, which is the worst of the three outcomes.
_ARGUMENT_ALIASES: dict[str, dict[str, str]] = {
    "catia_gsd_circle": {"support": "support_plane", "plane": "support_plane"},
    "catia_gsd_project": {"support_plane": "support", "plane": "support"},
    "catia_gsd_plane_offset": {"support": "base_plane", "plane": "base_plane"},
    "catia_gsd_intersection": {"cutter": "element2", "element": "element1"},
    "catia_gsd_split": {"element2": "cutter"},
    "catia_gsd_symmetry": {"mirror_plane": "plane"},
}

# What to do after a split refuses to update. The ORDER is the fix: until 0.3.97
# the first item was "measure a few points of the cutter", which is exactly the
# instruction the 2026-11-10 report followed three times - rebuilding a cutter
# that lay on the surface by construction, because the message body had just
# measured that combination as unsupported. Advice that contradicts the
# measurement is worse than no advice, so the unsupported case now comes first
# and "measure points" is scoped to the one case where it decides anything.
_SPLIT_FAILURE_ACTIONS = (
    "If the cutter came from catia_gsd_project onto a CURVED surface, stop "
    "rebuilding it: that combination is measured-unsupported on B30 - the "
    "projection itself, an extract of it, and a projected circle all failed "
    "(2026-11-10). Use one of the routes below instead",
    "If the element is a JOIN or an array, split the PIECE before the join and "
    "join the results afterwards",
    "If the centre of the part is not flat, do not split: cap the hole with "
    "catia_gsd_circ_pattern(cap_center=true, cap_curve=<the hole's own edge>), "
    "which needs no coplanar disc",
    "To remove material inside a closed curve on a curved surface, build a "
    "solid tool body and call catia_boolean(operation='remove') - its depth "
    "follows an axis, not the surface normal",
    "Only for a PLANE patch is measuring points the right next step: "
    "catia_measure_distance other than 0.0000 mm there means the contour is "
    "not on the surface at all",
)


def _normalise_arguments(tool_name: str,
                         arguments: dict[str, Any]) -> dict[str, Any]:
    """Rename known alternate spellings to the key the tool actually reads.

    Never overrides a key the caller already passed, so an alias can only fill a
    hole and an explicit call stays exactly as explicit as it was.
    """
    aliases = _ARGUMENT_ALIASES.get(tool_name)
    if not aliases or not arguments:
        return arguments
    renamed = dict(arguments)
    for alias, canonical in aliases.items():
        if alias in renamed and canonical not in renamed:
            renamed[canonical] = renamed.pop(alias)
    return renamed


# Keys a tool cannot work without, checked AFTER the aliases are folded in.
#
# They are deliberately absent from each tool's JSON `required`, and that is not
# an oversight: a schema cannot express "one of three spellings" without anyOf,
# so listing the canonical name makes every alias unreachable - the client
# validates against the schema before the server sees the call. 0.3.60 shipped
# exactly that contradiction: the CHANGELOG said "all three answer to all three"
# while `required: [support_plane]` rejected `support` on the client side
# (reported 2026-11-04). The check moved here, where it can name the spellings.
_MISSING_KEYS: dict[str, tuple[str, ...]] = {
    "catia_gsd_circle": ("support_plane",),
    "catia_gsd_project": ("support",),
    "catia_gsd_plane_offset": ("base_plane",),
    "catia_gsd_intersection": ("element2",),
    "catia_gsd_split": ("cutter",),
}


def _missing_key_error(tool_name: str, arguments: dict[str, Any]) -> str | None:
    """The message for a call that gave none of a required key's spellings.

    Normalises internally so the answer does not depend on the caller having
    done it first, while still reporting the keys the CALLER typed.
    """
    resolved = _normalise_arguments(tool_name, arguments)
    missing = [key for key in _MISSING_KEYS.get(tool_name, ())
               if key not in resolved]
    if not missing:
        return None
    aliases = _ARGUMENT_ALIASES.get(tool_name) or {}
    spellings = []
    for key in missing:
        names = [key] + [alias for alias, canonical in aliases.items()
                         if canonical == key]
        spellings.append(" or ".join(f"'{name}'" for name in names))
    return (f"{tool_name} needs {' and '.join(spellings)}; got: "
            f"{sorted(arguments) or 'no arguments'}.")


# Schema fragment: an element addressed by tree name, or [x, y, z] coordinates
# (coordinates create a new 3D point on the fly).
POINT_OR_NAME = {
    "oneOf": [
        {"type": "string", "description": "Name of an existing point element"},
        {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 3,
            "maxItems": 3,
            "description": "[x, y, z] coordinates in mm (creates a new point)",
        },
    ],
}


# A projection of a CLOSED curve onto a round support keeps about HALF of it,
# and that half is usable: the 0.3.98 acceptance geometry (a r=4 trace onto a
# r=40 cylinder) measured 47.2 % of the source and engraved correctly - 2.974
# mm3 out of a 5.9657 mm3 tube. So a "mostly missing" warning cannot sit at 50 %,
# which would fire on the one case that works. It sits below the half a closed
# curve legitimately loses, and above the fragment the 2026-11-10 bottle report
# got: 4.000 mm of a 63.947 mm outline = 6.3 %, one segment of seven.
_PROJECT_FRAGMENT_SHARE = 25.0


class GSDTools:
    """Tools for Generative Shape Design (wireframe & surface) in CATIA V5."""

    DEFAULT_GEOSET = "GSD_Set"

    # Below this a fill is not a face, it is the degenerate result of boundaries
    # that do not close a region (measured 2026-11-09: an open 25.0000 mm line
    # came back as a face with no area at all, while a real closed loop measured
    # 314.1593 mm2). 0.001 mm2 is 30 micrometres square - below anything a caller
    # can mean, above the noise of a legitimate tiny patch.
    _FILL_MIN_AREA_MM2 = 0.001

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection
        self._active_geoset_name: str | None = None
        # Which document the remembered geoset belongs to. CATIA reuses the
        # auto-name 几何图形集.1 / Part1 in every new part, so a name alone
        # cannot tell a fresh document from the one that is gone.
        self._active_geoset_doc: str | None = None
        # How many point features the CURRENT call turned coordinates into.
        # Every one of them is a real tree element, which is the whole reason a
        # 90-point spline leaves 91 elements behind; the count goes into the
        # reply so the caller can see the cost instead of discovering it when
        # catia_gsd_list_elements prints 666 names (reported 2026-11-03).
        self._coordinate_points = 0
        # The names of those points, so the feature built from them can be
        # deleted together with them (catia_delete_feature cascade=true).
        self._coordinate_point_names: list[str] = []

    # ────────────────────────────────────────────────────────────
    # Tool definitions
    # ────────────────────────────────────────────────────────────

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "catia_gsd_create_geoset",
                "description": (
                    "Create a new Geometrical Set (HybridBody) in the active Part and "
                    "make it the active target for subsequent GSD elements. Pass "
                    "'parent' to create it INSIDE another set: without that, a set "
                    "created while another was active still landed at the root, so "
                    "no caller could build the nested tree that "
                    "catia_set_visibility descends into (reported 2026-11-08)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Name for the new geometrical set",
                            "default": "GSD_Set",
                        },
                        "parent": {
                            "type": "string",
                            "description": (
                                "Name of an existing geometrical set to create "
                                "this one inside. Omit for the root of the part."
                            ),
                        },
                    },
                },
            },
            {
                "name": "catia_gsd_set_active_geoset",
                "description": (
                    "Select which Geometrical Set receives subsequently created GSD elements."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Name of an existing geometrical set",
                        },
                    },
                    "required": ["name"],
                },
            },
            {
                "name": "catia_gsd_list_elements",
                "description": (
                    "List all geometrical sets with their wireframe/surface elements and "
                    "sketches, plus sketches in solid bodies. Use this to find element "
                    "names for other GSD tools."
                ),
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "catia_gsd_point",
                "description": "Create a 3D point at (x, y, z). Coordinates in mm.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number", "description": "X coordinate (mm)"},
                        "y": {"type": "number", "description": "Y coordinate (mm)"},
                        "z": {"type": "number", "description": "Z coordinate (mm)"},
                        "name": {"type": "string", "description": "Optional name for the point"},
                    },
                    "required": ["x", "y", "z"],
                },
            },
            {
                "name": "catia_gsd_line",
                "description": (
                    "Create a 3D line between two points. Each point is an existing "
                    "element name or [x, y, z] coordinates."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "point1": POINT_OR_NAME,
                        "point2": POINT_OR_NAME,
                        "name": {"type": "string", "description": "Optional name for the line"},
                    },
                    "required": ["point1", "point2"],
                },
            },
            {
                "name": "catia_gsd_plane_offset",
                "description": (
                    "Create a plane offset from a reference plane. Reference is 'xy', 'yz', "
                    "'zx', or the name of an existing plane element."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "base_plane": {
                            "type": "string",
                            "description": "Reference plane: 'xy', 'yz', 'zx', or element name",
                        },
                        "support": {
                            "type": "string",
                            "description": "Alias for base_plane",
                        },
                        "plane": {
                            "type": "string",
                            "description": "Alias for base_plane",
                        },
                        "offset": {"type": "number", "description": "Offset distance (mm)"},
                        "reverse": {
                            "type": "boolean",
                            "description": "Offset in the opposite direction",
                            "default": False,
                        },
                        "name": {"type": "string", "description": "Optional name for the plane"},
                    },
                    # base_plane left out of required so its aliases can stand in.
                    "required": ["offset"],
                },
            },
            {
                "name": "catia_gsd_plane_3points",
                "description": "Create a plane through three points (element names or [x,y,z]).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "point1": POINT_OR_NAME,
                        "point2": POINT_OR_NAME,
                        "point3": POINT_OR_NAME,
                        "name": {"type": "string", "description": "Optional name for the plane"},
                    },
                    "required": ["point1", "point2", "point3"],
                },
            },
            {
                "name": "catia_gsd_plane_normal_to_curve",
                "description": (
                    "Create a datum plane NORMAL to a curve, with its origin ON that "
                    "curve at a given fraction of its length - the tutorial's "
                    "'Normal to Curve + Set Origin on Curve'. 'position' is 0..1 "
                    "(0 start, 1 end, 0.5 middle) and it IS a fraction: measured on "
                    "B30 2026-11-08, a point at 0.5 on a 100 mm line put the plane "
                    "0.000 mm from a datum point at z=50. The reply states the "
                    "curve's measured length and the distance along it, because the "
                    "point's own coordinates cannot be read back on this build. Both "
                    "halves are real type-library members (AddNewPlaneNormal(iCurve, "
                    "iPt) and AddNewPointOnCurveFromPercent(iCrv, iLong, "
                    "iOrientation) - note the THREE arguments, a two-argument call "
                    "answers 'invalid number of arguments'), and the plane this "
                    "produces is a TREE ELEMENT: unlike the frame sketch that used "
                    "to be the only way, later features can reference it."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "curve": {
                            "type": "string",
                            "description": "Name of the curve (line, spline, circle, edge) the plane is normal to",
                        },
                        "position": {
                            "type": "number",
                            "description": "Fraction of the curve's length for the plane's origin (0..1, default 0.5)",
                            "default": 0.5,
                        },
                        "name": {"type": "string", "description": "Optional name for the plane"},
                    },
                    "required": ["curve"],
                },
            },
            {
                "name": "catia_gsd_plane_angle",
                "description": (
                    "Create a datum plane rotated a given angle about an axis - "
                    "the tutorial's Step 3 ('以轴线为参考，新建 36.00° 的基准面'), "
                    "and the general way to take one 1/N sector of a revolved "
                    "part. 'plane' is 'xy'/'yz'/'zx' or the name of any planar "
                    "element in the tree; 'axis' is 'x'/'y'/'z' (a line on the "
                    "origin axis is built for you) or the name of a line. The "
                    "result is a TREE ELEMENT: visible, named, editable and "
                    "referenceable - which is what the frame-sketch workaround "
                    "could never be. "
                    "TWO MEASURED RULES. (1) THE AXIS HAS TO LIE IN THE "
                    "REFERENCE PLANE. Rotating a plane about its own normal "
                    "leaves it where it was, and CATIA answers with a generic "
                    "E_FAIL that looks like a bad argument - measured on B30 "
                    "2026-11-09, eight argument shapes were refused before the "
                    "geometry was blamed: plane='xy' with axis='z' can never "
                    "work, plane='yz' with axis='z' does. (2) The angle is in "
                    "DEGREES: 72 put the plane 0.0000 mm from a point at 72 "
                    "degrees, while 1.2566 (the same angle in radians) was read "
                    "as 1.2566 degrees."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "plane": {
                            "type": "string",
                            "description": "Reference plane: 'xy', 'yz', 'zx', or "
                                           "the name of a planar element in the tree",
                        },
                        "axis": {
                            "type": "string",
                            "description": "Rotation axis: 'x', 'y', 'z', or the "
                                           "name of a line element. It must lie in "
                                           "the reference plane",
                        },
                        "angle": {
                            "type": "number",
                            "description": "Rotation angle in DEGREES (72 for a "
                                           "five-fold sector)",
                        },
                        "orientation": {
                            "type": "integer",
                            "description": "Passed to AddNewPlaneAngle unchanged "
                                           "(default 0). Measured: 0, 1 and -1 all "
                                           "gave the same plane on a YZ/Z case, so "
                                           "this is not interpreted here",
                            "default": 0,
                        },
                        "name": {"type": "string",
                                 "description": "Optional name for the plane"},
                    },
                    "required": ["plane", "axis", "angle"],
                },
            },
            {
                "name": "catia_gsd_spline",
                "description": (
                    "Create a 3D spline through a list of points. Each point is an "
                    "existing element name or [x, y, z] coordinates in mm. "
                    "Pass 'element' instead of 'points' to take the corners of a "
                    "sketch THIS SERVER drew in this session: useful when the "
                    "sketch holds a polygon (catia_sketch_rectangle, or a chain of "
                    "catia_sketch_line calls), because a loft section is ONE curve "
                    "and a polygon outline is four - the spline turns it into a "
                    "curve that can be lofted. The corners come back in loop order "
                    "and in global XYZ, converted through the plane the sketch was "
                    "created on (xy/yz/zx; a sketch on a face has no readable "
                    "frame and is refused rather than guessed). It also saves "
                    "transcribing coordinates: they were typed once, when the "
                    "polygon was drawn. "
                    "WHAT YOU GET IS A SPLINE, NOT THE POLYGON: the curve "
                    "interpolates, so it bulges outward between the corners and "
                    "bulges most across the short side - a closed spline through "
                    "the corners of a 20 x 12 rectangle measured 24.04 x 21.46 on "
                    "B30 2026-10-25 (+20 % / +79 %). If the sections are a "
                    "specification with tolerances, that bulge is a shape change, "
                    "not a conversion; check the envelope after using this, or "
                    "cancel it: scale the points toward their centroid by "
                    "k = sqrt(target area / measured area) and rebuild once - "
                    "measured on a nine-section blade, k=0.76 landed within 2 %. "
                    "SHORTER ROUTE on the sketch side: catia_sketch_spline("
                    "closed=true) draws the same interpolating curve INSIDE the "
                    "sketch, so the sketch is already one curve and can be a loft "
                    "section directly - measured on B30 2026-10-25, such a sketch "
                    "lofts and closes into a solid. Use this 3D spline when the "
                    "curve has to live in GSD (blend, trim, offset); use the sketch "
                    "spline when it just has to be loftable. "
                    "NARROW FEATURE: DO NOT PUT A PLATEAU AND A NARROW TRANSITION "
                    "BAND SIDE BY SIDE IN ONE CLOSED SECTION. A section that is "
                    "flat over an arc and then transitions over a much shorter arc "
                    "cannot be sampled: at 4 degrees per point a 0.6 degree band "
                    "falls between two samples and the section jumps (13 mm on a "
                    "16 mm claw), and tightening the sampling to catch it drives "
                    "the spacing below 0.1 mm. The way through is one continuous "
                    "function whose SHAPE is changed instead of its piecewise "
                    "structure: z = z_valley + (z_foot - z_valley) * "
                    "smoothstep((psi/11)**n), which puts plateau and transition on "
                    "the same curve, and n = ln(0.5)/ln(W_half/11) sets the "
                    "half-height width W_half directly. Reported from a five-petal "
                    "bottle base on B32 SP6 2026-11-03: with that mapping a "
                    "8.50 mm valley width, a 50 degree claw width and a 0.3 mm wall "
                    "offset all held in one 12-step plan at a uniform 4 degree "
                    "sampling, where the plateau+band version had twice failed to "
                    "thicken. Reported, not measured here - the arithmetic is the "
                    "caller's, but the sampling argument is worth reading before "
                    "declaring a shape impossible."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "points": {
                            "type": "array",
                            "items": POINT_OR_NAME,
                            "minItems": 2,
                            "description": "Points the spline passes through, in order",
                        },
                        "element": {
                            "type": "string",
                            "description": (
                                "Sketch name (e.g. '草图.1') whose drawn outline "
                                "becomes the point list, in loop order. Only "
                                "sketches this server drew in this session, on a "
                                "datum plane. Mutually exclusive with 'points'."
                            ),
                        },
                        "closed": {
                            "type": "boolean",
                            "description": "Close the spline into a loop",
                            "default": False,
                        },
                        "create_points": {
                            "type": "boolean",
                            "description": (
                                "Put the coordinate points in the tree as Point "
                                "features. Default false: the points are still "
                                "created, still referenced by the spline and still "
                                "saved with the part (measured 2026-11-03: save + "
                                "reopen keeps the curve at its exact length), they "
                                "are just not listed - which keeps a hundred-point "
                                "ring at one tree element instead of a hundred and "
                                "one. Set true only when you want to select, "
                                "measure or move the individual points."
                            ),
                            "default": False,
                        },
                        "name": {"type": "string", "description": "Optional name for the spline"},
                    },
                    "required": [],
                },
            },
            {
                "name": "catia_gsd_circle",
                "description": (
                    "Create a full 3D circle from a center point, a support plane, and a "
                    "radius. Useful as a section for multi-sections surfaces."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "center": POINT_OR_NAME,
                        "support_plane": {
                            "type": "string",
                            "description": "Support plane: 'xy', 'yz', 'zx', or element name",
                        },
                        # The same idea under the names the sibling tools use.
                        # Declared here because a client validates against this
                        # schema BEFORE the server can normalise anything, so an
                        # alias the schema does not list is unreachable no matter
                        # what execute() accepts (reported 2026-11-04 - the
                        # CHANGELOG claimed "all three answer to all three" while
                        # the schema refused two of them).
                        "support": {
                            "type": "string",
                            "description": "Alias for support_plane",
                        },
                        "plane": {
                            "type": "string",
                            "description": "Alias for support_plane",
                        },
                        "radius": {"type": "number", "description": "Radius (mm)"},
                        "name": {"type": "string", "description": "Optional name for the circle"},
                    },
                    # support_plane is NOT required here for the sole reason that
                    # aliases have to be able to stand in for it; the server
                    # rejects the call when none of the three is given. Message is
                    # from _MISSING_KEYS below.
                    "required": ["center", "radius"],
                },
            },
            {
                "name": "catia_gsd_project",
                "description": (
                    "Project a curve or point onto a support surface or plane, and "
                    "MEASURE WHAT LANDED. A projection runs along the support's own "
                    "normal, so on a curved support part of the element can miss it "
                    "entirely: measured on B30 2026-11-10, a 63.947 mm glyph "
                    "outline onto a lofted bottle face produced a 4.000 mm curve - "
                    "6.3 %, one segment - and the reply said only 'created'. "
                    "catia_measure_distance then answered 0.0000 mm, because that "
                    "is a MINIMUM: the fragment touches, so the number looks "
                    "perfect. The reply now starts with WARNING and carries the "
                    "projected length against the source's, so a fragment cannot be "
                    "mistaken for the whole element. That matters downstream: "
                    "catia_surface_engrave sweeps a tube along THIS curve, and a "
                    "4 mm tube removes nothing (measured the same day)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "element": {
                            "type": "string",
                            "description": "Name of the element to project",
                        },
                        "support": {
                            "type": "string",
                            "description": "Projection support: 'xy', 'yz', 'zx', or element name",
                        },
                        "support_plane": {
                            "type": "string",
                            "description": "Alias for support",
                        },
                        "plane": {
                            "type": "string",
                            "description": "Alias for support",
                        },
                        "name": {"type": "string", "description": "Optional name for the result"},
                    },
                    # 'support' left out of required so its aliases can stand in;
                    # the server rejects a call that gives none of the three.
                    "required": ["element"],
                },
            },
            {
                "name": "catia_gsd_extract",
                "description": (
                    "Extract a curve or surface from existing geometry into the active "
                    "geometrical set. Needed whenever a downstream feature demands a "
                    "wire/surface but you only have a sketch or a solid's topology: "
                    "catia_gsd_fill's boundaries, and loft sections that came from a "
                    "multi-entity outline, both fail on a raw sketch reference and work "
                    "on the Extract."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "element": {
                            "type": "string",
                            "description": (
                                "Element to extract: a tree name, or 'Edge.N' / 'Face.N' "
                                "taken from the active body (see catia_list_edges)"
                            ),
                        },
                        "propagation": {
                            "type": "integer",
                            "enum": [1, 2, 3, 4],
                            "default": 1,
                            "description": (
                                "1 = none (this element only), 2 = point, 3 = tangent "
                                "continuity, 4 = curvature continuity"
                            ),
                        },
                        "name": {"type": "string", "description": "Optional name for the result"},
                    },
                    "required": ["element"],
                },
            },
            {
                "name": "catia_gsd_heal",
                "description": (
                    "STITCH separate surfaces into one: closes the gaps between "
                    "them up to 'merging_distance', and that is all it does. "
                    "MEASURED on B30 2026-11-03 with every state isolated in its "
                    "own STL - two lofts whose sections sit 0.05 mm apart start "
                    "with 48 open edges at the seam; healing them at Continuity 1 "
                    "with merging_distance 0.5 turns those into 24 interior edges "
                    "and 0 open ones (the same 112 triangles, same extent), while "
                    "merging_distance 0.001 leaves all 48 open. So the distance "
                    "has to exceed the gap. "
                    "WHAT IT DOES NOT DO: it does not change CONTINUITY. Two lofts "
                    "sharing a section exactly - one geometry, two "
                    "parametrisations, 0.52 deg across the seam - came out "
                    "identical at Continuity 1 and 2: same 112 triangles, same 24 "
                    "interior edges, same 0.52 deg. And an honest G0 fold stays: "
                    "two walls at 90 deg read 90.0 before and after, 4 triangles "
                    "either way. If a seam is already joined and you want it "
                    "smooth, the surfaces have to be rebuilt tangent "
                    "(catia_gsd_blend, or sections that meet properly) - there is "
                    "no smoothing operator in this automation. "
                    "WHY THAT IS WORTH SAYING TWICE: HybridShapeHealing exposes "
                    "AddBodyToHeal, SetMergingDistance, SetTangencyAngle and more, "
                    "and with NO body registered it is a pass-through that reports "
                    "success. 0.3.51 concluded 'healing does nothing' from exactly "
                    "that pass-through; this tool registers every element you name."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "elements": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "description": (
                                "Surfaces (or curves) to stitch, by tree name. "
                                "Two or more for a seam; one is accepted and "
                                "heals that element's own gaps."),
                        },
                        "continuity": {
                            "type": "integer",
                            "enum": [0, 1, 2],
                            "default": 1,
                            "description": (
                                "0 = threshold (no curvature change), 1 = tangent, "
                                "2 = curvature. Measured: the value changes nothing "
                                "about an already closed seam, so 1 is the honest "
                                "default."),
                        },
                        "merging_distance": {
                            "type": "number",
                            "default": 0.1,
                            "description": (
                                "Gaps up to this size are closed, in mm. Measured: "
                                "0.001 left a 0.05 mm gap open, 0.5 closed it. Read "
                                "the gap with catia_measure_distance first; too "
                                "large a value merges things you meant to keep "
                                "apart."),
                        },
                        "name": {"type": "string", "description": "Optional name for the result"},
                    },
                    "required": ["elements"],
                },
            },
            {
                "name": "catia_gsd_fillet_surface",
                "description": (
                    "Round the edge where TWO SURFACES meet: a rolling-ball fillet "
                    "of the given radius, as a GSD surface. "
                    "MEASURED on B30 2026-11-03, and the reason this tool exists: "
                    "AddNewFilletBiTangent(el1, el2, r, o1, o2, trim, relim) "
                    "updates for exactly ONE of the four orientation pairs on a "
                    "straight corner, (-1, +1), and what it builds is a real "
                    "fillet - on 60/90/120 deg wedges every mesh vertex sat at "
                    "exactly r from the rolling-ball axis (min = max = r, area "
                    "314.159 mm2 for r=5 on a 40 mm 90 deg edge, i.e. "
                    "(pi - theta) * r * L to 0.001 %), and the same held for a "
                    "floor-and-wall corner built in a different direction - so "
                    "(-1,+1) is a property of the call, not of the build order. "
                    "This tool tries all four and keeps what CATIA accepts, but "
                    "ONLY when exactly one survives. None surviving means the two "
                    "surfaces do not form a corner (measured: two disjoint "
                    "parallel cylinders, 0 of 9 combinations, which is the "
                    "arrangement 0.3.53 mistook for a verdict). MORE THAN ONE "
                    "surviving means the pair is not a plain corner: a disc with a "
                    "cylinder standing on it built both (+1,+1) and (+1,-1), "
                    "573.4 and 391.3 mm2, two different surfaces - and then this "
                    "tool refuses rather than picking one for the caller. "
                    "Trim is fixed at 0 on purpose: trim=1 also trims the SUPPORT "
                    "surfaces, so the result covers the walls as well (measured: "
                    "the same 90 deg case jumped from 314.159 to 4714.159 mm2). "
                    "Round a SOLID edge with catia_fillet when the wall is thick "
                    "enough for the radius; catia_gsd_blend joins two curves, it "
                    "does not round an edge."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "surface1": {
                            "type": "string",
                            "description": "First surface, by tree name",
                        },
                        "surface2": {
                            "type": "string",
                            "description": (
                                "Second surface, by tree name. It has to share an "
                                "edge with the first: two surfaces that meet at a "
                                "corner, not two that merely exist."),
                        },
                        "radius": {
                            "type": "number",
                            "exclusiveMinimum": 0,
                            "description": "Fillet radius in mm",
                        },
                        "name": {"type": "string", "description": "Optional name"},
                    },
                    "required": ["surface1", "surface2", "radius"],
                },
            },
            {
                "name": "catia_gsd_spine",
                "description": (
                    "Create a SPINE: the curve normal to a series of section "
                    "curves - reference geometry for carrying sweep sections "
                    "along. Feed the result to catia_gsd_sweep as its 'guide'. "
                    "CORRECTION to what 0.3.53 wrote: 'the typelib names no way to "
                    "give it sections' was wrong. HybridShapeSpine declares 40 "
                    "members, among them AddSection(iSection), AddGuide(iGuide), "
                    "GetNumberOfSections(), GetSection and ModifySectionCurve; the "
                    "0.3.53 probe listed dir(spine) filtered for 'Add' and found "
                    "nothing, which for a late-bound COM object proves nothing at "
                    "all. "
                    "MEASURED on B30 2026-11-03: AddSection takes a circle, "
                    "GetNumberOfSections counts it, and UpdateObject SUCCEEDS - "
                    "the failure 0.3.53 measured was on an EMPTY spine, which is a "
                    "different statement. One section is not enough (update "
                    "fails). And it is a spine, not a line through the centres: "
                    "three parallel rings 40 mm apart gave 40.0000 mm along their "
                    "common axis and stayed there when a centre was offset 30 mm "
                    "sideways, because a spine follows the sections' NORMALS; a "
                    "second ring tilted 45 deg turned it into a 37.9224 mm curve "
                    "that leaves that axis by 7.05 mm at z=10. "
                    "Guides are accepted too (AddGuide moved the guide count to 1 "
                    "and the update stayed clean)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "sections": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 2,
                            "description": (
                                "Section curves by tree name, at least two. Their "
                                "orientation decides the spine's direction, so "
                                "tilted sections make it curve."),
                        },
                        "guides": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional guide curves",
                        },
                        "name": {"type": "string", "description": "Optional name"},
                    },
                    "required": ["sections"],
                },
            },
            {
                "name": "catia_gsd_boundary",
                "description": (
                    "Extract the BOUNDARY CURVE of a surface into the active "
                    "geometrical set. This is how a patch is rebuilt from its own "
                    "border: take the boundary, delete the face, loft again over the "
                    "same outline - the workflow a C0 seam between two joined "
                    "patches needs, and the one catia_gsd_extract cannot do (an "
                    "extract of a face gives a surface, not a wire). "
                    "MEASURED on B30 2026-11-03: AddNewBoundaryOfSurface takes ONE "
                    "argument and updates; on the 30 mm extrusion of a r=10 circle "
                    "the result measured 125.664 mm, exactly the two circles. A "
                    "closed surface gives one curve carrying both loops, so the "
                    "length is the sum of the borders - that is not a defect. "
                    "TAKING ONE LOOP (0.3.85): pass 'edge' and the reply is that "
                    "loop alone - AddNewBoundary(edge, support, propagation) has "
                    "the selector AddNewBoundaryOfSurface lacks (three arguments "
                    "against one). Measured on a cone: propagation 0 -> 188.496 mm "
                    "(both loops), 1 -> 62.832 mm (the r=10 loop) or 125.664 mm "
                    "(the r=20 loop) depending on which edge is named, 2 -> the "
                    "same as 1 on a smooth border. This is a SINGLE-FEATURE tool: "
                    "on a JOIN result (an array) all 30 border edges of a "
                    "five-patch base refused at update, while the whole-border "
                    "call on that same surface worked. "
                    "NOT the same tool as catia_gsd_extract (which copies the "
                    "element you name, keeping its type). "
                    "This does NOT smooth a seam: CATIA V5 automation exposes no "
                    "Match and AddNewHealing leaves a 90-degree fold at 90 degrees "
                    "(measured), so a G0 seam stays G0 unless the surfaces are "
                    "rebuilt tangent."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "surface": {
                            "type": "string",
                            "description": (
                                "Surface whose border you want: a tree name, or "
                                "'Face.N' from the active body"),
                        },
                        "edge": {
                            "type": "integer",
                            "minimum": 1,
                            "description": (
                                "Take ONE loop of the border instead of all of "
                                "them: the number of the surface-border edge, "
                                "counted within 'surface'. Without it the reply is "
                                "one curve carrying every loop (fill that and you "
                                "rebuild every loop as a separate face). Measured "
                                "on a cone (B30 2026-11-09): edge 1 -> 62.832 mm "
                                "(r=10 loop), edge 2 -> 125.664 mm (r=20 loop); "
                                "the fills were 314.159 and 1256.637 mm2, against "
                                "1570.796 for both loops at once (the two discs "
                                "ADDED, not the lateral area). "
                                "ON AN ARRAY RESULT THIS REFUSES: all 30 border "
                                "edges of a five-patch join failed at "
                                "UpdateObject while the whole-border call on the "
                                "same surface worked. Take the loop from the "
                                "source piece before the join, or let "
                                "catia_gsd_circ_pattern cap the centre."
                            ),
                        },
                        "propagation": {
                            "type": "integer",
                            "enum": [0, 1, 2],
                            "default": 1,
                            "description": (
                                "Only used with 'edge'. 0 returns the whole border "
                                "(all loops, same as omitting 'edge'), 1 the loop "
                                "the chosen edge belongs to, 2 the tangent chain "
                                "from it. 1 and 2 gave identical results on a "
                                "smooth border (measured); they differ where the "
                                "border has corners."
                            ),
                        },
                        "name": {"type": "string", "description": "Optional name for the result"},
                    },
                    "required": ["surface"],
                },
            },
            {
                "name": "catia_gsd_intersection",
                "description": "Create the intersection of two elements (e.g. surface ∩ plane).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "element1": {"type": "string", "description": "First element name"},
                        "element2": {
                            "type": "string",
                            "description": "Second element: 'xy', 'yz', 'zx', or element name",
                        },
                        "cutter": {
                            "type": "string",
                            "description": "Alias for element2 (the name catia_gsd_split uses)",
                        },
                        "name": {"type": "string", "description": "Optional name for the result"},
                    },
                    # element2 left out of required so 'cutter' can stand in - and
                    # so a caller who writes it does not get a KeyError that reads
                    # as "the surfaces do not intersect".
                    "required": ["element1"],
                },
            },
            {
                "name": "catia_gsd_multi_section_surface",
                "description": (
                    "Create a Multi-sections Surface (loft) through 2+ section curves "
                    "(sketches, circles, splines...), optionally following guide curves. "
                    "Sections should be listed in order along the lofting direction. A "
                    "section may also be a solid's own topology - 'Edge.3' takes it "
                    "straight from the finished body (see catia_list_edges), which is "
                    "how you loft from an existing end-face edge without redrawing it. "
                    "Set 'ruled' for a STRAIGHT-SIDED loft: it is built as one blend "
                    "per section pair, joined, which reproduces a 'loft with no end "
                    "tangency' (measured on B30: 73304.7 mm3 vs 73303.8 analytic for "
                    "r=10/20/10, against 90059.0 for the smooth loft). The default "
                    "smooth loft interpolates THROUGH the sections and bulges between "
                    "them, so a spec whose ends are straight cannot be met that way. "
                    "Note on tangency: the API does have an end condition - "
                    "SetStartSectionTangent / SetEndSectionTangent, one support "
                    "surface each - so 'keep the loft tangent to the boss it grows "
                    "out of' is possible: pass the surface as start_tangent / "
                    "end_tangent. Measured on B30 2026-10-24: a cylindrical surface "
                    "through the first section attached as start_tangent updates and "
                    "changes the area by -4.9 % (10701.639 -> 10174.914 mm2 on "
                    "r=10/20/10), while the same surface attached to the far end "
                    "fails to update - the support has to touch that section. What "
                    "does NOT exist: a tangent factor, a curvature factor, or a "
                    "MaintainTangency switch (every *TangentFactor / MaintainTangency "
                    "name is absent from the object; HybridShapeLoft exposes 78 "
                    "members on B30, read from its type information 2026-11-10). "
                    "TUTORIAL PARAMETERS: 'normal_to_profile', 'tangent_factor' "
                    "and 'guide_influence' are accepted as aliases, so a tutorial "
                    "recipe gets an answer about the tutorial - a coded refusal "
                    "naming the measurement and the route that does work - instead "
                    "of 'root: must NOT have additional properties'. "
                    "BUILD SENSITIVITY: reported failing on V5-6R2022 (B32) with "
                    "'UpdateObject failed' even for two coaxial equal-radius circles "
                    "whose pair catia_gsd_blend accepts in the same session "
                    "(reported 2026-10-23, 7 configurations, one B32 SP6 session). "
                    "Two later B32 SP6 sessions built both the smooth and the ruled "
                    "loft and matched B30 to 0.02 mm3 (2026-10-24), so treat this as "
                    "SESSION-sensitive, not build-determined: if it fails, retry once "
                    "in a fresh session before working around it, and prefer "
                    "catia_gsd_blend for the same section pair in the meantime. "
                    "ONE CURVE PER SECTION, and 'closed' is not the same as 'one "
                    "curve'. Measured on B30 2026-10-25, two sections each: "
                    "gsd_circle builds, a SKETCH CIRCLE builds, a closed 3D spline "
                    "through the corners builds - but a sketch rectangle (four "
                    "edges) fails, and so does its Extract. The rectangle is a "
                    "closed loop, yet the sketch stores it as four curves, and a "
                    "loft section is ONE curve: CATIA does not associate a loop. "
                    "Extracting the profile does not help either, because the "
                    "extract is still four cells. So to loft a polygon, pass a "
                    "closed catia_gsd_spline through its corners - or draw the "
                    "section as a closed catia_sketch_spline, which is one curve "
                    "already - that is the only form measured to work, and it is "
                    "also why a spec that gives corner coordinates ends up as a "
                    "spline section rather than the polygon it describes. "
                    "A SPLINE IS NOT THE POLYGON: it interpolates, so it bulges "
                    "outward between the corners, and it bulges most across the "
                    "SHORT side. Measured on B30 2026-10-25, a closed spline "
                    "through the corners of a 20 x 12 rectangle swept "
                    "24.04 x 21.46 - +20 % in x and +79 % in y. Expect the same "
                    "sign of error whenever a quadrilateral section is replaced by "
                    "a spline through its corners; it is a substitution, not a "
                    "neutral conversion. "
                    "SPAN LIMIT, and it is about the OFFSET, not this tool: a "
                    "smooth loft that builds is not a loft that can be thickened. "
                    "Because it interpolates through the sections, a rapid change "
                    "of section size tightens the curvature BETWEEN two sections, "
                    "and that tightened region is what refuses a thin-wall offset "
                    "later. Reported 2026-11-03 from a five-petal bottle base: "
                    "the six-section span [02..07] of ten built a surface and then "
                    "catia_gsd_thick_surface refused 0.3 mm, where every 2-3 "
                    "section span of the same sections thickened; the 7 and 10 "
                    "section spans did not build at all for that model. So a 6-10 "
                    "section span building is not permission to offset it - when "
                    "the part needs a thin wall, build 2-3 section pieces and "
                    "catia_gsd_join them, and read catia_check_surface before "
                    "offsetting. "
                    "WHEN THE LOFT REFUSES TO UPDATE ON A REAL PART, check the "
                    "ARRANGEMENT before the curve type. This surface interpolates "
                    "through the sections and stays inside their hull, so a section "
                    "that sits past the turn of its neighbours makes it "
                    "self-intersect. Measured on B30 2026-11-03: sections r 2.9 / "
                    "3.0 / 8 with the middle one at z -17.58 above both neighbours "
                    "(-19 and -25) fail as circles, as 12-point closed splines and "
                    "as 90-point closed splines alike, while the same three radii in "
                    "a monotone stack build. Changing a circle into a closed spline "
                    "is therefore NOT a fix for that failure; keeping the sections "
                    "monotone, splitting the loft at the turn, or 'ruled' are. "
                    "WHEN ADJACENT SECTIONS HAVE THEIR ANGULAR FEATURES OUT OF "
                    "PHASE (lobes, claws or ribs sitting at different angles), a "
                    "report from a five-petal bottle base says the loft builds and "
                    "catia_gsd_thick_surface then refuses 0.3 mm, because the "
                    "interpolation between the two rings makes an S-twist whose "
                    "curvature is too tight. Measured on B30 2026-11-03 on an "
                    "isolated three-ring lobed loft (r = R + 2*cos(5t + phase), "
                    "phases 0/30/0 degrees - a far bigger shift than the 10-12 "
                    "degrees in that model): the loft built AND thickened 0.3 mm, "
                    "at 40 and at 90 points per section, as did 0/15/0 and 0/0/0. "
                    "So a phase shift on its own is not enough to reproduce it. In "
                    "the model that reported it, the failing span also crossed "
                    "joins between six patches and a split, so before re-cutting "
                    "the sections find out whether the refusal is at a JOINED SEAM: "
                    "run catia_check_surface with region={'z': [...]} (or r) over "
                    "the span and see whether the tight edges cluster at one seam."
                    "POINT AND VERTEX SECTIONS ARE NOT SUPPORTED: the automation "
                    "loft accepts a datum point and then fails the update, and "
                    "refuses a solid Vertex.N outright with 'invalid number of "
                    "arguments', even though the CATIA UI lets you pick a vertex as "
                    "an end section (measured on B30 2026-11-03, both orders). Both "
                    "are now refused before anything is built; end the loft on a "
                    "small closed section where the apex would be and cap it with "
                    "catia_gsd_fill instead. "
                    "WHEN THE SOURCE BULGES PAST ITS OWN SECTIONS: this surface "
                    "interpolates through the sections and never runs outside "
                    "them, and neither does a spline through the same corners. If "
                    "the source part's envelope is larger than its sections, the "
                    "extra comes from a tangency condition with a tension factor "
                    "(SolidWorks 'maintain tangency, factor 1.0'), and CATIA's GSM "
                    "loft has no such control: the two end conditions it does have "
                    "are support-surface tangency, which needs a surface touching "
                    "the section. The same gap shows up as SolidWorks' 'Normal to "
                    "Profile' with a strength/tangent factor (1.5, say): there is no "
                    "1:1 equivalent, so following the tutorial produces a different "
                    "end curvature and a different bulge - a caller abandoned that "
                    "route for exactly this (reported 2026-11-08); it is not a "
                    "parameter under another name. "
                    "The way through is to model the bulge instead of "
                    "asking the loft for it - add sections where the source is "
                    "thickest, with area and span read off measured cross-sections "
                    "of the source part. Worked example, a propeller blade whose "
                    "sections at z=0/19.05/158.75 span x -4.604..16.256 while the "
                    "part reaches x +-23.222: add a section at z=30 of 78.0 mm2 "
                    "(x span 25.7, y span 13.7) and one at z=70 of 106.4 mm2 "
                    "(x span 30.1, y span 14.6), and the envelope lands within "
                    "5 %. 'ruled' is not the alternative here: it reproduces a "
                    "straight-sided frustum, which came out 67 % short on that "
                    "same part."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "sections": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 2,
                            "description": "Section curve element names, in lofting order",
                        },
                        "guides": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional guide curve element names. Each guide MUST "
                                "geometrically intersect every section, or the surface fails."
                            ),
                        },
                        "ruled": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Straight sides between consecutive sections "
                                "(piecewise ruled, built from blends and joined) "
                                "instead of one interpolated NURBS surface. Use this "
                                "when the spec says the ends are tangent-continuous "
                                "or the volume must match the analytic frustum - and "
                                "also when the smooth loft refuses to update because "
                                "it would self-intersect: ruled covers all sections "
                                "in one call (10 sections measured at 1.39 s, "
                                "2026-11-03). Both are C0; the only difference is "
                                "whether the tool or the caller joins the pieces."
                            ),
                        },
                        "start_tangent": {
                            "type": "string",
                            "description": (
                                "Element name of a surface (or face) the FIRST section "
                                "lies on; the loft leaves that section tangent to it. "
                                "This is 'keep tangency' in the UI. The support must "
                                "actually touch the section - CATIA accepts the setter "
                                "and then fails the update otherwise. No tangent factor "
                                "is exposed by the API."
                            ),
                        },
                        "end_tangent": {
                            "type": "string",
                            "description": (
                                "Element name of a surface (or face) the LAST section "
                                "lies on; the loft leaves that section tangent to it. "
                                "Same rule as start_tangent: the support has to touch "
                                "the section, and no tangent factor is exposed."
                            ),
                        },
                        "orientations": {
                            "type": "array",
                            "items": {"type": "integer", "enum": [1, -1]},
                            "description": (
                                "Optional per-section orientation, same length/order as "
                                "sections. Set -1 for a section whose curve direction opposes "
                                "the others (otherwise the loft twists or fails). Default: all 1."
                            ),
                        },
                        "name": {"type": "string", "description": "Optional name for the surface"},
                        "tangent_factor": {
                            "type": "number",
                            "description": (
                                "TUTORIAL ALIAS, accepted so the answer is about the "
                                "tutorial and not about JSON Schema. A guide that "
                                "says 'strength 1.5' means CATIA's tension field; "
                                "HybridShapeLoft exposes 78 members on B30 and none "
                                "of them carries a tension (measured 2026-11-10). "
                                "1.0 is accepted and changes nothing - it is the "
                                "only value this API has. Any other value is refused "
                                "with the measurement and the two things that do "
                                "change the shape: an end tangency support, or an "
                                "extra section where the bulge belongs."),
                        },
                        "normal_to_profile": {
                            "type": "boolean",
                            "description": (
                                "TUTORIAL ALIAS for the end condition 'Normal to "
                                "Profile'. The nearest member is start_tangent / "
                                "end_tangent with a support the section lies on, and "
                                "a DATUM PLANE is one (measured 2026-11-10: two "
                                "circles r=10 and r=14 give 3030.971 mm2 open, "
                                "2869.025 with start_tangent='xy', 3202.807 with "
                                "end_tangent='xy'). Note what that means: tangent TO "
                                "the plane, so the surface leaves flat - not the same "
                                "as leaving perpendicular to the section. Setting "
                                "this true without a support named is refused with "
                                "that choice spelled out."),
                        },
                        "guide_influence": {
                            "type": "string",
                            "description": (
                                "TUTORIAL ALIAS for CATIA's 'Guide influence' "
                                "('To Next Guide' / 'To End Points'). Not exposed by "
                                "the API on this build: HybridShapeLoft has "
                                "AddGuide / AddGuideWithTangent / GetGuide and no "
                                "per-guide influence member (78 members read "
                                "2026-11-10). Passing it is refused with that fact "
                                "and the routes that do control where a guide acts: "
                                "an extra section, or moving the guide's own control "
                                "points."),
                        },
                    },
                    "required": ["sections"],
                },
            },
            {
                "name": "catia_gsd_sweep",
                "description": (
                    "Create a swept surface by sweeping a profile curve along a guide curve "
                    "(explicit sweep)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "profile": {"type": "string", "description": "Profile curve element name"},
                        "guide": {"type": "string", "description": "Guide curve element name"},
                        "name": {"type": "string", "description": "Optional name for the surface"},
                    },
                    "required": ["profile", "guide"],
                },
            },
            {
                "name": "catia_surface_engrave",
                "description": (
                    "Engrave a CONSTANT-DEPTH groove along a curve that lies ON a "
                    "surface - the 'cut text into a curved face' step. Depth is "
                    "measured along the SURFACE NORMAL everywhere, which is what a "
                    "planar pocket cannot do (its depth follows an axis). "
                    "HOW IT WORKS, all measured on B30 2026-11-10: a datum plane "
                    "normal to the curve, a circle of radius = depth on that plane, "
                    "an explicit sweep of the circle along the curve (a tube), "
                    "CloseSurface to make it a solid, and catia_boolean(remove). "
                    "Because the tube is CENTRED on a curve that lies ON the surface, "
                    "exactly half of it is inside the material, so the groove depth "
                    "IS the radius. A r=4 trace on a r=40 cylinder gave a 5.9657 mm3 "
                    "tube (2*pi^2*R*r^2 = 5.97) and removed 2.974 mm3 - the half. "
                    "The reply prints the removed volume against that prediction, so a "
                    "groove that did not follow the surface shows up in the same call. "
                    "IT IS A GROOVE ALONG THE CURVE, NOT A FILLED GLYPH. To engrave a "
                    "letter, give its OUTLINE (catia_sketch_strokes draws a whole "
                    "outline in one call), project it onto the face with "
                    "catia_gsd_project, and engrave along the projection. The curve "
                    "must LIE ON the target body; the tool cannot make it do so. "
                    "CHECK THAT THE PROJECTION KEPT YOUR CURVE: catia_gsd_project "
                    "now prints the projected length against the source's and puts "
                    "a WARNING in front when most of the element did not land. A "
                    "fragment still measures 0.0000 mm to the face - that number is "
                    "a MINIMUM - and it engraves NOTHING: measured on a lofted "
                    "bottle face (2026-11-10), a 63.947 mm outline projected to "
                    "4.000 mm, the tube measured 2.0106 mm3, sat 0.0000 mm from the "
                    "body, and removed no material at all. When that happens this "
                    "tool now reports the tube's volume, its swept length and its "
                    "distance to the body, because the boolean consumes the cutter "
                    "and those numbers are otherwise unrecoverable. "
                    "WHICH CURVES IT ACCEPTS, measured on B30 2026-11-11 with one "
                    "fresh document per case: catia_gsd_line, the extract of a "
                    "gsd_line, an OPEN catia_gsd_spline, and a SKETCH outline "
                    "projected onto the face. A catia_gsd_circle, the extract of "
                    "one, a CLOSED spline, and the projection of any of those are "
                    "refused with ENGRAVE_CURVE_UNPARAMETRISABLE - the route needs a "
                    "point-on-curve on the guide, and a closed curve will not carry "
                    "one. The guide being OPEN is fine: CloseSurface closes the ends "
                    "of the swept pipe, measured 2026-11-11 (a 50 mm segment on a "
                    "generator, 0.5 mm deep, removed 19.5829 mm3 of a predicted "
                    "19.635). "
                    "DO THE PROJECTION AND THE ENGRAVE IN ONE SESSION: an engrave "
                    "that removed 9.991 mm3 in the session that made its projection "
                    "removed NOTHING after the document was saved, closed and "
                    "reopened, with the curve still measuring 17.7831 mm and 0.0000 "
                    "mm from the body. A condition of the reopened file is what "
                    "changes, not the curve - the cause is not known (measured "
                    "2026-11-11), so it is measured here and not explained."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "curve": {
                            "type": "string",
                            "description": (
                                "The curve to engrave along - normally the element "
                                "catia_gsd_project produced. It has to lie ON the "
                                "target body, or the tube misses the material. It "
                                "also has to carry a point-on-curve parameter: a "
                                "gsd_line, an OPEN gsd_spline or a PROJECTED SKETCH "
                                "outline do; a circle, a closed spline and the "
                                "projection of either do not (measured 2026-11-11)."
                            ),
                        },
                        "depth": {
                            "type": "number",
                            "description": (
                                "Groove depth in mm. This is also the sweep radius: "
                                "half the tube ends up inside the material."
                            ),
                        },
                        "target_body": {
                            "type": "string",
                            "description": (
                                "Body to cut. Default: the body that is active now."
                            ),
                        },
                        "position": {
                            "type": "number",
                            "default": 0.5,
                            "description": (
                                "Where along the curve the profile plane sits, as a "
                                "fraction of its length (0.5 = the middle). It only "
                                "orients the circle; a closed guide is closed at "
                                "every position."
                            ),
                        },
                        "name": {
                            "type": "string",
                            "description": (
                                "Base name for the four elements and the cutter body "
                                "this call creates (the cutter body is consumed by "
                                "the boolean, so only the tree elements survive)."
                            ),
                        },
                    },
                    "required": ["curve", "depth"],
                },
            },
            {
                "name": "catia_gsd_extrude",
                "description": (
                    "Extrude a profile (curve/sketch) into a surface along a direction "
                    "vector. limit1/limit2 are the extents on each side (mm)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "profile": {"type": "string", "description": "Profile element name"},
                        "direction": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 3,
                            "maxItems": 3,
                            "description": "Extrusion direction vector [x, y, z]",
                        },
                        "limit1": {"type": "number", "description": "Length along direction (mm)"},
                        "limit2": {
                            "type": "number",
                            "description": "Length opposite to direction (mm)",
                            "default": 0,
                        },
                        "name": {"type": "string", "description": "Optional name for the surface"},
                    },
                    "required": ["profile", "direction", "limit1"],
                },
            },
            {
                "name": "catia_gsd_revolve",
                "description": (
                    "Revolve a profile curve around an axis line to create a "
                    "surface of revolution. Angles in degrees. "
                    "angle1/angle2 ARE NOT A START ANGLE AND AN END ANGLE. They are "
                    "the typelib's iOffsetDebut / iOffsetFin: how far to sweep from "
                    "the PROFILE PLANE toward each side, and the surface spans "
                    "angle1 + angle2. The default angle1=360 covers the full turn; "
                    "angle1=72 with angle2=72 gives a 144 degree sector centred on "
                    "the profile, not a sector from 72 to 72 (measured on B30 "
                    "2026-11-04, by STL: the sector covered 144). This matters the "
                    "moment a sector has to land at a particular place: the angles "
                    "only ever say HOW MUCH, so POSITION COMES FROM WHERE THE "
                    "PROFILE IS - draw the profile along the first edge of the "
                    "sector you want, or rotate the profile with catia_gsd_rotate "
                    "first. Building the same sector five times by rebuilding the "
                    "profile five times is the mistake catia_gsd_rotate exists to "
                    "remove."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "profile": {"type": "string", "description": "Profile element name"},
                        "axis": {
                            "type": "string",
                            "description": (
                                "Rotation axis: a line element name, or 'x'/'y'/'z' "
                                "(built as a line on first use, like catia_gsd_rotate)"
                            ),
                        },
                        "angle1": {
                            "type": "number",
                            "description": (
                                "Degrees swept from the profile plane (sweep total = "
                                "angle1 + angle2), NOT a start angle"
                            ),
                            "default": 360,
                        },
                        "angle2": {
                            "type": "number",
                            "description": (
                                "Degrees swept from the profile plane toward the "
                                "other side; 0 for a one-sided revolve"
                            ),
                            "default": 0,
                        },
                        "name": {"type": "string", "description": "Optional name for the surface"},
                    },
                    "required": ["profile", "axis"],
                },
            },
            {
                "name": "catia_gsd_fill",
                "description": (
                    "Create a Fill surface bounded by a closed contour of curves "
                    "(one closed curve or several connected curves). "
                    "CLOSENESS IS NOT CHECKED BEFORE THE FEATURE IS BUILT. A "
                    "single open curve can come back as a \"successful\" fill that "
                    "is a degenerate face, and CATIA does not complain (measured "
                    "2026-11-09: a 25.0000 mm straight edge filled and produced a "
                    "surface, while the same edge passed as 'Edge.N' was refused "
                    "with \"needs CLOSED boundaries\" - two routes, two answers, "
                    "same geometry). The degenerate face then shows up at a "
                    "thickening or an export, where it reads as a geometry "
                    "problem. Close the contour with catia_gsd_join first, or use "
                    "catia_gsd_circ_pattern(cap_center=true) for a ring's centre. "
                    "DO NOT PASS IT A catia_gsd_boundary CURVE FROM A SURFACE "
                    "WITH A HOLE: one boundary curve carries every loop, so the "
                    "fill comes back as a face covering the whole original - "
                    "overlapping geometry that joins without complaint and then "
                    "breaks the thickening (measured 2026-11-09 on a five-lobe "
                    "base: 1714 edges shared by more than two triangles). The "
                    "same measurement, on a cone, came to 1570.796 mm2 = pi*400 + "
                    "pi*100: one flat face per loop, stacked - not the lateral "
                    "area of the surface it came from. "
                    "IF YOU NEED ONE LOOP ONLY, ask catia_gsd_boundary for it by "
                    "index (edge=N): that curve carries one loop and its fill "
                    "covers only that loop (314.159 and 1256.637 mm2 measured, "
                    "one per loop). That works on a single feature, not on a "
                    "join. "
                    "TO CAP A HOLE, cut first: catia_gsd_split(element=surface, "
                    "cutter=<circle>) leaves the outer piece with that circle as "
                    "its inner border, and filling the CIRCLE then joining the "
                    "two shares the edge by construction. Whole chain measured "
                    "on B30 2026-11-09: 1600.000 -> split 1486.903 -> fill "
                    "113.097 -> join 1600.000 mm2, thickness 0.5 -> 800.0 mm3."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "boundaries": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "description": (
                                "Boundary curve element names, in contour order. "
                                "'Edge.N' is accepted too and is extracted to a "
                                "curve first - a raw topological edge is not one "
                                "(Fill.AddBound answers E_INVALIDARG). On a "
                                "surface in a geometrical set, N counts that "
                                "set's SURFACE-BORDER edges; construction-line "
                                "edges are not counted, because a GSD line's edge "
                                "can measure the same as a border circle "
                                "(measured: both 50.0000 mm on B30 2026-11-09). "
                                "The extract returns the WHOLE border ring of the "
                                "patch, not the single edge (91.1450 mm for a "
                                "four-sided sector, measured), so filling it "
                                "covers that patch's outline - use it when the "
                                "outline is what you want to cap."
                            ),
                        },
                        "name": {"type": "string", "description": "Optional name for the surface"},
                    },
                    "required": ["boundaries"],
                },
            },
            {
                "name": "catia_gsd_blend",
                "description": "Create a Blend surface connecting two curves.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "curve1": {"type": "string", "description": "First curve element name"},
                        "curve2": {"type": "string", "description": "Second curve element name"},
                        "name": {"type": "string", "description": "Optional name for the surface"},
                    },
                    "required": ["curve1", "curve2"],
                },
            },
            {
                "name": "catia_gsd_offset_surface",
                "description": "Create a surface offset from an existing surface.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "surface": {"type": "string", "description": "Surface element name"},
                        "offset": {"type": "number", "description": "Offset distance (mm)"},
                        "reverse": {
                            "type": "boolean",
                            "description": "Offset in the opposite direction",
                            "default": False,
                        },
                        "name": {"type": "string", "description": "Optional name for the surface"},
                    },
                    "required": ["surface", "offset"],
                },
            },
            {
                "name": "catia_gsd_join",
                "description": (
                    "Join (assemble) two or more curves or surfaces into one element. "
                    "The list argument is 'elements'; 'surfaces' is accepted as a "
                    "synonym because every other GSD tool names it after its content. "
                    "Join needs a SHARED EDGE between consecutive elements and says "
                    "nothing when there is none - the update just fails. That is what "
                    "a split causes: cutting away the edge two surfaces shared leaves "
                    "the piece with nothing to join on (measured 2026-11-03). Join "
                    "first, split afterwards. "
                    "WHAT A SHARED EDGE IS NOT: it does not have to be the same curve "
                    "OBJECT. Two lofts whose facing sections are separate circles "
                    "built from the same parameters join fine (measured on B30 "
                    "2026-11-03), and so do two lofts rebuilt from scratch with the "
                    "shared section also rebuilt - so a deleted-and-rebuilt piece "
                    "does not by itself break a join, whatever the delete did to its "
                    "internals. What DOES break the picture is a tree holding two "
                    "elements with one name: 'elements' is resolved by name, so a "
                    "join can silently take the stale twin of a piece you thought you "
                    "had rebuilt. Check with catia_gsd_list_elements when a join "
                    "fails for no geometric reason. "
                    "WHERE TO PUT THE SEAM ON A THIN WALL: in a CURVED region, not "
                    "on a flat or plateau section. The mesh uses its largest triangles "
                    "where curvature is near zero, and a 0.3 mm wall is narrow "
                    "enough for one of them to bridge the two skins; the resulting "
                    "triangle's normal sits between them and catia_check_surface "
                    "then reports a 100 deg+ 'fold' that the surface does not have "
                    "(reported 2026-11-04: a five-petal base whose seam fell on the "
                    "near-flat bottom of its R3 foot read 124.05 deg there and "
                    "nothing above 47.59 deg anywhere else; moving the seam off "
                    "that radius took the extremes to 27.42 deg). Those edges are "
                    "flagged cross_wall, and check_surface raises seam_hint when it "
                    "sees them."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "elements": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 2,
                            "description": "Element names to join",
                        },
                        "surfaces": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 2,
                            "description": "Synonym for 'elements'",
                        },
                        "name": {"type": "string", "description": "Optional name for the result"},
                    },
                },
            },
            {
                "name": "catia_gsd_split",
                "description": (
                    "Split an element by a cutting element, keeping one side. "
                    "DEFAULT SIDE, measured on B30 2026-11-03 (cylinder r10 "
                    "z 0..40 cut by the xy plane at z=10): the default keeps the "
                    "side the cutter's NORMAL points to - z 10..40 above the "
                    "plane, so splitting a 133 mm bottle at z=100 keeps the "
                    "100..133 stub, not the bottle. reverse=true keeps the other "
                    "side. The reply names the side it kept. Also worth knowing: "
                    "join FIRST and split afterwards when the neighbour shares the "
                    "edge you are cutting away - a split piece no longer has that "
                    "edge to join on (measured the same day: join then fails with "
                    "the generic update error). "
                    "THE CUTTER CAN BE A CURVE THAT LIES ON THE SURFACE, and that "
                    "is how a hole gets capped: split the surface with a circle "
                    "(or any closed curve) drawn over the hole, keep the outer "
                    "piece, then catia_gsd_fill that curve and catia_gsd_join - "
                    "the two share the edge because the split created it. Measured "
                    "on B30 2026-11-09 on a 40x40 patch: split by a r=6 circle "
                    "kept 1486.903 mm2 (= 1600 - pi*36), fill gave 113.097, join "
                    "gave 1600.000, thickness 0.5 gave 800.0 mm3. THAT PATCH IS A "
                    "PLANE, and what it demonstrates is narrower than it looks: "
                    "the curve has to LIE ON the surface, not merely cross it. A "
                    "planar circle over a CURVED surface touches it at some "
                    "points and leaves it in between, so there is no contour to "
                    "cut along - measured on B30 2026-11-09, a circle 3 mm above "
                    "a patch and a circle over a five-lobe loft both failed with "
                    "nothing but 方法 UpdateObject 失败. So this call measures the "
                    "closest approach BEFORE building: a cutter that does not "
                    "reach the element is refused as SPLIT_CUTTER_MISSES with "
                    "that distance, and a refusal at update is reported as "
                    "SPLIT_FAILED with the distance and both candidate causes. "
                    "Where the centre of a part is not flat, do not split at all: "
                    "catia_gsd_circ_pattern(cap_center=true, cap_curve=<the hole's "
                    "own edge>) caps the hole in its OWN border shape and needs no "
                    "coplanar disc. Splitting twice "
                    "on the same element does NOT stack: do both cuts from the "
                    "original, not from the first result."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "element": {"type": "string", "description": "Element to split"},
                        "cutter": {
                            "type": "string",
                            "description": "Cutting element: 'xy', 'yz', 'zx', or element name",
                        },
                        "element2": {
                            "type": "string",
                            "description": "Alias for cutter",
                        },
                        "reverse": {
                            "type": "boolean",
                            "description": "Keep the other side",
                            "default": False,
                        },
                        "name": {"type": "string", "description": "Optional name for the result"},
                    },
                    "required": ["element"],
                },
            },
            {
                "name": "catia_gsd_trim",
                "description": (
                    "Mutually trim two elements, keeping one side of each. "
                    "Use reverse flags to flip which sides are kept."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "element1": {"type": "string", "description": "First element name"},
                        "element2": {"type": "string", "description": "Second element name"},
                        "reverse1": {
                            "type": "boolean",
                            "description": "Keep the other side of element1",
                            "default": False,
                        },
                        "reverse2": {
                            "type": "boolean",
                            "description": "Keep the other side of element2",
                            "default": False,
                        },
                        "name": {"type": "string", "description": "Optional name for the result"},
                    },
                    "required": ["element1", "element2"],
                },
            },
            {
                "name": "catia_gsd_symmetry",
                "description": "Mirror an element about a plane ('xy', 'yz', 'zx', or plane name).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "element": {"type": "string", "description": "Element to mirror"},
                        "plane": {
                            "type": "string",
                            "description": "Mirror plane: 'xy', 'yz', 'zx', or element name",
                        },
                        "name": {"type": "string", "description": "Optional name for the result"},
                    },
                    "required": ["element", "plane"],
                },
            },
            {
                "name": "catia_gsd_rotate",
                "description": (
                    "Copy an element and rotate the copy about an axis. The "
                    "original stays where it is - that is a property, not a "
                    "hidden default: SetCreationMode/GetCreationMode exist on "
                    "this interface and were measured on B30 2026-11-05 (a fresh "
                    "rotate reads mode 1; SetCreationMode(0) reads back as 2, "
                    "modes 1 and 2 read back as 1; the source line stayed in the "
                    "geometrical set in all of them). No mode consumes the "
                    "original, so none is set - hard-coding a number whose enum "
                    "the type library does not name would be inventing a "
                    "constant. 'axis' is 'x', 'y' or 'z' (the "
                    "origin axis, built as a line on first use) or the name of an "
                    "existing line/axis element; 'angle' is in degrees, positive "
                    "counter-clockwise looking down the axis toward its origin "
                    "(right-hand rule along +x/+y/+z). "
                    "THIS IS THE PRIMITIVE BEHIND A GSD PATTERN. A five-lobe "
                    "base is one lobe plus four rotations of 72 degrees; "
                    "rebuilding the lobe at five positions instead costs five "
                    "extra constructions of the same geometry and is what this "
                    "tool exists to stop (2026-11-04). For the whole pattern in "
                    "one call use catia_gsd_circ_pattern, which calls this "
                    "count-1 times and joins the copies - the single-feature "
                    "member ShapeFactory.AddNewGSDCircPattern exists but refuses "
                    "to update on this build (measured 2026-11-09). "
                    "Rotating about an arbitrary axis needs a LINE, not a "
                    "direction: pass the name of a line you built."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "element": {"type": "string", "description": "Element to copy and rotate"},
                        "axis": {
                            "type": "string",
                            "description": "Rotation axis: 'x', 'y', 'z', or a line/axis element name",
                        },
                        "angle": {"type": "number", "description": "Angle in degrees"},
                        "name": {"type": "string", "description": "Optional name for the result"},
                    },
                    "required": ["element", "axis", "angle"],
                },
            },
            {
                "name": "catia_gsd_circ_pattern",
                "description": (
                    "Repeat one surface/curve N times around an axis and join the "
                    "copies into a single element - the GSD pattern the tutorial "
                    "route needs, in one call instead of count-1 rotations plus a "
                    "join. 'total_angle' (default 360) is the span; the step is "
                    "total_angle / count, so count=5 total_angle=360 gives 72 "
                    "degrees, which is what a five-lobe base needs. "
                    "WHAT THIS IS NOT: a single pattern node. The member DOES "
                    "exist - on the Part Design factory, "
                    "ShapeFactory.AddNewGSDCircPattern, which takes 14 "
                    "arguments - and an earlier version of this text said GSD "
                    "patterns were not declared anywhere. That was wrong, and "
                    "the 2026-11-09 report was right to call it out. What had "
                    "actually been measured is only that HybridShapeFactory "
                    "declares none: 160 members in all, 131 of them AddNew*, "
                    "and not one name contains 'Pattern' (the two numbers count "
                    "different things and both are quoted here so a recheck "
                    "does not stall on them). "
                    "What has been measured NOW (B30 2026-11-09, seven call "
                    "shapes): the ShapeFactory member returns an object, and "
                    "that object then refuses part.UpdateObject in every "
                    "combination tried - iType 0/1/2, completeCrown on and off, "
                    "a GSD surface and a Part Design pad as the source, the body "
                    "set as the in-work object. A declared member that cannot be "
                    "updated is not a capability, so the rotation path below is "
                    "not a preference: it is what this build can do. "
                    "CAPPING THE CENTRE: cap_center=true closes the hole a full "
                    "ring leaves in the middle, which is the tutorial's Step 6. "
                    "It does NOT use a circle: a five-sided hole's nearest point "
                    "to the axis is 5*cos36 = 4.045, so a circle at that radius "
                    "lies wholly inside the hole and splits nothing (measured), "
                    "and a circle that does cut it leaves a wavy border no join "
                    "will take. It rotates the hole's own edge (cap_curve) count-1 "
                    "times and joins those copies - geometry that meets exactly. "
                    "So the cap has the HOLE'S OWN SHAPE: a five-sided hole gets a "
                    "five-sided cap, and a round cap is not something this build "
                    "can produce here (the 2026-11-09 report asked for the "
                    "tutorial's circular cover and settled for this - correctly). "
                    "Consequences to plan around: the tree gets count-1 rotate "
                    "features plus one join instead of one node, editing 'count' "
                    "afterwards means calling again, and a join can refuse when "
                    "the copies do not share an edge (the reply measures the "
                    "distance rather than guessing why). Each copy is a real "
                    "element with its own name, so the rotation angles are "
                    "readable in the tree, and the copies are NOT removed on "
                    "failure: a join that fails leaves the rotations in place "
                    "and says so, because deleting them would also delete "
                    "whatever the caller built on them."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "element": {
                            "type": "string",
                            "description": "Surface or curve to repeat",
                        },
                        "count": {
                            "type": "integer",
                            "description": "Total number of instances, including the "
                                           "original (count=5 makes 4 rotated copies)",
                            "minimum": 2,
                        },
                        "axis": {
                            "type": "string",
                            "description": "'x', 'y', 'z' (origin axis, built as a line on "
                                           "first use) or the name of a line element",
                            "default": "z",
                        },
                        "total_angle": {
                            "type": "number",
                            "description": "Angular span to fill, in degrees (default 360)",
                            "default": 360,
                        },
                        "join": {
                            "type": "boolean",
                            "description": (
                                "Join the copies into one element (default true). Set "
                                "false to get the rotations alone - the way out when the "
                                "join is refused because the copies do not share edges "
                                "pairwise (measured 2026-11-08: three coplanar patches "
                                "all touching the SAME axis line cannot be joined - one "
                                "edge, three faces)"
                            ),
                            "default": True,
                        },
                        "cap_center": {
                            "type": "boolean",
                            "description": (
                                "Close the hole the ring leaves in the middle, in "
                                "the same call (default false). Needs cap_curve and "
                                "a full turn. The cap is built from the hole's OWN "
                                "edge: that curve is rotated count-1 times about "
                                "the same axis, the copies are joined into the "
                                "hole's border, filled, and joined onto the result. "
                                "Measured on B30 2026-11-09 on a five-lobe base "
                                "(2080.436 mm2, hole at r=5): border 29.3893 mm, "
                                "cap 59.441 mm2, closed 2139.877 mm2 - exact. If "
                                "the cap fails, the elements this call built for it "
                                "are deleted again and the array is left alone."
                            ),
                            "default": False,
                        },
                        "cap_curve": {
                            "type": "string",
                            "description": (
                                "The curve that runs along the hole's border on "
                                "'element' - a tree name, required when "
                                "cap_center=true. It has to be the edge of ONE "
                                "period: rotating it count-1 times must close the "
                                "ring (on a five-lobe base, the inner edge of one "
                                "72-degree sector). There is no automatic way to "
                                "pick it - measured: AddNewBoundary(edge, surface, "
                                "1) returns the WHOLE border ring of a filled patch "
                                "(91.1450 mm for a four-sided sector) and so does "
                                "an extract of one of its edges, so the name has "
                                "to come from the caller. "
                                "ON A LOFT THIS CURVE HAS TO BE ONE OF THE "
                                "GUIDES, and the sections' corresponding points "
                                "have to lie on it. A loft's inner border is an "
                                "interpolated curve, not an element; a curve that "
                                "merely touches it (0.0000 mm apart, measured) is "
                                "not the same edge, so the cap is refused even "
                                "though the geometry looks right. Pass it as a "
                                "third entry in catia_gsd_multi_section_surface's "
                                "guides and make the sections start on it - then "
                                "the border IS that curve and the cap shares an "
                                "edge by construction. This is how the 2026-11-09 "
                                "tutorial build succeeded after two failures that "
                                "looked like parameter errors."
                            ),
                        },
                        "name": {
                            "type": "string",
                            "description": "Name for the joined result",
                        },
                    },
                    "required": ["element", "count"],
                },
            },
            {
                "name": "catia_gsd_translate",
                "description": (
                    "Copy an element and move the copy along a direction. "
                    "'direction' is [x, y, z], 'x'/'y'/'z', or the name of a "
                    "line element whose direction is used; 'distance' is in mm, "
                    "positive along that direction. The original stays - same "
                    "measurement as catia_gsd_rotate (B30 2026-11-05): "
                    "GetCreationMode reads 1, mode 0 reads back as 2, and the "
                    "source line is still in the tree in every mode. "
                    "Measured on B30 2026-11-04: a cylinder spanning z 0..20 "
                    "translated 30 along +z spans 30..50, so the sign is the "
                    "direction's, not a guess. The direction is created as a "
                    "Direction element in the geometrical set (the translate "
                    "keeps a dependency on it) - that is a real tree element, "
                    "not a hidden one."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "element": {"type": "string", "description": "Element to copy and move"},
                        "direction": {
                            "description": "Direction vector, 'x'/'y'/'z', or a line element name",
                            "oneOf": [
                                {
                                    "type": "array",
                                    "items": {"type": "number"},
                                    "minItems": 3,
                                    "maxItems": 3,
                                },
                                {"type": "string"},
                            ],
                        },
                        "distance": {"type": "number", "description": "Distance in mm"},
                        "name": {"type": "string", "description": "Optional name for the result"},
                    },
                    "required": ["element", "direction", "distance"],
                },
            },
            {
                "name": "catia_gsd_thick_surface",
                "description": (
                    "Turn a surface into a SOLID by adding thickness (Part Design "
                    "ThickSurface). The solid is created in the active body. "
                    "IT RETRIES ONCE BY ITSELF, with identical arguments, before it "
                    "reports a failure - and the reply says so when the second "
                    "attempt is the one that worked. This operator is "
                    "session-sensitive the way the loft is: reported 2026-11-04, a "
                    "0.3 mm thickening failed on the last step of a 13-step plan, "
                    "then succeeded called alone, then succeeded again with the whole "
                    "tree visible - same document, same session, same numbers. So a "
                    "single failure is not evidence about the geometry; two are. "
                    "EACH SIDE'S OFFSET MUST STAY BELOW THE LOCAL RADIUS OF "
                    "CURVATURE - not the total. Four measurements on B30 "
                    "2026-11-09 agree with that reading and disagree with the "
                    "old \"total below twice the radius\" one: 0.5/0 refused (one "
                    "side of 0.5 over a 0.355 mm radius), 0.25/0.25 BUILT a "
                    "0.500 mm wall (each side 0.25 under 0.355, total above it), "
                    "2.54/0 refused on a 2.30 mm section, 1.27/1.27 refused "
                    "there too. So when a wall you asked for is refused, the "
                    "answer is often not \"thicken less\" but \"split the same "
                    "total across both sides\": 0.25/0.25 and 0.5/0 produce the "
                    "same 0.500 mm wall on the good side of the limit, and only "
                    "the first one is inside it. The failure message carries the "
                    "measured radius, so compare it with each side before giving "
                    "up a wall thickness the design needs (reported 2026-11-09: "
                    "the old text read as \"this wall can never be 0.5 mm\" and "
                    "the caller abandoned it). "
                    "If a section is thinner than either offset, model the part as "
                    "a padded polygon - or better, use catia_solid_loft, which "
                    "builds the same shape as a solid in one feature with no "
                    "thickness to add and therefore no limit to hit (measured: a "
                    "2.30 mm lens section that refuses every offset here lofts to "
                    "a 720.5 mm3 solid there). "
                    "A SMOOTH LOFT THAT BUILT IS NOT A LOFT THAT CAN BE "
                    "THICKENED. A multi-section surface interpolates through its "
                    "sections, so where the sections change size quickly the "
                    "surface's curvature tightens between them - the very thing "
                    "that makes the loft build is the thing that limits the "
                    "offset. Reported 2026-11-03 from a five-petal bottle base: "
                    "the six-section span [02..07] built a surface and then "
                    "refused 0.3 mm, while every 2-3 section span of the same ten "
                    "sections thickened; the failure reads \"surface thinner than "
                    "offset\" and names nothing about the loft. So for thin walls, "
                    "split the loft into 2-3 section pieces and catia_gsd_join "
                    "them rather than trusting a 6-10 section span because it "
                    "built. The same rule applies to any patch: check "
                    "catia_check_surface after building and before offsetting. "
                    "SECTIONS WHOSE ANGULAR FEATURES ARE OUT OF PHASE are the "
                    "other candidate - reported from the same bottle model - but "
                    "measured on B30 2026-11-03 an isolated three-ring lobed loft "
                    "with a 30 degree lobe shift still thickened 0.3 mm, so check "
                    "the joined seams with catia_check_surface region={} before "
                    "re-cutting the sections."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "surface": {"type": "string", "description": "Surface element name"},
                        "thickness1": {
                            "type": "number",
                            "description": "Thickness on the primary side (mm)",
                        },
                        "thickness2": {
                            "type": "number",
                            "description": "Thickness on the other side (mm)",
                            "default": 0,
                        },
                    },
                    "required": ["surface", "thickness1"],
                },
            },
            {
                "name": "catia_gsd_close_surface",
                "description": (
                    "Turn a closed (watertight) surface into a SOLID (Part Design "
                    "CloseSurface). The solid is created in the active body."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "surface": {"type": "string", "description": "Surface element name"},
                    },
                    "required": ["surface"],
                },
            },
        ]

    # ────────────────────────────────────────────────────────────
    # Dispatch
    # ────────────────────────────────────────────────────────────

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        arguments = _normalise_arguments(tool_name, arguments)
        problem = _missing_key_error(tool_name, arguments)
        if problem:
            raise ArgumentRejected(problem)
        match tool_name:
            case "catia_gsd_create_geoset":
                return self._create_geoset(arguments)
            case "catia_gsd_set_active_geoset":
                return self._set_active_geoset(arguments)
            case "catia_gsd_list_elements":
                return self._list_elements()
            case "catia_gsd_point":
                return self._point(arguments)
            case "catia_gsd_line":
                return self._line(arguments)
            case "catia_gsd_plane_offset":
                return self._plane_offset(arguments)
            case "catia_gsd_plane_3points":
                return self._plane_3points(arguments)
            case "catia_gsd_plane_normal_to_curve":
                return self._plane_normal_to_curve(arguments)
            case "catia_gsd_plane_angle":
                return self._plane_angle(arguments)
            case "catia_gsd_spline":
                return self._spline(arguments)
            case "catia_gsd_circle":
                return self._circle(arguments)
            case "catia_gsd_project":
                return self._project(arguments)
            case "catia_gsd_extract":
                return self._extract(arguments)
            case "catia_gsd_intersection":
                return self._intersection(arguments)
            case "catia_gsd_boundary":
                return self._boundary(arguments)
            case "catia_gsd_heal":
                return self._heal(arguments)
            case "catia_gsd_fillet_surface":
                return self._fillet_surface(arguments)
            case "catia_gsd_spine":
                return self._spine(arguments)
            case "catia_gsd_multi_section_surface":
                return self._multi_section_surface(arguments)
            case "catia_gsd_sweep":
                return self._sweep(arguments)
            case "catia_surface_engrave":
                return self._surface_engrave(arguments)
            case "catia_gsd_extrude":
                return self._extrude(arguments)
            case "catia_gsd_revolve":
                return self._revolve(arguments)
            case "catia_gsd_fill":
                return self._fill(arguments)
            case "catia_gsd_blend":
                return self._blend(arguments)
            case "catia_gsd_offset_surface":
                return self._offset_surface(arguments)
            case "catia_gsd_join":
                return self._join(arguments)
            case "catia_gsd_split":
                return self._split(arguments)
            case "catia_gsd_trim":
                return self._trim(arguments)
            case "catia_gsd_symmetry":
                return self._symmetry(arguments)
            case "catia_gsd_rotate":
                return self._rotate(arguments)
            case "catia_gsd_circ_pattern":
                return self._circ_pattern(arguments)
            case "catia_gsd_translate":
                return self._translate(arguments)
            case "catia_gsd_thick_surface":
                return self._thick_surface(arguments)
            case "catia_gsd_close_surface":
                return self._close_surface(arguments)
            case _:
                raise ArgumentRejected(f"Unknown GSD tool: {tool_name}")

    # ────────────────────────────────────────────────────────────
    # Helpers: geoset management and element resolution
    # ────────────────────────────────────────────────────────────

    def _factory(self) -> Any:
        return self.conn.get_active_part().HybridShapeFactory

    def _iter_geosets(self, hybrid_bodies: Any):
        """Yield all geometrical sets, including nested ones."""
        for i in range(1, hybrid_bodies.Count + 1):
            hb = hybrid_bodies.Item(i)
            yield hb
            try:
                yield from self._iter_geosets(hb.HybridBodies)
            except Exception:
                pass

    def _geoset_index(self, part: Any) -> list[tuple[Any, str | None, int]]:
        """(set, parent name, depth) for every set, nested ones included.

        ``_iter_geosets`` yields the same sets but loses WHERE each one sits, and
        that omission is the reported defect: 0.3.78 added ``parent`` to
        catia_gsd_create_geoset and the reading side never caught up, so a set
        created inside another came back flat next to it, with no field a caller
        could check (reported 2026-11-08, M2). Depth is 0 at the root.
        """
        found: list[tuple[Any, str | None, int]] = []

        def walk(collection: Any, parent: str | None, depth: int) -> None:
            try:
                count = int(collection.Count)
            except Exception:  # noqa: BLE001 - an empty body has none
                return
            for index in range(1, count + 1):
                hb = collection.Item(index)
                found.append((hb, parent, depth))
                try:
                    walk(hb.HybridBodies, str(hb.Name), depth + 1)
                except Exception:  # noqa: BLE001 - leaves have no collection
                    continue

        walk(part.HybridBodies, None, 0)
        return found

    @staticmethod
    def _unique_geoset_name(taken: list[str], wanted: str) -> tuple[str, str]:
        """(name to use, note when it had to change).

        Same rule the sketcher applies to a duplicate sketch name, because it is
        the same problem: ``Collection.Item(name)`` answers the FIRST match, so a
        second set called 'OUTER' made every later lookup by that name point at
        the wrong branch of the tree while catia_set_visibility happily hid both
        (reported 2026-11-08, M1 - N2 in a different namespace).
        """
        lowered = {str(name).casefold() for name in taken}
        if str(wanted).casefold() not in lowered:
            return wanted, ""
        for suffix in range(2, 1000):
            candidate = f"{wanted}#{suffix}"
            if candidate.casefold() not in lowered:
                return candidate, (
                    f" A geometrical set named '{wanted}' already exists in this "
                    f"part, so this one was named '{candidate}' instead - "
                    "Collection.Item(name) answers the first match, and two sets "
                    "with one name make every later lookup by name ambiguous.")
        return wanted, ""

    def _get_geoset(self) -> Any:
        """Get the active geometrical set, falling back to the first existing
        one, or creating a default one if the part has none."""
        part = self.conn.get_active_part()
        hbs = part.HybridBodies

        if self._active_geoset_name and self._active_geoset_doc != self._doc_name():
            # Another document is active: the remembered set belongs to the
            # previous one, and its name very likely exists here too. Keeping
            # the name would silently retarget the next GSD element.
            self._active_geoset_name = None
            self._active_geoset_doc = None

        if self._active_geoset_name:
            for hb in self._iter_geosets(hbs):
                if hb.Name == self._active_geoset_name:
                    return hb
            # The remembered geoset is gone (document changed) — fall through
            self._active_geoset_name = None
            self._active_geoset_doc = None

        if hbs.Count > 0:
            hb = hbs.Item(1)
        else:
            hb = hbs.Add()
            hb.Name = self.DEFAULT_GEOSET
        self._active_geoset_name = hb.Name
        self._active_geoset_doc = self._doc_name()
        return hb

    def _doc_name(self) -> str | None:
        """Name of the active document, or None if there is not one yet."""
        try:
            return self.conn.active_document.Name
        except Exception:  # noqa: BLE001 - only used to invalidate a cache
            return None

    def _find_element(self, name: str) -> Any:
        """Find a wireframe/surface element or sketch by its tree name.

        Search order: origin planes (xy/yz/zx aliases), hybrid shapes and
        sketches in all geometrical sets (nested included), then sketches
        in solid bodies.
        """
        part = self.conn.get_active_part()

        key = name.lower()
        if key in PLANE_MAP:
            return getattr(part.OriginElements, PLANE_MAP[key])

        # A sketch rebuilt after a rollback answers to the name the caller used
        # before it, in the Part Design tools and in the sketcher. GSD searched
        # the tree verbatim, so an element that `catia_pad` could resolve was
        # "not found" here (reported 2026-10-28).
        alias = self._sketch_alias(name)
        candidates = [name] if not alias or alias == name else [name, alias]
        for candidate in candidates:
            found = self._lookup_element(part, candidate)
            if found is not None:
                return found

        # Not found. A bare "not found" was a lie often enough (a 2026-10-23
        # report: after three new_part/close_document rounds in one session,
        # elements that catia_gsd_list_elements could see became unreachable
        # here) that the failure has to carry its own evidence: which document
        # was searched, what it holds, and whether the name is sitting in a
        # different open document.
        searched = self._element_names(part)
        active = self._doc_name() or "the active document"
        message = (
            f"Element '{name}' not found in '{active}'. Searched "
            f"{len(searched)} tree name(s)"
            + (f": {self._name_preview(searched)}" if searched else " (none)")
            + ". If the element is on screen, the call may be running against a "
            "document that is no longer active."
        )
        elsewhere = self._documents_holding(name)
        if elsewhere:
            message = (
                f"Element '{name}' is not in '{active}'; it is in "
                f"{', '.join(elsewhere)}. Bring that document to the front with "
                "catia_activate_document and retry. "
            ) + message
        elif alias:
            message += (f" '{name}' was re-created as '{alias}' after a rollback, "
                        f"and '{alias}' is not in the tree either.")
        else:
            message += " Use catia_gsd_list_elements to see available names."
        raise ArgumentRejected(message)

    def _lookup_element(self, part: Any, name: str) -> Any | None:
        """One pass over the tree for that exact name, or None.

        Each collection is asked for its Count first: ``Item(name)`` on an EMPTY
        collection raises, and a raise costs ~15 ms against ~1 ms for the count,
        so asking a GSD-only part for a sketch name used to be the expensive half
        of every lookup (measured 2026-11-03 while timing the loft's point guard:
        236 ms for ten sections, down from 460 ms once the empty collections were
        skipped).
        """
        for hb in self._iter_geosets(part.HybridBodies):
            for collection in ("HybridShapes", "HybridSketches"):
                try:
                    items = getattr(hb, collection)
                    if not items.Count:
                        continue
                    return items.Item(name)
                except Exception:  # noqa: BLE001 - the next collection may have it
                    pass

        bodies = part.Bodies
        for i in range(1, bodies.Count + 1):
            try:
                sketches = bodies.Item(i).Sketches
                if not sketches.Count:
                    continue
                return sketches.Item(name)
            except Exception:  # noqa: BLE001 - keep looking
                pass
        return None

    def _sketch_alias(self, name: str) -> str | None:
        """What this sketch name was rebuilt as, from the connection's table."""
        resolve = getattr(self.conn, "sketch_alias", None)
        return resolve(name) if resolve is not None else None

    def _element_names(self, part: Any) -> list[str]:
        """Every name the resolver can see in this part, for error messages."""
        names: list[str] = []
        for hb in self._iter_geosets(part.HybridBodies):
            for collection in ("HybridShapes", "HybridSketches"):
                try:
                    shapes = getattr(hb, collection)
                    for i in range(1, shapes.Count + 1):
                        names.append(shapes.Item(i).Name)
                except Exception:  # noqa: BLE001 - diagnostics only
                    continue
        try:
            bodies = part.Bodies
            for i in range(1, bodies.Count + 1):
                sketches = bodies.Item(i).Sketches
                for j in range(1, sketches.Count + 1):
                    names.append(sketches.Item(j).Name)
        except Exception:  # noqa: BLE001
            pass
        return names

    @staticmethod
    def _name_preview(names: list[str], limit: int = 12) -> str:
        shown = ", ".join(repr(n) for n in names[:limit])
        return shown + (f" (+{len(names) - limit} more)" if len(names) > limit else "")

    def _documents_holding(self, name: str) -> list[str]:
        """Names of the other open documents that do hold this element."""
        hits: list[str] = []
        try:
            docs = self.conn.documents
            active = self._doc_name()
            count = docs.Count
        except Exception:  # noqa: BLE001
            return hits
        for i in range(1, count + 1):
            try:
                doc = docs.Item(i)
                if doc.Name == active:
                    continue
                part = self.conn.part_of(doc)
                if name in self._element_names(part):
                    hits.append(doc.Name)
            except Exception:  # noqa: BLE001 - a Product document has no Part
                continue
        return hits

    _TOPO_NAME = re.compile(r"(edge|face|vertex)\s*\.\s*(\d+)", re.I)

    def _topology_reference(self, name: str) -> Any | None:
        """Resolve 'Edge.N' / 'Face.N' / 'Vertex.N' to a usable reference.

        Specs routinely loft from an existing solid's end-face edge ("loft from
        the Ø38.1 edge at x=254"), which has no tree name - the only handle is
        the indexed topology a search produces.

        A raw topological edge cannot be consumed by a loft: AddSectionToLoft
        rejects it ("invalid number of arguments", which is CATIA's misleading
        way of saying the type is wrong). Extracting it into a GSD curve first
        makes it a real section, so that happens here and the Extract lands in
        the active geometrical set.
        """
        match = self._TOPO_NAME.fullmatch(name.strip())
        if not match:
            return None
        kind = match.group(1).capitalize()      # Edge / Face / Vertex
        index = int(match.group(2))
        # A surface in a geometrical set has no body shape, so a body search
        # finds nothing and every "Edge.N" answered "the active body has no solid
        # shape yet" - true about the body, useless about the surface the caller
        # is pointing at. The set is searched instead (0.3.85).
        if kind == "Edge":
            body = None
            try:
                body = self.conn.get_active_part_body()
            except Exception:  # noqa: BLE001 - no body is exactly the case below
                body = None
            try:
                shaped = body is not None and int(body.Shapes.Count) > 0
            except Exception:  # noqa: BLE001
                # The body will not report its shapes - a test double, or a build
                # that hides the property. Keep the old path: switching to the set
                # search on a guess would change where Edge.N points.
                shaped = True
            if not shaped:
                return self._geoset_edge_reference(name)
        part = self.conn.get_active_part()
        body = self.conn.get_active_part_body()
        sel = self.conn.hso
        try:
            sel.Clear()
            sel.Add(body)
            search_topology(sel, kind)
            total = sel.Count
            if not 1 <= index <= total:
                raise ArgumentOutOfRange(
                    f"'{name}' is out of range: the active body has {total} "
                    f"{kind.lower()}s. Use catia_list_edges / catia_list_faces."
                )
            raw = sel.Item(index).Reference
        finally:
            try:
                sel.Clear()
            except Exception:
                pass

        # A vertex is already a point-like element; only edges and faces need
        # the Extract to become a curve/surface.
        if kind == "Vertex":
            return raw
        extract = self._factory().AddNewExtract(raw)
        try:
            # 1 = no propagation: this edge only, not the tangent chain.
            extract.PropagationType = 1
        except Exception:
            pass
        self._get_geoset().AppendHybridShape(extract)
        part.UpdateObject(extract)
        return part.CreateReferenceFromObject(extract)

    def _ref(self, name: str) -> Any:
        """Get a Reference to a named element."""
        part = self.conn.get_active_part()
        topology = self._topology_reference(name)
        if topology is not None:
            return topology
        return part.CreateReferenceFromObject(self._find_element(name))

    def _geoset_border_edges(self) -> tuple[Any, list[tuple[int, Any, float | None]]]:
        """Every surface-border edge in the active geometrical set.

        Returns the selection (left HOLDING the cells: a reference taken from
        ``Selection.Item(i).Reference`` dies the moment the selection is cleared,
        and a factory call given a dead reference answers "method failed" with no
        explanation) plus ``(index, reference, length)`` rows.

        Only ``Selection_BorderREdge`` rows come back. A GSD line's own edge sits
        in the same search and can measure exactly the same length as a border
        circle (measured on B30 2026-11-09: a 50 mm construction line and a
        r=7.96 border are both 50.0000), so an index over "all edges" points at
        the wrong element often enough to be unusable. ``WireREdge`` and
        ``BorderRVertex`` are CATIA's own labels for "not a surface border"
        (topo_search.construction_reason knows the rest).

        Lengths are read here, once, because the answer "which loop is the inner
        one" is a comparison of lengths and distances - a bare index would make
        the caller guess.
        """
        sel = self.conn.hso
        sel.Clear()
        house = self._get_geoset()
        sel.Add(house)
        search_topology(sel, "Edge")
        spa = None
        try:
            spa = self.conn.active_document.GetWorkbench("SPAWorkbench")
        except Exception:  # noqa: BLE001 - lengths become None, nothing else
            pass
        rows: list[tuple[int, Any, float | None]] = []
        for i in range(1, int(getattr(sel, "Count", 0)) + 1):
            try:
                item = sel.Item(i)
                nls = str(item.Value.Name)
            except Exception:  # noqa: BLE001 - an unreadable cell is not a border
                continue
            if not nls.startswith("Selection_BorderREdge"):
                continue
            # A DATUM PLANE carries the same Selection_BorderREdge label as a
            # surface (topo_search reads that label as "datum-plane border",
            # which is right in a BODY search and wrong in a set search). Its
            # four borders measure 2e7 mm - CATIA's idea of an infinite plane -
            # and including them turned "91.0844 mm of border" into
            # "80000091.0844" (2026-11-09). GSMPlane.N in the Brp path is the
            # datum plane; a surface reads GSMFill.N / GSMRevol.N / GSMLoft.N.
            if "GSMPlane" in nls:
                continue
            reference = item.Reference
            length = None
            if spa is not None:
                try:
                    length = float(spa.GetMeasurable(reference).Length)
                except Exception:  # noqa: BLE001 - a length is optional
                    length = None
            rows.append((i, reference, length))
        return sel, rows

    def _geoset_edge_reference(self, name: str) -> Any:
        """``Edge.N`` on a GSD surface: extract the nth surface-border edge.

        ``_topology_reference`` searches the active BODY, which is right for a
        solid's topology and useless here - a surface in a geometrical set has no
        body shape, so every ``Edge.N`` answered "the active body has no solid
        shape yet". The set is searched instead, and only surface borders are
        counted, so N means the same thing as it does in ``_boundary``.

        The extract is appended and updated because a raw topological edge is not
        a curve: ``Fill.AddBound`` and friends reject the reference (the same
        E_INVALIDARG that made ``_topology_reference`` extract in the first
        place). ``PropagationType`` is NOT set: measured on B30 2026-11-09, an
        extract of one edge of a filled quadrilateral came back as the WHOLE
        border ring (91.1450 mm = all four sides) with PropagationType=1, so
        setting it buys nothing and claiming "this edge only" would be false.
        """
        match = re.fullmatch(r"edge\s*\.\s*(\d+)", name.strip(), re.I)
        if not match:
            raise ArgumentRejected(
                f"'{name}' is not an edge index. Use 'Edge.N' where N counts the "
                "SURFACE BORDER edges of the active geometrical set.",
                code="EDGE_NAME_MALFORMED", stage="validate")
        index = int(match.group(1))
        sel, rows = self._geoset_border_edges()
        try:
            if not 1 <= index <= len(rows):
                listed = ", ".join(
                    f"Edge.{i}"
                    + (f" ({length:.4f} mm)" if length is not None else "")
                    for i, _r, length in rows) or "none"
                raise ArgumentOutOfRange(
                    f"Edge.{index} is out of range: the active geometrical set "
                    f"exposes {len(rows)} surface-border edge(s): {listed}. "
                    "Construction-line edges are NOT counted here - only curves "
                    "that border a surface, which is what a fill can use.",
                    code="EDGE_OUT_OF_RANGE", stage="validate")
            _i, reference, _length = rows[index - 1]
            part = self.conn.get_active_part()
            extract = self._factory().AddNewExtract(reference)
            self._get_geoset().AppendHybridShape(extract)
            part.UpdateObject(extract)
        finally:
            try:
                sel.Clear()
            except Exception:  # noqa: BLE001
                pass
        return part.CreateReferenceFromObject(extract)

    def _point_ref(self, spec: Any) -> Any:
        """Resolve points, sharing one part/factory/geoset lookup.

        Coordinates create a point feature in the active geoset. That point is a
        DEPENDENCY of the curve being built, so its own update is redundant - the
        feature-level update at the end of the tool cascades into it, and the
        curve comes out identical (188.496 mm for r=30 against 188.496 analytic).

        Do not read this as a speed fix. A cold-session A/B on B30 2026-11-03,
        one CATIA process per condition, 90 points: 3.50 s and 4.68 s WITH the
        per-point UpdateObject, 4.08 s and 4.19 s without - the update costs
        nothing measurable, and what a 90-point spline really spends is the 90
        datum points themselves (~39 ms each). Measured inside one session the
        same work ranged from 2.35 s to 13.65 s, which is why the first A/B run
        here looked like a 5x win and was not: CATIA slows down as the session
        fills. The count of those points is reported in the reply instead.
        """
        return self._point_refs([spec])[0]

    def _point_refs(self, specs: list[Any], in_tree: bool = True) -> list[Any]:
        """``_point_ref`` for a whole list, with the part resolved once.

        ``in_tree`` decides whether coordinates become Point FEATURES in the
        geometrical set. Leaving them out is not a shortcut that loses data: a
        point that is referenced by the spline is saved with the part even when
        it is not in the tree - measured on B30 2026-11-03, a 90-point spline
        built this way, saved, closed and reopened with its tree holding exactly
        one element (the spline) and its length still 188.496 mm for r=30, the
        analytic answer. What it changes is the tree: ten loft sections of
        coordinate splines used to leave 636 Point features behind (reported
        2026-11-03), which then took 28 s just to hide.
        """
        refs: list[Any] = []
        context: tuple[Any, Any, Any] | None = None
        self._coordinate_point_names = []
        for spec in specs:
            if isinstance(spec, str):
                refs.append(self._ref(spec))
                continue
            if not (isinstance(spec, (list, tuple)) and len(spec) == 3):
                raise ArgumentRejected(
                    f"Invalid point spec: {spec!r}. Use an element name or [x, y, z].")
            if context is None:
                context = (self.conn.get_active_part(), self._factory(),
                           self._get_geoset())
            part, factory, geoset = context
            point = factory.AddNewPointCoord(spec[0], spec[1], spec[2])
            if in_tree:
                geoset.AppendHybridShape(point)
            self._coordinate_points += 1
            if in_tree:
                try:
                    # Remember the name so the feature that uses it can be deleted
                    # together with it later (see CATIAConnection.note_owned).
                    self._coordinate_point_names.append(str(point.Name))
                except Exception:  # noqa: BLE001 - an unnamed point is still usable
                    pass
            refs.append(part.CreateReferenceFromObject(point))
        return refs

    def _safe_update(self, feature: Any) -> None:
        """Update a feature; if CATIA rejects it, delete the broken feature so
        failed attempts don't linger in the tree, then re-raise."""
        part = self.conn.get_active_part()
        try:
            part.UpdateObject(feature)
        except Exception as e:
            try:
                selection = self.conn.active_document.Selection
                selection.Clear()
                selection.Add(feature)
                selection.Delete()
            except Exception:
                pass
            raise RuntimeError(
                f"CATIA rejected the feature (update failed) and it was removed "
                f"from the tree: {e}"
            ) from e

    def _finish(self, shape: Any, name: str | None, message: str) -> str:
        """Append a hybrid shape to the active geoset, rename, update, refresh."""
        geoset = self._get_geoset()
        geoset.AppendHybridShape(shape)
        return self._completed(shape, name, message, geoset)

    def _shape_area_mm2(self, shape: Any) -> float | None:
        """Area of a surface in mm2, or None when it cannot be measured.

        ``Measurable.Area`` answers in m2 (a 1600 mm2 face reads 0.0016) - the
        same unit trap as ``Volume`` in m3. It is ALSO the only number that can
        tell a real fill from the degenerate face this build builds when the
        boundary does not close (2026-11-09), so a failure to measure it is
        reported as "unknown", never as zero.
        """
        try:
            part = self.conn.get_active_part()
            spa = self.conn.active_document.GetWorkbench("SPAWorkbench")
            reference = part.CreateReferenceFromObject(shape)
            return float(spa.GetMeasurable(reference).Area) * 1e6
        except Exception:  # noqa: BLE001 - an unmeasurable shape is not a zero
            return None

    def _drop_element(self, shape: Any) -> None:
        """Delete an element this call created, best effort, leaving no trace."""
        try:
            selection = self.conn.active_document.Selection
            selection.Clear()
            selection.Add(shape)
            selection.Delete()
        except Exception:  # noqa: BLE001 - the caller is already being told
            pass
        try:
            self.conn.active_document.Selection.Clear()
        except Exception:  # noqa: BLE001
            pass

    def _name_gone(self, part: Any, name: str) -> bool:
        """Is ``name`` really out of the tree? False when the part cannot answer.

        Used right after a delete, because "I called delete" is not "it is gone".
        An unreadable answer counts as NOT gone: the cost of that mistake is a
        wrong claim that the part is clean, which is the mistake being fixed.
        """
        try:
            return part.FindObjectByName(str(name)) is None
        except Exception:  # noqa: BLE001 - unanswerable is not clean
            return False

    def _free_name(self, part: Any, wanted: str) -> tuple[str, bool]:
        """``(name, verified)``: ``wanted``, or ``wanted.2`` / ``.3`` … if taken.

        Nothing in CATIA stops two elements from carrying the same name; this
        server used to produce them by assigning ``Name`` blindly, and a report
        then spent two hours on a join that kept failing after a rebuild it had
        already done (2026-11-03; measured: two elements named C_P, one delete
        removed one, the other C_P stayed and kept answering to that name).

        ``verified`` is False when ``FindObjectByName`` itself failed, in which
        case the caller has to say so: falling back to the requested name is the
        only thing that can be done, but it is also exactly the pre-0.3.52
        behaviour, so a silent fallback could recreate the duplicate this exists
        to prevent (raised by the 0.3.52 acceptance report, 2026-11-03).
        """
        candidate = wanted
        for index in range(2, 52):
            try:
                existing = part.FindObjectByName(candidate)
            except Exception:  # noqa: BLE001 - cannot tell, so take the caller's
                return candidate, False
            if existing is None:
                return candidate, True
            candidate = f"{wanted}.{index}"
        return candidate, True

    def _completed(self, shape: Any, name: str | None, message: str,
                   geoset: Any) -> str:
        """Rename / update / refresh an element that is ALREADY in the geoset.

        ``_finish`` is ``AppendHybridShape`` + this. The split exists because an
        element that is already in a body cannot be appended a second time:
        CATIA answers "方法 AppendHybridShape 失败" (E_FAIL), not "already
        added".
        """
        part = self.conn.get_active_part()
        if name:
            wanted = str(name)
            unique, verified = self._free_name(part, wanted)
            shape.Name = unique
            if not verified:
                message += (
                    f" NOTE: the name could not be checked against the existing "
                    f"tree ({wanted} kept as given), so if another element already "
                    f"carries it the part now holds two with that name - CATIA "
                    f"allows it, and later calls by name are then ambiguous.")
            elif unique != wanted:
                # CATIA lets two elements share a name, and assigning Name does
                # not check. A tree with two "P_04_06" makes every later call by
                # name ambiguous: FindObjectByName returns one of them and
                # catia_delete_feature removes one of them, so a rebuild can look
                # like it did nothing (measured on B30 2026-11-03 - two elements
                # named C_P, one delete, one C_P left, and that is also the state
                # a report read as "delete + rebuild breaks the loft topology").
                # The acceptance report of the same day measured which one wins:
                # the OLD element keeps answering to the old name, deterministically
                # rather than at random, which is worth saying because a caller who
                # rebuilds and keeps the old name will keep getting the old element.
                message += (f" NOTE: the name '{wanted}' was already taken, so this "
                            f"element is '{unique}'. The element already holding "
                            f"'{wanted}' keeps that name and keeps answering to it, "
                            f"so a call by '{wanted}' still reaches the OLD element.")
        part.InWorkObject = shape
        self._safe_update(shape)
        # Whatever points this call turned coordinates into belong to this
        # element: record the pairing so catia_delete_feature can offer to take
        # them away too. CATIA has no reverse dependency query, and deleting a
        # spline leaves its points behind (measured 2026-11-03: six points stayed
        # after the spline was deleted).
        names = getattr(self, "_coordinate_point_names", None)
        if names:
            try:
                self.conn.note_owned(str(shape.Name), list(names))
            except Exception:  # noqa: BLE001 - bookkeeping, never the feature
                pass
            self._coordinate_point_names = []
        self.conn.refresh_display()
        return f"{message} Element: '{shape.Name}' in geometrical set '{geoset.Name}'"

    # ────────────────────────────────────────────────────────────
    # Geoset management
    # ────────────────────────────────────────────────────────────

    def _create_geoset(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        name = str(args.get("name", self.DEFAULT_GEOSET))
        parent_name = (args.get("parent") or "").strip() or None
        index = self._geoset_index(part)

        if parent_name:
            # Nested sets: without this, "create a set while another is active"
            # always landed at the root, so a set inside a set could not be
            # built at all - which made catia_set_visibility's recursive descent
            # (0.3.77) unreachable and untestable by any caller (reported
            # 2026-11-08, N5).
            matches = [item for item in index if str(item[0].Name) == parent_name]
            if not matches:
                available = [str(item[0].Name) for item in index]
                raise ArgumentRejected(
                    f"No geometrical set named '{parent_name}' to create '{name}' "
                    f"inside. Available: {available or 'none'}"
                )
            if len(matches) > 1:
                # Two sets can carry one name - this build does not enforce
                # uniqueness - and the old loop silently took the first, so
                # "create X inside OUTER" landed in an arbitrary branch of the
                # tree with no way to tell which (reported 2026-11-08, M1).
                # Numbered in catia_gsd_list_elements order, because two sets
                # with one name at the same depth are otherwise indistinguishable
                # in prose - the caller needs something to point at.
                where = ", ".join(
                    f"#{position} (depth {depth}"
                    + (f", inside '{parent}')" if parent else ", at the root)")
                    for position, (_hb, parent, depth)
                    in enumerate(matches, 1))
                raise CodedError(
                    f"'{parent_name}' is ambiguous: {len(matches)} geometrical sets "
                    f"carry that name ({where}). Nothing was created. Rename one of "
                    "them (there is no rename tool for a geometrical set - delete and "
                    "re-create the empty one), then repeat.",
                    code="GEOSET_AMBIGUOUS",
                    stage="validate",
                    modified=False,
                    data={"name": parent_name, "matches": [
                        {"parent": parent, "depth": depth}
                        for _hb, parent, depth in matches]},
                )
            parent = matches[0][0]
            try:
                hb = parent.HybridBodies.Add()
            except Exception as error:  # noqa: BLE001 - the nesting is refused
                raise PreconditionRejected(
                    f"CATIA refused to create a geometrical set inside "
                    f"'{parent_name}' ({str(error).splitlines()[0][:120]}). The "
                    "set was not created; the tree holds no nesting support on "
                    "this build for that parent."
                ) from error
        else:
            hb = part.HybridBodies.Add()
        # Checked across the WHOLE part, nested sets included: a duplicate name
        # is what makes a later Item(name) silent-wrong, and the tree does not
        # stop a caller from creating one (measured in the 2026-11-08 report:
        # two sets called OUTER, and set_visibility hid both while gsd_spline
        # drew into one of them).
        name, rename_note = self._unique_geoset_name(
            [str(item[0].Name) for item in index], name)
        hb.Name = name
        self._active_geoset_name = str(hb.Name)
        self._active_geoset_doc = self._doc_name()
        try:
            part.Update()
        except Exception:
            pass
        where = f" inside '{parent_name}'" if parent_name else " at the root of the part"
        return (f"Geometrical set '{hb.Name}' created{where} and set as active."
                + rename_note
                + (" A set inside a set is what catia_set_visibility recurses "
                   "into; a set holding only curves will NOT show up in a mesh "
                   "bounding box, because a curve cannot be triangulated - use a "
                   "surface to test visibility in the envelope."
                   if parent_name else ""))

    def _set_active_geoset(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        name = args["name"]
        index = self._geoset_index(part)

        matches = [item for item in index if str(item[0].Name) == name]
        if len(matches) == 1:
            _hb, parent, depth = matches[0]
            self._active_geoset_name = name
            self._active_geoset_doc = self._doc_name()
            where = (f"depth {depth}" if depth else "at the root of the part")
            if parent:
                where += f", inside '{parent}'"
            return f"Active geometrical set: '{name}' ({where})."
        if len(matches) > 1:
            # Answering "Active geometrical set: 'OUTER'" while silently picking
            # the first of two was the reported half of M1: catia_set_visibility
            # hid BOTH, so the caller had every reason to think one set was meant.
            where = ", ".join(
                f"#{position} (depth {depth}"
                + (f", inside '{parent}')" if parent else ", at the root)")
                for position, (_hb, parent, depth) in enumerate(matches, 1))
            raise CodedError(
                f"Cannot make '{name}' active: {len(matches)} geometrical sets carry "
                f"that name ({where}), and Collection.Item(name) would answer the "
                "first one without saying so. Nothing was changed. Delete the empty "
                "duplicate (or use the set that is already active) and repeat.",
                code="GEOSET_AMBIGUOUS",
                stage="validate",
                modified=False,
                data={"name": name, "matches": [
                    {"parent": parent, "depth": depth}
                    for _hb, parent, depth in matches]},
            )

        available = [str(item[0].Name) for item in index]
        raise ArgumentRejected(
            f"Geometrical set '{name}' not found. Available: {available or 'none'}"
        )

    def _list_elements(self) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()

        result: dict[str, Any] = {
            "active_geoset": self._active_geoset_name,
            "geometrical_sets": [],
            "body_sketches": [],
        }

        for position, (hb, parent, depth) in enumerate(self._geoset_index(part), 1):
            shapes = []
            try:
                hs = hb.HybridShapes
                for i in range(1, hs.Count + 1):
                    shapes.append(hs.Item(i).Name)
            except Exception:
                pass
            sketches = []
            try:
                sk = hb.HybridSketches
                for i in range(1, sk.Count + 1):
                    sketches.append(sk.Item(i).Name)
            except Exception:
                pass
            # parent/depth: without them a set created inside another came back
            # flat beside it, so the ONE thing catia_gsd_create_geoset(parent=)
            # promises could not be verified by the caller - and two sets with
            # the same name could not be told apart at all (reported 2026-11-08,
            # M2).
            result["geometrical_sets"].append(
                {"index": position, "name": str(hb.Name), "parent": parent,
                 "depth": depth, "elements": shapes, "sketches": sketches}
            )

        bodies = part.Bodies
        for i in range(1, bodies.Count + 1):
            body = bodies.Item(i)
            try:
                for j in range(1, body.Sketches.Count + 1):
                    result["body_sketches"].append(
                        {"body": body.Name, "sketch": body.Sketches.Item(j).Name}
                    )
            except Exception:
                pass

        return json.dumps(result, indent=2, ensure_ascii=False)

    # ────────────────────────────────────────────────────────────
    # Wireframe
    # ────────────────────────────────────────────────────────────

    def _point(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        x, y, z = args["x"], args["y"], args["z"]
        point = self._factory().AddNewPointCoord(x, y, z)
        return self._finish(point, args.get("name"), f"3D point created at ({x}, {y}, {z}) mm.")

    def _line(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        # One context lookup for both points, same as catia_gsd_spline: the
        # per-point ActiveDocument round trip was 90 % of a spline's build time
        # (measured on B30 2026-11-03).
        ref1, ref2 = self._point_refs([args["point1"], args["point2"]])
        line = self._factory().AddNewLinePtPt(ref1, ref2)
        return self._finish(line, args.get("name"), "3D line created between two points.")

    def _plane_offset(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        base_ref = self._ref(args["base_plane"])
        offset = args["offset"]
        reverse = args.get("reverse", False)
        plane = self._factory().AddNewPlaneOffset(base_ref, offset, reverse)
        return self._finish(
            plane, args.get("name"),
            f"Offset plane created: {offset} mm from '{args['base_plane']}'"
            f"{' (reversed)' if reverse else ''}.",
        )

    def _plane_3points(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        ref1, ref2, ref3 = self._point_refs(
            [args["point1"], args["point2"], args["point3"]])
        plane = self._factory().AddNewPlane3Points(ref1, ref2, ref3)
        return self._finish(plane, args.get("name"), "Plane created through 3 points.")

    _MAIN_PLANES = {"xy": "PlaneXY", "yx": "PlaneXY",
                    "yz": "PlaneYZ", "zy": "PlaneYZ",
                    "zx": "PlaneZX", "xz": "PlaneZX"}

    def _plane_ref(self, spec: str) -> tuple[Any, str]:
        """``(reference, label)`` for a reference plane or a named plane element.

        A string is either one of the three main planes or the name of a planar
        element in the tree — the same two forms ``catia_create_sketch`` takes,
        so a plane built by this tool can be fed straight back in.
        """
        key = str(spec).strip().lower()
        part = self.conn.get_active_part()
        attribute = self._MAIN_PLANES.get(key)
        if attribute is not None:
            return (part.CreateReferenceFromObject(
                getattr(part.OriginElements, attribute)), f"the {key} main plane")
        return self._ref(str(spec)), f"'{spec}'"

    def _plane_angle(self, args: dict[str, Any]) -> str:
        """A datum plane rotated about an axis — ``AddNewPlaneAngle``.

        The tutorial step this exists for ("新建两个基准面，角度取 36.00°") is the
        general way to take one 1/N sector of a revolved part, and before 0.3.84
        the only substitute was a hand-computed frame: three vectors to recompute
        whenever the angle changed, and a hidden temporary element instead of a
        reusable tree node.

        Measured on B30 2026-11-09 (scripts/b30_v0384_planeangles.py,
        _final.py), because the call has one trap that costs a whole session if
        it is not known:

          * THE AXIS HAS TO LIE IN THE REFERENCE PLANE. Rotating the XY plane
            about Z is a rotation of a plane about its own normal: the plane does
            not move, and CATIA does not say "degenerate" - ``UpdateObject``
            fails with a generic E_FAIL, which reads like a bad argument. Eight
            call shapes were refused that way before the geometry was blamed
            instead of the arguments.
          * The angle is in DEGREES. 72 landed 0.0000 mm from a point at 72
            degrees on the rotated plane; 1.2566 (the same angle in radians) put
            the plane 0.1355 mm away, i.e. it was read as 1.2566 degrees.
          * ``orientation`` made no difference on that geometry (0 and 1 and -1
            all produced the same plane), so it is passed through rather than
            interpreted.
        """
        self.conn.ensure_connected()
        angle = float(args["angle"])
        if abs(angle) < 1e-9:
            raise ArgumentOutOfRange(
                f"angle must be non-zero; got {angle}. A plane rotated by 0 "
                "degrees is the reference plane itself.")
        plane_ref, plane_label = self._plane_ref(str(args["plane"]))
        axis_ref, axis_note = self._axis_ref(args["axis"], "PlaneAngle")
        orientation = int(args.get("orientation", 0))

        plane = self._factory().AddNewPlaneAngle(plane_ref, axis_ref,
                                                angle, orientation)
        try:
            reply = self._finish(
                plane, args.get("name"),
                f"Datum plane created {angle:g} degrees from {plane_label} about "
                f"the axis given{axis_note}. This is a TREE ELEMENT, so it can be "
                "referenced by later sketches and features - unlike a frame "
                "sketch, which carries no name and cannot be picked.",
            )
        except RuntimeError as error:
            raise PreconditionRejected(
                f"CATIA rejected the {angle:g}-degree plane from {plane_label} "
                f"({str(error).splitlines()[0][:120]}). The plane was removed "
                "from the tree again. The one cause measured on B30 is "
                "GEOMETRY, not the arguments: the rotation axis has to LIE IN "
                "the reference plane. Rotating a plane about its own normal "
                "does not move it, and this build answers with a generic "
                "failure rather than 'degenerate'. For the usual case - a "
                "sector of a revolved part - pass a plane and an axis that "
                "share a direction, e.g. plane='yz' with axis='z', or "
                "plane='zx' with axis='x'.",
                code="PLANE_ANGLE_REJECTED", stage="execute",
            ) from error
        remembered = self._remember_plane_angle(plane, args, angle)
        return reply + remembered

    def _remember_plane_angle(self, shape: Any, args: dict[str, Any],
                              angle: float) -> str:
        """Remember the rotation this plane was built from, so a sketch on it can
        be placed in 3D later.

        The plane's own frame cannot be read back on this build: a typeinfo dump
        of ``HybridShapePlaneAngle`` (44 members, 2026-11-09) shows
        ``GetFirstAxis`` / ``GetSecondAxis`` / ``GetPosition`` / ``GetOrigin``,
        and every one of them answers 「方法 … 失败」 because they take by-ref
        out-parameters - the same wall as ``Sketch.GetAbsoluteAxisData``. What IS
        readable is what it was built from, so that is recorded: the reference
        plane's frame rotated about this axis by this angle. Callers that publish
        coordinates check that claim against a measurement before trusting it
        (``_verify_frame``), because a reconstruction is not a reading.
        """
        base = str(args.get("plane", "")).strip().lower()
        axis = str(args.get("axis", "")).strip().lower()
        if base not in ("xy", "yz", "zx") or axis not in ORIGIN_AXIS:
            return (" The sketch-frame record for this plane is NOT available: "
                    "its reference has to be one of xy/yz/zx and its axis one of "
                    "'x'/'y'/'z' for the frame to be reconstructible, so a sketch "
                    "drawn on it will refuse to give global coordinates.")
        try:
            name = str(shape.Name)
        except Exception:  # noqa: BLE001 - no name, nothing to key on
            return ""
        store = getattr(self.conn, "datum_plane_angles", None)
        if store is None:
            store = {}
            self.conn.datum_plane_angles = store
        store[name] = {"base": base, "axis": list(ORIGIN_AXIS[axis]),
                       "angle": float(angle), "axis_name": axis}
        return (f" Its frame is recorded as the {base} plane rotated {angle:g} "
                f"degrees about {axis}, so a sketch drawn on it can be turned "
                "back into global XYZ (checked against a measurement when it is "
                "used).")

    _CLOSE_EPS = 1e-6

    @staticmethod
    def _as_xyz(spec: Any) -> list[float] | None:
        """``[x, y, z]`` as floats, or None when the point is named, not given."""
        if not isinstance(spec, (list, tuple)) or len(spec) != 3:
            return None
        try:
            return [float(value) for value in spec]
        except (TypeError, ValueError):
            return None

    def _drop_closing_point(self, points: list[Any]) -> tuple[list[Any], str]:
        """Remove a trailing repeat of the first point from a CLOSED spline.

        Closing a ring is the intuitive thing to do and it is wrong here: the
        spline's own ``SetClosing(1)`` already joins the last point to the
        first, so repeating the start makes the curve pass through one point
        twice and CATIA refuses the WHOLE spline. It does not say "duplicate
        point" either - it raises a generic invalid-parameter error, which
        reads like "your coordinates are bad" and sends you counting decimals
        (reported 2026-11-04: a hand-built ten-point section, written the
        obvious way, was rejected until the repeat came out).
        """
        if len(points) < 4:
            return points, ""
        first = self._as_xyz(points[0])
        last = self._as_xyz(points[-1])
        if first is None or last is None:
            return points, ""
        if max(abs(a - b) for a, b in zip(first, last)) > self._CLOSE_EPS:
            return points, ""
        kept = list(points[:-1])
        return kept, (
            f" The last point you gave repeats the first to within "
            f"{self._CLOSE_EPS:g} mm and was dropped: a closed spline already "
            f"joins back to its start, and keeping the repeat makes CATIA refuse "
            f"the whole spline. {len(points)} points in, {len(kept)} on the curve."
        )

    def _spline(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        element = args.get("element")
        closed = bool(args.get("closed", False))
        # Coordinates become Point FEATURES by default when the caller asks for
        # it, and tree-less otherwise. See _point_refs for the save/reopen
        # measurement that makes the default safe.
        in_tree = bool(args.get("create_points", False))
        self._coordinate_points = 0

        if element and args.get("points"):
            raise ArgumentRejected(
                "Pass either 'points' or 'element', not both: 'element' takes the "
                "corners of a sketch this server drew, 'points' takes them from you."
            )
        if element:
            coords, note = self._outline_points(str(element), closed)
            refs = self._point_refs(coords, in_tree)
            count = len(coords)
        else:
            points = args.get("points")
            if not points:
                raise ArgumentRejected(
                    "catia_gsd_spline needs 'points' (element names or [x, y, z]) "
                    "or 'element' (a sketch whose outline this server drew)."
                )
            closing_note = ""
            if closed:
                points, closing_note = self._drop_closing_point(points)
            refs = self._point_refs(points, in_tree)
            count = len(points)
            note = closing_note

        spline = self._factory().AddNewSpline()
        for ref in refs:
            spline.AddPoint(ref)
        if closed:
            spline.SetClosing(1)

        # A spline holds REFERENCES to points. With create_points=false those
        # points are objects the part saves but does not list, which is the
        # difference between "one sweep" and a geometrical set holding 636
        # elements that takes 21+ minutes to delete one by one (reported
        # 2026-11-03 across two rounds, with 636 points for ten sections).
        created = self._coordinate_points
        if created and in_tree:
            points_note = (
                f" {created} of them were coordinate lists, and each one is a separate "
                f"Point feature in the geometrical set (create_points=true): expect "
                f"{created} extra tree elements."
            )
        elif created:
            points_note = (
                f" {created} of them were coordinate lists and did NOT become tree "
                f"elements: the spline references them and the part saves them, but "
                f"the geometrical set lists one element (this spline). Pass "
                f"create_points=true if you want the Point features visible."
            )
        else:
            points_note = ""
        return self._finish(
            spline, args.get("name"),
            f"3D spline created through {count} points"
            f"{' (closed)' if closed else ''}.{note}{points_note}",
        )

    # Matching tolerance when chaining a sketch's lines back into a loop.
    # The recorded numbers are the caller's own, so they agree exactly; the
    # slack is for CATIA snapping a typed endpoint onto an existing point.
    _LOOP_TOL = 1e-3

    def _outline_points(self, name: str, closed: bool
                        ) -> tuple[list[list[float]], str]:
        """Global coordinates of a sketch's outline corners, in loop order.

        Exists because a loft section is ONE curve and a polygon outline is
        several: ``catia_sketch_rectangle`` is four curves, so it cannot be
        lofted, and neither can the Extract of it (measured on B30 2026-10-25).
        The way through is a curve through the same corners, which needs their
        coordinates - and a sketch cannot give them back, because a CATIA
        Point2D exposes no readable X/Y through automation. So the corners come
        from what this server drew, and the sketch's placement turns them into
        global XYZ.
        """
        outlines = getattr(self.conn, "sketch_outlines", None) or {}
        try:
            document = str(self.conn.active_document.Name)
        except Exception:  # noqa: BLE001 - no document, no record either
            document = "?"
        # A rebuild records the outline under the NEW name and keeps the old
        # record, so both keys can hit; the alias covers the case where only the
        # new one does (reported 2026-10-28: gsd_spline(element='草图.2') said
        # "No outline recorded" while pad and get_geometry both resolved it).
        alias = self._sketch_alias(name)
        recorded = outlines.get(f"{document}::{name}")
        if not recorded and alias and alias != name:
            recorded = outlines.get(f"{document}::{alias}")
        if not recorded:
            raise ArgumentRejected(
                f"No outline recorded for sketch '{name}' in '{document}'. This "
                "server remembers the outlines it drew in THIS session and THIS "
                "document - reopening a file does not restore them, and a sketch "
                "drawn in CATIA's own UI never had one. Pass the corners as "
                "'points' instead, or draw the outline again in this session. "
                "(Falling back to the sketch's own geometry is not possible: a "
                "CATIA Point2D exposes no readable X/Y through automation, which "
                "is why the numbers are remembered at drawing time.)"
                + (f" ('{name}' was re-created as '{alias}' after a rollback; no "
                   "outline was recorded for either name.)" if alias else "")
            )
        if not self._sketch_exists(name):
            raise ArgumentRejected(
                f"Sketch '{name}' is not in '{document}', although this session "
                "drew an outline under that name - the sketch was renamed or "
                "deleted. Run catia_list_features and read its 'sketches' array "
                "(each entry lists the aliases a rebuild gave it), or pass "
                "'points'."
                + (f" '{name}' was re-created as '{alias}', which is not in the "
                   "tree either." if alias and alias != name else "")
            )
        placement = recorded.get("placement")
        if not placement:
            raise ArgumentRejected(
                f"'{name}' has no recorded frame here, so its 2D coordinates "
                "cannot be turned into global XYZ. This covers three cases and "
                "they need different answers: (a) the sketch sits on a FACE - "
                "the frame of a face cannot be read back on this build, so "
                "rebuild on an offset datum plane; (b) the sketch sits on a datum "
                "plane BY NAME (catia_gsd_plane_offset / _plane_angle / "
                "_3points and so on) - the plane exists and is correct, but this "
                "server only carries a frame for the three main planes, and "
                "CATIA's own Sketch.GetAbsoluteAxisData needs an out-array that "
                "pywin32's late binding cannot deliver (the same wall as "
                "Measurable.GetPoint). One exception since 0.3.87: a plane built "
                "by catia_gsd_plane_angle IN THIS SESSION does carry a frame, "
                "reconstructed from its rotation (reference plane xy/yz/zx plus "
                "axis 'x'/'y'/'z') and checked against a measured distance before "
                "any coordinate is handed over; a plane that came from disk, or "
                "one rotated about a NAMED element, still has none. (c) the sketch "
                "was made in an earlier session, when nothing was recorded. "
                "WHAT TO DO INSTEAD for (b): the placement is checkable without "
                "reading it back - catia_measure_distance between the copy and "
                "the original equals the chord between the two support planes, "
                "which is computable (a copy onto a plane rotated by A degrees "
                "moves a point at radius r by 2*r*sin(A/2); measured 2026-11-09: "
                "7.0534 mm against 7.053 for r=6 at 72 degrees). Or pass the "
                "corners as 'points' explicitly, which needs no frame at all."
            )

        try:
            loop = self._chain_outline(recorded.get("elements") or [], name)
        except ArgumentRejected as error:
            # A sketch that already holds a SPLINE is one curve, so wrapping it
            # in a 3D spline is refused - correctly, and that refusal used to be
            # the end of the road: gsd_spline(element=...) was the only readback
            # this server had, and the tutorial's sections are exactly the
            # single-spline sketches. The frame is already reconstructed by this
            # point, so hand the coordinates over WITH the refusal instead of
            # keeping them to ourselves (2026-11-09 report, §4.1).
            world = self._recorded_spline_points(recorded, placement)
            if not world:
                raise
            listed = "; ".join("(%g, %g, %g)" % tuple(round(c, 4) for c in point)
                               for point in world[:8])
            more = (f" and {len(world) - 8} more" if len(world) > 8 else "")
            raise ArgumentRejected(
                f"{error} Its points, in global XYZ, so the placement can be "
                f"checked without building anything: {listed}{more} mm "
                f"(frame{self._placement_note(placement)}). Nothing was created."
                + self._verify_frame(name, max(world, key=lambda p: sum(c * c
                                                                      for c in p)),
                                     placement)
            ) from None
        if len(loop) < 2:
            raise ArgumentRejected(
                f"'{name}' holds {len(loop)} usable corner(s); a spline needs at "
                "least 2."
            )
        coords = [self._to_global(placement, u, v) for u, v in loop]
        label = (f"'{name}'" if not alias or alias == name
                 else f"'{name}' (rebuilt as '{alias}')")
        listed = "; ".join(
            "(%g, %g, %g)" % tuple(round(c, 4) for c in point)
            for point in (coords if len(coords) <= 3 else [coords[0], coords[-1]]))
        return coords, (f" Corners taken from sketch {label}"
                        f"{self._placement_note(placement)}: {listed} mm."
                        + self._verify_frame(name, self._far_corner(coords),
                                             placement))

    @staticmethod
    def _far_corner(coords: list[list[float]]) -> list[float]:
        """The corner FARTHEST from the plane's origin.

        This is the point the frame check probes with, and it has to be that one:
        the first corner of most profiles is the sketch origin, which lies on any
        sketch through it - a probe there would answer 0.0000 mm for a frame that
        is wrong by 90 degrees, which is exactly the failure the check exists to
        catch.
        """
        return max(coords, key=lambda point: sum(c * c for c in point))

    def _recorded_spline_points(self, recorded: dict[str, Any],
                                placement: dict[str, Any]) -> list[list[float]]:
        """Every control point of a recorded spline, in global XYZ."""
        for element in recorded.get("elements") or []:
            if str(element.get("kind")) != "spline":
                continue
            points = []
            for pair in element.get("pts") or []:
                try:
                    points.append(self._to_global(placement, float(pair[0]),
                                                  float(pair[1])))
                except (IndexError, TypeError, ValueError):
                    continue
            if points:
                return points
        return []

    @staticmethod
    def _placement_note(placement: dict[str, Any]) -> str:
        """How the frame was obtained, in the caller's terms."""
        base = str(placement.get("base") or "xy").lower()
        if base == "frame":
            derived = placement.get("derived")
            return (f" in the frame of {derived}" if derived
                    else " in the frame it was drawn in")
        return (f" in {base} plane order, "
                f"z={float(placement.get('distance') or 0.0):g}")

    def _verify_frame(self, name: str, point: list[float],
                      placement: dict[str, Any]) -> str:
        """Check a RECONSTRUCTED sketch frame against CATIA before trusting it.

        A frame taken from the plane's construction arguments is a claim about
        where CATIA put the sketch, and a wrong claim does not error - it puts
        every coordinate in the wrong place. So one corner is turned into a real
        point element at the position the frame predicts, and the distance from
        that point to the sketch itself is measured: on a correct frame it is
        0.0000 mm, because that point IS on the sketch.

        Only reconstructed frames are checked (a datum plane's frame is measured,
        not derived), the answer is cached on the placement, and the probe point
        is deleted again either way.
        """
        if not placement.get("derived"):
            return ""
        cached = placement.get("verified")
        if isinstance(cached, str):
            return cached
        part = self.conn.get_active_part()
        probe = None
        gap: float | None = None
        try:
            sketch = part.FindObjectByName(str(name))
            if sketch is None:
                return ""
            sketch_ref = part.CreateReferenceFromObject(sketch)
            probe = self._factory().AddNewPointCoord(float(point[0]),
                                                     float(point[1]),
                                                     float(point[2]))
            self._get_geoset().AppendHybridShape(probe)
            part.UpdateObject(probe)
            spa = self.conn.active_document.GetWorkbench("SPAWorkbench")
            gap = float(spa.GetMeasurable(
                part.CreateReferenceFromObject(probe)).GetMinimumDistance(sketch_ref))
        except Exception:  # noqa: BLE001 - an unchecked frame says so
            gap = None
        finally:
            if probe is not None:
                self._drop_element(probe)
        if gap is None:
            note = (" The frame was reconstructed from the plane's construction "
                    "(this build cannot read it back) and could not be checked "
                    "here - if the placement matters, measure the result.")
        elif gap > 0.01:
            raise PreconditionRejected(
                f"The frame recorded for '{name}' does not match the sketch: a "
                f"probe point placed where that frame predicts one corner lies "
                f"{gap:.4f} mm from the sketch itself, and on a correct frame it "
                "would be 0.0000 mm (the point would BE on the sketch). Nothing "
                "was changed - the probe point has been deleted again. The frame "
                "is reconstructed from the datum plane's rotation arguments, so "
                "this means this build oriented the sketch differently from the "
                "reconstruction. Pass the corners as 'points' explicitly, or call "
                "catia_measure_distance between the sketch and a known reference.",
                code="SKETCH_FRAME_MISMATCH", stage="validate", modified=False,
            )
        else:
            note = (f" The frame was reconstructed from the plane's construction "
                    f"(this build cannot read it back) and CHECKED: a probe point "
                    f"placed at the predicted position of one corner lies "
                    f"{gap:.4f} mm from the sketch itself.")
        placement["verified"] = note
        return note

    def _sketch_exists(self, name: str) -> bool:
        """Whether the active part still holds a sketch by that name.

        Cheap guard against a record outliving its sketch: the outline is keyed
        by document and name, and CATIA renumbers sketches when one is deleted,
        so a hit in memory is not proof that the sketch is still there.

        A rebuilt sketch is in the tree under a new name with the old one aliased
        to it, so "by that name" has to mean the same thing here as it means in
        `catia_pad` - otherwise this guard turned a live sketch into
        "the sketch was renamed or deleted" (reported 2026-10-28).
        """
        wanted = {name}
        alias = self._sketch_alias(name)
        if alias:
            wanted.add(alias)
        try:
            body = self.conn.get_active_part_body()
            sketches = body.Sketches
            for i in range(1, sketches.Count + 1):
                if str(sketches.Item(i).Name) in wanted:
                    return True
        except Exception:  # noqa: BLE001 - cannot check, do not block the call
            return True
        return False

    def _chain_outline(self, elements: list[dict[str, Any]],
                       name: str) -> list[list[float]]:
        """Order a sketch's drawn elements into one loop of 2D corners."""
        kinds = {str(e.get("kind")) for e in elements}
        if kinds == {"rectangle"} and len(elements) == 1:
            e = elements[0]
            x1, y1, x2, y2 = (float(e["x1"]), float(e["y1"]),
                              float(e["x2"]), float(e["y2"]))
            return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
        if kinds == {"line"}:
            return self._chain_lines(elements, name)

        already = kinds & {"circle", "arc"}
        if already:
            raise ArgumentRejected(
                f"'{name}' holds a {sorted(already)[0]}, which is already ONE "
                "curve - pass the sketch itself as the loft section; it needs no "
                "3D spline."
            )
        if "spline" in kinds:
            raise ArgumentRejected(
                f"'{name}' already holds a spline, which is one curve: pass the "
                "sketch itself as the section instead of wrapping it in a 3D "
                "spline."
            )
        raise ArgumentRejected(
            f"Cannot turn '{name}' into a corner list: it holds "
            f"{sorted(kinds) or 'nothing'}. Only a rectangle or a chain of lines "
            "is supported; pass 'points' for anything else."
        )

    def _chain_lines(self, elements: list[dict[str, Any]], name: str
                     ) -> list[list[float]]:
        """Walk a set of drawn lines into one ordered loop (reversing as needed)."""
        tol = self._LOOP_TOL

        def near(a: list[float], b: list[float]) -> bool:
            return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol

        remaining = []
        for e in elements:
            remaining.append([[float(e["x1"]), float(e["y1"])],
                              [float(e["x2"]), float(e["y2"])]])
        first = remaining.pop(0)
        loop = [first[0], first[1]]
        while remaining:
            tail = loop[-1]
            for index, seg in enumerate(remaining):
                if near(seg[0], tail):
                    loop.append(seg[1])
                elif near(seg[1], tail):
                    loop.append(seg[0])
                else:
                    continue
                remaining.pop(index)
                break
            else:
                raise ArgumentRejected(
                    f"The lines in '{name}' do not form one chain: after "
                    f"{len(loop)} corner(s) nothing continues from "
                    f"({tail[0]:g}, {tail[1]:g}). Draw them end to end, or pass "
                    "'points' in the order you want the spline to run."
                )
        if len(loop) > 2 and near(loop[0], loop[-1]):
            loop.pop()          # a closed polygon: the spline closes it
        return loop

    @staticmethod
    def _to_global(placement: dict[str, Any], u: float, v: float) -> list[float]:
        """Sketch (u, v) to global XYZ, per the plane it was drawn on.

        Measured on B30 2026-10-25: a 20 x 6 rectangle on each datum plane at
        offset 25 padded to the box xy -> (0..20, 0..6, 25), yz -> (25, 0..20,
        0..6), zx -> (0..6, 25, 0..20). So zx maps u to Z and v to X, which is
        the mapping everybody gets wrong once - the table now lives in
        catia_mcp.sketch_frames so this side and the sketcher cannot drift.
        """
        mapped = point_world(placement, u, v)
        if mapped is not None:
            return mapped
        return [float(u), float(v), float(placement.get("distance") or 0.0)]

    def _circle(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        center_ref = self._point_ref(args["center"])
        support_ref = self._ref(args["support_plane"])
        radius = args["radius"]

        # AddNewCircleCtrRad(center, support, geodesic, radius); limitation 1 = whole circle
        circle = self._factory().AddNewCircleCtrRad(center_ref, support_ref, False, radius)
        circle.SetLimitation(1)

        return self._finish(
            circle, args.get("name"),
            f"3D circle created: radius {radius} mm on '{args['support_plane']}'.",
        )

    def _sketch_outline_length_mm(
            self, element_name: str) -> tuple[float, int, int] | None:
        """Length of a SKETCH's outline, measured element by element.

        A sketch has no measurable ``Length`` of its own - ``GetMeasurable`` on a
        sketch raises on B30 (2026-11-10) - and the session record only exists
        for a sketch THIS server drew. So a sketch that came back with a
        reopened file used to answer "I cannot tell you", and the projection's
        completeness check quietly did nothing in exactly the case users hit
        (reopen yesterday's part and keep working) - reported 2026-11-11.

        The curves INSIDE the sketch can be measured one at a time. Measured on
        B30 2026-11-11, all against hand values: a 40x10 rectangle (100.000)
        read 100.0000 from four lines; a circle r=10 read 62.8319
        (2*pi*r=62.8319); a 90-degree arc r=10 read 15.7080; an open 4-point
        spline read 73.7960; a closed spline through a 30x20 rectangle read
        113.760. The absolute axis and the points refused, and a CONSTRUCTION
        line measured 10.000 but is excluded here because it is not part of the
        outline.

        Returns ``(total, curves_read, curves_unread)``, or None when the part
        holds no such sketch. ``curves_unread`` is not cosmetic: an unread curve
        makes the total a LOWER bound, which makes the projected share an UPPER
        bound - the direction that hides a fragment - so the caller must not
        present the ratio as exact when this is non-zero.
        """
        part = self.conn.get_active_part()
        try:
            sketch = part.FindObjectByName(str(element_name))
        except Exception:  # noqa: BLE001 - an unresolvable name is not a sketch
            return None
        if sketch is None or not hasattr(sketch, "GeometricElements"):
            return None
        try:
            count = int(sketch.GeometricElements.Count)
        except Exception:  # noqa: BLE001
            return None
        try:
            spa = self.conn.active_document.GetWorkbench("SPAWorkbench")
        except Exception:  # noqa: BLE001
            return None
        total = 0.0
        read = unread = 0
        for index in range(1, count + 1):
            try:
                element = sketch.GeometricElements.Item(index)
            except Exception:  # noqa: BLE001 - skip what will not be read
                continue
            # A CURVE ANSWERS StartPoint/EndPoint AND A POINT DOES NOT - measured
            # 2026-11-11 on a sketch holding a line, a circle, an arc and a
            # spline: all four curves answered both, the absolute axis and the
            # four spline control points answered neither. A CLOSED spline
            # answers them too, so this is not a test for "open".
            try:
                element.StartPoint
                element.EndPoint
            except Exception:  # noqa: BLE001 - axis or point: not the outline
                continue
            try:
                if element.Construction:
                    continue
            except Exception:  # noqa: BLE001 - unknown reads as "count it"
                pass
            try:
                reference = part.CreateReferenceFromObject(element)
                total += float(spa.GetMeasurable(reference).Length)
                read += 1
            except Exception:  # noqa: BLE001 - told, not hidden
                unread += 1
        return (total, read, unread) if read else None

    def _source_length_mm(
            self, element_name: str) -> tuple[float | None, str, bool]:
        """Length of the element being projected, where it came from, and whether
        it is exact.

        Three sources, in the order that trusts the model most:

        1. the element measures directly (a GSD curve, a line) - exact;
        2. the element is a SKETCH, and its own curves measure one by one -
           exact only when every curve answered (``exact`` False otherwise: the
           sum is then a lower bound and the share an upper bound);
        3. the coordinates this session recorded while drawing it - exact, but
           only for a sketch drawn here, which is the case the 2026-11-11 report
           showed to be the RARE one in real use.

        None (and ``exact`` False) when none of them answers. Never a guess.
        """
        name = str(element_name)
        try:
            reference = self._ref(name)
        except Exception:  # noqa: BLE001 - not resolvable here; the call reports it
            reference = None
        if reference is not None:
            length = self._curve_length_mm(reference)
            if length is not None:
                return length, "measured on the curve itself", True
        measured = self._sketch_outline_length_mm(name)
        if measured is not None:
            total, read, unread = measured
            if total > 0:
                where = ("measured on the sketch's %d curve%s"
                         % (read, "" if read == 1 else "s"))
                return total, where, unread == 0
        try:
            document = str(self.conn.active_document.Name)
        except Exception:  # noqa: BLE001
            return None, "", False
        outlines = getattr(self.conn, "sketch_outlines", None) or {}
        record = outlines.get(f"{document}::{name}") or {}
        total = 0.0
        found = False
        for element in record.get("elements") or []:
            kind = str(element.get("kind"))
            try:
                if kind == "line":
                    total += math.hypot(float(element["x2"]) - float(element["x1"]),
                                        float(element["y2"]) - float(element["y1"]))
                    found = True
                elif kind == "circle":
                    total += 2.0 * math.pi * float(element["radius"])
                    found = True
            except (KeyError, TypeError, ValueError):
                continue
        return ((total, "the outline this session drew", True)
                if found and total > 0 else (None, "", False))

    def _project(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        elem_ref = self._ref(args["element"])
        support_ref = self._ref(args["support"])
        projection = self._factory().AddNewProject(elem_ref, support_ref)
        try:
            reply = self._finish(
                projection, args.get("name"),
                f"Projection of '{args['element']}' onto '{args['support']}' "
                "created.",
            )
            # HOW MUCH OF IT LANDED. A projection that keeps 6 % of the element
            # is not a projection, it is a fragment - and every downstream tool
            # measures THAT fragment: engrave sweeps a tube along it, split cuts
            # with it, fill closes with it. Measured on the 2026-11-10 bottle
            # report: a 63.96 mm glyph outline onto a lofted bottle face gave a
            # 4.000 mm curve, distance-to-face 0.0000 mm (a MINIMUM, so it only
            # says "touches somewhere"), and catia_surface_engrave then found no
            # material - correctly, because the tube was 4 mm long.
            part = self.conn.get_active_part()
            try:
                projected = self._curve_length_mm(
                    part.CreateReferenceFromObject(projection))
            except Exception:  # noqa: BLE001 - an unreadable length is not 0 mm
                projected = None
            source, origin, exact = self._source_length_mm(str(args["element"]))
            warning = ""
            if projected is None:
                warning = (" WARNING: COMPLETENESS UNKNOWN - the projection's own "
                           "length could not be measured. ")
                note = (" This call cannot say how much of the element landed, and "
                        "everything downstream uses THIS curve: catia_surface_engrave "
                        "sweeps a tube along it and finds no material when it is only "
                        "a fragment. Measure it against the support before relying "
                        "on it.")
            else:
                note = f" MEASURED: the projected curve is {projected:.3f} mm long"
                if source and exact:
                    share = projected / source * 100.0
                    note += (f", {share:.1f} % of the {source:.3f} mm it was "
                             f"projected from ({origin}).")
                    if share < _PROJECT_FRAGMENT_SHARE:
                        # BEFORE the success sentence, because a caller that
                        # reads the first line and stops would otherwise take
                        # "created" for "usable" - which is what happened in the
                        # 2026-11-10 report.
                        warning = (" WARNING: ONLY PART OF THE ELEMENT LANDED - "
                                   f"{share:.1f} % of it. ")
                        note += (" A projection lands only where the support "
                                 "faces the element, and on a curved support most "
                                 "of an outline can miss it entirely. Downstream "
                                 "tools use THIS curve, not the one you drew: "
                                 "catia_surface_engrave sweeps a tube along it and "
                                 "will then find no material, and the 0.0000 mm "
                                 "distance is a MINIMUM, so it only says the "
                                 "fragment touches. Move the element closer to the "
                                 "support (catia_gsd_translate), or project onto a "
                                 "surface that wraps the target more fully.")
                elif source:
                    # SOME of the element's curves answered and some did not, so
                    # the total is a LOWER bound - which makes this share an
                    # UPPER bound. The direction matters: a share that is too
                    # high HIDES a fragment, so it cannot be reported as a
                    # measurement, only as a ceiling.
                    share = projected / source * 100.0
                    warning = (" WARNING: COMPLETENESS UNKNOWN - at most "
                               f"{share:.1f} % of the element landed. ")
                    note += (f", at most {share:.1f} % of the {source:.3f} mm in "
                             f"the parts of it that could be measured ({origin}); "
                             "a curve that would not answer makes that source a "
                             "LOWER bound, so the real share can be smaller and the "
                             "fragment bigger. Everything downstream uses THIS "
                             "curve, not the one you drew - catia_surface_engrave "
                             "sweeps a tube along it and finds no material when it "
                             "is a fragment. Measure the projected curve against "
                             "the support, or reproject from an element whose "
                             "length this server can read (a GSD curve), before "
                             "relying on the groove.")
                else:
                    warning = (" WARNING: COMPLETENESS UNKNOWN - the element's own "
                               "length could not be read at all. ")
                    note += (" This call therefore cannot say how much of the "
                             "element landed: a sketch has no measurable Length of "
                             "its own, none of the curves inside it answered, and "
                             "this session did not draw it. Everything downstream "
                             "uses THIS curve - catia_surface_engrave sweeps a tube "
                             "along it and finds no material when it is a "
                             "fragment - so check the groove's depth and length in "
                             "its reply rather than assuming the whole element "
                             "landed.")
            return warning + reply + note
        except Exception as error:  # noqa: BLE001 - the diagnostic is the point
            # A PROJECTION RUNS ALONG THE SUPPORT'S NORMAL, AND A PLANAR ELEMENT
            # NEEDS A SUPPORT THAT CROSSES IT AT AN ANGLE (0.3.97). The first
            # draft of this message blamed the PARALLEL case only - which the
            # audit then disproved in one run: the same rectangle failed onto
            # 'yz' and 'zx' too. Measured on B30 2026-11-10, 40x40 rectangle on
            # an XY plane offset 40 mm: xy FAIL, yz FAIL, zx FAIL, the block's
            # own top face FAIL, a plane tilted 45 deg about X OK. Parallel
            # collapses the curve to a point, perpendicular collapses it to a
            # line, and CATIA reports both as 方法 UpdateObject 失败.
            gap = self._cutter_gap(support_ref, elem_ref)
            measured = (
                f"Measured after the refusal: '{args['element']}' is {gap:.4f} mm "
                f"from '{args['support']}' at its closest point."
                if gap is not None else
                "The distance could not be measured after the refusal (once an "
                "update fails, new elements in that document refuse to update too "
                "- measured 2026-11-09)."
            )
            raise PreconditionRejected(
                f"CATIA created the projection of '{args['element']}' onto "
                f"'{args['support']}' and then refused it at update. A projection "
                "runs along the SUPPORT's own normal, so a PLANAR element needs a "
                "support that crosses its plane AT AN ANGLE: parallel makes every "
                "point land on the same spot, perpendicular flattens the curve "
                "onto a line, and both are degenerate - CATIA reports them as "
                "'方法 UpdateObject 失败' and nothing else. Measured on B30 "
                "2026-11-10 with a 40x40 rectangle on an XY plane offset 40 mm: "
                "'xy', 'yz' and 'zx' and the block's own top face ALL failed, and "
                "a plane tilted 45 degrees about X worked. " + measured +
                " So 'not parallel' is not enough - the support has to be skewed "
                "to the element, or the element has to not be flat. Nothing this "
                "call created was left behind.",
                code="PROJECT_FAILED", stage="execute", modified=False,
                next_actions=[
                    "Project onto a support that is NEITHER parallel NOR "
                    "perpendicular to the element - on B30 a plane tilted 45 "
                    "degrees (catia_gsd_plane_angle) worked where 'xy', 'yz' and "
                    "'zx' all failed (measured 2026-11-10)",
                    "A NON-PLANAR support is fine and is usually what you want: "
                    "projecting onto an extracted curved face succeeded in the "
                    "2026-11-10 acceptance (extract the face, then project onto "
                    "that element)",
                    "If the target really is a parallel plane, there is no "
                    "projection to make: move the element to it with "
                    "catia_gsd_translate, or extrude it there with "
                    "catia_gsd_extrude",
                ],
            ) from error

    def _extract(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        extract = self._factory().AddNewExtract(self._ref(args["element"]))
        propagation = int(args.get("propagation", 1))
        try:
            # CatPropagationType: 1 none, 2 point, 3 tangent, 4 curvature.
            extract.PropagationType = propagation
        except Exception:
            pass
        return self._finish(
            extract, args.get("name"),
            f"Extract of '{args['element']}' (propagation {propagation}) created.",
        )

    def _heal(self, args: dict[str, Any]) -> str:
        """Stitch surfaces: AddNewHealing + AddBodyToHeal per element + merge distance.

        Measured on B30 2026-11-04, every state isolated in its own STL export:

            two lofts 0.05 mm apart, no heal          48 open seam edges, 0 interior
            heal C1, merging 0.5                      0 open, 24 interior, 112 tris
            heal C1, merging 0.001                    48 open (nothing merged)
            two lofts sharing a section, 0.52 deg     C1 and C2 identical to the join
            two walls at 90 deg                       C1 and C2 identical, 90.0 stays

        So this closes gaps and changes nothing about continuity. The reason the
        0.3.51 note said "healing does nothing" is that the feature had NO body
        registered: HybridShapeHealing with an empty body list is a pass-through
        that reports success, which is why the numbers never moved. Registering
        every element is therefore the whole job of this wrapper.
        """
        self.conn.ensure_connected()
        elements = args.get("elements") or args.get("surfaces")
        if not elements:
            raise ArgumentRejected(
                "catia_gsd_heal needs 'elements': the list of surfaces to stitch, "
                "e.g. {\"elements\": [\"A\", \"B\"]}.")
        if isinstance(elements, str):
            elements = [elements]
        continuity = int(args.get("continuity", 1))
        if continuity not in (0, 1, 2):
            raise ArgumentOutOfRange(
                f"continuity must be 0 (threshold), 1 (tangent) or 2 (curvature); "
                f"got {continuity}.")
        distance = float(args.get("merging_distance", 0.1))
        if distance <= 0:
            raise ArgumentOutOfRange(
                f"merging_distance must be positive; got {distance}. A gap of "
                f"0.05 mm needed more than 0.001 to close (measured).")

        part = self.conn.get_active_part()
        refs = [self._ref(name) for name in elements]
        healed = self._factory().AddNewHealing(refs[0])
        healed.Continuity = continuity
        for ref in refs:
            # THE load-bearing call: without it the feature heals nothing and
            # still updates cleanly.
            healed.AddBodyToHeal(ref)
        try:
            healed.SetMergingDistance(distance)
        except Exception as error:  # noqa: BLE001 - the default may still work
            raise RuntimeError(
                f"CATIA refused merging_distance {distance}: "
                f"{str(error).splitlines()[0]}") from error

        registered = None
        try:
            registered = int(healed.NoOfBodiesToHeal)
        except Exception:  # noqa: BLE001 - advisory only
            pass
        reply = self._finish(
            healed, args.get("name"),
            f"Heal of {len(elements)} element(s) at Continuity {continuity}, "
            f"merging distance {distance} mm"
            + (f" ({registered} body/bodies registered with CATIA)" if registered
               else "")
            + ". This closes gaps up to that distance. It does NOT change "
              "continuity: measured on B30 2026-11-03, a 0.52 deg seam stayed "
              "0.52 deg at Continuity 1 and 2, and a 90 deg fold stayed 90.0. "
              "Check the result with catia_check_surface.",
        )
        return reply

    # One pair out of four is what CATIA accepts on a straight corner, and
    # which pair it is does not depend on how the supports were built: a 90/60/
    # 120 deg wedge and a floor-and-wall corner built in a different direction
    # all took (-1, +1) (measured on B30 2026-11-03). So the wrapper searches
    # instead of exposing a pair the caller cannot predict.
    _FILLET_ORIENTATIONS = ((1, 1), (-1, 1), (1, -1), (-1, -1))

    def _drop(self, shape: Any) -> None:
        """Delete one element by selection, immediately.

        A fillet whose ``UpdateObject`` failed stays in the tree in ERROR, and
        every later ``part.Update()`` on that document then fails too - measured
        on B30 2026-11-03, and it is what made the first probe write its
        orientation matrix and its geometry check into two different documents.
        A search therefore has to remove each losing attempt before the next,
        or it hands the caller a part that cannot be updated at all.
        """
        selection = self.conn.hso
        try:
            selection.Clear()
            selection.Add(shape)
            selection.Delete()
        finally:
            try:
                selection.Clear()
            except Exception:  # noqa: BLE001 - the delete already happened
                pass

    def _fillet_surface(self, args: dict[str, Any]) -> str:
        """Rolling-ball fillet between two surfaces, by searching the orientations.

        Measured on B30 2026-11-03 (scripts/b30_v0356_fillet_geom.py and
        _fillet_scope.py), with the walls hidden so the STL carried the fillet
        alone:

        90 deg wedge, r=5, 40 mm edge   every vertex exactly 5.0000 mm from the
                                        rolling-ball axis (min = max), area
                                        314.159 mm2 = (pi/2)*5*40, and 0 mm from
                                        both walls
        60 deg / 120 deg, same radius   radius min = max = 5.0000, areas 418.879
                                        and 209.440 mm2 = (pi - theta)*r*L
        floor z=0 + wall x=0, r=5       same: 5.0000 mm, 465.874 mesh / 471.239
                                        exact, 0 deg from both walls

        So the surviving call is a real fillet, not merely a surface that
        happened to update. What it is NOT: a guaranteed unique answer. The disc
        with a cylinder standing on it built TWO different surfaces, which is
        why more than one survivor is a refusal here instead of a pick.
        """
        self.conn.ensure_connected()
        radius = float(args["radius"])
        if radius <= 0:
            raise ArgumentOutOfRange(
                f"radius must be positive; got {radius} mm")
        first = str(args["surface1"])
        second = str(args["surface2"])
        part = self.conn.get_active_part()
        ref1 = self._ref(first)
        ref2 = self._ref(second)
        factory = self._factory()
        geoset = self._get_geoset()

        failed: list[str] = []
        kept: list[tuple[int, int, Any]] = []
        for o1, o2 in self._FILLET_ORIENTATIONS:
            shape = None
            try:
                shape = factory.AddNewFilletBiTangent(ref1, ref2, radius,
                                                      o1, o2, 0, 0)
                geoset.AppendHybridShape(shape)
                # Named before the update so a failure can be reported by name.
                shape.Name = f"FilletTry_{o1:+d}{o2:+d}"
                part.UpdateObject(shape)
            except Exception as error:  # noqa: BLE001 - the search is the point
                failed.append(f"(o1={o1:+d}, o2={o2:+d}): "
                              f"{str(error).splitlines()[0][:90]}")
                if shape is not None:
                    try:
                        self._drop(shape)
                    except Exception:  # noqa: BLE001 - report the search, not this
                        pass
                continue
            kept.append((o1, o2, shape))

        if not kept:
            raise CodedError(
                f"CATIA refused a {radius} mm fillet between '{first}' and "
                f"'{second}' in all four orientations, so nothing was created:\n  "
                + "\n  ".join(failed)
                + "\nA rolling ball needs the two surfaces to form a CORNER - "
                  "sharing an edge, with the ball fitting between them. Two "
                  "surfaces that merely exist fail every orientation (measured: "
                  "two disjoint parallel cylinders, 0 of 9 combinations). Check "
                  "that they meet with catia_measure_distance; if the wall is "
                  "thick enough, catia_fillet rounds a solid edge."
            , code="FEATURE_REJECTED", stage="execute")

        if len(kept) > 1:
            # Different survivors are different surfaces, not variants of one:
            # measured on a disc with a cylinder standing on it, (+1,+1) gave
            # 573.4 mm2 and (+1,-1) gave 391.3 mm2. Picking one would be a guess
            # dressed as a result, so all of them go.
            built = ", ".join(f"(o1={o1:+d}, o2={o2:+d})" for o1, o2, _s in kept)
            for _o1, _o2, shape in kept:
                try:
                    self._drop(shape)
                except Exception:  # noqa: BLE001 - the refusal is the message
                    pass
            raise ArgumentRejected(
                f"{len(kept)} orientations built a fillet between '{first}' and "
                f"'{second}': {built}. When more than one survives they are "
                "DIFFERENT surfaces, not one surface found twice - measured on a "
                "disc with a cylinder standing on it, (+1,+1) produced 573.4 mm2 "
                "and (+1,-1) 391.3 mm2. This tool will not choose for you, and "
                "has removed all of them, so the part is unchanged. Build the "
                "fillet into the geometry instead (an arc in the profile), or "
                "round a solid edge with catia_fillet."
            )

        o1, o2, shape = kept[0]
        return self._completed(
            shape, args.get("name"),
            f"Fillet of radius {radius} mm between '{first}' and '{second}', "
            f"built with orientation (o1={o1:+d}, o2={o2:+d}) - the only pair "
            f"CATIA accepted out of four. Supports are NOT trimmed (trimMode 0), "
            f"so '{first}' and '{second}' are unchanged; check the result with "
            f"catia_check_surface or catia_get_bounding_box.",
            geoset,
        )

    def _spine(self, args: dict[str, Any]) -> str:
        """The curve normal to a set of section curves: ``AddSection`` per section.

        0.3.53 wrote that the typelib names no way to add sections. It does:
        HybridShapeSpine declares 40 members including AddSection(iSection),
        AddGuide(iGuide), GetNumberOfSections() and ModifySectionCurve. The old
        probe looked at ``dir(spine)`` filtered for "Add"/"ection", which for a
        late-bound COM object returns almost nothing - declared was read as
        absent.

        Measured on B30 2026-11-03 (scripts/b30_v0356_spine.py, _spine2.py,
        _spine3.py): AddSection moves GetNumberOfSections, UpdateObject succeeds
        with two or more sections and fails with one, and the result really is a
        spine - three parallel rings 40 mm apart gave 40.0000 mm along their
        common axis and did NOT bend when one centre was offset 30 mm sideways
        (a spine follows the normals), while a second ring tilted 45 deg turned
        it into a 37.9224 mm curve that leaves that axis by 7.05 mm at z=10.
        """
        self.conn.ensure_connected()
        sections = args.get("sections") or []
        if isinstance(sections, str):
            sections = [sections]
        if len(sections) < 2:
            raise ArgumentRejected(
                f"catia_gsd_spine needs at least TWO sections; got {len(sections)}. "
                "Measured on B30 2026-11-03: a spine carrying one section fails at "
                "UpdateObject, so passing one would only leave an element in error "
                "in the tree."
            )
        guides = args.get("guides") or []
        if isinstance(guides, str):
            guides = [guides]

        part = self.conn.get_active_part()
        geoset = self._get_geoset()
        spine = self._factory().AddNewSpine()
        geoset.AppendHybridShape(spine)
        spine.Name = "Spine"
        try:
            for name in sections:
                spine.AddSection(self._ref(name))
            registered = int(spine.GetNumberOfSections())
            # The healing lesson: a call that returns without raising can still
            # be a no-op. Count, do not assume.
            if registered != len(sections):
                raise CodedError(
                    f"CATIA kept {registered} of the {len(sections)} sections "
                    f"({', '.join(sections)}). Sections have to be curves the "
                    "spine can run normal to; a point or a surface is accepted by "
                    "AddSection and then silently dropped.", code="FEATURE_INCOMPLETE", stage="execute")
            for name in guides:
                spine.AddGuide(self._ref(name))
            guide_count = int(spine.GetNumberOfGuides())
            if guides and guide_count != len(guides):
                raise CodedError(
                    f"CATIA kept {guide_count} of the {len(guides)} guides "
                    f"({', '.join(guides)}).", code="FEATURE_INCOMPLETE", stage="execute")
        except Exception:
            # Same rule as the fillet search: never leave an element in error.
            try:
                self._drop(spine)
            except Exception:  # noqa: BLE001 - the original error matters more
                pass
            raise

        message = f"Spine across {registered} sections"
        if guides:
            message += f" with {guide_count} guide(s)"
        message += (". The curve runs NORMAL to the sections, not through their "
                    "centres - parallel sections give a straight spine, tilted "
                    "ones bend it.")
        reply = self._completed(spine, args.get("name") or "Spine", message, geoset)
        try:
            measurable = (self.conn.active_document.GetWorkbench("SPAWorkbench")
                          .GetMeasurable(
                              part.CreateReferenceFromObject(spine)))
            reply += f" Length {float(measurable.Length):.4f} mm."
        except Exception:  # noqa: BLE001 - the spine exists either way
            pass
        return reply

    def _boundary(self, args: dict[str, Any]) -> str:
        """The boundary curve of a surface: ``AddNewBoundaryOfSurface(Surface)``.

        Measured on B30 2026-11-03: the typelib declares it with ONE parameter,
        and it updates - on a 30 mm extrusion of a r=10 circle the boundary
        measured 125.664 mm, i.e. exactly the two circles (2 x 2*pi*10). That
        makes it the cheap way to rebuild a patch from its border, which is what
        "delete this face, keep its outline" needs.

        No propagation argument: unlike Extract, the boundary of a surface is one
        curve and there is nothing to propagate along. The two-argument overload
        some documentation mentions is not reachable here (measured: passing two
        arguments raises "takes from 1 to 2 positional arguments but 3 were
        given" from the wrapper).
        """
        self.conn.ensure_connected()
        element = args["surface"]
        if args.get("edge") is not None:
            return self._boundary_loop(args)
        boundary = self._factory().AddNewBoundaryOfSurface(self._ref(element))
        return self._finish(
            boundary, args.get("name"),
            f"Boundary of '{element}' created. ONE curve carries EVERY loop of "
            "the surface: on a surface with a hole that is the outer border and "
            "the hole's border in a single element. That matters before the next "
            "step - handing this curve to catia_gsd_fill rebuilds the WHOLE face "
            "over the original, and the duplicate surfaces then break a "
            "thickening (measured on a caller's five-lobe base, 2026-11-09: 1714 "
            "edges shared by more than two triangles). There is no loop selector "
            "to ask for one of them: AddNewBoundaryOfSurface takes exactly one "
            "argument on this build. TO CAP A HOLE, cut instead of filling the "
            "border - catia_gsd_split(element=surface, cutter=<a circle or curve "
            "on the hole>) keeps the outer piece with that curve as its inner "
            "border, then catia_gsd_fill(boundaries=[that curve]) and "
            "catia_gsd_join share the edge by construction. Measured end to end "
            "on B30 2026-11-09: 1600.000 -> split 1486.903 -> fill 113.097 -> "
            "join 1600.000 mm2, then thickness 0.5 gave 800.0 mm3.",
        )

    def _boundary_loop(self, args: dict[str, Any]) -> str:
        """One loop of a surface's border, chosen by index.

        ``AddNewBoundaryOfSurface(surface)`` takes one argument and returns ONE
        curve carrying every loop, which is why the 2026-11-09 report could not
        cap a hole: filling that curve rebuilds every loop as a separate flat
        face stacked on the original. ``AddNewBoundary(edge, support,
        propagation)`` takes three, and the third one is the loop selector the
        report asked for - measured on B30 2026-11-09 on a cone whose two border
        circles have different lengths:

            propagation 0 -> 188.496 mm   both loops
            propagation 1 -> 62.832 mm    the r=10 loop only
            propagation 1 -> 125.664 mm   the r=20 loop only
            propagation 2 -> the same as 1 on a smooth border

        (0 is "the whole border", so passing 0 is the same as omitting 'edge'.)

        WHERE IT DOES NOT WORK, and this is the part worth knowing before trying
        it on an array: on a JOIN of five patches all 30 border edges refused at
        ``UpdateObject`` (same day, scripts/b30_v0385_cap.py), while the
        one-argument version on the same joined surface was fine (border 205.725
        mm). So the selector is a single-feature tool. On an array result, pick
        the loop BEFORE the join, or cap with the source edge rotated (what
        catia_gsd_circ_pattern's cap_center does).

        Which index belongs to the surface is decided by CATIA rather than by us:
        the factory REJECTS an edge that is not on the given support (measured),
        so the edges are offered to it one by one and the ones it refuses are not
        this surface's. The rows are only OFFERED, never updated, so a wrong
        guess costs nothing but a COM object.

        A fill built on the loop covers only that loop: the same measurements gave
        1256.637 mm2 for the r=20 loop and 314.159 for the r=10 one, against
        1570.796 - the two discs ADDED - for a fill of both.
        """
        self.conn.ensure_connected()
        element = str(args["surface"])
        index = int(args["edge"])
        if index < 1:
            raise ArgumentOutOfRange(
                f"edge={index} - the index counts loops from 1 (measured: a cone "
                "exposes 2 surface-border edges).", code="EDGE_OUT_OF_RANGE",
                stage="validate")
        propagation = int(args.get("propagation", 1))
        if propagation not in (0, 1, 2):
            raise ArgumentOutOfRange(
                f"propagation={propagation} is not a CATIA mode: 0 returns the "
                "whole border (all loops), 1 the loop of the chosen edge, 2 the "
                "tangent chain from it.", code="PROPAGATION_OUT_OF_RANGE",
                stage="validate")
        surface_ref = self._ref(element)
        factory = self._factory()
        part = self.conn.get_active_part()
        geoset = self._get_geoset()
        sel, rows = self._geoset_border_edges()
        offered: list[tuple[Any, float | None]] = []
        try:
            if not rows:
                raise PreconditionRejected(
                    "The active geometrical set exposes no surface-border edges, "
                    "so there is no loop to choose. Build the surface first, or "
                    "pass no 'edge' to get the whole border as one curve.",
                    code="NO_BORDER_EDGES", stage="validate")
            for _cell, reference, length in rows:
                try:
                    feature = factory.AddNewBoundary(reference, surface_ref,
                                                     propagation)
                except Exception:  # noqa: BLE001 - not this surface's edge
                    continue
                offered.append((feature, length))
        finally:
            try:
                sel.Clear()
            except Exception:  # noqa: BLE001
                pass
        if not offered:
            raise ArgumentOutOfRange(
                f"None of the {len(rows)} surface-border edge(s) in "
                f"'{geoset.Name}' belongs to '{element}', so there is no loop to "
                "cut out of it. Name the surface whose border you mean, or pass "
                "no 'edge' to take its whole border as one curve.",
                code="BOUNDARY_EDGE_NOT_ON_SURFACE", stage="validate")
        if index > len(offered):
            listed = ", ".join(
                f"{n}" + (f" ({length:.4f} mm)" if length is not None else "")
                for n, (_f, length) in enumerate(offered, start=1))
            raise ArgumentOutOfRange(
                f"edge={index} is out of range: '{element}' exposes "
                f"{len(offered)} surface-border edge(s) - {listed}. Which loop is "
                "the inner one is a question of length and distance to the axis, "
                "not of index order.", code="EDGE_OUT_OF_RANGE", stage="validate")
        chosen, length = offered[index - 1]
        geoset.AppendHybridShape(chosen)
        # What came back is described by a MEASUREMENT, not by a word. "One loop"
        # was wrong on a four-sided flat sector: the reply called a 25.0000 mm
        # radial straight edge "Loop #1 ... THIS loop only" while the border is
        # 91.1450 mm in all four sides (reported 2026-11-09).
        #
        # The total is summed over the edges the FACTORY accepted for THIS
        # surface, not over every border edge in the set: a datum plane's border
        # carries the same Selection_BorderREdge label as a surface's, and its
        # length is 8e7 mm (CATIA's idea of an infinite plane), which turned
        # "91.1450" into "80000091.1450" the first time this was written.
        lengths = [length for _feature, length in offered if length is not None]
        total = sum(lengths) if lengths else None
        if length is not None and total:
            share = (f"{length:.4f} mm of this surface's {total:.4f} mm of border "
                     f"edges")
            if abs(length - total) < 1e-4:
                shape = ("that is the WHOLE border in one curve - a closed "
                         "surface's single loop")
            else:
                shape = ("so it is PART of the border, not necessarily a closed "
                         "loop: a straight side of a flat sector is an open "
                         "segment, and a fill on one of those does not fail - it "
                         "builds a degenerate face that only shows up at a "
                         "thickening or an export. Check the endpoints before "
                         "filling it")
        elif length is not None:
            share = f"{length:.4f} mm of border"
            shape = "whether it closes was not measured (no per-edge lengths)"
        else:
            share = "length not readable"
            shape = "whether it closes was not measured (the measurable refused)"
        try:
            message = self._completed(
                chosen, args.get("name"),
                f"Border curve #{index} of '{element}' created"
                f" ({share}), propagation={propagation}. {shape}.",
                geoset)
        except Exception as error:  # noqa: BLE001 - keep the measurement
            raise PreconditionRejected(
                "CATIA accepted the edge and refused the feature at update "
                f"({str(error).splitlines()[0][:140]}). Two DIFFERENT situations "
                "produce this, and this message will not pretend to know which "
                "one you hit: (a) the surface is a JOIN result - measured on B30 "
                "2026-11-09, all 30 border edges of a five-patch array refused at "
                "UpdateObject while the whole-border call on that same surface "
                "worked; (b) the surface is a single feature and the build still "
                "refuses the selector - the 2026-11-09 report hit this on a "
                "single multi-section surface (a loft), which is NOT a join. What "
                "is measured to work: the one-argument whole-border boundary "
                "(omit 'edge'), and catia_gsd_circ_pattern(cap_center=true, "
                "cap_curve=...) for closing a ring's centre. Take the loop from "
                "the piece before the join where there is one.",
                code="BOUNDARY_LOOP_FAILED", stage="execute") from error
        return message

    def _intersection(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        ref1 = self._ref(args["element1"])
        ref2 = self._ref(args["element2"])
        inter = self._factory().AddNewIntersection(ref1, ref2)
        return self._finish(
            inter, args.get("name"),
            f"Intersection of '{args['element1']}' and '{args['element2']}' created.",
        )

    # ────────────────────────────────────────────────────────────
    # Surfaces
    # ────────────────────────────────────────────────────────────

    # What an element is, as far as a loft section is concerned, judged by its
    # automation type name rather than by measuring it: a circle answers Length
    # 62.83, a surface answers its boundary length, and a point answers nothing -
    # but every GetMeasurable round trip is expensive next to the type name, and
    # the type name is exact ('HybridShapePointCoord' against
    # 'HybridShapeCircleCtrRad', measured on B30 2026-11-03).
    _SECTION_POINT = "point"
    _SECTION_VERTEX = "vertex"

    def _section_kind(self, name: str, element: Any | None = None) -> str:
        """'vertex' / 'point' / 'curve' / 'unknown' - never raises.

        ``element`` is the tree node when the caller has it already, which is how
        the loft avoids looking every section up twice (the lookup is the cost:
        23 ms per name against 0.3 ms for the type name).

        'unknown' means nothing could be decided, and a caller must treat it as
        "carry on": refusing a valid section would be worse than the failure this
        tries to prevent.
        """
        text = str(name).strip()
        if self._TOPO_NAME.fullmatch(text) and text.lower().startswith("vertex"):
            return self._SECTION_VERTEX
        if element is None:
            try:
                element = self._find_element(text)
            except Exception:  # noqa: BLE001 - a missing element is _ref's problem
                return "unknown"
        type_name = self._com_type_name(element)
        if type_name:
            if "Point" in type_name and "Circle" not in type_name:
                return self._SECTION_POINT
            return "curve"
        # No type information on this build: fall back to the measurement.
        try:
            part = self.conn.get_active_part()
            spa = self.conn.active_document.GetWorkbench("SPAWorkbench")
            measurable = spa.GetMeasurable(part.CreateReferenceFromObject(element))
            length = float(measurable.Length)
        except Exception:  # noqa: BLE001 - cannot judge, so do not judge
            return "unknown"
        return self._SECTION_POINT if length <= 1e-6 else "curve"

    @staticmethod
    def _com_type_name(element: Any) -> str | None:
        """The automation type behind a tree node, or None if it will not say.

        ``dir()`` on these objects raises inside GetTypeInfoCount on B30, but
        asking the type information for its name works, and it is the only cheap
        way to tell a datum point from a curve.
        """
        try:
            info = element._oleobj_.GetTypeInfo()
            return str(info.GetDocumentation(-1)[0])
        except Exception:  # noqa: BLE001 - not every node exposes type information
            return None

    def _resolve_sections(self, names: list[str]) -> list[tuple[Any, Any]]:
        """One tree lookup per section: ``(element or None, reference)``.

        The loft needs the reference and the point guard needs the element's
        automation type. Resolving each name once for both is the difference
        between 23 ms and 46 ms per section (measured on B30 2026-11-03: a
        ten-section loft spent 230 ms on the guard alone when it looked the tree
        up a second time).

        A ``Vertex.N`` / ``Edge.N`` spec resolves to a topology reference and no
        tree node, so its element is None - the guard handles that by name.
        """
        part = self.conn.get_active_part()
        resolved: list[tuple[Any, Any]] = []
        for name in names:
            topology = self._topology_reference(name)
            if topology is not None:
                resolved.append((None, topology))
                continue
            element = self._find_element(name)
            resolved.append((element, part.CreateReferenceFromObject(element)))
        return resolved

    def _refuse_point_sections(self, sections: list[str],
                               resolved: list[tuple[Any, Any]] | None = None) -> None:
        """Reject a point/vertex section BEFORE the loft is built.

        Both forms fail, one stage apart, and neither failure says why (measured
        on B30 2026-11-03):

            datum point   AddSectionToLoft ACCEPTS it, then UpdateObject fails
                          with the generic "CATIA rejected the feature" text
            Vertex.N      AddSectionToLoft fails at once with "invalid number
                          of arguments", which reads like a wrong argument count

        The CATIA UI does offer a vertex as an end section; this automation
        object does not. Refusing here keeps the tree clean and, more to the
        point, stops the caller from rewriting a section that was never
        consumable (reported 2026-11-03, P1-2).
        """
        if resolved is None:
            kinds = [self._section_kind(name) for name in sections]
        else:
            kinds = [self._section_kind(name, element)
                     for name, (element, _ref) in zip(sections, resolved)]
        bad = [(name, kind) for name, kind in zip(sections, kinds)
               if kind in (self._SECTION_POINT, self._SECTION_VERTEX)]
        if not bad:
            return
        listing = ", ".join(f"'{name}' ({kind})" for name, kind in bad)
        raise ArgumentRejected(
            f"a loft section must be a CURVE, and {listing} is not one. "
            "A datum point is accepted by AddSectionToLoft and then fails the "
            "update; a solid Vertex.N is refused by the same call with 'invalid "
            "number of arguments'. The CATIA UI lets you pick a vertex as an end "
            "section - the automation object behind catia_gsd_multi_section_surface "
            "does not (measured on B30 2026-11-03, both orders, both kinds). "
            "Workaround that does build: end the loft on a small closed section "
            "(a circle or closed spline of r = 0.05-0.5 mm) where the point would "
            "have been, then close that end with catia_gsd_fill, or run the "
            "neighbouring cross-section out to the apex and let a ruled loft "
            "carry it there."
        )

    def _answer_tutorial_parameters(self, args: dict[str, Any]) -> None:
        """Turn the tutorial's three loft parameters into the answer they need.

        The 2026-11-10 report called exactly this call:

            {"sections": [...], "normal_to_profile": true, "tangent_factor": 1.5,
             "guide_influence": "To Next Guide", "guides": []}

        and got ``root: must NOT have additional properties`` three times - before
        a single CATIA call, and with nothing said about what the API can do. The
        refusal is correct, so it stays; what changes is that it now names the
        measurement and the route that works. Every claim below was read off
        HybridShapeLoft's OWN type information on B30 2026-11-10, not off the
        factory method's parameter list - which is how the AddNewSpline/SetClosing
        mistake was made in 0.3.97.
        """
        factor = args.get("tangent_factor")
        if factor is not None:
            try:
                value = float(factor)
            except (TypeError, ValueError):
                raise ArgumentRejected(
                    f"tangent_factor must be a number, not {factor!r}.",
                    code="BAD_ARGUMENT", stage="validate") from None
            if abs(value - 1.0) > 1e-9:
                raise ArgumentRejected(
                    f"tangent_factor={value:g} cannot be applied: there is no "
                    "tension member to apply it to. HybridShapeLoft exposes 78 "
                    "members on B30 and not one of them takes a factor, a weight or "
                    "a strength (read from its type information 2026-11-10) - the "
                    "same answer for curvature. 1.0 is accepted because it is the "
                    "only value this API has. What DOES change the shape at a "
                    "section, both measured on B30 the same day: (1) an end tangency "
                    "support - start_tangent/end_tangent take a surface or a DATUM "
                    "PLANE the section lies on, and two circles r=10 and r=14 give "
                    "3030.971 mm2 open, 2869.025 with start_tangent='xy', 3202.807 "
                    "with end_tangent='xy'; (2) an extra SECTION where the bulge "
                    "belongs, which the tutorial itself does by sampling. Nothing "
                    "was created.",
                    code="NO_TANGENT_FACTOR", stage="validate",
                    next_actions=[
                        "Drop tangent_factor and add the section where the bulge "
                        "should be - a section is a control the loft must pass "
                        "through, and it is what 'strength' was standing in for",
                        "Or set the end condition the API does have: "
                        "start_tangent='<surface or datum plane the section lies "
                        "on>'",
                    ])
        if args.get("normal_to_profile") and not (args.get("start_tangent")
                                                  or args.get("end_tangent")):
            raise ArgumentRejected(
                "normal_to_profile was asked for, and there is no member with that "
                "name. The nearest one is an end tangency support: "
                "start_tangent / end_tangent take a surface - or a DATUM PLANE - "
                "that the section lies on, and a plane does work and does change "
                "the shape (measured on B30 2026-11-10: two circles r=10 at z=0 and "
                "r=14 at z=40 give 3030.971 mm2 with no condition, 2869.025 with "
                "start_tangent='xy', 3202.807 with end_tangent='xy'). Read that "
                "literally: the surface leaves TANGENT TO THE PLANE, i.e. flat along "
                "it. 'Normal to profile' in the tutorial means the opposite "
                "direction - perpendicular to the section's own plane - and no "
                "member here says that. Nothing was created.",
                code="NO_NORMAL_TO_PROFILE", stage="validate",
                next_actions=[
                    "Pass start_tangent='xy' (or the plane the section actually "
                    "lies on) if a FLAT departure is what the shape needs",
                    "If the surface has to leave perpendicular to the section, add "
                    "a section right next to it - one short segment leaving at 90 "
                    "degrees is what the loft then interpolates",
                ])
        influence = args.get("guide_influence")
        if influence:
            raise ArgumentRejected(
                f"guide_influence='{influence}' cannot be applied: there is no "
                "per-guide influence member. HybridShapeLoft has AddGuide, "
                "AddGuideWithTangent, GetGuide, GetNbOfGuides and RemoveGuide - and "
                "no influence, coupling or reach setting on any of them (78 members "
                "read from its type information on B30 2026-11-10). 'To Next Guide' "
                "decides how far a guide acts between two sections; the API here "
                "lets a guide act along the whole loft or not at all. Nothing was "
                "created.",
                code="NO_GUIDE_INFLUENCE", stage="validate",
                next_actions=[
                    "Put the effect where it belongs by adding a SECTION: with a "
                    "section at the place the guide should stop acting, the loft "
                    "run to that section is the same shape the influence would have "
                    "produced",
                    "Or move the guide's own control points - the guide pulls the "
                    "surface wherever it passes close to a section",
                ])

    def _multi_section_surface(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        self._answer_tutorial_parameters(args)
        sections = args["sections"]
        guides = args.get("guides", [])
        orientations = args.get("orientations") or [1] * len(sections)
        if len(orientations) != len(sections):
            raise ArgumentRejected(
                f"'orientations' has {len(orientations)} entries but there are "
                f"{len(sections)} sections — lengths must match."
            )
        resolved = self._resolve_sections(sections)
        self._refuse_point_sections(sections, resolved)

        if args.get("ruled"):
            if args.get("start_tangent") or args.get("end_tangent"):
                raise ArgumentRejected(
                    "'ruled' and an end tangency condition cannot be combined: the "
                    "ruled path is built from one blend per section pair, so there is "
                    "no single loft feature to carry the condition. Drop 'ruled' if "
                    "you need tangency; drop the tangency if you need straight sides."
                )
            return self._ruled_loft(sections, args.get("name"),
                                    [reference for _element, reference in resolved])

        loft = self._factory().AddNewLoft()
        for (_element, ref), orientation in zip(resolved, orientations):
            # (section, orientation, closing point). Nothing/None = automatic.
            try:
                loft.AddSectionToLoft(ref, orientation, None)
            except Exception:
                loft.AddSectionToLoft(ref, orientation)
        for guide_name in guides:
            loft.AddGuide(self._ref(guide_name))

        tangency: list[str] = []
        tangency_error = ""
        for attribute, spec, end in (("SetStartSectionTangent", args.get("start_tangent"), "first"),
                                     ("SetEndSectionTangent", args.get("end_tangent"), "last")):
            if not spec:
                continue
            try:
                getattr(self.conn._dynamic(loft), attribute)(self._ref(spec))
            except Exception as exc:  # noqa: BLE001 - reported with the rule that matters
                raise RuntimeError(
                    f"CATIA rejected '{spec}' as the {end} section's tangency support "
                    f"({str(exc).splitlines()[0][:120]}). {attribute} takes ONE "
                    "geometry: a surface or face that the section actually lies on. "
                    "Measured on B30: a cylindrical surface through the first "
                    "section works; a sketch circle and a surface that does not reach "
                    "the section do not."
                ) from exc
            tangency.append(f"{end} section tangent to '{spec}'")
            tangency_error = (
                " A tangency support was set, and CATIA accepts the setter before it "
                "checks the geometry: the support has to TOUCH that section, or the "
                "update fails exactly like this (measured: a cylinder spanning the "
                "start section updates, the same surface attached to the far end "
                "does not).")

        guides_msg = f" with {len(guides)} guide(s)" if guides else ""
        tangent_msg = ("; " + ", ".join(tangency)) if tangency else ""
        try:
            return self._finish(
                loft, args.get("name"),
                f"Multi-sections surface created through {len(sections)} sections"
                f"{guides_msg}{tangent_msg}.",
            )
        except RuntimeError as e:
            # Which call failed, in its own numbers: the report of 2026-11-03
            # pointed out that the same six-line speech came back for every
            # failure and never said what had been attempted.
            kinds: dict[str, int] = {}
            for name, (element, _ref) in zip(sections, resolved):
                kind = self._section_kind(name, element)
                kinds[kind] = kinds.get(kind, 0) + 1
            context = (f"sections: {len(sections)} ("
                       + ", ".join(f"{count} {kind}" for kind, count in sorted(kinds.items()))
                       + f"), guides: {len(guides)}, ruled: false"
                       + (", end tangency set" if tangency else ""))
            # A guide has to INTERSECT EVERY section, and that is measurable, so
            # it is measured instead of being one bullet in a list of guesses.
            # The 2026-11-09 report made five attempts and had to invent a
            # subtraction (2 guides fail, 1 builds) to find that the guides, not
            # the sections, were the problem - while this list led with "fold",
            # which was wrong every time.
            guide_note = ""
            if guides:
                rows: list[tuple[str, float | None]] = []
                for guide_name in guides:
                    try:
                        guide_ref = self._ref(str(guide_name))
                    except Exception:  # noqa: BLE001 - already resolved once
                        continue
                    worst: float | None = None
                    for _name, (_element, section_ref) in zip(sections, resolved):
                        gap = self._cutter_gap(section_ref, guide_ref)
                        if gap is None:
                            continue
                        if worst is None or gap > worst:
                            worst = gap
                    rows.append((str(guide_name), worst))
                measured = ", ".join(
                    f"'{name}' {('unknown' if gap is None else f'{gap:.4f} mm')}"
                    for name, gap in rows)
                missing = [name for name, gap in rows
                           if gap is not None and gap > 0.01]
                if missing:
                    guide_note = (
                        f"MEASURED BEFORE THIS MESSAGE - the farthest any section is "
                        f"from each guide: {measured}. "
                        f"'{missing[0]}' does not reach every section, and a guide "
                        "has to INTERSECT ALL OF THEM: a guide that misses one "
                        "leaves the loft with no correspondence to follow and the "
                        "update fails exactly like this. That is the first thing to "
                        "fix, before anything below. ")
                else:
                    guide_note = (
                        f"MEASURED BEFORE THIS MESSAGE - the farthest any section is "
                        f"from each guide: {measured}. Every guide REACHES every "
                        "section, so 'a guide that misses a section' is NOT what "
                        "this is - read the list below with that excluded. Touching "
                        "is necessary, not sufficient: a guide anchored to a "
                        "SPLINE's control point touches the hull of the section "
                        "while missing the interpolated curve the loft actually "
                        "follows (measured 2026-11-09: three guides anchored to "
                        "control points failed three times, while ONE guide through "
                        "the sections' shared start points built). So if the "
                        "sections alone build, the guide anchors are the problem - "
                        "put them on points the section curve really passes "
                        "through. ")
            raise RuntimeError(
                f"{e}{tangency_error} THIS CALL: {context}. " + guide_note + 
                f"FIRST, and the cause of nearly every failure on a real part: AN "
                f"ARRANGEMENT THAT MAKES THE INTERPOLATED SURFACE FOLD. This loft "
                f"passes THROUGH the sections and stays inside their hull; if one "
                f"section sits past the turn of its two neighbours - the radius "
                f"goes out, back in, then out again, or a section's plane crosses "
                f"another - the surface has to self-intersect and CATIA refuses to "
                f"update. Measured on B30 2026-11-03, three sections r 2.9 / 3.0 / 8 "
                f"with the middle one at z -17.58 and its neighbours at -19 and -25: "
                f"fails as circles, as 12-point CLOSED SPLINES and as 90-point closed "
                f"splines alike, while the SAME three radii in a monotone stack "
                f"build, and so does the outer pair alone. So swapping the curve "
                f"type is not a fix for this - the fix is to keep the sections "
                f"monotone along the lofting direction, to split the loft at the "
                f"turn and join the pieces, or to use ruled=true (which covers all "
                f"sections in one call: 10 sections measured at 1.39 s in the same "
                f"report). A section with NO feature beside one that carries a DEEP "
                f"one is the same fold in different clothes: measured on B32 SP6 "
                f"2026-11-04 on a five-petal base, r=8 was a plain circle (valley and "
                f"foot coincided there) and r=14 already carried an 8.6 mm valley, so "
                f"the loft had to create the whole feature inside one span and "
                f"self-intersected - while the same three sections split into two "
                f"lofts built. Move the first wavy section closer to the circle, or "
                f"split the loft there. Second: a section that is not ONE curve - a polygon "
                f"outline is four curves however you draw it (four catia_sketch_line "
                f"calls, and catia_sketch_rectangle too), and an Extract of it is "
                f"still four, so a polygon section has to become a closed catia_gsd_spline "
                f"through its corners (measured on B30 2026-10-25). "
                f"Third: a guide that does not intersect every section (check with "
                f"measurements). Fourth: a section whose curve direction opposes the "
                f"others - flip it with orientations=-1. A point or vertex section "
                f"is no longer in this list: it is refused before the loft is built "
                f"and says so. If none of the above applies, retry once in a fresh "
                f"session - one B32 SP6 session reported every loft failing while "
                f"catia_gsd_blend accepted the same curve pair (2026-10-23), and two "
                f"other B32 SP6 sessions then built both the smooth and the ruled "
                f"loft and matched B30 to 0.02 mm3 (2026-10-24) - and if it still "
                f"fails, build the transition with two blends."
            ) from e

    def _ruled_loft(self, sections: list[str], name: str | None,
                    refs: list[Any] | None = None) -> str:
        """Straight-sided loft: one blend per section pair, then joined.

        Measured on V5-6R2020 B30, 2026-10-24, three circles r=10/20/10 at
        z=0/50/100 closed into a solid:

            GSM loft (NURBS through the sections)   90059.0 mm3  (+22.86 %)
            two blends joined (this method)         73304.7 mm3  (ratio 1.0000)
            analytic, two frusta                    73303.8 mm3

        which is why the GSM loft cannot reproduce a "loft with no end tangency":
        it interpolates smoothly through the sections and bulges. The blend API
        is the ruled one, and joining the pieces gives exactly the straight-sided
        solid. ``SectionCoupling`` was tried first and has NO effect on volume
        (0/1/2/3 all measured 90059.0) - it changes the correspondence between
        sections, not the interpolation.
        """
        part = self.conn.get_active_part()
        factory = self._factory()
        if refs is None:
            refs = [self._ref(section) for section in sections]
        blends = []
        for i in range(len(refs) - 1):
            blend = factory.AddNewBlend()
            blend.SetCurve(1, refs[i])
            blend.SetCurve(2, refs[i + 1])
            self._get_geoset().AppendHybridShape(blend)
            part.UpdateObject(blend)
            blends.append(blend)
        if len(blends) == 1:
            # TWO sections: the single blend was appended inside the loop, so it
            # must NOT go through _finish - a second AppendHybridShape on an
            # element already in the body fails with "方法 AppendHybridShape 失败",
            # which is what `ruled=true` answered for every two-section loft
            # (reported 2026-10-29, §3: a fan blade from y=40 to y=540). Three or
            # more sections never hit it, because the Join is a new element -
            # that is why the 2026-10-24 smoke test (three circles) passed.
            return self._completed(
                blends[0], name,
                f"Ruled surface created between {len(sections)} sections "
                "(straight sides, not an interpolated loft).",
                self._get_geoset(),
            )
        joined = factory.AddNewJoin(part.CreateReferenceFromObject(blends[0]),
                                   part.CreateReferenceFromObject(blends[1]))
        for blend in blends[2:]:
            joined.AddElement(part.CreateReferenceFromObject(blend))
        return self._finish(
            joined, name,
            f"Ruled surface created through {len(sections)} sections as "
            f"{len(blends)} straight-sided pieces, joined. Measured on B30: "
            "73304.7 mm3 for r=10/20/10 at z=0/50/100 (analytic 73303.8), "
            "against 90059.0 for the smooth loft - +22.86 %.",
        )

    def _sweep(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        profile_ref = self._ref(args["profile"])
        guide_ref = self._ref(args["guide"])
        sweep = self._factory().AddNewSweepExplicit(profile_ref, guide_ref)
        return self._finish(
            sweep, args.get("name"),
            f"Swept surface created: profile '{args['profile']}' along guide '{args['guide']}'.",
        )

    def _curve_length_mm(self, reference: Any) -> float | None:
        """Length of a curve in mm, or None.

        ``Measurable.Length`` gives a curve's perimeter in mm (unlike Area and
        Volume, which are MKS) - the same reading ``_on_curve_note`` uses for a
        normal plane's position. None, never a zero.
        """
        try:
            spa = self.conn.active_document.GetWorkbench("SPAWorkbench")
            return float(spa.GetMeasurable(reference).Length)
        except Exception:  # noqa: BLE001 - an unreadable length is not 0 mm
            return None

    def _body_by_name(self, part: Any, name: str) -> Any:
        for index in range(1, int(part.Bodies.Count) + 1):
            body = part.Bodies.Item(index)
            if str(body.Name) == str(name):
                return body
        names = ", ".join(str(part.Bodies.Item(i).Name)
                          for i in range(1, int(part.Bodies.Count) + 1))
        raise ArgumentRejected(
            f"No body named '{name}'. Top-level bodies: {names or 'none'}.")

    def _engrave_cleanup(self, created: list[str], body_name: str | None,
                         body_was_created: bool = True) -> str:
        """Take back everything an engrave attempt made; say what is ACTUALLY left.

        Two defects of the 2026-11-10 bottle report are fixed here, and both were
        the reply talking about the model without looking at it:

        * it said "Cleanup could not remove body 'CurveEngrave_A' - the tree may
          still hold them" while `catia_list_bodies` showed ONE body: the boolean
          had already consumed the cutter (``tool_body_consumed: true``), so
          deleting it BY NAME failed and the failure was read as "still there".
          A body that is gone cannot be deleted - this now checks before it
          speaks, and reports the bodies that remain.
        * the deletion order took the point out before the plane that references
          it, which is what raises CATIA's modal 警告 (『已移除授权输入
          "参考点"』). `created` is ordered by dependency, so reversing it deletes
          dependents first: tube, profile, plane, point.

        The 2026-11-11 report found the other half of the same defect, and it is
        why this now checks three things instead of one: an engrave refused at
        ``ENGRAVE_CURVE_UNPARAMETRISABLE`` replied "Nothing this call created was
        left behind ... Bodies now: 零件几何体" while the point-on-curve element it
        had added ('OvalOnSkin3D_at0.5') was still in the tree, and
        ``catia_update_part`` named that element as what stops every later update
        - 2/2 reproducible. Only BODIES were checked. So: every name is looked up
        AGAIN after its delete, the body is looked up again too, and the part is
        asked to update - a tree that will not update cannot be called clean even
        when every name is gone.
        """
        from catia_mcp.tools.part_design import PartDesignTools

        pd = PartDesignTools(self.conn)
        part = self.conn.get_active_part()
        consumed = False

        def exists(name: str) -> bool | None:
            """Is it in the tree? None when the part cannot answer."""
            try:
                return part.FindObjectByName(name) is not None
            except Exception:  # noqa: BLE001 - a part that will not be asked
                return None

        if body_name and body_was_created:
            if not self._body_exists(part, body_name):
                # Already consumed by the boolean - nothing to delete, and
                # nothing to report as a failure either. Only a body this call
                # actually made can be described this way: when the refusal
                # happens BEFORE catia_new_body there is no cutter at all, and
                # saying "consumed by the boolean" would explain away an
                # element that is simply absent.
                consumed = True
            else:
                try:
                    pd.execute("catia_delete_body",
                               {"name": body_name, "force": True})
                except Exception:  # noqa: BLE001 - the check below is the answer
                    pass
        for name in reversed(created):
            if exists(name) is False:
                continue                      # it went with the body
            try:
                pd.execute("catia_delete_feature", {"feature_name": name})
            except Exception:  # noqa: BLE001 - the check below is the answer
                pass

        # "I CALLED DELETE" IS NOT "IT IS GONE". Ask again, in the same
        # dependents-first order, before this speaks about the tree at all.
        still: list[str] = []
        unchecked: list[str] = []
        for name in reversed(created):
            present = exists(name)
            if present is None:
                unchecked.append(f"'{name}'")
            elif present:
                still.append(f"'{name}'")
        body_still = bool(body_name) and self._body_exists(part, body_name)

        # AND THE TREE HAS TO STILL UPDATE. A leftover element that cannot update
        # is worse than one that can: CATIA stops every later update at it. The
        # 2026-11-11 report is exactly this pair - "Nothing this call created was
        # left behind" in the same reply that left 'OvalOnSkin3D_at0.5' in the
        # tree, and catia_update_part naming it as the blocker 2/2.
        culprits = self._tree_update_culprits(part)

        bodies = [str(part.Bodies.Item(i).Name)
                  for i in range(1, int(getattr(part.Bodies, "Count", 0)) + 1)]
        body_text = (" Bodies now: " + (", ".join(bodies) if bodies else "none")
                     + ".")

        if (not body_still and not still and not unchecked
                and culprits is None):
            return (" Nothing this call created was left behind"
                    + (" (the cutter body had already been consumed by the "
                       "boolean, so there was nothing to delete)" if consumed else "")
                    + ", and the part still updates." + body_text)

        parts: list[str] = []
        if body_still:
            parts.append(f"The cutter body '{body_name}' is still in the tree")
        if still:
            parts.append("The delete ran but these are STILL in the tree: "
                         + ", ".join(still))
        if unchecked:
            parts.append("These could not be checked - their delete failed and this "
                         "part cannot answer for a name: " + ", ".join(unchecked))
        if culprits:
            from catia_mcp.update_errors import describe

            parts.append("And the part no longer updates: " + describe(culprits)
                         + " - every later update stops there, so delete "
                         + ("it" if len(culprits) == 1 else "them")
                         + " with catia_delete_feature and update again")
        elif culprits == []:
            parts.append("And the part no longer updates, though no single element "
                         "reproduces it on its own, so the tree is not clean either "
                         "way")
        return " " + " ".join(part + "." for part in parts) + body_text

    def _tree_update_culprits(self, part: Any) -> list[str] | None:
        """None when the part updates, else the names that fail on their own.

        ``part.Update()`` is the only thing that can vouch for the tree, and the
        walk behind ``failing_elements`` costs one COM call per element (measured
        2026-11-03: 49.9 s over 91 elements), so it only runs when the update has
        already failed. An empty list means "it fails but no element does on its
        own" - a relation between features, which this cannot name.
        """
        try:
            part.Update()
            return None
        except Exception:  # noqa: BLE001 - the walk below is the answer
            pass
        from catia_mcp.update_errors import failing_elements

        try:
            return failing_elements(part)
        except Exception:  # noqa: BLE001 - unnameable is still "not clean"
            return []

    def _body_exists(self, part: Any, name: str) -> bool:
        try:
            for i in range(1, int(part.Bodies.Count) + 1):
                if str(part.Bodies.Item(i).Name) == str(name):
                    return True
        except Exception:  # noqa: BLE001 - unreadable is not "gone"
            return True
        return False

    def _surface_engrave(self, args: dict[str, Any]) -> str:
        """A constant-depth groove along a curve that lies ON a surface.

        THE ROUTE THREE 2026-11-10 REPORTS COULD NOT FIND. They needed "cut the
        letter into the curved face" and every path via the surface was refused
        (split of a projected curve, fill of it, Sew/Wrap never update), so 0.3.95
        declined to wrap an engrave tool at all. That was right about the SURFACE
        route and wrong about the capability: measured on B30 2026-11-10, this
        chain runs end to end with tools that already existed -

            plane normal to the curve -> circle of radius = depth on it
            -> sweep that circle along the curve (a tube) -> CloseSurface
            -> catia_boolean(remove)

        and the numbers say why it works: the tube is CENTRED on a curve that lies
        ON the surface, so exactly half of it is inside the material. A r=4 trace
        on a r=40 cylinder produced a 5.9657 mm3 tube (2*pi^2*R*r^2 = 5.97) and
        removed 2.974 mm3 - the half. The depth is therefore the RADIUS, measured
        along the surface normal everywhere, which is the thing a planar pocket
        cannot do (its depth follows an axis).
        """
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        curve = args.get("curve")
        if not curve:
            raise ArgumentRejected(
                "catia_surface_engrave needs 'curve': the element that lies ON the "
                "surface - normally the one catia_gsd_project produced. Nothing was "
                "created.", code="CURVE_REQUIRED", stage="validate")
        curve = str(curve)
        try:
            depth = float(args["depth"])
        except (TypeError, ValueError):
            raise ArgumentRejected(
                "catia_surface_engrave needs 'depth': the groove depth in mm, "
                "which is also the sweep radius. Got "
                f"{args.get('depth')!r}.")
        if depth <= 0:
            raise ArgumentOutOfRange(
                f"depth must be positive; got {depth}. A groove of depth 0 would "
                "remove nothing.", code="DEPTH_OUT_OF_RANGE", stage="validate")
        position = float(args.get("position", 0.5))
        if not 0.0 < position < 1.0:
            raise ArgumentOutOfRange(
                "position is a FRACTION of the curve's length (0.5 = its middle) "
                f"and must lie strictly between 0 and 1; got {position}. It only "
                "places the plane that orients the profile.",
                code="POSITION_OUT_OF_RANGE", stage="validate")

        target_name = args.get("target_body")
        target = (self._body_by_name(part, str(target_name)) if target_name
                  else self.conn.get_active_part_body())
        curve_ref = self._ref(curve)
        length = self._curve_length_mm(curve_ref)

        base = str(args.get("name") or f"Engrave_{curve}")
        taken = {str(part.Bodies.Item(i).Name)
                 for i in range(1, int(part.Bodies.Count) + 1)}
        body_name = base
        suffix = 2
        while body_name in taken:
            body_name = f"{base}.{suffix}"
            suffix += 1

        created: list[str] = []
        # Whether a cutter body was ever made. The refusal can happen earlier (a
        # curve that cannot carry a point), and then there is nothing for the
        # boolean to have consumed - the cleanup must not say there was.
        body_created = False
        # THE POINT COMES BEFORE THE PLANE IN THIS LIST, and the list is deleted
        # in reverse. The plane holds the point as an input, so removing the point
        # first makes CATIA raise a modal 警告 ('已移除授权输入 "参考点"') - which is
        # exactly what the 2026-11-10 bottle report photographed. Reverse order is
        # dependency order: tube, profile, plane, point.
        tube_facts = ""
        plane_name = f"{base}_N"
        profile_name = f"{base}_P"
        tube_name = f"{base}_T"
        try:
            try:
                plane_reply = self._plane_normal_to_curve(
                    {"curve": curve, "position": position, "name": plane_name})
            except CodedError as error:
                # WHICH CURVES CAN CARRY A POINT-ON-CURVE PARAMETER, measured on
                # B30 2026-11-11 one fresh document per case (a failed update is
                # document-wide, so eleven cases in one part measured the first
                # failure eleven times). WORK: gsd_line, extract of a gsd_line,
                # an OPEN gsd_spline, and a SKETCH outline projected onto a face.
                # REFUSE: gsd_circle, its extract, a CLOSED gsd_spline, and the
                # projection of any of those - including a closed spline's, so
                # "it came from catia_gsd_project" is NOT the test. The 0.3.100
                # message said the projection was what mattered; the 2026-11-11
                # report followed that advice and got refused, so it is written
                # as the matrix now.
                if getattr(error, "code", "") != "POINT_ON_CURVE_FAILED":
                    raise
                modified = bool(getattr(error, "modified", False))
                stranded = re.search(r"already added \('([^']+)'\)", str(error))
                raise PreconditionRejected(
                    f"'{curve}' cannot carry a point-on-curve parameter, which this "
                    "route needs: the profile plane is built normal to the curve at "
                    "that point. Which curves do, measured on B30 2026-11-11 one "
                    "fresh document per case: a catia_gsd_line yes, the extract of "
                    "one yes, an OPEN 3D spline yes, and a SKETCH outline projected "
                    "onto the face yes. A catia_gsd_circle no, its extract no, a "
                    "CLOSED spline no, and the projection of any of those onto a "
                    "face no - including a closed spline's projection, so 'it came "
                    "from catia_gsd_project' is not what decides. What the working "
                    "ones share is an OPEN curve, or a sketch outline projected "
                    "onto the surface."
                    + (f" The point this call added ('{stranded.group(1)}') could "
                       "not be removed - delete it with catia_delete_feature "
                       "before updating the part." if modified and stranded else
                       "" if not modified else
                       " A point this call added may still be in the tree - check "
                       "before the next update."),
                    code="ENGRAVE_CURVE_UNPARAMETRISABLE", stage="execute",
                    modified=modified,
                    next_actions=[
                        "Draw the outline in a SKETCH and project THAT onto the "
                        "face: a projected sketch outline carries the parameter "
                        "(measured 2026-11-11) and is the route this tool is built "
                        "for",
                        "Or engrave along a catia_gsd_line / an OPEN catia_gsd_spline: "
                        "both carry it. A CLOSED curve does not - that includes "
                        "catia_gsd_circle and catia_gsd_spline(closed=true), and "
                        "their projections",
                    ]) from error
            # The point the plane tool made is named after the curve and the
            # position, and it is the only thing the profile can be centred on
            # that is guaranteed to lie ON the curve.
            origin = re.search(r"origin '([^']+)'", plane_reply or "")
            centre = origin.group(1) if origin else f"{curve}_at{position:g}"
            created.append(centre)
            created.append(plane_name)
            self._circle({"center": centre, "support_plane": plane_name,
                          "radius": depth, "name": profile_name})
            created.append(profile_name)
            self._sweep({"profile": profile_name, "guide": curve,
                         "name": tube_name})
            created.append(tube_name)

            from catia_mcp.tools.part_design import PartDesignTools

            pd = PartDesignTools(self.conn)
            pd.execute("catia_new_body", {"name": body_name})
            body_created = True
            try:
                self._close_surface({"surface": tube_name})
            except Exception as error:  # noqa: BLE001 - this is the usual break
                raise PreconditionRejected(
                    f"The sweep along '{curve}' produced a surface that CloseSurface "
                    f"will not close ({str(error).splitlines()[0][:120]}). OPEN "
                    "guides are normally FINE - CloseSurface closes the two ends of "
                    "the pipe by itself, measured on B30 2026-11-11 with a 50 mm "
                    "segment (19.5829 mm3 removed) and a 65.682 mm open spline "
                    "(25.6436 mm3) - so this is about the surface the sweep "
                    "actually made, not about the curve being open. An earlier "
                    "version of this message blamed open guides and was wrong. "
                    "Check that the sweep produced a real pipe (its area, or its "
                    "distance to the body) before retrying.",
                    code="ENGRAVE_TUBE_NOT_CLOSED", stage="execute",
                    modified=False) from error

            # MEASURE THE CUTTER BEFORE THE BOOLEAN EATS IT. catia_boolean
            # consumes the tool body, so once it reports NO_MATERIAL the tube is
            # gone and its numbers are unrecoverable - which is why the report got
            # "shares no VOLUME" and nothing else. Three numbers make the failure
            # actionable: how big the tube is, how far it is from the material,
            # and over what length it was swept.
            # The measurement must never become the failure: if any of it cannot
            # be read, the boolean's own code is what the caller needs to see.
            try:
                tube_body = self._body_by_name(part, body_name)
                tube_volume = pd._body_volume_mm3(part, tube_body)
                tube_gap = self._cutter_gap(
                    part.CreateReferenceFromObject(tube_body),
                    part.CreateReferenceFromObject(target))
            except Exception:  # noqa: BLE001 - diagnostics, not the operation
                tube_volume, tube_gap = None, None
            tube_facts = (
                " The cutter was measured before it was consumed:"
                + (f" a {tube_volume:.4f} mm3 tube" if tube_volume is not None
                   else " a tube whose volume could not be read")
                + (f" swept over {length:.3f} mm of curve" if length else "")
                + (f", its body {tube_gap:.4f} mm from '{target.Name}' at the "
                   "closest point" if tube_gap is not None else
                   ", and its distance to '" + str(target.Name) + "' could not be "
                   "measured")
                + (". A tube that touches at 0.0000 mm and still removes nothing is "
                   "GRAZING the body, not buried in it: the curve it swept along "
                   "meets the body at a point or two instead of running through "
                   "it. Compare the swept length above with the length of the "
                   "element you projected - a curve that is a small fraction of it "
                   "is a fragment that merely touches, which is what a projection "
                   "onto a curved face can produce (measured on a lofted bottle "
                   "face 2026-11-10: 4.000 mm of a 63.947 mm outline)"
                   if tube_gap is not None and tube_gap < 0.05 else "."))
            pd.execute("catia_set_active_body", {"name": str(target.Name)})
            before = pd._body_volume_mm3(part, target)
            pd.execute("catia_boolean", {"operation": "remove",
                                         "tool_body": body_name,
                                         "target_body": str(target.Name)})
            after = pd._body_volume_mm3(part, target)
        except Exception as error:
            note = self._engrave_cleanup(created, body_name, body_created)
            # Keep the specific code the boolean or a GSD step already chose -
            # TOOL_DOES_NOT_TOUCH ("'FarEngrave' is 200.0579 mm away") is a much
            # better answer than a wrapper's ENGRAVE_FAILED. FeatureRejected is
            # NOT a CodedError subclass (it carries .code without inheriting it),
            # so the test is on the attribute, not the class.
            if getattr(error, "code", None):
                error.args = (str(error) + tube_facts + note,)
                raise
            raise PreconditionRejected(
                f"Engraving along '{curve}' failed: "
                f"{str(error).splitlines()[0][:160]}.{tube_facts}{note}",
                code="ENGRAVE_FAILED", stage="execute", modified=False,
                next_actions=[
                    "Check that the curve really lies ON the target body - a "
                    "curve that floats above it removes nothing, and one that "
                    "misses it entirely cannot be swept into a cutter",
                    "Build the curve with catia_gsd_project onto the surface "
                    "first; it is what makes the depth follow the normal",
                ],
            ) from error

        removed = (before - after) if (before is not None and after is not None) else None
        expected = (length * math.pi * depth * depth / 2.0) if length else None
        body_line = (f"Removed {removed:.3f} mm3" if removed is not None
                     else "Removed an amount that could not be measured")
        if removed is not None and expected:
            body_line += (f" (a {depth:g} mm tube centred on {length:.3f} mm of curve "
                          f"holds half of itself in the material: {expected:.3f} mm3)")
        verdict = ""
        # No "removed nothing" branch here: catia_boolean(remove) already measures
        # the body either side of itself and refuses with NO_MATERIAL, so anything
        # reaching this line took material off. A second check would be dead code
        # (0.3.96 removed one of those rather than keeping it as decoration).
        if removed is not None and expected and removed < 0.25 * expected:
            verdict = (
                " WARNING: that is less than a quarter of what the geometry "
                "predicts, so the curve probably does not follow this surface along "
                "its whole length."
            )
        length_text = (f"{length:.3f} mm of it" if length is not None
                       else "it")
        return (
            f"Engraved along '{curve}' into '{target.Name}': a {depth:g} mm deep "
            f"groove over {length_text}. {body_line}.{verdict} The tube is centred "
            "on the curve, which lies on the surface, so HALF of it is inside the "
            f"material and the depth is the radius - {depth:g} mm along the surface "
            "normal everywhere, which is what a planar pocket cannot do. This is a "
            "groove ALONG the curve, not a filled glyph: for a letter, pass its "
            "OUTLINE (catia_sketch_strokes draws one in a call) - not its middle."
        )

    def _extrude(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        factory = self._factory()
        profile_ref = self._ref(args["profile"])
        dx, dy, dz = args["direction"]
        limit1 = args["limit1"]
        limit2 = args.get("limit2", 0)

        direction = factory.AddNewDirectionByCoord(dx, dy, dz)
        extrude = factory.AddNewExtrude(profile_ref, limit1, limit2, direction)
        return self._finish(
            extrude, args.get("name"),
            f"Extruded surface created: {limit1} mm along [{dx}, {dy}, {dz}].",
        )

    def _revolve(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        profile_ref = self._ref(args["profile"])
        # The axis takes the same 'x'/'y'/'z' shorthand rotate does. 0.3.60 gave
        # the shorthand to rotate only, and a caller quoting "axis: 'z'" at
        # revolve got "Element 'z' not found ... searched 12 tree name(s)" - a
        # message that reads like a data problem instead of a parameter one
        # (reported 2026-11-04).
        axis_ref, axis_note = self._axis_ref(args["axis"], str(args["profile"]))
        angle1 = args.get("angle1", 360)
        angle2 = args.get("angle2", 0)
        revol = self._factory().AddNewRevol(profile_ref, angle1, angle2, axis_ref)
        total = angle1 + angle2
        return self._finish(
            revol, args.get("name"),
            f"Revolution surface created: {total:g}° swept about '{args['axis']}' "
            f"(angle1={angle1:g}° toward one side of the profile plane, "
            f"angle2={angle2:g}° toward the other; total is their sum).{axis_note}",
        )

    def _fill(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        boundaries = args["boundaries"]
        fill = self._factory().AddNewFill()
        for boundary_name in boundaries:
            # 'Edge.N' means "use this edge as the boundary": a raw topological
            # edge is not a curve (Fill.AddBound answers E_INVALIDARG), so it is
            # extracted first - the same route _topology_reference already takes
            # for loft sections. On a surface in a geometrical set that search
            # walks the SET, not the body (0.3.85); before that, every Edge.N
            # against a GSD surface answered "the active body has no solid shape
            # yet", which was true and useless.
            if self._TOPO_NAME.fullmatch(str(boundary_name).strip()):
                fill.AddBound(self._topology_reference(str(boundary_name)))
            else:
                fill.AddBound(self._ref(boundary_name))
        try:
            message = self._finish(
                fill, args.get("name"),
                f"Fill surface created from {len(boundaries)} boundary curve(s).",
            )
        except RuntimeError as error:
            raise PreconditionRejected(
                f"{error} A fill needs CLOSED boundaries that meet each other: one "
                "wire for a single loop, or several curves that actually share "
                "endpoints (the two ends of a loft plus its section rings, for "
                "instance). Boundaries that only come close do not bind. If the "
                "boundary came from catia_gsd_boundary on a surface that has a "
                "hole, it carries BOTH loops in one curve and this fill will "
                "cover the whole face - cut the surface with the curve you want "
                "as the border instead (catia_gsd_split), then fill that curve. "
                "If they do "
                "meet and this still fails, it is session-sensitive like the loft: "
                "retry once in a fresh session, and if it still fails build the cap "
                "as a swept-and-thickened section instead.",
                code="FILL_NOT_CLOSED", stage="execute", modified=False,
            ) from error

        # MEASURE WHAT CAME BACK. Before 0.3.87 this returned "Fill surface
        # created" for a 25.0000 mm open straight line, while the same call with
        # "Edge.1" was refused with "needs CLOSED boundaries" - two routes, two
        # answers, and the silent one put a zero-area face in the tree that only
        # showed up when a later thickening produced non-manifold edges (reported
        # 2026-11-09, twice). The area is the number that separates the two
        # cases, so it is measured, published, and a degenerate result is taken
        # back out instead of being handed over.
        area = self._shape_area_mm2(fill)
        if area is None:
            return message
        if area < self._FILL_MIN_AREA_MM2:
            self._drop_element(fill)
            raise PreconditionRejected(
                f"The fill was built and measured {area:.6f} mm2, which is a "
                "degenerate face: the boundaries do not close a region. Nothing "
                "was left in the tree - the fill this call created has been "
                "deleted again. The usual cause is a boundary that is an OPEN "
                "piece rather than a loop: catia_gsd_boundary(edge=N) can return "
                "one SEGMENT of a border (measured 2026-11-09: 25.0000 mm for one "
                "radial side of a 91.1450 mm four-sided border), and filling that "
                "segment does not fail on this build - it makes this face. Check "
                "the boundary is a single closed wire, or pass every segment of "
                "the loop in order, then retry.",
                code="FILL_DEGENERATE", stage="execute", modified=False,
            )
        return (message + f" Its area measures {area:.4f} mm2, which is what says "
                "the boundary really closed a region.")

    def _blend(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        blend = self._factory().AddNewBlend()
        blend.SetCurve(1, self._ref(args["curve1"]))
        blend.SetCurve(2, self._ref(args["curve2"]))
        return self._finish(
            blend, args.get("name"),
            f"Blend surface created between '{args['curve1']}' and '{args['curve2']}'.",
        )

    def _offset_surface(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        surface_ref = self._ref(args["surface"])
        offset = args["offset"]
        reverse = args.get("reverse", False)
        # AddNewOffset(surface, value, orientation, repetition)
        offset_surf = self._factory().AddNewOffset(surface_ref, offset, reverse, 0)
        reply = self._finish(
            offset_surf, args.get("name"),
            f"Offset surface created: {offset} mm from '{args['surface']}'.",
        )
        return reply + self._offset_side_note(part, surface_ref, offset_surf,
                                              reverse)

    def _offset_side_note(self, part: Any, surface_ref: Any, offset_surf: Any,
                          reverse: bool) -> str:
        """What the offset's side can and cannot be read from here.

        The 2026-11-10 report offset a bottle skin by 0.4 mm and could not tell
        inside from outside: the reply said only "0.4 mm from 'BottleSkin'", and
        `reverse` is CATIA's orientation flag, not a word for a direction.

        THE OBVIOUS ANSWERS DO NOT WORK, and both were measured on B30 2026-11-10
        with this exact code before being rejected:

          * ``SPAWorkbench.GetMeasurable(<reference to a hybrid surface>)``
            resolves to the enclosing SOLID. A 100x100 face extract reports
            Area 24000 mm2 - the whole 100x100x10 block - and its COG is the
            block's (0, 0, 5), so comparing the two centroids measures the same
            block twice and answers 0.0000 mm.
          * the distance between the surface and EITHER of its two offsets is
            0.3999999 mm, so a distance cannot separate the two directions.

        What is left is the flag that was used, the distance that shows the
        offset landed where it was asked, and the operation that answers the
        question by consequence: cut toward it and read the volume.
        """
        flag = ("reverse=false (CATIA orientation flag 0)" if not reverse
                else "reverse=true (CATIA orientation flag 1)")
        gap = None
        try:
            gap = self._cutter_gap(part.CreateReferenceFromObject(offset_surf),
                                   surface_ref)
        except Exception:  # noqa: BLE001 - the note degrades, the call succeeded
            gap = None
        distance = (
            f"The two surfaces measure {gap:.4f} mm apart, so the offset landed "
            "where it was asked to. " if gap is not None else
            "The distance between the two surfaces could not be measured here. "
        )
        return (
            f" Orientation used: {flag}. {distance}"
            "WHICH SIDE that is cannot be read from here, and the two obvious "
            "measurements were tried and rejected on B30 (2026-11-10): a measurable "
            "taken on a hybrid surface resolves to the enclosing SOLID (a 100x100 "
            "face extract reports the whole block's area and centroid), and both "
            "offset directions sit 0.4000 mm from the original, so a distance "
            "cannot tell them apart. To settle the side, cut toward it and read the "
            "volume - catia_boolean(remove) reports before/after, and a tool body "
            "built on this surface that removes nothing went the wrong way, so pass "
            "reverse=true and repeat."
        )

    # ────────────────────────────────────────────────────────────
    # Operations
    # ────────────────────────────────────────────────────────────

    def _join(self, args: dict[str, Any]) -> str:
        # 'elements' everywhere else is 'sections' / 'boundaries' / 'edges'; the
        # bare KeyError this used to raise said none of that (reported
        # 2026-10-23). Accept 'surfaces' as a synonym and explain the shape.
        # Arguments are validated before CATIA is even touched.
        elements = args.get("elements") or args.get("surfaces") or args.get("curves")
        if not elements:
            raise ArgumentRejected(
                "catia_gsd_join needs 'elements': the list of curves/surfaces to "
                "join, in order, e.g. {\"elements\": [\"C1\", \"C2\", \"Shell\"]}. "
                "('surfaces' is accepted as a synonym.)"
            )
        if not isinstance(elements, list) or len(elements) < 2:
            raise ArgumentRejected(
                "catia_gsd_join needs at least two elements; got "
                f"{elements!r}. Joining one element is a no-op in CATIA."
            )
        self.conn.ensure_connected()
        refs = [self._ref(name) for name in elements]

        join = self._factory().AddNewJoin(refs[0], refs[1])
        for ref in refs[2:]:
            join.AddElement(ref)

        try:
            return self._finish(join, args.get("name"),
                                f"Join created from {len(elements)} elements.")
        except RuntimeError as error:
            # The 0.3.49 acceptance report was blunt about this one: the rule
            # ("join needs a shared edge; a split takes it away") had been written
            # into the docstring, where the caller could not read it at the moment
            # it mattered - the failure was 167 characters of raw COM. Measured on
            # B30 2026-11-03: two coaxial surfaces sharing the r10 circle at z=20
            # join fine untouched; split the lower one at z=10 and join the kept
            # piece with the upper one and the update fails exactly like this.
            listed = []
            for name in elements:
                try:
                    type_name = self._com_type_name(self._find_element(name))
                except Exception:  # noqa: BLE001 - a name that will not resolve
                    type_name = None
                listed.append(f"'{name}'" + (f" ({type_name})" if type_name else ""))
            raise RuntimeError(
                f"{error} Joined: {', '.join(listed)}. "
                "A join needs a SHARED EDGE between consecutive elements, and this "
                "failure is what 'there is none' looks like - CATIA reports nothing "
                "more specific. The usual cause, measured on B30 2026-11-03: one of "
                "these was SPLIT away from the edge it used to share. Two coaxial "
                "surfaces meeting on an r10 circle join while untouched; split the "
                "lower one at z=10 and join the kept piece with the upper one, and "
                "the update fails with this same message. Two more shapes that do "
                "NOT join: surfaces that merely touch along circles of different "
                "radii (r10 against r12 at the same z), and a piece whose boundary "
                "is open on the side you expect to meet. Fix by JOINING FIRST and "
                "SPLITTING AFTERWARDS, by re-cutting both sides on the same plane, or "
                "by using catia_gsd_trim, which needs an intersection rather than a "
                "shared edge."
            ) from error

    def _cutter_gap(self, elem_ref: Any, cutter_ref: Any) -> float | None:
        """Closest approach between a cutter and the element, in mm.

        One number, two uses. Before the split it separates "this cutter never
        reaches the element" from "it reaches it and the update still fails" -
        CATIA answers both with a bare ``方法 UpdateObject 失败``, which is what
        the 2026-11-09 report could not get past. After a failed update it is
        still readable: measured the same day, once an update fails EVERY new
        point in that document refuses to update too, so sampling the cutter
        afterwards is impossible and this is what is left.

        Returns None when it cannot be measured (a datum plane of a workbench
        that is not loaded), never a zero.
        """
        try:
            spa = self.conn.active_document.GetWorkbench("SPAWorkbench")
            return float(spa.GetMeasurable(cutter_ref).GetMinimumDistance(elem_ref))
        except Exception:  # noqa: BLE001 - an unmeasurable pair is not a zero
            return None

    def _split(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        elem_ref = self._ref(args["element"])
        cutter_ref = self._ref(args["cutter"])
        orientation = -1 if args.get("reverse", False) else 1
        # A cutter that does not reach the element cannot cut it, and this build
        # says so with the same generic "update failed" it uses for everything
        # else. Settle that case BEFORE building anything (2026-11-09 report: a
        # circle 3 mm above a patch produced only the generic error; the distance
        # measured 3.0000 mm, which is the whole diagnosis).
        gap = self._cutter_gap(elem_ref, cutter_ref)
        if gap is not None and gap > 0.01:
            raise PreconditionRejected(
                f"'{args['cutter']}' is {gap:.4f} mm from '{args['element']}' at "
                "its closest point, so it cannot cut it - a split needs the "
                "cutter to reach the element. Nothing was created. Two cases "
                "produce this: a curve drawn where the surface is not (a circle "
                "on a plane the surface never passes through), or a plane offset "
                "past the geometry. Move the cutter until this distance is "
                "0.0000 mm - catia_measure_distance reports the same number.",
                code="SPLIT_CUTTER_MISSES", stage="validate", modified=False,
                next_actions=[
                    "Move the cutter onto the element until this distance is "
                    "0.0000 mm - catia_measure_distance reports the same number, so "
                    "iterate there instead of guessing",
                    "A curve cutter has to be BUILT on the surface (a circle whose "
                    "support plane the surface passes through, or a sketch on the "
                    "face itself); a plane cutter has to cross the element",
                ],
            )
        split = self._factory().AddNewHybridSplit(elem_ref, cutter_ref, orientation)
        # Which side survived is the one thing a caller has to know and the one
        # thing the old reply never said: a plain split of a 133 mm bottle at
        # z=100 returned 742.80 mm3 instead of 9488.98 and looked like a broken
        # model (reported 2026-11-03, P1-3). Measured on B30 the same day with a
        # cylinder r10 z 0..40 cut at z=10: orientation +1 keeps z 10..40, i.e.
        # the side the cutter's normal points to, and -1 keeps 0..10. The STL of
        # the result is what answered it - Measurable.Area reads 0.0 for a
        # surface, so it cannot.
        side = ("the side the cutter's normal points to (orientation +1)"
                if orientation == 1 else
                "the side the cutter's normal points away from (orientation -1)")
        try:
            return self._finish(
                split, args.get("name"),
                f"Split of '{args['element']}' by '{args['cutter']}' created, "
                f"keeping {side}; reverse=true swaps the two.",
            )
        except Exception as error:  # noqa: BLE001 - the diagnostic is the point
            after = self._cutter_gap(elem_ref, cutter_ref)
            measured = (f"{after:.4f} mm" if after is not None
                        else "not measurable after the failure")
            # THE NUMBER THAT USED TO LEAD THIS MESSAGE WAS A MINIMUM (0.3.95).
            # A 2026-11-10 report with three cutters (a projected curve, its
            # extract, and a projected circle) got "closest approach 0.0000 mm"
            # three times and spent three controlled experiments rebuilding a
            # cutter that was already lying on the surface by construction - the
            # projected curve touches the surface, and the minimum is 0 whether
            # or not the whole contour follows it. A minimum cannot answer the
            # question. The maximum deviation along the contour would, and there
            # is no way to sample a curve on this build (AddNewPointOnCurveFromPercent
            # does not update - measured 2026-11-09), so this message now says
            # what the number can and cannot mean instead of implying it decides.
            raise PreconditionRejected(
                f"CATIA rejected the split of '{args['element']}' by "
                f"'{args['cutter']}' at update, and the attempt was removed from "
                f"the tree again. The closest approach between the two measures "
                f"{measured} AFTER the refusal - and that is a MINIMUM, so it is 0 "
                "for a curve that merely touches the element in one place. It does "
                "NOT prove the contour follows the element, and this build offers "
                "no way to sample along a curve to find out. What the measurements "
                "do separate: "
                "a closed curve lying on a PLANE patch splits cleanly (1486.903 "
                "mm2 kept, 2026-11-09); the same circle 3 mm above that patch "
                "fails, and a circle over a five-lobe loft fails, and on 2026-11-10 "
                "a curve projected onto a NON-PLANAR surface failed THREE ways - "
                "the projection itself, an extract of it, and a projected circle. "
                "So: if the cutter came from catia_gsd_project onto a curved "
                "surface, this is the measured-unsupported combination on B30 and "
                "no argument will turn it into a split. "
                "What works instead: (a) split a PLANAR patch; (b) take the piece "
                "BEFORE a join or an array (the same limit catia_gsd_boundary"
                "(edge=N) reports); (c) to cap a hole whose centre is not flat, "
                "catia_gsd_circ_pattern(cap_center=true, cap_curve=<the hole's own "
                "edge>) - it caps with the hole's OWN border and needs no coplanar "
                "disc; (d) to remove material inside a curve on a curved surface, a "
                "solid tool body through catia_boolean(remove) - with the caveat "
                "that its depth follows an axis, not the surface normal. "
                "Nothing this call created was left behind. One more measurement: "
                "after a failed update even an unrelated point refuses to update in "
                "this document, so do not read a second failure as a second cause.",
                code="SPLIT_FAILED", stage="execute", modified=False,
                next_actions=list(_SPLIT_FAILURE_ACTIONS),
            ) from error

    def _trim(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        ref1 = self._ref(args["element1"])
        ref2 = self._ref(args["element2"])
        orient1 = -1 if args.get("reverse1", False) else 1
        orient2 = -1 if args.get("reverse2", False) else 1
        trim = self._factory().AddNewHybridTrim(ref1, orient1, ref2, orient2)
        return self._finish(
            trim, args.get("name"),
            f"Trim of '{args['element1']}' with '{args['element2']}' created.",
        )

    def _symmetry(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        elem_ref = self._ref(args["element"])
        plane_ref = self._ref(args["plane"])
        symmetry = self._factory().AddNewSymmetry(elem_ref, plane_ref)
        return self._finish(
            symmetry, args.get("name"),
            f"Symmetry of '{args['element']}' about '{args['plane']}' created.",
        )

    # ────────────────────────────────────────────────────────────
    # Transforms — the primitive a GSD pattern is built from
    # ────────────────────────────────────────────────────────────

    _ORIGIN_AXIS = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0),
                    "z": (0.0, 0.0, 1.0)}

    def _axis_ref(self, spec: Any, owner: str) -> tuple[Any, str]:
        """Reference to a rotation axis, and a note if one had to be built.

        ``AddNewRotate`` takes a REFERENCE to a line here — the opposite of
        ``AddNewTranslate``'s direction argument, which takes the Direction
        OBJECT (passing a Reference there raises "type mismatch, argument 2";
        measured on B30 2026-11-04, both in one script). Two members of the same
        family, two different argument kinds, so neither is guessed.

        'x'/'y'/'z' become a LINE from the origin, because an axis needs a
        position and a direction, and a direction alone cannot say where the
        origin is. Built on demand and named after the element it rotates, so a
        five-fold pattern leaves five readable axes rather than five "直线.37".
        """
        key = str(spec).strip().lower()
        vector = self._ORIGIN_AXIS.get(key)
        if vector is None:
            return self._ref(str(spec)), ""

        part = self.conn.get_active_part()
        wanted = f"{owner}_axis_{key}"
        wanted, _ = self._free_name(part, wanted)
        origin = self._point_ref([0.0, 0.0, 0.0])
        tip = self._point_ref(list(vector))
        line = self._factory().AddNewLinePtPt(origin, tip)
        self._get_geoset().AppendHybridShape(line)
        line.Name = wanted
        part.UpdateObject(line)
        return (part.CreateReferenceFromObject(line),
                f" The origin {key}-axis was built as line '{wanted}'.")

    def _direction_obj(self, spec: Any, owner: str) -> tuple[Any, str]:
        """A Direction OBJECT for ``AddNewTranslate``, plus a note when built.

        The object has to be in the tree: a translate that references a
        tree-less direction is a dependency nobody can see or select, and the
        same 2026-11-03 lesson that made splines keep their points in the part
        applies here.
        """
        factory = self._factory()
        vector: tuple[float, float, float] | None = None
        label = ""
        if isinstance(spec, (list, tuple)):
            if len(spec) != 3:
                raise ArgumentRejected(
                    "catia_gsd_translate's direction needs exactly 3 numbers "
                    f"[x, y, z]; got {len(spec)}."
                )
            vector = (float(spec[0]), float(spec[1]), float(spec[2]))
            label = "[" + ", ".join(f"{value:g}" for value in vector) + "]"
        else:
            key = str(spec).strip().lower()
            vector = self._ORIGIN_AXIS.get(key)
            if vector is not None:
                label = f"the origin {key} direction"
            else:
                from_line = factory.AddNewDirection(self._ref(str(spec)))
                return self._adopt_direction(from_line, owner, f"the direction of '{spec}'")

        if not any(abs(value) > 1e-9 for value in vector):
            raise ArgumentRejected(
                "catia_gsd_translate's direction is the zero vector, so there is "
                "no direction to move along."
            )
        direction = factory.AddNewDirectionByCoord(vector[0], vector[1], vector[2])
        return self._adopt_direction(direction, owner, label)

    def _adopt_direction(self, direction: Any, owner: str,
                         label: str) -> tuple[Any, str]:
        """Put a Direction in the geometrical set so the translate can cite it."""
        part = self.conn.get_active_part()
        wanted, _ = self._free_name(part, f"{owner}_dir")
        self._get_geoset().AppendHybridShape(direction)
        direction.Name = wanted
        part.UpdateObject(direction)
        return direction, f" Direction element '{wanted}' ({label}) was created for it."

    def _rotate(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        element = args["element"]
        angle = float(args["angle"])
        axis_ref, axis_note = self._axis_ref(args["axis"], element)
        rotate = self._factory().AddNewRotate(self._ref(element), axis_ref, angle)
        return self._finish(
            rotate, args.get("name"),
            f"'{element}' copied and rotated {angle:g}° about '{args['axis']}'."
            f"{axis_note}",
        )

    def _circ_pattern(self, args: dict[str, Any]) -> str:
        """Repeat an element N times around an axis and join the copies.

        This is rotate+join behind one call, not a CATIA GSD pattern. Two
        separate facts, kept separate because conflating them is what made the
        0.3.82 description wrong:

          * HybridShapeFactory declares no pattern member: 160 members in all,
            131 of them AddNew*, none with "Pattern" in its name (B30
            2026-11-08).
          * ShapeFactory DOES declare AddNewGSDCircPattern (14 arguments), and
            an object comes back - but part.UpdateObject refuses it in all seven
            call shapes tried (iType 0/1/2, completeCrown on and off, a GSD
            surface and a Part Design pad as the source, the body as the in-work
            object; B30 2026-11-09, scripts/b30_v0384_pattern.py and _final.py).

        So a single pattern node is declared and not callable here. The reply
        the caller gets says exactly that instead of claiming the member does
        not exist - the 2026-11-09 report was right that "not in A" does not
        mean "not anywhere".
        """
        self.conn.ensure_connected()
        element = args["element"]
        count = int(args["count"])
        total = float(args.get("total_angle", 360.0))
        if count < 2:
            raise ArgumentOutOfRange(
                f"count must be at least 2 - one instance is the original alone, "
                f"and nothing would be joined. Got {count}.",
                code="PATTERN_COUNT_OUT_OF_RANGE", stage="validate")
        if not 0 < abs(total) <= 360:
            raise ArgumentOutOfRange(
                "total_angle must be greater than 0 and at most 360 degrees "
                f"(got {total}). For a five-lobe base pass total_angle=360, "
                "count=5 - the step is derived, not passed.",
                code="PATTERN_ANGLE_OUT_OF_RANGE", stage="validate")
        # Resolve before anything is built: a typo must not leave four rotations.
        self._ref(element)
        # cap_center is validated HERE, not in the capping code: by the time the
        # rotations exist, refusing costs four features and an apology.
        cap_center = bool(args.get("cap_center"))
        cap_curve = args.get("cap_curve")
        if cap_center:
            if not cap_curve:
                raise ArgumentRejected(
                    "cap_center=true also needs cap_curve: the name of the curve "
                    "that runs along the hole's border ON THE SOURCE ELEMENT "
                    f"'{element}'. There is no way to find it automatically - "
                    "measured on B30 2026-11-09: AddNewBoundary(edge, surface, 1) "
                    "walks the whole border ring of a filled patch (a four-sided "
                    "sector came back as 91.1450 mm, all four sides), and an "
                    "extract of one of its edges came back as the same 91.1450 mm. "
                    "So the caller has to name the curve, and every ring built from "
                    "it is then exact geometry rather than a circle that nearly "
                    "fits.",
                    code="CAP_CURVE_REQUIRED", stage="validate")
            if abs(total - 360.0) > 1e-6:
                raise PreconditionRejected(
                    f"cap_center=true needs a full turn (total_angle=360) - a "
                    f"centre hole is what the ring leaves behind when it closes. "
                    f"Got total_angle={total:g}: the copies would leave a pie "
                    "slice open, not a hole. Drop cap_center for a partial array.",
                    code="CAP_NOT_FULL_TURN", stage="validate")
            self._ref(str(cap_curve))

        part = self.conn.get_active_part()
        factory = self._factory()
        geoset = self._get_geoset()
        axis_ref, axis_note = self._axis_ref(args.get("axis", "z"), element)
        step = total / count

        copies: list[str] = []
        for index in range(1, count):
            angle = index * step
            rotate = factory.AddNewRotate(self._ref(element), axis_ref, angle)
            geoset.AppendHybridShape(rotate)
            wanted, _ = self._free_name(part, f"{element}_r{angle:g}")
            rotate.Name = wanted
            try:
                part.UpdateObject(rotate)
            except Exception as error:  # noqa: BLE001 - the rotation is the report
                raise PreconditionRejected(
                    f"CATIA refused to build rotation #{index} "
                    f"({angle:g} degrees) of '{element}' "
                    f"({str(error).splitlines()[0][:120]}). "
                    + (f"{len(copies)} rotation(s) were already in the tree: "
                       f"{', '.join(copies)}. " if copies else "")
                    + "Nothing was joined and nothing was deleted.",
                    code="PATTERN_ROTATE_FAILED", stage="execute",
                ) from error
            copies.append(wanted)

        if args.get("join", True) is False:
            return (
                f"Repeated '{element}' {count} times over {total:g} degrees "
                f"(step {step:g} degrees) about '{args.get('axis', 'z')}' - "
                f"{len(copies)} rotated element(s): {', '.join(copies)}.{axis_note} "
                "They were NOT joined (join=false); catia_gsd_join takes them by "
                "name. A join needs a shared edge, and it has to be shared by TWO "
                "faces: copies that all touch one common line are refused."
            )

        join = factory.AddNewJoin(self._ref(element), self._ref(copies[0]))
        for name in copies[1:]:
            join.AddElement(self._ref(name))
        try:
            reply = self._finish(
                join, args.get("name") or f"{element}_pattern",
                f"Repeated '{element}' {count} times over {total:g} degrees "
                f"(step {step:g} degrees) about '{args.get('axis', 'z')}' and "
                f"joined the copies.{axis_note} "
                f"The tree holds the original plus {len(copies)} rotated "
                f"elements ({', '.join(copies)}) and this join - there is no "
                "single pattern node on this build, so changing count means "
                "running this again rather than editing one number.",
            )
        except Exception as error:  # noqa: BLE001 - the rotations stay
            raise PreconditionRejected(
                f"The {len(copies)} rotations were built but CATIA refused the "
                f"join ({str(error).splitlines()[0][:140]})."
                + self._join_diagnosis(part, element, copies[0])
                + " The copies are still in the tree and they are VISIBLE, so they "
                "enter every mesh-based measurement (catia_get_bounding_box, "
                "catia_check_surface, any STL export) - remove them in one call "
                f"with catia_delete_feature(names={copies!r}) if this attempt is "
                "being abandoned, or call this tool again with join=false to keep "
                "the rotations deliberately.",
                code="PATTERN_JOIN_FAILED", stage="execute",
            ) from error

        if cap_center:
            reply += self._cap_centre(part, factory, geoset, axis_ref,
                                      str(cap_curve), count, step, join)
        return reply

    def _cap_centre(self, part: Any, factory: Any, geoset: Any, axis_ref: Any,
                    curve: str, count: int, step: float, base: Any) -> str:
        """Close the hole an array leaves in the middle, using the hole's edge.

        The report's Step 6 in one call. Three routes were measured on B30
        2026-11-09 and two of them do not work on an array result:

          * fill the whole border curve - it carries every loop, and the fill
            comes back as one flat face per loop, stacked (1570.796 mm2 for the
            two discs of a cone border, i.e. pi*400 + pi*100, not the lateral
            area), which then breaks a thickening;
          * split by a circle - a five-sided hole's nearest point to the axis is
            5*cos36 = 4.045, not 5, so a circle at that radius lies wholly inside
            the hole and cuts nothing;
          * rotate the hole's OWN edge and join the copies - exact, no near miss.

        The third one is this. Measured end to end on a five-lobe base (r 5..30,
        five 72-degree sectors, 2080.436 mm2): the joined inner edges measured
        29.3893 mm (the pentagon's perimeter), the fill 59.441 mm2 (the pentagon's
        area), and the closed base 2139.877 = 2080.436 + 59.441.

        Every intermediate is named and every failure deletes what this call
        already built: a caller who abandons an attempt should not be left with
        five rotated copies to hunt down.
        """
        built: list[Any] = []
        try:
            copies: list[str] = []
            for index in range(1, count):
                angle = index * step
                rotate = factory.AddNewRotate(self._ref(curve), axis_ref, angle)
                geoset.AppendHybridShape(rotate)
                unique, _verified = self._free_name(
                    part, f"{curve}_cap_r{angle:g}")
                rotate.Name = unique
                part.UpdateObject(rotate)
                built.append(rotate)
                copies.append(unique)
            border = factory.AddNewJoin(self._ref(curve), self._ref(copies[0]))
            for name in copies[1:]:
                border.AddElement(self._ref(name))
            geoset.AppendHybridShape(border)
            unique, _verified = self._free_name(part, f"{base.Name}_hole")
            border.Name = unique
            part.UpdateObject(border)
            built.append(border)
            cap = factory.AddNewFill()
            cap.AddBound(part.CreateReferenceFromObject(border))
            geoset.AppendHybridShape(cap)
            unique, _verified = self._free_name(part, f"{base.Name}_centre")
            cap.Name = unique
            part.UpdateObject(cap)
            built.append(cap)
            closed = factory.AddNewJoin(part.CreateReferenceFromObject(base),
                                        part.CreateReferenceFromObject(cap))
            geoset.AppendHybridShape(closed)
            unique, _verified = self._free_name(part, f"{base.Name}_closed")
            closed.Name = unique
            part.InWorkObject = closed
            self._safe_update(closed)
            self.conn.refresh_display()
        except Exception as error:  # noqa: BLE001 - clean up, then explain
            for shape in reversed(built):
                try:
                    selection = self.conn.active_document.Selection
                    selection.Clear()
                    selection.Add(shape)
                    selection.Delete()
                except Exception:  # noqa: BLE001 - a leftover is not the failure
                    pass
            try:
                self.conn.active_document.Selection.Clear()
            except Exception:  # noqa: BLE001
                pass
            raise PreconditionRejected(
                f"The {count} copies and their join were built, but capping the "
                f"centre failed ({str(error).splitlines()[0][:160]}). "
                f"The cap needs '{curve}' to run along the hole's border on "
                f"'{base.Name}' and its rotated copies to meet end to end: "
                "a curve that stops short leaves the joined border open and "
                "the fill cannot close it. IF 'element' IS A LOFT "
                "(catia_gsd_multi_section_surface), THIS IS THE USUAL CAUSE AND "
                "THE FIX IS NOT ON THIS CALL: a loft's inner border is an "
                "interpolated curve, not an element, so a curve that merely "
                "sits on it - even at 0.0000 mm, measured 2026-11-09 - is a "
                "different edge and cannot be shared. Pass this same curve as "
                "one of the loft's guides AND make the sections start on it, "
                "then run this call again: the border IS that curve and the cap "
                "shares an edge by construction. That is how the 2026-11-09 "
                "tutorial build succeeded after two failures that looked like "
                "parameter errors. WHAT WAS ROLLED BACK: "
                + (f"{len(built)} element(s) this call created were deleted; "
                   "the array itself was NOT touched. " if built else
                   "nothing this call created was left behind. ")
                + "Measure the joined border with catia_measure_distance before "
                "retrying, or run without cap_center and close the hole by hand.",
                code="CAP_CENTRE_FAILED", stage="execute",
            ) from error
        return (
            f" CENTRE CAPPED: '{curve}' was rotated {count - 1} time(s) about the "
            f"same axis, the copies were joined into '{border.Name}' (the hole's "
            f"border), filled as '{cap.Name}', and joined onto the base as "
            f"'{closed.Name}'. The cap shares the hole's edge by construction - "
            "no circle, no near miss. The tree now holds those "
            f"{2 + (count - 1)} extra elements."
        )

    def _join_diagnosis(self, part: Any, original: str, copy: str) -> str:
        """What the distance between the original and its first copy says.

        The 0.3.82 message asserted a cause - "copies that all touch one common
        line" - and a caller was told to fix a geometry that had nothing to do
        with it: his sector was a ring whose inner boundary is an arc, nowhere
        near the axis, and the real reason was that the sector spans half a
        period so its two side boundaries differ in shape (reported 2026-11-09,
        P2). A cause in a message has to come from a measurement, so measure the
        one thing that separates the two cases that matter, and name the rest as
        possibilities instead of as the reason.
        """
        try:
            spa = self.conn.active_document.GetWorkbench("SPAWorkbench")
            gap = float(spa.GetMeasurable(self._ref(original)).GetMinimumDistance(
                self._ref(copy)))
        except Exception:  # noqa: BLE001 - an unmeasurable pair is not a failure
            return (" The distance between the original and its first copy could "
                    "not be measured, so this reply does not claim a cause.")
        if gap > 1e-3:
            return (f" The original and its first copy are {gap:.4f} mm apart, so "
                    "they do not touch and a join has nothing to merge. Check the "
                    "step against what the element spans: the step is "
                    "total_angle/count, and copies only meet when that matches the "
                    "sector's own angular size.")
        return (f" The original and its first copy touch ({gap:.4f} mm apart), so "
                "the join was not refused for a gap: a shared edge has to be the "
                "SAME edge on both sides, not two edges that merely coincide. A "
                "sector that spans half a period has two differently shaped side "
                "boundaries, so copy i's trailing edge is not copy i+1's leading "
                "edge and the ring cannot be built from it - make the sector one "
                "full period (valley-ridge-valley) and divide by the number of "
                "periods instead. Compare the boundary edges themselves with "
                "catia_measure_distance if this is still unclear.")

    def _plane_normal_to_curve(self, args: dict[str, Any]) -> str:
        """A datum plane normal to a curve, with its origin ON that curve.

        The 2026-11-08 report needed the tutorial's "Normal to Curve + Set Origin
        on Curve" and had to fake it with a frame sketch, which gives a sketch but
        no plane element for later features to reference. Both halves exist in the
        type library and were read out of it on B30 (2026-11-08):
        ``AddNewPlaneNormal(iCurve, iPt)`` and
        ``AddNewPointOnCurveFromPercent(iCurve, ...)``, so nothing is guessed
        here except the point's parameter convention - and that one is reported
        back from the measurement rather than asserted.
        """
        self.conn.ensure_connected()
        curve = args["curve"]
        position = float(args.get("position", 0.5))
        if not 0.0 <= position <= 1.0:
            raise ArgumentOutOfRange(
                "position is a FRACTION of the curve's length: 0 is its start, 1 "
                f"its end, 0.5 the middle. Got {position}. Compose with a curve "
                "whose direction you know (catia_gsd_line or catia_gsd_spline).",
                code="POSITION_OUT_OF_RANGE", stage="validate")

        part = self.conn.get_active_part()
        factory = self._factory()
        geoset = self._get_geoset()
        curve_ref = self._ref(curve)

        # Three arguments, not two: the type library names them
        # (iCrv, iLong, iOrientation) and a two-argument call answers
        # "无效的参数数目 / invalid number of arguments" - measured on B30
        # 2026-11-08, after the same two-argument guess had been reported as if
        # the plane call were the one refusing.
        point = factory.AddNewPointOnCurveFromPercent(curve_ref, position, 0)
        geoset.AppendHybridShape(point)
        point_name, _ = self._free_name(part, f"{curve}_at{position:g}")
        point.Name = point_name
        try:
            part.UpdateObject(point)
        except Exception as error:  # noqa: BLE001 - the point is the blocker
            # THE POINT IS ALREADY IN THE TREE WHEN THIS RAISES. It was appended
            # and named a few lines up, so a caller that only sees the exception
            # is left with an element that cannot update - and CATIA stops every
            # later update at it. The 2026-11-11 report hit exactly this: the
            # engrave said "Nothing this call created was left behind", the point
            # stayed, and catia_update_part then named it as the blocker, 2/2.
            self._drop_element(point)
            gone = self._name_gone(part, point_name)
            raise PreconditionRejected(
                f"CATIA refused a point at {position:g} along '{curve}' "
                f"({str(error).splitlines()[0][:120]}). A plane normal to a curve "
                "needs a point ON that curve, so the plane was not attempted. "
                + (f"The point this call had already added ('{point_name}') was "
                   "removed again, so the tree is as it was."
                   if gone else
                   f"THE POINT THIS CALL ADDED ('{point_name}') COULD NOT BE "
                   "REMOVED or could not be checked for - if it is still in the "
                   "tree it will stop every later update. Look for it and delete "
                   "it with catia_delete_feature if present.")
                + " '" + curve + "' may not be a curve - a surface or a plane "
                "cannot carry a parameter.",
                code="POINT_ON_CURVE_FAILED", stage="execute",
                modified=not gone,
            ) from error

        where = self._on_curve_note(part, curve_ref, position)
        plane = factory.AddNewPlaneNormal(curve_ref,
                                         part.CreateReferenceFromObject(point))
        return self._finish(
            plane, args.get("name"),
            f"Plane normal to '{curve}' at {position:g} of its length "
            f"(origin '{point_name}'{where}). This is the 'Normal to Curve, origin "
            "on curve' plane - unlike a frame sketch it is a tree element, so "
            "later features can reference it directly.",
        )

    def _on_curve_note(self, part: Any, curve_ref: Any, position: float) -> str:
        """``" on a 100.000 mm curve, 50.000 mm along it"`` - measured.

        The point's own coordinates cannot be read back (`GetPoint` answers
        「方法 GetPoint 失败」 on this build: it wants a by-ref array), so the
        position is reported from the curve's measured LENGTH instead - and that
        length is what makes ``position`` mean something specific. Measured on B30
        2026-11-08: the parameter is a FRACTION, not a length in mm - a point at
        0.5 on a 100 mm line put the plane 0.000 mm from a datum point at z=50,
        and 49.5 mm would be what a length would have given.
        """
        try:
            spa = self.conn.active_document.GetWorkbench("SPAWorkbench")
            length = float(spa.GetMeasurable(curve_ref).Length)
        except Exception:  # noqa: BLE001 - a curve with no length reports nothing
            return ""
        if not length:
            return ""
        return (f" - which is {position * length:.3f} mm along a {length:.3f} mm "
                "curve, because position is a fraction of the length")

    def _translate(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        element = args["element"]
        distance = float(args["distance"])
        direction, direction_note = self._direction_obj(args["direction"], element)
        translate = self._factory().AddNewTranslate(
            self._ref(element), direction, distance)
        return self._finish(
            translate, args.get("name"),
            f"'{element}' copied and moved {distance:g} mm.{direction_note}",
        )

    # ────────────────────────────────────────────────────────────
    # Surface → solid bridges (Part Design features on GSD surfaces)
    # ────────────────────────────────────────────────────────────

    def _thick_surface(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self.conn.get_active_part_body()

        surface_ref = self._ref(args["surface"])
        thickness1 = args["thickness1"]
        thickness2 = args.get("thickness2", 0)

        part.InWorkObject = body
        # AddNewThickSurface(surface, isymmetric, top offset, bottom offset)
        thick = part.ShapeFactory.AddNewThickSurface(surface_ref, 0, thickness1, thickness2)
        try:
            self._safe_update(thick)
        except Exception as first_error:
            # RETRY ONCE, same arguments, before saying anything about geometry.
            # Reported 2026-11-04: a 0.3 mm thickening failed on the last step of
            # a 13-step plan, then succeeded immediately when called alone, and
            # again with the whole tree visible - same document, same session,
            # same numbers. This operator is session-sensitive the way the loft
            # already documented is, and gsd_multi_section_surface has said
            # "retry once" for months while thick_surface said nothing and
            # instead handed the caller a radius to design against.
            first_reason = str(first_error).splitlines()[0][:200]
            try:
                thick = part.ShapeFactory.AddNewThickSurface(
                    surface_ref, 0, thickness1, thickness2)
                self._safe_update(thick)
            except Exception as second_error:
                total = abs(float(thickness1)) + abs(float(thickness2))
                # The number that is actually compared against the curvature is
                # ONE SIDE's offset, not the two added together - see the four
                # measurements in _offset_blocker.
                side = max(abs(float(thickness1)), abs(float(thickness2)))
                raise RuntimeError(
                    f"{second_error} ThickSurface offset {thickness1}+{thickness2} mm "
                    "and the update failed TWICE - this call already retried once "
                    f"with the same arguments (first attempt: {first_reason}). The "
                    "usual cause is geometric, not an argument: if the surface is "
                    "thinner somewhere than the total offset, the two offset faces "
                    "cross and CATIA refuses - measured on a 2.30 mm lens section "
                    "with 1.27/1.27 and with 2.54/0, both refused, while a 6 mm "
                    "hexagon of the same shape thickened."
                    + self._offset_blocker(part, total, side)
                ) from second_error
            self.conn.refresh_display()
            return (
                f"ThickSurface solid created from '{args['surface']}' "
                f"({thickness1}/{thickness2} mm) on the SECOND attempt. The first "
                f"update failed with: {first_reason}. That is session-sensitive, "
                f"not a property of the surface - it succeeded with identical "
                f"arguments, so do not re-design on the strength of the first "
                f"failure. Feature: '{thick.Name}'"
            )
        self.conn.refresh_display()
        return (
            f"ThickSurface solid created from '{args['surface']}' "
            f"({thickness1}/{thickness2} mm). Feature: '{thick.Name}'"
        )

    def _offset_blocker(self, part: Any, total: float, side: float | None = None) -> str:
        """The tightest radius of curvature in this document's mesh, measured now.

        "Update failed" is all CATIA says when an offset has to cross itself, and
        the 2026-11-04 report rebuilt a 22-section model twice before working out
        by hand that its valley walls had a 0.19-0.24 mm radius against a 0.3 mm
        wall. The mesh carries that number (measurement._curvature_stats), so it
        is read here and put in the error.

        WHICH NUMBER IS COMPARED AGAINST THAT RADIUS is one side's offset, not
        the two sides added: ``AddNewThickSurface(surface, symmetric, top,
        bottom)`` applies each offset to its own side, so the two faces cross
        when EITHER side exceeds the local radius. Four measurements agree with
        that reading and disagree with "total must stay below the radius":

          0.500 / 0       refused twice at a 0.355 mm radius (bottle base,
                          2026-11-09 - one side of 0.5 over 0.355)
          0.250 / 0.250   built, wall 0.500 (same report - each side 0.25 under
                          0.355, and the TOTAL was still above it)
          2.540 / 0       refused on a 2.30 mm lens section (one side 2.54)
          1.270 / 1.270   refused on the same section (each side 1.27 over the
                          ~1.15 mm half-thickness)

        The 2026-11-09 report's complaint is the reason this is spelled out: the
        old text offered only "keep the total below 0.355 mm", which reads as
        "this wall can never be 0.5 mm" and sent them to abandon the thickness
        the design needed. Splitting the same total across both sides is the
        answer, and it is a different wall only in name.

        It runs in a FAILURE path, so everything is optional: the part is in an
        error state, the export may write nothing, and the caller still has to
        get a usable message. When the measurement cannot be taken, the reply
        says where to get it instead of pretending.
        """
        pointer = (" Run catia_check_surface with thickness=<total> on this document "
                   "to read the smallest radius of curvature and where it is; add "
                   "region, e.g. {\"z\": [-34, -14]}, to look at one band instead of "
                   "the whole model.")
        try:
            from catia_mcp.tools.measurement import (MeasurementTools,
                                                    _curvature_stats)

            captured: dict[str, Any] = {}

            def visit(path: str) -> None:
                captured.update(_curvature_stats(path, total, 2))

            MeasurementTools(self.conn)._mesh_export(part, None, visit)
        except Exception:  # noqa: BLE001 - a failure path may not fail again
            return pointer
        data = captured
        if not data:
            return pointer

        # What the estimate refused to call a bend. A duplicated or double-sided
        # sheet folds back 180 deg, and "radius = facet / pi" from that used to be
        # reported as the blocker - 0.034 mm at the open top border of a skirt,
        # where there was nothing to widen (reported 2026-11-04). Those edges are
        # now excluded, and this says how many so the number cannot silently
        # disappear either.
        excluded = data.get("excluded") or {}
        folded = int(excluded.get("fold_back_edges") or 0)
        ambiguous = int(excluded.get("ambiguous_edges") or 0)
        borders = int(excluded.get("open_boundary_edges") or 0)
        skip = ""
        if folded or ambiguous:
            parts = []
            if folded:
                parts.append(f"{folded} edge(s) fold back by 175 deg or more")
            if ambiguous:
                parts.append(f"{ambiguous} edge(s) are shared by more than two "
                             f"triangles")
            skip = (f" EXCLUDED from this number: {', '.join(parts)} - overlapping "
                    f"or duplicated geometry, where 'radius' would be the facet "
                    f"size, not a bend.")
        if borders:
            skip += (f" The visible mesh has {borders} open boundary edge(s): a "
                     f"surface whose border is visible, so a reading within one "
                     f"facet of a border may describe where the mesh ends rather "
                     f"than a bend.")

        if not data.get("edges_measured"):
            return (f" MEASURED: nothing in the visible mesh is a bend this "
                    f"estimate can use.{skip} Curvature therefore cannot explain "
                    f"the failure; look at the surface's continuity or at a "
                    f"self-intersection." + pointer)

        radius = data.get("tightest_smooth_radius_mm")
        worst = (data.get("tightest_edges") or [{}])[0]
        where = ""
        if worst:
            where = (f" at r={worst.get('r')}, theta={worst.get('theta_deg')} deg, "
                     f"z={worst.get('z')}")
            if worst.get("at_open_border"):
                where += (" (an endpoint of that edge lies on an OPEN BORDER, so "
                          "check whether the bend is real or the mesh ends there)")
        # What the mesh actually covered. This diagnosis reads the DOCUMENT's
        # visible geometry, and the surface being thickened is one element of it.
        # Reported 2026-11-04: a 0.079 mm reading came from a mesh holding nine
        # overlapping lofts plus a skirt, while the element being thickened had
        # two tight edges of its own, both on a seam, and thickened fine once
        # isolated. A number quoted without its scope invites exactly that.
        visible = self._visible_element_count(part)
        scope = ""
        if visible > 1:
            scope = (f" SCOPE: the mesh measured here is the document's VISIBLE "
                     f"geometry - {visible} GSD element(s) are visible and the one "
                     f"being thickened is only one of them, so a tight reading may "
                     f"belong to a neighbour. Hide the others and re-run before "
                     f"acting on any number in this message.")
        on_border = bool(worst.get("at_open_border"))
        # The comparison that matters is ONE side against the radius. When the
        # caller has already split the total, `side` says so and the radius stops
        # being an explanation at all.
        worst_side = total if side is None else side
        if radius is not None and radius < worst_side:
            head = (f" MEASURED: the tightest smooth radius in the visible mesh is "
                    f"{radius:.3f} mm{where}, and the largest SINGLE offset is "
                    f"{worst_side:.3f} mm (of {total:.3f} mm total).")
            if on_border:
                # A CONDITIONAL prescription, not a flat one. The report's point
                # was that "keep the total below 0.079 mm" reads as an instruction;
                # it is only an instruction if the bend is real, and an edge on an
                # open border is where the mesh ends as often as it bends.
                return (head + " That edge lies on an OPEN BORDER, where the mesh "
                        "ends as often as the surface bends - so confirm the bend "
                        "before acting on it. IF THE BEND IS REAL: Widen that "
                        f"feature, or keep EACH SIDE of the offset below "
                        f"{radius:.3f} mm. IF IT "
                        "IS THE BORDER: nothing in the model needs changing, and "
                        f"the failure is elsewhere - a seam or a "
                        f"self-intersection.{skip}{scope}")
            return (head + " There the offset face on that side has to cross the "
                    "other. Two ways out, and the second keeps the wall you "
                    "asked for: (1) widen that feature, or (2) KEEP EACH SIDE "
                    f"BELOW {radius:.3f} mm - this call takes two offsets and "
                    "CATIA applies one to each side, so the SAME total wall split "
                    "in half is compared against the radius one side at a time. "
                    "Measured on a bottle base (2026-11-09): 0.500 on one side "
                    "was refused twice against a 0.355 mm radius, 0.250/0.250 "
                    "built with a 0.500 mm wall - same thickness, different "
                    f"argument. (Read from the document's VISIBLE mesh over "
                    f"{data['edges_measured']} interior edges; the estimate runs a "
                    f"few percent low.){skip}{scope}")
        # ONE SIDE, ALWAYS (0.3.95). The old sentence compared the radius with
        # `total` even when the caller had split it, so 0.1/0.1 was reported as
        # "NOT below the 0.200 mm offset" while the rule this package states
        # everywhere is "EACH SIDE's offset". A report quoted the two side by side
        # (2026-11-10, P1-4); both now use the same number. The branch that read
        # `radius < total` was unreachable anyway - whoever got there had already
        # passed `radius < side` - so it is gone rather than fixed twice.
        if radius is not None:
            return (f" MEASURED: the tightest smooth radius in the visible mesh is "
                    f"{radius:.3f} mm{where}, which is NOT below the "
                    f"{worst_side:.3f} mm single-side offset"
                    + (f" (of {total:.3f} mm total)" if side is not None else "")
                    + ", so on what this mesh resolves, curvature does not explain "
                    "the failure. Look at a seam or a self-intersection in the "
                    "surface itself, not at its radius. NEXT STEP THAT RUNS: "
                    "catia_check_surface on the surface reports its creases and "
                    "non-manifold edges - a seam shows up as one of them, and an "
                    "extract of a periodic loft face carries one. If it is a seam, "
                    "take ONE piece of it with catia_gsd_boundary(edge=N) and "
                    "thicken that piece instead of the whole face."
                    f"{skip}{scope}")
        return pointer

    def _visible_element_count(self, part: Any) -> int:
        """How many GSD elements are visible - i.e. what an STL export contains.

        Read from ``HybridShape.Visible``, which is readable even where writing it
        is refused (setting ``Item.Visible`` fails; catia_set_visibility goes
        through SetShow). An element whose flag cannot be read counts as visible:
        over-reporting the scope is honest, under-reporting it is not.
        """
        total = 0
        try:
            bodies = part.HybridBodies
            for index in range(1, bodies.Count + 1):
                try:
                    shapes = bodies.Item(index).HybridShapes
                    for position in range(1, shapes.Count + 1):
                        try:
                            if shapes.Item(position).Visible:
                                total += 1
                        except Exception:  # noqa: BLE001
                            total += 1
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001 - no geometrical sets at all
            return 0
        return total

    def _close_surface(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self.conn.get_active_part_body()

        surface_ref = self._ref(args["surface"])
        part.InWorkObject = body
        close = part.ShapeFactory.AddNewCloseSurface(surface_ref)
        self._safe_update(close)
        self.conn.refresh_display()
        return f"CloseSurface solid created from '{args['surface']}'. Feature: '{close.Name}'"
