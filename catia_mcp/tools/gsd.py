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
                        "offset": {"type": "number", "description": "Offset distance (mm)"},
                        "reverse": {
                            "type": "boolean",
                            "description": "Offset in the opposite direction",
                            "default": False,
                        },
                        "name": {"type": "string", "description": "Optional name for the plane"},
                    },
                    "required": ["base_plane", "offset"],
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
                    "spline when it just has to be loftable."
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
                        "radius": {"type": "number", "description": "Radius (mm)"},
                        "name": {"type": "string", "description": "Optional name for the circle"},
                    },
                    "required": ["center", "support_plane", "radius"],
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
                        "name": {"type": "string", "description": "Optional name for the result"},
                    },
                    "required": ["element", "support"],
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
                        "name": {"type": "string", "description": "Optional name for the result"},
                    },
                    "required": ["element1", "element2"],
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
                                "or the volume must match the analytic frustum."
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
                    "Revolve a profile curve around an axis line to create a surface of "
                    "revolution. Angles in degrees."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "profile": {"type": "string", "description": "Profile element name"},
                        "axis": {"type": "string", "description": "Axis line element name"},
                        "angle1": {
                            "type": "number",
                            "description": "First angle limit (degrees)",
                            "default": 360,
                        },
                        "angle2": {
                            "type": "number",
                            "description": "Second angle limit (degrees)",
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
                    "synonym because every other GSD tool names it after its content."
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
                    "Use orientation to flip which side is kept."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "element": {"type": "string", "description": "Element to split"},
                        "cutter": {
                            "type": "string",
                            "description": "Cutting element: 'xy', 'yz', 'zx', or element name",
                        },
                        "reverse": {
                            "type": "boolean",
                            "description": "Keep the other side",
                            "default": False,
                        },
                        "name": {"type": "string", "description": "Optional name for the result"},
                    },
                    "required": ["element", "cutter"],
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
                "name": "catia_gsd_thick_surface",
                "description": (
                    "Turn a surface into a SOLID by adding thickness (Part Design "
                    "ThickSurface). The solid is created in the active body. "
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
                    "solid there)."
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
        """One pass over the tree for that exact name, or None."""
        for hb in self._iter_geosets(part.HybridBodies):
            for collection in ("HybridShapes", "HybridSketches"):
                try:
                    return getattr(hb, collection).Item(name)
                except Exception:  # noqa: BLE001 - the next collection may have it
                    pass

        bodies = part.Bodies
        for i in range(1, bodies.Count + 1):
            try:
                return bodies.Item(i).Sketches.Item(name)
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
        """Resolve a point spec (element name or [x, y, z]) to a Reference.

        Coordinates create a new point feature in the active geoset.
        """
        if isinstance(spec, str):
            return self._ref(spec)
        if isinstance(spec, (list, tuple)) and len(spec) == 3:
            part = self.conn.get_active_part()
            point = self._factory().AddNewPointCoord(spec[0], spec[1], spec[2])
            self._get_geoset().AppendHybridShape(point)
            part.UpdateObject(point)
            return part.CreateReferenceFromObject(point)
        raise ValueError(f"Invalid point spec: {spec!r}. Use an element name or [x, y, z].")

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
            shape.Name = name
        part.InWorkObject = shape
        self._safe_update(shape)
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
        ref1 = self._point_ref(args["point1"])
        ref2 = self._point_ref(args["point2"])
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
        ref1 = self._point_ref(args["point1"])
        ref2 = self._point_ref(args["point2"])
        ref3 = self._point_ref(args["point3"])
        plane = self._factory().AddNewPlane3Points(ref1, ref2, ref3)
        return self._finish(plane, args.get("name"), "Plane created through 3 points.")

    def _spline(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        element = args.get("element")
        closed = bool(args.get("closed", False))

        if element and args.get("points"):
            raise ValueError(
                "Pass either 'points' or 'element', not both: 'element' takes the "
                "corners of a sketch this server drew, 'points' takes them from you."
            )
        if element:
            coords, note = self._outline_points(str(element), closed)
            refs = [self._point_ref(p) for p in coords]
            count = len(coords)
        else:
            points = args.get("points")
            if not points:
                raise ValueError(
                    "catia_gsd_spline needs 'points' (element names or [x, y, z]) "
                    "or 'element' (a sketch whose outline this server drew)."
                )
            refs = [self._point_ref(p) for p in points]
            count = len(points)
            note = ""

        spline = self._factory().AddNewSpline()
        for ref in refs:
            spline.AddPoint(ref)
        if closed:
            spline.SetClosing(1)

        return self._finish(
            spline, args.get("name"),
            f"3D spline created through {count} points"
            f"{' (closed)' if closed else ''}.{note}",
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

        if args.get("ruled"):
            if args.get("start_tangent") or args.get("end_tangent"):
                raise ValueError(
                    "'ruled' and an end tangency condition cannot be combined: the "
                    "ruled path is built from one blend per section pair, so there is "
                    "no single loft feature to carry the condition. Drop 'ruled' if "
                    "you need tangency; drop the tangency if you need straight sides."
                )
            return self._ruled_loft(sections, args.get("name"))

        loft = self._factory().AddNewLoft()
        for section_name, orientation in zip(sections, orientations):
            ref = self._ref(section_name)
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
            raise RuntimeError(
                f"{e}{tangency_error} Common causes, in the order to check them: "
                f"(1) a section that is not ONE curve - a polygon outline is four "
                f"curves however you draw it (four catia_sketch_line calls, and "
                f"catia_sketch_rectangle too), and an Extract of it is still four, "
                f"so build the section as a closed catia_gsd_spline through the "
                f"corners or as catia_gsd_circle; measured on B30 2026-10-25, that "
                f"is the difference between building and failing; (2) a guide that "
                f"does not intersect every section (check with measurements); "
                f"(3) a section whose curve direction opposes the others (flip it "
                f"with orientations=-1). If none applies and the sections are "
                f"plainly lofts-able, retry once in a fresh session: one B32 SP6 "
                f"session reported every loft failing while catia_gsd_blend "
                f"accepted the same curve pair (2026-10-23), and two other B32 SP6 "
                f"sessions then built both the smooth and the ruled loft and "
                f"matched B30 to 0.02 mm3 (2026-10-24). If it still fails, build "
                f"the transition with two blends."
            ) from e

    def _ruled_loft(self, sections: list[str], name: str | None) -> str:
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
        axis_ref = self._ref(args["axis"])
        angle1 = args.get("angle1", 360)
        angle2 = args.get("angle2", 0)
        revol = self._factory().AddNewRevol(profile_ref, angle1, angle2, axis_ref)
        return self._finish(
            revol, args.get("name"),
            f"Revolution surface created: {angle1}° around '{args['axis']}'.",
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

        return self._finish(join, args.get("name"), f"Join created from {len(elements)} elements.")

    def _split(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        elem_ref = self._ref(args["element"])
        cutter_ref = self._ref(args["cutter"])
        orientation = -1 if args.get("reverse", False) else 1
        split = self._factory().AddNewHybridSplit(elem_ref, cutter_ref, orientation)
        return self._finish(
            split, args.get("name"),
            f"Split of '{args['element']}' by '{args['cutter']}' created.",
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
        except Exception as error:
            raise RuntimeError(
                f"{error} ThickSurface offset {thickness1}+{thickness2} mm and the "
                "update failed. The usual cause is geometric, not an argument: if "
                "the surface is thinner somewhere than the total offset, the two "
                "offset faces cross and CATIA refuses - measured on a 2.30 mm lens "
                "section with 1.27/1.27 and with 2.54/0, both refused, while a 6 mm "
                "hexagon of the same shape thickened. Try a smaller total, or model "
                "the part as a padded polygon instead."
            ) from error
        self.conn.refresh_display()
        return (
            f"ThickSurface solid created from '{args['surface']}' "
            f"({thickness1}/{thickness2} mm). Feature: '{thick.Name}'"
        )

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
