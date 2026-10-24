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
                    "Create a 3D spline through a list of points. Each point is an existing "
                    "element name or [x, y, z] coordinates in mm."
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
                        "closed": {
                            "type": "boolean",
                            "description": "Close the spline into a loop",
                            "default": False,
                        },
                        "name": {"type": "string", "description": "Optional name for the spline"},
                    },
                    "required": ["points"],
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
                    "Note there is no tangent/curvature condition here: CATIA's GSD "
                    "loft does not expose one (only SectionCoupling and Relimitation, "
                    "and measured SectionCoupling 0/1/2/3 all give the same volume). "
                    "BUILD SENSITIVITY: reported failing on V5-6R2022 (B32) with "
                    "'UpdateObject failed' even for two coaxial equal-radius circles "
                    "whose pair catia_gsd_blend accepts in the same session "
                    "(reported 2026-10-23, 7 configurations, one B32 SP6 session). "
                    "Two later B32 SP6 sessions built both the smooth and the ruled "
                    "loft and matched B30 to 0.02 mm3 (2026-10-24), so treat this as "
                    "SESSION-sensitive, not build-determined: if it fails, retry once "
                    "in a fresh session before working around it, and prefer "
                    "catia_gsd_blend for the same section pair in the meantime."
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
                    "ThickSurface). The solid is created in the active body."
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

        for hb in self._iter_geosets(part.HybridBodies):
            for collection in ("HybridShapes", "HybridSketches"):
                try:
                    return getattr(hb, collection).Item(name)
                except Exception:
                    pass

        bodies = part.Bodies
        for i in range(1, bodies.Count + 1):
            try:
                return bodies.Item(i).Sketches.Item(name)
            except Exception:
                pass

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
        else:
            message += " Use catia_gsd_list_elements to see available names."
        raise RuntimeError(message)

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
                raise RuntimeError(
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
        part = self.conn.get_active_part()
        geoset = self._get_geoset()
        geoset.AppendHybridShape(shape)
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
        raise RuntimeError(
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
        points = args["points"]
        closed = args.get("closed", False)

        refs = [self._point_ref(p) for p in points]
        spline = self._factory().AddNewSpline()
        for ref in refs:
            spline.AddPoint(ref)
        if closed:
            spline.SetClosing(1)

        return self._finish(
            spline, args.get("name"),
            f"3D spline created through {len(points)} points{' (closed)' if closed else ''}.",
        )

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

        guides_msg = f" with {len(guides)} guide(s)" if guides else ""
        try:
            return self._finish(
                loft, args.get("name"),
                f"Multi-sections surface created through {len(sections)} sections{guides_msg}.",
            )
        except RuntimeError as e:
            raise RuntimeError(
                f"{e} Common causes: a guide that does not intersect every section "
                f"(check with measurements), or a section whose curve direction "
                f"opposes the others (flip it with orientations=-1). If neither "
                f"applies and the sections are plainly lofts-able, retry once in a "
                f"fresh session: one B32 SP6 session reported every loft failing "
                f"while catia_gsd_blend accepted the same curve pair (2026-10-23), "
                f"and two other B32 SP6 sessions then built both the smooth and the "
                f"ruled loft and matched B30 to 0.02 mm3 (2026-10-24). If it still "
                f"fails, build the transition with two blends."
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
            return self._finish(
                blends[0], name,
                f"Ruled surface created between {len(sections)} sections "
                "(straight sides, not an interpolated loft).",
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
        return self._finish(
            fill, args.get("name"),
            f"Fill surface created from {len(boundaries)} boundary curve(s).",
        )

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
        self._safe_update(thick)
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
