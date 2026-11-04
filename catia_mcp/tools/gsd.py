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
import re
from typing import Any

from catia_mcp.connection import CATIAConnection
from catia_mcp.results import ArgumentOutOfRange, ArgumentRejected
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


class GSDTools:
    """Tools for Generative Shape Design (wireframe & surface) in CATIA V5."""

    DEFAULT_GEOSET = "GSD_Set"

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
                    "make it the active target for subsequent GSD elements."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Name for the new geometrical set",
                            "default": "GSD_Set",
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
                "description": "Project a curve/point onto a support surface or plane.",
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
                    "length is the sum of the borders - that is not a defect, and "
                    "splitting it further needs catia_gsd_split on the loops. "
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
                    "name is absent from the object). "
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
                    "the section. The way through is to model the bulge instead of "
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
                    "(one closed curve or several connected curves)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "boundaries": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "description": "Boundary curve element names, in contour order",
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
                    "the generic update error)."
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
                    "original stays where it is. 'axis' is 'x', 'y' or 'z' (the "
                    "origin axis, built as a line on first use) or the name of an "
                    "existing line/axis element; 'angle' is in degrees, positive "
                    "counter-clockwise looking down the axis toward its origin "
                    "(right-hand rule along +x/+y/+z). "
                    "THIS IS HOW A GSD PATTERN IS MADE. There is no surface "
                    "pattern tool - catia_circ_pattern is a Part Design feature "
                    "and takes a solid. A five-lobe base is one lobe plus four "
                    "rotations of 72 degrees; rebuilding the lobe at five "
                    "positions instead costs five extra constructions of the same "
                    "geometry and is what this tool exists to stop (2026-11-04). "
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
                "name": "catia_gsd_translate",
                "description": (
                    "Copy an element and move the copy along a direction. "
                    "'direction' is [x, y, z], 'x'/'y'/'z', or the name of a "
                    "line element whose direction is used; 'distance' is in mm, "
                    "positive along that direction. The original stays. "
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
                    "THE TOTAL OFFSET MUST STAY BELOW TWICE THE SURFACE'S SMALLEST "
                    "RADIUS OF CURVATURE, and a thin wing section is the case that "
                    "breaks: the offset surfaces meet on the far side and the update "
                    "fails. Measured on B30 2026-10-25 on a lens-shaped loft whose "
                    "normal thickness is 2.30 mm - thickness1/thickness2 = 1.27/1.27 "
                    "failed, and so did 2.54/0, so it is not a two-sided problem: at "
                    "the sharp corners the radius goes to zero and the offset "
                    "self-intersects whichever side it goes. A 6 mm hexagonal section "
                    "of the same length thickens fine. If a section is thinner than "
                    "the offset you want, thicken LESS, or model the part as a padded "
                    "polygon - or better, use catia_solid_loft, which builds the "
                    "same shape as a solid in one feature with no thickness to add "
                    "and therefore no limit to hit (measured: a 2.30 mm lens "
                    "section that refuses every offset here lofts to a 720.5 mm3 "
                    "solid there). "
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
            raise ValueError(problem)
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
            case "catia_gsd_translate":
                return self._translate(arguments)
            case "catia_gsd_thick_surface":
                return self._thick_surface(arguments)
            case "catia_gsd_close_surface":
                return self._close_surface(arguments)
            case _:
                raise ValueError(f"Unknown GSD tool: {tool_name}")

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
        raise RuntimeError(message)

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
                raise ValueError(
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
        name = args.get("name", self.DEFAULT_GEOSET)

        hb = part.HybridBodies.Add()
        hb.Name = name
        self._active_geoset_name = hb.Name
        self._active_geoset_doc = self._doc_name()
        try:
            part.Update()
        except Exception:
            pass
        return f"Geometrical set '{hb.Name}' created and set as active."

    def _set_active_geoset(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        name = args["name"]

        for hb in self._iter_geosets(part.HybridBodies):
            if hb.Name == name:
                self._active_geoset_name = name
                self._active_geoset_doc = self._doc_name()
                return f"Active geometrical set: '{name}'"

        available = [hb.Name for hb in self._iter_geosets(part.HybridBodies)]
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

        for hb in self._iter_geosets(part.HybridBodies):
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
            result["geometrical_sets"].append(
                {"name": hb.Name, "elements": shapes, "sketches": sketches}
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
            raise ValueError(
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
                raise ValueError(
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
            raise RuntimeError(
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
            raise RuntimeError(
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
            raise RuntimeError(
                f"'{name}' was not created on a datum plane (it sits on a face, "
                "or on an element whose frame cannot be read back), so its 2D "
                "coordinates cannot be turned into global XYZ here. Pass the "
                "corners as 'points', or rebuild the sketch on an offset datum "
                "plane - the frame of xy/yz/zx is known, a face's is not."
            )

        loop = self._chain_outline(recorded.get("elements") or [], name)
        if len(loop) < 2:
            raise RuntimeError(
                f"'{name}' holds {len(loop)} usable corner(s); a spline needs at "
                "least 2."
            )
        coords = [self._to_global(placement, u, v) for u, v in loop]
        label = (f"'{name}'" if not alias or alias == name
                 else f"'{name}' (rebuilt as '{alias}')")
        return coords, (f" Corners taken from sketch {label} in "
                        f"{placement['base']} plane order, z={placement['distance']:g}.")

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
            raise RuntimeError(
                f"'{name}' holds a {sorted(already)[0]}, which is already ONE "
                "curve - pass the sketch itself as the loft section; it needs no "
                "3D spline."
            )
        if "spline" in kinds:
            raise RuntimeError(
                f"'{name}' already holds a spline, which is one curve: pass the "
                "sketch itself as the section instead of wrapping it in a 3D "
                "spline."
            )
        raise RuntimeError(
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
                raise RuntimeError(
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
        the mapping everybody gets wrong once.
        """
        base = str(placement.get("base") or "xy").lower()
        if base == "frame":
            # A sketch on an arbitrary plane keeps its own (u, v): p = O + u*w + v*t.
            origin = [float(x) for x in placement.get("origin") or (0, 0, 0)]
            first = [float(x) for x in placement.get("u") or (1, 0, 0)]
            second = [float(x) for x in placement.get("v") or (0, 1, 0)]
            return [origin[i] + first[i] * u + second[i] * v for i in range(3)]
        distance = float(placement.get("distance") or 0.0)
        if base == "yz":
            return [distance, u, v]
        if base == "zx":
            return [v, distance, u]
        return [u, v, distance]

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

    def _project(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        elem_ref = self._ref(args["element"])
        support_ref = self._ref(args["support"])
        projection = self._factory().AddNewProject(elem_ref, support_ref)
        return self._finish(
            projection, args.get("name"),
            f"Projection of '{args['element']}' onto '{args['support']}' created.",
        )

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
            raise RuntimeError(
                f"CATIA refused a {radius} mm fillet between '{first}' and "
                f"'{second}' in all four orientations, so nothing was created:\n  "
                + "\n  ".join(failed)
                + "\nA rolling ball needs the two surfaces to form a CORNER - "
                  "sharing an edge, with the ball fitting between them. Two "
                  "surfaces that merely exist fail every orientation (measured: "
                  "two disjoint parallel cylinders, 0 of 9 combinations). Check "
                  "that they meet with catia_measure_distance; if the wall is "
                  "thick enough, catia_fillet rounds a solid edge."
            )

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
                raise RuntimeError(
                    f"CATIA kept {registered} of the {len(sections)} sections "
                    f"({', '.join(sections)}). Sections have to be curves the "
                    "spine can run normal to; a point or a surface is accepted by "
                    "AddSection and then silently dropped.")
            for name in guides:
                spine.AddGuide(self._ref(name))
            guide_count = int(spine.GetNumberOfGuides())
            if guides and guide_count != len(guides):
                raise RuntimeError(
                    f"CATIA kept {guide_count} of the {len(guides)} guides "
                    f"({', '.join(guides)}).")
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
        boundary = self._factory().AddNewBoundaryOfSurface(self._ref(element))
        return self._finish(
            boundary, args.get("name"),
            f"Boundary of '{element}' created (a closed surface gives one curve "
            f"with both loops; measure it with catia_measure_distance if its "
            f"length matters).",
        )

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

    def _multi_section_surface(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        sections = args["sections"]
        guides = args.get("guides", [])
        orientations = args.get("orientations") or [1] * len(sections)
        if len(orientations) != len(sections):
            raise ValueError(
                f"'orientations' has {len(orientations)} entries but there are "
                f"{len(sections)} sections — lengths must match."
            )
        resolved = self._resolve_sections(sections)
        self._refuse_point_sections(sections, resolved)

        if args.get("ruled"):
            if args.get("start_tangent") or args.get("end_tangent"):
                raise ValueError(
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
            raise RuntimeError(
                f"{e}{tangency_error} THIS CALL: {context}. "
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
            fill.AddBound(self._ref(boundary_name))
        try:
            return self._finish(
                fill, args.get("name"),
                f"Fill surface created from {len(boundaries)} boundary curve(s).",
            )
        except RuntimeError as error:
            raise RuntimeError(
                f"{error} A fill needs CLOSED boundaries that meet each other: one "
                "wire for a single loop, or several curves that actually share "
                "endpoints (the two ends of a loft plus its section rings, for "
                "instance). Boundaries that only come close do not bind. If they do "
                "meet and this still fails, it is session-sensitive like the loft: "
                "retry once in a fresh session, and if it still fails build the cap "
                "as a swept-and-thickened section instead."
            ) from error

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
        surface_ref = self._ref(args["surface"])
        offset = args["offset"]
        reverse = args.get("reverse", False)
        # AddNewOffset(surface, value, orientation, repetition)
        offset_surf = self._factory().AddNewOffset(surface_ref, offset, reverse, 0)
        return self._finish(
            offset_surf, args.get("name"),
            f"Offset surface created: {offset} mm from '{args['surface']}'.",
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
            raise ValueError(
                "catia_gsd_join needs 'elements': the list of curves/surfaces to "
                "join, in order, e.g. {\"elements\": [\"C1\", \"C2\", \"Shell\"]}. "
                "('surfaces' is accepted as a synonym.)"
            )
        if not isinstance(elements, list) or len(elements) < 2:
            raise ValueError(
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

    def _split(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        elem_ref = self._ref(args["element"])
        cutter_ref = self._ref(args["cutter"])
        orientation = -1 if args.get("reverse", False) else 1
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
        return self._finish(
            split, args.get("name"),
            f"Split of '{args['element']}' by '{args['cutter']}' created, "
            f"keeping {side}; reverse=true swaps the two.",
        )

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
                raise ValueError(
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
            raise ValueError(
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
                raise RuntimeError(
                    f"{second_error} ThickSurface offset {thickness1}+{thickness2} mm "
                    "and the update failed TWICE - this call already retried once "
                    f"with the same arguments (first attempt: {first_reason}). The "
                    "usual cause is geometric, not an argument: if the surface is "
                    "thinner somewhere than the total offset, the two offset faces "
                    "cross and CATIA refuses - measured on a 2.30 mm lens section "
                    "with 1.27/1.27 and with 2.54/0, both refused, while a 6 mm "
                    "hexagon of the same shape thickened."
                    + self._offset_blocker(part, total)
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

    def _offset_blocker(self, part: Any, total: float) -> str:
        """The tightest radius of curvature in this document's mesh, measured now.

        "Update failed" is all CATIA says when an offset has to cross itself, and
        the 2026-11-04 report rebuilt a 22-section model twice before working out
        by hand that its valley walls had a 0.19-0.24 mm radius against a 0.3 mm
        wall. The mesh carries that number (measurement._curvature_stats), so it
        is read here and put in the error.

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
        if radius is not None and radius < total:
            head = (f" MEASURED: the tightest smooth radius in the visible mesh is "
                    f"{radius:.3f} mm{where}, which is less than the {total:.3f} mm "
                    f"total offset.")
            if on_border:
                # A CONDITIONAL prescription, not a flat one. The report's point
                # was that "keep the total below 0.079 mm" reads as an instruction;
                # it is only an instruction if the bend is real, and an edge on an
                # open border is where the mesh ends as often as it bends.
                return (head + " That edge lies on an OPEN BORDER, where the mesh "
                        "ends as often as the surface bends - so confirm the bend "
                        "before acting on it. IF THE BEND IS REAL: Widen that "
                        f"feature, or keep the total below {radius:.3f} mm. IF IT "
                        "IS THE BORDER: nothing in the model needs changing, and "
                        f"the failure is elsewhere - a seam or a "
                        f"self-intersection.{skip}{scope}")
            return (head + " There the two offset faces must cross: widen that "
                    "feature, or keep the total below "
                    f"{radius:.3f} mm. (Read from the document's VISIBLE mesh over "
                    f"{data['edges_measured']} interior edges; the estimate runs a "
                    f"few percent low.){skip}{scope}")
        return (f" MEASURED: the tightest smooth radius in the visible mesh is "
                f"{radius} mm{where}, which is NOT below the {total:.3f} mm offset - "
                f"so on what this mesh resolves, curvature does not explain the "
                f"failure. Look at a seam or a self-intersection in the surface "
                f"itself, not at its radius.{skip}{scope}")

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
