"""Part Design tools for CATIA V5.

3D feature creation: Pad, Pocket, Fillet, Chamfer, Shaft, Groove, Hole,
RectPattern, CircPattern, Mirror, Rib, Slot, Shell, Thickness, Draft.
"""

from __future__ import annotations

import json
import re
from typing import Any

from catia_mcp.connection import CATIAConnection
from catia_mcp.results import FeatureRejected
from catia_mcp.topo_search import (
    edge_fingerprint,
    face_keys,
    nls_features,
    nls_of,
    search_topology,
    topology_cells,
    topology_reference,
    topology_references,
)


# Shared by catia_fillet and catia_chamfer: pick edges by a coordinate that is
# ON them instead of by an index. Reports come in the other direction - a spec
# sheet says 'fillet R2.5 on the edge at (0, 65, -20)' and never says Edge.11.
_EDGE_ANCHOR_PROPERTIES: dict[str, Any] = {
    "edge_anchors": {
        "type": "array",
        "items": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 3,
            "maxItems": 3,
        },
        "description": (
            "Edges given as [x, y, z] points that lie ON them. Each anchor is "
            "matched to the edge whose minimum distance to it is smallest, and "
            "the reply states the edge name and the measured distance for every "
            "anchor - so 'I selected the wrong edge' becomes visible instead of "
            "producing a legal fillet in the wrong place. An anchor that is "
            "outside anchor_tol, or equally close to two edges, is refused with "
            "the candidates and their distances. Can be combined with edge_names."
        ),
    },
    "anchor_tol": {
        "type": "number",
        "default": 0.5,
        "exclusiveMinimum": 0,
        "description": (
            "How far an anchor may sit from the edge it means, in mm. 0.5 "
            "accepts a coordinate rounded to a tenth; tighten it when two "
            "candidate edges pass close together."
        ),
    },
}


