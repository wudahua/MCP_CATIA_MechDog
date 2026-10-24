"""Sketcher tools for CATIA V5.

2D sketch creation and editing: lines, circles, rectangles, arcs, splines, constraints.
All dimensions are in millimeters. CATIA COM API uses millimeters natively.
"""

from __future__ import annotations

import json
from typing import Any

from catia_mcp.connection import CATIAConnection
from catia_mcp.topo_search import topology_reference

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
                                "guess cuts nothing rather than failing."
                            ),
                            "oneOf": [
                                {
                                    "type": "string",
                                    "enum": ["xy", "yz", "zx"],
                                },
                                {
                                    "type": "object",
                                    "properties": {
                                        "type": {
                                            "type": "string",
                                            "enum": ["offset", "face"],
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
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
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
                    "Creates 4 lines forming a closed profile. Coordinates in mm."
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
                "name": "catia_sketch_arc",
                "description": (
                    "Draw a circular arc defined by center, radius, and start/end angles (degrees). "
                    "Angles are measured counter-clockwise from the positive X axis."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "cx": {"type": "number", "description": "Center X (mm)"},
                        "cy": {"type": "number", "description": "Center Y (mm)"},
                        "radius": {"type": "number", "description": "Radius (mm)"},
                        "start_angle": {"type": "number", "description": "Start angle (degrees)"},
                        "end_angle": {"type": "number", "description": "End angle (degrees)"},
                    },
                    "required": ["cx", "cy", "radius", "start_angle", "end_angle"],
                },
            },
            {
                "name": "catia_sketch_spline",
                "description": (
                    "Draw a spline through a list of control points. "
                    "Each point is [x, y] in mm."
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
                                "fails at update."
                            ),
                            "default": False,
                        },
                    },
                    "required": ["points"],
                },
            },
            {
                "name": "catia_sketch_point",
                "description": "Create a point in the active sketch. Coordinates in mm.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number", "description": "X coordinate (mm)"},
                        "y": {"type": "number", "description": "Y coordinate (mm)"},
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
                    "absolute axis and cannot be deleted."
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
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        match tool_name:
            case "catia_create_sketch":
                return self._create_sketch(
                    arguments.get("plane", "xy"), arguments.get("offset")
                )
            case "catia_close_sketch":
                return self._close_sketch()
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
            case "catia_sketch_arc":
                return self._draw_arc(
                    arguments["cx"], arguments["cy"], arguments["radius"],
                    arguments["start_angle"], arguments["end_angle"],
                )
            case "catia_sketch_spline":
                return self._draw_spline(
                    arguments["points"], arguments.get("closed", False),
                )
            case "catia_sketch_point":
                return self._draw_point(arguments["x"], arguments["y"])
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
            case _:
                raise ValueError(f"Unknown sketcher tool: {tool_name}")

    def _ensure_sketch_open(self) -> None:
        if self._active_sketch is None:
            raise RuntimeError(
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
    def _axis_note(cls, plane: Any) -> str:
        if isinstance(plane, str) and plane.lower() in cls._AXES:
            return "H/V/normal: " + cls._AXES[plane.lower()]
        if isinstance(plane, dict):
            kind = str(plane.get("type", "")).lower()
            if kind == "face":
                return ("H/V/normal: the face's own frame, which automation cannot "
                        "read back - prefer an offset datum plane")
            base = str(plane.get("base", "")).lower()
            if base in cls._AXES:
                return "H/V/normal: " + cls._AXES[base]
        return "H/V/normal: unknown for this support (see catia_sketch_get_geometry)"

    def _describe_plane(self, plane_key: str, offset: float | None) -> str:
        where = self._PLANE_LABELS.get(plane_key, plane_key)
        if offset is not None and abs(offset) > 1e-9:
            where += f" offset {offset:g} mm"
        return where

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
            if key not in PLANE_MAP:
                raise ValueError(f"Unknown plane '{plane}'. Use 'xy', 'yz', or 'zx'.")
            return (
                self._reference_plane(part, body, key, offset),
                self._describe_plane(key, offset),
            )

        if not isinstance(plane, dict):
            raise ValueError(
                "plane must be 'xy'/'yz'/'zx' or an object with a 'type' of "
                f"'offset' or 'face', got {type(plane).__name__}."
            )

        kind = str(plane.get("type", "")).strip().lower()

        if kind == "face":
            index = plane.get("index")
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError(
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
                raise ValueError(
                    f"Unknown offset base '{plane.get('base')}'. Use 'xy', 'yz', or 'zx'."
                )
            distance = plane.get("distance", plane.get("offset", 0))
            if isinstance(distance, bool) or not isinstance(distance, (int, float)):
                raise ValueError(
                    "plane of type 'offset' needs a numeric 'distance' in mm "
                    f"(it may be negative), got {distance!r}."
                )
            distance = float(distance)
            return (
                self._reference_plane(part, body, base, distance),
                self._describe_plane(base, distance),
            )

        raise ValueError(
            f"Unknown plane type {plane.get('type')!r}. Use 'offset', 'face', or "
            "pass plane='xy' | 'yz' | 'zx'."
        )

    def _create_sketch(self, plane: Any = "xy", offset: float | None = None) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self.conn.get_active_part_body()

        ref, where = self._resolve_support(part, body, plane, offset)

        # Create the sketch on the plane
        sketches = body.Sketches
        sketch = sketches.Add(ref)

        # Open the sketch for editing
        self._active_sketch = sketch
        self._sketch_part = part
        self._active_factory = sketch.OpenEdition()
        # Remember it by identity: body.Sketches is ordered by tree position,
        # not by creation, so a later feature cannot find "the sketch I just
        # made" by index. Recording it here is what makes pad/pocket/shallow
        # features land on the intended profile in a multi-sketch part.
        self.conn.last_sketch = sketch
        self._profile_ends = []

        return (f"Sketch '{sketch.Name}' created on {where}. "
                f"{self._axis_note(plane)}. Ready for geometry.")

    # What counts as "the same endpoint" and what counts as a gap that will
    # still break a pad. A 3.5 um gap did (reported 2026-10-23), so the second
    # number is deliberately generous.
    _END_TOL = 1e-4
    _NEAR_TOL = 0.05

    def _note_ends(self, *points: tuple[float, float]) -> None:
        self._profile_ends.extend((float(x), float(y)) for x, y in points)

    def _note_element(self, kind: str, **params: Any) -> None:
        """Remember what was drawn, for the profile echo at close time."""
        try:
            name = str(self._active_sketch.Name)
        except Exception:  # noqa: BLE001 - the record is still useful
            name = "?"
        self._drawn.setdefault(name, []).append({"kind": kind, **params})

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
        drawn = self._drawn.get(sketch_name, []) if sketch_name else []
        if not ends and not drawn:
            return ""
        counts: dict[str, int] = {}
        length = 0.0
        for item in drawn:
            counts[item["kind"]] = counts.get(item["kind"], 0) + 1
            if item["kind"] == "line":
                length += math.hypot(item["x2"] - item["x1"], item["y2"] - item["y1"])
            elif item["kind"] in ("arc", "circle"):
                length += item["radius"] * abs(item["sweep_rad"])
            elif item["kind"] == "rectangle":
                length += 2 * (abs(item["x2"] - item["x1"])
                               + abs(item["y2"] - item["y1"]))
            elif item["kind"] == "spline":
                length += item.get("chord", 0.0)
        parts = []
        if counts:
            parts.append(", ".join(f"{n} {kind}(s)"
                                   for kind, n in sorted(counts.items())))
        if ends:
            xs = [p[0] for p in ends]
            ys = [p[1] for p in ends]
            parts.append("bbox (%.4f, %.4f) .. (%.4f, %.4f)"
                         % (min(xs), min(ys), max(xs), max(ys)))
        if length:
            parts.append("outline %.3f mm" % length)
        return " Profile: " + "; ".join(parts) + "." if parts else ""

    def _profile_warning(self) -> str:
        """Name the near-miss endpoints that will make pad/pocket fail.

        The gap itself is invisible in CATIA's error ("open polyline or an arc
        chain with a gap"), and the coordinates cannot be read back afterwards,
        so the warning is computed from the numbers the sketch tools were given.
        Only near misses are reported: a genuinely open profile (a sweep guide,
        say) is not a mistake and would otherwise generate noise on every close.
        """
        import math

        ends = self._profile_ends
        if len(ends) < 2:
            return ""
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
        best: tuple[float, tuple[float, float], tuple[float, float]] | None = None
        for i in range(len(loose)):
            for j in range(i + 1, len(loose)):
                gap = math.hypot(loose[i][0] - loose[j][0], loose[i][1] - loose[j][1])
                if best is None or gap < best[0]:
                    best = (gap, loose[i], loose[j])
        if best is None or best[0] > self._NEAR_TOL:
            return ""
        gap, a, b = best
        return (
            f" WARNING: the profile is not closed - ({a[0]:.4f}, {a[1]:.4f}) and "
            f"({b[0]:.4f}, {b[1]:.4f}) are {gap:.4f} mm apart. A gap this small "
            "still makes pad/pocket fail with 'open polyline or an arc chain with "
            "a gap'; make the two coordinates identical. (Computed from the "
            "coordinates passed to the sketch tools; CATIA's own points cannot be "
            "read back through automation, so a constraint may have moved them.)"
        )

    def _close_sketch(self) -> str:
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
        warning = self._profile_warning()
        self._profile_ends = []
        return (
            f"Sketch '{name}' closed. You can now apply Part Design features "
            "(pad, pocket, user pattern on its points, etc.)."
            + summary + warning
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
        self._note_element("circle", cx=cx, cy=cy, radius=radius,
                           sweep_rad=2 * math.pi)
        return f"Circle created at ({cx}, {cy}) with radius {radius} mm"

    def _draw_arc(
        self, cx: float, cy: float, radius: float,
        start_angle: float, end_angle: float,
    ) -> str:
        self._ensure_sketch_open()
        factory = self._active_factory
        import math
        # Factory2D has no CreateArc in CATIA V5 (field-verified against the
        # R20 API reference). An arc is an open circle: CreateCircle takes
        # optional start/end parameters in radians that leave it open.
        start_rad = math.radians(start_angle)
        end_rad = math.radians(end_angle)
        # CreateCircle requires endParam strictly greater than startParam;
        # normalize sweeps that wrap past 0 degrees.
        while end_rad <= start_rad:
            end_rad += 2 * math.pi
        factory.CreateCircle(cx, cy, radius, start_rad, end_rad)
        # Remember the arc's own ends, computed from the numbers we were given.
        # Automation cannot read a Point2D's coordinates back on this build
        # (GetCoordinates needs a by-ref array it never fills), so this is the
        # only place the exact endpoint survives - and it is the number that
        # explains a pad failure caused by a 3.5 um gap.
        self._note_ends(
            (cx + radius * math.cos(start_rad), cy + radius * math.sin(start_rad)),
            (cx + radius * math.cos(end_rad), cy + radius * math.sin(end_rad)),
        )
        self._note_element("arc", cx=cx, cy=cy, radius=radius,
                           sweep_rad=end_rad - start_rad)
        return (
            f"Arc created at ({cx}, {cy}), radius={radius} mm, "
            f"from {start_angle}° to {end_angle}°"
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
        chord = sum(math.hypot(points[i + 1][0] - points[i][0],
                               points[i + 1][1] - points[i][1])
                    for i in range(len(points) - 1))
        self._note_element("spline", points=len(points), chord=chord)
        pts_str = ", ".join(f"({p[0]}, {p[1]})" for p in points)
        return f"Spline created through {len(points)} points: {pts_str}" + (
            " (closed into a single curve)" if closed and len(control) > len(points)
            else ""
        )

    def _draw_point(self, x: float, y: float) -> str:
        self._ensure_sketch_open()
        factory = self._active_factory
        point = factory.CreatePoint(x, y)
        self._note_element("point", x=x, y=y)
        # CreatePoint defaults to construction geometry, which user patterns
        # (AddFeatureToLocatePositions) ignore. Field-validated on V5R20.
        try:
            point.Construction = False
        except Exception:
            pass
        return f"Point created at ({x}, {y}) mm"

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
        def _ref(index: int) -> Any:
            return part.CreateReferenceFromObject(geom.Item(index))

        # Dimensional constraints (need a geometry reference + value)
        if constraint_type in ("distance", "radius", "angle"):
            if value is None:
                raise ValueError(f"Constraint type '{constraint_type}' requires a 'value' parameter.")
            if idx1 is None:
                raise ValueError(f"Constraint type '{constraint_type}' requires 'geometry_index_1'.")

            if constraint_type == "distance" and idx2 is not None:
                cst = constraints.AddBiEltCst(self._CST_DISTANCE, _ref(idx1), _ref(idx2))
            elif constraint_type == "distance":
                # A single-element distance is a length dimension in CATIA.
                cst = constraints.AddMonoEltCst(self._CST_LENGTH, _ref(idx1))
            elif constraint_type == "radius":
                cst = constraints.AddMonoEltCst(self._CST_RADIUS, _ref(idx1))
            else:  # angle
                if idx2 is None:
                    raise ValueError("Angle constraint requires 'geometry_index_2'.")
                cst = constraints.AddBiEltCst(self._CST_ANGLE, _ref(idx1), _ref(idx2))
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
            raise ValueError(f"Unknown constraint type: {constraint_type}")

        if constraint_type in ("horizontal", "vertical", "fix"):
            if idx1 is None:
                raise ValueError(f"Constraint '{constraint_type}' requires 'geometry_index_1'.")
            cst = constraints.AddMonoEltCst(cst_code, _ref(idx1))
        else:
            if idx1 is None or idx2 is None:
                raise ValueError(
                    f"Constraint '{constraint_type}' requires both 'geometry_index_1' and 'geometry_index_2'."
                )
            cst = constraints.AddBiEltCst(cst_code, _ref(idx1), _ref(idx2))

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
                names = ", ".join(str(body.Sketches.Item(i).Name)
                                  for i in range(1, body.Sketches.Count + 1))
                raise RuntimeError(
                    f"No sketch named '{sketch_name}' in the active body. "
                    f"Sketches: {names or 'none'}."
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
        names = ", ".join(str(body.Sketches.Item(i).Name)
                          for i in range(1, body.Sketches.Count + 1))
        raise RuntimeError(
            "No sketch is open and none was created in this session, so there is "
            "nothing to default to. Pass sketch_name explicitly. Sketches in the "
            f"active body: {names or 'none'}."
        )

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
            info: dict[str, Any] = {"index": i, "name": str(elem.Name)}
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
                "catia_sketch_delete_element to remove one element."
            ),
        }
        drawn = self._drawn.get(str(sketch.Name))
        if drawn:
            payload["drawn_this_session"] = drawn
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
        sketch, how = self._locate_sketch(sketch_name)
        name = str(sketch.Name)
        was_open = self._active_sketch is sketch
        if not was_open:
            sketch.OpenEdition()
        try:
            geom = sketch.GeometricElements
            if index < 1 or index > geom.Count:
                raise RuntimeError(
                    f"Element index {index} is out of range: sketch '{name}' has "
                    f"{geom.Count} element(s). Use catia_sketch_get_geometry."
                )
            if index == 1:
                raise RuntimeError(
                    "Element 1 is the sketch's absolute axis, not a profile "
                    "element - deleting it would break the sketch. Pick another "
                    "index from catia_sketch_get_geometry."
                )
            element_name = str(geom.Item(index).Name)
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
        finally:
            if not was_open:
                try:
                    sketch.CloseEdition()
                except Exception:  # noqa: BLE001 - best effort
                    pass
        try:
            part.UpdateObject(sketch)
            # UpdateObject(sketch) rebuilds the sketch, not the features built on
            # it: the reporter's pad stayed at the old volume while this reply
            # claimed the profile had been rebuilt (2026-10-24). A full part
            # update is what makes the claim true.
            part.Update()
        except Exception as exc:  # noqa: BLE001 - the delete itself worked
            return (
                f"Deleted element {index} ('{element_name}') from sketch '{name}', "
                f"{left} element(s) left, but the rebuild afterwards failed ({exc}) "
                "- features built on this sketch may be in error; fix them next."
            )
        self.conn.refresh_display()
        return (
            f"Deleted element {index} ('{element_name}') from sketch '{name}': "
            f"{left} element(s) left. Features that consume this sketch were "
            "rebuilt with the new profile."
        )
