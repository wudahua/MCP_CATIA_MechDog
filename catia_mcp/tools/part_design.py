"""Part Design tools for CATIA V5.

3D feature creation: Pad, Pocket, Fillet, Chamfer, Shaft, Groove, Hole,
RectPattern, CircPattern, Mirror, Rib, Slot, Shell, Thickness, Draft.
"""

from __future__ import annotations

import json
import re
from typing import Any

from catia_mcp.connection import CATIAConnection
from catia_mcp.topo_search import (
    nls_features,
    nls_of,
    search_topology,
    topology_cells,
    topology_reference,
    topology_references,
)


class PartDesignTools:
    """Tools for 3D Part Design features in CATIA V5."""

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "catia_pad",
                "description": (
                    "Create a Pad (extrusion) from the last sketch. "
                    "Extrudes a 2D profile into a 3D solid along the normal to the sketch plane."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "height": {
                            "type": "number",
                            "description": "Extrusion height/depth in mm",
                        },
                        "direction": {
                            "type": "string",
                            "description": "Extrusion direction: 'normal' (default), 'reverse', 'both'",
                            "enum": ["normal", "reverse", "both"],
                            "default": "normal",
                        },
                        "symmetric": {
                            "type": "boolean",
                            "description": (
                                "Mirror the pad about the sketch plane. The documented "
                                "contract is that the TOTAL thickness is 'height' "
                                "(each side gets height/2). CATIA's own IsSymmetric is a "
                                "mirrored extent that gives each side the full dimension; "
                                "the tool halves it for you."
                            ),
                            "default": False,
                        },
                        "sketch_name": {
                            "type": "string",
                            "description": "Name of sketch to use. If not specified, uses the last created sketch.",
                        },
                    },
                    "required": ["height"],
                },
            },
            {
                "name": "catia_pocket",
                "description": (
                    "Create a Pocket (cut extrusion) from a sketch. Removes material "
                    "by extruding a 2D profile into the solid. Give either `depth` or "
                    "`through_all`. This is also the tool for a plain cylindrical hole: "
                    "sketch a circle and cut it."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "depth": {
                            "type": "number",
                            "description": "Cut depth in mm. Omit when through_all is true.",
                        },
                        "through_all": {
                            "type": "boolean",
                            "description": (
                                "Cut through the whole solid (CATIA 'up to last'), "
                                "whatever its thickness. Ignored if depth is given."
                            ),
                            "default": False,
                        },
                        "direction": {
                            "type": "string",
                            "description": (
                                "Cut direction: 'normal' (default), 'reverse', "
                                "or 'both' (same as symmetric=true)."
                            ),
                            "enum": ["normal", "reverse", "both"],
                            "default": "normal",
                        },
                        "symmetric": {
                            "type": "boolean",
                            "description": (
                                "Cut equally on both sides of the sketch plane - "
                                "CATIA's 'mid-plane' cut. The documented contract is "
                                "that the TOTAL depth is 'depth' (each side gets "
                                "depth/2); the tool halves the dimension it hands to "
                                "CATIA, whose IsSymmetric is a mirrored extent. This "
                                "is how a spec's 'mid-plane cut 38.1' is meant: the "
                                "slot ends up 38.1 long in total, not 76.2."
                            ),
                            "default": False,
                        },
                        "sketch_name": {
                            "type": "string",
                            "description": "Name of sketch to use. If not specified, uses the last sketch.",
                        },
                    },
                },
            },
            {
                "name": "catia_shaft",
                "description": (
                    "Create a Shaft (solid of revolution) from a sketch. "
                    "The profile must be a closed contour; it is revolved about one "
                    "of the sketch's axes (see `axis`). Use this for stepped shafts, "
                    "pistons, covers - anything whose cross-section is a polygon "
                    "turned 360 degrees, which Pad cannot produce."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "angle": {
                            "type": "number",
                            "description": "Revolution angle in degrees (default: 360 for full revolution)",
                            "default": 360,
                        },
                        "axis": {
                            "type": "string",
                            "enum": ["horizontal", "vertical"],
                            "description": (
                                "Axis to revolve about. 'horizontal' (default) revolves "
                                "about the sketch's H axis (u axis, v=0) and suits a "
                                "profile drawn above it with x = axial, y = radius. "
                                "'vertical' uses the V axis (v axis, u=0). Only the "
                                "sketch's own axes work: CATIA rejects a reference to a "
                                "construction line here (verified)."
                            ),
                            "default": "horizontal",
                        },
                        "sketch_name": {
                            "type": "string",
                            "description": "Name of sketch to use.",
                        },
                    },
                },
            },
            {
                "name": "catia_groove",
                "description": (
                    "Create a Groove (revolution cut), e.g. a piston ring groove. "
                    "Removes material by revolving a closed profile about one of the "
                    "sketch's axes (see `axis`)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "angle": {
                            "type": "number",
                            "description": "Revolution angle in degrees (default: 360)",
                            "default": 360,
                        },
                        "axis": {
                            "type": "string",
                            "enum": ["horizontal", "vertical"],
                            "description": (
                                "Axis to revolve about: 'horizontal' (default) or "
                                "'vertical' - the sketch's own H or V axis."
                            ),
                            "default": "horizontal",
                        },
                        "sketch_name": {
                            "type": "string",
                            "description": "Name of sketch to use.",
                        },
                    },
                },
            },
            {
                "name": "catia_fillet",
                "description": (
                    "Add a fillet (rounded edge) to one or more edges of the current solid. "
                    "Specify the radius and the edge names or feature to fillet. "
                    "Pass 'edge_names' to fillet several edges in one call: each edge "
                    "becomes its own feature, so one bad edge does not lose the rest - "
                    "the reply reports which ones took and why the others did not."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "radius": {
                            "type": "number",
                            "description": "Fillet radius in mm",
                        },
                        "edge_name": {
                            "type": "string",
                            "description": (
                                "Indexed edge to fillet ('Edge.N' from "
                                "catia_list_edges). Omit to fillet all edges "
                                "of the last feature."
                            ),
                        },
                        "edge_names": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Several edges at once, e.g. ['Edge.4','Edge.7']. "
                                "Takes precedence over edge_name."
                            ),
                        },
                        "propagation": {
                            "type": "string",
                            "description": (
                                "'tangency' (default, CATIA's own default) runs the "
                                "fillet along every edge tangent-continuous with the "
                                "picked one - on a smooth chain that can be the whole "
                                "boundary, not the one edge you asked for. 'none' "
                                "fillets exactly the edges given. Use 'none' when a "
                                "spec says 'fillet these 4 edges' and the surface they "
                                "belong to continues smoothly into its neighbours."
                            ),
                            "enum": ["tangency", "none"],
                            "default": "tangency",
                        },
                    },
                    "required": ["radius"],
                },
            },
            {
                "name": "catia_chamfer",
                "description": "Add a chamfer (beveled edge) to an edge of the current solid.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "length": {
                            "type": "number",
                            "description": "Chamfer length in mm",
                        },
                        "angle": {
                            "type": "number",
                            "description": "Chamfer angle in degrees (default: 45)",
                            "default": 45,
                        },
                        "edge_name": {
                            "type": "string",
                            "description": "Indexed edge to chamfer ('Edge.N' from catia_list_edges). Omit to chamfer all edges of the last feature.",
                        },
                        "edge_names": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Several edges at once, e.g. ['Edge.3','Edge.9']. Takes precedence over edge_name.",
                        },
                    },
                    "required": ["length"],
                },
            },
            {
                "name": "catia_hole",
                "description": (
                    "Create a Hole feature at a point in the active sketch. "
                    "Supports simple, tapered, counterbored, and countersunk holes. "
                    "Give either `depth` or `through_all`. Note: on V5-6R2020 B30 a "
                    "finite depth does not cut through automation (verified), so for a "
                    "blind cylindrical hole prefer catia_pocket with a circle sketch."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "diameter": {
                            "type": "number",
                            "description": "Hole diameter in mm",
                        },
                        "depth": {
                            "type": "number",
                            "description": "Hole depth in mm. Omit when through_all is true.",
                        },
                        "through_all": {
                            "type": "boolean",
                            "description": (
                                "Drill through the whole solid (CATIA 'up to last'). "
                                "This is the only depth mode that works through "
                                "automation on this CATIA build."
                            ),
                            "default": False,
                        },
                        "type": {
                            "type": "string",
                            "description": "Hole type: 'simple', 'counterbored', 'countersunk', 'tapered'",
                            "enum": ["simple", "counterbored", "countersunk", "tapered"],
                            "default": "simple",
                        },
                        "threaded": {
                            "type": "boolean",
                            "description": "Whether to add threading (default: false)",
                            "default": False,
                        },
                        "sketch_name": {
                            "type": "string",
                            "description": "Sketch containing the hole center point",
                        },
                    },
                    "required": ["diameter"],
                },
            },
            {
                "name": "catia_rect_pattern",
                "description": (
                    "Create a Rectangular Pattern of the last feature. "
                    "Duplicates a feature in a grid along two directions."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "dir1_edge": {
                            "type": "string",
                            "description": (
                                "Edge giving direction 1 ('Edge.N' from "
                                "catia_list_edges). Pass with dir2_edge, two "
                                "ADJACENT edges sharing a corner (CATIA "
                                "rejects parallel directions). Omit both to "
                                "let CATIA pick defaults."
                            ),
                        },
                        "dir2_edge": {
                            "type": "string",
                            "description": "Edge giving direction 2 ('Edge.N', adjacent to dir1_edge)",
                        },
                        "dir1_count": {
                            "type": "integer",
                            "description": "Number of instances in first direction",
                        },
                        "dir1_spacing": {
                            "type": "number",
                            "description": "Spacing in first direction (mm)",
                        },
                        "dir2_count": {
                            "type": "integer",
                            "description": "Number of instances in second direction (default: 1)",
                            "default": 1,
                        },
                        "dir2_spacing": {
                            "type": "number",
                            "description": "Spacing in second direction (mm)",
                            "default": 0,
                        },
                        "feature_name": {
                            "type": "string",
                            "description": "Name of the feature to pattern. Defaults to last feature.",
                        },
                    },
                    "required": ["dir1_count", "dir1_spacing"],
                },
            },
            {
                "name": "catia_circ_pattern",
                "description": (
                    "Create a Circular Pattern of the last feature. "
                    "Duplicates a feature around a central axis."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "axis": {
                            "type": "string",
                            "enum": ["x", "y", "z"],
                            "description": "Rotation axis direction (default z)",
                        },
                        "center": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": "Point [x, y, z] in mm the rotation axis passes through (default origin)",
                        },
                        "count": {
                            "type": "integer",
                            "description": "Number of instances around the circle",
                        },
                        "angular_spacing": {
                            "type": "number",
                            "description": "Angular spacing in degrees (default: equal spacing = 360/count)",
                        },
                        "feature_name": {
                            "type": "string",
                            "description": "Feature to pattern. Defaults to last feature.",
                        },
                    },
                    "required": ["count"],
                },
            },
            {
                "name": "catia_user_pattern",
                "description": (
                    "Create a User Pattern: copies of a feature placed on the "
                    "points of a sketch (the most flexible pattern, e.g. "
                    "arbitrary bolt layouts). Draw the points with "
                    "catia_sketch_point in a dedicated sketch and close it; "
                    "pass its name from catia_close_sketch. Points must not "
                    "coincide with the seed feature's position."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "count": {
                            "type": "integer",
                            "description": "Number of instances (number of locating points)",
                        },
                        "sketch_name": {
                            "type": "string",
                            "description": "REQUIRED: name of the points sketch as reported by catia_close_sketch (e.g. 'Sketch.3')",
                        },
                        "feature_name": {
                            "type": "string",
                            "description": "Feature to pattern. Defaults to last feature.",
                        },
                    },
                    "required": ["count", "sketch_name"],
                },
            },
            {
                "name": "catia_mirror",
                "description": (
                    "Mirror a feature or body about a plane. "
                    "Creates a symmetric copy of the geometry."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "plane": {
                            "type": "string",
                            "description": "Mirror plane: 'xy', 'yz', or 'zx'",
                            "enum": ["xy", "yz", "zx"],
                        },
                    },
                    "required": ["plane"],
                },
            },
            {
                "name": "catia_shell",
                "description": (
                    "Create a Shell feature: hollows out a solid leaving walls of specified thickness. "
                    "Optionally remove faces to create openings."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "thickness": {
                            "type": "number",
                            "description": "Wall thickness in mm",
                        },
                        "faces_to_remove": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "REQUIRED: indexed faces to remove and open "
                                "('Face.N' from catia_list_faces); the first "
                                "one anchors the shell."
                            ),
                        },
                    },
                    "required": ["thickness"],
                },
            },
            {
                "name": "catia_draft",
                "description": (
                    "Add a Draft Angle to faces for mold-release purposes. "
                    "Tapers faces by a given angle relative to a pulling direction."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "angle": {
                            "type": "number",
                            "description": "Draft angle in degrees",
                        },
                        "face_name": {
                            "type": "string",
                            "description": "REQUIRED: indexed face to draft ('Face.N' from catia_list_faces)",
                        },
                        "pulling_direction": {
                            "type": "string",
                            "description": "Pulling direction plane: 'xy', 'yz', 'zx'",
                            "enum": ["xy", "yz", "zx"],
                            "default": "xy",
                        },
                    },
                    "required": ["angle"],
                },
            },
            {
                "name": "catia_thickness",
                "description": (
                    "Add or remove thickness from faces of a solid. "
                    "Offsets faces inward or outward."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "offset": {
                            "type": "number",
                            "description": "Thickness offset in mm (positive = outward, negative = inward)",
                        },
                        "face_name": {
                            "type": "string",
                            "description": "REQUIRED: indexed face to thicken ('Face.N' from catia_list_faces)",
                        },
                    },
                    "required": ["offset"],
                },
            },
            {
                "name": "catia_delete_feature",
                "description": (
                    "Delete a feature by name (e.g. 'Pocket.2') in any body "
                    "of the active part, then update the part. Features "
                    "depending on it end up in error and are reported. "
                    "With neither argument it deletes the last feature - the usual "
                    "case is undoing the feature you have just created, which is "
                    "otherwise only possible by closing the document."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "feature_name": {
                            "type": "string",
                            "description": "Feature to delete (see catia_list_features)",
                        },
                        "index": {
                            "type": "integer",
                            "description": (
                                "1-based position in the active body's feature list, for when "
                                "the name is not known. Ignored when feature_name is given."
                            ),
                        },
                    },
                },
            },
            {
                "name": "catia_list_features",
                "description": "List all features in the active Part Body with their names and types.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "catia_new_body",
                "description": (
                    "Create a new empty solid body and make it active: every later "
                    "pad/pocket/hole/shaft goes into it. A Part can hold several "
                    "bodies, which is how a spec that declares 'Merge = false' "
                    "(several solids in one part, possibly interpenetrating) is "
                    "reproduced without collapsing them into one volume. Switch "
                    "back with catia_list_bodies + catia_boolean(assemble)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Optional body name (e.g. 'ArmEnd_1')",
                        },
                    },
                },
            },
            {
                "name": "catia_list_bodies",
                "description": (
                    "List every solid body in the active part, with its volume and "
                    "feature count, and mark which one is active for new features."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "catia_set_active_body",
                "description": (
                    "Make an existing solid body the active one, so later pad / pocket / "
                    "hole / fillet features go into it. catia_new_body can only create a "
                    "body and make it active; without this there is no way back to a "
                    "body you left, which makes any multi-body part unbuildable past the "
                    "second body - the drone body is 9 solids. Use catia_list_bodies to "
                    "see the names and which one is currently active."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Body name from catia_list_bodies, e.g. '零件几何体'.",
                        },
                    },
                    "required": ["name"],
                },
            },
            {
                "name": "catia_set_visibility",
                "description": (
                    "Show or hide a tree node: a body, a geometrical set, a single "
                    "surface/curve inside one, or a sketch. "
                    "GSD surfaces stay visible after catia_gsd_close_surface turns them "
                    "into a solid, and they overlay the solid - which reads as a shape "
                    "error rather than the drawing-bookkeeping it is. Hide the "
                    "geometrical set before taking a screenshot or exporting an STL; "
                    "catia_get_bounding_box's mesh method and any STL export include "
                    "visible surfaces, so leftovers also corrupt the measured envelope."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": (
                                "Tree name: a body ('零件几何体'), a geometrical set "
                                "('GSD_Set'), an element inside one ('S1', 'Closed'), "
                                "a feature, or a sketch."
                            ),
                        },
                        "show": {
                            "type": "boolean",
                            "description": "true shows, false hides (default false).",
                            "default": False,
                        },
                    },
                    "required": ["name"],
                },
            },
            {
                "name": "catia_boolean",
                "description": (
                    "Combine two solid bodies. The target keeps the result and the "
                    "tool body is consumed (as CATIA does), so `target_body` is the "
                    "survivor. To keep several solids that may interpenetrate WITHOUT "
                    "fusing them, do not use this at all - leave them in separate "
                    "bodies created by catia_new_body; a part's volume is then the sum "
                    "of its bodies. 'assemble' joins them into one body and does "
                    "deduplicate the overlap, so it is not a substitute for that."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "enum": ["add", "remove", "intersect", "assemble"],
                            "description": (
                                "add = union (overlap counted once), remove = target "
                                "minus tool, intersect = common volume, assemble = "
                                "one body holding both (overlap still counted once, "
                                "so this does NOT preserve a sum-of-solids volume)"
                            ),
                        },
                        "tool_body": {
                            "type": "string",
                            "description": "Body consumed by the operation (from catia_list_bodies)",
                        },
                        "target_body": {
                            "type": "string",
                            "description": (
                                "Body that keeps the result. Defaults to the active "
                                "body; the active body then becomes the target."
                            ),
                        },
                        "name": {
                            "type": "string",
                            "description": "Optional feature name",
                        },
                    },
                    "required": ["operation", "tool_body"],
                },
            },
            {
                "name": "catia_list_edges",
                "description": (
                    "List the edges of the final solid shape as indexed names "
                    "(Edge.N). These indices feed catia_measure_distance; for "
                    "fillet/chamfer targeting see each tool's own contract."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "catia_list_faces",
                "description": (
                    "List the faces of a body's final solid shape as indexed "
                    "names (Face.N) for catia_measure_distance, shell, draft, "
                    "thickness and face-driven drawing views. Defaults to the "
                    "main body; pass body_name to enumerate another body "
                    "(use the same body_name in catia_drawing_add_view)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "body_name": {
                            "type": "string",
                            "description": "Body to enumerate (default: the part's main body)",
                        },
                    },
                },
            },
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        match tool_name:
            case "catia_pad":
                return self._pad(arguments)
            case "catia_pocket":
                return self._pocket(arguments)
            case "catia_shaft":
                return self._shaft(arguments)
            case "catia_groove":
                return self._groove(arguments)
            case "catia_fillet":
                return self._fillet(arguments)
            case "catia_chamfer":
                return self._chamfer(arguments)
            case "catia_hole":
                return self._hole(arguments)
            case "catia_rect_pattern":
                return self._rect_pattern(arguments)
            case "catia_user_pattern":
                return self._user_pattern(arguments)
            case "catia_circ_pattern":
                return self._circ_pattern(arguments)
            case "catia_mirror":
                return self._mirror(arguments)
            case "catia_shell":
                return self._shell(arguments)
            case "catia_draft":
                return self._draft(arguments)
            case "catia_thickness":
                return self._thickness(arguments)
            case "catia_delete_feature":
                return self._delete_feature(arguments)
            case "catia_list_features":
                return self._list_features()
            case "catia_new_body":
                return self._new_body(arguments.get("name"))
            case "catia_list_bodies":
                return self._list_bodies()
            case "catia_set_active_body":
                return self._set_active_body(arguments["name"])
            case "catia_set_visibility":
                return self._set_visibility(
                    arguments["name"], bool(arguments.get("show", False)))
            case "catia_boolean":
                return self._boolean(arguments)
            case "catia_list_faces":
                return self._list_faces(arguments.get("body_name"))
            case "catia_list_edges":
                return self._list_edges()
            case _:
                raise ValueError(f"Unknown part design tool: {tool_name}")

    def _get_last_sketch(self, sketch_name: str | None = None) -> Any:
        """Get a sketch by name, or the most recently created one.

        ``body.Sketches`` is ordered by **tree position, not creation order**.
        On a real part that means ``Item(Count)`` returns some earlier profile:
        a cylinder cap with three sketches came back as 草图.1 / 草图.3 / 草图.2,
        so a pocket aimed at the bolt-hole sketch cut the boss instead, and the
        volume landed exactly on the boss volume. Creation order is therefore
        remembered on the connection and preferred here.
        """
        part = self.conn.get_active_part()
        body = self.conn.get_active_part_body()
        sketches = body.Sketches

        if sketch_name:
            try:
                return sketches.Item(sketch_name)
            except Exception:
                names = ", ".join(str(sketches.Item(i).Name) for i in range(1, sketches.Count + 1))
                raise RuntimeError(
                    f"No sketch named '{sketch_name}' in the active body. "
                    f"Available: {names or 'none'}."
                )

        remembered = getattr(self.conn, "last_sketch", None)
        if remembered is not None:
            try:
                _ = remembered.Name  # still alive?
                return remembered
            except Exception:
                self.conn.last_sketch = None

        if sketches.Count == 0:
            raise RuntimeError("No sketches found in the active body. Create a sketch first.")
        return sketches.Item(sketches.Count)

    def _get_last_shape(self, feature_name: str | None = None) -> Any:
        """Get a shape/feature by name, or the body's final solid.

        The body itself (not ``body.Shapes.Item(last)``) is returned for the
        no-name case: a selection search on the body enumerates the final
        solid's topology, while ``Shapes.Item(last)`` resolves to only the
        last feature's own region (a pocket's cut cylinders, a fillet's
        torus, ...), not the accumulated solid.
        """
        body = self.conn.get_active_part_body()
        shapes = body.Shapes

        if feature_name:
            try:
                return shapes.Item(feature_name)
            except Exception:
                names = ", ".join(str(shapes.Item(i).Name) for i in range(1, shapes.Count + 1))
                raise RuntimeError(
                    f"No feature named '{feature_name}' in the active body. "
                    f"Available: {names or 'none'}."
                )

        if shapes.Count == 0:
            raise RuntimeError("No features found in the active body.")
        return body

    def _clear_selection(self) -> None:
        try:
            self.conn.hso.Clear()
        except Exception:
            pass

    def _prepare_body(self, part: Any) -> Any:
        """Put the solid body back in work and return it.

        Every Part Design factory (AddNewPocket / AddNewFillet / ...) acts on
        the in-work object. GSD calls leave it pointing at the geometrical set
        (``GSD_Set``), and then those factories fail with a bare
        "method failed" COM error that says nothing about the cause - it looks
        like a bad sketch or a bad edge. Field-confirmed on V5-6R2020 B30,
        2026-10-22: after catia_gsd_close_surface, pocket/fillet/chamfer all
        failed until InWorkObject was set back to the body by hand.
        """
        body = self.conn.get_active_part_body()
        try:
            if part.InWorkObject.Name != body.Name:
                part.InWorkObject = body
        except Exception:
            part.InWorkObject = body
        # Rebuild before touching topology: a body whose last feature was added
        # in this same call still hands out stale edge references, and
        # AddNewSolidEdgeFilletWithConstantRadius then fails with a bare
        # "method failed" instead of anything useful. Field-confirmed on
        # V5-6R2020 B30: the identical fillet call succeeds once part.Update()
        # has run.
        try:
            part.Update()
        except Exception:
            pass
        return body

    def _pad(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        sketch = self._get_last_sketch(args.get("sketch_name"))
        height = args["height"]
        direction = args.get("direction", "normal")
        symmetric = args.get("symmetric", False) or direction == "both"

        # CATIA's IsSymmetric is a *mirrored extent*: each side gets the full
        # dimension, so the solid comes out 2x thick. Measured on V5-6R2020 B30
        # (r=10 pad, height=10 -> y -10..+10, 6283.19 mm3). The documented
        # contract here is "total = height", so halve the dimension we hand to
        # CATIA. This bit the drone motor disc: a 31.75 mm disc became 63.5 mm,
        # and the blind hole then fell entirely inside the material and was
        # rejected as an enclosed void.
        pad = sf.AddNewPad(sketch, height / 2.0 if symmetric else height)

        if symmetric:
            pad.IsSymmetric = True
        elif direction == "reverse":
            pad.DirectionOrientation = 1  # catReverse

        part.UpdateObject(pad)
        self.conn.refresh_display()
        span = "total %.3f mm, %0.3f each side" % (height, height / 2.0) if symmetric \
            else "%.3f mm" % height
        return f"Pad created: {span} ({direction}). Feature: '{pad.Name}'"

    def _body_volume_mm3(self, part: Any, body: Any) -> float | None:
        """Measure the body's solid volume in mm3, or None if unavailable."""
        try:
            doc = part.Parent
            try:
                spa = doc.GetWorkbench("SPAWorkbench")
            except AttributeError:
                spa = self.conn.app.GetWorkbench("SPAWorkbench")
            ref = part.CreateReferenceFromObject(body)
            return spa.GetMeasurable(ref).Volume * 1e9  # m3 -> mm3
        except Exception:
            return None

    def _pocket(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        sketch = self._get_last_sketch(args.get("sketch_name"))
        through_all = bool(args.get("through_all")) and "depth" not in args
        depth = args.get("depth", 0)
        reverse = args.get("direction") == "reverse"
        symmetric = bool(args.get("symmetric", False)) or args.get("direction") == "both"

        before = self._body_volume_mm3(part, body)

        # CATIA is not consistent between Pad and Pocket here, measured on
        # V5-6R2020 B30:
        #   Pad + IsSymmetric    -> a mirrored EXTENT, each side gets the full
        #                           dimension (r=10, height=10 -> y -10..+10).
        #   Pocket + IsSymmetric -> accepted and ignored, the cut stays one-sided.
        # Measured on the drone body: a "mid-plane cut 38.1" passed as 19.05 with
        # IsSymmetric removed 5 609.6 mm3, exactly the single-sided 19.05 figure
        # (4 x pi x 4.826^2 x 19.05 = 5 575.4), not the 11 150.9 a 38.1 cut gives.
        # So a mid-plane cut is built as two one-sided cuts, depth/2 each.
        half = depth / 2.0 if symmetric else depth
        pocket = sf.AddNewPocket(sketch, 0 if through_all else half)
        if through_all:
            # catUpToLastLimit: CATIA works out the extent itself, so the cut
            # reaches through whatever thickness the solid has.
            pocket.FirstLimit.LimitMode = 2
        # A pocket cuts on one side of the profile's support plane only, so
        # the orientation is set explicitly in both cases (field-verified:
        # the wrong side produces a tree feature that removes nothing).
        pocket.DirectionOrientation = 1 if reverse else 0
        try:
            part.UpdateObject(pocket)
        except Exception as exc:
            # A through-all cut from a sketch that sits on a solid face can be
            # rejected outright rather than removing nothing (measured: 1 of 6
            # faces of a plain box). Roll the dead feature back and point at the
            # route that does not depend on the face frame.
            self._remove_feature(part, pocket)
            raise RuntimeError(
                "CATIA rejected the pocket and it was removed from the tree. "
                "When through_all is set, this usually means the sketch sits on "
                "a face it cannot cut from - most often a face sketch whose "
                "local coordinates do not land on the material. Put the sketch "
                "on a datum plane with an offset instead, which uses global "
                f"coordinates. Original error: {exc}"
            ) from exc

        explicit_direction = "direction" in args or symmetric
        flipped = False
        after = self._body_volume_mm3(part, body)
        if (
            before is not None
            and after is not None
            and after >= before
            and not explicit_direction
        ):
            # No material removed and the caller did not pin the side: flip
            # and re-verify instead of reporting a false success. An explicit
            # 'direction' is honored as-is and reported honestly below.
            pocket.DirectionOrientation = 0 if reverse else 1
            part.UpdateObject(pocket)
            new_after = self._body_volume_mm3(part, body)
            if new_after is not None and new_after < before:
                after = new_after
                flipped = True
            else:
                # The flip did not help either: restore the requested
                # orientation so the tree reflects the original intent.
                pocket.DirectionOrientation = 1 if reverse else 0
                part.UpdateObject(pocket)
                after = self._body_volume_mm3(part, body)

        self.conn.refresh_display()

        mirror = None
        if symmetric and not through_all:
            # The other half of the mid-plane cut. Same sketch, opposite side.
            mirror = sf.AddNewPocket(sketch, half)
            mirror.DirectionOrientation = 0 if reverse else 1
            part.UpdateObject(mirror)
            new_after = self._body_volume_mm3(part, body)
            if (
                after is not None
                and new_after is not None
                and new_after >= after        # that side was air
            ):
                # A mid-plane cut from a face into open space removes material on
                # one side only. Keep the tree honest: drop the empty feature.
                self._remove_feature(part, mirror)
                mirror = None
            else:
                after = new_after

        if before is not None and after is not None:
            removed = before - after
            if removed <= 0:
                return (
                    f"FEATURE_NO_EFFECT: pocket '{pocket.Name}' was created in "
                    f"the tree but removed no material"
                    + (" in the requested direction" if explicit_direction
                       else " in either direction")
                    + f" (volume stayed {before:.1f} mm3). Check that the "
                    "profile intersects the solid."
                )
            flip_note = " (direction auto-flipped to reach material)" if flipped else ""
            extent = self._pocket_extent(through_all, depth, symmetric)
            names = f"'{pocket.Name}'" if mirror is None else \
                f"'{pocket.Name}' + '{mirror.Name}' (two one-sided cuts)"
            return (
                f"Pocket {names} created: {extent}{flip_note}. "
                f"Removed {removed:.1f} mm3 ({before:.1f} -> {after:.1f})."
            )
        extent = self._pocket_extent(through_all, depth, symmetric)
        return (
            f"Pocket created: {extent}. Feature: '{pocket.Name}' "
            "(volume verification unavailable on this installation)."
        )

    @staticmethod
    def _pocket_extent(through_all: bool, depth: float, symmetric: bool) -> str:
        """Describe the cut in the caller's terms, not CATIA's."""
        if through_all:
            return "through all"
        if symmetric:
            return "%.3f mm total, %.3f each side" % (depth, depth / 2.0)
        return f"{depth} mm deep"

    @staticmethod
    def _sketch_absolute_axis(sketch: Any) -> Any:
        """The sketch's absolute axis element.

        Always item 1 of GeometricElements. Never looked up by name: a Chinese
        build calls it '绝对轴' and Item("AbsoluteAxis") fails with E_FAIL there.
        """
        elements = sketch.GeometricElements
        try:
            return elements.Item(1)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "This sketch exposes no absolute axis, so it cannot be revolved. "
                "Re-create the sketch and try again."
            ) from exc

    def _revolve_axis_reference(self, part: Any, sketch: Any, mode: str) -> Any:
        """Resolve the revolution axis to the Reference CATIA wants.

        Only the sketch's own absolute axes work. A reference to a construction
        line inside the sketch is accepted by the property setter and then
        rejected at update time with E_FAIL, so it is not offered as an option.
        """
        mode = (mode or "horizontal").lower()
        axis = self._sketch_absolute_axis(sketch)
        if mode == "horizontal":
            return axis.HorizontalReference
        if mode == "vertical":
            return axis.VerticalReference
        raise ValueError(f"Unknown axis '{mode}'. Use 'horizontal' or 'vertical'.")

    def _build_revolution(self, part: Any, sketch: Any, angle: float, axis_mode: str,
                          from_ref: str, label: str) -> Any:
        """Create a Shaft/Groove through the ...FromRef constructor.

        AddNewShaft(sketch) looks like the obvious call and always fails at
        UpdateObject: CATIA wants the profile and the axis set explicitly on a
        feature created empty, and FirstAngle is an object with a .Value, not a
        number. Verified against V5-6R2020 B30; see FORK.md.
        """
        factory = part.ShapeFactory
        feature = getattr(factory, from_ref)(part.CreateReferenceFromName(""))
        feature.FirstAngle.Value = angle
        feature.SetProfileElement(part.CreateReferenceFromObject(sketch))
        feature.RevoluteAxis = self._revolve_axis_reference(part, sketch, axis_mode)
        self._safe_update(part, feature)
        self.conn.refresh_display()
        return feature

    def _shaft(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        self._prepare_body(part)
        sketch = self._get_last_sketch(args.get("sketch_name"))
        angle = args.get("angle", 360)
        axis_mode = args.get("axis", "horizontal")

        before = self._body_volume_mm3(part, self.conn.get_active_part_body())
        shaft = self._build_revolution(part, sketch, angle, axis_mode, "AddNewShaftFromRef", "Shaft")
        after = self._body_volume_mm3(part, self.conn.get_active_part_body())

        detail = ""
        if before is not None and after is not None:
            detail = f" Volume {before:.1f} -> {after:.1f} mm3 (added {after - before:.1f})."
        return (
            f"Shaft created: {angle}° about the {axis_mode} axis of '{sketch.Name}'."
            f"{detail} Feature: '{shaft.Name}'"
        )

    def _groove(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        self._prepare_body(part)
        sketch = self._get_last_sketch(args.get("sketch_name"))
        angle = args.get("angle", 360)
        axis_mode = args.get("axis", "horizontal")

        before = self._body_volume_mm3(part, self.conn.get_active_part_body())
        groove = self._build_revolution(part, sketch, angle, axis_mode, "AddNewGrooveFromRef", "Groove")
        after = self._body_volume_mm3(part, self.conn.get_active_part_body())

        detail = ""
        if before is not None and after is not None:
            removed = before - after
            detail = f" Removed {removed:.1f} mm3 ({before:.1f} -> {after:.1f})."
            if abs(removed) < 1e-6:
                detail += " WARNING: nothing was removed - check the profile side of the axis."
        return (
            f"Groove created: {angle}° about the {axis_mode} axis of '{sketch.Name}'."
            f"{detail} Feature: '{groove.Name}'"
        )

    def _remove_feature(self, part: Any, feature: Any) -> None:
        """Remove a feature from the tree, best effort."""
        try:
            selection = self.conn.hso
            selection.Clear()
            selection.Add(feature)
            selection.Delete()
        except Exception:  # noqa: BLE001 - nothing better to do than carry on
            pass

    def _safe_update(self, part: Any, feature: Any) -> None:
        """Update a feature; if CATIA rejects it, remove the broken feature
        from the tree so a failed attempt does not poison later features."""
        try:
            part.UpdateObject(feature)
        except Exception as e:
            self._remove_feature(part, feature)
            raise RuntimeError(
                f"CATIA rejected the feature (update failed) and it was "
                f"removed from the tree: {e}"
            ) from e

    def _topo_reference(self, kind: str, index: int) -> Any:
        """Resolve Face.N / Edge.N on the body's final shape to a Reference.

        Indices match catia_list_faces / catia_list_edges. The resolution
        itself lives in topo_search so the sketcher can put a sketch on a
        solid face with exactly the same rules.
        """
        body = self.conn.get_active_part_body()
        return topology_reference(self.conn, body, kind, index).reference

    @staticmethod
    def _topo_index(name: str, kind: str) -> int:
        m = re.match(rf"^{kind}\.(\d+)$", name or "")
        if not m:
            raise ValueError(
                f"Expected an indexed name like '{kind}.1' (from "
                f"catia_list_faces / catia_list_edges), got '{name}'."
            )
        return int(m.group(1))

    def _fillet(self, args: dict[str, Any]) -> str:
        names = args.get("edge_names") or []
        if isinstance(names, str):
            names = [names]
        if len(names) > 1 or (names and not args.get("edge_name")):
            return self._fillet_many(args["radius"], list(names),
                                     self._propagation(args))
        return self._fillet_one(args["radius"], args.get("edge_name"),
                                self._propagation(args))

    @staticmethod
    def _propagation(args: dict[str, Any]) -> int:
        """CATIA edge-propagation enum: 0 = only the given edges, 1 = tangency."""
        return 0 if args.get("propagation") == "none" else 1

    def _fillet_many(self, radius: float, names: list[str],
                     propagation: int = 1) -> str:
        """One feature per edge, built as a batch and rebuilt once.

        Two measured facts drive the shape of this method.

        1. Every reference is resolved BEFORE the first feature is created.
           Each fillet renumbers the body's edges, so a loop that re-resolves
           "Edge.N" between cuts ends up pointing at the tangent edge the
           previous fillet just created - which is no longer sharp, and CATIA
           answers with an update diagnostic ("某些选定的边线不是锐边") instead of
           an error. Measured on the drone motor disc: the two Ø69.85 circles
           and the fillet's tangent edge are all 219.440 mm long, so neither
           length nor type can tell them apart afterwards.

        2. The whole batch is rebuilt ONCE, at the end. Updating each fillet as
           it is created invalidates the references still waiting in the queue:
           on the drone body, 12 edges at R5.08 gave "1/12 done" and then every
           remaining factory call failed with a bare "method failed", because
           the first update had rebuilt the topology the other 11 referenced.
           This is also how the CATIA UI works - select many edges, one feature.
        """
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        indices = [self._topo_index(name, "Edge") for name in names]
        # One search, all references, selection kept alive - a reference dies
        # with the selection that produced it.
        refs = [r.reference for r in
                topology_references(self.conn, body, "Edge", indices)]

        def add(ref: Any) -> Any:
            return sf.AddNewSolidEdgeFilletWithConstantRadius(ref, propagation, radius)

        try:
            return self._apply_fillets(radius, names, refs, add, part=part)
        finally:
            self._clear_selection()
            self.conn.refresh_display()

    def _apply_fillets(self, radius: float, names: list[str], refs: list[Any],
                       add: Any = None, part: Any = None,
                       label: str = "Fillet R") -> str:
        """Create one fillet per (name, ref), then rebuild once.

        ``add`` is injectable for tests; it may return the new feature (in which
        case a failed rebuild rolls it back) or None.
        """
        done, failed, created = [], [], []
        for name, ref in zip(names, refs):
            try:
                feature = add(ref)
                done.append(name)
                if feature is not None:
                    created.append(feature)
            except Exception as error:        # noqa: BLE001 - reported per edge
                failed.append((name, str(error).splitlines()[0][:120]))

        rebuild_error = None
        if created and part is not None:
            try:
                part.Update()
            except Exception as error:        # noqa: BLE001 - reported below
                rebuild_error = str(error).splitlines()[0][:120]

        shown = f"{label}{radius}" if label.endswith("R") else f"{label} {radius}"
        head = f"{shown}: {len(done)}/{len(names)} edge(s) done"
        if rebuild_error:
            # The features exist but the body no longer rebuilds: that is a
            # failure, not a partial success, so say so and undo it.
            self._remove_created(created)
            head = (f"{shown}: 0/{len(names)} edge(s) done - CATIA "
                    f"created {len(done)} feature(s) but the body would not "
                    f"rebuild ({rebuild_error}). They were removed.")
            done, failed = [], failed or [(n, "batch rebuild failed") for n in names]
        if done:
            head += f" ({', '.join(done)})"
        if failed:
            head += ". Rejected: " + "; ".join(f"{n} -> {m}" for n, m in failed)
            head += (
                ". A refusal here is usually the topology, not the argument: edges "
                "that came out of catia_gsd_close_surface cannot take a Part Design "
                "fillet, while edges of a pad in the same body can."
            )
        return head

    def _fillet_one(self, radius: float, edge_name: str | None,
                    propagation: int = 1) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        self._prepare_body(part)
        sf = part.ShapeFactory

        if edge_name:
            target = self._topo_reference("Edge", self._topo_index(edge_name, "Edge"))
            scope = edge_name
        else:
            # Whole-feature fallback: a Reference to the feature fillets its
            # edges with tangency propagation.
            target = part.CreateReferenceFromObject(self._get_last_shape())
            scope = "all edges of the last feature"

        fillet = sf.AddNewSolidEdgeFilletWithConstantRadius(
            target,
            propagation,   # 0 = only these edges, 1 = catTangencyFilletEdgePropagation
            radius,
        )

        part.UpdateObject(fillet)
        self.conn.refresh_display()
        self._clear_selection()
        return f"Fillet created: R{radius} mm on {scope}. Feature: '{fillet.Name}'"

    def _chamfer(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        names = args.get("edge_names") or []
        if isinstance(names, str):
            names = [names]
        if len(names) > 1 or (names and not args.get("edge_name")):
            return self._chamfer_many(args["length"], args.get("angle", 45), list(names))
        return self._chamfer_one(args["length"], args.get("angle", 45), args.get("edge_name"))

    def _chamfer_many(self, length: float, angle: float, names: list[str]) -> str:
        """Same batching rule as ``_fillet_many``: resolve all, build all, rebuild once."""
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        indices = [self._topo_index(name, "Edge") for name in names]
        refs = [r.reference for r in
                topology_references(self.conn, body, "Edge", indices)]

        def add(ref: Any) -> Any:
            return sf.AddNewChamfer(
                ref,
                1,       # catTangencyChamferPropagation
                0,       # catLengthAngleChamfer mode
                0,       # catNoReverseChamfer orientation
                length,
                angle,
            )

        try:
            return self._apply_fillets(length, names, refs, add, part=part,
                                       label="Chamfer")
        finally:
            self._clear_selection()
            self.conn.refresh_display()

    def _remove_created(self, features: list[Any]) -> None:
        """Delete features that were created but left the body unbuildable."""
        sel = self.conn.hso
        for feature in features:
            try:
                sel.Clear()
                sel.Add(feature)
                sel.Delete()
            except Exception:  # noqa: BLE001 - best effort cleanup
                pass
        try:
            sel.Clear()
        except Exception:
            pass

    def _chamfer_one(self, length: float, angle: float, edge_name: str | None) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        self._prepare_body(part)
        sf = part.ShapeFactory

        if edge_name:
            target = self._topo_reference("Edge", self._topo_index(edge_name, "Edge"))
            scope = edge_name
        else:
            target = part.CreateReferenceFromObject(self._get_last_shape())
            scope = "all edges of the last feature"

        # AddNewChamfer(iObjectToChamfer, iPropagation, iMode, iOrientation,
        #               iLength1, iLength2OrAngle) - 6 parameters.
        chamfer = sf.AddNewChamfer(
            target,
            1,       # catTangencyChamferPropagation
            0,       # catLengthAngleChamfer mode
            0,       # catNoReverseChamfer orientation
            length,
            angle,
        )

        part.UpdateObject(chamfer)
        self.conn.refresh_display()
        self._clear_selection()
        return f"Chamfer created: {length} mm at {angle} deg on {scope}. Feature: '{chamfer.Name}'"

    def _hole(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        sketch = self._get_last_sketch(args.get("sketch_name"))
        diameter = args["diameter"]
        through_all = bool(args.get("through_all")) and "depth" not in args
        depth = args.get("depth", 0)
        before = self._body_volume_mm3(part, body)

        # AddNewHole takes a support FACE reference; the sketch-positioned
        # form is AddNewHoleFromSketch. Diameter is a read-only Length
        # parameter object whose .Value is assigned.
        hole = sf.AddNewHoleFromSketch(sketch, 0 if through_all else depth)
        hole.Diameter.Value = diameter
        hole.BottomType = 0  # catFlatBottom
        if through_all:
            # BottomLimit is the hole's depth control (FirstLimit is the
            # pocket's). LimitMode 2 = catUpToLastLimit.
            hole.BottomLimit.LimitMode = 2

        hole_type = args.get("type", "simple")
        type_map = {"simple": 0, "tapered": 1, "counterbored": 2, "countersunk": 3}
        type_note = ""
        if hole_type in type_map and hole_type != "simple":
            try:
                hole.Type = type_map[hole_type]
                type_note = (
                    f" Type: {hole_type} (head dimensions keep CATIA "
                    "defaults; adjust in the tree if needed)."
                )
            except Exception:
                type_note = f" Type '{hole_type}' not applied (API rejected it); simple hole created."

        if args.get("threaded", False):
            hole.ThreadingMode = 1  # catThreaded

        part.UpdateObject(hole)
        self.conn.refresh_display()

        after = self._body_volume_mm3(part, body)
        extent = "through all" if through_all else f"depth {depth} mm"
        if before is not None and after is not None:
            removed = before - after
            if removed <= 0:
                # A tree feature that removes nothing is worse than an error:
                # it looks like success and poisons every later measurement.
                # Roll it back and say what actually happened.
                self._remove_feature(part, hole)
                if through_all:
                    raise RuntimeError(
                        f"The hole D{diameter} removed no material even with "
                        f"through_all. Check that the sketch point sits inside the "
                        f"solid and that its plane faces the material."
                    )
                raise RuntimeError(
                    f"A finite-depth hole D{diameter} x {depth} mm cuts nothing on "
                    f"this CATIA build (V5-6R2020 B30): the depth is stored but not "
                    f"applied by the Hole feature through automation. The hole was "
                    f"rolled back. Use catia_pocket with a circle sketch of the same "
                    f"diameter instead - that is how the spec itself builds these "
                    f"holes, and it can be patterned afterwards."
                )
            return (
                f"Hole created: D{diameter} mm, {extent}. Removed {removed:.1f} mm3 "
                f"({before:.1f} -> {after:.1f}). Feature: '{hole.Name}'.{type_note}"
            )
        return f"Hole created: D{diameter} mm, {extent}. Feature: '{hole.Name}'.{type_note}"

    def _rect_pattern(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        feature = self._get_last_shape(args.get("feature_name"))
        d1_count = args["dir1_count"]
        d1_spacing = args["dir1_spacing"]
        d2_count = args.get("dir2_count", 1)
        d2_spacing = args.get("dir2_spacing", 0)

        # AddNewRectPattern takes 12 parameters. Field-proven on V5R20:
        # topology edge references (Selection.Item(i).Reference) are accepted
        # as direction references, and CATIA rejects two PARALLEL directions,
        # so pass two ADJACENT edges (sharing a corner). Without explicit
        # edges, the origin planes give deterministic X/Y directions.
        dir1_edge = args.get("dir1_edge")
        dir2_edge = args.get("dir2_edge")
        if bool(dir1_edge) != bool(dir2_edge):
            raise ValueError(
                "Pass both 'dir1_edge' and 'dir2_edge' ('Edge.N' from "
                "catia_list_edges, two adjacent edges sharing a corner), "
                "or neither."
            )
        if dir1_edge:
            r1, r2 = topology_references(
                self.conn, body, "Edge",
                [self._topo_index(dir1_edge, "Edge"),
                 self._topo_index(dir2_edge, "Edge")])
            ref1, ref2 = r1.reference, r2.reference
            directions = f"{dir1_edge} -> dir1, {dir2_edge} -> dir2"
        else:
            # Field-proven default on V5R20 (measured 2x2 boss grid): the
            # origin planes give deterministic directions, PlaneYZ -> X
            # and PlaneZX -> Y, through CreateReferenceFromObject.
            planes = self.conn.get_origin_elements()
            ref1 = part.CreateReferenceFromObject(planes["yz"])
            ref2 = part.CreateReferenceFromObject(planes["zx"])
            directions = "origin PlaneYZ -> dir1 (X), PlaneZX -> dir2 (Y)"

        before = self._body_volume_mm3(part, body)
        part.InWorkObject = body
        pattern = sf.AddNewRectPattern(
            feature,
            d1_count, d2_count,
            d1_spacing, d2_spacing,
            1, 1,          # position of the original along dir1/dir2
            ref1, ref2,    # direction references
            False, False,  # reversed dir1/dir2
            0.0,           # rotation angle
        )

        self._safe_update(part, pattern)
        self.conn.refresh_display()
        after = self._body_volume_mm3(part, body)
        effect = ""
        if before is not None and after is not None:
            effect = f" Volume {before:.1f} -> {after:.1f} mm3."
        return (
            f"Rectangular pattern '{pattern.Name}' created: {d1_count}x{d2_count}, "
            f"spacing {d1_spacing}x{d2_spacing} mm, directions: {directions}."
            + effect
        )

    def _mint_axis_reference(self, axis: str, center: list[float]) -> Any:
        """Build a rotation-axis Reference from a GSD line in a throwaway
        donor document.

        Field-proven on V5R20: AddNewCircPattern fails with 0x80020009 when
        the patterned document itself contains GSD geometry, but a Reference
        minted from a line in a separate document is accepted and survives
        closing that document.
        """
        app = self.conn.app
        target_doc = app.ActiveDocument
        target_name = str(target_doc.Name)
        previous_alerts = None
        try:
            previous_alerts = app.DisplayFileAlerts
            app.DisplayFileAlerts = False
        except Exception:
            pass
        donor_doc = app.Documents.Add("Part")
        try:
            donor = self.conn.part_of(donor_doc)
            hsf = donor.HybridShapeFactory
            geoset = donor.HybridBodies.Add()
            dx, dy, dz = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[axis]
            cx, cy, cz = center
            p1 = hsf.AddNewPointCoord(cx - 50 * dx, cy - 50 * dy, cz - 50 * dz)
            geoset.AppendHybridShape(p1)
            p2 = hsf.AddNewPointCoord(cx + 50 * dx, cy + 50 * dy, cz + 50 * dz)
            geoset.AppendHybridShape(p2)
            line = hsf.AddNewLinePtPt(
                donor.CreateReferenceFromObject(p1),
                donor.CreateReferenceFromObject(p2),
            )
            geoset.AppendHybridShape(line)
            donor.Update()
            # Both references are minted from the donor, exactly as the
            # validated recipe does.
            axis_ref = donor.CreateReferenceFromObject(line)
            center_ref = donor.CreateReferenceFromName("")
            return axis_ref, center_ref
        finally:
            try:
                donor_doc.Close()
            except Exception:
                pass
            try:
                target_doc.Activate()
            except Exception:
                pass
            if previous_alerts is not None:
                try:
                    app.DisplayFileAlerts = previous_alerts
                except Exception:
                    pass
            try:
                if str(app.ActiveDocument.Name) != target_name:
                    raise RuntimeError(
                        f"Could not re-activate '{target_name}' after the "
                        "donor round trip; aborting before patterning the "
                        "wrong document."
                    )
            except RuntimeError:
                raise
            except Exception:
                pass

    def _circ_pattern(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        count = int(args["count"])
        if count < 1:
            raise ValueError("count must be at least 1")
        angular_spacing = args.get("angular_spacing")
        if angular_spacing is None:
            angular_spacing = 360.0 / count
        axis = (args.get("axis") or "z").lower()
        if axis not in ("x", "y", "z"):
            raise ValueError("axis must be 'x', 'y' or 'z'")
        center = args.get("center") or [0.0, 0.0, 0.0]
        if len(center) != 3:
            raise ValueError("center must be [x, y, z] in mm")
        # Field-proven precondition: AddNewCircPattern fails with 0x80020009
        # when the patterned document itself contains GSD geometry.
        try:
            geosets = part.HybridBodies.Count
        except Exception:
            geosets = 0
        if geosets:
            raise RuntimeError(
                "catia_circ_pattern cannot run on a part that contains "
                "geometrical sets (GSD geometry): CATIA rejects the pattern "
                "with 0x80020009 in that case. Remove the geometrical sets "
                "or pattern a GSD-free part. "
                "If what you need is a second instance rotated by a fixed angle, "
                "you can get it without a pattern: a rotation of 180 deg equals "
                "mirroring about two perpendicular planes, so two "
                "catia_gsd_symmetry calls (about 'xy' then about 'yz') followed by "
                "catia_gsd_close_surface reproduce the rotated solid exactly - "
                "verified on a two-blade propeller, 0.6 mm3 off the analytic volume "
                "with the centroid landing exactly on the axes."
            )
        self._get_last_shape(args.get("feature_name"))  # validate early

        axis_ref, center_ref = self._mint_axis_reference(axis, center)
        # Re-resolve after the donor round trip: the active document changed
        # and came back.
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory
        feature = self._get_last_shape(args.get("feature_name"))

        before = self._body_volume_mm3(part, body)
        part.InWorkObject = body
        # AddNewCircPattern takes 12 parameters; instances around the axis
        # belong in the ANGULAR slot (3rd). Flags as field-validated
        # (4 bosses at radius 40, angles 0/90/180/270 on V5R20).
        pattern = sf.AddNewCircPattern(
            feature,
            1, count,              # radial copies, angular copies
            0.0, angular_spacing,  # radial step, angular step (deg)
            1, 1,                  # position of the original
            center_ref, axis_ref,  # rotation center (from axis line), axis
            False,                 # reversed rotation axis
            0.0,                   # rotation angle
            False,                 # radius aligned
        )

        self._safe_update(part, pattern)
        self.conn.refresh_display()
        after = self._body_volume_mm3(part, body)
        effect = ""
        if before is not None and after is not None:
            effect = f" Volume {before:.1f} -> {after:.1f} mm3."
        return (
            f"Circular pattern '{pattern.Name}' created: {count} instances, "
            f"{angular_spacing} deg spacing around the {axis.upper()} axis "
            f"through {center}." + effect
        )

    def _user_pattern(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        feature = self._get_last_shape(args.get("feature_name"))
        count = int(args["count"])
        if count < 1:
            raise ValueError("count must be at least 1")
        sketch_name = args.get("sketch_name")
        if not sketch_name:
            # Field-proven trap: the last item of body.Sketches is stale right
            # after CloseEdition and resolves to the previous sketch, so the
            # points sketch must be addressed by name.
            raise ValueError(
                "catia_user_pattern requires 'sketch_name': the points sketch "
                "name reported by catia_close_sketch (e.g. 'Sketch.3')."
            )
        sketch = self._get_last_sketch(sketch_name)

        before = self._body_volume_mm3(part, body)
        part.InWorkObject = body
        # Field-proven on V5R20: AddNewUserPattern(shape, n) then
        # AddFeatureToLocatePositions(points sketch). A locating point that
        # coincides with the seed makes the update fail (0x80020009).
        pattern = sf.AddNewUserPattern(feature, count)
        pattern.AddFeatureToLocatePositions(sketch)
        self._safe_update(part, pattern)
        self.conn.refresh_display()
        after = self._body_volume_mm3(part, body)
        effect = ""
        if before is not None and after is not None:
            effect = f" Volume {before:.1f} -> {after:.1f} mm3."
        return (
            f"User pattern '{pattern.Name}' created: {count} instances placed "
            f"on the points of sketch '{sketch.Name}'." + effect
        )

    def _mirror(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        plane_key = args["plane"].lower()
        planes = self.conn.get_origin_elements()
        if plane_key not in planes:
            raise ValueError(f"Unknown plane '{plane_key}'. Use 'xy', 'yz', or 'zx'.")

        mirror_plane = planes[plane_key]
        ref = part.CreateReferenceFromObject(mirror_plane)

        # AddNewMirror takes only the plane reference and mirrors the
        # body's existing shapes; per-feature mirroring is not expressible
        # through this API.
        mirror = sf.AddNewMirror(ref)

        part.UpdateObject(mirror)
        self.conn.refresh_display()
        return (
            f"Mirror created about {plane_key.upper()} plane (mirrors the "
            f"body's shapes). Feature: '{mirror.Name}'"
        )

    def _shell(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        thickness = args["thickness"]
        faces = args.get("faces_to_remove") or []
        if not faces:
            raise ValueError(
                "catia_shell requires 'faces_to_remove' with at least one "
                "indexed face name ('Face.N' from catia_list_faces): "
                "AddNewShell's first parameter is the face to remove."
            )
        refs = [
            r.reference
            for r in topology_references(
                self.conn, body, "Face", [self._topo_index(f, "Face") for f in faces])
        ]
        # AddNewShell(iFaceToRemove, iInternalThickness, iExternalThickness)
        shell = sf.AddNewShell(refs[0], thickness, 0)
        skipped = []
        for extra, ref in zip(faces[1:], refs[1:]):
            try:
                shell.AddFaceToRemove(ref)
            except Exception:
                skipped.append(extra)

        part.UpdateObject(shell)
        self.conn.refresh_display()
        try:
            self.conn.hso.Clear()
        except Exception:
            pass
        note = f" (could not add: {', '.join(skipped)})" if skipped else ""
        return (
            f"Shell created: {thickness} mm wall, removed "
            f"{len(faces) - len(skipped)} face(s){note}. Feature: '{shell.Name}'"
        )

    def _draft(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        angle = args["angle"]
        face_name = args.get("face_name")
        if not face_name:
            raise ValueError(
                "catia_draft requires 'face_name' ('Face.N' from "
                "catia_list_faces): the face to draft."
            )
        face_ref = self._topo_reference("Face", self._topo_index(face_name, "Face"))

        plane_key = args.get("pulling_direction", "xy").lower()
        planes = self.conn.get_origin_elements()
        neutral = planes.get(plane_key)
        if not neutral:
            raise ValueError(f"Unknown pulling direction plane: {plane_key}")
        neutral_ref = part.CreateReferenceFromObject(neutral)

        # Pulling direction = the selected plane's normal.
        direction = {"xy": (0, 0, 1), "yz": (1, 0, 0), "zx": (0, 1, 0)}[plane_key]
        parting_ref = part.CreateReferenceFromName("")

        # AddNewDraft(iFaceToDraft, iNeutral, iNeutralMode, iParting,
        #             iDirX, iDirY, iDirZ, iMode, iAngle, iMultiselectionMode)
        draft = sf.AddNewDraft(
            face_ref, neutral_ref, 0, parting_ref,
            direction[0], direction[1], direction[2],
            0, angle, 0,
        )

        part.UpdateObject(draft)
        self.conn.refresh_display()
        return f"Draft created: {angle} deg on {face_name}. Feature: '{draft.Name}'"

    def _thickness(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        offset = args["offset"]
        face_name = args.get("face_name")
        if not face_name:
            raise ValueError(
                "catia_thickness requires 'face_name' ('Face.N' from "
                "catia_list_faces): the face to thicken."
            )
        face_ref = self._topo_reference("Face", self._topo_index(face_name, "Face"))

        # AddNewThickness(iFaceToThicken, iOffset) - 2 parameters.
        thickness = sf.AddNewThickness(face_ref, offset)

        part.UpdateObject(thickness)
        self.conn.refresh_display()
        return f"Thickness added: {offset} mm on {face_name}. Feature: '{thickness.Name}'"

    def _delete_feature(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        target = args.get("feature_name")
        if not target and args.get("index") is None:
            # Default: the feature just created. Undoing an experiment should
            # not require knowing CATIA's localized feature names.
            shapes = self.conn.get_active_part_body().Shapes
            if shapes.Count == 0:
                raise RuntimeError("The active body has no features to delete.")
            target = str(shapes.Item(shapes.Count).Name)
        if not target:
            shapes = self.conn.get_active_part_body().Shapes
            index = int(args["index"])
            if index < 1 or index > shapes.Count:
                raise RuntimeError(
                    f"index {index} is out of range: the active body has "
                    f"{shapes.Count} feature(s)."
                )
            target = str(shapes.Item(index).Name)
        feature = None
        bodies = part.Bodies
        for b in range(1, bodies.Count + 1):
            shapes = bodies.Item(b).Shapes
            for i in range(1, shapes.Count + 1):
                shape = shapes.Item(i)
                if str(shape.Name) == target:
                    feature = shape
                    break
            if feature is not None:
                break
        if feature is None:
            raise RuntimeError(
                f"No feature named '{target}' in any body of the active part. "
                "Use catia_list_features to see names."
            )
        sel = self.conn.hso
        try:
            sel.Clear()
            sel.Add(feature)
            sel.Delete()
        finally:
            try:
                sel.Clear()
            except Exception:
                pass
        try:
            part.Update()
        except Exception as e:
            self.conn.refresh_display()
            return (
                f"Feature '{target}' deleted, but the part update failed "
                f"afterwards ({e}): features that depended on it are now "
                "in error. Fix or delete them too."
            )
        self.conn.refresh_display()
        return f"Feature '{target}' deleted."

    def _list_features(self) -> str:
        self.conn.ensure_connected()
        body = self.conn.get_active_part_body()
        shapes = body.Shapes

        features = []
        for i in range(1, shapes.Count + 1):
            shape = shapes.Item(i)
            features.append({
                "index": i,
                "name": shape.Name,
                "type": shape.Type if hasattr(shape, "Type") else "unknown",
            })

        if not features:
            return "No features in the active body"
        return json.dumps(features, indent=2)

    _BOOLEAN_METHODS = {
        "add": "AddNewAdd",
        "remove": "AddNewRemove",
        "intersect": "AddNewIntersect",
        "assemble": "AddNewAssemble",
    }

    def _new_body(self, name: str | None = None) -> str:
        """Create a solid body and route subsequent features into it."""
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        bodies = part.Bodies
        before = bodies.Count
        body = bodies.Add()
        if name:
            try:
                body.Name = name
            except Exception:
                # CATIA refuses some names; the default one is still usable.
                pass
        self.conn.active_body = body
        try:
            part.InWorkObject = body
        except Exception:
            pass
        self.conn.refresh_display()
        return (
            f"Solid body '{body.Name}' created and made active "
            f"({before} -> {bodies.Count} bodies). Later features go into it."
        )

    def _set_active_body(self, name: str) -> str:
        """Route later features into an existing body.

        catia_new_body only ever moves forward. A part with several solids -
        the drone body is 9 - needs to come back to a body it already left, and
        without that the second body is the last one you can build.
        """
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        bodies = part.Bodies
        body = None
        for i in range(1, bodies.Count + 1):
            candidate = bodies.Item(i)
            if str(candidate.Name) == name:
                body = candidate
                break
        if body is None:
            names = ", ".join(str(bodies.Item(i).Name) for i in range(1, bodies.Count + 1))
            raise RuntimeError(
                f"No solid body named '{name}'. Bodies: {names or 'none'}."
            )
        self.conn.active_body = body
        try:
            part.InWorkObject = body
        except Exception:
            pass
        self.conn.refresh_display()
        return (
            f"Body '{body.Name}' is now active ({body.Shapes.Count} feature(s)). "
            "Later pad / pocket / hole / fillet features go into it."
        )

    def _list_bodies(self) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        bodies = part.Bodies
        spa = self._topo_measure(part)
        sel = self.conn.hso
        try:
            active_name = str(self.conn.get_active_part_body().Name)
        except Exception:
            active_name = ""

        out = []
        total_volume = 0.0
        total_faces = 0
        for i in range(1, bodies.Count + 1):
            body = bodies.Item(i)
            entry: dict[str, Any] = {
                "index": i,
                "name": body.Name,
                "features": body.Shapes.Count,
                "active": str(body.Name) == active_name,
            }
            # An empty body has nothing to measure; that is normal for a freshly
            # created one, not an error.
            if body.Shapes.Count:
                if spa is not None:
                    try:
                        measurable = spa.GetMeasurable(
                            part.CreateReferenceFromObject(body))
                        volume = measurable.Volume * 1e9
                        entry["volume_mm3"] = round(volume, 1)
                        total_volume += volume
                    except Exception:
                        pass
                try:
                    sel.Clear()
                    sel.Add(body)
                    search_topology(sel, "Face")
                    entry["faces"] = sel.Count
                    total_faces += sel.Count
                except Exception:
                    pass
                finally:
                    try:
                        sel.Clear()
                    except Exception:
                        pass
            out.append(entry)

        if not out:
            return "No solid bodies in the active part"
        # The part's volume is the sum of its bodies, which is what a spec means
        # by "several solids that interpenetrate, Merge = false".
        return json.dumps({
            "bodies": out,
            "count": len(out),
            "total_volume_mm3": round(total_volume, 1),
            "total_faces": total_faces,
        }, indent=2)

    def _set_visibility(self, name: str, show: bool) -> str:
        """Show or hide a tree node by name, searching the whole part.

        Visibility is not cosmetic here: a GSD surface left visible after
        CloseSurface overlays the solid, and an STL export or the mesh bounding
        box then includes it - so both the picture and the measured envelope come
        out wrong. Field case: a two-blade propeller looked like one blade had an
        extra lobe, the envelope read 45.6 mm wide instead of 30.1, and nothing
        was actually wrong with the geometry.
        """
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        target = None
        where = ""

        def try_get(collection, label):
            try:
                return collection.Item(name), label
            except Exception:
                return None, ""

        for i in range(1, part.Bodies.Count + 1):
            body = part.Bodies.Item(i)
            if str(body.Name) == name:
                target, where = body, "body"
                break
            got, lbl = try_get(body.Shapes, "feature")
            if got is not None:
                target, where = got, lbl
                break
            got, lbl = try_get(body.Sketches, "sketch")
            if got is not None:
                target, where = got, lbl
                break

        if target is None:
            for i in range(1, part.HybridBodies.Count + 1):
                hb = part.HybridBodies.Item(i)
                if str(hb.Name) == name:
                    target, where = hb, "geometrical set"
                    break
                got, lbl = try_get(hb.HybridShapes, "geometrical element")
                if got is not None:
                    target, where = got, lbl
                    break
                got, lbl = try_get(hb.HybridSketches, "sketch")
                if got is not None:
                    target, where = got, lbl
                    break

        if target is None:
            raise RuntimeError(
                f"No tree node named '{name}'. Use catia_list_bodies, "
                "catia_list_features or catia_gsd_list_elements to see names."
            )

        sel = self.conn.active_document.Selection

        def apply(node):
            sel.Clear()
            sel.Add(node)
            # Field-measured on V5-6R2020 B30, and not what the reference
            # suggests: 0 shows, 1 hides, 2 is accepted and does nothing. Using
            # 2 for "hide" is silently ignored, which is how a set of GSD
            # surfaces stayed on screen (and in every STL export) while the tool
            # reported it had hidden them.
            sel.VisProperties.SetShow(0 if show else 1)

        try:
            apply(target)
            # Hiding a geometrical set does not hide what is inside it: each
            # element keeps its own visibility, so the surfaces stay on screen
            # and keep poisoning STL exports. Descend one level.
            children = 0
            if where == "geometrical set":
                for collection in ("HybridShapes", "HybridSketches"):
                    try:
                        items = getattr(target, collection)
                    except Exception:
                        continue
                    for i in range(1, items.Count + 1):
                        try:
                            apply(items.Item(i))
                            children += 1
                        except Exception:
                            pass
        finally:
            self._clear_selection()
        self.conn.refresh_display()
        extra = f" and {children} element(s) inside it" if children else ""
        return f"'{name}' ({where}) is now {'shown' if show else 'hidden'}{extra}."

    def _boolean(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        operation = args["operation"]
        method = self._BOOLEAN_METHODS[operation]
        bodies = part.Bodies

        try:
            tool = bodies.Item(args["tool_body"])
        except Exception:
            names = ", ".join(str(bodies.Item(i).Name)
                              for i in range(1, bodies.Count + 1))
            raise RuntimeError(
                f"No body named '{args['tool_body']}'. Bodies: {names or 'none'}."
            ) from None

        if args.get("target_body"):
            try:
                target = bodies.Item(args["target_body"])
            except Exception:
                raise RuntimeError(
                    f"No body named '{args['target_body']}'."
                ) from None
        else:
            # Default target is "the other body". The active body is the usual
            # answer (you just built the tool body), but when the tool body IS
            # the active one, MainBody is the only sensible survivor.
            active = self.conn.get_active_part_body()
            if str(active.Name) != str(tool.Name):
                target = active
            elif str(part.MainBody.Name) != str(tool.Name):
                target = part.MainBody
            else:
                raise ValueError(
                    f"'{tool.Name}' is the only candidate; pass target_body "
                    f"explicitly to say which body should survive."
                )

        if str(tool.Name) == str(target.Name):
            raise ValueError(
                "tool_body and target_body are the same body - a boolean needs two."
            )

        # These four take the Body itself, not a Reference: passing a Reference
        # is accepted for add/remove/assemble and then does nothing, and
        # AddNewIntersect rejects it outright with a type mismatch.
        part.InWorkObject = target
        feature = getattr(part.ShapeFactory, method)(tool)
        if args.get("name"):
            feature.Name = args["name"]
        part.UpdateObject(feature)

        # The tool body is consumed by CATIA, so it can no longer be the target
        # of new features.
        self.conn.active_body = target
        return (
            f"Boolean '{operation}': '{tool.Name}' into '{target.Name}' "
            f"(feature '{feature.Name}'). Active body is now '{target.Name}'."
        )

    def _topo_measure(self, part: Any) -> Any:
        """SPA workbench for edge/face geometry, or None if unavailable."""
        try:
            return part.Parent.GetWorkbench("SPAWorkbench")
        except Exception:
            try:
                return self.conn.app.GetWorkbench("SPAWorkbench")
            except Exception:
                return None

    def _cell_measure(self, spa: Any, cell: Any, prop: str) -> float | None:
        """Read Area/Length off an HSO cell, or None when the value is not real.

        SPA does not raise for topology it cannot resolve - it hands back a
        placeholder constant (field-measured on V5-6R2020 B30: 2e7 for an edge
        of a GSD CloseSurface solid, 8e7 for an extract of that same edge, and
        0.0 for a face area). Publishing those numbers is worse than publishing
        nothing: 2e7 reads like a measurement and silently poisons any
        length-based edge selection.
        """
        if spa is None:
            return None
        try:
            raw = getattr(spa.GetMeasurable(cell.Reference), prop)
        except Exception:
            return None
        if prop == "Area":
            value = raw * 1e6            # SPA gives m2
            limit = 1e10                 # 100 m x 100 m
        else:
            value = raw                  # Length already comes back in mm
            limit = 1e5                  # 100 m
        if value <= 0 or value > limit:
            return None
        return value

    def _topology_cells(self, kind: str, body: Any = None) -> tuple[list[Any], int]:
        """The body's solid cells of ``kind`` and their count.

        The body is searched, not ``Shapes.Item(last)``: only the body resolves
        to the final solid's topology, and ``topology_reference`` - which is what
        ``Edge.N`` / ``Face.N`` mean everywhere else - searches the body too.
        Listing from the last feature instead used to hand out indices that
        pointed at a different element by the time fillet resolved them.

        Construction geometry is filtered out by ``topology_cells`` so these
        indices match the ones fillet/chamfer/measure resolve.
        """
        body = body if body is not None else self.conn.get_active_part_body()
        if body.Shapes.Count == 0:
            raise RuntimeError(
                f"Body '{body.Name}' has no solid shape yet."
            )
        cells = topology_cells(self.conn, body, kind)
        return cells, len(cells)

    def _list_faces(self, body_name: str | None = None) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        if body_name:
            # The name used to be validated and then dropped, so the listing
            # always described the ACTIVE body - silently wrong on any part with
            # more than one solid.
            try:
                body = part.Bodies.Item(body_name)
            except Exception:
                names = ", ".join(str(part.Bodies.Item(i).Name)
                                  for i in range(1, part.Bodies.Count + 1))
                raise RuntimeError(
                    f"No body named '{body_name}'. Top-level bodies: {names or 'none'}."
                )
        spa = self._topo_measure(part)

        faces = []
        try:
            cells, count = self._topology_cells("Face", body)
            for i, cell in enumerate(cells, start=1):
                entry: dict[str, Any] = {"index": i, "name": f"Face.{i}"}
                try:
                    entry["type"] = str(cell.Type)
                except Exception:
                    pass
                nls = nls_of(cell)
                if nls:
                    entry["nls"] = nls
                    entry["created_by"] = nls_features(nls)
                area = self._cell_measure(spa, cell, "Area")
                if area is None:
                    entry["area_unknown"] = True
                else:
                    entry["area_mm2"] = round(area, 3)
                faces.append(entry)
        except Exception as e:
            return f"Could not enumerate faces: {e}"
        finally:
            self._clear_selection()

        if not faces:
            return "No faces found on the final shape"
        return json.dumps(faces, indent=2)

    def _list_edges(self) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        self._prepare_body(part)
        spa = self._topo_measure(part)

        edges = []
        try:
            cells, count = self._topology_cells("Edge")
            for i, cell in enumerate(cells, start=1):
                # Canonical indexed name accepted by catia_measure_distance,
                # catia_fillet and catia_chamfer. Raw HSO names are long
                # and unusable in searches.
                entry: dict[str, Any] = {"index": i, "name": f"Edge.{i}"}
                try:
                    entry["type"] = str(cell.Type)
                except Exception:
                    pass
                # Which feature produced this edge: the only reliable handle
                # when several edges share a length (four corners of a square,
                # two circles on one disc, ...).
                nls = nls_of(cell)
                if nls:
                    entry["nls"] = nls
                    entry["created_by"] = nls_features(nls)
                length = self._cell_measure(spa, cell, "Length")
                if length is None:
                    entry["length_unknown"] = True
                else:
                    entry["length_mm"] = round(length, 4)
                edges.append(entry)
        except Exception as e:
            return f"Could not enumerate edges: {e}. Use CATIA selection to identify edge names."
        finally:
            self._clear_selection()

        if not edges:
            return "No edges found on the final shape"
        return json.dumps(edges, indent=2)
        if not edges:
            return "No edges found on the last feature"
        return json.dumps(edges, indent=2)