class _ProbePoints:
    """A few scratch points in a throwaway geometrical set.

    CATIA's minimum-distance API takes a POINT REFERENCE, not a coordinate, so
    turning "[x, y, z] from a report" into "which edge is this" needs a real
    point object. They are collected in one set and that set is deleted on the
    way out (field-verified on B30 2026-10-25: Selection.Delete removes the set
    itself, leaving HybridBodies.Count back at 0), so the reply can quote
    coordinates without leaving anything in the caller's model tree.
    """

    NAME = "MCP_probe_points"

    def __init__(self, conn: Any, part: Any) -> None:
        self.conn = conn
        self.part = part
        self.factory = part.HybridShapeFactory
        self.set = part.HybridBodies.Add()
        self.set.Name = self.NAME
        self.shapes: list[Any] = []
        self._updated = False

    def add(self, x: float, y: float, z: float) -> Any:
        point = self.factory.AddNewPointCoord(float(x), float(y), float(z))
        self.set.AppendHybridShape(point)
        self.shapes.append(point)
        self._updated = False
        return point

    def update(self) -> None:
        """One rebuild for every point added since the last one."""
        if self._updated or not self.shapes:
            return
        self.part.Update()
        self._updated = True

    def close(self) -> None:
        try:
            sel = self.conn.hso
            sel.Clear()
            sel.Add(self.set)
            sel.Delete()
            sel.Clear()
        except Exception:  # noqa: BLE001 - a leftover probe set is not fatal
            pass
        try:
            self.part.Update()
        except Exception:  # noqa: BLE001
            pass


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
                                "whatever its thickness. Ignored if depth is given. "
                                "Combine with direction='both' to reach through from "
                                "a mid-plane sketch: that is built as two up-to-last "
                                "cuts, because one 'through all' only goes one way "
                                "(measured 2026-10-23: half the expected volume)."
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
                    "becomes its own feature, and if the single batch rebuild fails "
                    "they are retried one at a time - the reply reports which ones "
                    "took and why the others did not. Prefer the 'fingerprint' from "
                    "catia_list_edges ('L17.6112|Pad.6;2|Sketch.7;2') over 'Edge.N': "
                    "the index is renumbered by every feature, the fingerprint is "
                    "re-resolved when the call runs. When the source data gives "
                    "COORDINATES instead of edge names - 'R2.5 on the edge at "
                    "(0, 65, -20)' - pass them as edge_anchors: each one is matched "
                    "to the nearest edge, and the reply states the edge name and the "
                    "measured distance for every anchor. An anchor that matches "
                    "nothing within anchor_tol, or matches two edges equally well, "
                    "is refused rather than guessed."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "radius": {
                            "type": "number",
                            "description": "Fillet radius in mm",
                        },
                        "edge_name": {
                            "anyOf": [{"type": "string"}, {"type": "object"}],
                            "description": (
                                "'Edge.N' or a fingerprint from catia_list_edges, or "
                                "{\"length_mm\": 17.6112, \"faces\": [\"Pad.6;2\"]}. "
                                "Omit to fillet all edges of the last feature."
                            ),
                        },
                        "edge_names": {
                            "type": "array",
                            "items": {"anyOf": [{"type": "string"}, {"type": "object"}]},
                            "description": (
                                "Several edges at once, e.g. ['Edge.4','Edge.7'] or a "
                                "list of fingerprints. Takes precedence over edge_name."
                            ),
                        },
                        "propagation": {
                            "type": "string",
                            "description": (
                                "'none' (default since 0.3.7) fillets exactly the edges "
                                "given. 'tangency' is CATIA's own default and runs the "
                                "fillet along every tangent-continuous edge - on a "
                                "smooth chain that is the whole boundary, not the edge "
                                "you asked for. It is still right when the spec really "
                                "means 'round this whole lip'."
                            ),
                            "enum": ["tangency", "none"],
                            "default": "none",
                        },
                        **_EDGE_ANCHOR_PROPERTIES,
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
                            "anyOf": [{"type": "string"}, {"type": "object"}],
                            "description": "Edge to chamfer: 'Edge.N' or a fingerprint from catia_list_edges. Omit to chamfer all edges of the last feature.",
                        },
                        "edge_names": {
                            "type": "array",
                            "items": {"anyOf": [{"type": "string"}, {"type": "object"}]},
                            "description": "Several edges at once, e.g. ['Edge.3','Edge.9'] or a list of fingerprints. Takes precedence over edge_name.",
                        },
                        **_EDGE_ANCHOR_PROPERTIES,
                    },
                    "required": ["length"],
                },
            },
            {
                "name": "catia_hole",
                "description": (
                    "Create a Hole feature on the active sketch. Supports simple, "
                    "tapered, counterbored and countersunk holes. `depth` is applied "
                    "through the hole's bottom limit (LimitMode 0 + Dimension), not "
                    "through AddNewHoleFromSketch's second argument, which CATIA "
                    "ignores - measured 2026-10-23: D4 x 4 in a 10 mm block removes "
                    "50.3 mm3 and D4 x 6 removes 75.4 mm3, both exactly pi*r^2*d. The "
                    "bottom is flat, so a blind hole has no drill point. The sketch "
                    "supplies the bore's position; the reply reports the volume "
                    "actually removed, which is the only trustworthy signal."
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
                                "Use it when the wall is thinner than the depth you "
                                "want, or when the sketch sits on a face."
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
                    "Duplicates a feature in a grid along two directions. Prefer "
                    "dir1_axis / dir2_axis (x, y or z) over dir1_edge / dir2_edge: "
                    "an edge's DIRECTION cannot be read back from any listing, so "
                    "two edges of equal length give opposite directions and the only "
                    "way to tell which you got is the volume delta (reported "
                    "2026-10-24, with 3 attempts and 2 wasted features). Omit all "
                    "four to use CATIA's defaults, which are PlaneYZ -> X and "
                    "PlaneZX -> Y regardless of what the spec meant."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "dir1_axis": {
                            "type": "string",
                            "enum": ["x", "y", "z"],
                            "description": (
                                "Direction 1 as a global axis (recommended). Do not "
                                "combine with dir1_edge."
                            ),
                        },
                        "dir2_axis": {
                            "type": "string",
                            "enum": ["x", "y", "z"],
                            "description": "Direction 2 as a global axis.",
                        },
                        "reverse_dir1": {
                            "type": "boolean",
                            "default": False,
                            "description": "Step along -dir1 instead of +dir1.",
                        },
                        "reverse_dir2": {
                            "type": "boolean",
                            "default": False,
                            "description": "Step along -dir2 instead of +dir2.",
                        },
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
                    "Create a Circular Pattern of the last feature around a main "
                    "axis through 'center'. 'count' includes the original, so "
                    "count=12 adds 11 instances. The rotation axis is built as a "
                    "line in the part's own 'PatternAxis' geometrical set and kept "
                    "there - a Reference to another document's geometry dies when "
                    "that document closes, which is why this tool used to fail "
                    "100% of the time at update. The reply reports the volume "
                    "change, which is the only trustworthy signal that the "
                    "instances were actually built."
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
                    "Create a Shell feature: hollows out a solid, leaving walls of the "
                    "given thickness. 'thickness' is the INTERNAL one (material goes "
                    "inward); 'external_thickness' is the other side of CATIA's "
                    "AddNewShell(face, internal, external) and is the only way to get "
                    "material outside the original surfaces. Both were measured on B30 "
                    "2026-10-25 on a 60x40x40 box (96 000 mm3) with one face removed: "
                    "internal 5 gives 43 500 mm3, external 5 gives 61 500, and 2.5/2.5 "
                    "gives 52 125 - three different shapes, so say which side you "
                    "mean. 'thickness' must be POSITIVE: CATIA rejects a negative "
                    "value with a bare 'UpdateObject failed', which reads like a bad "
                    "feature name rather than a bad argument. The first face in "
                    "'faces_to_remove' is what the API calls the face to remove, so "
                    "at least one is required - a closed shell is not expressible "
                    "through this factory. Measured 2026-10-25: passing None for "
                    "the face 'succeeds' and leaves an EMPTY feature (volume 0.0 "
                    "on a 96 000 mm3 box), which is worse than a refusal. For a "
                    "part that must stay closed, shell it with one face removed "
                    "and thicken or pad the opening back, or build the walls as a "
                    "pad of the outer profile minus a pocket of the inner one."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "thickness": {
                            "type": "number",
                            "exclusiveMinimum": 0,
                            "description": (
                                "Wall thickness in mm, measured INWARD from the "
                                "surface. Must be positive."
                            ),
                        },
                        "external_thickness": {
                            "type": "number",
                            "minimum": 0,
                            "default": 0,
                            "description": (
                                "Thickness added OUTSIDE the original surface "
                                "(CATIA's third AddNewShell argument). 0 = the "
                                "usual inward shell."
                            ),
                        },
                        "faces_to_remove": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "description": (
                                "Indexed faces to remove and open ('Face.N' from "
                                "catia_list_faces); the first one is the face "
                                "CATIA's AddNewShell anchors on, so at least one "
                                "is required."
                            ),
                        },
                    },
                    "required": ["thickness", "faces_to_remove"],
                },
            },
            {
                "name": "catia_draft",
                "description": (
                    "Add a Draft Angle to a face for mold-release purposes. Tapers "
                    "the face by a given angle about a neutral face along a pulling "
                    "direction. CATIA wants the neutral as a real FACE reference - a "
                    "datum plane is rejected outright (AddNewDraft fails, measured). "
                    "Omit `neutral_face` to have the largest remaining face picked "
                    "for you; the reply always says which face was used, because the "
                    "hinge determines whether the wall thickens or thins."
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
                        "neutral_face": {
                            "type": "string",
                            "description": (
                                "Face the draft hinges on ('Face.N'). It must touch "
                                "the drafted face. Omit to auto-pick the largest "
                                "remaining face."
                            ),
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
                    "Undoing the feature you have just created is still one call, "
                    "but it now has to be asked for: with neither feature_name nor "
                    "index, pass last=true. A bare no-argument call is refused and "
                    "names what it would have deleted, because on a long tree the "
                    "last created feature is usually the fillet you just added, not "
                    "the test sketch you meant (reported 2026-10-25)."
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
                        "last": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Delete the last feature of the active body. "
                                "Required when neither feature_name nor index is "
                                "given."
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
                "name": "catia_solid_loft",
                "description": (
                    "SOLID multi-section loft (Part Design 'Multi-sections solid'): "
                    "one feature, one Update, a closed solid from 2+ section curves. "
                    "Sections may be sketches, GSD curves or a solid's topology. "
                    "THIS IS THE ROUTE FOR THIN SECTIONS, and the reason is measured: "
                    "the surface route (catia_gsd_multi_section_surface + "
                    "catia_gsd_thick_surface) cannot offset a section that is thinner "
                    "than the thickness asked for - a 2.30 mm lens section refused "
                    "1.27/1.27 AND 2.54/0 - while this tool builds it in one call. "
                    "Verified on B30 2026-10-25 with r8 at z=0 and r16 at z=40: "
                    "feature '多截面实体.1', volume 18765.8 mm3, area 4080.97 mm2, "
                    "centre of gravity z=24.286 - a solid, not a surface. "
                    "Section rule is the same as the surface loft: ONE curve per "
                    "section. A polygon of separate sketch lines is several curves "
                    "and fails; a closed catia_sketch_spline or catia_gsd_spline is "
                    "one. Reported by a user who found AddNewLoft after this server "
                    "said no solid loft existed (2026-10-25) - that statement was a "
                    "gap in the probing, not a CATIA limit."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "sections": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 2,
                            "description": (
                                "Section names in lofting order: sketch names, GSD "
                                "curve names, or 'Edge.N' / 'Face.N'."
                            ),
                        },
                        "start_tangent": {
                            "type": "string",
                            "description": (
                                "Optional support surface the first section is "
                                "tangent to (see catia_gsd_multi_section_surface)."
                            ),
                        },
                        "end_tangent": {
                            "type": "string",
                            "description": "Optional tangency support at the last section.",
                        },
                        "name": {"type": "string", "description": "Optional feature name"},
                    },
                    "required": ["sections"],
                },
            },
            {
                "name": "catia_delete_body",
                "description": (
                    "Delete a solid body from the active part. Only needed for "
                    "cleanup: bodies created for an experiment stay in the tree "
                    "otherwise, because catia_delete_feature looks inside bodies and "
                    "never sees one, so a part ended up carrying nine empty bodies "
                    "(reported 2026-10-25). The MainBody cannot be deleted, and a "
                    "body that still holds features is refused unless 'force' is "
                    "true - deleting it silently removes those features too."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Body name to delete"},
                        "force": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Delete even when the body still holds features. "
                                "Those features are lost."
                            ),
                        },
                    },
                    "required": ["name"],
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
                        "kind": {
                            "type": "string",
                            "description": (
                                "Hide a whole class of tree bookkeeping in one call, "
                                "instead of naming each one: 'sketch' (every sketch in "
                                "every body), 'datum_plane' (the three reference "
                                "planes), or 'construction' (both). These are the "
                                "white frames that clutter a screenshot - a sketch "
                                "outline is not solid geometry, but it is drawn on top "
                                "of the solid and reads as a defect."
                            ),
                            "enum": ["sketch", "datum_plane", "construction"],
                        },
                        "show": {
                            "type": "boolean",
                            "description": "true shows, false hides (default false).",
                            "default": False,
                        },
                    },
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
                    "List the edges of the final solid shape with three handles: "
                    "'Edge.N' (an enumeration position - it is renumbered by every "
                    "feature that changes the topology), the length, and a "
                    "'fingerprint' such as 'L17.6112|Pad.6;2|Sketch.7;2' built from "
                    "the faces that bound the edge. Pass the fingerprint back to "
                    "catia_fillet / catia_chamfer and the reference is re-resolved "
                    "at call time, which is what makes a multi-fillet script "
                    "survive its own edits. Filter by length or by bounding face "
                    "and page the result: a 67-edge part otherwise returns tens of "
                    "thousands of characters of NLS text."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "min_length": {"type": "number",
                                       "description": "Only edges at least this long (mm)"},
                        "max_length": {"type": "number",
                                       "description": "Only edges at most this long (mm)"},
                        "faces": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": ("Only edges bounded by all of these, e.g. "
                                            "['Pad.6;2'] or ['Face.3','Face.7']. Face "
                                            "keys come from 'bounded_by'."),
                        },
                        "offset": {"type": "integer", "default": 0,
                                   "description": "Skip this many matches"},
                        "limit": {"type": "integer", "default": 0,
                                  "description": "Return at most this many (0 = all)"},
                        "include_nls": {"type": "boolean", "default": True,
                                        "description": "Include each edge's NLS path"},
                        "coords": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Add 'point_on_edge' [x, y, z] to every edge - a "
                                "coordinate that lies ON it (the point closest to "
                                "the body's centre of gravity, i.e. the midpoint "
                                "for a straight edge). Costs one measurement per "
                                "edge, so it is off by default. This is the "
                                "coordinate to feed back as edge_anchors, and the "
                                "one to compare with a spec sheet that says "
                                "'the edge at (0, 65, -20)'."
                            ),
                        },
                    },
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
            case "catia_solid_loft":
                return self._solid_loft(arguments)
            case "catia_delete_body":
                return self._delete_body(arguments["name"],
                                         bool(arguments.get("force", False)))
            case "catia_list_bodies":
                return self._list_bodies()
            case "catia_set_active_body":
                return self._set_active_body(arguments["name"])
            case "catia_set_visibility":
                if arguments.get("kind"):
                    return self._set_visibility_kind(
                        str(arguments["kind"]), bool(arguments.get("show", False)))
                return self._set_visibility(
                    arguments["name"], bool(arguments.get("show", False)))
            case "catia_boolean":
                return self._boolean(arguments)
            case "catia_list_faces":
                return self._list_faces(arguments.get("body_name"))
            case "catia_list_edges":
                return self._list_edges(
                    arguments.get("min_length"), arguments.get("max_length"),
                    arguments.get("faces"), int(arguments.get("offset", 0) or 0),
                    int(arguments.get("limit", 0) or 0),
                    bool(arguments.get("include_nls", True)),
                    bool(arguments.get("coords", False)))
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

    def _edge_probe_points(self, part: Any) -> "_ProbePoints":
        """Scratch points for "which edge is this coordinate on" questions."""
        return _ProbePoints(self.conn, part)

    def _anchors_to_edges(
        self, body: Any, anchors: list[list[float]], tol: float,
    ) -> tuple[list[str], list[Any], str, list[dict[str, Any]]]:
        """Map anchor coordinates to edges by minimum distance.

        Measured on B30 2026-10-25: ``Measurable.GetMinimumDistance`` accepts a
        POINT REFERENCE (it rejects a Measurable with "type mismatch"), so a
        coordinate becomes an edge index without ever moving the mouse. The
        distance is reported for every anchor - an anchor that lands 0.4 mm from
        the edge the caller meant looks identical to a correct one if the reply
        only says "fillet created".

        Anything that is not a single unambiguous match is refused rather than
        guessed: a fillet on the wrong edge is a legal feature nobody asked for.
        """
        part = self.conn.get_active_part()
        spa = self._topo_measure(part)
        if spa is None:
            raise RuntimeError(
                "SPAWorkbench is not available, so edge anchors cannot be "
                "measured. Use Edge.N or a fingerprint from catia_list_edges."
            )
        table = self._edge_table(body)
        if not table:
            raise RuntimeError("The active body has no edges to fillet yet.")
        measurables = []
        for entry in table:
            try:
                measurables.append(spa.GetMeasurable(entry["cell"].Reference))
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"Could not measure {entry['name']}: {exc}"
                ) from exc

        labels: list[str] = []
        refs: list[Any] = []
        matches: list[int] = []
        report: list[str] = []
        seen: set[int] = set()
        probe = self._edge_probe_points(part)
        try:
            for anchor in anchors:
                if len(anchor) != 3:
                    raise RuntimeError(
                        f"Each edge anchor must be [x, y, z]; got {anchor!r}."
                    )
                point_ref = probe.add(*anchor)
                probe.update()
                dists: list[tuple[float, int]] = []
                for i, measurable in enumerate(measurables):
                    try:
                        dists.append((float(measurable.GetMinimumDistance(point_ref)), i))
                    except Exception:  # noqa: BLE001 - one edge, not the call
                        continue
                dists.sort()
                if not dists:
                    raise RuntimeError(
                        f"No edge of the body could be measured against anchor "
                        f"{anchor}."
                    )
                best_distance, best_index = dists[0]
                entry = table[best_index]
                if best_distance > tol:
                    raise RuntimeError(
                        f"Anchor {anchor} is {best_distance:.4f} mm from the "
                        f"nearest edge ({entry['name']}, L{entry['length'] or 0:.4f}), "
                        f"beyond anchor_tol={tol}. Nothing was filleted. Raise "
                        "anchor_tol if the coordinate is a rounded measurement, or "
                        "pass Edge.N / a fingerprint from catia_list_edges."
                    )
                near = [d for d in dists[1:]
                        if d[0] <= tol and abs(d[0] - best_distance) < 1e-6]
                if near:
                    others = ", ".join(
                        f"{table[i]['name']} (L{table[i]['length'] or 0:.4f})"
                        for _, i in near)
                    raise RuntimeError(
                        f"Anchor {anchor} is {best_distance:.4f} mm from "
                        f"{entry['name']} and equally close to {others}, so the "
                        "edge it means is ambiguous - the anchor sits on where "
                        "they meet. Nothing was filleted; pass explicit "
                        "edge_names/edge_name for these edges."
                    )
                if best_index in seen:
                    report.append(
                        f"Anchor {anchor} -> {entry['name']} @ {best_distance:.4f} mm "
                        "(already selected by an earlier anchor; not added twice)"
                    )
                    continue
                seen.add(best_index)
                matches.append(best_index)
                report.append(
                    f"Anchor {anchor} -> {entry['name']} @ {best_distance:.4f} mm")
        finally:
            # The probe set is deleted here, and that clears the selection,
            # which invalidates every topology Reference taken from it - so the
            # references are re-read afterwards, by index, not kept from above.
            probe.close()
        if not matches:
            raise RuntimeError(
                "None of the given edge_anchors matched a distinct edge: "
                + "; ".join(report)
            )
        table = self._edge_table(body)
        for index in matches:
            entry = table[index]
            labels.append(f"{entry['name']} (by anchor)")
            refs.append(entry["cell"].Reference)
        return labels, refs, "Edge anchors: " + "; ".join(report) + ".", table

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
        # Measured BEFORE the factory call: the moment a feature exists but has
        # not been updated, the body's measurable can fail, which left `before`
        # as None and the failure message then died formatting it ("unsupported
        # format string passed to NoneType") instead of naming the open profile.
        before, volume_note = self._volume_probe(part, body)
        if before is None and self._body_is_empty(body):
            # The very first pad: an empty body has volume 0 by definition, and
            # its measurable cannot answer. Reporting that as "unverified" would
            # make every new part look doubtful.
            before, volume_note = 0.0, None

        pad = sf.AddNewPad(sketch, height / 2.0 if symmetric else height)

        if symmetric:
            pad.IsSymmetric = True
        elif direction == "reverse":
            pad.DirectionOrientation = 1  # catReverse

        update_error = None
        try:
            part.UpdateObject(pad)
        except Exception as exc:  # noqa: BLE001 - judged by volume below
            update_error = exc
        after, took = self._settle(part, body, before, expect="add")
        if took is False:
            # Nothing grew: drop the dead node so the document stays usable.
            # This is the case that used to poison the whole part - a pad on an
            # open profile left '凸台.N' behind and every later update failed.
            gone = self._remove_feature(part, pad)
            stayed = (f"Volume stayed {before:.1f} mm3." if before is not None
                      else f"The volume could not be measured ({volume_note}).")
            # Before blaming the profile, ask CATIA the one question that settles
            # it: does the SAME sketch, padded the other way, grow the body? If it
            # does, the direction is wrong and no amount of profile repair helps.
            # Reported 2026-10-25: a 55 mm pad pointing into existing material
            # came back as "the profile may be open", and the advice to retry sent
            # a user round a loop that could never succeed.
            reverse_gain = self._pad_other_direction(
                part, body, sketch, height, symmetric, direction, before)
            if reverse_gain is not None:
                raise RuntimeError(
                    "CATIA built no material for this pad, so it was rolled back"
                    + ("" if gone else " (the dead feature could NOT be deleted - "
                                        "close the document without saving)")
                    + f" {stayed} THE PROFILE IS FINE: the same sketch padded in the "
                    f"opposite direction adds {reverse_gain:.1f} mm3. "
                    + ("You already passed direction='reverse', so the material is "
                       "on the other side - use direction='normal'."
                       if direction == "reverse"
                       else "Pass direction='reverse' to build it there.")
                    + " (Measured by building it both ways and rolling back the "
                      "probe, not guessed.)"
                )
            raise RuntimeError(
                "CATIA built no material for this pad, so it was rolled back"
                + ("" if gone else " (the dead feature could NOT be deleted - "
                                    "close the document without saving)")
                + f" {stayed} It adds nothing in EITHER direction, so the direction "
                "is not the problem. Three things do this://n"
                "  · the profile is open (a 0.0035 mm gap is enough) - "
                "catia_close_sketch reports the endpoints and the gap when it can "
                "see one;\n"
                "  · the profile lies inside existing material - the sweep is "
                "absorbed, so move the sketch's offset so the sweep starts outside "
                "the body, or extend the profile through the material;\n"
                "  · the outline self-intersects (check the sign of its area).\n"
                "Retrying changes none of these. A retry is only worth it when the "
                "profile echo was clean, the direction is right AND the sweep has "
                "somewhere to grow."
                + (f". Original error: {update_error}" if update_error else ".")
            )
        self.conn.refresh_display()
        span = "total %.3f mm, %0.3f each side" % (height, height / 2.0) if symmetric \
            else "%.3f mm" % height
        if took is None:
            # No volume, no verdict - same rule as pocket and hole.
            return (
                f"UNVERIFIED: pad '{pad.Name}' was created: {span} ({direction}). "
                f"The solid growth could not be measured ({volume_note})"
                + (f"; CATIA also raised during the update: {update_error}"
                   if update_error is not None else "")
                + ", so this is reported on CATIA's return alone."
            )
        note = ""
        if before is not None and after is not None:
            gain = after - before
            note = (f" Volume {before:.1f} -> {after:.1f} mm3 "
                    f"(delta {gain:+.1f}).")
        if update_error is not None:
            note += (" (CATIA raised during the update, but the solid did grow "
                     "and the feature is kept.)")
        return f"Pad created: {span} ({direction}). Feature: '{pad.Name}'{note}"

    def _volume_probe(self, part: Any, body: Any) -> tuple[float | None, str | None]:
        """Measure the body's volume in mm3, or explain why it cannot be.

        ``_body_volume_mm3`` is the terse form for callers that only need the
        number. A caller that has to tell the user "not verified" owes them the
        reason: a 2026-10-23 report got "volume verification unavailable on this
        installation" for a cut that the same installation verified fine one
        turn later, and had no way to tell whether the installation, the
        document or the solid was at fault.
        """
        try:
            doc = part.Parent
            try:
                spa = doc.GetWorkbench("SPAWorkbench")
            except AttributeError:
                spa = self.conn.app.GetWorkbench("SPAWorkbench")
        except Exception as exc:  # noqa: BLE001 - the reason is the payload
            return None, f"the SPAWorkbench (space analysis) would not open: {exc}"
        try:
            ref = part.CreateReferenceFromObject(body)
        except Exception as exc:  # noqa: BLE001
            return None, f"no reference could be made to the body: {exc}"
        try:
            volume = spa.GetMeasurable(ref).Volume
        except Exception as exc:  # noqa: BLE001
            return None, f"the measurable's Volume failed: {exc}"
        if volume is None:
            return None, "the measurable returned no volume"
        return volume * 1e9, None  # m3 -> mm3

    def _body_volume_mm3(self, part: Any, body: Any) -> float | None:
        """Measure the body's solid volume in mm3, or None if unavailable."""
        return self._volume_probe(part, body)[0]

    @staticmethod
    def _body_is_empty(body: Any) -> bool:
        """True when the body has no solid yet - so its volume is 0, not unknown."""
        try:
            return body.Shapes.Count == 0
        except Exception:  # noqa: BLE001 - unresolved counts as "not known to be empty"
            return False

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

        before, volume_note = self._volume_probe(part, body)

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
        update_error = self._try_update(part, pocket)
        after, took = self._settle(part, body, before, expect="subtract")

        explicit_direction = "direction" in args or symmetric
        flipped = False
        single_sided = False
        if took is False and symmetric:
            # A mid-plane cut where one half is air: the primary side removed
            # nothing, but the other side has material. Honor the half that
            # exists and say so - failing the whole feature (which is what
            # happened to the reporter) leaves a 2-feature hole to patch by hand.
            pocket.DirectionOrientation = 0 if reverse else 1
            retry_error = self._try_update(part, pocket)
            after, retook = self._settle(part, body, before, expect="subtract")
            if retook:
                flipped = True
                took = True
                single_sided = True
                update_error = retry_error
        if took is False and not explicit_direction:
            # Nothing was removed and the caller did not pin the side. A
            # profile sketched on a solid face lands on the air side often
            # enough that 0.3.3 flipped here automatically, and that is what
            # makes "pocket from the top face" work on the first call. 0.3.4's
            # volume gate raised before this block could run, which handed the
            # choice of direction back to the caller as a guaranteed failed
            # round trip. Flip, measure again, and only call it a failure when
            # the other side is air too.
            pocket.DirectionOrientation = 0 if reverse else 1
            retry_error = self._try_update(part, pocket)
            after, retook = self._settle(part, body, before, expect="subtract")
            if retook:
                flipped = True
                took = True
                update_error = retry_error
            else:
                # Air on both sides: put the requested orientation back so the
                # tree reflects the original intent, then roll back below.
                pocket.DirectionOrientation = 1 if reverse else 0
                self._try_update(part, pocket)
                after = self._body_volume_mm3(part, body)
                took = False

        if took is False:
            # Nothing was removed. Two different stories used to be told here,
            # and one of them was a lie: UpdateObject can raise *after* the cut
            # is already in the geometry. Deciding on the volume keeps the
            # message and the model in agreement whichever way CATIA behaved.
            gone = self._remove_feature(part, pocket)
            side = ("the requested direction" if explicit_direction
                    else "either direction")
            raise RuntimeError(
                f"The pocket removed no material in {side}, so it was "
                + ("rolled back" if gone else
                   "left in the tree (the dead feature could NOT be deleted - "
                   "close the document without saving)")
                + f" (volume stayed {before:.1f} mm3). Two usual causes: the "
                "profile does not intersect the solid (a face sketch whose "
                "local coordinates do not land on the material - put the sketch "
                "on a datum plane with an offset instead), or the profile is "
                "not closed"
                + (f". Original error: {update_error}" if update_error else ".")
            ) from (update_error or None)

        self.conn.refresh_display()

        mirror = None
        if symmetric and not single_sided:
            # The other half of the cut. Same sketch, opposite side.
            mirror = sf.AddNewPocket(sketch, 0 if through_all else half)
            if through_all:
                # 'through all' reaches only one way from the sketch plane, so
                # a "both directions" through-cut came back at exactly half the
                # expected volume (measured 2026-10-23: Ø12.7 x 25.4 read
                # 1608.8 mm3 instead of 3217.6). Two up-to-last cuts is what
                # "through, both ways" means.
                mirror.FirstLimit.LimitMode = 2
            mirror.DirectionOrientation = 0 if reverse else 1
            self._try_update(part, mirror)
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
                # Unreachable while _settle and this measurement agree, but a
                # race between them would otherwise report a success for a cut
                # that did nothing.
                return (
                    f"FEATURE_NO_EFFECT: pocket '{pocket.Name}' was created in "
                    f"the tree but removed no material"
                    + (" in the requested direction" if explicit_direction
                       else " in either direction")
                    + f" (volume stayed {before:.1f} mm3). Check that the "
                    "profile intersects the solid."
                )
            flip_note = " (direction auto-flipped to reach material)" if flipped else ""
            if single_sided:
                flip_note = (" (only one side of the sketch plane had material, so "
                             "this is a single-sided cut despite symmetric=true)")
            if update_error is not None:
                flip_note += (" (CATIA raised during the update, but the cut is "
                              "in the geometry - kept)")
            extent = self._pocket_extent(through_all, depth, symmetric)
            names = f"'{pocket.Name}'" if mirror is None else \
                f"'{pocket.Name}' + '{mirror.Name}' (two one-sided cuts)"
            return (
                f"Pocket {names} created: {extent}{flip_note}. "
                f"Removed {removed:.1f} mm3 ({before:.1f} -> {after:.1f})."
            )
        # No volume, no verdict: say which measurement failed rather than
        # blaming "this installation" for a cut the same session may verify on
        # the next call.
        extent = self._pocket_extent(through_all, depth, symmetric)
        why = f" ({volume_note})" if volume_note else ""
        raised = (f" CATIA also raised during the update: {update_error}."
                  if update_error is not None else "")
        return (
            f"UNVERIFIED: pocket '{pocket.Name}' was created: {extent}. The cut "
            f"could not be checked against the body volume{why}, so it is "
            f"reported on CATIA's return alone - check the geometry before "
            f"relying on it.{raised}"
        )

    @staticmethod
    def _pocket_extent(through_all: bool, depth: float, symmetric: bool) -> str:
        """Describe the cut in the caller's terms, not CATIA's."""
        if through_all:
            return "through all, both directions" if symmetric else "through all"
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

    def _remove_feature(self, part: Any, feature: Any) -> bool:
        """Remove a feature from the tree; True if the call went through.

        A silent failure here is how a document ends up "dirty": the caller is
        told the feature was rolled back while the dead node stays in the tree
        and poisons every later update. Return the outcome so the message can
        stay honest.
        """
        try:
            selection = self.conn.hso
            selection.Clear()
            selection.Add(feature)
            selection.Delete()
            return True
        except Exception:  # noqa: BLE001 - nothing better to do than carry on
            return False
        finally:
            try:
                self.conn.hso.Clear()
            except Exception:
                pass

    @staticmethod
    def _try_update(part: Any, feature: Any) -> Exception | None:
        """Call UpdateObject, returning the exception instead of raising it."""
        try:
            part.UpdateObject(feature)
            return None
        except Exception as exc:  # noqa: BLE001 - the caller measures instead
            return exc

    def _pad_other_direction(self, part: Any, body: Any, sketch: Any, height: float,
                             symmetric: bool, direction: str,
                             before: float | None) -> float | None:
        """How much the SAME pad would add the other way, or None if it adds none.

        Builds a throwaway pad, measures, and deletes it again, so the diagnosis
        costs one feature and one update only on the failure path. ``None`` means
        "it adds nothing that way either", which is the case where the profile or
        the material really is the problem.
        """
        probe = None
        try:
            probe = part.ShapeFactory.AddNewPad(sketch, height / 2.0 if symmetric else height)
            if symmetric:
                probe.IsSymmetric = True
            else:
                probe.DirectionOrientation = 0 if direction == "reverse" else 1
            part.UpdateObject(probe)
        except Exception:  # noqa: BLE001 - a failed probe answers "no"
            if probe is not None:
                self._remove_feature(part, probe)
            return None
        grew = None
        try:
            after = self._body_volume_mm3(part, body)
            if before is not None and after is not None and after - before > 1e-6:
                grew = after - before
        finally:
            self._remove_feature(part, probe)
        return grew

    def _settle(self, part: Any, body: Any, before: float | None,
                expect: str = "subtract") -> tuple[float | None, bool | None]:
        """Measure what a feature actually did: ``(after_volume, took_effect)``.

        CATIA reports failures two ways that both lie: an update that raises
        after the geometry was already computed, and an update that "succeeds"
        while removing nothing. The volume delta is the only honest witness, so
        every additive/subtractive feature goes through here instead of
        trusting the COM result.
        """
        after = self._body_volume_mm3(part, body)
        if before is None or after is None:
            return after, None
        delta = after - before
        if expect == "add":
            return after, delta > 1e-6
        return after, delta < -1e-6

    def _safe_update(self, part: Any, feature: Any) -> None:
        """Update a feature; if CATIA rejects it, take the dead node back out.

        A failed Part Design feature left in the tree is not inert: **every**
        later Update of the part then fails at it, so one rejected fillet turns
        a 22-feature part into one that cannot be rebuilt at all. Reproduced on
        B30 2026-10-24: fillet R40 on a 40x20x10 block -> shape_count 1 -> 2,
        `update_part` fails, and the next pad fails too, until the dead
        `倒圆角.1` is deleted. The batch paths have rolled back since 0.3.7; the
        single-feature paths (fillet / chamfer / mirror / shell / thickness /
        boolean) called `UpdateObject` raw and did not.
        """
        try:
            part.UpdateObject(feature)
            return
        except Exception as error:  # noqa: BLE001 - reported with the rollback state
            name = str(getattr(feature, "Name", "the new feature"))
            gone = self._remove_feature(part, feature)
            if gone:
                raise FeatureRejected(
                    f"CATIA rejected the feature and it was removed from the tree "
                    f"again (rolled_back: true), so the rest of the part still "
                    f"updates. Feature was '{name}'. Original error: {error}",
                    rolled_back=True,
                ) from error
            raise FeatureRejected(
                f"CATIA rejected the feature '{name}' and it could NOT be removed "
                "from the tree (rolled_back: false). Every later Update of this "
                "part will now fail at that feature, so delete it before doing "
                f"anything else: catia_delete_feature(feature_name='{name}'). "
                f"Original error: {error}",
                rolled_back=False,
            ) from error

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

    # ── stable edge references ──────────────────────────────────────────
    #
    # ``Edge.N`` is an enumeration position, not an identity: it is renumbered
    # by every feature that changes the topology. Reported 2026-10-23 with the
    # numbers - the same edge went from Edge.30 to Edge.34 and from 17.6112 to
    # 9.8429 mm long after one neighbouring fillet, and a script that reused
    # the old index filleted a concave edge instead (volume went UP by 49.1
    # mm3). The bounding faces (``Pad.6;2``, ``Sketch.7;2``) and the edge's own
    # length do not drift, so those are what the tools take now.

    def _edge_table(self, body: Any) -> list[dict[str, Any]]:
        """Every solid edge, with the handles that survive a rebuild."""
        part = self.conn.get_active_part()
        spa = self._topo_measure(part)
        cells = topology_cells(self.conn, body, "Edge")
        table: list[dict[str, Any]] = []
        for index, cell in enumerate(cells, start=1):
            nls = nls_of(cell)
            faces = face_keys(nls)
            length = self._cell_measure(spa, cell, "Length")
            table.append({
                "index": index,
                "name": f"Edge.{index}",
                "cell": cell,
                "nls": nls,
                "bounded_by": faces,
                "length": length,
                "fingerprint": edge_fingerprint(faces, length),
            })
        return table

    @staticmethod
    def _parse_edge_spec(spec: Any) -> tuple[int | None, list[str], float | None]:
        """``Edge.N`` | ``L17.6112|Pad.6;2|Sketch.7;2`` | dict -> (index, faces, length)."""
        if isinstance(spec, int):
            return spec, [], None
        if isinstance(spec, dict):
            index = spec.get("index")
            faces = spec.get("faces") or spec.get("bounded_by") or []
            if isinstance(faces, str):
                faces = [f.strip() for f in faces.split("|") if f.strip()]
            length = spec.get("length_mm", spec.get("length"))
            return (int(index) if index is not None else None), list(faces), (
                float(length) if length is not None else None)
        text = str(spec).strip()
        m = re.fullmatch(r"[Ee]dge\.(\d+)", text)
        if m:
            return int(m.group(1)), [], None
        stamp, _, rest = text.partition("|")
        m = re.fullmatch(r"[Ll](-?\d+(?:\.\d+)?|\?)", stamp.strip())
        if m:
            length = None if m.group(1) == "?" else float(m.group(1))
            faces = [f for f in rest.split("|") if f.strip()]
            return None, faces, length
        raise ValueError(
            f"Edge reference '{spec}' is neither 'Edge.N' nor a fingerprint from "
            "catia_list_edges ('L17.6112|Pad.6;2|Sketch.7;2'), nor an object "
            "like {\"length_mm\": 17.6112, \"faces\": [\"Pad.6;2\"]}."
        )

    def _resolve_edge_specs(self, body: Any, specs: list[Any],
                            table: list[dict[str, Any]] | None = None,
                            ) -> tuple[list[str], list[Any]]:
        """Resolve edge references against the body as it is *now*.

        Returns ``(labels, references)``. The labels keep both handles
        (``Edge.30 (L17.6112|Pad.6;2|...)``) so the caller can see what was
        matched. Faces decide the match; the length only breaks a tie, because
        a neighbouring fillet legitimately shortens an edge.

        ``table`` lets a caller that has just enumerated the edges reuse that
        enumeration. It is not an optimisation: re-enumerating clears the
        selection, and every Reference from the previous enumeration dies with
        it - so a call that mixes anchors (which enumerate) with names has to
        resolve both against ONE table.
        """
        if table is None:
            table = self._edge_table(body)
        if not table:
            raise RuntimeError("The active body has no edges to fillet yet.")
        labels: list[str] = []
        refs: list[Any] = []
        for spec in specs:
            index, faces, length = self._parse_edge_spec(spec)
            if index is not None:
                if not 1 <= index <= len(table):
                    raise RuntimeError(
                        f"Edge.{index} is out of range: the final shape exposes "
                        f"{len(table)} edge(s).")
                entry = table[index - 1]
                if faces and set(faces) != set(entry["bounded_by"]):
                    # An index plus faces means the caller wanted a specific one
                    # of two indistinguishable edges. If the index no longer
                    # carries those faces, the tree changed under them, and
                    # quietly filleting the wrong edge is worse than stopping.
                    raise RuntimeError(
                        f"Edge.{index} is not the edge you fingerprinted: its "
                        f"bounding faces are {sorted(entry['bounded_by'])} but the "
                        f"spec gave {sorted(set(faces))}. Re-run catia_list_edges "
                        "and take the Edge.N from that listing."
                    )
            else:
                entry = self._match_edge(table, faces, length, spec)
            labels.append(f"{entry['name']} ({entry['fingerprint']})")
            refs.append(entry["cell"].Reference)
        return labels, refs

    @classmethod
    def _match_edge(cls, table: list[dict[str, Any]], faces: list[str],
                    length: float | None, spec: Any) -> dict[str, Any]:
        wanted = set(faces)
        candidates = [e for e in table if wanted and set(e["bounded_by"]) == wanted]
        if not candidates and wanted:
            candidates = [e for e in table if wanted <= set(e["bounded_by"])]
        if not candidates and not wanted:
            candidates = list(table)
        if length is not None and candidates:
            close = [e for e in candidates if e["length"] is not None
                     and abs(e["length"] - length) <= 0.01]
            if close:
                candidates = close
            elif len(candidates) > 1:
                candidates = sorted(
                    candidates,
                    key=lambda e: abs((e["length"] or 0.0) - length))[:1]
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            near = sorted(table, key=lambda e: abs((e["length"] or 0.0) - (length or 0.0)))[:5]
            raise RuntimeError(
                f"No edge matches '{spec}'. The body's edges are (nearest first): "
                + "; ".join(f"{e['fingerprint']}" for e in near)
                + ". Fingerprints come from catia_list_edges and are re-resolved "
                "on every call, so they survive renumbering - an Edge.N does not."
            )
        same_print = len({e["fingerprint"] for e in candidates}) == 1
        raise RuntimeError(
            f"'{spec}' matches {len(candidates)} edges: "
            + "; ".join(f"{e['name']} len={e['length']} {e['fingerprint']}"
                        for e in candidates[:6])
            + (" - and their FINGERPRINTS ARE IDENTICAL, so length and bounding "
               "faces cannot tell them apart. This happens at a fillet boundary: "
               "the fillet cuts a new edge beside the original one, with the same "
               "length and the same two faces. Pass "
               '{"index": N, "faces": [...]} instead of a bare fingerprint - the '
               "Edge.N values above are the disambiguator, and nothing rebuilds "
               "between this listing and the retry, so they are still valid."
               if same_print else
               ". Add length_mm, or pass one fingerprint per edge.")
        )

    def _fillet(self, args: dict[str, Any]) -> str:
        names = args.get("edge_names") or []
        if isinstance(names, str):
            names = [names]
        anchors, tol = self._anchor_args(args)
        if anchors:
            specs = list(names)
            if args.get("edge_name"):
                specs.append(args["edge_name"])
            return self._edges_by_anchor(
                args["radius"], specs, anchors, tol, self._propagation(args),
                kind="fillet")
        if len(names) > 1 or (names and not args.get("edge_name")):
            return self._fillet_many(args["radius"], list(names),
                                     self._propagation(args))
        target = args.get("edge_name")
        return self._fillet_one(args["radius"], target,
                                self._propagation(args))

    @staticmethod
    def _anchor_args(args: dict[str, Any]) -> tuple[list[list[float]], float]:
        """``edge_anchors`` / ``anchor_tol`` in the shapes callers actually send."""
        raw = args.get("edge_anchors") or []
        if isinstance(raw, dict):
            raw = raw.get("points") or []
        cleaned: list[list[float]] = []
        for item in raw:
            if isinstance(item, dict):
                item = [item.get("x"), item.get("y"), item.get("z")]
            try:
                values = [float(v) for v in item]
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"edge_anchors entries must be [x, y, z]; got {item!r}."
                ) from exc
            if len(values) != 3:
                raise RuntimeError(
                    f"edge_anchors entries must be [x, y, z]; got {item!r}."
                )
            cleaned.append(values)
        tol = args.get("anchor_tol")
        tol = 0.5 if tol is None else float(tol)
        if tol <= 0:
            raise RuntimeError(
                f"anchor_tol must be greater than 0; got {tol}. It is a "
                "distance in mm - 0.5 accepts a coordinate rounded to a tenth."
            )
        return cleaned, tol

    def _edges_by_anchor(self, value: float, specs: list[Any],
                         anchors: list[list[float]], tol: float,
                         propagation: int, kind: str = "fillet",
                         angle: float = 45.0) -> str:
        """Fillet/chamfer a list of coordinate anchors instead of edge names."""
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        labels, refs, report, table = self._anchors_to_edges(body, anchors, tol)
        if specs:
            more_labels, more_refs = self._resolve_edge_specs(body, specs,
                                                              table=table)
            labels = more_labels + labels
            refs = more_refs + refs

        if kind == "chamfer":
            def add(ref: Any) -> Any:
                return sf.AddNewChamfer(
                    ref,
                    1,        # catTangencyChamferPropagation
                    1,        # catLengthAngleChamfer mode
                    0,        # catNoReverseChamfer
                    value,
                    angle,
                )
            label = "Chamfer"
        else:
            def add(ref: Any) -> Any:
                return sf.AddNewSolidEdgeFilletWithConstantRadius(
                    ref, propagation, value)
            label = "Fillet R"

        try:
            out = self._apply_fillets(value, labels, refs, add, part=part,
                                      label=label,
                                      resolve=lambda: (labels, refs))
        finally:
            self._clear_selection()
            self.conn.refresh_display()
        return out + " " + report

    @staticmethod
    def _propagation(args: dict[str, Any]) -> int:
        """CATIA edge-propagation enum: 0 = only the given edges, 1 = tangency.

        The default is 0 (off) since 0.3.7: with tangency on, selecting one edge
        of a tangent chain fillets the whole chain, which is a silent way to cut
        geometry nobody asked for - reported 2026-10-23 from a part where a
        10-edge batch reached a non-target edge and broke the part's symmetry.
        Pass ``"propagation": "tangency"`` to get the old behaviour.
        """
        return 1 if args.get("propagation") == "tangency" else 0

    def _fillet_many(self, radius: float, specs: list[Any],
                     propagation: int = 0) -> str:
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
           When that single rebuild fails, the batch falls back to one edge at a
           time (``_apply_fillets``), because separate updates are what actually
           works for adjacent edges - measured by the reporter: three edges of
           one face fail as a batch and succeed one by one.
        """
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        names, refs = self._resolve_edge_specs(body, specs)

        def add(ref: Any) -> Any:
            return sf.AddNewSolidEdgeFilletWithConstantRadius(ref, propagation, radius)

        try:
            return self._apply_fillets(
                radius, names, refs, add, part=part,
                resolve=lambda: self._resolve_edge_specs(body, specs))
        finally:
            self._clear_selection()
            self.conn.refresh_display()

    def _apply_fillets(self, radius: float, names: list[str], refs: list[Any],
                       add: Any = None, part: Any = None,
                       label: str = "Fillet R",
                       resolve: Any = None) -> str:
        """Create one fillet per (name, ref), then rebuild once.

        ``add`` is injectable for tests; it may return the new feature (in which
        case a failed rebuild rolls it back) or None. ``resolve`` re-reads the
        edge references from scratch and is what makes the sequential retry
        possible: deleting the batch rebuilds the topology, so the references
        that were resolved for it are dead by the time the retry needs them.
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
            # One edge that cannot rebuild used to lose the whole batch: "0/3
            # done" for three edges of which two were fine (reported 2026-10-23).
            # Separate updates are what actually works for adjacent edges, so
            # fall back to one edge at a time - the batch was only ever an
            # optimisation.
            self._remove_created(created)
            done, failed, created = [], [], []
            fresh_names, fresh_refs = (resolve() if resolve is not None
                                       else (names, refs))
            for name, ref in zip(fresh_names, fresh_refs):
                try:
                    feature = add(ref)
                except Exception as error:    # noqa: BLE001 - reported per edge
                    failed.append((name, str(error).splitlines()[0][:120]))
                    continue
                try:
                    if feature is not None:
                        part.UpdateObject(feature)
                    else:
                        part.Update()
                except Exception as error:    # noqa: BLE001 - roll this one back
                    if feature is not None:
                        self._remove_created([feature])
                    failed.append((name, "rebuild failed: "
                                        + str(error).splitlines()[0][:110]))
                    continue
                done.append(name)
                if feature is not None:
                    created.append(feature)
            head = (f"{shown}: {len(done)}/{len(names)} edge(s) done after a "
                    f"one-at-a-time retry (the batch rebuild failed: {rebuild_error})")
        if done:
            head += f" ({', '.join(done)})"
            # Which radius each edge got, spelled out: the same edge can be sent
            # again in a later call with a different radius, and the reply used to
            # name the edges without saying what was applied to them (reported
            # 2026-10-25, after two edges were rebuilt at the wrong radius).
            try:
                stamp = f"{float(radius):g}"
            except (TypeError, ValueError):
                stamp = str(radius)
            short = [str(name).split(" ")[0] for name in done]
            head += " [applied " + ", ".join(f"{n}@R{stamp}" for n in short) + "]"
        if failed:
            head += ". Rejected: " + "; ".join(f"{n} -> {m}" for n, m in failed)
            head += (
                ". A refusal here is usually the topology, not the argument: edges "
                "that came out of catia_gsd_close_surface cannot take a Part Design "
                "fillet, while edges of a pad in the same body can."
            )
            hint = self._coplanar_hint([n for n, _ in failed])
            if hint:
                head += hint
        return head

    @staticmethod
    def _bounding_faces(label: str) -> int:
        """How many faces the fingerprint inside ``label`` names (0 when none)."""
        inner = label[label.find("(") + 1:label.rfind(")")] if "(" in label else label
        return len([part for part in inner.split("|") if ";" in part])

    @staticmethod
    def _coplanar_hint(labels: list[str]) -> str:
        """Name the real cause when a rejected edge carries three or more faces.

        A solid edge has two bounding faces. Three or more means two of them are
        coplanar - they share a normal and lie in the same plane - so the "edge"
        between them is a 180 degree seam with no dihedral angle and no material
        to round off. Measured by the reporter 2026-10-24: after a boolean union
        of two coplanar bosses, `catia_list_edges` hands out fingerprints like
        `L43.1800|Sketch.9;2|Sketch.10;1|Sketch.9;1`, and every fillet on them
        fails with a bare 'UpdateObject failed' - while their successful edges
        all show exactly two faces. It is a hint rather than a refusal: this
        server cannot measure face normals cheaply, and a part that legitimately
        came out with three bounding faces would still be worth trying.
        """
        suspicious = [label for label in labels
                      if PartDesignTools._bounding_faces(label) >= 3]
        if not suspicious:
            return ""
        return (
            f" {len(suspicious)} of these edge(s) are bounded by three or more "
            "faces, and a real edge has two: the extra pair is most likely "
            "coplanar (same normal, same plane), so the seam between them has no "
            "dihedral angle and a fillet has no material to remove. That is what "
            "a boolean union of two coplanar bosses leaves behind. Confirm with "
            "catia_list_faces, then either draw the radius in the sketch, or cut "
            "the transition with catia_gsd_* instead of a Part Design fillet. "
            "SECOND CAUSE, and it looks identical: if this edge shares an endpoint "
            "with an edge you have ALREADY filleted, the faces of that fillet are "
            "the extra pair - the boundary really has three faces now. Fillet the "
            "whole chain in ONE call (edge_names=[...]) instead of one by one; "
            "reported 2026-10-25, a six-sided tip took R3.81 on one side and R1.27 "
            "on the next, then refused the third edge for exactly this reason."
        )

    def _fillet_one(self, radius: float, edge_name: Any,
                    propagation: int = 0) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        if edge_name:
            labels, refs = self._resolve_edge_specs(body, [edge_name])
            target, scope = refs[0], labels[0]
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

        try:
            self._safe_update(part, fillet)
        except FeatureRejected as error:
            hint = self._coplanar_hint([scope])
            if hint:
                raise FeatureRejected(str(error) + hint,
                                      rolled_back=error.rolled_back) from error
            raise
        self.conn.refresh_display()
        self._clear_selection()
        return (f"Fillet created: R{radius} mm on {scope}. Feature: '{fillet.Name}'"
                + self._tree_position(part, body, fillet))

    def _tree_position(self, part: Any, body: Any, feature: Any) -> str:
        """Where the new feature landed in the tree, and why it cannot move.

        Reported 2026-10-25: every Part Design feature is appended to the end of
        the tree and there is no way to place it earlier, so an edge that only
        exists *before* a later feature can never be filleted afterwards - the
        fillet would have to run before that feature, which is a different tree
        order. CATIA's automation surface has no reorder: ``Part`` and ``Body``
        expose no Insert / Reorder / Move (checked on B30 by listing the members
        of both), and dragging is UI-only. Saying where the feature sits and that
        it is last is the only honest answer; the alternative used to be silence
        and a caller wondering why the geometry came out wrong.
        """
        try:
            shapes = body.Shapes
            total = shapes.Count
            index = None
            for i in range(1, total + 1):
                if str(shapes.Item(i).Name) == str(feature.Name):
                    index = i
                    break
        except Exception:  # noqa: BLE001 - the position is informative only
            return ""
        if index is None:
            return ""
        note = (f" It is feature {index} of {total} in '{body.Name}'. New features are "
                "always appended and CATIA exposes no reorder (Part and Body have no "
                "Insert / Reorder / Move), so a fillet cannot be placed before an "
                "existing feature - rebuild in the order you need if it has to.")
        if index != total:
            note += (f" Something already sits after it ({total - index} feature(s)), "
                     "which is worth knowing: the tree order decides which edges exist.")
        return note

    def _chamfer(self, args: dict[str, Any]) -> str:
        anchors, tol = self._anchor_args(args)
        if anchors:
            specs = []
            names = args.get("edge_names") or []
            if isinstance(names, str):
                names = [names]
            specs.extend(names)
            if args.get("edge_name"):
                specs.append(args["edge_name"])
            return self._edges_by_anchor(
                float(args["length"]), specs, anchors, tol, 1,
                kind="chamfer", angle=float(args.get("angle", 45)))
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

    def _chamfer_many(self, length: float, angle: float, specs: list[Any]) -> str:
        """Same batching rule as ``_fillet_many``: resolve all, build all, rebuild once."""
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        names, refs = self._resolve_edge_specs(body, specs)

        def add(ref: Any) -> Any:
            return sf.AddNewChamfer(
                ref,
                1,       # catTangencyChamferPropagation
                1,       # catLengthAngleChamfer mode (0 = length-length)
                0,       # catNoReverseChamfer orientation
                length,
                angle,   # degrees
            )

        try:
            return self._apply_fillets(length, names, refs, add, part=part,
                                       label="Chamfer",
                                       resolve=lambda: self._resolve_edge_specs(body, specs))
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

    def _chamfer_one(self, length: float, angle: float, edge_name: Any) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        if edge_name:
            labels, refs = self._resolve_edge_specs(body, [edge_name])
            target, scope = refs[0], labels[0]
        else:
            target = part.CreateReferenceFromObject(self._get_last_shape())
            scope = "all edges of the last feature"

        # AddNewChamfer(iObjectToChamfer, iPropagation, iMode, iOrientation,
        #               iLength1, iLength2OrAngle) - 6 parameters.
        # iMode matters: 0 is catLengthLength, so the 6th argument is read as a
        # second length. Passing 45 there asked CATIA for a 2 x 45 mm bevel,
        # which can never build - every chamfer failed at UpdateObject until
        # this was measured (2026-10-23). Mode 1 is catLengthAngle.
        chamfer = sf.AddNewChamfer(
            target,
            1,       # catTangencyChamferPropagation
            1,       # catLengthAngleChamfer mode
            0,       # catNoReverseChamfer orientation
            length,
            angle,   # degrees, verified: 2 mm / 45 deg removes 20.00 mm3 on a 10 mm edge
        )

        update_error = self._try_update(part, chamfer)
        self.conn.refresh_display()
        self._clear_selection()
        if update_error is not None:
            gone = self._remove_feature(part, chamfer)
            raise RuntimeError(
                "CATIA rejected the chamfer"
                + (" and it was removed from the tree" if gone else
                   " (the dead feature could NOT be deleted - close the document "
                   "without saving)")
                + f". {length} mm at {angle} deg on {scope}. A chamfer longer "
                "than the adjacent face, or an edge shared with an existing "
                f"fillet, cannot build. Original error: {update_error}"
            ) from update_error
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
        before, volume_note = self._volume_probe(part, body)

        # AddNewHole takes a support FACE reference; the sketch-positioned
        # form is AddNewHoleFromSketch. Diameter is a read-only Length
        # parameter object whose .Value is assigned.
        # The second argument of AddNewHoleFromSketch is NOT the depth: measured
        # 2026-10-23 on V5-6R2020 B30, a D4 hole asked for depth 4 in a 10 mm
        # block removed 125.7 mm3 = pi*2^2*10, i.e. straight through. The real
        # control is the bottom limit:
        #   BottomLimit.Dimension.Value holds the depth and defaults to the wall
        #   thickness; LimitMode 0 is an offset limit, 2 is up-to-last.
        # With LimitMode 0 + Dimension = depth + a flat bottom, D4 x 4 removes
        # exactly 50.3 mm3 and D4 x 6 removes 75.4 mm3 - both match pi*r^2*d.
        hole = sf.AddNewHoleFromSketch(sketch, 0 if through_all else depth)
        hole.Diameter.Value = diameter
        hole.BottomType = 0  # catFlatBottom; a V bottom adds the conical tip
        if through_all:
            # BottomLimit is the hole's depth control (FirstLimit is the
            # pocket's). LimitMode 2 = catUpToLastLimit.
            hole.BottomLimit.LimitMode = 2
        else:
            hole.BottomLimit.LimitMode = 0          # catOffsetLimit
            hole.BottomLimit.Dimension.Value = depth

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

        update_error = self._try_update(part, hole)
        self.conn.refresh_display()

        after, took = self._settle(part, body, before, expect="subtract")
        extent = "through all" if through_all else f"depth {depth} mm"
        if took is False:
            gone = self._remove_feature(part, hole)
            raise RuntimeError(
                "The hole removed no material, so it was "
                + ("rolled back" if gone else
                   "left in the tree (the dead feature could NOT be deleted - "
                   "close the document without saving)")
                + f" (volume stayed {before:.1f} mm3). The sketch has to hold a "
                "closed CIRCLE - the Hole feature takes the bore from the "
                "profile, so a bare point produces a feature that cannot "
                "update." + ("" if through_all else
                             " A depth deeper than the local wall also fails; "
                             "pass through_all for that case.")
                + (f" Original error: {update_error}" if update_error else "")
            ) from (update_error or None)
        if before is not None and after is not None:
            removed = before - after
            note = ""
            if update_error is not None:
                note = (" (CATIA raised during the update, but the bore is in the "
                        "geometry - kept)")
            return (
                f"Hole created: D{diameter} mm, {extent}. Removed {removed:.1f} mm3 "
                f"({before:.1f} -> {after:.1f}). Feature: '{hole.Name}'."
                f"{type_note}{note}"
            )
        why = f" ({volume_note})" if volume_note else ""
        return (
            f"UNVERIFIED: hole '{hole.Name}' created: D{diameter} mm, {extent}, "
            f"but the bore was not checked against the body volume{why}.{type_note}"
        )

    def _rect_pattern(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        # Not _get_last_shape(None): that returns the BODY (right for topology
        # searches), and patterning the body fails at UpdateObject - the same
        # trap circ_pattern hit in 0.3.7. Reproduced 2026-10-24 while verifying
        # the new axis arguments: every rect_pattern without feature_name failed,
        # with or without dir1_axis.
        feature = self._pattern_seed(body, args.get("feature_name"))
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
        dir1_axis = (args.get("dir1_axis") or "").lower() or None
        dir2_axis = (args.get("dir2_axis") or "").lower() or None
        if bool(dir1_edge) != bool(dir2_edge):
            raise ValueError(
                "Pass both 'dir1_edge' and 'dir2_edge' ('Edge.N' from "
                "catia_list_edges, two adjacent edges sharing a corner), "
                "or neither."
            )
        if (dir1_axis or dir2_axis) and (dir1_edge or dir2_edge):
            raise ValueError(
                "Use either dir1_axis/dir2_axis or dir1_edge/dir2_edge, not both. "
                "The axis form is the readable one."
            )
        for value in (dir1_axis, dir2_axis):
            if value and value not in ("x", "y", "z"):
                raise ValueError("dir1_axis / dir2_axis must be 'x', 'y' or 'z'")
        if dir1_axis and dir2_axis and dir1_axis == dir2_axis:
            raise ValueError(
                "dir1_axis and dir2_axis are the same axis; CATIA rejects two "
                "parallel directions, and so does this tool."
            )
        reverse1 = bool(args.get("reverse_dir1"))
        reverse2 = bool(args.get("reverse_dir2"))
        if dir1_axis or dir2_axis:
            # A datum plane's reference gives its NORMAL as the direction, so
            # x -> PlaneYZ, y -> PlaneZX, z -> PlaneXY. Same mapping the edge
            # path documents, minus the guesswork about which way an edge runs.
            planes = self.conn.get_origin_elements()
            by_axis = {"x": "yz", "y": "zx", "z": "xy"}
            # CATIA needs dir1 x dir2 to be RIGHT-handed, or the update fails with
            # a bare "UpdateObject failed". Measured on B30 2026-10-24 with a pad
            # seed: (x,y) ok, (y,z) ok, (z,x) ok, (z,y) failed, (x,z) failed -
            # the failures are exactly the left-handed pairs, and (z) alone took
            # the same path because the old default second axis was 'y'.
            cyclic = {"x": "y", "y": "z", "z": "x"}
            first = dir1_axis or "x"
            second = dir2_axis or cyclic[first]
            if second == first:
                raise ValueError(
                    f"dir1_axis and dir2_axis are both '{first}'; a pattern needs two "
                    "different directions."
                )
            # Right-handed or nothing: CATIA answers a left-handed frame with a bare
            # "UpdateObject failed". Measured on B30 2026-10-24: (x,y), (y,z) and
            # (z,x) build; (x,z), (z,y) and (y,x) do not. A reversal flips the
            # handedness too, so it is part of the test. Rather than refuse the
            # caller's axes, swap the two slots (counts and spacings with them) -
            # the resulting geometry is identical.
            right_handed = ((first, second) in (("x", "y"), ("y", "z"), ("z", "x"))
                            and reverse1 == reverse2)
            swapped = False
            if not right_handed:
                first, second = second, first
                reverse1, reverse2 = reverse2, reverse1
                d1_count, d2_count = d2_count, d1_count
                d1_spacing, d2_spacing = d2_spacing, d1_spacing
                swapped = True
            ref1 = part.CreateReferenceFromObject(planes[by_axis[first]])
            ref2 = part.CreateReferenceFromObject(planes[by_axis[second]])
            directions = (f"axis {first}{'-' if reverse1 else '+'} -> dir1, "
                          f"axis {second}{'-' if reverse2 else '+'} -> dir2")
            if swapped:
                directions += " (axes swapped internally: CATIA rejects a left-handed pair)"
        elif dir1_edge:
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
            reverse1, reverse2,  # reversed dir1/dir2
            0.0,           # rotation angle
        )

        self._safe_update(part, pattern)
        self.conn.refresh_display()
        after = self._body_volume_mm3(part, body)
        effect = ""
        if before is not None and after is not None:
            effect = f" Volume {before:.1f} -> {after:.1f} mm3."
            # A pattern with no material delta used to be reported as a plain
            # success, so a 3x1 grid that put every copy outside the model read
            # exactly like one that worked (reported 2026-10-25). The copies go
            # only to the +side of each direction - the seed keeps index 1 - so a
            # seed at the far end of the part has nowhere to go.
            if abs(after - before) < 1e-6 and d1_count * d2_count > 1:
                effect += (
                    " WARNING: the pattern added no material at all. CATIA places "
                    "the copies only on the side each direction points to (the seed "
                    "stays at index 1), so a seed at the far end of the model puts "
                    "them all outside it. Set reverse_dir1 / reverse_dir2 to flip "
                    "that side, or use a larger spacing."
                )
        return (
            f"Rectangular pattern '{pattern.Name}' created: {d1_count}x{d2_count}, "
            f"spacing {d1_spacing}x{d2_spacing} mm, directions: {directions}."
            + effect
        )

    # Measured on V5-6R2020 B30, 2026-10-24: the old recipe minted the axis line
    # in a throwaway part document and closed it before building the pattern.
    # Every AddNewCircPattern then failed at UpdateObject - the Reference belongs
    # to the document that owns the geometry, and closing that document kills it
    # (the "survives closing" note in the old comment was wrong). A line in the
    # part's own geometrical set works, and so does patterning a part that has a
    # geometrical set (the earlier "GSD geometry in the part fails with
    # 0x80020009" guard was a misdiagnosis of the same closed-document problem:
    # with a live axis, pad + pocket + geoset + pattern all succeed).
    _PATTERN_GEOSET = "PatternAxis"

    def _pattern_axis_reference(self, part: Any, axis: str,
                                center: list[float]) -> Any:
        """A line through ``center`` along ``axis``, inside the part itself."""
        hsf = part.HybridShapeFactory
        geoset = self._pattern_geoset(part)
        d = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[axis]
        cx, cy, cz = center
        p1 = hsf.AddNewPointCoord(cx - 60 * d[0], cy - 60 * d[1], cz - 60 * d[2])
        p2 = hsf.AddNewPointCoord(cx + 60 * d[0], cy + 60 * d[1], cz + 60 * d[2])
        geoset.AppendHybridShape(p1)
        geoset.AppendHybridShape(p2)
        line = hsf.AddNewLinePtPt(part.CreateReferenceFromObject(p1),
                                  part.CreateReferenceFromObject(p2))
        try:
            line.Name = f"PatternAxis_{axis.upper()}"
        except Exception:  # noqa: BLE001 - a default name is fine
            pass
        geoset.AppendHybridShape(line)
        # A full Update, not UpdateObject(line): measured 2026-10-24, the
        # pattern still failed at UpdateObject when only the line had been
        # updated, and succeeded once the whole part was rebuilt here.
        part.Update()
        return part.CreateReferenceFromObject(line)

    def _pattern_geoset(self, part: Any) -> Any:
        """The geometrical set that holds pattern axis lines (created on demand)."""
        try:
            bodies = part.HybridBodies
            for i in range(1, bodies.Count + 1):
                hb = bodies.Item(i)
                if str(hb.Name) == self._PATTERN_GEOSET:
                    return hb
            hb = bodies.Add()
            hb.Name = self._PATTERN_GEOSET
            return hb
        except Exception as exc:  # noqa: BLE001 - reported by the caller
            raise RuntimeError(
                f"Could not create the '{self._PATTERN_GEOSET}' geometrical set "
                f"that holds the pattern axis: {exc}"
            ) from exc

    @staticmethod
    def _pattern_seed(body: Any, feature_name: str | None) -> Any:
        """The feature a pattern duplicates: the named one, or the last one.

        Not ``_get_last_shape(None)``: that returns the body (which is right for
        topology searches) and patterning the body fails at UpdateObject.
        """
        shapes = body.Shapes
        if shapes.Count == 0:
            raise RuntimeError("No features to pattern in the active body.")
        if feature_name:
            try:
                return shapes.Item(feature_name)
            except Exception:
                names = ", ".join(str(shapes.Item(i).Name)
                                  for i in range(1, shapes.Count + 1))
                raise RuntimeError(
                    f"No feature named '{feature_name}' in the active body. "
                    f"Available: {names or 'none'}."
                ) from None
        return shapes.Item(shapes.Count)

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
        # The seed must be a FEATURE, not the body: _get_last_shape(None) returns
        # the body on purpose (topology searches want it), but patterning the
        # body is what made every circular pattern fail at update - the pattern
        # has to duplicate one feature.
        self._pattern_seed(body, args.get("feature_name"))

        # The axis lives in the part itself, and stays there: a Reference to
        # geometry in a document that gets closed dies with it, which is exactly
        # how this tool used to fail every time (see _pattern_axis_reference).
        axis_ref = self._pattern_axis_reference(part, axis, center)
        center_ref = part.CreateReferenceFromName("")
        feature = self._pattern_seed(body, args.get("feature_name"))

        before, volume_note = self._volume_probe(part, body)
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

        update_error = self._try_update(part, pattern)
        self.conn.refresh_display()
        after = self._body_volume_mm3(part, body)

        moved = None
        if before is not None and after is not None:
            moved = after - before
        if update_error is not None and not moved:
            # Nothing changed and CATIA refused: this really is a failure, so
            # take the dead node out - but the seed feature is NOT touched, and
            # that is worth saying because the old failure mode looked like the
            # seed had vanished (it had not; a dead 圆形阵列.1 was in the tree).
            gone = self._remove_feature(part, pattern)
            raise RuntimeError(
                "CATIA rejected the circular pattern"
                + (" and it was removed from the tree" if gone else
                   " (the dead feature could NOT be deleted - close the document "
                   "without saving)")
                + f". The seed feature and the axis line '{self._PATTERN_GEOSET}' "
                "are untouched. Usual cause: the instance would overlap material "
                "the seed already removed, or 'count' exceeds what fits in "
                f"angular_spacing ({angular_spacing} deg). Original error: "
                f"{update_error}"
            ) from update_error

        detail = ""
        if moved is not None and abs(moved) > 1e-6:
            detail = (f" Volume {before:.1f} -> {after:.1f} mm3 "
                      f"(delta {moved:+.1f}).")
        elif volume_note:
            detail = f" Volume not verified ({volume_note})."
        else:
            detail = " WARNING: the pattern changed no material - check the count and the axis."
        if update_error is not None:
            detail += (" (CATIA raised during the update, but the volume did "
                       "change, so the feature is kept.)")
        return (
            f"Circular pattern '{pattern.Name}' created: {count} instances "
            f"({count - 1} added), {angular_spacing} deg spacing around the "
            f"{axis.upper()} axis through {center}.{detail}"
        )

    def _user_pattern(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        feature = self._pattern_seed(body, args.get("feature_name"))
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

        self._safe_update(part, mirror)
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
        external = float(args.get("external_thickness") or 0.0)
        if thickness <= 0:
            raise ValueError(
                f"'thickness' must be positive, got {thickness}. CATIA's Shell "
                "offsets INWARD by this amount; a negative value is rejected at "
                "update with a message that looks like a bad feature name. To put "
                "material outside the surface, use 'external_thickness'."
            )
        if external < 0:
            raise ValueError("'external_thickness' cannot be negative.")
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
        # AddNewShell(iFaceToRemove, iInternalThickness, iExternalThickness):
        # the third argument is the only route to material outside the surface.
        shell = sf.AddNewShell(refs[0], thickness, external)
        skipped = []
        for extra, ref in zip(faces[1:], refs[1:]):
            try:
                shell.AddFaceToRemove(ref)
            except Exception:
                skipped.append(extra)

        self._safe_update(part, shell)
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
        if plane_key not in ("xy", "yz", "zx") or not planes.get(plane_key):
            raise ValueError(f"Unknown pulling direction plane: {plane_key}")

        # The neutral element has to be a face of the solid, not a datum plane:
        # AddNewDraft rejects the datum and fails before it even gets to the
        # update (measured 2026-10-23, four variants, only a face worked).
        neutral_name = args.get("neutral_face")
        if neutral_name:
            candidates = [(neutral_name, f"neutral {neutral_name}")]
        else:
            # _list_faces returns {"count": ..., "faces": [...]} since 0.3.7;
            # iterating the object yielded its keys (strings) and every draft
            # without an explicit neutral face died with "'str' object has no
            # attribute 'get'" - reported 2026-10-24 as a regression.
            payload = json.loads(self._list_faces())
            faces = payload["faces"] if isinstance(payload, dict) else payload
            others = [f for f in faces if f.get("name") != face_name]
            others.sort(key=lambda f: -(f.get("area_mm2") or 0))
            if not others:
                raise RuntimeError(
                    "catia_draft needs a neutral face, and this body reports only "
                    "the face being drafted. Pass neutral_face explicitly.")
            # Area alone is not the criterion - the neutral face also has to be
            # perpendicular to the pulling direction, and the largest face is
            # usually the base, whose normal is parallel to it instead. Picking
            # only the largest failed every time on a reported plate (2400 mm2
            # base chosen for a zx pull, "UpdateObject failed", 2026-10-24), and
            # an explicit side wall then worked. Try the largest few in turn and
            # keep the first that updates.
            candidates = [
                (f["name"],
                 f"neutral auto-picked: {f['name']} "
                 f"({f.get('area_mm2', 0):.0f} mm2)")
                for f in others[:4]
            ]

        # Pulling direction = the selected plane's normal.
        direction = {"xy": (0, 0, 1), "yz": (1, 0, 0), "zx": (0, 1, 0)}[plane_key]

        errors: list[str] = []
        for neutral_name, neutral_note in candidates:
            neutral_ref = self._topo_reference(
                "Face", self._topo_index(neutral_name, "Face"))
            # AddNewDraft(iFaceToDraft, iNeutral, iNeutralMode, iParting,
            #             iDirX, iDirY, iDirZ, iMode, iAngle, iMultiselectionMode)
            # iParting may be None - an empty Reference (CreateReferenceFromName(""))
            # is accepted by COM and then rejected by the factory.
            draft = sf.AddNewDraft(
                face_ref, neutral_ref, 0, None,
                direction[0], direction[1], direction[2],
                0, angle, 0,
            )
            update_error = self._try_update(part, draft)
            if update_error is None:
                self.conn.refresh_display()
                return (f"Draft created: {angle} deg on {face_name}, {neutral_note}. "
                        f"Feature: '{draft.Name}'")
            gone = self._remove_feature(part, draft)
            errors.append(f"{neutral_name}: {update_error}"
                          + ("" if gone else " (and the dead feature could NOT be "
                                              "deleted - close the document without "
                                              "saving)"))
        self.conn.refresh_display()
        raise RuntimeError(
            "CATIA rejected the draft on every neutral face tried. "
            f"{angle} deg on {face_name}, pulling direction {plane_key} "
            f"({'/'.join(str(v) for v in direction)}). Check the DIRECTION first: a "
            "face cannot be drafted along its own normal, and when it is, no neutral "
            "face can rescue it - the reported plate failed all five faces that way, "
            "while the same face drafted fine with the other two directions "
            "(2026-10-24). If the drafted face is a side wall, pull along an axis "
            "that is NOT that wall's normal. Only then try neutral_face: it has to "
            "touch the drafted face and be perpendicular to the pulling direction. "
            "Tried - " + "; ".join(errors)
        )

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

        self._safe_update(part, thickness)
        self.conn.refresh_display()
        return f"Thickness added: {offset} mm on {face_name}. Feature: '{thickness.Name}'"

    def _delete_feature(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        target = args.get("feature_name")
        if not target and args.get("index") is None:
            # "Delete the last feature" is a convenience that turned into an
            # accident: on a long tree, the last feature is whatever was created
            # most recently, which is often the fillet you just added, not the
            # test sketch you meant (reported 2026-10-25, one tip fillet lost).
            # It is still available, but it has to be asked for by name.
            shapes = self.conn.get_active_part_body().Shapes
            if not args.get("last"):
                would = ("the body is empty" if shapes.Count == 0
                         else f"'{shapes.Item(shapes.Count).Name}' (feature "
                              f"{shapes.Count} of {shapes.Count})")
                raise RuntimeError(
                    "catia_delete_feature was called without 'feature_name' or "
                    f"'index', and the shortcut would delete {would}. That is the "
                    "LAST CREATED feature, which on a long tree is usually not the "
                    "one you meant. Pass 'feature_name', 'index', or "
                    "last=true to mean this one."
                )
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
        kind = "feature"
        bodies = part.Bodies
        for b in range(1, bodies.Count + 1):
            body = bodies.Item(b)
            shapes = body.Shapes
            for i in range(1, shapes.Count + 1):
                shape = shapes.Item(i)
                if str(shape.Name) == target:
                    feature = shape
                    break
            if feature is not None:
                break
            # Sketches live in body.Sketches, not in body.Shapes: the tool used
            # to answer "No feature named '草图.3'" for a sketch sitting right
            # there in the tree (reported 2026-10-23), so a wrong profile could
            # never be removed - only worked around by rebuilding the part.
            sketches = body.Sketches
            for i in range(1, sketches.Count + 1):
                sketch = sketches.Item(i)
                if str(sketch.Name) == target:
                    feature, kind = sketch, "sketch"
                    break
            if feature is not None:
                break
        if feature is None:
            raise RuntimeError(
                f"No feature or sketch named '{target}' in any body of the active "
                "part. Use catia_list_features to see feature names; sketches are "
                "reported by catia_list_sketches."
            )
        # Deleting a solid feature takes its support sketch with it (CATIA
        # cascades), and the old reply said nothing about it - the reporter lost
        # 草图.3 with 凹槽.1 and only found out on the next pocket call.
        before = self._sketch_names()
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
            lost = [n for n in before
                    if n not in self._sketch_names() and n != target]
            return (
                f"Feature '{target}' deleted, but the part update failed "
                f"afterwards ({e}): features that depended on it are now "
                "in error. Fix or delete them too."
                + (f" Sketches removed with it: {', '.join(lost)}." if lost else "")
            )
        self.conn.refresh_display()
        # A sketch deleted on purpose is not a casualty of the delete.
        lost = [n for n in before
                if n not in self._sketch_names() and n != target]
        message = f"Deleted {kind} '{target}'."
        if lost:
            message += (f" CATIA also removed {len(lost)} sketch(es) that belonged "
                        f"to it: {', '.join(lost)}. Re-create the profile before "
                        "cutting with it again.")
        return message

    def _sketch_names(self) -> list[str]:
        """Every sketch name in the active part, for before/after comparisons."""
        names: list[str] = []
        try:
            bodies = self.conn.get_active_part().Bodies
            for b in range(1, bodies.Count + 1):
                sketches = bodies.Item(b).Sketches
                for i in range(1, sketches.Count + 1):
                    names.append(str(sketches.Item(i).Name))
        except Exception:  # noqa: BLE001 - diagnostics only
            pass
        return names

    # `Shape.Type` is not usable on this API: the late-bound object either has no
    # Type attribute at all ("unknown") or exposes a raw CATIA id (a hole came
    # back as 0). The NLS name prefix is reliable, so canonicalise from there and
    # keep the raw value as `catia_type` for anyone who wants it.
    _FEATURE_PREFIXES = (
        ("凸台", "pad"), ("Pad", "pad"),
        ("凹槽", "pocket"), ("Pocket", "pocket"),
        ("旋转体", "shaft"), ("Shaft", "shaft"),
        ("沟槽", "groove"), ("Groove", "groove"),
        ("孔", "hole"), ("Hole", "hole"),
        ("倒圆角", "fillet"), ("EdgeFillet", "fillet"), ("Fillet", "fillet"),
        ("倒角", "chamfer"), ("Chamfer", "chamfer"),
        ("拔模", "draft"), ("Draft", "draft"),
        ("抽壳", "shell"), ("Shell", "shell"),
        ("加厚", "thickness"), ("Thickness", "thickness"),
        ("矩形阵列", "rect_pattern"), ("RectPattern", "rect_pattern"),
        ("圆周阵列", "circ_pattern"), ("CircPattern", "circ_pattern"),
        ("镜像", "mirror"), ("Mirror", "mirror"),
        ("多截面实体", "loft"), ("MultiSection", "loft"),
        ("接合", "join"), ("分割", "split"), ("修剪", "trim"),
    )

    @classmethod
    def _feature_kind(cls, name: str) -> str:
        """Canonical feature type from the NLS name prefix ('凸台.3' -> 'pad')."""
        for prefix, kind in cls._FEATURE_PREFIXES:
            if name.startswith(prefix):
                return kind
        return "unknown"

    def _list_features(self) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self.conn.get_active_part_body()
        shapes = body.Shapes

        features = []
        for i in range(1, shapes.Count + 1):
            shape = shapes.Item(i)
            features.append({
                "index": i,
                "name": shape.Name,
                "type": self._feature_kind(shape.Name),
                "catia_type": shape.Type if hasattr(shape, "Type") else None,
            })

        if not features:
            return "No features in the active body"
        payload: dict[str, Any] = {
            "body": str(body.Name),
            "count": len(features),
            "bodies": part.Bodies.Count,
            "features": features,
        }
        # count == body.Shapes.Count, and it is published so a caller can tell
        # "my feature was created" from "a dead feature is still hanging around".
        # A failed fillet used to show up here as a normal-looking row and as a
        # shape_count one higher than expected in the failure state line
        # (reported 2026-10-24, which is how that report located the bug).
        #
        # Geometrical sets are NOT features and were not listed at all, so the
        # 'PatternAxis' set every circular pattern leaves behind (2 points + 1
        # line, never cleaned) was invisible here while being plain to see in
        # CATIA's tree - reported 2026-10-25 as residue nobody could account for.
        geosets = self._pattern_geoset_inventory(part)
        if geosets:
            payload["geometrical_sets"] = geosets
            payload["note"] = (
                "geometrical_sets are wireframe containers, not Part Design "
                "features, and they are not counted in 'count'. Each circular "
                "pattern adds a line (plus its two points) to the 'PatternAxis' "
                "set and that geometry must stay: the pattern holds a reference "
                "to it, so deleting it deletes the pattern."
            )
        return json.dumps(payload, indent=2)

    def _pattern_geoset_inventory(self, part: Any) -> list[dict[str, Any]]:
        """Every geometrical set and how many elements it holds."""
        found: list[dict[str, Any]] = []
        try:
            bodies = part.HybridBodies
            for i in range(1, bodies.Count + 1):
                hb = bodies.Item(i)
                entry: dict[str, Any] = {"name": str(hb.Name)}
                try:
                    entry["elements"] = hb.HybridShapes.Count
                except Exception:  # noqa: BLE001 - not reported when unknown
                    pass
                found.append(entry)
        except Exception:  # noqa: BLE001 - the listing is informative only
            return found
        return found

    _BOOLEAN_METHODS = {
        "add": "AddNewAdd",
        "remove": "AddNewRemove",
        "intersect": "AddNewIntersect",
        "assemble": "AddNewAssemble",
    }

    def _solid_loft(self, args: dict[str, Any]) -> str:
        """A Part Design solid loft: sections in, closed solid out.

        ``ShapeFactory.AddNewLoft()`` returns a Part Design feature whose
        ``.HybridShape`` is the GSM loft that feeds it - sections go into THAT,
        with the same three-argument form the surface loft uses. Measured on B30
        2026-10-25 (the reporter's recipe, independently reproduced): two circles
        r8 at z=0 and r16 at z=40 give a 18765.8 mm3 solid in one Update.

        This exists because the surface route cannot make a thin solid: offsetting
        a 2.30 mm section by 1.27+1.27 mm self-intersects, whichever side it goes.
        A solid loft has no thickness to add, so the limit does not apply.
        """
        sections = args["sections"]
        if len(sections) < 2:
            # Checked before touching CATIA: a bad argument should not depend on
            # a document being open.
            raise ValueError("A loft needs at least two sections.")
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        refs = [self._section_reference(name) for name in sections]

        before, volume_note = self._volume_probe(part, body)
        if before is None and self._body_is_empty(body):
            # An empty body has no volume to measure, which is not a failure:
            # the loft's own volume is the whole of it, and saying "not verified"
            # there would hide the one number the caller wants.
            before, volume_note = 0.0, None
        part.InWorkObject = body
        feature = sf.AddNewLoft()
        inner = feature.HybridShape
        for ref in refs:
            # (section, orientation, closing point) - three arguments; the
            # closing point may be None, which is what "automatic" means here.
            try:
                inner.AddSectionToLoft(ref, 1, None)
            except Exception:
                inner.AddSectionToLoft(ref, 1)

        tangency = []
        for attribute, spec, end in (("SetStartSectionTangent", args.get("start_tangent"), "first"),
                                     ("SetEndSectionTangent", args.get("end_tangent"), "last")):
            if not spec:
                continue
            try:
                getattr(self.conn._dynamic(inner), attribute)(self._section_reference(spec))
            except Exception as exc:  # noqa: BLE001 - reported with the rule
                raise RuntimeError(
                    f"CATIA rejected '{spec}' as the {end} section's tangency support "
                    f"({str(exc).splitlines()[0][:120]}). The support has to be a "
                    "surface that actually touches that section."
                ) from exc
            tangency.append(f"{end} section tangent to '{spec}'")

        if args.get("name"):
            try:
                feature.Name = str(args["name"])
            except Exception:  # noqa: BLE001 - the default name is usable
                pass

        try:
            part.Update()
        except Exception as error:
            gone = self._remove_feature(part, feature)
            raise FeatureRejected(
                f"CATIA rejected the solid loft"
                + (" and it was removed from the tree again (rolled_back: true)"
                   if gone else " and it could NOT be removed (rolled_back: false)")
                + ". Sections: " + ", ".join(sections)
                + ". The usual cause is a section that is not ONE curve - a polygon "
                "of separate sketch lines is several curves, and an Extract of it is "
                "still several; use a closed catia_sketch_spline or catia_gsd_spline. "
                "Second cause, seen with the surface loft on the same build: the "
                "loft is session-sensitive, so retry once in a fresh session before "
                f"rebuilding the sections. Original error: {error}",
                rolled_back=gone,
            ) from error

        self.conn.refresh_display()
        after, after_note = self._volume_probe(part, body)
        detail = ""
        if before is not None and after is not None:
            detail = f" Volume {before:.1f} -> {after:.1f} mm3."
        elif after_note or volume_note:
            detail = f" Volume not verified ({after_note or volume_note})."
        return (
            f"Solid loft created through {len(sections)} section(s)"
            + (f" ({'; '.join(tangency)})" if tangency else "")
            + f". Feature: '{feature.Name}' in '{body.Name}', a Part Design solid, "
            "not a surface - no thickening step is needed."
            + detail
        )

    def _section_reference(self, name: str) -> Any:
        """A Reference for a loft section: sketch, GSD curve, or Edge.N / Face.N."""
        part = self.conn.get_active_part()
        body = self.conn.get_active_part_body()
        cleaned = (name or "").strip()
        if re.fullmatch(r"(Edge|Face|Vertex)\.\d+", cleaned, re.I):
            kind = cleaned.split(".")[0].capitalize()
            index = int(cleaned.split(".")[1])
            return topology_reference(self.conn, body, kind, index).reference
        for i in range(1, part.Bodies.Count + 1):
            candidate = part.Bodies.Item(i)
            try:
                return part.CreateReferenceFromObject(candidate.Sketches.Item(cleaned))
            except Exception:  # noqa: BLE001 - not this body
                pass
        for i in range(1, part.HybridBodies.Count + 1):
            hb = part.HybridBodies.Item(i)
            for collection in ("HybridShapes", "HybridSketches"):
                try:
                    element = getattr(hb, collection).Item(cleaned)
                except Exception:  # noqa: BLE001
                    continue
                return part.CreateReferenceFromObject(element)
        raise RuntimeError(
            f"No sketch or geometrical element named '{name}' to use as a loft "
            "section. catia_list_sketches and catia_gsd_list_elements both report "
            "the names this accepts."
        )

    def _delete_body(self, name: str, force: bool = False) -> str:
        """Remove a body from the part, refusing to take features with it.

        ``catia_delete_feature`` cannot do this: it searches ``body.Shapes`` and
        ``body.Sketches``, and a body is not inside a body, so an experimental
        body could only be emptied, never removed (reported 2026-10-25). CATIA
        does allow the deletion through the selection.
        """
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        bodies = part.Bodies
        target = None
        names = []
        for i in range(1, bodies.Count + 1):
            body = bodies.Item(i)
            label = str(body.Name)
            names.append(label)
            if label == name:
                target = body
        if target is None:
            raise RuntimeError(
                f"No solid body named '{name}'. Bodies: {', '.join(names) or 'none'}."
            )
        if str(target.Name) == str(part.MainBody.Name):
            raise RuntimeError(
                f"'{name}' is the MainBody and cannot be deleted: every part needs "
                "one. Delete its features instead, or build the geometry in another "
                "body and leave this one empty."
            )
        try:
            count = target.Shapes.Count
        except Exception:  # noqa: BLE001 - unknown, so do not force
            count = None
        if count and not force:
            raise RuntimeError(
                f"Body '{name}' still holds {count} feature(s), and deleting the body "
                "deletes them too. Pass force=true if that is what you want, or "
                "remove the features first with catia_delete_feature."
            )
        before = bodies.Count
        sel = self.conn.hso
        try:
            sel.Clear()
            sel.Add(target)
            sel.Delete()
        finally:
            try:
                sel.Clear()
            except Exception:  # noqa: BLE001
                pass
        self.conn.refresh_display()
        if self.conn.get_active_part_body() is target:  # the handle is gone
            self.conn.active_body = None
        after = part.Bodies.Count
        if after >= before:
            raise RuntimeError(
                f"CATIA accepted the deletion but '{name}' is still there "
                f"({before} -> {after} bodies). Close the document without saving "
                "and rebuild, or empty the body and leave it."
            )
        return (f"Body '{name}' deleted ({before} -> {after} bodies)."
                + (f" Its {count} feature(s) went with it." if count else ""))

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
        total_excluded = 0
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
                    # Same definition of "face of the solid" as
                    # catia_measure_topology: the Face search also returns the
                    # datum planes' own borders, so counting sel.Count reported
                    # 191 on a body where measure_topology said 186 - the same
                    # class of bug as the old edge-count split (reported
                    # 2026-10-24, third release of this family).
                    sel.Clear()
                    sel.Add(body)
                    search_topology(sel, "Face")
                    raw = sel.Count
                    faces = len(topology_cells(self.conn, body, "Face"))
                    entry["faces"] = faces
                    total_faces += faces
                    if raw != faces:
                        entry["faces_excluded"] = raw - faces
                        total_excluded += raw - faces
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
        payload = {
            "bodies": out,
            "count": len(out),
            "total_volume_mm3": round(total_volume, 1),
            "total_faces": total_faces,
        }
        if total_excluded:
            payload["faces_note"] = (
                f"{total_excluded} face(s) the search also returns are datum-plane "
                "borders or sketch wires, and are excluded here exactly as in "
                "catia_measure_topology and catia_list_faces - the counts agree.")
        return json.dumps(payload, indent=2)

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

    def _set_visibility_kind(self, kind: str, show: bool) -> str:
        """Show or hide a whole class of tree bookkeeping at once.

        A screenshot of a finished part is covered in white frames that are not
        geometry: every sketch keeps its profile curves on screen, and the three
        reference planes keep their borders. They are drawn over the solid, so
        they read as surface defects rather than as the drawing bookkeeping they
        are - the drone assembly looked like it had comb-shaped artefacts at each
        motor until they were hidden.

        CATIA has no "hide all sketches" switch in the automation API, and the
        sketches are not selectable by name through the HSO, so this walks the
        tree the way the UI's Ctrl+F search would.
        """
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        sel = self.conn.active_document.Selection
        counts = {"sketch": 0, "datum_plane": 0}

        def apply(node) -> None:
            sel.Clear()
            sel.Add(node)
            sel.VisProperties.SetShow(0 if show else 1)

        want_sketch = kind in ("sketch", "construction")
        want_plane = kind in ("datum_plane", "construction")

        try:
            if want_sketch:
                for i in range(1, part.Bodies.Count + 1):
                    sketches = part.Bodies.Item(i).Sketches
                    for j in range(1, sketches.Count + 1):
                        try:
                            apply(sketches.Item(j))
                            counts["sketch"] += 1
                        except Exception:      # noqa: BLE001 - per sketch
                            pass
                for i in range(1, part.HybridBodies.Count + 1):
                    sketches = part.HybridBodies.Item(i).HybridSketches
                    for j in range(1, sketches.Count + 1):
                        try:
                            apply(sketches.Item(j))
                            counts["sketch"] += 1
                        except Exception:      # noqa: BLE001 - per sketch
                            pass
            if want_plane:
                origin = part.OriginElements
                for name in ("PlaneXY", "PlaneYZ", "PlaneZX"):
                    try:
                        apply(getattr(origin, name))
                        counts["datum_plane"] += 1
                    except Exception:          # noqa: BLE001 - per plane
                        pass
        finally:
            self._clear_selection()
        self.conn.refresh_display()

        if not counts["sketch"] and not counts["datum_plane"]:
            raise RuntimeError(
                f"Nothing of kind '{kind}' found to {'show' if show else 'hide'}: "
                "the part has no sketches (or no datum planes). Use "
                "catia_list_features to see what the tree holds."
            )
        parts = []
        if counts["sketch"]:
            parts.append(f"{counts['sketch']} sketch(es)")
        if counts["datum_plane"]:
            parts.append(f"{counts['datum_plane']} datum plane(s)")
        return "%s now %s." % (" and ".join(parts), "shown" if show else "hidden")

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
        self._safe_update(part, feature)

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
        raw_total = None
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
                    entry["bounded_by"] = face_keys(nls)
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
        raw_total = self._raw_topology_count(body, "Face")

        if not faces:
            return "No faces found on the final shape"
        payload: dict[str, Any] = {
            "count": len(faces),
            "raw_total": raw_total,
            "faces": faces,
        }
        # Publising both numbers is how a shortfall stops being invisible: the
        # report of 2026-10-23 counted 14 faces on a solid and got 10 here, and
        # nothing in the old payload made the difference visible. total_raw is
        # every cell CATIA's search returns; the gap is construction geometry
        # (datum-plane borders, sketch wires) that has no area.
        if raw_total is not None and raw_total != len(faces):
            payload["note"] = (
                f"{raw_total - len(faces)} of {raw_total} searched cell(s) are "
                "construction geometry (datum planes, sketch wires) and are not "
                "part of the solid. Neither count can be independently wrong: "
                "if these do not add up to what the solid really has, report "
                "both numbers with the part."
            )
        return json.dumps(payload, indent=2)

    def _raw_topology_count(self, body: Any, kind: str) -> int | None:
        """Cells CATIA's search returns, construction geometry included."""
        sel = self.conn.hso
        try:
            sel.Clear()
            sel.Add(body)
            search_topology(sel, kind)
            return sel.Count
        except Exception:  # noqa: BLE001 - reported as null
            return None
        finally:
            try:
                sel.Clear()
            except Exception:
                pass

    def _attach_edge_points(self, part: Any, rows: list[dict[str, Any]],
                            edges: list[dict[str, Any]]) -> str | None:
        """Give each listed edge a coordinate that lies ON it.

        One measurement per edge, which is why it is opt-in: a 78-edge part pays
        78 SPA calls. The point is the one closest to the body's centre of
        gravity - for a straight edge that is its midpoint (verified on B30
        2026-10-25 against a 60x40x40 box: all 12 edges came back exactly at
        their midpoint), for an arc it is the part of the arc facing the centre.
        There is no way to read an edge's own vertices through this API: a
        vertex reference answers 'GetPoint failed', so this is the only route to
        a coordinate at all.
        """
        spa = self._topo_measure(part)
        if spa is None:
            return "point_on_edge was not reported: SPAWorkbench is unavailable."
        try:
            from catia_mcp.tools.measurement import (  # noqa: PLC0415 - cycle
                read_measurable_array,
                read_min_distance_points,
            )
            body = self._prepare_body(part)
            body_ref = part.CreateReferenceFromObject(body)
            centre = read_measurable_array(
                self.conn.app, spa.GetMeasurable(body_ref), "GetCOG", 3)
        except Exception as exc:  # noqa: BLE001 - the listing still stands
            return f"point_on_edge was not reported: {exc}"

        probe = self._edge_probe_points(part)
        failures = 0
        try:
            point_ref = probe.add(*centre)
            probe.update()
            # Re-enumerate AFTER the rebuild: the references held by the first
            # enumeration died when its selection was cleared, and every
            # coordinate here is measured against them.
            fresh = {entry["index"]: entry for entry in self._edge_table(body)}
            for entry, item in zip(rows, edges):
                source = fresh.get(entry["index"])
                if source is None:
                    failures += 1
                    item["point_on_edge_unknown"] = True
                    continue
                try:
                    on_edge, _ = read_min_distance_points(
                        self.conn.app,
                        spa.GetMeasurable(source["cell"].Reference), point_ref)
                except Exception:  # noqa: BLE001 - one edge, not the listing
                    failures += 1
                    item["point_on_edge_unknown"] = True
                    continue
                item["point_on_edge"] = [round(v, 4) for v in on_edge]
                item["distance_to_centre_mm"] = round(
                    sum((on_edge[i] - centre[i]) ** 2 for i in range(3)) ** 0.5, 3)
        finally:
            probe.close()
        note = (
            "point_on_edge is a point ON the edge - the one closest to the "
            "body's centre of gravity, which is the midpoint for a straight edge "
            "and the centre-facing part for an arc. Pass it back to "
            "catia_fillet / catia_chamfer as edge_anchors when the source data "
            "gives coordinates instead of Edge.N."
        )
        if failures:
            note += (f" {failures} edge(s) could not be measured and carry "
                     "point_on_edge_unknown instead.")
        return note

    def _list_edges(self, min_length: float | None = None,
                    max_length: float | None = None,
                    faces: list[str] | None = None,
                    offset: int = 0, limit: int = 0,
                    include_nls: bool = True,
                    coords: bool = False) -> str:
        """List solid edges, filterable and pageable, with a stable fingerprint.

        The fingerprint is the point: ``Edge.N`` moves when the topology changes,
        the pair of bounding faces plus the length does not. Feed it back to
        catia_fillet / catia_chamfer instead of an index and the "fillet one,
        re-list everything, fillet the next" loop disappears.

        A part with 67 edges produced ~50 000 characters of NLS text per call
        (reported 2026-10-23), so ``include_nls=False`` and the paging arguments
        exist for exactly that case.
        """
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)

        try:
            table = self._edge_table(body)
        except Exception as e:
            return f"Could not enumerate edges: {e}. Use CATIA selection to identify edge names."
        finally:
            self._clear_selection()

        total = len(table)
        raw_total = self._raw_topology_count(body, "Edge")
        wanted = [f for f in (faces or []) if f]
        rows = table
        if wanted:
            rows = [e for e in rows if set(wanted) <= set(e["bounded_by"])]
        if min_length is not None:
            rows = [e for e in rows if e["length"] is None or e["length"] >= min_length]
        if max_length is not None:
            rows = [e for e in rows if e["length"] is None or e["length"] <= max_length]
        matched = len(rows)
        if offset:
            rows = rows[offset:]
        if limit and limit > 0:
            rows = rows[:limit]

        edges = []
        points_on_edges: list[tuple[dict[str, Any], list[float]]] = []
        for entry in rows:
            item: dict[str, Any] = {
                "index": entry["index"],
                "name": entry["name"],
                "fingerprint": entry["fingerprint"],
                "bounded_by": entry["bounded_by"],
            }
            try:
                item["type"] = str(entry["cell"].Type)
            except Exception:
                pass
            if entry["length"] is None:
                # SPA hands back a placeholder rather than raising for topology
                # it cannot resolve, so "unknown" has to be said out loud.
                item["length_unknown"] = True
            else:
                item["length_mm"] = round(entry["length"], 4)
            if include_nls and entry.get("nls"):
                item["nls"] = entry["nls"]
                item["created_by"] = nls_features(entry["nls"])
            edges.append(item)

        coordinates_note = None
        if coords and rows:
            coordinates_note = self._attach_edge_points(part, rows, edges)

        if not edges:
            return json.dumps({
                "count": 0, "total": total,
                "hint": "No edge matched the filter.",
                "edges": [],
            }, indent=2)
        payload: dict[str, Any] = {
            "count": len(edges),
            "matched": matched,
            "total": total,
            "raw_total": raw_total,
            "offset": offset,
            # The fingerprint survives renumbering; 'name' does not. Say it in
            # the payload, because that is where the caller will look.
            "note": ("Pass 'fingerprint' (or {\"length_mm\":..., \"faces\":[...]}) to "
                     "catia_fillet / catia_chamfer instead of 'name': Edge.N is "
                     "renumbered by every feature that changes the topology."),
            "edges": edges,
        }
        if coordinates_note:
            payload["coordinates"] = coordinates_note
        if raw_total is not None and raw_total != total:
            # Same sentence as catia_measure_topology: one definition of "edge of
            # the solid", so an acceptance script cannot get 109 from one tool and
            # 101 from the other (reported three releases running).
            payload["excluded"] = {
                "total": raw_total - total,
                "raw_total": raw_total,
                "reason": ("consumed sketch wires and datum-plane borders, which the "
                           "Edge search also returns; 'total' and Edge.N count only "
                           "the solid's own edges"),
            }
        return json.dumps(payload, indent=2)
