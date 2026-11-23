"""Part Design tools for CATIA V5.

3D feature creation: Pad, Pocket, Fillet, Chamfer, Shaft, Groove, Hole,
RectPattern, CircPattern, Mirror, Rib, Slot, Shell, Thickness, Draft.
"""

from __future__ import annotations

import fnmatch
import json
import math
import re
import time
from typing import Any

from catia_mcp.connection import CATIAConnection
from catia_mcp.update_errors import describe, failing_elements
from catia_mcp.results import CodedError, FeatureRejected, PreconditionRejected
from catia_mcp import sketch_frames
from catia_mcp.sketch_frames import BASES, frame_of, point_world
from catia_mcp.results import ArgumentOutOfRange, ArgumentRejected
from catia_mcp.geometry2d import JUNCTION_TOL, free_ends_with_gaps, segments_of
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
    "on_anchor_miss": {
        "type": "string",
        "enum": ["fail", "skip"],
        "default": "fail",
        "description": (
            "'fail' (default) refuses the whole batch if ANY anchor is beyond "
            "anchor_tol or ambiguous, and lists every one of them - nothing is "
            "filleted, so no edge gets a feature nobody asked for. 'skip' fillets "
            "the anchors that do match and reports the misses in the reply, which "
            "is what a long coordinate table needs: 20 anchors with one bad "
            "coordinate should not cost 20 round trips."
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

    def __init__(self, conn: Any, part: Any, body: Any = None) -> None:
        self.conn = conn
        self.part = part
        #: The body the anchors will be matched in, when the caller has it.
        #: ``close`` updates THIS instead of the whole document when it can - see
        #: ``update`` for why a full rebuild per anchor is not acceptable.
        self.body = body
        self.factory = part.HybridShapeFactory
        # What was in work BEFORE the scratch set existed. Adding a HybridBody
        # moves InWorkObject onto it, and deleting the set does not move it
        # back: in a document that already holds a geometrical set (any GSD
        # write leaves one), the in-work object ends up on that other set, and
        # every Part Design factory then answers a bare E_FAIL while the
        # references are perfectly good. Field-measured 2026-10-30 on B30: the
        # same edge reference that fails there builds a fillet the moment
        # InWorkObject is set back to the body.
        self._in_work = self._read_in_work()
        self.set = part.HybridBodies.Add()
        self.set.Name = self.NAME
        self.shapes: list[Any] = []
        self._updated = False

    def _read_in_work(self) -> Any:
        try:
            return self.part.InWorkObject
        except Exception:  # noqa: BLE001 - nothing to restore is fine
            return None

    def _restore_in_work(self) -> None:
        previous = self._in_work
        if previous is None:
            return
        try:
            if str(self.part.InWorkObject.Name) == str(previous.Name):
                return
        except Exception:  # noqa: BLE001 - unreadable means "probably wrong"
            pass
        try:
            self.part.InWorkObject = previous
        except Exception:  # noqa: BLE001 - better a stale in-work than a raise
            pass

    def add(self, x: float, y: float, z: float) -> Any:
        point = self.factory.AddNewPointCoord(float(x), float(y), float(z))
        self.set.AppendHybridShape(point)
        self.shapes.append(point)
        self._updated = False
        return point

    def add_on_edge(self, edge: Any, ratio: float) -> Any:
        """A point at ``ratio`` along a TOPOLOGICAL edge.

        Measured on B30 2026-11-15 (`scripts/b30_v0431_probe_edge_samples.py`):
        this works on every OPEN edge - 12/12 on a padded plate, 18/18 once four
        corners were filleted, and five points at 0/25/50/75/100 % of a 40 mm edge
        landed at exactly 10.000 mm apart - and is refused on a CLOSED rim (0/2
        on a padded circle's two rims), the same matrix the engrave code found:
        point-on-curve needs a curve with two ends.

        The edge is passed as the OBJECT, not as ``cell.Reference``: both work,
        but ``TriDimFeatEdge.GetCurve()`` does NOT exist on this build (measured
        the same day: ``<unknown>.GetCurve``), which is why the route through the
        edge's curve is closed and this one is not.
        """
        point = self.factory.AddNewPointOnCurveFromPercent(edge, float(ratio), 0)
        self.set.AppendHybridShape(point)
        self.shapes.append(point)
        self._updated = False
        return point

    def update(self) -> None:
        """One rebuild for every point added since the last one.

        THE SCOPE OF THE REBUILD IS THE POINT (2026-11-19 report, P1-4). This
        used to be ``part.Update()`` - the WHOLE document's tree - and it ran once
        per anchor, so a twenty-anchor fillet rebuilt the entire part twenty
        times: the report measured single-edge chamfers at 25-56 s on a 140-face
        part while pad took 0.3 s, and the reply said nothing about where the time
        went. ``UpdateObject(self.set)`` updates the scratch set, which is all a
        probe point needs: measured on B30 2026-11-19, the distance from the
        probe to the edge it sits on is exactly 0.0000 mm after it, exactly as
        after a full rebuild. The full rebuild stays as the fallback, so a build
        that refuses a partial update behaves as it did.
        """
        if self._updated or not self.shapes:
            return
        try:
            self.part.UpdateObject(self.set)
        except Exception:  # noqa: BLE001 - fall back to what always worked
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
        # Updating the BODY, not the document, for the same reason `update` does:
        # the solid's topology did not change when the scratch set went away, and
        # a full tree rebuild here was the second half of the cost the 2026-11-19
        # report measured.
        try:
            if self.body is not None:
                self.part.UpdateObject(self.body)
            else:
                self.part.Update()
        except Exception:  # noqa: BLE001
            pass
        # After the rebuild, because deleting a set can itself move the in-work
        # object. This is the line whose absence made edge_anchors fail with
        # E_FAIL in every document that had ever seen a GSD write.
        self._restore_in_work()


# Where a sketch's own (x, y) sits in the part's coordinate system. The table
# itself lives in catia_mcp.sketch_frames, because the GSD side and the sketcher
# need the same one - two copies is how they stop agreeing (2026-11-09).
_SKETCH_BASES = BASES


def _sketch_frame(placement: Any) -> tuple[list[float], list[float],
                                           list[float]] | None:
    """``(origin, u, v)`` in world mm for a recorded sketch placement."""
    return frame_of(placement)


def _sketch_point_world(placement: Any, x: float, y: float) -> list[float] | None:
    """World XYZ of the sketch coordinate ``(x, y)``, or None when unknown."""
    return point_world(placement, x, y)


def _describe_axes(axes: Any) -> str:
    """``origin (x, y, z), H (..), V (..)`` for a reply, or a placeholder."""
    try:
        origin, h, v = axes
        return (f"origin ({origin[0]:.4f}, {origin[1]:.4f}, {origin[2]:.4f}), "
                f"H ({h[0]:g}, {h[1]:g}, {h[2]:g}), "
                f"V ({v[0]:g}, {v[1]:g}, {v[2]:g})")
    except Exception:  # noqa: BLE001 - never worth failing a reply over
        return "axes not printable"


def _in_plane_distance(first: Any, second: Any, placement: Any) -> float | None:
    """Distance between two world points measured IN the sketch plane."""
    frame = _sketch_frame(placement)
    if frame is None or first is None or second is None:
        return None
    _origin, u, v = frame
    normal = [u[1] * v[2] - u[2] * v[1],
              u[2] * v[0] - u[0] * v[2],
              u[0] * v[1] - u[1] * v[0]]
    span = math.sqrt(sum(c * c for c in normal))
    if span == 0:
        return None
    unit = [c / span for c in normal]
    delta = [float(first[i]) - float(second[i]) for i in range(3)]
    along = sum(delta[i] * unit[i] for i in range(3))
    off = [delta[i] - along * unit[i] for i in range(3)]
    return math.sqrt(sum(c * c for c in off))


def _removed_centroid(v0: Any, g0: Any, v1: Any, g1: Any
                      ) -> tuple[list[float] | None, float | None]:
    """Where the vanished material was, from the shift of the centre of gravity.

    ``V0*G0 - V1*G1 = v*g`` for the piece that was cut away, so the volumes and
    centres of gravity read before and after a cut give the centroid of the cut
    itself - the only witness that says WHERE a cut went, as opposed to how much
    it removed. That is exactly the question a bore drilled at the wrong place
    poses: the volume delta looks right and the feature is somewhere else.

    Returns ``(point, amplification)``; ``amplification`` is ``V1/v``, how much
    an error in the COG reading is multiplied by. A 5 mm bore in a 16 000 mm3
    block amplifies by 21 (microns), a 0.1 mm3 nick in a 10 000 000 mm3 part by
    1e8 - so a caller can tell a meaningful check from an arithmetic artefact.
    """
    if v0 is None or v1 is None or g0 is None or g1 is None:
        return None, None
    removed = float(v0) - float(v1)
    if abs(removed) < 1e-9:
        return None, None
    point = [(float(v0) * float(g0[i]) - float(v1) * float(g1[i])) / removed
             for i in range(3)]
    return point, abs(float(v1) / removed)


def _material_names(body: Any) -> set[str]:
    """Names of the features that MAKE MATERIAL in this body.

    ``body.HybridShapes`` is not "the construction inside the body". On a Part
    Design loft it also lists the loft itself - measured on B30 2026-11-14, a
    two-sketch loft in 零件几何体 gave ``body.HybridShapes`` =
    ['Datum_xy_20', 'Body'] where 'Body' is the solid, and ``body.Shapes`` =
    ['Body']. Anything whose name is a shape here is material, whichever
    collection it also appears in, and hiding it takes the model off the screen.
    """
    names: set[str] = set()
    try:
        shapes = body.Shapes
        for index in range(1, int(shapes.Count) + 1):
            try:
                names.add(str(shapes.Item(index).Name))
            except Exception:  # noqa: BLE001 - one unreadable shape
                pass
    except Exception:  # noqa: BLE001 - a body with no shapes
        pass
    return names


class PartDesignTools:
    """Tools for 3D Part Design features in CATIA V5."""

    #: Tools of this class that carry a semantic check catia_run_plan can pre-run
    #: (see planner's _semantic_problems). Only the loft is here: its minimum is a
    #: COUNT, which no argument schema can express, and a one-section loft used to
    #: be refused after the plan had already built the section.
    PRE_CHECKS = {"catia_solid_loft": "_precheck_loft"}

    def _precheck_loft(self, args: dict[str, Any]) -> None:
        """Refuse a loft the tool would refuse, without touching CATIA."""
        sections = args.get("sections")
        if not isinstance(sections, (list, tuple)):
            return
        if len(sections) < 2:
            raise ArgumentRejected(
                "A loft needs at least two sections - one section is a face, not "
                "a solid. Nothing was created.",
                code="NOT_ENOUGH_SECTIONS", stage="validate")

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection
        #: absolute axis system (origin, H, V) of each sketch this call deleted,
        #: by name. Read BEFORE the delete because nothing about a deleted sketch
        #: can be read afterwards, and the frame cleanup needs it to know which
        #: surviving sketches could have been standing on the datum (0.3.149:
        #: the whole axis system, not just the plane - two frames on one plane
        #: are two datums).
        self._deleted_sketch_datums: dict[str, Any] = {}

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "catia_pad",
                "description": (
                    "Create a Pad (extrusion) from the last sketch: a 2D profile "
                    "extruded along the sketch plane's normal. `direction` picks the "
                    "side ('normal', 'reverse', or 'both' with symmetric=true). "
                    "A pad that adds no material in the requested direction is "
                    "FLIPPED and re-measured, and if that side grows the body the "
                    "feature is KEPT and the reply says "
                    "'direction auto-flipped to reach material' - the same behaviour "
                    "catia_pocket has had since 0.3.4, because a seed sketch sitting "
                    "on the material boundary is the common case, not an error. Only "
                    "when BOTH orientations add nothing is the pad rolled back, and "
                    "then the reply states that both were measured and whether your "
                    "sketch is still in the tree. "
                    "AN OPEN CURVE IN THE SKETCH IS REFUSED BEFORE CATIA IS ASKED: "
                    "one free endpoint anywhere makes the whole profile unusable - "
                    "measured on B30 2026-11-13, a circle alone cut 603.2 mm3 and the "
                    "same circle with one open spline (through it or inside it) or "
                    "one open line removed 0 mm3 in all three cells. The refusal names "
                    "the free points; allow_open_curves=true sends it anyway."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "allow_open_curves": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Skip the open-outline refusal and let CATIA decide. "
                                "Measured 3/3 to remove nothing, so this is only for "
                                "the case where the drawing record is wrong about "
                                "what the sketch holds (a sketch edited by hand "
                                "after the tools drew it)."
                            ),
                        },
                        "height": {
                            "type": "number",
                            "description": (
                                "Extrusion height in mm, on ONE side of the sketch "
                                "plane. For two different extents use limit1/limit2 "
                                "instead - they are the same feature, not two pads."
                            ),
                        },
                        "limit1": {
                            "type": "number",
                            "description": (
                                "Two-sided extrusion: how far the pad runs toward "
                                "the sketch normal's positive side, in mm. Give "
                                "limit2 as well for the other side - one Pad whose "
                                "two extents differ, which is what a SolidWorks "
                                "'blind 57.5 / blind 4.5' becomes. Measured on B30 "
                                "2026-11-19: limit1=5, limit2=3 on a 20x10 profile "
                                "gave z -3.000..+5.000 and 1600 mm3 "
                                "(200 mm2 x 8). ONE feature in the tree instead "
                                "of two pads; the face count is the same either "
                                "way (72/191/120 measured both routes - CATIA "
                                "merges the coplanar walls), so the gain is the "
                                "tree and the volume, not the topology."
                            ),
                        },
                        "limit2": {
                            "type": "number",
                            "description": (
                                "How far the same Pad runs the OTHER way from the "
                                "sketch plane, in mm (CATIA's Limit 2). 0 or "
                                "omitted is one-sided. 'direction' decides which "
                                "side limit1 counts on, and reverse FLIPS THE "
                                "PAIR about the sketch plane - limit1 keeps its "
                                "magnitude and changes side (limit1=1, limit2=4 "
                                "with reverse gives z -4..+1, not -1..+4). "
                                "Measured on B30 2026-11-19."
                            ),
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
                    "required": [],
                    # One of the two extent FORMS, checked by the schema so a
                    # call with neither is rejected before CATIA is touched:
                    # height for one side, limit1 (with optional limit2) for two.
                    "anyOf": [{"required": ["height"]}, {"required": ["limit1"]}],
                },
            },
            {
                "name": "catia_edit_pad",
                "description": (
                    "CHANGE THE DEPTH OF A FEATURE THAT ALREADY EXISTS - a pad, a "
                    "pocket, a shaft or a groove - instead of throwing the part "
                    "away. Until 0.3.161 the only way to fix a pad built to the "
                    "wrong depth was to delete it, which breaks every feature "
                    "after it, so the 2026-11-23 report rebuilt a 15-feature part "
                    "twice for a 5 mm mistake (P1-4). "
                    "MEASURED on B30 2026-11-23: Pad.FirstLimit.Dimension.Value "
                    "reads and writes (80 -> 20 on a 25x45 block gave 22500 mm3, "
                    "exactly 25*45*20), SecondLimit.Dimension.Value reads 0 on a "
                    "one-sided pad, and Pad.IsSymmetric reads. WHAT CANNOT BE "
                    "EDITED, and is not offered: the DIRECTION (Pad.Direction, "
                    "Pad.Reversed, Pad.Symmetric, FirstLimit.Type all raise "
                    "'Item.X' on this build), a seed surface, and any reordering "
                    "(Part and Body have no Insert/Reorder). A pad that points "
                    "the wrong way is still a rebuild - what this fixes is the "
                    "SIZE. "
                    "The reply gives the old and new value, the volume before and "
                    "after, and the IMPLIED AREA of the change (volume moved / "
                    "depth changed) - a pad whose sketch is 25 x 45 answers 1125 "
                    "mm2 there, and a change that landed on the wrong side "
                    "answers the same number with the opposite sign."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "feature_name": {
                            "type": "string",
                            "description": (
                                "Feature to change ('凸台.1', '凹槽.2' - see "
                                "catia_list_features). Any Part Design feature that "
                                "carries a FirstLimit works; one that does not is "
                                "refused by name."
                            ),
                        },
                        "height": {
                            "type": "number",
                            "description": (
                                "New depth of the FIRST limit, in mm. This is the "
                                "number catia_pad was given as 'height'."
                            ),
                        },
                        "second_height": {
                            "type": "number",
                            "description": (
                                "New depth of the SECOND limit, in mm. A one-sided "
                                "pad reads 0 here, and setting it makes the pad "
                                "TWO-SIDED: measured on B30 2026-11-23, a 25x45 pad "
                                "at 20 mm plus a 5 mm second limit gave 28125 mm3, "
                                "exactly 25*45*25. Leave it out to keep the feature "
                                "one-sided."
                            ),
                        },
                        "symmetric": {
                            "type": "boolean",
                            "description": (
                                "Set the feature's IsSymmetric flag. It reads back "
                                "on this build; when the read-back disagrees the "
                                "change is undone and the call says so."
                            ),
                        },
                    },
                    "required": ["feature_name"],
                },
            },
            {
                "name": "catia_pocket",
                "description": (
                    "Create a Pocket (cut extrusion) from a sketch. Removes material "
                    "by extruding a 2D profile into the solid. Give either `depth` or "
                    "`through_all`. This is also the tool for a plain cylindrical hole: "
                    "sketch a circle and cut it."
                    "SCOPE: a pocket cuts EVERYTHING it passes through in the active "
                    "body - there is no 'only this object' argument, so a through cut "
                    "meant for one panel also opens whatever else lies in its path. "
                    "To isolate a cut, put the target in its own body "
                    "(catia_new_body, catia_set_active_body), cut there, and combine "
                    "with catia_boolean; or use a shallow depth that stops inside the "
                    "panel. Reported 2026-11-07, P7, where a through-all slot opened "
                    "the hull side behind it."
                    "MULTI-REGION SKETCHES ARE REFUSED UP FRONT. A sketch that holds "
                    "a closed region plus an open curve - the boundary that divides "
                    "the region into pieces - removes NOTHING: measured on B30 "
                    "2026-11-13, a circle alone cut 603.2 mm3 and the same circle "
                    "with one open spline through it, one inside it, or one open "
                    "line through it all removed 0 mm3 (NO_MATERIAL). The open curve "
                    "does not have to divide anything. This is checked from the "
                    "drawing record BEFORE CATIA runs, so the failure costs no "
                    "rollback and no feature node; the reply names the free points. "
                    "There is no 'cut only this region' argument - draw each region "
                    "in its own sketch, or pass allow_open_curves=true to send the "
                    "sketch to CATIA anyway."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "allow_open_curves": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Skip the open-outline refusal and let CATIA decide. "
                                "Measured 3/3 to remove nothing, so this is only for "
                                "the case where the drawing record is wrong about "
                                "what the sketch holds."
                            ),
                        },
                        "depth": {
                            "type": "number",
                            "description": "Cut depth in mm. Omit when through_all is true.",
                        },
                        "limit1": {
                            "type": "number",
                            "description": (
                                "Two-sided cut: how far the pocket runs toward the "
                                "sketch normal's positive side, in mm. Give limit2 "
                                "for the other side - one Pocket whose two depths "
                                "differ (catia_pad has the same pair; measured "
                                "there on B30 2026-11-19, z -3..+5 for limit1=5, "
                                "limit2=3)."
                            ),
                        },
                        "limit2": {
                            "type": "number",
                            "description": (
                                "How far the same Pocket cuts the OTHER way from "
                                "the sketch plane, in mm. 0 or omitted = one-sided."
                            ),
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
                                "or 'both' (same as symmetric=true). 'DEPTH IS THE "
                                "TOTAL, on 'both' AS WELL': depth=14 removes 7 mm on "
                                "each side of the sketch plane, not 14. Reported "
                                "2026-11-13 after depth=14 cut y = -7..+7 on a 26 mm "
                                "wall and depth=28 was needed to go through - the "
                                "old text only said this for symmetric=true. "
                                "HOW MANY FEATURES: one, when the sketch plane sits "
                                "on a face (measured on B30 2026-11-13: a pocket on "
                                "a 40x40x10 block answered '5.000 mm total, 2.500 "
                                "each side' and added ONE '凹槽.1'). TWO when the "
                                "plane sits INSIDE the material - a mid-plane sketch "
                                "is built as two one-sided cuts, so the tree gains "
                                "'凹槽.1' and '凹槽.2' and the reply says 'two "
                                "one-sided cuts' (measured the same day, offset=5 on "
                                "a 10 mm block). Bookkeeping by feature count is off "
                                "by one in that case; catia_list_features shows both. "
                                "THE SECOND COST IS TOPOLOGY, AND IT DEPENDS ON THE "
                                "GEOMETRY - measured in both directions. The "
                                "2026-11-21 report's P01 skirt window came back with "
                                "37 faces against the source's 33, the extras "
                                "arriving as duplicate pairs at the sketch plane "
                                "(Face.4/Face.26 at 28.901 mm2, Face.5/Face.25 at "
                                "1191.621 mm2). A controlled through-cut from the "
                                "mid-plane of a 100x60x20 block did NOT split "
                                "anything on B30 the same day: 10 faces, exactly the "
                                "same as the same window cut one-sided from the top. "
                                "So neither 'it always splits' nor 'it never does' "
                                "is true; if a face count is your acceptance "
                                "criterion, cut ONE side from a sketch on an offset "
                                "plane (reported 2026-11-21, P1-13)."
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
                            "description": (
                                "Revolution angle in degrees (default: 360 for full "
                                "revolution), swept on ONE side of the profile "
                                "plane. For 'the same sweep either side of that "
                                "plane' - a 67 deg segment centred on it - use "
                                "angle1/angle2 instead."
                            ),
                            "default": 360,
                        },
                        "angle1": {
                            "type": "number",
                            "description": (
                                "Revolution angle swept toward one side of the "
                                "profile plane, in degrees. With angle2 it makes "
                                "one Shaft whose segment straddles the plane - "
                                "CATIA's Angle 1 / Angle 2. Measured on B30 "
                                "2026-11-19: angle1=30, angle2=30 swept the same "
                                "total as a single 60 (1570.7963 mm3 both times) "
                                "but its extent moved, so angle2 really is the "
                                "OTHER side. A report's '67 deg, symmetric about "
                                "the mid-plane' is angle1=33.5, angle2=33.5."
                            ),
                        },
                        "angle2": {
                            "type": "number",
                            "description": (
                                "Revolution angle swept the other way from the "
                                "profile plane, in degrees (default 0). Only read "
                                "when angle1 is given."
                            ),
                            "default": 0,
                        },
                        "axis": {
                            "type": "string",
                            "description": (
                                "Axis to revolve about. 'horizontal' (default) is the "
                                "sketch's H axis (u axis, v=0) and suits a profile "
                                "drawn above it with x = axial, y = radius; "
                                "'vertical' is the V axis. ANY OTHER VALUE IS AN "
                                "ELEMENT NAME - a catia_gsd_line, 'Edge.3', the axis "
                                "of an earlier feature - and that works: measured on "
                                "B30 2026-11-14, the same profile revolved about "
                                "three lines gave 28274.33 / 18849.56 / 84823.00 mm3, "
                                "each matching 2*pi*R*area for that line's own "
                                "distance. A construction line INSIDE the sketch is "
                                "still refused by CATIA at update - draw the axis "
                                "outside the sketch (a gsd_line) and name it here."
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
                            "description": (
                                "Revolution angle in degrees (default: 360), swept "
                                "on ONE side of the profile plane. Use "
                                "angle1/angle2 for a groove centred on that plane."
                            ),
                            "default": 360,
                        },
                        "angle1": {
                            "type": "number",
                            "description": (
                                "Sweep toward one side of the profile plane, in "
                                "degrees - see catia_shaft, where the pair is "
                                "measured."
                            ),
                        },
                        "angle2": {
                            "type": "number",
                            "description": (
                                "Sweep the other way from the profile plane, in "
                                "degrees (default 0). Only read when angle1 is "
                                "given."
                            ),
                            "default": 0,
                        },
                        "axis": {
                            "type": "string",
                            "description": (
                                "Axis to revolve about: 'horizontal' (default), "
                                "'vertical', or the NAME OF AN ELEMENT - a "
                                "catia_gsd_line or an edge - which is measured to "
                                "work (see catia_shaft). A construction line inside "
                                "the sketch is refused by CATIA at update."
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
                    "Add a fillet (rounded edge) to one or more edges of the current "
                    "solid. Specify the radius and the edge names or feature to fillet. "
                    "Pass 'edge_names' to fillet several edges in one call: each edge "
                    "becomes its own feature, and if the single batch rebuild fails "
                    "they are retried one at a time - the reply reports which ones "
                    "took and why the others did not. Omit every edge argument to "
                    "round ALL edges of the body: the tool enumerates them (CATIA's "
                    "whole-feature fillet call does not build on B30 - measured on a "
                    "2-edge cylinder, a 12-edge rectangle and two polylines), builds "
                    "one feature per edge, and stops retrying above max_edges. "
                    "Prefer the 'fingerprint' from "
                    "catia_list_edges ('L17.6112|Pad.6;2|Sketch.7;2') over 'Edge.N': "
                    "the index is renumbered by every feature, the fingerprint is "
                    "re-resolved when the call runs. When the source data gives "
                    "COORDINATES instead of edge names - 'R2.5 on the edge at "
                    "(0, 65, -20)' - pass them as edge_anchors: each one is matched "
                    "to the nearest edge, and the reply states the edge name and the "
                    "measured distance for every anchor. An anchor that matches "
                    "nothing within anchor_tol, or matches two edges equally well, "
                    "is refused rather than guessed.\n"
                    "A NAMED EDGE THAT DOES NOT TAKE THE ROUND MAKES THE CALL FAIL: "
                    "if any requested edge is left out the reply is ok:false with "
                    "code=PARTIAL_EDGES_FAILED (EDGES_REJECTED when none built), the "
                    "applied and rejected edges in data, and modified=true when "
                    "something did build. Measured on B30 2026-11-16: two ADJACENT "
                    "top edges of a box give 1 of 2 - the second one's update "
                    "refuses - while two OPPOSITE edges give 2 of 2. Omit every edge "
                    "argument to round all of them: that path still answers in "
                    "prose, because a short edge refusing a large radius is "
                    "arithmetic rather than a failed request."
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
                                "Omit together with edge_names/edge_anchors to round "
                                "EVERY edge of the body: the tool enumerates them "
                                "and builds one feature per edge (see max_edges)."
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
                        "max_edges": {
                            "type": "integer",
                            "default": 200,
                            "description": (
                                "Only used when edge_name/edge_names/edge_anchors "
                                "are all omitted. The tool then rounds EVERY edge "
                                "of the body and, up to this many edges, retries "
                                "each one on its own when the single batch rebuild "
                                "fails. Above it only the batch is attempted and "
                                "the reply says so: a retry is a separate rebuild "
                                "and a separate feature (measured: about 0.6 s per "
                                "edge that takes and longer for the ones that fail "
                                "- 381 edges of a real part took 458 s and left "
                                "101 features)."
                            ),
                        },
                        **_EDGE_ANCHOR_PROPERTIES,
                    },
                    "required": ["radius"],
                },
            },
            {
                "name": "catia_chamfer",
                "description": (
                    "Add a chamfer (beveled edge) to one or more edges of the "
                    "current solid. Omit every edge argument to bevel ALL edges of "
                    "the body: the tool enumerates them, builds one feature per "
                    "edge and stops retrying above max_edges, because CATIA's "
                    "whole-feature chamfer call does not build on B30 (measured: "
                    "a feature reference and a body reference both fail at "
                    "construction on a 2-edge cylinder and a 12-edge rectangle).\n"
                    "A NAMED EDGE THAT DOES NOT TAKE THE BEVEL MAKES THE CALL FAIL: "
                    "if any requested edge is left out the reply is ok:false with "
                    "code=PARTIAL_EDGES_FAILED (EDGES_REJECTED when none built), the "
                    "applied and rejected edges in data, and modified=true when "
                    "something did build. Measured on B30 2026-11-16: two ADJACENT "
                    "top edges of a box give 1 of 2 with 'UpdateObject failed' on "
                    "the second, all four give 2 of 4, and two OPPOSITE edges give "
                    "2 of 2 - so check whether the edges share a vertex before "
                    "reading a partial result as a bad argument. Omit every edge "
                    "argument to bevel all of them: that path still answers in "
                    "prose, because a short edge refusing a large bevel is "
                    "arithmetic rather than a failed request.\n"
                    "TWO-DISTANCE (ASYMMETRIC) BEVEL: pass `length2` and the bevel "
                    "is built in CATIA's catLengthLength mode instead of "
                    "length+angle - `length` on one adjacent face, `length2` on "
                    "the other, `angle` ignored. Measured on B30 2026-11-21 on a "
                    "100 mm cube edge: 25/20 removes exactly 25*20/2*100 = "
                    "25 000 mm3, where 25 mm at 20 deg removes 11 374 mm3 - so "
                    "read the volume, not the face count, when checking this. "
                    "WHICH face gets which distance follows the edge's own "
                    "direction, not the caller's naming, and this build cannot "
                    "read the two lengths back: Chamfer.Length and "
                    "Chamfer.SecondLength both raise 'Item.Length' (measured on "
                    "B30 2026-11-22, typed and dynamic dispatch alike), while "
                    "Chamfer.Mode DOES read back. So the reply quotes the mode it "
                    "read, says plainly that the lengths are the values that were "
                    "sent, and reports the volume it removed against the ideal "
                    "L*a*b/2 - which is the check the 2026-11-21 report did by "
                    "hand. If the bevel landed on the wrong side, repeat with "
                    "swap_distances=true instead of deleting and rebuilding by "
                    "hand. This is what the 2026-11-21 report (P0-1) needed and "
                    "had to cut two triangle sketches for."
                ),
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
                        "length2": {
                            "type": "number",
                            "description": (
                                "Second distance in mm - selects a TWO-DISTANCE "
                                "(asymmetric) bevel: `length` on one adjacent "
                                "face, `length2` on the other, and `angle` is "
                                "then ignored. Measured on B30 2026-11-21: 25/20 "
                                "on a 100 mm edge removes exactly 25 000 mm3 "
                                "(25*20/2*100), against 11 374 mm3 for 25 mm at "
                                "20 deg. Omit it for the usual length+angle "
                                "bevel. Must be > 0."
                            ),
                        },
                        "edge_name": {
                            "anyOf": [{"type": "string"}, {"type": "object"}],
                            "description": (
                                "Edge to chamfer: 'Edge.N' or a fingerprint from "
                                "catia_list_edges. Omit together with edge_names/"
                                "edge_anchors to bevel EVERY edge of the body - the "
                                "tool enumerates them and builds one feature per "
                                "edge (see max_edges)."
                            ),
                        },
                        "swap_distances": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Exchange `length` and `length2` before building. "
                                "For the case the reply cannot decide for you: a "
                                "two-distance bevel landed with the long side on "
                                "the face you did not mean, and this build cannot "
                                "read the lengths back to prove it. Needs "
                                "length2; refused (nothing created) without it. "
                                "Delete the wrong feature first - this builds a "
                                "new one rather than editing the old."
                            ),
                        },
                        "edge_names": {
                            "type": "array",
                            "items": {"anyOf": [{"type": "string"}, {"type": "object"}]},
                            "description": "Several edges at once, e.g. ['Edge.3','Edge.9'] or a list of fingerprints. Takes precedence over edge_name.",
                        },
                        "max_edges": {
                            "type": "integer",
                            "default": 200,
                            "description": (
                                "Only used when every edge argument is omitted. "
                                "Up to this many edges each one is retried on its "
                                "own when the batch rebuild fails; above it only "
                                "the batch is attempted and the reply says so."
                            ),
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
                    "bottom is flat, so a blind hole has no drill point. "
                    "WHERE THE BORE GOES: the Hole feature takes its axis from a "
                    "POINT in the sketch, not from the circle's centre. A sketch "
                    "holding only a circle is drilled at the SKETCH ORIGIN - "
                    "measured 2026-10-27 on B30, circles at (0,0) and at (10,0) "
                    "produced bit-identical parts, and a circle at (100,100) on a "
                    "40x20 block drilled a bore through the middle of the block. So "
                    "when the sketch was drawn in this session and holds a circle "
                    "with no point at its centre, a construction point is added at "
                    "that centre first (and the reply says so), and the bore's "
                    "position is then CHECKED against the material actually removed: "
                    "a bore that did not land on the profile is rolled back and "
                    "reported as an error, never left in the tree. "
                    "`depth` is measured FROM THE SKETCH PLANE, not from the face "
                    "the bore breaks out of, and the reply converts the removed "
                    "volume into the bore length it actually drilled."),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "diameter": {
                            "type": "number",
                            "description": "Hole diameter in mm",
                        },
                        "depth": {
                            "type": "number",
                            "description": (
                                "Hole depth in mm, measured from the SKETCH PLANE. "
                                "With the sketch on a datum plane an offset away from "
                                "the material, the bore is (distance to the face) + "
                                "depth long; the reply states the length it drilled. "
                                "Omit when through_all is true."
                            ),
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
                            "description": (
                                "Add a thread to the bore (default: false). "
                                "CatHoleThreadingMode on B30 is 0 = threaded, "
                                "1 = smooth. Two measured facts shape this flag: "
                                "a new hole carries 1, so the thread is applied "
                                "AFTER the hole has materialized and the reply says "
                                "THREADED when it took; and a hole INHERITS the mode "
                                "of the hole before it in the same part, so this "
                                "flag is always written - leaving it at false still "
                                "resets the bore to smooth instead of inheriting the "
                                "previous thread. Pitch and thread standard are the "
                                "hole's own parameters and are NOT set here: the "
                                "thread is whatever CATIA defaults to for this "
                                "diameter, so a drawing that calls out a specific "
                                "pitch still needs the thread definition edited in "
                                "CATIA. "
                                "BUILD LIMIT: on V5-6R2022 (B32) this build cannot "
                                "thread a hole through ThreadingMode alone - the "
                                "update fails, the feature is removed again and the "
                                "call answers FEATURE_INCOMPLETE / execute with "
                                "rolled_back, so a plan that needs threads will stop "
                                "there (reported on B32 2026-11-05/06; B30 is the "
                                "verified build, and it reports NO_MATERIAL before "
                                "the write instead)."
                            ),
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
                    "four to use CATIA's origin planes, which drive dir1 along -Y "
                    "and dir2 along -X on this build (measured 2026-11-08 by "
                    "reading the body's box ENDPOINTS - a length delta cannot see "
                    "a sign). The explicit dir1_axis/dir2_axis form builds exactly "
                    "what it says: 'axis x+' moves the copies to +X."
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
                    "change - which proves the INSTANCES were built, and says "
                    "nothing about their orientation. That distinction is not "
                    "academic: with radius_aligned=false CATIA places every copy "
                    "at the right angle and leaves its body parallel to the seed, "
                    "and the volume is still exactly count x seed (reported "
                    "2026-10-30: an 8-blade fan came out with blades that did "
                    "not turn, bbox Z +-305 where 8-fold symmetry needs +-500)."
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
                        "radius_aligned": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Whether each copy is ROTATED with the pattern "
                                "(CATIA's 'radius aligned'). True by default, "
                                "because a circular pattern of a real part - a "
                                "blade, a crank, a lug - means the copy points "
                                "outward. With false, CATIA translates each copy "
                                "to its angular position and keeps it parallel to "
                                "the seed: correct for a ring of identical bosses, "
                                "wrong for anything with a direction. Measured on "
                                "B30 2026-10-30: an 8-copy pattern of a 400 mm "
                                "bar gave Z +-305 with false and +-500 with true, "
                                "while the volume was 8x the seed in both."
                            ),
                        },
                        "reverse": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Turn the copies the other way round the axis. "
                                "The measured default is CLOCKWISE about the named "
                                "axis - a 4x4 pad seeded at (30, 0) copied to "
                                "(0, -30) with angular_spacing 90 about Z, on B30 "
                                "2026-11-02 - which is the opposite of the "
                                "right-hand rule a caller reads off 'axis z'. "
                                "Until 0.3.48 the only workaround was a negative "
                                "angular_spacing, and the flag behind it was hard "
                                "coded. The reply now says which way it went."
                            ),
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
                    "MIRROR THE WHOLE BODY about a plane and UNION the copy with it "
                    "- Transform > Mirror, not 'mirror this one feature'. The "
                    "distinction is not cosmetic, and it is measured FOUR ways on "
                    "B30 2026-11-22 (the 2026-11-22 report asked whether another "
                    "route existed, so the whole surface was read): "
                    "(1) ShapeFactory.AddNewMirror takes ONE argument and ignores "
                    "what kind of reference it is - passed a plane reference and "
                    "passed a FEATURE reference, a 60x40x50 box with a 10x10x10 pad "
                    "on top answered 121 000 -> 242 000 mm3 both times, the whole "
                    "body twice; "
                    "(2) the feature's Object AND MirroringObject both raise "
                    "\"can not be set\"; only MirroringPlane is writable; "
                    "(3) Mirror.AddObjectToMirror(reference) is accepted, but it "
                    "appends to a list that already holds the BODY and nothing "
                    "removes the body - 112 000 mm3 became 224 000; "
                    "(4) on a body symmetric about the plane (232 000 mm3, one "
                    "off-centre pocket) the same call gave 240 000: the mirrored "
                    "body's material lands in the pocket and FILLS it. "
                    "Earlier rounds measured the same operation's cost on real "
                    "parts: a 60x40x50 box with a 20x40 pocket (80000 mm3) answers "
                    "160000, and the 2026-11-17 report's groove went 26228730.962 -> "
                    "42188262.4593 mm3 (the pre-groove volume). "
                    "SO IT IS REFUSED WHEN THE LAST FEATURE IS A CUT (pocket, "
                    "groove, hole, split, shell, thickness): MIRROR_WOULD_FILL_CUT, "
                    "nothing created, and the volume in the payload. "
                    "feature='<name>' is refused too (MIRROR_SEED_UNSUPPORTED) - "
                    "this API cannot target a seed, and accepting the parameter "
                    "would produce the body operation under a feature's name. "
                    "TO MIRROR ONE FEATURE, build it twice: a second sketch on the "
                    "mirrored frame, then the same pocket/groove. The report that "
                    "found this reached -0.00027% of its source volume that way "
                    "(frame u negated, two catia_groove(360) calls). "
                    "allow_body_mirror=true performs the body operation anyway, for "
                    "the case it IS right for: completing a symmetric part whose "
                    "half was built from pads. Every reply carries the volume before "
                    "and after, so 'the copy was added' is a measurement and not a "
                    "claim."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "plane": {
                            "type": "string",
                            "description": "Mirror plane: 'xy', 'yz', or 'zx'",
                            "enum": ["xy", "yz", "zx"],
                        },
                        "feature": {
                            "type": "string",
                            "description": (
                                "A feature name from catia_list_features, if what you "
                                "want is one FEATURE mirrored. Refused with "
                                "MIRROR_SEED_UNSUPPORTED and the two-call recipe. "
                                "Measured on B30 2026-11-22: no seed route exists - "
                                "Object/MirroringObject are read-only, AddNewMirror "
                                "mirrors the whole body whichever reference is "
                                "passed, and AddObjectToMirror only appends to a list "
                                "that already holds the body. Answering with the "
                                "whole-body operation would be a different shape, "
                                "not a narrower one."),
                        },
                        "allow_body_mirror": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Perform the whole-body mirror even though the last "
                                "feature is a cut. Correct for completing a symmetric "
                                "half built from pads; it WILL put material back into "
                                "a cut, and the reply says by how much."),
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
                            "minimum": 0,
                            "description": (
                                "Wall thickness in mm, measured INWARD from the "
                                "surface. 0 is allowed when external_thickness > 0: "
                                "that is a purely OUTWARD shell, whose wall grows "
                                "outside the original envelope (a part whose "
                                "tolerances are on the outside needs exactly that). "
                                "A negative value is refused up front - CATIA "
                                "rejects it with a bare 'UpdateObject failed'."
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
                    "It also deletes a GSD element or a geometrical set - "
                    "'bridging.1', 'loft.1', 'GSD_Set' - which live in "
                    "HybridBodies rather than in a solid body's feature list "
                    "(reported 2026-10-29, §4: five failed loft pieces stayed in "
                    "the tree and polluted the STL envelope). "
                    "Undoing the feature you have just created is still one call, "
                    "but it now has to be asked for: with neither feature_name nor "
                    "index, pass last=true. A bare no-argument call is refused and "
                    "names what it would have deleted, because on a long tree the "
                    "last created feature is usually the fillet you just added, not "
                    "the test sketch you meant (reported 2026-10-25). "
                    "CASCADE: points that ARE tree features are not deleted with the "
                    "feature that uses them - a six-point spline built with "
                    "create_points=true left six Point features behind when it was "
                    "deleted (measured 2026-11-03, and a real session grew from 666 "
                    "to 801 elements that way), and the reply names them. "
                    "cascade=true removes them too. THIS ONLY APPLIES WHEN THE "
                    "POINTS ARE IN THE TREE: catia_gsd_spline builds with "
                    "create_points=false by default, and then the points are "
                    "referenced but not listed, so deleting the curve leaves "
                    "NOTHING behind and cascade has nothing to do (measured on B30 "
                    "2026-11-16: 6 coordinates -> 1 element -> 0 after the delete; "
                    "the same six with create_points=true -> 7 elements -> 6 left). "
                    "Only "
                    "points created in THIS session are recorded, so the reply says "
                    "how many it can account for. BATCH: pass 'names' with a list "
                    "to delete several elements in one call - cleaning up after a "
                    "rebuild cost 15 calls of 2.0-2.4 s each in that same session. "
                    "DUPLICATE NAMES: CATIA lets two elements carry one name, and "
                    "assigning Name does not prevent it, so a tree can hold two "
                    "'P_04_06'. Deleting one BY NAME then removes an arbitrary one "
                    "of them and leaves the other answering to the same name, which "
                    "is a state where 'I deleted it and rebuilt it' is true and the "
                    "old element is still there - measured on B30 2026-11-03, and "
                    "the reason a report spent two hours on a join that kept "
                    "failing. Such a delete is now REFUSED with the count, and "
                    "names=['P_04_06'] deletes every element carrying that name and "
                    "reports how many it took. New elements no longer create "
                    "duplicates: a taken name comes back as 'P_04_06.2' with a note. "
                    "FRAME SCAFFOLDING: deleting a sketch that was built on a frame "
                    "leaves its datum plane and 3 construction points in the tree - "
                    "they are NOT its children (measured on B30 2026-11-18). "
                    "cleanup_frame_datum defaults to \"auto\" and removes them "
                    "unless ANOTHER SKETCH STANDS ON THAT DATUM - a frame datum "
                    "can be a shared support, and deleting it under a live sketch "
                    "breaks that sketch (measured on B30 2026-11-18: the "
                    "survivor then reads 需要修改绝对轴定义以解决这些问题 and the "
                    "update fails). Which sketches stand on it is READ from their "
                    "own absolute axis systems, not guessed from how many sketches "
                    "exist and not inferred from sharing its plane; when it cannot "
                    "be read, nothing is deleted. Pass true to force it, false to "
                    "keep them. "
                    "A SKETCH A FEATURE CONSUMES is refused before anything is "
                    "attempted (SKETCH_CONSUMED_BY): the Part Design features "
                    "answer .Sketch, so the profile of '凸台.1' is known, and "
                    "deleting that sketch is either refused by CATIA or leaves "
                    "the pad in error - measured both ways on B30 2026-11-18. "
                    "Delete the feature instead, or pass both names to 'names'. "
                    "document='<name>' deletes in ANOTHER open part without the "
                    "caller having to activate it first - and it DOES activate it, "
                    "because every step here resolves names through the active "
                    "part; a delete that silently resolved them in the wrong tree "
                    "would remove something nobody named. The switch is "
                    "verified, and the reply says the active document moved."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "document": {
                            "type": "string",
                            "description": (
                                "Name or full path of an open Part to delete in. "
                                "This ACTIVATES that document (a delete resolves "
                                "names in the active part); if CATIA will not put "
                                "it in front - a part held by an open assembly - "
                                "the call is refused rather than run against the "
                                "wrong document."
                            ),
                        },
                        "feature_name": {
                            "type": "string",
                            "description": "Feature to delete (see catia_list_features)",
                        },
                        "names": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Delete several features/GSD elements by name in one "
                                "call. Cannot be combined with feature_name, index or "
                                "last; failures are reported per name. An entry with "
                                "'*' or '?' is a PATTERN, matched against the "
                                "elements that exist now ('*' any run, '?' one "
                                "character) and expanded before anything is deleted - "
                                "the reply prints what it matched. Feature names do "
                                "not survive a rebuild (the same four fillets came "
                                "back as 倒圆角.13/.14/.18/.19, then .37/.38/.42, "
                                "then .61/.62/.66/.67 - 2026-11-23 report), so "
                                "['倒圆角.*'] is the form that keeps working where a "
                                "list typed from a previous call does not. A pattern "
                                "that matches nothing is REFUSED: deleting nothing "
                                "successfully reads as a cleanup that happened."
                            ),
                        },
                        "cascade": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Also delete the construction points this feature "
                                "created (recorded from this session only)."
                            ),
                        },
                        "dry_run": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Answer what this delete WOULD take with it, and "
                                "delete nothing: the sketch a feature's profile "
                                "consumes (CATIA cascades it), whether a sketch is "
                                "consumed at all (that delete is refused), and "
                                "which features come later in the tree as "
                                "CANDIDATES for breaking - a superset, because "
                                "this server has no dependency graph. Needs "
                                "'feature_name' or 'names'."
                            ),
                        },
                        "cleanup_frame_datum": {
                            "type": ["boolean", "string"],
                            "enum": [True, False, "auto"],
                            "default": "auto",
                            "description": (
                                "When the deleted element is a SKETCH, also remove "
                                "the datum plane and the 3 points a frame support "
                                "left in the tree. A frame sketch's scaffolding is "
                                "NOT its child: deleting the sketch leaves "
                                "'<sketch>_FRAME' and '<sketch>_FRAME.O/.U/.V' "
                                "behind (measured on B30 2026-11-18: the plane and "
                                "the 3 points stayed, and deleting them afterwards "
                                "works, plane first). The names are given by "
                                "catia_create_sketch since 0.3.146; a sketch made "
                                "before that carries 'Datum_frame.N' and three "
                                "unnamed points that cannot be tied to it, and the "
                                "reply says so instead of deleting a neighbour's "
                                "support. "
                                "\"auto\" (the default) cleans up unless another "
                                "sketch stands on the same DATUM - the same "
                                "absolute axis system, origin and directions both. "
                                "Two earlier criteria were measured wrong and are "
                                "recorded: \"are there other sketches\" was not "
                                "about the datum at all, so a part full of frame "
                                "sketches (the 2026-11-18 report's P02/P05/P06 "
                                "hold 5-8 each) never cleaned anything up; and "
                                "\"same plane\" (0.3.148) refused on sketches that "
                                "merely share an infinite plane - two frames on "
                                "z = 0 with datum points 50 mm apart are two "
                                "datums, and the reporter measured that the "
                                "cleanup is safe there. Sketch.GetAbsoluteAxisData "
                                "says where each surviving sketch sits, origin "
                                "included; if that cannot be read, nothing is "
                                "deleted. true forces the cleanup (and reports an "
                                "update failure if one follows), false leaves "
                                "everything alone."
                            ),
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
                "description": (
                    "List all features in the active Part Body with their names and "
                    "types, plus the sketches in that body ('sketches', not counted "
                    "in 'count') and any geometrical sets the tools left behind. "
                    "This is how to check whether a sketch still exists after a "
                    "failed pad/pocket/hole: those rollbacks take the sketch with "
                    "them. ALWAYS an object, even when the body holds no feature: "
                    "'count' is 0 and 'features' is empty then, and the sketches "
                    "are still listed (a body holding only sketches used to answer "
                    "with a bare string, which broke every caller reading "
                    "['sketches'] in exactly the situation this tool exists for). "
                    "document='<name>' reads another OPEN part's tree without "
                    "changing the active document (its MainBody, since a named "
                    "document has no 'work body' of its own): useful straight after "
                    "opening an assembly, when the next feature call would "
                    "otherwise land on the product."),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "document": {
                            "type": "string",
                            "description": (
                                "Name or full path of an open Part to list "
                                "instead of the active one. Reading it does NOT "
                                "activate it. Omit to list the active part."
                            ),
                        },
                    },
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
                    "gap in the probing, not a CATIA limit. "
                    "THE REPLY NAMES THE SECTIONS CATIA ACTUALLY KEPT, read back with "
                    "GetSectionFromLoft after the update: 'CATIA's own section list "
                    "holds 3: 草图.1, 草图.2, 草图.3'. So a section that was dropped "
                    "is a warning with its name, not something to infer from a "
                    "volume that happens to look right - the 2026-11-12 report had 45 "
                    "sections in and could only compare its own integral (69976 mm3) "
                    "against the reported 70047.2. If the read-back refuses on some "
                    "build, the reply says the count is the number SENT, not one "
                    "measured."
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
                        "closing_points": {
                            "type": "array",
                            "items": {
                                "anyOf": [{"type": "string"},
                                          {"type": "null"}],
                            },
                            "description": (
                                "ONE PER SECTION, in order: the tree name of the "
                                "point each section's parameter 0 is taken from, or "
                                "null to leave that section automatic. Where the "
                                "sections move sideways, CATIA's automatic choice "
                                "can put parameter 0 on a different side of one "
                                "section than of the next, and the surface then "
                                "twists on that side while the other side stays "
                                "smooth - which is what makes a 24-section head "
                                "look faceted on one half only (2026-11-15 report "
                                "C2). The point must lie ON its section; build it "
                                "with catia_gsd_point. MEASURED on B30 2026-11-15 "
                                "on two eccentric ellipses: a closing point at "
                                "their +Y extreme changed the solid from "
                                "4523.6938 to 4523.8263 mm3, so the argument is "
                                "honoured rather than ignored. What is NOT "
                                "available: SetSectionCoupling exists on the "
                                "object but every call for it answers "
                                "'<unknown>.SetSectionCoupling' on this build "
                                "(direct and dynamic dispatch, all four values), "
                                "and SectionCoupling only reads back."
                            ),
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
                "name": "catia_move_body",
                "description": (
                    "Move a whole solid body by (dx, dy, dz) millimetres, and prove "
                    "the shape did not change: the volume and the surface area are "
                    "compared before and after, and the centre of gravity must move "
                    "by exactly the vector asked for. Any difference is refused with "
                    "MOVE_NOT_RIGID and the two sets of numbers.\n"
                    "\n"
                    "HOW, because it decides what you get back: CATIA V5 exposes no "
                    "transform for a SOLID. Measured on B30 2026-11-23 - Part, Body "
                    "and Shape declare no move/translate/position member at all; "
                    "ShapeFactory's AddNewTranslate2 and AddNewAxisToAxis2 do build a "
                    "feature, but it rebuilds nowhere (body, pad and selection as "
                    "in-work object all fail with 'Update', and it exposes no "
                    "Distance/Vector to set); and GSD's AddNewTranslate on a body "
                    "reference returns the body's BOUNDARY, not the body (its Area "
                    "equals the solid's and its Volume raises). So this tool "
                    "translates that boundary and closes it back into a solid.\n"
                    "\n"
                    "WHAT THAT COSTS: the result is a DEAD SOLID - one "
                    "CloseSurface feature, no sketch/pad/fillet history - in a NEW "
                    "body, and the source body stays in the tree with every feature "
                    "it had (by default it is hidden, so the picture and the mesh "
                    "envelope show the moved part only). Features that referenced "
                    "the source body's faces do NOT move with it. A translated "
                    "surface is left in a geometrical set named '<body>_MOVE' "
                    "because the closed solid depends on it."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "dx": {
                            "type": "number",
                            "description": "Millimetres along +X. At least one of "
                                           "dx, dy, dz must be non-zero.",
                        },
                        "dy": {"type": "number",
                               "description": "Millimetres along +Y."},
                        "dz": {"type": "number",
                               "description": "Millimetres along +Z."},
                        "name": {
                            "type": "string",
                            "description": (
                                "Body to move. Default: the active body (the one "
                                "catia_new_body last switched to), else the MainBody."
                            ),
                        },
                        "new_body_name": {
                            "type": "string",
                            "description": (
                                "Name for the body that receives the moved solid. "
                                "Default '<name>_MOVED'."
                            ),
                        },
                        "hide_source": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Hide the source body after the move, so the part "
                                "looks and measures as one solid. False keeps both "
                                "visible, overlapping."
                            ),
                        },
                        "document": {
                            "type": "string",
                            "description": (
                                "Part document to act on, by name or full path. "
                                "Default: the active document."
                            ),
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
                    "visible surfaces, so leftovers also corrupt the measured envelope. "
                    "On a PRODUCT document it applies to every part under it (one "
                    "entry per part, sub-assemblies included) and the active document "
                    "does not move - so cleaning a finished assembly before a "
                    "screenshot is one call, not one call per part. Visibility is "
                    "session state in each part document: save the parts if it has to "
                    "survive the session. "
                    "When only ONE thing has to come back, call this on that element "
                    "by name rather than on the set: measured 2026-11-09 on a "
                    "thickened surface inside a hidden set - "
                    "set_visibility(name='加厚曲面.1', show=true) brought the solid "
                    "back without showing the rest of the set."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": (
                                "Tree name: a body ('零件几何体'), a geometrical set "
                                "('GSD_Set'), an element inside one ('S1', 'Closed'), "
                                "a feature, a sketch - or, in an assembly document, "
                                "a COMPONENT INSTANCE ('P05_leg.3', or a path like "
                                "'ASM/SUB.1/leg.2'). An instance name was refused "
                                "until 0.3.161 ('No tree node named ... in any of "
                                "the 4 part(s)'), which made 'hide that side plate and "
                                "look inside' impossible. An instance is hidden "
                                "through the product's own Selection and the result "
                                "is READ BACK with VisProperties.GetShow - measured "
                                "on B30 2026-11-23, (0, 1) is hidden and (0, 0) is "
                                "shown - so the reply states a reading."
                            ),
                        },
                        "kind": {
                            "type": "string",
                            "description": (
                                "Hide a whole class of tree bookkeeping in one call, "
                                "instead of naming each one: 'sketch' (every sketch in "
                                "every body), 'datum_plane' (the three reference "
                                "planes PLUS every datum element inside a solid body - "
                                "the points and plane a frame sketch creates live "
                                "there, and until 0.3.80 they were left on screen), or "
                                "'construction' (both). These are the "
                                "white frames that clutter a screenshot - a sketch "
                                "outline is not solid geometry, but it is drawn on top "
                                "of the solid and reads as a defect. On a Product this "
                                "walks every part under it and reports how many of how "
                                "many it touched. A geometrical set is NOT descended "
                                "here: a surface in one is usually the design, so use "
                                "the name for those."
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
                                "body; the active body then becomes the target. "
                                "ALIASES so a non-English installation's default "
                                "body name is not needed: 'active'/'current' is the "
                                "body in work, 'first'/'main'/'mainbody'/'partbody' "
                                "is the part's main body (零件几何体 on a Chinese "
                                "B30). Anything else is looked up as a name."
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
                "name": "catia_split_solid",
                "description": (
                    "CUTS A BODY IN TWO AND KEEPS ONE HALF - it is NOT a local cut, "
                    "not an engraving and not a pocket. (CATIA's Chinese UI calls "
                    "both this and the surface split '分割'; if you want a hole, a "
                    "label or a pattern cut into a face, this is the wrong tool - "
                    "use catia_boolean(remove) with a solid tool body.) It takes a "
                    "surface or a datum plane and keeps one side (Part Design "
                    "'Split'). The body keeps the result; the cutting element stays "
                    "where it was, so unlike catia_boolean nothing is consumed. "
                    "THE CUTTER MUST DIVIDE THE BODY COMPLETELY. A patch that "
                    "covers only part of the cross-section leaves no valid second "
                    "half and the update fails (measured on B30 2026-11-10: a "
                    "40x200 mm wall failed on a 100x100x10 block, the same wall "
                    "extended to 400x200 mm split it into 70000 and 30000 mm3). "
                    "Extend the surface past the body on every side, or use one "
                    "that closes around it - a local 'cut only here' is not a "
                    "Split, it is catia_boolean(remove) with a solid tool body. "
                    "WHICH HALF YOU GET: 'front' is the side the cutter's own "
                    "normal points to (measured, not assumed: a plane offset +Z "
                    "keeps Z below it, an extruded wall keeps the side its normal "
                    "faces). The reply reports both volumes, so a wrong half is "
                    "visible in the same call instead of three calls later; "
                    "catia_delete_feature that node and re-run with the other value."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "cutter": {
                            "type": "string",
                            "description": (
                                "Surface or plane to cut with, by tree name - a "
                                "surface in a geometrical set, or a datum plane "
                                "('xy'/'yz'/'zx' also work for the three origin "
                                "planes). It must reach past the body on every "
                                "side of the cut."
                            ),
                        },
                        "keep": {
                            "type": "string",
                            "enum": ["front", "back"],
                            "default": "front",
                            "description": (
                                "'front' = the side the cutter's normal points to, "
                                "'back' = the other side. If the reply's volume is "
                                "the half you did not want, delete the feature and "
                                "run again with the other value."
                            ),
                        },
                        "body": {
                            "type": "string",
                            "description": (
                                "Body to split. Defaults to the active body, which "
                                "then becomes the in-work object."
                            ),
                        },
                        "name": {
                            "type": "string",
                            "description": "Optional feature name",
                        },
                    },
                    "required": ["cutter"],
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
                    "thousands of characters of NLS text. coords=true gives ONE "
                    "point on each edge (to anchor a fillet); samples=N gives N "
                    "points spread along it (to rebuild the curve), which works on "
                    "open edges and is refused on a closed rim - see 'samples'."
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
                                "'the edge at (0, 65, -20)'. ONE point, though: to "
                                "rebuild the curve itself ask for 'samples'."
                            ),
                        },
                        "samples": {
                            "type": "integer",
                            "default": 0,
                            "minimum": 0,
                            "maximum": 50,
                            "description": (
                                "Points to place ALONG each edge (2..50; 0 = none), "
                                "so its curve can be rebuilt instead of only "
                                "located - the 2026-11-13 report's P1-5, which had "
                                "to recompute every contour from side files. They "
                                "come back as 'samples_mm' with the ratios that "
                                "produced them. WORKS ON OPEN EDGES ONLY: measured "
                                "on B30 2026-11-15, 12/12 edges of a padded plate "
                                "and 18/18 of a filleted one (arcs included), with "
                                "five points on a 40 mm edge landing exactly 10.000 "
                                "mm apart; a CLOSED rim - a hole's rim - refuses "
                                "(0/2 on a padded circle, because a point-on-curve "
                                "parameter needs a curve with two ends), and that "
                                "edge carries 'samples_unavailable' with CATIA's own "
                                "words. Every point is a real object in a scratch "
                                "set that is deleted again, and a rebuild of its "
                                "own, so a call is capped at 400 points "
                                "(edges x samples)."
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
                    "main body; pass body_name to enumerate another body. "
                    "Each face carries its own CENTROID and, when it is planar, "
                    "its PLANE (origin plus two directions) and NORMAL, so a face "
                    "can be picked by where it is instead of by guessing area - "
                    "the 0.3.120 reports' P1-3, twice. Measured on B30 2026-11-13 "
                    "on a 60x40x40 block: GetCOG on a FACE reference answers the "
                    "face's own centre (top face (30,20,40), bottom (30,20,0)) and "
                    "NOT the solid's (30,20,20) - the 'GetMeasurable reads the "
                    "whole body' note of 2026-11-10 applies to HYBRID references, "
                    "not to topological cells. There is no per-face BOUNDING BOX "
                    "and none is invented here: Measurable exposes 44 members on "
                    "B30 and none of them is a box, Inertia on a face reference "
                    "has none either, and searching Vertex/Edge under a face cell "
                    "returns 0 (all measured 2026-11-13). "
                    "TO FIND WHERE A FEATURE'S GEOMETRY IS, read this listing's "
                    "'created_by' - every face names the feature that made it - "
                    "and look at that face's centroid and area. That chain is the "
                    "answer to 'the pit is in the tree but I cannot ask where it "
                    "is' without reading sketch coordinates, which this API cannot "
                    "read at all."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "body_name": {
                            "type": "string",
                            "description": "Body to enumerate (default: the part's main body)",
                        },
                        "geometry": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Read each face's centroid (and plane/normal when "
                                "planar). Two extra measurements per face - pass "
                                "false for a quick list of a part with hundreds of "
                                "faces."
                            ),
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
            case "catia_edit_pad":
                return self._edit_pad(arguments)
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
                reply = self._delete_feature(arguments)
                # A delete that was given a document SWITCHED the active one, and
                # that is a change outside the model, so it is said in the reply -
                # unless the reply is a JSON payload, where appending prose would
                # break the shape the caller parses.
                switched = getattr(self, "_document_switched", "")
                self._document_switched = ""
                if switched and isinstance(reply, str) and not reply.lstrip().startswith("{"):
                    reply += (f" The active document is now '{switched}': this call "
                              "switched it, because 'document' named it and every "
                              "delete step resolves names in the active part.")
                return reply
            case "catia_list_features":
                return self._list_features(arguments.get("document"))
            case "catia_new_body":
                return self._new_body(arguments.get("name"))
            case "catia_solid_loft":
                return self._solid_loft(arguments)
            case "catia_delete_body":
                return self._delete_body(arguments["name"],
                                         bool(arguments.get("force", False)))
            case "catia_move_body":
                return self._move_body(arguments)
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
            case "catia_split_solid":
                return self._split_solid(arguments)
            case "catia_list_faces":
                return self._list_faces(arguments.get("body_name"),
                                        bool(arguments.get("geometry", True)))
            case "catia_list_edges":
                return self._list_edges(
                    arguments.get("min_length"), arguments.get("max_length"),
                    arguments.get("faces"), int(arguments.get("offset", 0) or 0),
                    int(arguments.get("limit", 0) or 0),
                    bool(arguments.get("include_nls", True)),
                    bool(arguments.get("coords", False)),
                    # Passed RAW: a non-numeric value has to become an
                    # ArgumentRejected that names 'samples', and int() here would
                    # raise a bare ValueError before the tool could say so.
                    arguments.get("samples", 0))
            case _:
                raise ArgumentRejected(f"Unknown part design tool: {tool_name}")

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
                replacement = self._sketch_alias(sketch_name)
                if replacement is not None:
                    try:
                        return sketches.Item(replacement)
                    except Exception:  # noqa: BLE001 - fall through to the error
                        pass
                names = self._sketch_names_with_aliases(body)
                hint = ""
                if replacement:
                    hint = (f" '{sketch_name}' was re-created as '{replacement}', "
                            "which is no longer in the tree either - read "
                            "catia_list_features.sketches.")
                raise CodedError(
                    f"No sketch named '{sketch_name}' in the active body. "
                    f"Available: {names or 'none'}.{hint}",
                    code="SKETCH_NOT_FOUND",
                    # The lookup is a walk over Body.Sketches; nothing was built,
                    # so the envelope must not say "model changes were not
                    # verified" (reported 2026-10-28, §5.2).
                    modified=False,
                    next_actions=[
                        "Pick a name from 'Available' (each one lists the older "
                        "names that still resolve to it) or call "
                        "catia_list_features to see the body's sketches; this "
                        "call created nothing, so nothing has to be undone"
                    ], stage="validate",
                )

        remembered = getattr(self.conn, "last_sketch", None)
        if remembered is not None:
            try:
                _ = remembered.Name  # still alive?
                return remembered
            except Exception:
                self.conn.last_sketch = None

        if sketches.Count == 0:
            raise PreconditionRejected("No sketches found in the active body. Create a sketch first.")
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
                raise ArgumentRejected(
                    f"No feature named '{feature_name}' in the active body. "
                    f"Available: {names or 'none'}."
                )

        if shapes.Count == 0:
            raise PreconditionRejected("No features found in the active body.")
        return body

    def _clear_selection(self) -> None:
        try:
            self.conn.hso.Clear()
        except Exception:
            pass

    def _edge_probe_points(self, part: Any,
                           body: Any = None) -> "_ProbePoints":
        """Scratch points for "which edge is this coordinate on" questions."""
        return _ProbePoints(self.conn, part, body)

    def _anchors_to_edges(
        self, body: Any, anchors: list[list[float]], tol: float,
        on_miss: str = "fail", sweep: float | None = None,
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
        Every bad anchor is listed at once, not just the first: twenty anchors
        with one wrong coordinate used to cost one round trip per bad anchor, and
        the report that hit it collapsed twenty anchors to nine by hand because
        the tool would only complain about one at a time (reported 2026-10-26).
        ``on_miss="skip"`` fillets the anchors that do match and reports the rest.
        """
        part = self.conn.get_active_part()
        spa = self._topo_measure(part)
        if spa is None:
            raise CodedError(
                "SPAWorkbench is not available, so edge anchors cannot be "
                "measured. Use Edge.N or a fingerprint from catia_list_edges.",
                code="UNSUPPORTED_CAPABILITY",
                stage="validate",
                modified=False,
                next_actions=[
                    "Address the edges as 'Edge.N' or as a fingerprint from "
                    "catia_list_edges - anchors need SPAWorkbench, which this "
                    "session does not expose",
                ],
            )
        # NO LENGTHS HERE: the measurables below are the expensive part and
        # they answer Length too, so asking the table for lengths as well would
        # pay for the same 53-ms-per-edge pass twice (see _edge_table).
        table = self._edge_table(body, lengths=False)
        if not table:
            raise PreconditionRejected("The active body has no edges to fillet yet.")
        measurables = []
        for entry in table:
            try:
                measured = spa.GetMeasurable(entry["cell"].Reference)
                entry["measurable"] = measured
                measurables.append(measured)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"Could not measure {entry['name']}: {exc}"
                ) from exc

        labels: list[str] = []
        refs: list[Any] = []
        matches: list[int] = []
        report: list[str] = []
        missed: list[dict[str, Any]] = []
        ambiguous: list[dict[str, Any]] = []
        seen: set[int] = set()
        probe = self._edge_probe_points(part, body)
        point_refs: dict[int, Any] = {}
        try:
            # ALL THE POINTS FIRST, ONE UPDATE. One rebuild per anchor is what
            # made a twenty-anchor fillet rebuild a whole part twenty times
            # (2026-11-19 report, P1-4). The points are independent scratch
            # objects, so one update after the last one is enough for all of them.
            indexed: list[tuple[int, list[float]]] = []
            for position, anchor in enumerate(anchors):
                if len(anchor) != 3:
                    raise ArgumentRejected(
                        f"Each edge anchor must be [x, y, z]; got {anchor!r}."
                    )
                point_refs[position] = probe.add(*anchor)
                indexed.append((position, anchor))
            probe.update()
            for position, anchor in indexed:
                point_ref = point_refs[position]
                dists: list[tuple[float, int]] = []
                for i, measurable in enumerate(measurables):
                    try:
                        dists.append((float(measurable.GetMinimumDistance(point_ref)), i))
                    except Exception:  # noqa: BLE001 - one edge, not the call
                        continue
                dists.sort()
                if not dists:
                    raise PreconditionRejected(
                        f"No edge of the body could be measured against anchor "
                        f"{anchor}."
                    )
                best_distance, best_index = dists[0]
                entry = table[best_index]
                if best_distance > tol:
                    # One length, because the caller's next move is to aim at
                    # this edge: the measurable is already in hand, so this is a
                    # property read, not another 53 ms pass.
                    if entry["length"] is None:
                        entry["length"] = self._cell_measure(
                            spa, entry["cell"], "Length",
                            measurable=entry.get("measurable"))
                    missed.append({
                        "anchor": [round(float(v), 6) for v in anchor],
                        "nearest_edge": entry["name"],
                        "nearest_length_mm": (
                            None if entry["length"] is None else
                            round(entry["length"], 4)),
                        "distance_mm": round(best_distance, 4),
                    })
                    continue
                near = [d for d in dists[1:]
                        if d[0] <= tol and abs(d[0] - best_distance) < 1e-6]
                if near:
                    ambiguous.append({
                        "anchor": [round(float(v), 6) for v in anchor],
                        "candidates": [entry["name"]] + [table[i]["name"] for _, i in near],
                        "distance_mm": round(best_distance, 4),
                    })
                    continue
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
            # The probe set is deleted here. Its points are scratch objects in a
            # throwaway geometrical set; the solid's topology is untouched, and
            # the edge references above keep working afterwards (measured on B30
            # 2026-11-19 - see the note where they are taken).
            probe.close()

        trouble = self._anchor_trouble_text(missed, ambiguous, tol)
        if trouble and on_miss != "skip":
            # The candidates travel in `data` as well as in the sentence: a caller
            # correcting coordinates by hand reads the sentence, a caller
            # re-matching them (catia_list_edges + a second call) needs the names
            # and the distances as fields. Without them the 2026-11-17 report had
            # to re-list every edge of the body to find out which one the anchor
            # was 3.22 mm from.
            raise ArgumentRejected(
                trouble + ". Nothing was filleted - the whole batch is refused so "
                "that no edge gets a feature nobody asked for. Fix the coordinates "
                "above, raise anchor_tol, or pass on_anchor_miss='skip' to fillet "
                "the anchors that do match and have these reported instead.",
                code="ANCHORS_NOT_MATCHED", stage="validate",
                data={"anchor_tol": tol,
                      "missed": missed,
                      "ambiguous": ambiguous,
                      "matched": report},
                next_actions=[
                    "catia_list_edges(coords=true) lists every edge with its "
                    "midpoint - feed those coordinates back as edge_anchors",
                    "raise anchor_tol if the coordinates come from a coarser "
                    "source; the 'distance_mm' in data.missed says by how much",
                    "on_anchor_miss='skip' fillets the anchors that DO match and "
                    "reports the rest instead of refusing the batch",
                ])
        if trouble:
            report.append("SKIPPED (on_anchor_miss='skip'): " + trouble)
        if not matches:
            raise ArgumentRejected(
                "None of the given edge_anchors matched a distinct edge"
                + (": " + trouble if trouble else ".")
            )
        # NO SECOND ENUMERATION. This used to re-read the whole edge table after
        # the probe set was deleted, on the belief that the references taken
        # earlier died with the selection. Measured on B30 2026-11-19: they do
        # not - a fillet built from a reference taken BEFORE probe.close() went
        # in for 38 ms with the volume moving 16000 -> 15982.8, and
        # topology_cells after the close returns the very same references. That
        # second walk was the expensive one, too: 7 955 ms for 12 edges right
        # after a close against 483 ms cold, because each edge's Length is
        # re-measured. It is most of what made a single-edge chamfer 25-56 s on a
        # 140-face part (2026-11-19 report, P1-4).
        for index in matches:
            entry = table[index]
            labels.append(f"{entry['name']} (by anchor)")
            refs.append(entry["cell"].Reference)
        clash = self._clash_note(table, matches, spa, sweep)
        return (labels, refs,
                "Edge anchors: " + "; ".join(report) + "." + clash, table)

    def _clash_note(self, table: list[dict[str, Any]], matches: list[int],
                    spa: Any, size: float | None,
                    pair_limit: int = 40) -> str:
        """How close the chosen edges pass to each other, in mm.

        THE REPORT ASKED FOR A PRE-CHECK AND THE MEASUREMENT DOWNGRADED IT
        (2026-11-19, P1-3). Its hypothesis was arithmetic: two rounds of size R
        on edges less than 2R apart cannot both exist. That held for its part -
        16 of 20 edges took R3 and the four refusals were the two pairs 0.921 mm
        apart - and it did NOT hold here: on B30 the same day, two edges 0.900 mm
        apart took R3 and both were built (2/2, volume moving as expected). So
        this publishes the DISTANCE and says plainly that it is a flag rather
        than a verdict. A warning that cries wolf on a legal selection is worse
        than the number itself.

        Only pairs that SHARE A FACE are measured - that is where edges run
        alongside each other - and the count is capped, because each distance is
        a COM call. What was not checked is stated as such.
        """
        if not size or len(matches) < 2:
            return ""
        pairs: list[tuple[int, int]] = []
        for first in range(len(matches)):
            for second in range(first + 1, len(matches)):
                pairs.append((matches[first], matches[second]))
        if not pairs:
            return ""
        # FACE-SHARING PAIRS FIRST. Adjacent edges of one wall are the shape the
        # report's tight pairs had, and they are measured before the rest so a
        # capped walk still looks where it is most likely to matter. The 0.9 mm
        # pair measured on B30 shared no face, so the rest are measured too -
        # which is why the cap is higher than the number of face pairs.
        pairs.sort(key=lambda pair: -len(set(table[pair[0]]["bounded_by"])
                                        & set(table[pair[1]]["bounded_by"])))
        checked = 0
        tight: list[tuple[str, str, float]] = []
        for one, other in pairs[:pair_limit]:
            try:
                gap = float(spa.GetMeasurable(
                    table[one]["cell"].Reference).GetMinimumDistance(
                        table[other]["cell"].Reference))
            except Exception:  # noqa: BLE001 - one pair, not the call
                continue
            checked += 1
            if gap < 2.0 * size:
                tight.append((table[one]["name"], table[other]["name"], gap))
        if not tight:
            return (f" Closest pair: the {checked} checked pair(s) of chosen edges "
                    f"are all further apart than 2x{size:g} = "
                    f"{2 * size:g} mm, which is the pattern the 2026-11-19 report "
                    "saw EVERY refusal in - so the size itself is not what would "
                    "refuse one of these.")
        listed = "; ".join(f"{a} and {b} are {gap:.4f} mm apart"
                           for a, b, gap in tight[:6])
        more = "" if len(tight) <= 6 else f"; and {len(tight) - 6} more pair(s)"
        return (f" CLOSE PAIR{'' if len(tight) == 1 else 'S'}: {len(tight)} pair(s) "
                f"of the chosen edges pass closer than 2x{size:g} = "
                f"{2 * size:g} mm - {listed}{more}. That is the situation the "
                "2026-11-19 report lost 4 of 20 edges in (two pairs 0.921 mm "
                "apart), and it is NOT a prediction: measured on B30 the same day, "
                "two edges 0.9 mm apart took R3 and BOTH succeeded. CATIA has the "
                "last word, so read the applied/rejected list rather than this "
                f"number. Checked {checked} of {len(pairs)} pair(s)"
                + ("" if len(pairs) <= pair_limit else
                   " - the rest were not measured, because each costs a COM call")
                + ".")

    def _re_resolve_anchors(self, body: Any, anchors: list[list[float]],
                            tol: float,
                            fallback: tuple[list[str], list[Any]]
                            ) -> tuple[list[str], list[Any]]:
        """Match the anchors again, after a failed batch changed the topology.

        Called by ``_apply_fillets`` only when a batch rebuild failed and its
        features were deleted - so the references resolved before the failure are
        dead, while the ANCHORS are coordinates and mean the same thing as they
        did a second ago. Matching again is the difference between a retry and a
        re-run of the same failure.

        ``on_miss='skip'`` because a retry is not the place to refuse a batch: if
        the topology moved enough that an anchor no longer has an edge, the
        fallback (the pre-failure pair) is what this call had before, and the
        per-edge failures are reported anyway. A refusal here would escape the
        retry loop and leave the caller holding half a body.
        """
        try:
            labels, refs, _report, _table = self._anchors_to_edges(
                body, anchors, tol, on_miss="skip")
        except Exception:  # noqa: BLE001 - the fallback is the old answer
            return fallback
        if not refs:
            return fallback
        return labels, refs

    @staticmethod
    def _anchor_trouble_text(missed: list[dict[str, Any]],
                             ambiguous: list[dict[str, Any]],
                             tol: float) -> str:
        """Every anchor that could not be used, in one sentence."""
        parts: list[str] = []
        if missed:
            parts.append(
                f"{len(missed)} anchor(s) beyond anchor_tol={tol}: " + "; ".join(
                    f"[{', '.join(f'{v:g}' for v in m['anchor'])}] is "
                    f"{m['distance_mm']} mm from the nearest edge "
                    f"({m['nearest_edge']}"
                    + (f", L{m['nearest_length_mm']}" if m["nearest_length_mm"] else "")
                    + ")"
                    for m in missed)
            )
        if ambiguous:
            parts.append(
                f"{len(ambiguous)} ambiguous anchor(s): " + "; ".join(
                    f"[{', '.join(f'{v:g}' for v in a['anchor'])}] is equally close "
                    f"to {', '.join(a['candidates'])}"
                    for a in ambiguous)
            )
        return ". ".join(parts)

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

    def _sketch_open_ends(self, name: str) -> tuple[list[dict[str, Any]], int]:
        """Free ends of one sketch, from the drawing record; ``(ends, segments)``.

        ``([], 0)`` means "nothing was recorded" - a sketch reopened from disk,
        or one filled by something other than this server - which is NOT the same
        as a closed sketch, so the caller must not read it as a clean bill.

        Each end carries the distance to the nearest other end (see
        ``geometry2d.free_ends_with_gaps``): the 2026-11-23 report (P1-2) was
        shown two coordinates that printed the same at four decimals and had no
        way to tell a rounding from a blunder.
        """
        outlines = getattr(self.conn, "sketch_outlines", None) or {}
        try:
            document = str(self.conn.active_document.Name)
        except Exception:  # noqa: BLE001 - no document, no record
            return [], 0
        record = outlines.get(f"{document}::{name}")
        if not record:
            return [], 0
        segments = segments_of(record.get("elements") or [])
        return free_ends_with_gaps(segments), len(segments)

    def _refuse_open_curves(self, sketch: Any, allow: bool) -> None:
        """Refuse a pad/pocket whose sketch holds an open curve, BEFORE CATIA runs.

        The 2026-11-13 report (P0) lost a whole retry loop to this: its sketch held
        one closed region plus the two open splines that divide it into three, and
        the pocket answered NO_MATERIAL - after CATIA had already been asked, and
        in their case with the sketch gone.

        Measured on B30 2026-11-13, four documents, one variable each
        (scripts/b30_v0419_probe_multiregion.py): a circle alone on the top face
        cuts 603.2 mm3, and the SAME circle with an open spline through it, an open
        spline inside it, or an open line through it removes 0 mm3 in all three.
        So the cause is the open curve itself, not the division and not the spline
        - one open curve anywhere makes the whole profile unusable.

        Why refuse instead of letting CATIA answer: the failure costs a rollback and
        a feature node, and it says "removed no material", which points at the
        direction. This says which two points are free and what to do.

        ``allow=True`` is the escape hatch, and it exists on purpose: a rule that
        can only ever say no is worse than CATIA's own answer when the record is
        wrong about the sketch. See the argument's description.

        AND THE THRESHOLD HAS TO MATCH THE RECORD'S PRECISION (2026-11-23 report,
        P1-2). It was 1e-6, finer than the gap an arc leaves when its ends are the
        radial projections CATIA actually drew - so every profile mixing
        `catia_sketch_arc` with `catia_sketch_profile` was refused, and the caller
        had to pass allow_open_curves=true (i.e. disable the guard) to build a
        sketch that was fine. `JUNCTION_TOL` is 1e-3 mm, and the reply prints the
        measured distance, so a 9e-7 gap and a 0.0009 gap no longer read alike.
        """
        if allow:
            return
        name = str(getattr(sketch, "Name", "") or "")
        ends, segments = self._sketch_open_ends(name)
        if not ends:
            return
        listed = ", ".join(
            f"({entry['point'][0]:.4f}, {entry['point'][1]:.4f})"
            + ("" if entry["nearest_mm"] is None
               else f" whose nearest other end is {entry['nearest_mm']:.3e} mm "
                    "away"
                    + (f" ('{entry['nearest_label']}')"
                       if entry["nearest_label"] else ""))
            for entry in ends[:6])
        more = "" if len(ends) <= 6 else f", and {len(ends) - 6} more"
        widest = max((entry["nearest_mm"] or 0.0) for entry in ends)
        raise PreconditionRejected(
            f"'{name}' holds {len(ends)} free endpoint(s) - {listed}{more} - so it "
            "is not a closed contour, and a pad or pocket on it removes NOTHING. "
            "Measured on B30 2026-11-13 with one change per document: a circle "
            "alone cut 603.2 mm3, and the same circle with ONE open spline through "
            "it, one open spline inside it, or one open line through it removed "
            "0 mm3 in all three. The open curve is the cause - it does not have to "
            "divide anything, and it does not have to be a spline. "
            f"A gap of {JUNCTION_TOL:g} mm or less counts as JOINED here, so these "
            f"{len(ends)} are wider than that (widest {widest:.3e} mm). Under "
            "1e-4 mm the gap is the rounding of the coordinates the sketch was "
            "drawn from, and nothing needs doing; from 0.01 mm up it is a real gap "
            "and the two ends have to meet. "
            f"Nothing was built and nothing was rolled back ({segments} segment(s) "
            "checked, from the coordinates this session gave the sketch tools).",
            code="SKETCH_HAS_OPEN_CURVES", stage="validate", modified=False,
            data={"sketch": name, "free_ends": len(ends),
                  "widest_gap_mm": widest, "joined_below_mm": JUNCTION_TOL,
                  "ends": ends[:20]},
            next_actions=[
                "Put the open curve(s) in their OWN sketch: they are boundaries or "
                "guides, and pad/pocket never needed them. Draw the closed region "
                "alone, close it, and cut that",
                "Or give each closed region its own sketch - the 2026-11-13 "
                "report's own workaround: three separate pockets gave 1164.92 mm3 "
                "against a 1142.849 mm3 source (+1.93 %)",
                "If the open curve is a construction element, draw it with "
                "construction=true: construction geometry is never part of the "
                "profile and is not counted here",
                "Only if you believe this sketch really is closed, pass "
                "allow_open_curves=true to send it to CATIA anyway",
            ])

    @staticmethod
    def _pad_extents(args: dict[str, Any]
                     ) -> tuple[bool, float, float, float | None, str, bool]:
        """Validate the extent arguments BEFORE CATIA is touched.

        Two reasons this is separate. A caller that asked for both forms at once
        gets the answer without a document being opened ("a writer rejected on
        its arguments is not told to inspect the model" - test_route_matrix), and
        the two-sided form is the one that has to be checked for arithmetic a pad
        cannot express (a zero extent, a negative length).

        Returns ``(two_sided, first_side, second_side, height, direction,
        symmetric)``.
        """
        height = args.get("height")
        limit1 = args.get("limit1")
        limit2 = args.get("limit2")
        direction = args.get("direction", "normal")
        symmetric = bool(args.get("symmetric", False)) or direction == "both"

        # TWO EXTENTS ON ONE FEATURE (2026-11-19 report, P1-1). `height` is the
        # one-sided form; limit1/limit2 say "this far one way, that far the
        # other". Accepting both forms at once would be two ways to say the same
        # thing with different answers, so the combination is refused.
        two_sided = limit1 is not None or limit2 is not None
        if not two_sided:
            if height is None:
                raise ArgumentRejected(
                    "catia_pad needs an extent: 'height' for one side, or "
                    "'limit1'/'limit2' for two different ones. Nothing was "
                    "created.", code="BAD_ARGUMENT", stage="validate",
                    modified=False)
            return False, 0.0, 0.0, float(height), direction, symmetric
        if height is not None:
            raise ArgumentRejected(
                "'height' is the ONE-sided form and 'limit1'/'limit2' are the "
                "two-sided form - pass one or the other. height=H means the "
                "pad runs H on one side; limit1=A, limit2=B means it runs A "
                "toward the normal and B the other way, in ONE feature. "
                "Nothing was created.",
                code="BAD_ARGUMENT", stage="validate", modified=False)
        if symmetric:
            raise ArgumentRejected(
                "symmetric=true already means 'the same extent both ways'; "
                "with limit1/limit2 the two extents are given explicitly, so "
                "one of the two is redundant. Pass limit1=limit2=H/2 for a "
                "symmetric pad, or drop symmetric. Nothing was created.",
                code="BAD_ARGUMENT", stage="validate", modified=False)
        try:
            first_side = float(limit1 if limit1 is not None else 0.0)
            second_side = float(limit2 if limit2 is not None else 0.0)
        except (TypeError, ValueError) as error:
            raise ArgumentRejected(
                f"limit1={limit1!r} / limit2={limit2!r} are not numbers; both "
                "are lengths in mm. Nothing was created.",
                code="BAD_ARGUMENT", stage="validate") from error
        if first_side < 0 or second_side < 0:
            raise ArgumentRejected(
                f"limit1={first_side:g} / limit2={second_side:g}: both are "
                "LENGTHS, so neither can be negative - use 'direction' to say "
                "which side limit1 counts on. Nothing was created.",
                code="BAD_ARGUMENT", stage="validate", modified=False)
        if first_side <= 0 and second_side <= 0:
            raise ArgumentRejected(
                "limit1 and limit2 are both 0, so the pad would have no "
                "extent. Nothing was created.",
                code="BAD_ARGUMENT", stage="validate", modified=False)
        if first_side <= 0:
            # Only limit2 was given: put the material on the first limit and flip
            # the pair, because a Sweep's first limit is the one CATIA builds
            # from the profile plane.
            first_side, second_side = second_side, 0.0
            direction = "reverse"
        # NOTE: 'reverse' does NOT swap the two magnitudes. Measured on B30
        # 2026-11-19: limit1=4, limit2=1 with DirectionOrientation=1 gave
        # z -4.000..+1.000 - the orientation flips the PAIR about the sketch
        # plane, so limit1 keeps its magnitude and changes side. Swapping first
        # and then flipping (which this did in its first version) cancels out and
        # hands back the request the caller did not make: z -1..+4.
        return True, first_side, second_side, None, direction, symmetric

    def _pad(self, args: dict[str, Any]) -> str:
        two_sided, first_side, second_side, height, direction, symmetric = \
            self._pad_extents(args)
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        sketch = self._get_last_sketch(args.get("sketch_name"))
        # The name the CALLER used, which after a rebuild is not the sketch's
        # name in the tree (草图.2 resolving to 草图.3). The failure below has to
        # answer in the caller's vocabulary.
        requested_name = args.get("sketch_name")
        # Checked before the factory is touched: an open curve makes the pad
        # remove nothing, and CATIA's own sentence about that points at the
        # direction instead of at the sketch.
        self._refuse_open_curves(sketch, bool(args.get("allow_open_curves", False)))

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

        if two_sided:
            pad = sf.AddNewPad(sketch, first_side)
            if second_side > 0:
                # Limit 2 is the same Prism feature's other extent - measured on
                # B30 2026-11-19: 20x10 profile, limit1=5, limit2=3 answered
                # z -3.000..+5.000 and 1600 mm3 (200 x 8), i.e. limit2 sweeps
                # AGAINST the first limit, which is the SolidWorks "blind 57.5
                # one way, 4.5 the other" shape in one feature.
                pad.SecondLimit.Dimension.Value = second_side
            if direction == "reverse":
                pad.DirectionOrientation = 1
        else:
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

        # The same node, flipped, instead of a second throwaway pad. Measured
        # 2026-10-26: the throwaway probe was built from a sketch the FAILED pad
        # had already consumed, so it answered "nothing either way" for a
        # direction that works - the caller was told the direction was not the
        # problem while `direction='reverse'` added 4 000 mm3 on the same sketch.
        # Removing that probe also took the caller's sketch with it, so the retry
        # the message discouraged could not even run.
        flipped = False
        # NOT FLIPPED when the two extents were given: 'limit1 one way, limit2
        # the other' is an explicit statement about both sides, and swapping them
        # would silently hand back the mirror image of what was asked for.
        if took is False and not symmetric and not two_sided:
            pad.DirectionOrientation = 0 if direction == "reverse" else 1
            flip_error = self._try_update(part, pad)
            after, retook = self._settle(part, body, before, expect="add")
            if retook:
                flipped = True
                took = True
                update_error = flip_error or update_error
                direction = "normal" if direction == "reverse" else "reverse"
            else:
                # Air both ways. Put the requested orientation back so the tree
                # would say what was asked for, then roll the node back below.
                pad.DirectionOrientation = 1 if direction == "reverse" else 0
                self._try_update(part, pad)
                after = self._body_volume_mm3(part, body)
                took = False

        if took is False:
            # Nothing grew either way: drop the dead node so the document stays
            # usable. This is the case that used to poison the whole part - a pad
            # on an open profile left '凸台.N' behind and every later update
            # failed. Only the pad is removed ... and CATIA takes the sketch with
            # it, because the sketch is consumed by the feature: measured on B30,
            # 5 of 5 such rollbacks left an empty body, and the message claimed
            # the opposite (reported 2026-10-27). The sketch is read back after
            # the delete and the sentence follows what is actually there.
            sketch_name = getattr(sketch, "Name", None)
            # _remove_feature also puts the body back in work: deleting a feature
            # leaves InWorkObject on whatever CATIA picks - on B32 it was the
            # sketch's datum plane (reported 2026-10-26), and the very next Part
            # Design call then fails with a bare "method failed" that reads like a
            # bad sketch.
            gone = self._remove_feature(part, pad, body)
            survived = self._sketch_present(part, sketch)
            stayed = (f"Volume stayed {before:.1f} mm3." if before is not None
                      else f"The volume could not be measured ({volume_note}).")
            recovery, sketch_usable = self._recover_lost_sketch(
                part, body, sketch_name, survived, requested=requested_name)
            raise FeatureRejected(
                "CATIA built no material for this pad in EITHER direction, so it "
                "was rolled back"
                + ("" if gone else " (the dead feature could NOT be deleted - "
                                   "close the document without saving)")
                + "."
                + f" {stayed} Both directions were MEASURED, not assumed: the same "
                "pad node was flipped and re-updated, and neither orientation grew "
                "the body."
                + recovery
                + " Four things do this:\n"
                "  · the sketch plane sits ON the material boundary and the sweep "
                "runs INTO the body, so the whole sweep is absorbed - move the "
                "sketch's offset so the sweep starts outside the material, or "
                "extend the profile through it;\n"
                "  · the profile lies entirely inside existing material, with air "
                "on neither side;\n"
                "  · the profile is open (a 0.0035 mm gap is enough) - "
                "catia_close_sketch reports every endpoint and gap it can see;\n"
                "  · the outline self-intersects, or is several overlapping closed "
                "loops (catia_sketch_rectangle already draws a complete rectangle, "
                "so calling it twice draws two).\n"
                "Retrying the SAME call changes none of these; changing the offset "
                "or the profile does."
                + (f" Original error: {update_error}" if update_error else ""),
                rolled_back=gone,
                # The generic "fix the argument and retry" is wrong here: the
                # retry IS the call that failed. Say what the four causes above
                # imply (reported 2026-10-28, where the envelope contradicted the
                # message this very reply already contained).
                next_actions=(
                    ["Draw the sketch again before retrying: CATIA took it with the "
                     "rollback and it could not be rebuilt from its record - its "
                     "old definition is echoed in the message above"]
                    if not sketch_usable else
                    ["Change the sketch's offset or profile, then retry: both "
                     "directions were measured and neither grew the body, so the "
                     "same call builds nothing again"
                     + ("; the sketch was rebuilt, so the name still resolves"
                        if sketch_name and not survived else "")]
                ),
            )
        self.conn.refresh_display()
        if two_sided:
            span = (f"{first_side:.3f} mm one way + {second_side:.3f} mm the "
                    f"other (total {first_side + second_side:.3f} mm)")
        else:
            span = ("total %.3f mm, %0.3f each side" % (height, height / 2.0)
                    if symmetric else "%.3f mm" % height)
        if took is None:
            # No volume, no verdict - same rule as pocket and hole. And when CATIA
            # also raised, "no volume" plus "update failed" is not an ok:true.
            return self._unverified(
                "pad", str(pad.Name), f"{span} ({direction})",
                volume_note or "the volume came back empty", update_error,
                ["the profile is the first thing to check: catia_close_sketch "
                 "reports an open outline or a self-crossing one",
                 "a pad on a profile that crosses itself builds a broken solid "
                 "without failing - that is what makes this route look healthy "
                 "until the next feature"])
        note = ""
        if before is not None and after is not None:
            gain = after - before
            note = (f" Volume {before:.1f} -> {after:.1f} mm3 "
                    f"(delta {gain:+.1f}).")
        if update_error is not None:
            note += (" (CATIA raised during the update, but the solid did grow "
                     "and the feature is kept.)")
        if flipped:
            note += (" (direction auto-flipped to reach material: the requested "
                     "side was empty, and the same feature keeps the material it "
                     "was asked for on the other side)")
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

    def _unverified(self, kind: str, feature_name: str, created: str,
                    why: str, update_error: Any,
                    next_actions: list[str]) -> str:
        """The "no volume, no verdict" sentence - or a refusal when CATIA said so.

        A reply of ``ok: true`` that also contains "UpdateObject failed" is worse
        than a failure: the 2026-11-13 report's pad answered exactly that, run_plan
        carried on for ten steps, and the first symptom showed up at
        ``catia_fillet`` as "the active body has no edges to fillet yet" - a
        sentence about the wrong feature. The root cause was one step earlier.

        So the two situations are kept apart:

        * CATIA did not raise, only the volume was unreadable -> ``UNVERIFIED``
          (the feature may well be right; saying "failed" would be a guess).
        * CATIA raised AND the volume is unreadable -> nothing supports the claim
          that material exists, so this refuses. The node stays in the tree (it
          may be the only record of what was asked for), the part is marked as in
          error for ``catia_list_bodies``, and the caller is told not to build on
          it.
        """
        if update_error is None:
            return (f"UNVERIFIED: {kind} '{feature_name}' was created: {created}. "
                    f"It could not be checked against the body volume ({why}), so "
                    "it is reported on CATIA's return alone - check the geometry "
                    "before relying on it.")
        self._note_part_error(str(update_error))
        raise FeatureRejected(
            f"CATIA failed to update the part after building this {kind} "
            f"('{feature_name}', {created}): {update_error}. The node is in the "
            f"tree but the solid was not rebuilt, and the body volume could not be "
            f"measured ({why}) - so nothing here shows that material was added or "
            "removed. Every later feature that depends on this one inherits the "
            "error, and CATIA keeps answering with the last good volume, so the "
            "next few calls can look fine. Do not build on this reply.",
            next_actions=next_actions + [
                "catia_update_part names every element that fails to update; run "
                "it now, before the next feature - it points at THIS node",
                "catia_close_sketch on the profile: an open or self-crossing "
                "outline is the usual reason a pad or pocket will not rebuild",
                "catia_delete_feature on the node if the profile is wrong, then "
                "draw the profile again - a kept node in error makes every later "
                "update fail as well",
            ], rolled_back=False, code="UPDATE_FAILED")

    def _note_part_error(self, message: str | None) -> None:
        """Remember whether the part is known to be in an update-error state.

        ``catia_list_bodies`` reads it: CATIA keeps answering with the last valid
        volume and face count while the tree is broken (2026-11-10: 168607.5 mm3
        reported twice on a part whose update had already failed).
        """
        try:
            self.conn.part_in_error = message
        except Exception:  # noqa: BLE001 - a double without the attribute
            pass

    def _body_volume_mm3(self, part: Any, body: Any) -> float | None:
        """Measure the body's solid volume in mm3, or None if unavailable."""
        return self._volume_probe(part, body)[0]

    def _body_gap_mm(self, part: Any, one: Any, other: Any) -> float | None:
        """Closest distance between two bodies in mm, or None if unmeasurable.

        Already in mm: GetCOG and GetMinimumDistance come back in the document's
        length unit while Volume and Area are MKS (field-verified 2026-10-13).
        None is not "touching" - the caller has to keep the two apart, because a
        distance that could not be read must not refuse a legitimate cut.
        """
        try:
            doc = part.Parent
            try:
                spa = doc.GetWorkbench("SPAWorkbench")
            except AttributeError:
                spa = self.conn.app.GetWorkbench("SPAWorkbench")
            return float(spa.GetMeasurable(
                part.CreateReferenceFromObject(one)).GetMinimumDistance(
                    part.CreateReferenceFromObject(other)))
        except Exception:  # noqa: BLE001 - unmeasurable is a valid answer
            return None

    @staticmethod
    def _body_is_empty(body: Any) -> bool:
        """True when the body has no solid yet - so its volume is 0, not unknown."""
        try:
            return body.Shapes.Count == 0
        except Exception:  # noqa: BLE001 - unresolved counts as "not known to be empty"
            return False

    def _pocket(self, args: dict[str, Any]) -> str:
        if args.get("limit1") is not None or args.get("limit2") is not None:
            return self._pocket_two_sided(args)
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        sketch = self._get_last_sketch(args.get("sketch_name"))
        requested_name = args.get("sketch_name")
        self._refuse_open_curves(sketch, bool(args.get("allow_open_curves", False)))
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
        if took is False and not symmetric and not reverse:
            # Nothing was removed on the side that was asked for. `direction:
            # "normal"` names the DEFAULT side, so it is not a pin and this
            # flips - that is what the reporter needed: measured 2026-11-07, a
            # frame sketch whose material sat on the +Y side of a plane with
            # normal -Y answered NO_MATERIAL for "normal" and cut for "reverse",
            # so the caller had to find the flip by hand. `reverse` stays
            # literal ("reverse means reverse", 0.3.5) - asking for the air side
            # is an error, and the next_actions below name the way back.
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
            gone = self._remove_feature(part, pocket, body)
            sketch_name = getattr(sketch, "Name", None)
            survived = self._sketch_present(part, sketch)
            side = ("the requested direction" if explicit_direction
                    else "either direction")
            recovery, sketch_usable = self._recover_lost_sketch(
                part, body, sketch_name, survived, requested=requested_name)
            raise FeatureRejected(
                f"The pocket removed no material in {side}, so it was "
                + ("rolled back" if gone else
                   "left in the tree (the dead feature could NOT be deleted - "
                   "close the document without saving)")
                + f" (volume stayed {before:.1f} mm3)."
                + recovery
                + " Two usual causes: the "
                "profile does not intersect the solid (a face sketch whose "
                "local coordinates do not land on the material - put the sketch "
                "on a datum plane with an offset instead), or the profile is "
                "not closed"
                # `through_all` is a different animal: CATIA works the extent
                # out itself ("up to last"), and on a sketch whose support is a
                # datum or frame plane - rather than a face of the solid - that
                # extent can come back empty while the same profile cuts fine
                # with an explicit depth (reported 2026-11-07, P4: the reporter's
                # three other through-cuts all worked once they used depth).
                # This package cannot reproduce it yet, so it says what was
                # measured and names the way out instead of blaming the profile.
                + (" A `through_all` cut lets CATIA work out its own extent, and "
                   "that is the part that can come back empty when the sketch "
                   "sits on a datum/frame plane rather than on a face of the "
                   "solid: retry the SAME profile with an explicit depth "
                   "(direction='both', depth well past the body) before "
                   "changing any geometry - that is measured to work on the "
                   "2026-11-07 case." if through_all else "")
                + (f". Original error: {update_error}" if update_error else "."),
                rolled_back=gone,
                next_actions=(
                    # A retry is only worth suggesting while there is a sketch to
                    # retry with. The report's M3 hit exactly this: the message
                    # said the sketch could not be rebuilt and the envelope still
                    # said "retry once with direction='normal'".
                    ["Draw the sketch again before retrying: CATIA took it with the "
                     "rollback and it could not be rebuilt from its record - its "
                     "old definition is echoed above"]
                    if not sketch_usable else
                    ["Retry the same profile with an explicit depth instead of "
                     "through_all - catia_pocket(depth=20000, direction='both') - "
                     "because a through_all extent can come back empty on a "
                     "datum/frame sketch while the profile itself is fine"]
                    if through_all else
                    (["Retry once with direction='normal': the profile may sit "
                      "with all its material on the other side of the sketch "
                      "plane. Measured 2026-11-07 on a frame sketch - 'reverse' "
                      "removed nothing while 'normal' cut, on the same profile"]
                     if reverse else
                     ["Change the profile or the sketch's offset so the cut meets "
                      "material, then retry: the same call removes nothing again"])
                ),
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
                self._remove_feature(part, mirror, body)
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
            flip_note = (" (the requested side had no material, so the cut was "
                         "auto-flipped - catia_pad has always behaved this way)"
                         if flipped else "")
            if single_sided:
                flip_note = (" (only one side of the sketch plane had material, so "
                             "this is a single-sided cut despite symmetric=true)")
            if update_error is not None:
                flip_note += (" (CATIA raised during the update, but the cut is "
                              "in the geometry - kept)")
            extent = self._pocket_extent(through_all, depth, symmetric)
            names = f"'{pocket.Name}'" if mirror is None else \
                f"'{pocket.Name}' + '{mirror.Name}' (two one-sided cuts)"
            # The second cost of a mid-plane cut, said where the second feature
            # appears (2026-11-21 report P1-13). It is not a guess: `mirror` only
            # exists when the OTHER side also removed material, which means the
            # sketch plane is inside the solid - and then every face the profile
            # crosses has a seam at that plane. The reported part is the evidence:
            # 37 faces against the source's 33, the extra ones arriving in pairs.
            topology_note = (
                "" if mirror is None
                else self._midplane_topology_note(mirror))
            return (
                f"Pocket {names} created: {extent}{flip_note}. "
                f"Removed {removed:.1f} mm3 ({before:.1f} -> {after:.1f})."
                + topology_note
                + self._pocket_area_note(sketch, depth, through_all, symmetric,
                                         single_sided)
            )
        # No volume, no verdict: say which measurement failed rather than
        # blaming "this installation" for a cut the same session may verify on
        # the next call. A raised update next to an unreadable volume is a
        # refusal, not an ok:true - see _unverified.
        extent = self._pocket_extent(through_all, depth, symmetric)
        why = volume_note or "the volume came back empty"
        return self._unverified(
            "pocket", str(pocket.Name), extent, why, update_error,
            ["catia_close_sketch on the profile: an open outline or one that "
             "crosses itself is the usual reason a cut will not rebuild",
             "a through_all cut whose sketch sits on a datum plane can come back "
             "empty while the same profile cuts with an explicit depth"])

    @staticmethod
    def _midplane_topology_note(mirror: Any) -> str:
        """What the second one-sided cut can cost in FACES - measured both ways.

        The 2026-11-21 report (P1-13) counted faces as its acceptance criterion
        and got 37 where the source had 33, all of the excess arriving as
        duplicate pairs at the sketch plane. That is the report's number. What
        this build does NOT do is split on every mid-plane cut: the same window
        cut through from the mid-plane of a 100x60x20 block answered 10 faces on
        B30 2026-11-21, exactly like the one-sided cut from the top. Both
        measurements are here because a note that promises a split would be as
        wrong as one that denies it.
        """
        if mirror is None:
            return ""
        return (
            " Two one-sided cuts is what a mid-plane cut has to be on this build "
            "(Pocket + IsSymmetric is ignored), and the pair CAN cost faces: the "
            "2026-11-21 report's P01 skirt window came back with 37 faces against "
            "the source's 33, the extras arriving as duplicate pairs (28.901 and "
            "1191.621 mm2). A controlled through-cut from the mid-plane of a "
            "100x60x20 block did NOT split anything when measured here - 10 faces, "
            "the same as the one-sided cut - so the split depends on the geometry "
            "rather than on 'both' alone. If a face count is your yardstick, cut "
            "one side from an offset plane instead.")

    def _pocket_two_sided(self, args: dict[str, Any]) -> str:
        """A pocket with two different depths - one feature, two limits.

        `depth`/`through_all` and the limit pair are different forms of the same
        request, so mixing them is refused here, before CATIA is touched. The
        report that asked for this (2026-11-19, P1-1) had to build two pads for a
        SolidWorks asymmetric extrude, and the SAME report then measured the
        split's cost at zero faces - 72/191/120 both ways, because CATIA merges
        the coplanar walls. What one feature buys is a shorter tree and the
        volume, not fewer faces.
        """
        if "depth" in args or args.get("through_all"):
            raise ArgumentRejected(
                "'limit1'/'limit2' are the two-sided form and 'depth'/'through_all' "
                "are the one-sided ones - pass one form or the other. Nothing was "
                "created.", code="BAD_ARGUMENT", stage="validate", modified=False)
        try:
            first_side = float(args.get("limit1") or 0.0)
            second_side = float(args.get("limit2") or 0.0)
        except (TypeError, ValueError) as error:
            raise ArgumentRejected(
                f"limit1={args.get('limit1')!r} / limit2={args.get('limit2')!r} are "
                "not numbers; both are lengths in mm. Nothing was created.",
                code="BAD_ARGUMENT", stage="validate") from error
        if first_side < 0 or second_side < 0:
            raise ArgumentRejected(
                f"limit1={first_side:g} / limit2={second_side:g}: both are LENGTHS, "
                "so neither can be negative - use 'direction' to say which side "
                "limit1 counts on. Nothing was created.",
                code="BAD_ARGUMENT", stage="validate", modified=False)
        if first_side <= 0 and second_side <= 0:
            raise ArgumentRejected(
                "limit1 and limit2 are both 0, so the cut would remove nothing. "
                "Nothing was created.",
                code="BAD_ARGUMENT", stage="validate", modified=False)
        if args.get("symmetric") or args.get("direction") == "both":
            raise ArgumentRejected(
                "'symmetric'/'direction=\"both\"' already mean 'the same depth both "
                "ways'; with limit1/limit2 the two depths are given explicitly. "
                "Nothing was created.",
                code="BAD_ARGUMENT", stage="validate", modified=False)

        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory
        sketch = self._get_last_sketch(args.get("sketch_name"))
        self._refuse_open_curves(sketch, bool(args.get("allow_open_curves", False)))
        reverse = args.get("direction") == "reverse"
        if reverse or first_side <= 0:
            first_side, second_side = second_side, first_side
            reverse = True

        before, volume_note = self._volume_probe(part, body)
        pocket = sf.AddNewPocket(sketch, first_side)
        if second_side > 0:
            try:
                pocket.SecondLimit.Dimension.Value = second_side
            except Exception as error:  # noqa: BLE001 - reported, not hidden
                self._remove_feature(part, pocket, body)
                raise CodedError(
                    "CATIA refused to give this Pocket a second limit "
                    f"({str(error).splitlines()[0][:120]}), so the two-sided cut "
                    "would have been silently one-sided. The feature was removed "
                    "again. Build it as two one-sided pockets instead and expect "
                    "the face the sketch plane leaves behind.",
                    code="POCKET_SECOND_LIMIT_UNSUPPORTED", stage="execute",
                    modified=False) from error
        pocket.DirectionOrientation = 1 if reverse else 0
        self._try_update(part, pocket)
        after, took = self._settle(part, body, before, expect="subtract")
        if took is False:
            gone = self._remove_feature(part, pocket, body)
            stayed = (f"The volume stayed {before:.1f} mm3."
                      if before is not None else
                      f"The volume could not be measured ({volume_note}).")
            raise FeatureRejected(
                f"The two-sided pocket removed no material ({stayed}) and was "
                f"rolled back{'' if gone else ' (it could not be removed)'}. Check "
                "that the profile is closed and that the cut passes through "
                "material on at least one of the two sides.",
                rolled_back=gone)
        self.conn.refresh_display()
        removed = (f"{before - after:.1f} mm3" if before is not None
                   and after is not None else "an unmeasured amount")
        return (f"Pocket created: {first_side:.3f} mm one way + "
                f"{second_side:.3f} mm the other (total "
                f"{first_side + second_side:.3f} mm), removed {removed}. Feature: "
                f"'{pocket.Name}'")

    def _pocket_area_note(self, sketch: Any, depth: float | None,
                          through_all: bool, symmetric: bool,
                          single_sided: bool) -> str:
        """The arithmetic a caller can check a cut against, when it is exact.

        A pocket's removed volume CANNOT be predicted from depth alone: depth is
        how far the cutter travels, not how thick the material is, so a 400 mm cut
        through a 40 mm wall removes a tenth of profile x 400 (measured on B30
        2026-11-08: profile 30 mm2, depth 400, one 40 mm wall -> 1200 mm3, not
        12000). What can be stated without guessing is the BOUND, and it is worth
        publishing because "the volume looks plausible while the shape is wrong"
        is a class only arithmetic catches (reported 2026-11-08, M7 - where the
        reporter's own 6000 mm3 estimate was this same product done the other way
        round).

        Silent unless the profile is ONE rectangle or ONE circle: with several
        loops, or an open outline, there is no single area and a number here
        would be invention.
        """
        try:
            name = str(sketch.Name)
            document = str(self.conn.active_document.Name)
        except Exception:  # noqa: BLE001 - a note, not a measurement
            return ""
        record = (getattr(self.conn, "sketch_outlines", None) or {}).get(
            f"{document}::{name}") or {}
        elements = record.get("elements") or []
        if len(elements) != 1:
            return ""
        element = elements[0]
        kind = str(element.get("kind"))
        if kind == "rectangle":
            width = abs(float(element.get("x2", 0.0)) - float(element.get("x1", 0.0)))
            height = abs(float(element.get("y2", 0.0)) - float(element.get("y1", 0.0)))
            area = width * height
            shape = f"{width:g} x {height:g}"
        elif kind == "circle":
            radius = float(element.get("radius", 0.0) or 0.0)
            area = math.pi * radius * radius
            shape = f"circle D{2 * radius:g}"
        else:
            return ""
        if area <= 0:
            return ""
        if through_all or not depth:
            return (f" Profile: {shape} = {area:.3f} mm2. The cut goes through, so "
                    "it has no fixed bound - a wall thinner than the body removes "
                    "less than profile x any distance.")
        travel = float(depth) * (2 if (symmetric and not single_sided) else 1)
        return (f" Profile: {shape} = {area:.3f} mm2, cutter travel {travel:g} mm, "
                f"so at most {area * travel:.1f} mm3 could be removed - a wall "
                "thinner than the travel removes less.")

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
            raise PreconditionRejected(
                "This sketch exposes no absolute axis, so it cannot be revolved. "
                "Re-create the sketch and try again."
            ) from exc

    def _revolve_axis_reference(self, part: Any, sketch: Any, mode: str) -> Any:
        """Resolve the revolution axis to the Reference CATIA wants.

        'horizontal' / 'vertical' are the sketch's own absolute axes. ANYTHING
        ELSE IS AN ELEMENT NAME - a gsd_line, an edge, the axis of an earlier
        feature - and that works, measured on B30 2026-11-14. The earlier text
        here said only the sketch's own axes work, quoting a construction line
        INSIDE the sketch being refused at update. That measurement was about a
        sketch child; an element in the tree is a different thing, and it was
        never tried until a report asked for a shaft about an arbitrary axis.

        The measurement, one fresh document per cell, profile = rectangle
        u 5..15 / v 0..30 on 'zx' (area 300, centroid 15 from H and 10 from V):

            sketch horizontal                  28274.33 mm3   (2*pi*15*300)
            sketch vertical                    18849.56 mm3   (2*pi*10*300)
            gsd_line along Z through origin    28274.33 mm3   (H IS Z here)
            gsd_line along X through origin    18849.56 mm3   (V IS X here)
            gsd_line along Z at x = 60         84823.00 mm3   (2*pi*45*300)

        The last one is the cell that proves the line is honoured rather than
        ignored: no sketch axis can produce 45 mm of centroid radius.
        """
        text = str(mode or "horizontal").strip()
        lowered = text.lower()
        axis = self._sketch_absolute_axis(sketch)
        if lowered == "horizontal":
            return axis.HorizontalReference
        if lowered == "vertical":
            return axis.VerticalReference
        # An element name. Resolved through the GSD resolver because that is the
        # one that reaches into geometrical sets and understands 'Face.N' /
        # 'Edge.N'; PartDesign has no equivalent and duplicating it here would be
        # a second copy of the topology rules.
        from catia_mcp.tools.gsd import GSDTools

        try:
            return GSDTools(self.conn)._ref(text)
        except Exception as error:  # noqa: BLE001 - reworded as an axis problem
            raise ArgumentRejected(
                f"axis must be 'horizontal', 'vertical', or the name of an "
                f"element to revolve about; '{text}' is not one this part can "
                f"find ({str(error).splitlines()[0][:100]}). Nothing was "
                "created.", code="BAD_AXIS", stage="validate") from error

    @staticmethod
    def _axis_label(axis_mode: str, sketch: Any) -> str:
        """What the reply calls the axis - and it must match what was used."""
        text = str(axis_mode or "horizontal").strip()
        if text.lower() in ("horizontal", "vertical"):
            return f"the {text.lower()} axis of '{sketch.Name}'"
        return f"'{text}' (an element of the tree, not a sketch axis)"

    @staticmethod
    def _revolution_angles(args: dict[str, Any]) -> tuple[float, float]:
        """``(first, second)`` sweep angles from `angle` or `angle1`/`angle2`.

        CATIA's Shaft/Groove have Angle 1 and Angle 2, and the 2026-11-19 report
        is right that a segment symmetric about the profile plane needs both.
        Measured on B30 2026-11-19: angle1=30 + angle2=30 swept 1570.7963 mm3 -
        exactly the single 60 - while the extent moved, so Angle 2 is the OTHER
        side and not a duplicate.
        """
        angle1 = args.get("angle1")
        angle2 = args.get("angle2")
        if angle1 is None:
            if angle2:
                raise ArgumentRejected(
                    f"angle2={angle2} sweeps the other side of the profile plane "
                    "and only means something with angle1. Pass angle1 as well "
                    "(the pair is CATIA's Angle 1 / Angle 2), or drop angle2 and "
                    "use 'angle' for a one-sided sweep. Nothing was created.",
                    code="BAD_ARGUMENT", stage="validate", modified=False)
            return float(args.get("angle", 360)), 0.0
        if "angle" in args:
            # NOT a blanket refusal: `angle` carries a schema default of 360, and
            # an MCP client that fills defaults in would then make angle1
            # unusable. A value that differs from that default is a real
            # conflict; 360 is what the caller would get by saying nothing.
            try:
                one_sided = float(args["angle"])
            except (TypeError, ValueError):
                one_sided = 360.0
            if one_sided != 360.0:
                raise ArgumentRejected(
                    "'angle' is the one-sided form; 'angle1'/'angle2' give both "
                    f"sides explicitly - got angle={args['angle']} with "
                    f"angle1={angle1}. Pass one form or the other. Nothing was "
                    "created.", code="BAD_ARGUMENT", stage="validate",
                    modified=False)
        try:
            first, second = float(angle1), float(angle2 or 0.0)
        except (TypeError, ValueError) as error:
            raise ArgumentRejected(
                f"angle1={angle1!r} / angle2={angle2!r} are not numbers; both are "
                "degrees. Nothing was created.",
                code="BAD_ARGUMENT", stage="validate") from error
        for value in (first, second):
            if value < 0 or value > 360:
                raise ArgumentRejected(
                    f"angle1={first:g} / angle2={second:g}: each sweep is 0 to 360 "
                    "degrees, and each is measured from the profile plane toward "
                    "its own side. Nothing was created.",
                    code="BAD_ARGUMENT", stage="validate", modified=False)
        if first + second <= 0:
            raise ArgumentRejected(
                "angle1 and angle2 are both 0, so the revolution would have no "
                "extent. Nothing was created.",
                code="BAD_ARGUMENT", stage="validate", modified=False)
        return first, second

    def _build_revolution(self, part: Any, sketch: Any, angle: float, axis_mode: str,
                         from_ref: str, label: str,
                         second_angle: float = 0.0) -> Any:
        """Create a Shaft/Groove through the ...FromRef constructor.

        AddNewShaft(sketch) looks like the obvious call and always fails at
        UpdateObject: CATIA wants the profile and the axis set explicitly on a
        feature created empty, and FirstAngle is an object with a .Value, not a
        number. Verified against V5-6R2020 B30; see FORK.md.

        ``second_angle`` is Angle 2 - the sweep on the other side of the profile
        plane (2026-11-19 report, P1-2). Measured on B30 2026-11-19 on the same
        profile: 60/0 and 30/30 both gave 1570.7963 mm3, but the axial extent ran
        5.000..20.000 for the first and 8.660..17.320 for the second, which is
        what "symmetric about the profile plane" has to look like.
        """
        factory = part.ShapeFactory
        feature = getattr(factory, from_ref)(part.CreateReferenceFromName(""))
        feature.FirstAngle.Value = angle
        if second_angle:
            feature.SecondAngle.Value = second_angle
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
        angle, second = self._revolution_angles(args)
        axis_mode = args.get("axis", "horizontal")

        before = self._body_volume_mm3(part, self.conn.get_active_part_body())
        shaft = self._build_revolution(part, sketch, angle, axis_mode, "AddNewShaftFromRef", "Shaft",
                                       second_angle=second)
        after = self._body_volume_mm3(part, self.conn.get_active_part_body())

        detail = ""
        if before is not None and after is not None:
            detail = f" Volume {before:.1f} -> {after:.1f} mm3 (added {after - before:.1f})."
        swept = (f"{angle}deg on one side of the profile plane"
                 if not second else
                 f"{angle}deg + {second}deg the other side "
                 f"(total {angle + second}deg, centred on the profile plane)")
        return (
            f"Shaft created: {swept} about "
            f"{self._axis_label(axis_mode, sketch)}.{detail} "
            f"Feature: '{shaft.Name}'"
        )

    def _groove(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        self._prepare_body(part)
        sketch = self._get_last_sketch(args.get("sketch_name"))
        angle, second = self._revolution_angles(args)
        axis_mode = args.get("axis", "horizontal")

        before = self._body_volume_mm3(part, self.conn.get_active_part_body())
        groove = self._build_revolution(part, sketch, angle, axis_mode, "AddNewGrooveFromRef", "Groove",
                                        second_angle=second)
        after = self._body_volume_mm3(part, self.conn.get_active_part_body())

        detail = ""
        if before is not None and after is not None:
            removed = before - after
            detail = f" Removed {removed:.1f} mm3 ({before:.1f} -> {after:.1f})."
            if abs(removed) < 1e-6:
                detail += " WARNING: nothing was removed - check the profile side of the axis."
        swept = (f"{angle}deg on one side of the profile plane"
                 if not second else
                 f"{angle}deg + {second}deg the other side "
                 f"(total {angle + second}deg, centred on the profile plane)")
        return (
            f"Groove created: {swept} about "
            f"{self._axis_label(axis_mode, sketch)}.{detail} "
            f"Feature: '{groove.Name}'"
        )

    def _remove_feature(self, part: Any, feature: Any,
                        body: Any = None) -> bool:
        """Remove a feature from the tree; True if the call went through.

        A silent failure here is how a document ends up "dirty": the caller is
        told the feature was rolled back while the dead node stays in the tree
        and poisons every later update. Return the outcome so the message can
        stay honest.

        ``body`` is the body the feature was built in. Deleting a feature leaves
        ``InWorkObject`` on whatever CATIA happens to pick - on B32 the sketch's
        datum plane - and the tool then hands the caller a part whose in-work
        object is a geometrical set, which is the state its own error text warns
        about ("a Part Design factory called while in_work_object is a
        geometrical set fails exactly this way"). The pad path learned this on
        2026-10-26; the pocket path still left ``Datum_xy_45`` behind (reported
        2026-10-28, §7.2). Restoring here covers every rollback instead of one.
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
            except Exception:  # noqa: BLE001
                pass
            # The sentence above was true of the intent and not of the code:
            # the restore was missing, so every rollback left whatever CATIA
            # picked in work. Done here, where every rollback passes through.
            if body is not None:
                try:
                    current = str(part.InWorkObject.Name)
                except Exception:  # noqa: BLE001 - unreadable: assign anyway
                    current = None
                if current != str(getattr(body, "Name", body)):
                    try:
                        part.InWorkObject = body
                    except Exception:  # noqa: BLE001
                        pass
            if body is not None:
                try:
                    part.InWorkObject = body
                except Exception:  # noqa: BLE001 - the message is the payload
                    pass

    def _sketch_present(self, part: Any, sketch: Any) -> bool | None:
        """Whether a sketch is still in the tree - ``None`` when it cannot be told.

        A rollback that says "the feature was removed" has to be able to say what
        else went with it. Reported 2026-10-26: a failed pad removed the caller's
        input sketch, and the only evidence was the NEXT call answering "No sketch
        named '草图.5'. Available: 草图.1, 草图.4." - which reads like a typo in the
        caller's own naming.

        The check itself was broken until 0.3.30: it went through
        ``body.Parent.Bodies``, a Body has no ``Parent`` in the V5 automation
        model, the fallback then called ``.Count`` on a Python list, and the outer
        ``except`` returned True - "cannot tell, do not accuse". So it answered
        "still in the tree" for sketches CATIA had already deleted (measured on
        B30: 3 of 5 failed pads said exactly that while ``Body.Sketches`` read
        right after the message no longer held the sketch, and an explicit
        ``Part.Update()`` did not change it). Read ``Part.Bodies`` and answer
        honestly - including "I could not check".
        """
        try:
            name = str(sketch.Name)
        except Exception:  # noqa: BLE001 - a dead handle proves nothing
            name = None
        try:
            bodies = part.Bodies
        except Exception:  # noqa: BLE001
            return None
        try:
            for index in range(1, bodies.Count + 1):
                container = bodies.Item(index)
                try:
                    sketches = container.Sketches
                except Exception:  # noqa: BLE001 - a body without sketches
                    continue
                for position in range(1, sketches.Count + 1):
                    if name is not None and str(sketches.Item(position).Name) == name:
                        return True
        except Exception:  # noqa: BLE001 - cannot tell, so do not accuse
            return None
        return False

    def _sketch_alias(self, sketch_name: str) -> str | None:
        """The sketch re-created in place of ``sketch_name``, if there is one."""
        resolve = getattr(self.conn, "sketch_alias", None)
        if resolve is not None:
            return resolve(sketch_name)
        aliases = getattr(self.conn, "sketch_aliases", None) or {}
        try:
            document = str(self.conn.active_document.Name)
        except Exception:  # noqa: BLE001
            document = "?"
        return aliases.get(f"{document}::{sketch_name}")

    def _sketch_names_with_aliases(self, body: Any) -> str:
        """``Available:`` text that names every usable sketch, aliases included.

        A rebuilt sketch answers to its old name, so a list of live names alone
        tells the caller the opposite of the truth (reported 2026-10-27).
        """
        names: list[str] = []
        try:
            sketches = body.Sketches
            for i in range(1, sketches.Count + 1):
                live = str(sketches.Item(i).Name)
                finder = getattr(self.conn, "sketch_aliases_of", None)
                olds = finder(live) if finder is not None else []
                names.append(f"{live} (alias: {', '.join(olds)})" if olds else live)
        except Exception:  # noqa: BLE001 - a listing must not mask the error
            return ""
        return ", ".join(names)

    def _recover_lost_sketch(self, part: Any, body: Any,
                             sketch_name: str | None,
                             survived: bool | None,
                             requested: str | None = None) -> tuple[str, bool]:
        """The sentence about the input sketch after a rollback - and the repair.

        0.3.30 learned to READ whether the sketch survived, which ended the false
        "still in the tree". The 2026-10-27 acceptance run then showed the other
        half: the same call, on the same geometry, sometimes kept the sketch and
        sometimes lost it, so ``NO_MATERIAL`` had two different recoveries and a
        retry with the same name answered ``SKETCH_NOT_FOUND`` - a second error
        code from the same failure. Redrawing it from the record makes the state
        AFTER the error constant: the sketch is there, either because CATIA kept
        it or because this server put it back.

        Returns ``(sentence, a retry_can_work)``. The flag exists because the
        envelope's next_actions used to say "retry once with direction='normal'"
        after a rebuild had FAILED - telling the caller to retry against a sketch
        that no longer existed (reported 2026-11-08, M3).
        """
        if survived is not False or not sketch_name:
            return self._sketch_after_rollback(sketch_name, survived), True
        from catia_mcp.tools.sketcher import SketcherTools  # noqa: PLC0415 - cycle

        new_name, problem = SketcherTools(self.conn).rebuild_lost_sketch(
            part, body, sketch_name)
        # The caller never used the tree's name for this sketch: it asked for
        # 草图.2, an alias, and the sketch it has been working on is 草图.3. A
        # reply that only says 草图.3 -> 草图.4 leaves that caller unable to match
        # its own name to anything (reported 2026-10-28, §P4). Reserve the first
        # sentence for the name the caller used.
        asked = (str(requested).strip() if requested else "")
        if asked and asked != sketch_name:
            lead = (f" ('{asked}' resolves to '{sketch_name}', which CATIA just "
                    "removed.)")
        else:
            lead = ""
        if new_name:
            return (lead + (
                f" CATIA removed the sketch '{sketch_name}' with the rollback - a "
                "sketch is consumed by the feature built on it, and whether it goes "
                "with the node depends on the document's history, not on anything "
                f"this tool controls. It has been re-created from its recorded "
                f"definition as '{new_name}' (same plane, same drawing calls), and "
                f"'{sketch_name}' now resolves to it, so retrying the same call "
                "works and nothing else in the part changed."
                # The rebuilt sketch IS the answer to "what did I lose", and the
                # 0.3.31 rebuild path dropped the sentence that says what it holds
                # - so a rebuilt sketch, an alias no read tool resolved, and a
                # listing that only printed live names left the caller with no
                # way to see the profile (reported 2026-10-27).
                + self._sketch_rebuild_hint(
                    sketch_name, tail="the rebuilt sketch holds the same elements.")),
                True)
        # The rebuild failed. Do NOT go through _sketch_after_rollback here: its
        # closing advice is "catia_create_sketch + the same drawing calls rebuilds
        # it", and this reply is about to say the opposite - one message that
        # claimed both was the reported M3.
        return (lead
                + f" CATIA removed the sketch '{sketch_name}' with the rollback, and "
                "it could NOT be re-created: " + problem + "."
                + self._sketch_rebuild_hint(
                    sketch_name,
                    tail="that record is not enough to redraw it, so the sketch has "
                         "to be drawn again by hand before this feature can be "
                         "retried."),
                False)

    def _sketch_after_rollback(self, sketch_name: str | None,
                               survived: bool | None) -> str:
        """One sentence about the input sketch, read AFTER the rollback.

        Three states, because there are three: still in the tree, taken by CATIA
        together with the feature that consumed it, or unreadable. The third used
        to be reported as "still in the tree" - see ``_sketch_present`` - and that
        sentence sent the caller to "change the offset", on a sketch that no
        longer existed (reported 2026-10-27).
        """
        label = sketch_name or "the sketch"
        if survived is True:
            return (f" Your sketch '{label}' is still in the tree - only the "
                    "feature node was removed.")
        if survived is None:
            return (f" Whether '{label}' survived the rollback could not be read back "
                    "- check the tree before retrying."
                    + self._sketch_rebuild_hint(sketch_name))
        return (f" CATIA removed the sketch '{label}' with the rollback: a sketch is "
                "consumed by the feature built on it, so it went with the node."
                + self._sketch_rebuild_hint(sketch_name))

    def _sketch_rebuild_hint(self, sketch_name: str | None,
                             tail: str | None = None) -> str:
        """What the lost sketch held, from this session's own drawing record.

        A rollback that takes the sketch has to leave the caller something to act
        on: "recreate it" with no coordinates is the same dead end as the false
        "still in the tree". The sketcher already records every element it draws
        (GSD uses it to place outlines in 3D), keyed by document and name, so the
        profile can be echoed verbatim - and that record outlives the sketch
        precisely because it is not read back from CATIA.

        ``tail`` replaces the closing advice: when the sketch has ALREADY been
        rebuilt, "catia_create_sketch + the same drawing calls rebuilds it" tells
        the caller to redo work this server just did.
        """
        if not sketch_name:
            return ""
        outlines = getattr(self.conn, "sketch_outlines", None) or {}
        try:
            document = str(self.conn.active_document.Name)
        except Exception:  # noqa: BLE001
            document = "?"
        record = outlines.get(f"{document}::{sketch_name}") or {}
        elements = record.get("elements") or []
        placement = record.get("placement") or {}
        parts: list[str] = []
        for element in elements[:4]:
            kind = str(element.get("kind"))
            if kind == "rectangle":
                parts.append("rectangle (%g, %g) .. (%g, %g)"
                             % (element.get("x1", 0.0), element.get("y1", 0.0),
                                element.get("x2", 0.0), element.get("y2", 0.0)))
            elif kind == "circle":
                parts.append("circle centre (%g, %g) r %g"
                             % (element.get("cx", 0.0), element.get("cy", 0.0),
                                element.get("radius", 0.0)))
            elif kind == "line":
                parts.append("line (%g, %g) -> (%g, %g)"
                             % (element.get("x1", 0.0), element.get("y1", 0.0),
                                element.get("x2", 0.0), element.get("y2", 0.0)))
            elif kind == "arc":
                parts.append("arc centre (%g, %g) r %g"
                             % (element.get("cx", 0.0), element.get("cy", 0.0),
                                element.get("radius", 0.0)))
            else:
                parts.append(kind)
        if len(elements) > 4:
            parts.append(f"… {len(elements) - 4} more element(s)")
        where = ""
        if placement.get("base"):
            if str(placement.get("base")).lower() == "frame":
                # A sketch on a rotated datum plane records vectors, not an
                # offset - and says where they came from (0.3.87).
                derived = placement.get("derived")
                where = (" on the plane it was drawn on (%s)" % derived
                         if derived else " on the plane it was drawn on")
            else:
                where = " on plane '%s'" % placement["base"]
                if placement.get("distance") is not None:
                    where += " at offset %g" % placement["distance"]
        if not parts and not where:
            return ""
        drawn = "; ".join(parts) if parts else "no element recorded"
        closing = tail or ("catia_create_sketch + the same drawing calls + "
                           "catia_close_sketch rebuilds it.")
        return f" It held: {drawn}{where} - {closing}"

    @staticmethod
    def _try_update(part: Any, feature: Any) -> Exception | None:
        """Call UpdateObject, returning the exception instead of raising it."""
        try:
            part.UpdateObject(feature)
            return None
        except Exception as exc:  # noqa: BLE001 - the caller measures instead
            return exc

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

    def _safe_update(self, part: Any, feature: Any,
                     body: Any = None) -> None:
        """Update a feature; if CATIA rejects it, take the dead node back out.

        A failed Part Design feature left in the tree is not inert: **every**
        later Update of the part then fails at it, so one rejected fillet turns
        a 22-feature part into one that cannot be rebuilt at all. Reproduced on
        B30 2026-10-24: fillet R40 on a 40x20x10 block -> shape_count 1 -> 2,
        `update_part` fails, and the next pad fails too, until the dead
        `倒圆角.1` is deleted. The batch paths have rolled back since 0.3.7; the
        single-feature paths (fillet / chamfer / mirror / shell / thickness /
        boolean) called `UpdateObject` raw and did not.

        ``body`` is optional because it used to be *missing*: the rollback line
        read a free variable of that name, so the first rejected single feature
        raised ``NameError: name 'body' is not defined`` instead of the two
        sentences below - no rollback, and no word about the dead node. Reported
        2026-10-30 as "a rejected fillet reports a Python error".
        """
        try:
            part.UpdateObject(feature)
            return
        except Exception as error:  # noqa: BLE001 - reported with the rollback state
            name = str(getattr(feature, "Name", "the new feature"))
            if body is None:
                try:
                    body = self.conn.get_active_part_body()
                except Exception:  # noqa: BLE001 - the rollback below still counts
                    body = None
            gone = self._remove_feature(part, feature, body)
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
            raise ArgumentRejected(
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

    def _edge_table(self, body: Any, lengths: bool = True) -> list[dict[str, Any]]:
        """Every solid edge, with the handles that survive a rebuild.

        ``lengths=False`` skips the Length read, and that is not a micro-
        optimisation: ``SPAWorkbench.GetMeasurable`` costs 53 ms per edge the
        FIRST time it is asked for a given reference (measured on B30 2026-11-19
        on a 144-edge star: 7 625 ms for the first pass, 514 ms for the second,
        because SPA caches measurables itself). Length is 312 ms of that 7 625,
        so the pass exists to create the measurables, not to read them - and
        reading them is only needed for a fingerprint, which is only needed to
        MATCH or to REPORT an edge.

        A caller that resolves ``Edge.N`` never needs either. On the 288-edge
        part the 2026-11-19 report used for its 10 s chamfer, that pass was 27 s
        of the 31 s the reply called "edges resolved".

        Rows carry ``length=None`` and ``fingerprint=None`` when it was skipped;
        ``_length_filler`` fills them for the few rows that need it.
        """
        part = self.conn.get_active_part()
        spa = self._topo_measure(part)
        cells = topology_cells(self.conn, body, "Edge")
        table: list[dict[str, Any]] = []
        for index, cell in enumerate(cells, start=1):
            nls = nls_of(cell)
            faces = face_keys(nls)
            length = self._cell_measure(spa, cell, "Length") if lengths else None
            table.append({
                "index": index,
                "name": f"Edge.{index}",
                "cell": cell,
                "nls": nls,
                "bounded_by": faces,
                "length": length,
                "fingerprint": (edge_fingerprint(faces, length) if lengths
                                else None),
            })
        return table

    def _length_filler(self, table: list[dict[str, Any]]):
        """A function that fills ``length``/``fingerprint`` for the rows given.

        Lazy twice over: nothing is measured until a row is actually asked for,
        and the workbench is not even looked up until then - a caller that
        resolves ``Edge.N`` never needs a length, and on a 288-edge part the
        whole-body pass is 27 s (``GetMeasurable`` is 53 ms per edge the first
        time, see ``_edge_table``). Idempotent: a row that already knows its
        length costs nothing.
        """

        def fill(entries: list[dict[str, Any]]) -> None:
            pending = [entry for entry in entries if entry.get("length") is None]
            if not pending:
                return
            part = self.conn.get_active_part()
            spa = self._topo_measure(part)
            for entry in pending:
                entry["length"] = self._cell_measure(
                    spa, entry["cell"], "Length",
                    measurable=entry.get("measurable"))
                entry["fingerprint"] = edge_fingerprint(entry["bounded_by"],
                                                        entry["length"])

        return fill

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
        # 0.3.43's fix table named this exact call as an example of the new
        # envelope and the site was never converted, so the one row the
        # CHANGELOG used to demonstrate the rule was the row that broke it
        # (reported 2026-11-05, P1 §2.1).
        raise ArgumentRejected(
            f"Edge reference '{spec}' is neither 'Edge.N' nor a fingerprint from "
            "catia_list_edges ('L17.6112|Pad.6;2|Sketch.7;2'), nor an object "
            "like {\"length_mm\": 17.6112, \"faces\": [\"Pad.6;2\"]}.",
            next_actions=[
                "Run catia_list_edges and pass one of the 'Edge.N' indices it "
                "returns, or copy one of its fingerprint strings unchanged - "
                "nothing was changed by this call",
            ],
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
            table = self._edge_table(body, lengths=False)
        if not table:
            raise PreconditionRejected("The active body has no edges to fillet yet.")
        fill = self._length_filler(table)
        labels: list[str] = []
        refs: list[Any] = []
        for spec in specs:
            index, faces, length = self._parse_edge_spec(spec)
            if index is not None:
                if not 1 <= index <= len(table):
                    raise ArgumentOutOfRange(
                        f"Edge.{index} is out of range: the final shape exposes "
                        f"{len(table)} edge(s).")
                entry = table[index - 1]
                if faces and set(faces) != set(entry["bounded_by"]):
                    # An index plus faces means the caller wanted a specific one
                    # of two indistinguishable edges. If the index no longer
                    # carries those faces, the tree changed under them, and
                    # quietly filleting the wrong edge is worse than stopping.
                    raise ArgumentRejected(
                        f"Edge.{index} is not the edge you fingerprinted: its "
                        f"bounding faces are {sorted(entry['bounded_by'])} but the "
                        f"spec gave {sorted(set(faces))}. Re-run catia_list_edges "
                        "and take the Edge.N from that listing."
                    )
            else:
                entry = self._match_edge(table, faces, length, spec, fill=fill)
            fill([entry])       # the label quotes the length, so one row is read
            labels.append(f"{entry['name']} ({entry['fingerprint']})")
            refs.append(entry["cell"].Reference)
        return labels, refs

    @classmethod
    def _match_edge(cls, table: list[dict[str, Any]], faces: list[str],
                    length: float | None, spec: Any, fill: Any = None,
                    ) -> dict[str, Any]:
        """The one edge ``spec`` means, or a refusal that lists the candidates.

        ``fill`` reads lengths on demand - only the rows that could still be the
        answer, plus the whole table on the "nothing matches" path, where the
        reply has to quote what it looked at.
        """
        fill = fill or (lambda entries: None)
        wanted = set(faces)
        candidates = [e for e in table if wanted and set(e["bounded_by"]) == wanted]
        if not candidates and wanted:
            candidates = [e for e in table if wanted <= set(e["bounded_by"])]
        if not candidates and not wanted:
            candidates = list(table)
        if length is not None and candidates:
            fill(candidates)
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
            fill(table)         # the refusal quotes what it looked at
            near = sorted(table, key=lambda e: abs((e["length"] or 0.0) - (length or 0.0)))[:5]
            raise ArgumentRejected(
                f"No edge matches '{spec}'. The body's edges are (nearest first): "
                + "; ".join(f"{e['fingerprint']}" for e in near)
                + ". Fingerprints come from catia_list_edges and are re-resolved "
                "on every call, so they survive renumbering - an Edge.N does not."
            )
        fill(candidates)
        same_print = len({e["fingerprint"] for e in candidates}) == 1
        raise ArgumentRejected(
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
                kind="fillet", on_miss=str(args.get("on_anchor_miss") or "fail"))
        if len(names) > 1 or (names and not args.get("edge_name")):
            return self._fillet_many(args["radius"], list(names),
                                     self._propagation(args))
        target = args.get("edge_name")
        if target is None:
            return self._fillet_all_edges(args["radius"],
                                          self._propagation(args),
                                          self._edge_budget(args))
        return self._fillet_one(args["radius"], target,
                                self._propagation(args))

    #: Above this many edges the all-edges path stops after ONE batch attempt.
    #: Measured on B30 2026-11-12, with the batch path's single resolve: the
    #: one-at-a-time retry costs about 0.6 s per edge THAT TAKES and more for the
    #: ones that refuse - 54 edges took 15 s, 246 took 145 s (44 built), and the
    #: 381-edge real part took 458 s (101 built). The batch attempt is not the
    #: cheap option either: on that part it took 186 s and built nothing.
    #: 200 edges is therefore about two minutes when most of them take, which a
    #: call survives; the real cost above it is the FEATURES, one per edge that
    #: takes. (An earlier limit of 40 was set from 6.4 s per edge, which was
    #: measured by re-running catia_list_edges before every edge - that pays for
    #: 54 SPA length measurements each time and is not what this path does.)
    _ALL_EDGES_RETRY_LIMIT = 200

    def _edge_budget(self, args: dict[str, Any]) -> int:
        raw = args.get("max_edges")
        if raw is None:
            return self._ALL_EDGES_RETRY_LIMIT
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ArgumentRejected(
                f"max_edges must be a whole number of edges; got {raw!r}.") from exc
        if value < 1:
            raise ArgumentOutOfRange(
                "max_edges must be at least 1. It bounds how many edges the "
                f"all-edges path retries one at a time; got {value}.",
                code="MAX_EDGES_OUT_OF_RANGE", stage="validate")
        return value

    def _as_enumerated(self, refusal: CodedError, total: int,
                       kind: str) -> CodedError:
        """Re-raise a batch refusal as "the set was chosen by ME, not by you".

        The all-edges paths enumerate the body and hand every edge over, so a
        partial result is still a partial result - but the caller did not choose
        that set, and the sentence has to say which half of the problem they own.
        It is added HERE rather than inside the batch machinery so the batch
        keeps one meaning for both callers (2026-11-23 report, P0-2: a 12-edge
        box answered ok:true reading "Every edge ... enumerated" with 4 built).

        The recipe in the next action is measured, not suggested: on that same
        25x45x80 box, 4 edges parallel to the extrusion in one call and then ONE
        rim edge per end with propagation='tangency' gives the full 26-face
        solid at V 89495.6695 / A 12930.4246 - the corpus values to the digit.
        """
        message = (str(refusal)
                   + f" THIS SET WAS CHOSEN BY THE TOOL, NOT BY YOU: no edge was "
                   f"named, so all {total} edge(s) of the body were offered. "
                   "Refusing the enclosing rims is normal - their ends land on "
                   "the surfaces the first fillets just made - so a partial "
                   "answer here means 'the body cannot take that many fillets "
                   "one per edge', not 'you addressed the wrong edges'.")
        data = dict(getattr(refusal, "data", {}) or {})
        data["self_enumerated"] = True
        return CodedError(
            message,
            code=getattr(refusal, "code", None),
            modified=getattr(refusal, "modified", None),
            stage=getattr(refusal, "stage", None),
            data=data,
            next_actions=list(getattr(refusal, "next_actions", []) or []) + [
                "Take a whole RIM in one call instead of one edge at a time: "
                "catia_%s(edge_name=<one rim edge>, propagation='tangency') "
                "propagates along the tangent chain, so one call closes an end "
                "face. Measured 2026-11-23 on a 25x45x80 block: the 4 edges "
                "parallel to the extrusion in one call plus one rim edge per end "
                "with tangency gives all 26 faces and the exact volume, where 12 "
                "separate calls built 4." % kind,
            ])

    def _fillet_all_edges(self, radius: float, propagation: int,
                          limit: int) -> str:
        """Fillet every edge of the body by ENUMERATING them.

        WHAT THE OLD PATH DID, measured on B30 2026-11-12. Leaving ``edge_name``
        out passed a Reference to the last feature into
        ``AddNewSolidEdgeFilletWithConstantRadius`` and called that "all edges
        of the last feature". That call fails AT CONSTRUCTION on every shape
        tried - a 2-edge cylinder, a 12-edge rectangle, a 54-edge and a 246-edge
        polyline - with "方法 AddNewSolidEdgeFilletWithConstantRadius 失败".
        Changing the reference (feature ref / whole body) and the propagation
        (0 / 1) gave four cells and four failures, so the parameter was never
        the reason and the entry never worked. Its failure also escaped as a
        bare COM_ERROR that named no edge, which is what the 2026-11-11
        report hit twice.

        So: enumerate the body's edges and hand them to the batch path, which
        already rebuilds once, falls back to one-at-a-time, and names every edge
        that took and every edge that did not. Above ``limit`` edges only the
        single batch attempt runs - the measured cost of the rest is in
        ``_ALL_EDGES_RETRY_LIMIT``.
        """
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)

        table = self._edge_table(body)
        if not table:
            raise PreconditionRejected(
                "The active body has no edges to fillet yet. Build the solid "
                "first - a sketch alone has no edges to round.")
        total = len(table)
        specs = [{"index": entry["index"],
                  "faces": list(entry["bounded_by"]),
                  "length_mm": entry["length"]}
                 for entry in table]
        sequential = total <= limit
        lengths = sorted(entry["length"] for entry in table
                         if entry["length"] is not None)
        too_short = [value for value in lengths if value < 2 * radius]

        head = (f"All {total} edge(s) of '{body.Name}' were OFFERED to one fillet "
                f"each: enumerated rather than taken from the feature, because "
                f"CATIA's whole-feature fillet call does not build on B30 "
                f"(measured: 2-edge cylinder, 12-edge rectangle and two polylines "
                f"all fail at construction, whatever the propagation). How many "
                f"TOOK is in the next sentence, and it is not always all of them. ")
        # The shortest-edge count is ARITHMETIC and used to sit AFTER the
        # attempt, so callers paid for it before reading it. Measured on the
        # 2026-11-11 report's own part (381 edges, R1.5): the batch attempt took
        # 186 s and built nothing, and only then the reply said 166 of the 381
        # edges were shorter than 2*R. A radius that cannot reach half the part
        # is knowable before touching CATIA, so it goes first.
        if lengths and too_short:
            head += (f"{len(too_short)} of {total} edge(s) "
                     f"({len(too_short) / total * 100:.1f} %) are shorter than "
                     f"2*R = {2 * radius:g} mm. Measured on the 381-edge part "
                     "from the 2026-11-11 report (2026-11-12): R1.5 rounded 0 of "
                     "381, and even R0.25 - where every edge is longer than "
                     "2*R - rounded only 101, because neighbouring edges meet at "
                     "the same corner and compete for the same material. Either "
                     "the radius comes down or the outline loses corners. "
                     f"Shortest edge {lengths[0]:.4f} mm, longest "
                     f"{lengths[-1]:.4f} mm. ")
        if not sequential:
            head += (
                f"More than max_edges={limit}, so ONE batch attempt was made and "
                "the edges were NOT retried one at a time. That attempt is not "
                "free either: on a 381-edge part it took 186 s and produced 0 "
                "fillets (measured 2026-11-12), and a retry is a separate "
                "rebuild plus a separate feature - about 0.6 s per edge that "
                "takes and longer for the ones that fail, which is how 381 edges "
                "came to 458 s and 103 features. ")
            head += ("NEXT: choose the edges yourself and send a bounded batch - "
                     "catia_list_edges shows length and bounding faces, and "
                     "catia_fillet(edge_names=[...]) retries each one on its "
                     "own and names what failed. Raise max_edges only if you "
                     "accept the time.")
        # THE CALL GOES LAST, AFTER THE ARITHMETIC (0.3.161). It used to sit
        # first and the sentences about "166 of 381 edges are shorter than 2*R"
        # were appended after it - so when the batch refused (which since
        # 0.3.161 it does, instead of answering ok:true) the caller lost the one
        # paragraph that explains WHY those edges refuse. Everything above is
        # arithmetic on the edge table; none of it needed CATIA.
        started = time.perf_counter()
        try:
            reply = self._fillet_many(radius, specs, propagation,
                                      sequential_retry=sequential, strict=True)
        except CodedError as refusal:
            enriched = self._as_enumerated(refusal, total, "fillet")
            enriched.args = (head + str(enriched),)
            raise enriched from refusal
        elapsed = time.perf_counter() - started
        # The reply used to end with names and no clock, so a caller could not
        # size the next call - reported 2026-11-12 after 147 edges (46 built,
        # 101 refused) came back from a long call with no elapsed time at all.
        return head + reply + f" Took {elapsed:.1f} s."

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
                raise ArgumentRejected(
                    f"edge_anchors entries must be [x, y, z]; got {item!r}."
                ) from exc
            if len(values) != 3:
                raise ArgumentRejected(
                    f"edge_anchors entries must be [x, y, z]; got {item!r}."
                )
            cleaned.append(values)
        tol = args.get("anchor_tol")
        tol = 0.5 if tol is None else float(tol)
        if tol <= 0:
            raise ArgumentRejected(
                f"anchor_tol must be greater than 0; got {tol}. It is a "
                "distance in mm - 0.5 accepts a coordinate rounded to a tenth."
            )
        return cleaned, tol

    def _edges_by_anchor(self, value: float, specs: list[Any],
                         anchors: list[list[float]], tol: float,
                         propagation: int, kind: str = "fillet",
                         angle: float = 45.0, on_miss: str = "fail",
                         length2: float | None = None) -> str:
        """Fillet/chamfer a list of coordinate anchors instead of edge names."""
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        resolve_started = time.perf_counter()
        labels, refs, report, table = self._anchors_to_edges(
            body, anchors, tol, on_miss=on_miss, sweep=value)
        if specs:
            more_labels, more_refs = self._resolve_edge_specs(body, specs,
                                                              table=table)
            labels = more_labels + labels
            refs = more_refs + refs
        resolve_ms = int((time.perf_counter() - resolve_started) * 1000)
        timings: dict[str, int] = {"resolve_ms": resolve_ms,
                                   "edge_count": len(table)}

        if kind == "chamfer":
            # mode 0 = catLengthLength, so the 6th argument is a SECOND LENGTH.
            # That is the only way to get a two-distance bevel from this API
            # (measured 2026-11-21: 25/20 on a 100 mm edge removes exactly
            # 25*20/2*100 = 25 000 mm3, against 11 374 for 25 mm at 20 deg).
            mode, sixth = (0, length2) if length2 else (1, angle)

            def add(ref: Any) -> Any:
                return sf.AddNewChamfer(
                    ref,
                    1,        # catTangencyChamferPropagation
                    mode,     # 0 = length/length, 1 = length/angle
                    0,        # catNoReverseChamfer
                    value,
                    sixth,
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
                                      # THE RETRY MUST RE-RESOLVE THE ANCHORS, not
                                      # re-use the references it already has.
                                      # `resolve` is only called after a failed
                                      # batch has been deleted, and that rebuild
                                      # kills every topology reference taken
                                      # before it - so `lambda: (labels, refs)`
                                      # handed the retry four dead references and
                                      # every one of them failed again, which is
                                      # how a batch that could only do 2/4 stayed
                                      # at 2/4 (reported 2026-11-17: "the
                                      # edge_anchors path has no retry"). The
                                      # anchors are coordinates, so they survive:
                                      # match them again, and if that matching
                                      # now refuses, fall back to what we had.
                                      resolve=lambda: self._re_resolve_anchors(
                                          body, anchors, tol, (labels, refs)),
                                      context=report,
                                      timings=timings)
        finally:
            self._clear_selection()
            self.conn.refresh_display()
        # WHERE THE TIME WENT. The 2026-11-19 report timed five single-edge
        # chamfers at 25.0-56.3 s on a 140-face part while pad/pocket took
        # 0.2-0.5 s in the same session, and the reply said nothing about which
        # phase was paying. These three numbers answer that without a profiler.
        #
        # The EDGE COUNT and the per-edge cost joined them in 0.3.151: the same
        # report measured 10.3 s on a 186-edge part against the 0.86 s this
        # changelog quoted for a 12-edge box, and the difference is entirely
        # "how many edges does an anchor have to be measured against". Saying the
        # count turns that into arithmetic the caller can act on.
        edges = int(timings.get("edge_count") or 0)
        per_edge = (timings.get("resolve_ms", 0) / edges) if edges else 0
        spent = (f" [timings: edges resolved {timings.get('resolve_ms', 0)} ms"
                 + (f" over {edges} edge(s), {per_edge:.0f} ms each" if edges else "")
                 + f", features built {timings.get('build_ms', 0)} ms, "
                 f"rebuild {timings.get('rebuild_ms', 0)} ms]"
                 + (" [an anchor has to be measured against every edge of the "
                    "body to find the nearest one; an 'Edge.N' or fingerprint "
                    "from catia_list_edges targets one directly and skips that "
                    "pass - worth it above a few hundred edges]"
                    if per_edge >= 8 else ""))
        return out + " " + report + spent

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
                     propagation: int = 0,
                     sequential_retry: bool = True,
                     strict: bool = True) -> str:
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
                resolve=lambda: self._resolve_edge_specs(body, specs),
                sequential_retry=sequential_retry, strict=strict)
        finally:
            self._clear_selection()
            self.conn.refresh_display()

    def _apply_fillets(self, radius: float, names: list[str], refs: list[Any],
                       add: Any = None, part: Any = None,
                       label: str = "Fillet R",
                       resolve: Any = None,
                       sequential_retry: bool = True,
                       strict: bool = True,
                       context: str = "",
                       timings: dict[str, int] | None = None) -> str:
        """Create one fillet per (name, ref), then rebuild once.

        ``add`` is injectable for tests; it may return the new feature (in which
        case a failed rebuild rolls it back) or None. ``resolve`` re-reads the
        edge references from scratch and is what makes the sequential retry
        possible: deleting the batch rebuilds the topology, so the references
        that were resolved for it are dead by the time the retry needs them.

        ``sequential_retry=False`` stops after the single batch attempt and
        reports why: the retry costs one rebuild per edge (measured about 6 s
        each), so the all-edges path only pays it when the caller bounded the
        edge count.

        ``timings`` collects ``build_ms`` and ``rebuild_ms`` so a caller can see
        where a slow call spent its time instead of guessing (2026-11-19 report,
        P1-4: a single chamfer took 25-56 s and nothing in the reply said why).
        """
        done, failed, created = [], [], []
        build_started = time.perf_counter()
        for name, ref in zip(names, refs):
            try:
                feature = add(ref)
                done.append(name)
                if feature is not None:
                    created.append(feature)
            except Exception as error:        # noqa: BLE001 - reported per edge
                failed.append((name, str(error).splitlines()[0][:120]))
        if timings is not None:
            timings["build_ms"] = int((time.perf_counter() - build_started) * 1000)

        rebuild_error = None
        rebuild_started = time.perf_counter()
        if created and part is not None:
            try:
                if len(created) == 1:
                    # ONE FEATURE: update THAT feature, as pad/pocket do. The
                    # 2026-11-19 report measured a single chamfer taking 25-56 s
                    # on a 140-face part while pad took 0.3 s in the same
                    # session, and the difference is exactly this call:
                    # part.Update() rebuilds the whole tree, UpdateObject(feature)
                    # updates one node. A full rebuild is needed only when
                    # several features were created together (their references
                    # all go stale at once), which is the other branch.
                    part.UpdateObject(created[0])
                else:
                    part.Update()
            except Exception as error:        # noqa: BLE001 - reported below
                rebuild_error = str(error).splitlines()[0][:120]
        if timings is not None:
            timings["rebuild_ms"] = int((time.perf_counter() - rebuild_started) * 1000)

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
            if not sequential_retry:
                head = (f"{shown}: 0/{len(names)} edge(s) done. The single batch "
                        f"rebuild failed ({rebuild_error}) and the features it "
                        "created were removed again, so the body is unchanged. "
                        f"The {len(names)} edges were NOT retried one at a time: "
                        "each retry is a separate rebuild and a separate feature "
                        "(measured: about 0.6 s per edge that takes, more for the "
                        "ones that refuse - 381 edges of a real part took 458 s), "
                        "and this call was given more edges than it will spend "
                        "that on.")
                if strict:
                    # "0/381 done" as ordinary prose is the same half-truth the
                    # 2026-11-14 report caught on a two-edge batch: nothing was
                    # built, and any caller branching on status was told the call
                    # succeeded. It travels as a failure with modified=false.
                    raise CodedError(
                        head, code="EDGES_REJECTED", modified=False,
                        stage="execute",
                        data={"kind": "chamfer" if label == "Chamfer" else "fillet",
                              "value": radius, "requested": len(names), "done": 0,
                              "applied": [], "rejected": [], "not_attempted": True},
                        next_actions=[
                            "Send a bounded set of edges instead of all of them "
                            "(catia_list_edges shows length and bounding faces), "
                            "or raise max_edges to pay for the one-at-a-time retry",
                        ])
                return head
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
            # A 246-edge outline can take 44 of them: listing every name turned
            # the reply into twenty thousand characters, so the first twelve are
            # named and the rest are counted.
            head += f" ({', '.join(done[:12])}"
            head += (f" ... and {len(done) - 12} more)" if len(done) > 12 else ")")
            # Which radius each edge got, spelled out: the same edge can be sent
            # again in a later call with a different radius, and the reply used to
            # name the edges without saying what was applied to them (reported
            # 2026-10-25, after two edges were rebuilt at the wrong radius).
            try:
                stamp = f"{float(radius):g}"
            except (TypeError, ValueError):
                stamp = str(radius)
            short = [str(name).split(" ")[0] for name in done[:12]]
            head += " [applied " + ", ".join(f"{n}@R{stamp}" for n in short) + "]"
        if failed:
            head += ". Rejected: " + "; ".join(
                f"{n} -> {m}" for n, m in failed[:8])
            if len(failed) > 8:
                # The count is the information; 202 near-identical lines are not.
                head += (f"; and {len(failed) - 8} more edge(s) refused the same "
                         "way - catia_list_edges lists them all with length and "
                         "bounding faces")
            head += self._rejection_reason([n for n, _ in failed])
            hint = self._coplanar_hint([n for n, _ in failed])
            if hint:
                head += hint
            segmented = self._segmented_outline_hint([n for n, _ in failed])
            if segmented:
                head += segmented
        if strict and len(done) < len(names):
            self._raise_batch_failure(label, radius, names, done, failed, head,
                                      context=context)
        return head

    def _raise_batch_failure(self, label: str, value: float,
                             names: list[str], done: list[str],
                             failed: list[tuple[str, str]], head: str,
                             context: str = "") -> None:
        """A batch that did not do what it was asked is a FAILURE, not a sentence.

        Reported on 2026-11-14 (0.3.126) and again on 2026-11-16 (0.3.138): a
        chamfer asked for two edges answered ``1/2 edge(s) done`` as ordinary
        prose - no ``ok:false``, no ``code``, no ``isError`` - and left an
        asymmetric solid. Every caller that branches on status was told the call
        succeeded, ``catia_run_plan`` counted the step as passed, and the report's
        own volume reading (98007.6155 -> 94081.0753 mm3) is what caught it.

        The sentence was never the problem: it is accurate and detailed. What was
        missing is that ``done < requested`` is a failure state a machine can see.
        So the same text now travels as the message of a ``CodedError``, with the
        applied and rejected lists in ``data``. ``modified`` is true when anything
        did build - "this failed" and "nothing happened" are different facts, and
        the caller of a half-applied bevel needs both.

        ``strict=False`` is for the paths where the CALLER did not ask for a
        specific set: catia_fillet/chamfer with no edge named enumerate every edge
        themselves, and some of those are too short by arithmetic (a bevel of L on
        an edge shorter than 2L cannot meet its neighbour). Counting that as a
        failed call would make the honest use of the tool unusable.
        ``self_enumerated`` is NOT the same as "the caller asked for this set',
        and 0.3.160 got that wrong (2026-11-23 report, P0-2). ``strict=False``
        was justified by "those edges were not named by the caller, so a partial
        result is not a broken promise" - but a 25x45x80 block asked for
        ``catia_fillet(radius=2)`` and answered ``ok: true`` reading "Every edge
        of '零件几何体' (12 edge(s)): enumerated ...", while 4 of the 12 had
        built. The other 8 (the two end faces' rims) refuse because their ends
        land on the fillet surfaces the first four just made, the volume said
        89725.31 against 89495.67, and NOTHING in the reply said a machine could
        read. So the enumerated path passes strict=True as well; what differs is
        the sentence and ``data.self_enumerated``, which tell the caller the
        edges were chosen by the tool rather than by them.
        """
        requested = len(names)
        code = "PARTIAL_EDGES_FAILED" if done else "EDGES_REJECTED"
        kind = "chamfer" if label == "Chamfer" else "fillet"
        raise CodedError(
            context + head,
            code=code,
            modified=bool(done),
            next_actions=[
                "catia_list_edges shows each edge's length and bounding faces - a "
                "Fillet.N or Chamfer.N among them is the usual reason the next "
                "one refuses",
                "if the batch is what failed rather than the edge, send the same "
                f"edges one per call (catia_{kind}, one edge each): separate "
                "rebuilds are what works for edges that share a vertex",
            ],
            data={
                "kind": kind,
                "value": value,
                "requested": requested,
                "done": len(done),
                "applied": list(done),
                "rejected": [name for name, _ in failed],
                "rejected_reasons": {name: why for name, why in failed},
            },
            stage="execute")

    @staticmethod
    def _rejection_reason(labels: list[str]) -> str:
        """Why a fillet can refuse an edge, in the order that actually happens.

        The old sentence named ``catia_gsd_close_surface`` as the usual cause.
        Reported 2026-10-26 with the counter-example: four rejected edges whose
        ``bounded_by`` contained only Sketch and Pad - no GSD surface anywhere -
        while the real common factor was an existing R2.5 fillet meeting them at
        a corner. The fingerprint carries the bounding faces, so the cause can be
        read instead of guessed.
        """
        joined = " ".join(labels)
        if "Fillet." in joined or "Chamfer." in joined:
            return (
                ". These edges are bounded by an existing fillet or chamfer - the "
                "topology, not the argument: two rounds that meet at a corner fight "
                "over the same material, and the second one cannot build. Apply "
                "rounds that share a vertex in ONE call (the batch path builds them "
                "together), or reduce the radius on the later one."
            )
        if "CloseSurface" in joined:
            return (
                ". At least one of these edges came out of a GSD CloseSurface: "
                "those edges cannot take a Part Design fillet, while edges of a "
                "pad in the same body can."
            )
        return (
            ". The refusal is about the topology, not the argument. Usual causes, "
            "in order: an edge adjacent to an existing fillet or chamfer that "
            "shares a vertex with it; an edge shorter than twice the radius; and a "
            "seam edge, where two coplanar faces meet at 180 degrees and there is "
            "no dihedral angle to round off. catia_list_edges shows the bounding "
            "faces of each edge, so a Fillet.N there is the first thing to look for."
        )

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

    _FINGERPRINT_FACE = re.compile(r"([A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)\.(\d+);")

    @classmethod
    def _segmented_outline_hint(cls, labels: list[str]) -> str:
        """Name the SEGMENTED OUTLINE when that is why a fillet was refused.

        The 2026-11-13 report (P1-1) paid for this the hard way: a hole cut from a
        12-segment sketch_profile gave a rim of 12 edges, `catia_fillet` refused
        12/12 ("bounded by three or more faces"), and the only advice on offer was
        "draw the radius in the sketch, or cut the transition with catia_gsd_*".
        The reporter then spent a 15-section / 1440-coordinate flare body trying to
        fake a roundover. Redrawing the SAME hole as one closed
        `catia_sketch_spline` left ONE rim edge, and R1.8 filleted 1/1 - the
        measurements are the reporter's, on B30 2026-11-13.

        So when several rejected edges name the SAME sketch or feature, say it: the
        profile was drawn as segments, each segment is its own edge, and the seam
        between two of them has three faces and nothing to round. The fix is at the
        PROFILE, not at the fillet - which is the one thing the old message left
        out.

        Deliberately a hint, not a verdict: this reads fingerprints, and a part can
        legitimately have three faces on an edge for other reasons (an earlier
        fillet meeting it is the other measured one).
        """
        if len(labels) < 3:
            return ""
        counts: dict[str, int] = {}
        for label in labels:
            named = {f"{m.group(1)}.{m.group(2)}"
                     for m in cls._FINGERPRINT_FACE.finditer(label or "")}
            for name in named:
                counts[name] = counts.get(name, 0) + 1
        if not counts:
            return ""
        owner, hits = max(counts.items(), key=lambda item: item[1])
        # ALL of the rejected edges, not most of them: a fillet that took four and
        # refused four is a different situation (measured on B30 2026-11-13 during
        # 0.3.123's own audit - a mixed batch produced this hint about the PAD's
        # outline, where "redraw it as one spline" is not the fix). The reporter's
        # case was 12/12.
        if hits < 3 or hits != len(labels):
            return ""
        return (
            f" {hits} of these {len(labels)} edge(s) all come from the same profile "
            f"'{owner}'. That is what a SEGMENTED outline leaves behind: every "
            "segment of the sketch becomes its own edge, and the seam between two "
            "of them is bounded by three or more faces, so there is no dihedral "
            "angle to round. THE FIX IS AT THE PROFILE, NOT AT THE FILLET - "
            "measured by the reporter on B30 2026-11-13 on their own part: a hole "
            "cut from a 12-segment outline refused 12/12, and the SAME hole redrawn "
            "as ONE closed catia_sketch_spline then filleted 1/1 at R1.8. Two calls: "
            "(1) redraw the loop as one curve - catia_sketch_spline(closed=true) "
            "through the same points (catia_sketch_get_geometry lists what the old "
            "sketch holds, and the drawing record has the points if this session "
            "drew them); (2) cut again with catia_pocket and fillet the single rim "
            "edge that results. Keep the radius under half the profile's smallest "
            "radius of curvature, or the fillet will refuse for the next reason "
            "instead.")

    def _fillet_one(self, radius: float, edge_name: Any,
                    propagation: int = 0) -> str:
        if not edge_name:
            # Reached only if a caller bypasses _fillet. The whole-feature
            # Reference this branch used to build never worked (see
            # _fillet_all_edges for the four measured cells), so it is gone
            # rather than kept as a dead end. Checked before touching CATIA,
            # so the refusal costs nothing and leaves nothing behind.
            raise ArgumentRejected(
                "catia_fillet needs edge_name, edge_names or edge_anchors. To "
                "round every edge of the body, omit all three and the tool "
                "enumerates them itself.")
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        labels, refs = self._resolve_edge_specs(body, [edge_name])
        target, scope = refs[0], labels[0]

        fillet = sf.AddNewSolidEdgeFilletWithConstantRadius(
            target,
            propagation,   # 0 = only these edges, 1 = catTangencyFilletEdgePropagation
            radius,
        )

        try:
            self._safe_update(part, fillet, body)
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
        # 0.3.156: a two-distance bevel. AddNewChamfer's 3rd argument selects
        # catLengthLength (0) or catLengthAngle (1), and in mode 0 the 6th
        # argument IS the second length. Measured on B30 2026-11-21: on a 100 mm
        # cube, mode 0 with 25 and 20 removes exactly 25*20/2*100 = 25 000 mm3,
        # against 11 374 mm3 for mode 1 at 25 mm / 20 deg. So the capability was
        # always there and only the parameter was missing - the 2026-11-21 report
        # (P0-1) had to cut two triangle sketches instead.
        length2 = args.get("length2")
        if length2 is not None:
            length2 = float(length2)
            if length2 <= 0:
                raise ArgumentRejected(
                    f"length2 must be a positive distance in mm - got {length2:g}. "
                    "Nothing was created.")
        if args.get("swap_distances"):
            # Which adjacent face takes `length` and which takes `length2` is
            # decided by CATIA from the edge's own direction, and this build does
            # NOT expose either length for read-back (measured 2026-11-22:
            # Chamfer.Length and Chamfer.SecondLength both raise 'Item.Length'),
            # so the first attempt can land the wrong way round and the caller
            # cannot tell from the reply. Swapping the two arguments is the whole
            # repair - said here instead of leaving the caller to delete the
            # feature, re-read the docs and rebuild by hand (2026-11-22 report,
            # P1-21). Refused rather than ignored when there is nothing to swap.
            if length2 is None:
                raise ArgumentRejected(
                    "swap_distances=true needs length2: a length/angle bevel has "
                    "no second distance to swap. Send length2 as well, or drop "
                    "this flag. Nothing was created.")
            args = dict(args)
            args["length"], length2 = length2, float(args["length"])
            length2 = float(length2)
        anchors, tol = self._anchor_args(args)
        if anchors:
            specs = []
            names = args.get("edge_names") or []
            if isinstance(names, str):
                names = [names]
            specs.extend(names)
            if args.get("edge_name"):
                specs.append(args["edge_name"])
            # Only passed when it is set: the default call keeps the shape every
            # test double already has (the same rule the measurement code uses).
            extra = {"length2": length2} if length2 else {}
            return self._edges_by_anchor(
                float(args["length"]), specs, anchors, tol, 1,
                kind="chamfer", angle=float(args.get("angle", 45)),
                on_miss=str(args.get("on_anchor_miss") or "fail"), **extra)
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        names = args.get("edge_names") or []
        if isinstance(names, str):
            names = [names]
        if len(names) > 1 or (names and not args.get("edge_name")):
            return (self._chamfer_many(args["length"], args.get("angle", 45),
                                       list(names), length2=length2)
                    if length2 else
                    self._chamfer_many(args["length"], args.get("angle", 45),
                                       list(names)))
        if args.get("edge_name") is None:
            return (self._chamfer_all_edges(
                        args["length"], args.get("angle", 45),
                        self._edge_budget(args), length2=length2)
                    if length2 else
                    self._chamfer_all_edges(
                        args["length"], args.get("angle", 45),
                        self._edge_budget(args)))
        return (self._chamfer_one(args["length"], args.get("angle", 45),
                                  args.get("edge_name"), length2=length2)
                if length2 else
                self._chamfer_one(args["length"], args.get("angle", 45),
                                  args.get("edge_name")))

    def _chamfer_all_edges(self, length: float, angle: float,
                           limit: int, length2: float | None = None) -> str:
        """Chamfer every edge of the body by enumerating them.

        The same dead end as the fillet, measured separately on B30 2026-11-12
        because AddNewChamfer is a different method with a different signature
        (six arguments, mode 1 = length+angle): a Reference to the last feature,
        and a Reference to the whole body, both fail AT CONSTRUCTION with
        "方法 AddNewChamfer 失败" - on a 2-edge cylinder and on a 12-edge
        rectangle, while the volume backs it up by not moving at all. So the
        edges are enumerated and handed to the batch path.
        """
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)

        table = self._edge_table(body)
        if not table:
            raise PreconditionRejected(
                "The active body has no edges to chamfer yet. Build the solid "
                "first - a sketch alone has no edges to bevel.")
        total = len(table)
        specs = [{"index": entry["index"],
                  "faces": list(entry["bounded_by"]),
                  "length_mm": entry["length"]}
                 for entry in table]
        sequential = total <= limit
        lengths = sorted(entry["length"] for entry in table
                         if entry["length"] is not None)
        too_short = [value for value in lengths if value < 2 * length]

        head = (f"All {total} edge(s) of '{body.Name}' were OFFERED to one bevel "
                f"each: enumerated rather than taken from the feature, because "
                f"CATIA's whole-feature chamfer call does not build on B30 "
                f"(measured: feature reference and body reference both fail at "
                f"construction on a 2-edge cylinder and a 12-edge rectangle). How "
                f"many TOOK is in the next sentence. ")
        if lengths and too_short:
            # 2*L is where two bevels of this size meet at a shared corner, so
            # the count is a warning about corners, not a proof about any one
            # edge: the arithmetic is the same as the fillet's, which measured
            # 101 of 381 at a radius where every edge was long enough.
            head += (f"{len(too_short)} of {total} edge(s) "
                     f"({len(too_short) / total * 100:.1f} %) are shorter than "
                     f"2*L = {2 * length:g} mm, so a bevel this size meets its "
                     "neighbour at the shared corner - expect those to be "
                     "refused. Shortest edge "
                     f"{lengths[0]:.4f} mm, longest {lengths[-1]:.4f} mm. ")
        if not sequential:
            head += (
                f"More than max_edges={limit}, so ONE batch attempt was made and "
                "the edges were NOT retried one at a time. That attempt is not "
                "free: on a 381-edge part the same batch took 186 s and produced "
                "0 fillets (measured 2026-11-12), and a retry is a separate "
                "rebuild plus a separate feature. ")
            head += ("NEXT: choose the edges yourself and send a bounded batch - "
                     "catia_list_edges shows length and bounding faces.")
        # Same order as the fillet: the arithmetic first, the call last, so a
        # refusal still carries the paragraph that explains the rejected edges.
        started = time.perf_counter()
        try:
            reply = (self._chamfer_many(length, angle, specs,
                                        sequential_retry=sequential, strict=True,
                                        length2=length2)
                     if length2 else
                     self._chamfer_many(length, angle, specs,
                                        sequential_retry=sequential, strict=True))
        except CodedError as refusal:
            enriched = self._as_enumerated(refusal, total, "chamfer")
            enriched.args = (head + str(enriched),)
            raise enriched from refusal
        elapsed = time.perf_counter() - started
        return head + reply + f" Took {elapsed:.1f} s."

    def _chamfer_readback(self, feature: Any, length: float,
                          length2: float | None, angle: float,
                          before_mm3: float | None, after_mm3: float | None,
                          scopes: list[str]) -> str:
        """What the FEATURE says, next to what was asked for (report P1-21).

        The 2026-11-22 report built a 25/20 bevel, saw the right volume, and
        still could not answer "is 25 on the face I meant?" from the reply - the
        tool's own description tells the caller to "do one and look at the
        result", so the reply is where that look belongs.

        What this build can and cannot answer, measured on B30 2026-11-22 on a
        two-distance bevel of a 100 mm cube edge:

        * ``Chamfer.Mode`` reads back (0 = catLengthLength for a two-distance
          bevel, 1 = catLengthAngle) - and stays 0 after ``part.Update()``.
        * ``Chamfer.Length`` and ``Chamfer.SecondLength`` do NOT read back:
          both answer ``Item.Length`` / ``Item.SecondLength``, through the
          typed dispatcher AND through a dynamic one. So the two numbers in
          this sentence are what was SENT, and it says so instead of printing
          them as if they had been confirmed.
        * ``Chamfer.Angle`` reads 45 in mode 0 as well - the default, not a
          bevel angle - so quoting it there would be a reading of something
          that does not apply.

        The removed volume is the physical confirmation: an a x b bevel over L
        mm of edge removes L*a*b/2, which is how the 2026-11-21 report and this
        one both checked the same feature (25 000 mm3 for 25/20 on 100 mm
        against 11 374 for 25 mm at 20 deg).
        """
        try:
            mode = int(feature.Mode)
        except Exception:  # noqa: BLE001 - a removed feature answers nothing
            return ""
        mode_words = {0: "catLengthLength (two distances)",
                      1: "catLengthAngle (length + angle)"}.get(mode, "unknown")
        size = (f"{length:g} x {length2:g} mm" if length2
                else f"{length:g} mm at {angle:g} deg")
        read: list[str] = []
        for name in ("Length", "SecondLength"):
            try:
                value = getattr(feature, name)
                read.append(f"{name}={float(value.Value):g}")
            except Exception:  # noqa: BLE001 - measured absent on this build
                read.append(f"{name}=unreadable")
        lengths_note = (
            "; the feature reads back " + ", ".join(read) + "."
            if all("unreadable" not in item for item in read) else
            f"; the LENGTHS are not readable on this build (measured 2026-11-22: "
            f"Chamfer.Length and .SecondLength both raise 'Item.Length'), so "
            f"{size} are the values that were SENT, not a re-read.")
        text = (f" Read back from '{getattr(feature, 'Name', 'the feature')}': "
                f"Mode={mode} ({mode_words})" + lengths_note)

        removed = None
        if before_mm3 is not None and after_mm3 is not None:
            removed = before_mm3 - after_mm3
        if removed is not None:
            text += f" Removed {removed:.1f} mm3."
            ideal = self._chamfer_ideal(length, length2, angle, scopes)
            if ideal:
                text += (f" An ideal {size} bevel over the {sum(ideal[1]):.1f} mm "
                         f"of edge named here removes {ideal[0]:.1f} mm3; a "
                         "difference means part of the edge was already consumed "
                         "(a neighbouring bevel, a fillet, a corner radius).")
        if length2:
            text += (" Which adjacent face takes which distance follows the edge's "
                     "own direction and cannot be read back here; if the bevel is "
                     "on the wrong side, catia_delete_feature it and repeat with "
                     "swap_distances=true.")
        return text

    @staticmethod
    def _chamfer_ideal(length: float, length2: float | None, angle: float,
                       scopes: list[str]) -> tuple[float, list[float]] | None:
        """Ideal removed volume and the edge lengths it was computed over.

        The scope labels this module builds carry the edge length as
        ``L100.0000`` (see ``_resolve_edge_specs``), which is where the numbers
        come from. When a label does not carry one, the arithmetic is skipped
        rather than guessed - a wrong "expected" value would read as a defect
        in CATIA.
        """
        lengths: list[float] = []
        for scope in scopes:
            match = re.search(r"\bL([0-9.]+)", scope)
            if match is None:
                return None
            try:
                lengths.append(float(match.group(1)))
            except ValueError:
                return None
        if not lengths:
            return None
        total = sum(lengths)
        if length2:
            return (total * length * length2 / 2.0, lengths)
        return (total * length * length * math.tan(math.radians(angle)) / 2.0,
                lengths)

    def _chamfer_many(self, length: float, angle: float, specs: list[Any],
                      sequential_retry: bool = True,
                      strict: bool = True,
                      length2: float | None = None) -> str:
        """Same batching rule as ``_fillet_many``: resolve all, build all, rebuild once."""
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        names, refs = self._resolve_edge_specs(body, specs)

        # mode 0 = catLengthLength, 6th argument = second length (see _chamfer).
        mode, sixth = (0, length2) if length2 else (1, angle)
        created: list[Any] = []

        def add(ref: Any) -> Any:
            feature = sf.AddNewChamfer(
                ref,
                1,       # catTangencyChamferPropagation
                mode,    # 0 = length/length, 1 = length/angle
                0,       # catNoReverseChamfer orientation
                length,
                sixth,   # second length in mode 0, degrees in mode 1
            )
            # Kept for the read-back below: Mode is readable on this build even
            # though the two lengths are not (measured 2026-11-22).
            created.append(feature)
            return feature

        before_volume = self._mass_probe(part, body)[0]
        try:
            reply = self._apply_fillets(length, names, refs, add, part=part,
                                        label="Chamfer",
                                        resolve=lambda: self._resolve_edge_specs(body, specs),
                                        sequential_retry=sequential_retry,
                                        strict=strict)
        except CodedError as refusal:
            # A PARTIAL BEVEL STILL OWES THE READ-BACK. Since 0.3.161 the
            # all-edges path raises when some edges refuse instead of returning
            # that as prose, and the sentence this feature can add - what Mode
            # says and how much material actually went - is exactly what a
            # caller needs then. It is appended rather than lost.
            if created:
                after = self._mass_probe(part, body)[0]
                try:
                    refusal.args = (str(refusal) + self._chamfer_readback(
                        created[0], length, length2, angle,
                        before_volume, after, names),)
                except Exception:  # noqa: BLE001 - the refusal stands without it
                    pass
            raise
        finally:
            self._clear_selection()
            self.conn.refresh_display()
        # A feature that was rolled back answers nothing, and the helper returns
        # "" for it - so a failed batch gains no sentence about a bevel that is
        # not in the tree.
        after_volume = self._mass_probe(part, body)[0] if created else None
        if created:
            reply += self._chamfer_readback(created[0], length, length2, angle,
                                            before_volume, after_volume, names)
        return reply

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

    def _chamfer_one(self, length: float, angle: float, edge_name: Any,
                     length2: float | None = None) -> str:
        if not edge_name:
            # Same dead end as the fillet: the whole-feature Reference never
            # built (see _chamfer_all_edges). Checked before touching CATIA.
            raise ArgumentRejected(
                "catia_chamfer needs edge_name, edge_names or edge_anchors. To "
                "bevel every edge of the body, omit all three and the tool "
                "enumerates them itself.")
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        labels, refs = self._resolve_edge_specs(body, [edge_name])
        target, scope = refs[0], labels[0]

        # AddNewChamfer(iObjectToChamfer, iPropagation, iMode, iOrientation,
        #               iLength1, iLength2OrAngle) - 6 parameters.
        # iMode matters: 0 is catLengthLength, so the 6th argument is read as a
        # second length. Passing 45 there asked CATIA for a 2 x 45 mm bevel,
        # which can never build - every chamfer failed at UpdateObject until
        # this was measured (2026-10-23). Mode 1 is catLengthAngle, and that is
        # the default; length2 selects mode 0 (measured 2026-11-21).
        mode, sixth = (0, length2) if length2 else (1, angle)
        before_volume = self._mass_probe(part, body)[0]
        chamfer = sf.AddNewChamfer(
            target,
            1,       # catTangencyChamferPropagation
            mode,    # 0 = length/length, 1 = length/angle
            0,       # catNoReverseChamfer orientation
            length,
            sixth,   # second length in mode 0, degrees in mode 1
        )

        update_error = self._try_update(part, chamfer)
        self.conn.refresh_display()
        self._clear_selection()
        if update_error is not None:
            gone = self._remove_feature(part, chamfer, body)
            size = (f"{length:g} x {length2:g} mm (two distances)"
                    if length2 else f"{length} mm at {angle} deg")
            raise CodedError(
                "CATIA rejected the chamfer"
                + (" and it was removed from the tree" if gone else
                   " (the dead feature could NOT be deleted - close the document "
                   "without saving)")
                + f". {size} on {scope}. A chamfer longer "
                "than the adjacent face, or an edge shared with an existing "
                f"fillet, cannot build. Original error: {update_error}"
            , code="FEATURE_REJECTED", stage="execute") from update_error
        size = (f"{length:g} x {length2:g} mm (two distances)"
                if length2 else f"{length} mm at {angle} deg")
        after_volume = self._mass_probe(part, body)[0]
        return (f"Chamfer created: {size} on {scope}. Feature: '{chamfer.Name}'"
                + self._chamfer_readback(chamfer, length, length2, angle,
                                         before_volume, after_volume, [scope]))

    def _hole(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        sketch = self._get_last_sketch(args.get("sketch_name"))
        sketch_name = getattr(sketch, "Name", None)
        diameter = args["diameter"]
        through_all = bool(args.get("through_all")) and "depth" not in args
        depth = args.get("depth", 0)

        # Put the bore where the profile is BEFORE cutting, and say what was
        # changed to get it there. See _SKETCH_BASES / _place_bore: CATIA takes
        # the axis from a POINT in the sketch, and a sketch with only a circle
        # is drilled at the sketch origin - silently, with a correct-looking
        # volume delta (reported 2026-10-27).
        placement = self._hole_profile(sketch).get("placement")
        axis_note, target = self._place_bore(part, sketch)
        before, cog_before, volume_note = self._mass_probe(part, body)

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

        threaded = bool(args.get("threaded", False))

        # The hole has to MATERIALIZE before its mode can be trusted to stick:
        # threading before the first update breaks the rebuild (upstream f9751a1,
        # field-verified on B30). So this update happens first, and the threading
        # is applied to a hole that already exists.
        update_error = self._try_update(part, hole)
        thread_note = self._apply_threading(part, hole, threaded)

        self.conn.refresh_display()

        after, cog_after, _after_note = self._mass_probe(part, body)
        took = None
        if before is not None and after is not None:
            took = (after - before) < -1e-6
        extent = "through all" if through_all else f"depth {depth} mm"
        if took is False:
            gone = self._remove_feature(part, hole, body)
            survived = self._sketch_present(part, sketch)
            recovery, sketch_usable = self._recover_lost_sketch(
                part, body, sketch_name, survived,
                requested=args.get("sketch_name"))
            raise FeatureRejected(
                "The hole removed no material, so it was "
                + ("rolled back" if gone else
                   "left in the tree (the dead feature could NOT be deleted - "
                   "close the document without saving)")
                + f" (volume stayed {before:.1f} mm3)."
                + recovery
                + self._bore_axis_sentence(target, placement)
                + " The sketch has to hold a "
                "closed CIRCLE: the bore's axis comes from a POINT in the "
                "sketch (this tool adds one at the circle's centre when the "
                "sketch has none), so a sketch without a circle produces a "
                "feature that cannot update."
                + ("" if through_all else
                   " A depth deeper than the local wall also fails; "
                   "pass through_all for that case.")
                + (f" Original error: {update_error}" if update_error else ""),
                rolled_back=gone,
                next_actions=(
                    ["Draw the sketch again before retrying: CATIA took it with the "
                     "rollback and it could not be rebuilt from its record - its "
                     "old definition is echoed above"]
                    if not sketch_usable else
                    ["Put a closed circle in the sketch at the bore's centre, or "
                     "pass through_all if the bore has to cross the whole wall; "
                     "these were already checked before the feature was built"]
                ),
            ) from (update_error or None)
        if before is not None and after is not None:
            removed = before - after
            wrong_at = self._bore_off_target(before, cog_before, after, cog_after,
                                             target, placement, diameter)
            if wrong_at is not None:
                # A bore in the wrong place is worse than no bore: the volume
                # delta is right, so nothing downstream can tell. Take it back
                # out and say where it went.
                went = self._remove_feature(part, hole, body)
                survived = self._sketch_present(part, sketch)
                raise CodedError(
                    f"The bore is not where the profile is, so it was "
                    + ("rolled back" if went else
                       "left in the tree (it could NOT be deleted - close the "
                       "document without saving)")
                    + f". {wrong_at}"
                    # Only the sentence: this refusal has no retry list, so the
                    # "can a retry work" flag has nowhere to go - and the sentence
                    # itself already says whether the sketch came back.
                    + self._recover_lost_sketch(part, body, sketch_name, survived)[0]
                , code="FEATURE_REJECTED", stage="execute") from None
            note = ""
            if update_error is not None:
                note = (" (CATIA raised during the update, but the bore is in the "
                        "geometry - kept)")
            length_note = self._bore_length_note(
                removed, diameter, depth, through_all, hole_type, threaded)
            position_note = self._bore_position_note(
                before, cog_before, after, cog_after, target, placement, diameter)
            return (
                f"Hole created: D{diameter} mm, {extent}. Removed {removed:.1f} mm3 "
                f"({before:.1f} -> {after:.1f}). Feature: '{hole.Name}'."
                f"{length_note}{position_note}{axis_note}{type_note}"
                f"{thread_note}{note}"
            )
        why = volume_note or "the volume came back empty"
        return self._unverified(
            "hole", str(hole.Name), f"D{diameter} mm, {extent}",
            why + axis_note + type_note + thread_note, update_error,
            ["catia_measure_topology shows the body's faces: a bore that landed "
             "on the wrong side of the material leaves the face count unchanged",
             "check the sketch the hole was built on: a bore's direction comes "
             "from its profile plane, not from the depth alone"])

    def _mass_probe(self, part: Any, body: Any
                    ) -> tuple[float | None, list[float] | None, str | None]:
        """``(volume_mm3, centre_of_gravity, note)`` for the body's solid.

        The volume answers "did the feature do anything"; the centre of gravity
        answers "did it do it HERE". One measurable, two properties, so a bore
        can be checked for position at the price of the volume probe it already
        paid.
        """
        try:
            doc = part.Parent
            try:
                spa = doc.GetWorkbench("SPAWorkbench")
            except AttributeError:
                spa = self.conn.app.GetWorkbench("SPAWorkbench")
        except Exception as exc:  # noqa: BLE001 - the reason is the payload
            return None, None, f"the SPAWorkbench (space analysis) would not open: {exc}"
        try:
            ref = part.CreateReferenceFromObject(body)
        except Exception as exc:  # noqa: BLE001
            return None, None, f"no reference could be made to the body: {exc}"
        try:
            measurable = spa.GetMeasurable(ref)
            volume = measurable.Volume
        except Exception as exc:  # noqa: BLE001
            return None, None, f"the measurable's Volume failed: {exc}"
        if volume is None:
            return None, None, "the measurable returned no volume"
        cog = None
        try:
            from catia_mcp.tools.measurement import (  # noqa: PLC0415 - cycle
                read_measurable_array,
            )
            cog = [float(v) for v in
                   read_measurable_array(self.conn.app, measurable, "GetCOG", 3)]
        except Exception:  # noqa: BLE001 - the volume still stands
            cog = None
        return volume * 1e9, cog, None  # m3 -> mm3

    def _hole_profile(self, sketch: Any) -> dict[str, Any]:
        """What the sketch holds and where it sits, from this session's record.

        Read from ``conn.sketch_outlines`` rather than from CATIA: a Point2D's
        coordinates cannot be read back through automation on this build, which
        is why the sketcher records every element it draws. A sketch that came
        from a reopened file simply has no record - and then nothing is guessed.
        """
        name = str(getattr(sketch, "Name", "") or "")
        outlines = getattr(self.conn, "sketch_outlines", None) or {}
        try:
            document = str(self.conn.active_document.Name)
        except Exception:  # noqa: BLE001
            document = "?"
        record = outlines.get(f"{document}::{name}") or {}
        elements = record.get("elements") or []
        circles: list[dict[str, float]] = []
        points: list[dict[str, float]] = []
        for element in elements:
            kind = str(element.get("kind"))
            if kind == "circle":
                circles.append({"cx": float(element.get("cx", 0.0)),
                                "cy": float(element.get("cy", 0.0)),
                                "r": float(element.get("radius", 0.0))})
            elif kind == "point":
                points.append({"x": float(element.get("x", 0.0)),
                               "y": float(element.get("y", 0.0))})
        placement = record.get("placement")
        return {"name": name, "placement": placement, "circles": circles,
                "points": points,
                "known": bool(circles) and _sketch_frame(placement) is not None}

    def _place_bore(self, part: Any, sketch: Any
                    ) -> tuple[str, list[float] | None]:
        """Make sure the sketch has a point where its circle is.

        CATIA's Hole takes the bore's axis from a POINT in the sketch, not from
        the circle: measured on B30 2026-10-27, a sketch holding one closed
        circle at (10, 0) gave a part bit-identical to the same circle at (0, 0),
        and one at (100, 100) - outside a 40 x 20 block - drilled a 15 mm bore
        through the middle of the block while the reply said "Removed 754.0 mm3".
        One construction point at the centre moved the bore onto it; a point
        somewhere else moved the bore to that point instead.

        Returns ``(sentence to append, expected world axis or None)``. Raises
        when the sketch's own point and its circle disagree, because that bore
        would be in the wrong place by construction.
        """
        profile = self._hole_profile(sketch)
        if not profile["known"]:
            return "", None
        circle = profile["circles"][0]
        cx, cy = circle["cx"], circle["cy"]
        placement = profile["placement"]
        target = _sketch_point_world(placement, cx, cy)
        for point in profile["points"]:
            if abs(point["x"] - cx) <= 1e-6 and abs(point["y"] - cy) <= 1e-6:
                return "", target
        if profile["points"]:
            first = profile["points"][0]
            where = _sketch_point_world(placement, first["x"], first["y"])
            gap = _in_plane_distance(
                where, target, placement) if where is not None else None
            raise ArgumentRejected(
                f"Sketch '{profile['name']}' holds a point at ({first['x']:g}, "
                f"{first['y']:g}) and a circle centred at ({cx:g}, {cy:g})"
                + (f", {gap:.3f} mm apart" if gap is not None else "")
                + ". CATIA's Hole feature takes the bore's axis from a POINT in "
                "the sketch, so this bore would land on that point and not on "
                "the circle - and the removed volume would look exactly right. "
                "Nothing was created. Move or delete that point "
                "(catia_sketch_edit, then the sketch tools), or draw the profile "
                "so that the point sits at the circle's centre."
            )
        problem = self._add_locating_point(part, sketch, cx, cy)
        if problem is not None:
            return (f" The bore's position was not anchored: no point could be "
                    f"added to '{profile['name']}' at ({cx:g}, {cy:g}) ({problem}), "
                    "and a sketch with no point is drilled at the sketch origin.",
                    target)
        return (f" The sketch held a circle at ({cx:g}, {cy:g}) and no point, and "
                "CATIA's Hole drills at the SKETCH ORIGIN in that case - so a "
                f"construction point was added at the circle's centre and the bore "
                "follows it.", target)

    def _add_locating_point(self, part: Any, sketch: Any, x: float,
                            y: float) -> str | None:
        """Add a construction point at ``(x, y)`` in a closed sketch.

        ``Factory2D.CreatePoint`` makes a construction point, which is what this
        is: a locator. Returns the failure reason, or None on success.
        """
        try:
            factory = sketch.OpenEdition()
        except Exception as exc:  # noqa: BLE001 - reported, then checked later
            return f"the sketch would not reopen for editing: {str(exc)[:80]}"
        try:
            point = factory.CreatePoint(float(x), float(y))
            try:
                point.Construction = True
            except Exception:  # noqa: BLE001 - construction is the default
                pass
            sketch.CloseEdition()
            part.Update()
        except Exception as exc:  # noqa: BLE001
            try:
                sketch.CloseEdition()
            except Exception:  # noqa: BLE001
                pass
            return f"drawing the point failed: {str(exc)[:80]}"
        return None

    def _bore_off_target(self, before: Any, cog_before: Any, after: Any,
                         cog_after: Any, target: Any, placement: Any,
                         diameter: float) -> str | None:
        """A sentence when the bore landed away from the profile, else None.

        The removed volume alone cannot tell a bore in the right place from one
        in the wrong place - both remove pi*r^2*h. The centroid of the removed
        material can: ``V0*G0 - V1*G1 = v*g``. When it disagrees with the sketch
        by more than a quarter of the bore's own radius, the feature is wrong and
        the caller has to hear about it.
        """
        if target is None:
            return None
        point, amplification = _removed_centroid(before, cog_before, after, cog_after)
        if point is None or amplification is None:
            return None
        off = _in_plane_distance(point, target, placement)
        if off is None:
            return None
        # Past this ratio the COG reading is amplified beyond usefulness: the
        # centroid of a 0.1 mm3 nick in a 10 kg part is arithmetic, not geometry.
        if amplification > 500:
            return None
        tolerance = max(0.5, 0.25 * float(diameter))
        if off <= tolerance:
            return None
        return (
            "The material was removed around (%.3f, %.3f, %.3f), but the "
            "sketch's circle is centred on (%.3f, %.3f, %.3f) - %.3f mm away in "
            "the sketch plane (tolerance %.2f mm). CATIA's Hole feature takes the "
            "bore's axis from a POINT in the sketch, so a stray point or a "
            "missing one moves the bore while the removed volume stays right."
            % (point[0], point[1], point[2], target[0], target[1], target[2],
               off, tolerance)
        )

    def _bore_position_note(self, before: Any, cog_before: Any, after: Any,
                            cog_after: Any, target: Any, placement: Any,
                            diameter: float) -> str:
        """One sentence saying the bore's position was checked, or why not."""
        if target is None:
            return (" The bore's position was NOT checked: this sketch was not "
                    "drawn in this session, so where its profile sits is unknown "
                    "- CATIA's Hole drills at the sketch origin when the profile "
                    "carries no point.")
        point, amplification = _removed_centroid(before, cog_before, after, cog_after)
        if point is None:
            return (" The bore's position was NOT checked: the centre of gravity "
                    "could not be read before and after the cut.")
        off = _in_plane_distance(point, target, placement)
        if off is None:
            return ""
        if amplification is not None and amplification > 500:
            return (" The bore's position could not be checked: the cut is "
                    f"{amplification:.0f}x smaller than the body, so the centre-of-"
                    "gravity shift is not precise enough to locate it.")
        return (" Bore checked against the material removed: centred on "
                "(%.3f, %.3f, %.3f), %.3f mm from the profile's circle."
                % (point[0], point[1], point[2], off))

    def _apply_threading(self, part: Any, hole: Any, threaded: bool) -> str:
        """Put the hole in the threading mode asked for, and say what happened.

        Two facts measured on B30, 2026-11-05, decide the shape of this method:

        * ``CatHoleThreadingMode`` is 0 = catThreadedHoleThreading and 1 =
          catSmoothHoleThreading, read out of the Hole interface's OWN type
          library. This file wrote 1 with ``# catThreaded`` beside it from 0.3.0
          through 0.3.61, so every hole asked for as threaded came out SMOOTH and
          was reported as created. Nothing caught it because 1 is also what a hole
          in a fresh part already carries: the write was a no-op on a property
          that was never read back.
        * A hole INHERITS the mode of the hole before it in the same part. Three
          holes built in a row read 0 / 0 / 0 after the first was threaded, and
          the two plain ones had never been touched. So ``threaded: false`` has to
          be WRITTEN too - doing nothing leaves the previous bore's thread on this
          one.

        The caller must have updated the hole once already; threading a hole that
        has not materialized breaks the rebuild.
        """
        wanted_mode = 0 if threaded else 1
        try:
            current_mode = hole.ThreadingMode
        except Exception:  # noqa: BLE001 - unreadable means "cannot tell, write it"
            current_mode = None

        note = ""
        if current_mode != wanted_mode:
            try:
                hole.ThreadingMode = wanted_mode
            except Exception as error:  # noqa: BLE001
                if threaded:
                    # The hole EXISTS and is a valid plain bore: nothing to roll
                    # back, and `modified` is a measured TRUE, not "unknown".
                    # It used to be a bare RuntimeError, i.e. COM_ERROR /
                    # modified:null / "inspect the model" for a call whose result
                    # the reply itself could describe (reported 2026-11-06).
                    raise CodedError(
                        f"Hole '{hole.Name}' was created as a SMOOTH bore: CATIA "
                        f"refused the threading-mode write "
                        f"({str(error).splitlines()[0][:110]}). This build does not "
                        "let this server thread a hole through ThreadingMode alone. "
                        f"Delete it with catia_delete_feature('{hole.Name}') if you "
                        "did not want a plain bore.",
                        code="FEATURE_INCOMPLETE",
                        stage="execute",
                        modified=True,
                        next_actions=[
                            f"Delete it with catia_delete_feature('{hole.Name}') "
                            "and build the thread in CATIA by hand, or keep it if a "
                            "plain bore is what you wanted - the bore is valid, "
                            "the thread is missing",
                        ],
                    ) from error
                # A plain hole that keeps the previous bore's thread is worse than
                # a plain hole, but it is still a hole: say so rather than throw.
                note = (
                    " WARNING: this bore is SMOOTH as asked, but CATIA refused the "
                    "write that would have made it so and the hole it copied may "
                    "have carried a thread - check the feature in CATIA."
                )
            else:
                try:
                    part.Update()
                except Exception as error:  # noqa: BLE001
                    if threaded:
                        # Measured on B32 (2026-11-06, 5/5): the write lands, the
                        # update after it fails, and the hole stays in the tree
                        # with no geometry - a zombie that poisons every later
                        # Update. So it is removed here, and `modified` says which
                        # of the two happened instead of `null`.
                        removed = False
                        try:
                            part.Selection.Clear()
                            part.Selection.Add(hole)
                            part.Selection.Delete()
                            part.Selection.Clear()
                            removed = True
                        except Exception:  # noqa: BLE001 - the caller is told
                            removed = False
                        failure = CodedError(
                            f"Hole '{hole.Name}' had its threading mode set but the "
                            f"part update after it failed "
                            f"({str(error).splitlines()[0][:110]}). The bore has no "
                            "geometry in the part"
                            + (" and the feature was removed again, so the tree is "
                               "as it was before this call."
                               if removed else
                               ". The feature is STILL IN THE TREE and every later "
                               "update in this part may fail until it is deleted."),
                            code="FEATURE_INCOMPLETE",
                            stage="execute",
                            modified=not removed,
                            next_actions=(
                                ["Retry the bore without threading (threaded=false) "
                                 "- the thread is what this build refused"]
                                if removed else
                                [f"Delete it now with catia_delete_feature("
                                 f"'{hole.Name}') before building anything else"]),
                        )
                        # The envelope publishes `rolled_back`; a caller branching
                        # on it must not be told "unknown" about a feature this
                        # method just removed (or failed to).
                        failure.rolled_back = removed
                        raise failure from error
                    note = (
                        " The threading mode was reset to smooth but the part update "
                        "after it raised; verify the bore in CATIA."
                    )

        if threaded:
            note = (
                " THREADED: CATIA reports threading mode 0, which is "
                "catThreadedHoleThreading. Pitch and standard are the hole's own "
                "parameters and this tool sets neither - they are whatever CATIA "
                "defaults to for this diameter. The next hole in this part inherits "
                "this mode, so pass threaded: false explicitly for a plain bore."
            )
        return note

    def _bore_length_note(self, removed: float, diameter: float, depth: float,
                          through_all: bool, hole_type: Any,
                          threaded: Any) -> str:
        """How long the bore actually is, in mm.

        ``depth`` is measured from the SKETCH PLANE, which is not the face the
        bore breaks out of unless the sketch sits on that face. A caller who puts
        the sketch on a datum plane and offsets it - the route this server's own
        error messages recommend - gets ``distance + depth`` and no warning, so
        the reply converts the volume it measured into the length it drilled
        (reported 2026-10-27: depth 5 in a 20 mm block drilled 15 mm).
        """
        if hole_type not in (None, "simple") or threaded:
            return ""
        area = math.pi * (float(diameter) / 2.0) ** 2
        if area <= 0 or removed <= 0:
            return ""
        length = removed / area
        if through_all:
            return (f" Bore length {length:.2f} mm - through all, so that is the "
                    "wall it crossed.")
        if abs(length - float(depth)) <= 0.05:
            return f" Bore length {length:.2f} mm."
        return (f" Bore length {length:.2f} mm, not {float(depth):g} mm: 'depth' is "
                f"measured from the SKETCH PLANE, which sits {length - float(depth):.2f} "
                "mm from the face the bore broke out of, so the two add up.")

    def _bore_axis_sentence(self, target: Any, placement: Any) -> str:
        """Why a bore that removed nothing may simply have started in air."""
        where = ""
        base = str((placement or {}).get("base") or "")
        if base:
            distance = float((placement or {}).get("distance") or 0.0)
            where = f" on plane '{base}' at offset {distance:g}"
        return (
            " A bore also removes nothing when the material is on the FAR side of "
            f"the sketch plane: the bore always runs along the sketch normal, and "
            f"this sketch sits{where or ' on a face'}. catia_hole has no reverse "
            "argument - CATIA's Hole feature does not expose one (DirectionOrientation,"
            " which Pad and Pocket use, does not exist on it; measured on B30 "
            "2026-10-27) - so put the sketch on the face you want to drill from, or "
            "pass through_all, which drills the full wall from either side."
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
            raise ArgumentRejected(
                "Pass both 'dir1_edge' and 'dir2_edge' ('Edge.N' from "
                "catia_list_edges, two adjacent edges sharing a corner), "
                "or neither."
            )
        if (dir1_axis or dir2_axis) and (dir1_edge or dir2_edge):
            raise ArgumentRejected(
                "Use either dir1_axis/dir2_axis or dir1_edge/dir2_edge, not both. "
                "The axis form is the readable one."
            )
        for value in (dir1_axis, dir2_axis):
            if value and value not in ("x", "y", "z"):
                raise ArgumentRejected("dir1_axis / dir2_axis must be 'x', 'y' or 'z'")
        if dir1_axis and dir2_axis and dir1_axis == dir2_axis:
            raise ArgumentRejected(
                "dir1_axis and dir2_axis are the same axis; CATIA rejects two "
                "parallel directions, and so does this tool."
            )
        reverse1 = bool(args.get("reverse_dir1"))
        reverse2 = bool(args.get("reverse_dir2"))
        if dir1_axis or dir2_axis:
            # A plane reference drives a DIFFERENT axis in each slot, both
            # backwards. Measured on B30 2026-11-08 by reading the body's box
            # ENDPOINTS after each call (a `dimensions` delta cannot see a sign,
            # which is why 0.3.77's audit passed a mirrored pattern):
            #   slot 1 -> the plane's H axis, negated   (PlaneXY -> -X)
            #   slot 2 -> the plane's V axis, negated   (PlaneXY -> -Y)
            # so dir1_axis='x' needs PlaneXY and dir2_axis='y' needs PlaneXY too,
            # while 'z' in slot 2 needs PlaneYZ. The two tables are not the same.
            planes = self.conn.get_origin_elements()
            by_h = {"x": "xy", "y": "yz", "z": "zx"}
            by_v = {"x": "zx", "y": "xy", "z": "yz"}
            # CATIA needs dir1 x dir2 to be RIGHT-handed, or the update fails with
            # a bare "UpdateObject failed". Measured on B30 2026-10-24 with a pad
            # seed: (x,y) ok, (y,z) ok, (z,x) ok, (z,y) failed, (x,z) failed -
            # the failures are exactly the left-handed pairs, and (z) alone took
            # the same path because the old default second axis was 'y'.
            cyclic = {"x": "y", "y": "z", "z": "x"}
            first = dir1_axis or "x"
            second = dir2_axis or cyclic[first]
            if second == first:
                raise ArgumentRejected(
                    f"dir1_axis and dir2_axis are both '{first}'; a pattern needs two "
                    "different directions."
                )
            # Right-handed or nothing: CATIA answers a left-handed AXIS PAIR with
            # a bare "UpdateObject failed". Measured on B30 2026-10-24: (x,y),
            # (y,z) and (z,x) build; (x,z), (z,y) and (y,x) do not. Rather than
            # refuse the caller's axes, swap the two slots (counts and spacings
            # with them) - the resulting geometry is identical.
            #
            # A REVERSAL is not part of that test, which 0.3.21 got wrong by
            # ANDing `reverse1 == reverse2` in: reversing one direction made the
            # tool swap the axes, the reversed axis landed in dir2, and
            # AddNewRectPattern then failed at update. Measured on B30
            # 2026-10-26: `reverse_dir1` with no axis builds (-1005.31 mm3),
            # `dir1_axis:"x" + reverse_dir1` was rolled back, and reversing BOTH
            # sides built again. So `(x-, y+)` is legal; only the axis pair
            # decides handedness.
            right_handed = (first, second) in (("x", "y"), ("y", "z"), ("z", "x"))
            swapped = False
            if not right_handed:
                first, second = second, first
                reverse1, reverse2 = reverse2, reverse1
                d1_count, d2_count = d2_count, d1_count
                d1_spacing, d2_spacing = d2_spacing, d1_spacing
                swapped = True
            ref1 = part.CreateReferenceFromObject(planes[by_h[first]])
            ref2 = part.CreateReferenceFromObject(planes[by_v[second]])
            directions = (f"axis {first}{'-' if reverse1 else '+'} -> dir1, "
                          f"axis {second}{'-' if reverse2 else '+'} -> dir2")
            if swapped:
                directions += " (axes swapped internally: CATIA rejects a left-handed pair)"
            # Both slots run the NEGATIVE of the reference's axis on this build,
            # so 'axis x+' put the copies at -X until this line existed (reported
            # 2026-11-08, N1). Flipping the flags keeps the label honest: the
            # caller asked for a sign, and the sign is now what the geometry does.
            reverse1, reverse2 = not reverse1, not reverse2
        elif dir1_edge:
            r1, r2 = topology_references(
                self.conn, body, "Edge",
                [self._topo_index(dir1_edge, "Edge"),
                 self._topo_index(dir2_edge, "Edge")])
            ref1, ref2 = r1.reference, r2.reference
            directions = f"{dir1_edge} -> dir1, {dir2_edge} -> dir2"
        else:
            # The default pair keeps its geometry (PlaneYZ, PlaneZX) so nothing
            # an existing caller depends on moves, but the sentence now states
            # the MEASURED directions: slot 1 takes PlaneYZ's H axis negated =
            # -Y, slot 2 takes PlaneZX's V axis negated = -X (B30 2026-11-08,
            # endpoints). It used to promise "dir1 (X), dir2 (Y)" - two errors in
            # one sentence, and the third time this line has been rewritten.
            planes = self.conn.get_origin_elements()
            ref1 = part.CreateReferenceFromObject(planes["yz"])
            ref2 = part.CreateReferenceFromObject(planes["zx"])
            directions = ("origin PlaneYZ -> dir1 (-Y), PlaneZX -> dir2 (-X) - "
                          "measured; pass dir1_axis/dir2_axis to choose the axes "
                          "AND their signs instead of relying on this default")

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
            raise PreconditionRejected("No features to pattern in the active body.")
        if feature_name:
            try:
                return shapes.Item(feature_name)
            except Exception:
                names = ", ".join(str(shapes.Item(i).Name)
                                  for i in range(1, shapes.Count + 1))
                raise ArgumentRejected(
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
            raise ArgumentRejected(
                "count must be at least 1",
                next_actions=[
                    "Pass count >= 1 - a pattern of 0 instances is not a pattern. "
                    "The model was read to prepare the pattern and not written, "
                    "so nothing needs repairing",
                ],
            )
        angular_spacing = args.get("angular_spacing")
        if angular_spacing is None:
            angular_spacing = 360.0 / count
        axis = (args.get("axis") or "z").lower()
        if axis not in ("x", "y", "z"):
            raise ArgumentRejected("axis must be 'x', 'y' or 'z'")
        center = args.get("center") or [0.0, 0.0, 0.0]
        if len(center) != 3:
            raise ArgumentRejected("center must be [x, y, z] in mm")
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
        # The last flag is not a cosmetic option: it is what makes a copy TURN.
        # With False, CATIA puts every instance at the right angle and leaves its
        # body parallel to the seed - the volume is still count x seed, so both
        # the reply and the docs used to call that a success. Reported
        # 2026-10-30: an 8-blade fan, bbox Z +-305 where 8-fold symmetry needs
        # +-500. Measured on B30 the same day: the same call with True gives
        # +-500, and nothing else changes.
        radius_aligned = args.get("radius_aligned")
        radius_aligned = True if radius_aligned is None else bool(radius_aligned)
        # The direction of travel, which used to be hardcoded. Measured on B30
        # 2026-11-02 with a 4x4 pad seeded at (30, 0): +90 deg about Z puts the
        # copy at (0, -30), i.e. CLOCKWISE, which is the opposite of the
        # right-hand rule a caller reads off "axis z" (reported 2026-10-31, #7 -
        # the only workaround was a negative angular_spacing). `reverse` asks for
        # the other way without changing the default that other models rely on.
        reversed_axis = bool(args.get("reverse", False))
        pattern = sf.AddNewCircPattern(
            feature,
            1, count,              # radial copies, angular copies
            0.0, angular_spacing,  # radial step, angular step (deg)
            1, 1,                  # position of the original
            center_ref, axis_ref,  # rotation center (from axis line), axis
            reversed_axis,         # reversed rotation axis
            0.0,                   # rotation angle
            radius_aligned,        # rotate each copy with the pattern
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
            gone = self._remove_feature(part, pattern, body)
            raise CodedError(
                "CATIA rejected the circular pattern"
                + (" and it was removed from the tree" if gone else
                   " (the dead feature could NOT be deleted - close the document "
                   "without saving)")
                + f". The seed feature and the axis line '{self._PATTERN_GEOSET}' "
                "are untouched. Usual cause: the instance would overlap material "
                "the seed already removed, or 'count' exceeds what fits in "
                f"angular_spacing ({angular_spacing} deg). Original error: "
                f"{update_error}"
            , code="FEATURE_REJECTED", stage="execute") from update_error

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
        # Which way the copies went, stated rather than implied: the measured
        # default is clockwise about the named axis, and a caller who assumed
        # the right-hand rule got a mirror image without a word of warning.
        if count > 1:
            detail += (" Copies go " + ("the other way round from the right-hand rule"
                                        if reversed_axis else
                                        "CLOCKWISE about the named axis, as measured "
                                        "- pass reverse=true for the other direction")
                       + ".")
        if radius_aligned:
            orientation = (
                " Each copy is rotated with the pattern (radius_aligned=true), so "
                "an asymmetric seed comes out 8-fold symmetric for count=8 - check "
                "that with the bounding box, not the volume: the volume only "
                "proves the copies exist.")
        else:
            orientation = (
                " Each copy is placed at its angle but NOT rotated "
                "(radius_aligned=false), so it stays parallel to the seed. The "
                "volume is still count x seed and cannot show this - if the copies "
                "were supposed to point outward, re-run with radius_aligned=true "
                "and delete this feature.")
        return (
            f"Circular pattern '{pattern.Name}' created: {count} instances "
            f"({count - 1} added), {angular_spacing} deg spacing around the "
            f"{axis.upper()} axis through {center}.{detail}{orientation}"
        )

    def _user_pattern(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        feature = self._pattern_seed(body, args.get("feature_name"))
        count = int(args["count"])
        if count < 1:
            raise ArgumentRejected(
                "count must be at least 1",
                next_actions=[
                    "Pass count >= 1 - a pattern of 0 instances is not a pattern. "
                    "The model was read to prepare the pattern and not written, "
                    "so nothing needs repairing",
                ],
            )
        sketch_name = args.get("sketch_name")
        if not sketch_name:
            # Field-proven trap: the last item of body.Sketches is stale right
            # after CloseEdition and resolves to the previous sketch, so the
            # points sketch must be addressed by name.
            raise ArgumentRejected(
                "catia_user_pattern requires 'sketch_name': the points sketch "
                "name reported by catia_close_sketch (e.g. 'Sketch.3')."
            )
        sketch = self._get_last_sketch(sketch_name)

        before = self._body_volume_mm3(part, body)
        part.InWorkObject = body
        # Field-proven on V5R20: AddNewUserPattern(shape, n) then
        # AddFeatureToLocatePositions(points sketch). A locating point that
        # coincides with the seed makes the update fail (0x80020009).
        # How many points the sketch actually has. `count` is passed to CATIA
        # and then ignored: measured on B30 2026-11-02, a 1000 mm3 pocket and a
        # 3-point sketch removed 4000 mm3 - four pockets - with count=1, 3 and 5
        # alike. The reply used to repeat the argument back ("5 instances
        # placed") while the tree held four, which is how a caller ends up
        # explaining a "2x" volume to himself (reported 2026-10-31, #6).
        points = None
        try:
            elements = sketch.GeometricElements
            total = int(elements.Count)
            # The sketch carries its own absolute axis as one of those elements
            # (measured on B30 2026-11-02: three points read as 4 elements, the
            # first named "绝对轴"), so the axis is subtracted by name. If the
            # names are not readable the count stays unknown rather than off by
            # one - a wrong instance count is the whole bug being fixed here.
            axes = 0
            for index in range(1, total + 1):
                name = str(getattr(elements.Item(index), "Name", ""))
                if "轴" in name or "AXIS" in name.upper():
                    axes += 1
            points = total - axes
        except Exception:  # noqa: BLE001 - the count is a courtesy, not the result
            points = None

        pattern = sf.AddNewUserPattern(feature, count)
        pattern.AddFeatureToLocatePositions(sketch)
        self._safe_update(part, pattern)
        self.conn.refresh_display()
        after = self._body_volume_mm3(part, body)
        effect = ""
        if before is not None and after is not None:
            effect = f" Volume {before:.1f} -> {after:.1f} mm3."
        if points is None:
            note = (f" The sketch's point count could not be read, so the number "
                    f"of copies is not stated - CATIA places one per locating "
                    f"point, and 'count' ({count}) does not decide it.")
            summary = f"instances placed on the points of sketch '{sketch.Name}'."
        else:
            summary = (f"{points} cop{'y' if points == 1 else 'ies'} placed on the "
                       f"{points} point(s) of sketch '{sketch.Name}', plus the "
                       f"original.")
            note = ("" if points == count else
                    f" CATIA ignores 'count' here: the sketch's {points} locating "
                    f"point(s) decide, so the pattern made {points} cop"
                    f"{'y' if points == 1 else 'ies'}, not {count}. Nothing was "
                    f"rolled back - this is the shape of the feature, not a "
                    f"failure.")
        return f"User pattern '{pattern.Name}' created: " + summary + effect + note

    def _mirror(self, args: dict[str, Any]) -> str:
        """Mirror the body about a plane - and refuse the case that lies.

        WHAT THIS CALL ACTUALLY DOES (measured on B30 2026-11-18): it is
        ``ShapeFactory.AddNewMirror(planeRef)``, which has no seed parameter and
        produces "the whole current shape, mirrored, UNIONED with the body". On a
        60x40x50 box with a 20x40 pocket (volume 80000 mm3) a mirror about xy
        answers 160000: a second copy below z=0, added.

        That is a real CATIA operation - Transform > Mirror on a body - and it is
        the right thing when a caller has modelled half a symmetric part. It is
        WRONG, and silently so, when the body's material is a CUT that is not
        symmetric about the plane: the mirror image has solid exactly where the cut
        removed it, and the union puts the material back. The 2026-11-17 report hit
        that on a groove:

            after the groove      26228730.962 mm3
            after mirror(xy)      42188262.4593 mm3   <- the pre-groove volume

        The tool reported success. So a cut seed is refused here rather than
        answered with a wrong number (the reporter asked for exactly this).

        The seed routes were re-read on B30 2026-11-22 because the 2026-11-22
        report asked whether another one existed, and the answer is no - with
        three measurements instead of one: ``Object`` and ``MirroringObject``
        both raise "can not be set" (only ``MirroringPlane`` is writable);
        ``AddNewMirror`` accepts a feature reference as readily as a plane and
        mirrors the whole body either way (121 000 -> 242 000); and
        ``AddObjectToMirror`` appends to a list that already holds the body, with
        nothing to remove it (112 000 -> 224 000, and 232 000 -> 240 000 on a
        symmetric body, where the mirrored material FILLS the off-centre pocket).

        ``allow_body_mirror=true`` says "I know, I want the body operation" and is
        what the old behaviour is now behind.
        """
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        plane_key = str(args["plane"]).lower()
        planes = self.conn.get_origin_elements()
        if plane_key not in planes:
            raise ArgumentRejected(f"Unknown plane '{plane_key}'. Use 'xy', 'yz', or 'zx'.")

        shapes = body.Shapes
        count = int(shapes.Count)
        if count == 0:
            raise ArgumentRejected(
                f"Body '{body.Name}' holds no Part Design feature yet, so there is "
                "nothing to mirror. Build the geometry first. Nothing was created.",
                code="EMPTY_BODY", stage="validate")
        last_shape = shapes.Item(count)
        last_name = str(getattr(last_shape, "Name", "") or "")
        last_kind = (self._feature_kind_resolved(part, last_shape)
                     if last_shape is not None else "none")

        seed = str(args.get("feature") or "").strip()
        allow = bool(args.get("allow_body_mirror"))
        names = []
        for index in range(1, count + 1):
            names.append(str(shapes.Item(index).Name))
        if seed:
            if seed not in names:
                raise ArgumentRejected(
                    f"No feature named '{seed}' in body '{body.Name}'. It holds: "
                    + (", ".join(names) or "nothing") + ". Nothing was created.",
                    code="FEATURE_NOT_FOUND", stage="validate")
            seed_kind = self._feature_kind_resolved(
                part, shapes.Item(names.index(seed) + 1))
            raise CodedError(
                f"A mirror of the single feature '{seed}' ({seed_kind}) is not "
                "expressible through this API, and that is measured rather than "
                "inferred from one read-only property (B30 2026-11-22): "
                "AddNewMirror takes ONE argument and mirrors the WHOLE BODY "
                "whichever reference is passed - a plane reference and a feature "
                "reference both took a 121 000 mm3 body to 242 000; "
                "AddObjectToMirror(reference) is accepted but only APPENDS to a "
                "list that already holds the body, and nothing removes the body "
                "(112 000 -> 224 000); and on a body symmetric about the plane it "
                "FILLS an off-centre pocket (232 000 -> 240 000). Mirroring a CUT "
                "this way is the exact opposite of what it looks like. Nothing was "
                "created.",
                code="MIRROR_SEED_UNSUPPORTED",
                stage="validate",
                modified=False,
                data={"requested_feature": seed, "kind": seed_kind,
                      "body": str(body.Name), "features": names},
                next_actions=[
                    "Build the cut twice instead: a second sketch on the mirrored "
                    "frame, then the same feature again. The 2026-11-17 report did "
                    "exactly that for its two annular cavities - frame u negated, "
                    "second catia_groove(360) - and landed on 9855303.187 mm3 "
                    "against a 9855329.47 source (-0.00027%)",
                    "the profile you need to redraw is now readable per element: "
                    "catia_sketch_get_geometry carries 'drawn' (x1,y1,x2,y2 and the "
                    "kind) on every element this session drew, so the mirrored "
                    "copy can be typed from numbers instead of from memory. Negate "
                    "the coordinate that runs across the mirror plane",
                    "catia_sketch_copy puts a recorded profile on another plane "
                    "unchanged; it has no mirror option, so negate the coordinate "
                    "in the copy arguments rather than expecting it to flip",
                    "allow_body_mirror=true mirrors the WHOLE body and unions it: "
                    "right for completing a symmetric half built from pads, wrong "
                    "for duplicating a cut",
                ])
        if last_kind in self._CUT_KINDS and not allow:
            before, note = self._volume_probe(part, body)
            raise CodedError(
                f"REFUSED: the body's last feature is '{last_name}' ({last_kind}), "
                "a cut. This tool mirrors the WHOLE body and unions the copy with "
                "it, so the mirror image's material lands exactly where the cut "
                "removed it - on B30 2026-11-18 a box with a 20x40 pocket (80000 "
                "mm3) answered 160000 after a mirror about xy, and the 2026-11-17 "
                "report's groove went 26228730.962 -> 42188262.4593 (the pre-groove "
                "volume) with the tool reporting success. "
                + (f"Right now this body is {before:.4f} mm3. " if before else "")
                + "Nothing was created.",
                code="MIRROR_WOULD_FILL_CUT",
                stage="validate",
                modified=False,
                data={"last_feature": last_name, "kind": last_kind,
                      "body": str(body.Name), "volume_mm3": before,
                      "volume_note": note, "features": names},
                next_actions=[
                    "Mirror the FEATURE by building it twice: a second sketch on "
                    "the mirrored frame, then the same pocket/groove. The "
                    "2026-11-17 report reached -0.00027% of its source volume that "
                    "way",
                    "allow_body_mirror=true if you really want Transform > Mirror "
                    "on the whole body (it will put material back into the cut)",
                ])

        mirror_plane = planes[plane_key]
        ref = part.CreateReferenceFromObject(mirror_plane)
        before, before_note = self._volume_probe(part, body)
        # THE ENVELOPE IS READ BEFORE THE COPY EXISTS. Measured on B30
        # 2026-11-18: read afterwards it always straddles the plane, because the
        # mirrored copy is the part below z=0 - which turned the report's own
        # one-sided body into a false "suspect".
        envelope = self._vertex_extent(part, body, {"xy": 2, "yz": 0,
                                                   "zx": 1}[plane_key])
        mirror = sf.AddNewMirror(ref)
        self._safe_update(part, mirror)
        after, after_note = self._volume_probe(part, body)
        self.conn.refresh_display()
        if before is None or after is None:
            volume_sentence = (" The volume could not be read on both sides of the "
                               f"call ({before_note or after_note}), so whether the "
                               "copy was added is not verified.")
        else:
            delta = after - before
            if abs(delta) < 1e-6:
                # A MIRROR THAT CHANGED NOTHING IS NOT A FEATURE WORTH KEEPING.
                # The 2026-11-18 report (R5) mirrored a symmetric body, was told
                # "added nothing", and still had a 镜像.1 in the tree to delete by
                # hand. So the node this call created goes again, and the reply
                # says the tree is back where it started.
                removed = self._discard_feature(part, mirror)
                return (
                    f"Mirror about {plane_key.upper()} plane: the body "
                    f"('{body.Name}') is already symmetric about this plane, so the "
                    f"operation added nothing - volume {before:.4f} mm3 before and "
                    "after (no_change: true). "
                    + ("The mirror feature this call created was DELETED again, so "
                       "the tree is unchanged." if removed else
                       f"The feature '{mirror.Name}' could NOT be removed and is "
                       "still in the tree; delete it with catia_delete_feature. "
                       "The geometry is unchanged either way.")
                    + " If you wanted ONE feature mirrored, this is not that "
                      "operation - see the tool description for the two-call route.")
            volume_sentence = (
                f" Volume {before:.4f} -> {after:.4f} mm3 (delta {delta:+.4f}): "
                "the whole body was mirrored and the copy added to it, which is "
                "Transform > Mirror - not a mirror of one feature.")
            if before and delta >= before - max(1e-6, abs(before) * 1e-9):
                # EVERY mm3 CAME BACK. That is the number the 2026-11-18 report
                # (P1) read as "the overlapping solid was counted twice", and it
                # is not: a body the plane cuts through does NOT double - measured
                # on B30 2026-11-18, a symmetric 40x40x20 pad plus a 20x20x10 pad
                # on one side went 36 000 -> 40 000 (+4 000, only what moved),
                # while a body sitting entirely on one side went 4 000 -> 8 000.
                # So the envelope is measured, and the reply says which of the two
                # it found instead of leaving a suspicious number unexplained.
                volume_sentence += self._duplication_note(envelope, plane_key)
        return (
            f"Mirror created about {plane_key.upper()} plane: the WHOLE body "
            f"('{body.Name}', last feature '{last_name}' = {last_kind}) was mirrored "
            f"and the copy added to it. Feature: '{mirror.Name}'." + volume_sentence
            + " If you wanted ONE feature mirrored, this is not that operation - "
            "see the tool description for the two-call route that is."
        )

    def _duplication_note(self, extent: Any, plane_key: str) -> str:
        """"Every mm3 was added again" - and whether that is possible at all.

        The numbers: a mirror about a plane of a body that has material on BOTH
        sides cannot double, because the copy overlaps the original, and CATIA's
        answer was measured to be the union (36 000 -> 40 000 mm3 on B30
        2026-11-18). A doubling therefore means the body sits on one side. That
        is read from its vertices rather than assumed, and when the vertices say
        "both sides" the volume is called out as suspect instead of published as
        a measurement.
        """
        axis_index = {"xy": 2, "yz": 0, "zx": 1}.get(plane_key)
        axis_name = "xyz"[axis_index] if axis_index is not None else "?"
        if extent is None:
            return (" EVERY mm3 was added again (delta = the volume before the call). "
                    "Its vertex envelope could not be read, so whether the copy "
                    "overlaps the original is NOT verified - measure the volume "
                    "again with catia_get_inertia before relying on it.")
        low, high = extent
        if low > 1e-6 or high < -1e-6:
            return (f" EVERY mm3 was added again (delta = the volume before the "
                    f"call), which is what a disjoint copy gives: the body's "
                    f"vertices run {axis_name} {low:.4f} .. {high:.4f}, all on one "
                    "side of the plane, so the copy cannot overlap it.")
        if low < -1e-6 and high > 1e-6:
            return (f" EVERY mm3 was added again (delta = the volume before the "
                    f"call) - TREAT THE VOLUME AS SUSPECT. The body's vertices run "
                    f"{axis_name} {low:.4f} .. {high:.4f}, so it has material on "
                    "both sides of the plane and its mirror image must overlap it; "
                    "a union cannot then give 2x (measured on B30 2026-11-18: "
                    "36 000 -> 40 000 mm3, not 72 000). Re-measure with "
                    "catia_get_inertia and check the tree with catia_list_features.")
        return (f" EVERY mm3 was added again (delta = the volume before the call); "
                f"the body sits on one side of the plane and only touches it "
                f"(vertices {axis_name} {low:.4f} .. {high:.4f}), so the copy meets "
                "the original along that contact and 2x is the union's volume.")

    def _vertex_extent(self, part: Any, body: Any,
                       axis_index: int | None) -> tuple[float, float] | None:
        """Min and max of the solid's vertex coordinates along one world axis."""
        if axis_index is None:
            return None
        try:
            from catia_mcp.tools.measurement import batch_vertex_sweep
        except Exception:  # noqa: BLE001 - the caller reports "not verified"
            return None
        selection = self.conn.hso
        try:
            spa = part.Parent.GetWorkbench("SPAWorkbench")
            selection.Clear()
            selection.Add(body)
            search_topology(selection, "Vertex")
            got = batch_vertex_sweep(self.conn.app, selection, spa)
        except Exception:  # noqa: BLE001 - reported as "not verified"
            return None
        finally:
            try:
                selection.Clear()
            except Exception:  # noqa: BLE001
                pass
        if not got:
            return None
        points = got[0]
        values = [point[axis_index] for point in points
                  if point is not None and len(point) > axis_index]
        if not values:
            return None
        return min(values), max(values)

    def _discard_feature(self, part: Any, feature: Any) -> bool:
        """Delete a feature this call just created, and say whether it went.

        Used where the operation turned out to be a no-op (a mirror of a body that
        is already symmetric): the node would otherwise sit in the tree until
        somebody noticed it. Measured on B30 2026-11-18 - the selection route
        deletes it and the update after it succeeds, the same route
        catia_delete_feature takes.
        """
        try:
            selection = self.conn.hso
            selection.Clear()
            selection.Add(feature)
            selection.Delete()
            selection.Clear()
        except Exception:  # noqa: BLE001 - the caller reports what happened
            return False
        try:
            part.Update()
        except Exception:  # noqa: BLE001 - the caller reports what happened
            return False
        return True

    def _shell(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        thickness = float(args["thickness"])
        external = float(args.get("external_thickness") or 0.0)
        if thickness < 0:
            raise ArgumentRejected(
                f"'thickness' cannot be negative, got {thickness}. CATIA's Shell "
                "offsets INWARD by this amount and rejects a negative one at "
                "update with a message that looks like a bad feature name. To put "
                "material OUTSIDE the surface use 'external_thickness'; to make "
                "the wall purely outward pass thickness=0 with external_thickness>0."
            )
        if external < 0:
            raise ArgumentRejected("'external_thickness' cannot be negative.")
        if thickness == 0 and external == 0:
            raise ArgumentRejected(
                "'thickness' and 'external_thickness' are both 0, so the shell "
                "would have no wall at all. Give one of them a value."
            )
        if thickness == 0 and external > 0:
            # Pure outward: the wall grows outside the original surface and the
            # inside stays hollow. Asked for by a part whose tolerances are
            # measured on the outside (reported 2026-10-26, whose workaround was
            # thickness=0.001 + external=5 and left a 10.4 mm3 inner membrane on a
            # 10 400 mm2 wall - right answer, wrong semantics).
            pass
        faces = args.get("faces_to_remove") or []
        if not faces:
            raise ArgumentRejected(
                "catia_shell requires 'faces_to_remove' with at least one "
                "indexed face name ('Face.N' from catia_list_faces): "
                "AddNewShell's first parameter is the face to remove."
            )
        # The volume on both sides of the cut, like every other feature here.
        # shell was the one Part Design tool that answered without it, so a caller
        # verifying "a 5 mm outward wall on a 60x40x40 box" (the case that is
        # easiest to get wrong, since the outer surface is what grows) had to make
        # a second call to catia_get_inertia (reported 2026-10-28, §7.3).
        before, volume_note = self._volume_probe(part, body)
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
        after, _after_note = self._volume_probe(part, body)
        if before is not None and after is not None:
            gain = after - before
            note += (f" Volume {before:.1f} -> {after:.1f} mm3 "
                     f"(delta {gain:+.1f}).")
        elif volume_note:
            note += (f" The volume could not be measured ({volume_note}), so the "
                     "wall is reported on CATIA's return alone.")
        return (
            f"Shell created: {self._shell_wall_text(thickness, external)}, removed "
            f"{len(faces) - len(skipped)} face(s){note} Feature: '{shell.Name}'"
        )

    @staticmethod
    def _shell_wall_text(thickness: float, external: float) -> str:
        """Say which side(s) the wall grew on, because "0.0 mm wall" is nonsense.

        A purely outward shell with thickness=0 built 61 500 mm3 on a 96 000 mm3
        box (the analytic 70x50x45 minus 60x40x40, measured 2026-10-26) and the
        reply described it as "0.0 mm wall".
        """
        if thickness and external:
            return f"{thickness:g} mm inward + {external:g} mm outward wall"
        if external:
            return f"{external:g} mm OUTWARD wall (the original surface stays hollow)"
        return f"{thickness:g} mm inward wall"

    def _draft(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        angle = args["angle"]
        face_name = args.get("face_name")
        if not face_name:
            raise ArgumentRejected(
                "catia_draft requires 'face_name' ('Face.N' from "
                "catia_list_faces): the face to draft."
            )
        face_ref = self._topo_reference("Face", self._topo_index(face_name, "Face"))

        plane_key = args.get("pulling_direction", "xy").lower()
        planes = self.conn.get_origin_elements()
        if plane_key not in ("xy", "yz", "zx") or not planes.get(plane_key):
            raise ArgumentRejected(f"Unknown pulling direction plane: {plane_key}")

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
                raise PreconditionRejected(
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
            gone = self._remove_feature(part, draft, body)
            errors.append(f"{neutral_name}: {update_error}"
                          + ("" if gone else " (and the dead feature could NOT be "
                                              "deleted - close the document without "
                                              "saving)"))
        self.conn.refresh_display()
        raise CodedError(
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
        , code="FEATURE_REJECTED", stage="execute")

    def _thickness(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        offset = args["offset"]
        face_name = args.get("face_name")
        if not face_name:
            raise ArgumentRejected(
                "catia_thickness requires 'face_name' ('Face.N' from "
                "catia_list_faces): the face to thicken."
            )
        face_ref = self._topo_reference("Face", self._topo_index(face_name, "Face"))

        # AddNewThickness(iFaceToThicken, iOffset) - 2 parameters.
        thickness = sf.AddNewThickness(face_ref, offset)

        self._safe_update(part, thickness)
        self.conn.refresh_display()
        return f"Thickness added: {offset} mm on {face_name}. Feature: '{thickness.Name}'"

    def _element_names(self, part: Any) -> list[str]:
        """Every name this tool could delete, in tree order.

        Bodies' features, their sketches, the hybrid shapes in each body and the
        geometrical sets (nested ones included) - the same four places
        ``_delete_one`` searches, so a pattern can never match something the
        delete then cannot reach. Names are NOT de-duplicated here: two elements
        may share one (that is the documented duplicate-name case), and the
        expansion has to see both or it would silently delete one.
        """
        names: list[str] = []

        def add(node: Any) -> None:
            try:
                name = str(getattr(node, "Name", "") or "")
            except Exception:  # noqa: BLE001 - an unreadable node has no name
                return
            if name:
                names.append(name)

        try:
            bodies = part.Bodies
            for index in range(1, int(bodies.Count) + 1):
                body = bodies.Item(index)
                add(body)
                for collection in ("Shapes", "Sketches", "HybridShapes"):
                    try:
                        items = getattr(body, collection)
                        for position in range(1, int(items.Count) + 1):
                            add(items.Item(position))
                    except Exception:  # noqa: BLE001 - that collection is absent
                        continue
        except Exception:  # noqa: BLE001 - no body list, no expansion
            return names

        def walk(holder: Any) -> None:
            add(holder)
            try:
                shapes = holder.HybridShapes
                for position in range(1, int(shapes.Count) + 1):
                    add(shapes.Item(position))
            except Exception:  # noqa: BLE001 - an empty set
                pass
            try:
                inner = holder.HybridBodies
                for position in range(1, int(inner.Count) + 1):
                    walk(inner.Item(position))
            except Exception:  # noqa: BLE001 - a set without children
                pass

        try:
            sets = part.HybridBodies
            for index in range(1, int(sets.Count) + 1):
                walk(sets.Item(index))
        except Exception:  # noqa: BLE001 - a part without geometrical sets
            pass
        return names

    def _expand_name_patterns(self, part: Any, names: list[str]
                              ) -> tuple[list[str], list[str]]:
        """``names`` with every ``*``/``?`` entry replaced by what it matches.

        Returns ``(expanded, notes)``; the notes are printed in the reply so the
        caller sees the expansion. A pattern that matches NOTHING is refused:
        deleting "nothing" successfully is how a caller comes to believe a
        cleanup happened (2026-11-23 report, P1-3 - the names it had written down
        were from before the rebuild, and three of four no longer existed).
        """
        if not any("*" in name or "?" in name for name in names):
            return list(names), []
        candidates = self._element_names(part)
        expanded: list[str] = []
        notes: list[str] = []
        for name in names:
            if "*" not in name and "?" not in name:
                expanded.append(name)
                continue
            hits = [candidate for candidate in dict.fromkeys(candidates)
                    if fnmatch.fnmatchcase(candidate, name)]
            if not hits:
                sample = ", ".join(dict.fromkeys(candidates))[:300]
                raise ArgumentRejected(
                    f"'{name}' matches no element in this part. A pattern is "
                    "expanded against the tree as it is NOW ('*' any run of "
                    "characters, '?' one), so a name written down before a "
                    "rebuild can match nothing - and deleting nothing "
                    "successfully would read as a cleanup that happened. Nothing "
                    f"was deleted. The part holds: {sample}",
                    code="PATTERN_MATCHED_NOTHING", stage="execute",
                    modified=False,
                    next_actions=[
                        "catia_list_features shows the names that exist now",
                    ])
            expanded.extend(hits)
            notes.append(f"'{name}' matched {len(hits)} element(s): "
                         + ", ".join(hits[:8])
                         + (f", ... ({len(hits) - 8} more)" if len(hits) > 8 else ""))
        # One name twice would delete twice; the batch already reports how many
        # OBJECTS went, so duplicates here would make that count a lie.
        return list(dict.fromkeys(expanded)), notes

    def _edit_pad(self, args: dict[str, Any]) -> str:
        """Change the depth of a feature that already exists.

        The gap the 2026-11-23 report (P1-4) hit hardest: a pad built 5 mm too
        deep, 15 features later, and no route but deleting it - which breaks
        everything after it - so the part was rebuilt twice.

        What this build allows, measured on B30 2026-11-23: ``FirstLimit`` and
        ``SecondLimit`` answer a ``Dimension`` whose ``Value`` reads AND writes
        (a 25x45 block padded to 80 and then set to 20 gave 22500 mm3, exactly
        25*45*20), ``IsSymmetric`` reads. What it does NOT allow:
        ``Pad.Direction``, ``Pad.Reversed``, ``Pad.Symmetric``, ``Pad.Offset``,
        ``FirstLimit.Type`` all raise ``Item.X``, so the direction of an existing
        pad cannot be read, let alone flipped - that is still a rebuild, and the
        reply says so instead of pretending otherwise.

        THE UNDO IS REAL. A value that CATIA accepts and then fails to rebuild
        leaves the feature in error, so the old numbers are written back and the
        part updated again before the refusal goes out; ``modified`` is false only
        when that restore succeeded (``_restore_limits``).
        """
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        name = str(args.get("feature_name") or "").strip()
        if not name:
            raise ArgumentRejected(
                "catia_edit_pad needs 'feature_name' - the feature whose depth is "
                "wrong, e.g. '凸台.1' (catia_list_features lists them). Nothing "
                "was changed.",
                code="BAD_ARGUMENT", stage="validate", modified=False)
        wanted_first = args.get("height")
        wanted_second = args.get("second_height")
        wanted_symmetric = args.get("symmetric")
        if wanted_first is None and wanted_second is None and wanted_symmetric is None:
            raise ArgumentRejected(
                "catia_edit_pad was given nothing to change: pass 'height' "
                "(the first limit's depth), 'second_height' (a two-sided feature's "
                "other side) or 'symmetric'. Nothing was changed.",
                code="BAD_ARGUMENT", stage="validate", modified=False)
        for label, value in (("height", wanted_first), ("second_height", wanted_second)):
            if value is None:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ArgumentRejected(
                    f"'{label}' has to be a number of millimetres; got {value!r}. "
                    "Nothing was changed.",
                    code="BAD_ARGUMENT", stage="validate") from exc
            if number <= 0:
                raise ArgumentOutOfRange(
                    f"'{label}' must be greater than 0; got {number:g}. A zero "
                    "depth is not a feature - delete it instead.",
                    code="DEPTH_OUT_OF_RANGE", stage="validate")
            if label == "height":
                wanted_first = number
            else:
                wanted_second = number

        feature = None
        try:
            shapes = body.Shapes
            for index in range(1, int(shapes.Count) + 1):
                candidate = shapes.Item(index)
                if str(getattr(candidate, "Name", "") or "") == name:
                    feature = candidate
                    break
        except Exception:  # noqa: BLE001 - unreadable body, no claim
            feature = None
        if feature is None:
            available = []
            try:
                shapes = body.Shapes
                for index in range(1, int(shapes.Count) + 1):
                    available.append(str(shapes.Item(index).Name))
            except Exception:  # noqa: BLE001 - a list is a courtesy
                available = []
            raise ArgumentRejected(
                f"No feature named '{name}' in '{body.Name}'. "
                f"catia_list_features shows what is there"
                + (f" ({', '.join(available[:12])}"
                   + (f", ... ({len(available) - 12} more)" if len(available) > 12 else "")
                   + ")" if available else "")
                + ". Nothing was changed.",
                code="FEATURE_NOT_FOUND", stage="execute", modified=False)

        first_value, first_dimension = self._limit_dimension(feature, "FirstLimit")
        second_value, second_dimension = self._limit_dimension(feature, "SecondLimit")
        if wanted_first is not None and first_dimension is None:
            raise ArgumentRejected(
                f"'{name}' has no FirstLimit dimension to change - it is not a "
                "depth-based feature (a fillet, a chamfer, a pattern or a boolean "
                "answers nothing here). Measured on B30 2026-11-23: pads, pockets, "
                "shafts and grooves do. Nothing was changed.",
                code="NOT_A_LIMIT_FEATURE", stage="execute", modified=False)
        if wanted_second is not None and second_dimension is None:
            raise ArgumentRejected(
                f"'{name}' has no SecondLimit dimension - it is a one-sided "
                "feature, so there is no second depth to set. Pass 'height' "
                "instead. Nothing was changed.",
                code="NOT_A_LIMIT_FEATURE", stage="execute", modified=False)

        before = self._mass_probe(part, body)[0]
        old_symmetric = None
        if wanted_symmetric is not None:
            try:
                old_symmetric = bool(feature.IsSymmetric)
            except Exception as error:  # noqa: BLE001 - no flag, no change
                raise ArgumentRejected(
                    f"'{name}' does not answer IsSymmetric "
                    f"({str(error).splitlines()[0][:80]}), so it cannot be set "
                    "here. Nothing was changed.",
                    code="NOT_A_LIMIT_FEATURE", stage="execute",
                    modified=False) from error

        changes: list[str] = []
        try:
            if wanted_first is not None:
                first_dimension.Value = wanted_first
                changes.append(f"first limit {self._mm_text(first_value)} -> "
                               f"{self._mm_text(wanted_first)} mm")
            if wanted_second is not None:
                second_dimension.Value = wanted_second
                changes.append(f"second limit {self._mm_text(second_value)} -> "
                               f"{self._mm_text(wanted_second)} mm")
            if wanted_symmetric is not None:
                feature.IsSymmetric = bool(wanted_symmetric)
                changes.append(f"IsSymmetric {old_symmetric} -> "
                               f"{bool(wanted_symmetric)}")
            part.UpdateObject(feature)
        except Exception as error:  # noqa: BLE001 - undo and report
            restored = self._restore_limits(part, feature, first_dimension,
                                            first_value, second_dimension,
                                            second_value, old_symmetric)
            raise CodedError(
                f"'{name}' refused the change ({str(error).splitlines()[0][:140]}). "
                + ("The old values were written back and the part rebuilt, so "
                   "nothing was changed."
                   if restored else
                   "The old values were written back but the part did NOT rebuild "
                   "cleanly - inspect it with catia_list_features before building "
                   "anything else.")
                + f" Tried: {'; '.join(changes)}.",
                code="EDIT_FEATURE_FAILED",
                modified=not restored, stage="execute",
                data={"feature": name,
                      "first_limit_before_mm": first_value,
                      "second_limit_before_mm": second_value,
                      "attempted": changes,
                      "restored": restored}) from error

        after = self._mass_probe(part, body)[0]
        new_first, _ = self._limit_dimension(feature, "FirstLimit")
        new_second, _ = self._limit_dimension(feature, "SecondLimit")
        read_back = (f"Feature now reads {self._mm_text(new_first)} mm on the first "
                     f"limit and {self._mm_text(new_second)} mm on the second")
        delta_note = ""
        if before is not None and after is not None:
            moved = after - before
            depths = [abs((wanted_first if wanted_first is not None else first_value)
                          - (first_value if first_value is not None else 0.0))]
            if wanted_second is not None and second_value is not None:
                depths.append(abs(wanted_second - second_value))
            depth_change = max(depths) if depths else 0.0
            delta_note = (f" Volume {before:.4f} -> {after:.4f} mm3 "
                          f"({moved:+.4f}).")
            if depth_change > 1e-9:
                # The MAGNITUDE is the area; the sign belongs to the volume and
                # would mislead here (shrinking a pad is a legitimate negative).
                delta_note += (f" That is {abs(moved) / depth_change:.4f} mm2 of "
                               "area per millimetre of depth changed - a pad's "
                               "sketch area answers the same number, and the sign "
                               "of the volume change is just which way it moved.")
        self.conn.refresh_display()
        return (f"'{name}' changed: {'; '.join(changes)}. {read_back}."
                + delta_note
                + " The DIRECTION of an existing feature cannot be read or "
                "changed on this build (measured 2026-11-23: Pad.Direction, "
                "Pad.Reversed, Pad.Symmetric and FirstLimit.Type all raise "
                "Item.X) - a feature that points the wrong way still has to be "
                "rebuilt, but its depth no longer does.")

    @staticmethod
    def _mm_text(value: float | None) -> str:
        return "?" if value is None else f"{value:g}"

    @staticmethod
    def _limit_dimension(feature: Any, limit: str
                         ) -> tuple[float | None, Any]:
        """``(value, Dimension)`` of a feature's limit, ``(None, None)`` if absent."""
        try:
            dimension = getattr(feature, limit).Dimension
            return float(dimension.Value), dimension
        except Exception:  # noqa: BLE001 - the feature has no such limit
            return None, None

    def _restore_limits(self, part: Any, feature: Any,
                        first_dimension: Any, first_value: float | None,
                        second_dimension: Any, second_value: float | None,
                        old_symmetric: bool | None) -> bool:
        """Put the old depths back and rebuild; True only if that worked."""
        try:
            if first_dimension is not None and first_value is not None:
                first_dimension.Value = first_value
            if second_dimension is not None and second_value is not None:
                second_dimension.Value = second_value
            if old_symmetric is not None:
                feature.IsSymmetric = old_symmetric
            part.UpdateObject(feature)
            return True
        except Exception:  # noqa: BLE001 - reported by the caller
            return False

    def _delete_feature(self, args: dict[str, Any]) -> str:
        """Delete one feature, or several when 'names' is given.

        The batch form exists because cleaning up after a rebuild took fifteen
        calls of 2.0-2.4 s each in the 2026-11-03 acceptance session - about 30 s
        of waiting for a job that is one request.
        """
        self.conn.ensure_connected()
        # A NAMED DOCUMENT IS SWITCHED TO, and that is a side effect on the
        # session (reported 2026-11-22, P1-17). It is switched rather than reached
        # around because every step below - dry run included - resolves names
        # through the ACTIVE part; a delete that silently resolved them in the
        # wrong tree would remove something the caller never named.
        _, switched = self._target_part(args.get("document"),
                                        activate=not args.get("dry_run"))
        if switched and not args.get("dry_run"):
            self._document_switched = switched
        if args.get("dry_run"):
            return self._delete_dry_run(args)
        many = args.get("names")
        if isinstance(many, list) and many:
            if args.get("feature_name") or args.get("index") is not None or args.get("last"):
                raise ArgumentRejected(
                    "'names' deletes several elements at once; do not also pass "
                    "'feature_name', 'index' or 'last'. Pass the list alone, e.g. "
                    '{"names": ["ZZ_sp90", "ZZ_pts"], "cascade": true}.'
                )
            many = [str(item) for item in many]
            part = self.conn.get_active_part()
            # A PATTERN IS EXPANDED AGAINST THE LIVE TREE, HERE AND ONLY HERE
            # (2026-11-23 report, P1-3). Feature names do not survive a rebuild:
            # the same four fillets came back as 倒圆角.13/.14/.18/.19, then
            # .37/.38/.42, then .61/.62/.66/.67 - so a list written before the
            # rebuild cannot be used after it, and the report had to spend a
            # round trip on catia_list_features before every cleanup. A name with
            # * or ? is now matched against the elements that ACTUALLY exist, and
            # what it matched is printed - the caller sees the expansion instead
            # of trusting it.
            many, pattern_notes = self._expand_name_patterns(part, many)
            # GSD elements go in ONE selection: on a 636-point clean-up the
            # per-name route took 49.9 s for 91 elements (0.3.50, measured on
            # B30 2026-11-03), because each name re-walked the whole tree and
            # then updated the part. Whatever is not in a geometrical set is a
            # Part Design feature and still goes through _delete_one, which
            # knows about bodies and indexes.
            # THE AXES OF EVERY SKETCH IN THE BATCH, READ BEFORE ANYTHING GOES.
            # 2026-11-23 report (P0-3): a failed plan rolled back with names that
            # included a sketch WHOSE FEATURE was also in the list, so CATIA
            # cascaded the sketch - and a cascaded sketch never reaches
            # _delete_one, which is the only place its axes were captured. The
            # frame cleanup then had no datum to compare, 'auto' refused by
            # design ("unknown must not delete"), and the _FRAME + 3 points
            # survived a rollback that was supposed to take everything back.
            # Reading them here costs one script call per sketch that actually
            # HAS a frame scaffold, and only on this path.
            self._capture_sketch_datums(part, many)
            found = self._map_geometrical_sets(part, set(many))
            gsd_names = [name for name in many if name in found]
            others = [name for name in many if name not in found]
            deleted, kept = self._delete_owned(gsd_names) if gsd_names else ([], [])
            done: list[str] = list(deleted)
            failed: list[str] = [f"'{name}' (could not be deleted)" for name in kept]
            failed_names: list[str] = list(kept)
            removed_features: list[str] = []
            # A SKETCH TAKEN BY A FEATURE EARLIER IN THE SAME CALL IS NOT A
            # FAILURE. The first B30 acceptance run of this batch form
            # (v0464, 2026-11-18) asked for ['凸台.2', 'SK_Q'], the pad went and
            # cascaded the sketch, and the reply read "Deleted 1 element(s) for 2
            # name(s) given. Failed (1): 'SK_Q' (No feature ... named 'SK_Q')" -
            # a complete success reported as a partial one, immediately after a
            # reply that had just recommended this exact call.
            sketches_alive = set(self._sketch_names())
            went_with: dict[str, str] = {}
            cascaded: list[str] = []
            for name in self._batch_order(others):
                try:
                    # NO UPDATE PER NAME. The 2026-11-13 report (P0-1) deleted a PAIR
                    # of one-sided pockets in one batch and got, after the first of
                    # the two, "Feature '凹槽.1' deleted, but the part update failed
                    # afterwards ... 8 elements fail on their own ... delete those
                    # too". The model was fine: 凹槽.2 was still there and referenced
                    # the feature that had just gone, so the update between the two
                    # deletes could not succeed - and the reply read a one-step
                    # in-between state as a verdict on the model, then told the
                    # caller to delete six healthy downstream features with it.
                    # Deleting the whole list and updating ONCE removes the state
                    # that produced that sentence.
                    done.append(self._delete_one(args, name, defer_update=True,
                                                 also=set(many)))
                    removed_features.append(name)
                    try:
                        after = set(self._sketch_names())
                    except Exception:  # noqa: BLE001 - no list, no bookkeeping
                        after = sketches_alive
                    for gone in sketches_alive - after:
                        went_with.setdefault(gone, name)
                    sketches_alive = after
                except Exception as error:  # noqa: BLE001 - reported per name
                    first = str(error).splitlines()[0]
                    if name in went_with and "No feature, sketch, GSD element" in first:
                        cascaded.append(f"'{name}' (taken with "
                                        f"'{went_with[name]}')")
                        continue
                    failed.append(f"'{name}' ({first[:120]})")
                    failed_names.append(name)
            # Kept OUT of `done`: that list is counted as elements removed, and a
            # sentence about the update is not one.
            scaffold_note = ""
            # A SKETCH TAKEN BY ITS OWN FEATURE NEEDS THE SAME CLEANUP. The first
            # version of this loop only walked `removed_features`, so the very
            # call the reply recommends for a consumed sketch - names=["凸台.1",
            # "SK"] - cascaded the sketch, left its `_FRAME` + 3 points in the
            # tree and said NOTHING: `quiet=True` swallowed the refusal and the
            # cascade meant no cleanup was even attempted (measured 2026-11-21
            # while writing the P2-15 acceptance cell). Cascaded sketches have no
            # captured axes (`_deleted_sketch_datums` only knows what THIS call
            # deleted directly), so 'auto' refuses on them by design - which is
            # exactly what the sentence below has to say.
            gone_sketches = list(removed_features) + [
                name for name in went_with if name not in removed_features]
            if gone_sketches:
                for name in gone_sketches:
                    scaffold_note += self._cleanup_after_sketch(
                        name, args.get("cleanup_frame_datum", "auto"),
                        update=False, quiet=True,
                        datum=self._deleted_sketch_datums.pop(name, None))
                # WHAT IS STILL THERE, said out loud (2026-11-21 report P2-15).
                # `quiet=True` above suppresses every refusal, so a batch that
                # deleted a frame sketch and left its datum+3 points said nothing
                # at all - the caller found out by looking at the tree. The
                # inventory is read AFTER the deletes, so it reports what actually
                # remained rather than what this code believes it decided.
                leftovers = self._frame_leftovers(gone_sketches)
                if leftovers:
                    listed = ", ".join(f"'{owner}' ({count} element(s))"
                                       for owner, count in sorted(leftovers.items()))
                    owner = sorted(leftovers)[0]
                    # The by-name route is the ONLY one left once the sketch is
                    # gone: its name no longer resolves, so a second call cannot
                    # pass it again with cleanup_frame_datum=true.
                    by_name = (f"catia_delete_feature(names=['{owner}_FRAME', "
                               f"'{owner}_FRAME.O', '{owner}_FRAME.U', "
                               f"'{owner}_FRAME.V'])")
                    scaffold_note += (
                        f" Frame scaffolding stayed in the tree for "
                        f"{len(leftovers)} of the deleted or cascaded sketch(es): "
                        f"{listed}. "
                        "'auto' will not take a datum that another sketch may be "
                        "standing on (measured on B30 2026-11-18: deleting it "
                        "leaves that sketch unsupported and the part's update "
                        "failing), and it will not take one whose axes were never "
                        "read - a sketch that went with its feature has no axes on "
                        "record. They are NAMED, so they can be removed without the "
                        f"sketch: {by_name}. cleanup_frame_datum=true on this call "
                        "would have forced it.")

            update_note = (self._final_update_note(removed_features)
                           if removed_features else "")
            # A NAME THAT IS GONE IS NOT A FAILURE (2026-11-23 report, P0-3 item
            # 3). The batch collects a refusal per name as it goes, and the frame
            # cleanup runs AFTERWARDS - so a call could delete the sketch, clean
            # up its `_FRAME` + 3 points, and still list all four as "could not
            # be deleted" in the same reply that said the sketch went. The tree
            # is the arbiter, and it has just been walked for the leftovers: a
            # name that is no longer there did not fail, it was taken by
            # something else this call did.
            quietly_gone = 0
            if failed:
                alive = set(self._element_names(part))
                # AN EMPTY WALK IS NOT AN EMPTY PART. If the tree cannot be read,
                # nothing is dropped from the failure list - "unknown" must not
                # turn a refusal into a success, which is the same rule the frame
                # cleanup applies to a datum it cannot compare.
                if alive:
                    survivors = [(text, name)
                                 for text, name in zip(failed, failed_names)
                                 if name in alive]
                    quietly_gone = len(failed) - len(survivors)
                    failed = [text for text, _name in survivors]
                    failed_names = [name for _text, name in survivors]
            # `deleted` counts OBJECTS, so a name that two elements carried
            # contributes two entries: saying "1 of 1" after removing two of them
            # would be the same kind of half-truth this batch exists to avoid.
            duplicates = [name for name in gsd_names if len(found.get(name, [])) > 1]
            summary = (f"Deleted {len(done) + len(cascaded) + quietly_gone} "
                       f"element(s) for {len(many)} name(s) given.")
            if quietly_gone:
                summary += (f" {quietly_gone} of them went with a feature this "
                            "call deleted rather than by name (the frame "
                            "scaffolding of a deleted sketch, or a sketch its own "
                            "feature cascaded) - they were refused when they were "
                            "asked for and are gone now, so they are not counted "
                            "as failures.")
            if cascaded:
                summary += (" Also gone, taken by a feature deleted above: "
                            + ", ".join(cascaded) + ".")
            if duplicates:
                summary += (" Duplicate names removed in full: " + ", ".join(
                    f"'{name}' x{len(found[name])}" for name in duplicates) + ".")
            if failed:
                summary += f" Failed ({len(failed)}): " + "; ".join(failed) + "."
            if pattern_notes:
                # The expansion is part of the answer: a caller who wrote
                # '倒圆角.*' has to see WHICH four features that meant, because
                # the names it would have guessed are not the ones in the tree.
                summary = ("Expanded " + "; ".join(pattern_notes) + ". "
                           + summary)
            return summary + " " + " ".join(done) + scaffold_note + update_note
        return self._delete_one(args, None)

    def _delete_dry_run(self, args: dict[str, Any]) -> str:
        """What a delete WOULD take with it, without deleting anything.

        The 2026-11-19 report deleted a middle feature and broke every feature
        after it, then worked around it by deleting in reverse order - a rule it
        had to discover. Two of those consequences can be named exactly before
        anything is touched:

        * a FEATURE whose profile is a sketch - the delete takes the sketch with
          it (measured on B30 2026-11-18: "CATIA also removed 1 sketch(es)");
        * a SKETCH that a feature consumes - deleting it is refused outright.

        What is NOT claimed: which downstream features will fail. This tool has
        no dependency graph, so it lists the features that come LATER in the tree
        and says they are candidates, not casualties. That is a superset, and it
        is labelled as one.
        """
        self.conn.ensure_connected()
        body = self.conn.get_active_part_body()
        many = args.get("names")
        if isinstance(many, list) and many:
            targets = [str(item) for item in many]
        elif args.get("feature_name"):
            targets = [str(args["feature_name"])]
        else:
            raise ArgumentRejected(
                "dry_run=true needs the names to plan for: pass 'feature_name' "
                "or 'names' (the index/last shortcuts resolve to whatever is "
                "last, which is the accident this preview exists to prevent). "
                "Nothing was deleted.",
                code="BAD_ARGUMENT", stage="validate", modified=False)
        # A PATTERN PREVIEWS WHAT IT WOULD TAKE, and the expansion is the point -
        # a dry run that answered for the literal string '*.*' would be worse
        # than no preview at all (2026-11-23 report, P1-3).
        targets, pattern_notes = self._expand_name_patterns(
            self.conn.get_active_part(), targets)
        shapes = body.Shapes
        order = {str(shapes.Item(i).Name): i
                 for i in range(1, int(shapes.Count) + 1)}
        sketches = set(self._sketch_names())
        lines: list[str] = []
        for target in targets:
            index = order.get(target)
            if target in sketches:
                consumers, _unasked = self._sketch_consumers(target)
                if consumers:
                    lines.append(
                        f"'{target}' is a SKETCH that is the profile of "
                        f"{', '.join(consumers)} - deleting it would be REFUSED "
                        "(SKETCH_CONSUMED_BY). Delete the feature instead and the "
                        "sketch goes with it.")
                else:
                    mine = [entry for entry in
                            self._frame_scaffold_inventory(body).get("sketches", [])
                            if entry["sketch"] == target]
                    extra = (f" Its {mine[0]['elements']} frame-datum element(s) go "
                             "too, subject to cleanup_frame_datum."
                             if mine else "")
                    lines.append(f"'{target}' is a SKETCH with no consuming "
                                 f"feature; it would be deleted.{extra}")
                continue
            if index is None:
                lines.append(f"'{target}' is not in this body's feature list - "
                             "nothing here can say what it would take with it "
                             "(a geometrical-set element is not walked).")
                continue
            try:
                profile = str(shapes.Item(index).Sketch.Name)
            except Exception:  # noqa: BLE001 - not a sketch-based feature
                profile = ""
            later = [name for name, position in sorted(order.items(),
                                                       key=lambda pair: pair[1])
                     if position > index]
            piece = f"'{target}' is a feature (#{index})."
            if profile:
                piece += (f" It would ALSO remove the sketch '{profile}', because "
                          "CATIA cascades a feature's profile with it.")
            if later:
                piece += (f" Later in the tree ({len(later)}): "
                          f"{', '.join(later[:8])}"
                          f"{', ...' if len(later) > 8 else ''} - CANDIDATES, not "
                          "casualties: this tool has no dependency graph, and a "
                          "feature that consumes this one fails its update.")
            lines.append(piece)
        return ("DRY RUN - nothing was deleted, created or updated. "
                + ("Expanded " + "; ".join(pattern_notes) + ". "
                   if pattern_notes else "")
                + " ".join(lines))

    def _delete_one(self, args: dict[str, Any], forced: str | None,
                    defer_update: bool = False,
                    also: set[str] | None = None) -> str:
        """Delete one named element. ``defer_update`` leaves Part.Update to a caller.

        The batch form deletes a whole list and updates ONCE at the end: an update
        between two halves of a pair (two one-sided pockets, a feature and the
        fillet on it) cannot succeed, and reading that in-between failure as a
        verdict on the model is what produced the destructive advice of 0.3.122
        (reported 2026-11-13, P0-1).

        ``also`` is the rest of that list. A sketch whose consuming feature is
        being deleted in the SAME call may go: the feature delete takes it, and
        refusing would make the one sensible batch ('delete the pad and the
        sketch') impossible.
        """
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        target = forced or args.get("feature_name")
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
                raise ArgumentRejected(
                    "catia_delete_feature was called without 'feature_name' or "
                    f"'index', and the shortcut would delete {would}. That is the "
                    "LAST CREATED feature, which on a long tree is usually not the "
                    "one you meant. Pass 'feature_name', 'index', or "
                    "last=true to mean this one."
                )
            if shapes.Count == 0:
                raise PreconditionRejected("The active body has no features to delete.")
            target = str(shapes.Item(shapes.Count).Name)
        if not target:
            shapes = self.conn.get_active_part_body().Shapes
            index = int(args["index"])
            if index < 1 or index > shapes.Count:
                raise ArgumentOutOfRange(
                    f"index {index} is out of range: the active body has "
                    f"{shapes.Count} feature(s)."
                )
            target = str(shapes.Item(index).Name)
        feature = None
        kind = "feature"
        # (features asked, features that would not answer) - filled in only for a
        # sketch, and read by the delete-failure sentence below. Initialised so
        # that sentence can never depend on a branch having run.
        sketch_scan = (0, 0)
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
            # GSD elements are not in a body's Shapes: they live in HybridBodies,
            # which this search never entered, so every failed loft / bridging /
            # extracted curve answered "No feature or sketch named ..." and stayed
            # in the tree - visible, and therefore measured by the STL envelope
            # (reported 2026-10-29, §4). Sets nest, so the walk recurses.
            feature, kind, same_named = self._find_in_geometrical_sets(part, target)
            if len(same_named) > 1:
                # Deleting "one of them" is not a delete the caller can reason
                # about: which one CATIA picks is not stated anywhere, and the
                # survivor keeps the name, so the next call by name still lands
                # on an element the caller believes is gone (measured on B30
                # 2026-11-03: two elements named C_P, one delete, one C_P left).
                raise ArgumentRejected(
                    f"{len(same_named)} elements in this part are named '{target}' "
                    f"({', '.join(kind for _obj, kind in same_named)}), so deleting "
                    f"'by name' would remove an arbitrary one of them and leave the "
                    f"rest under the same name - every later call by name would "
                    f"still find an element you believe was deleted. CATIA allows "
                    f"duplicate names and setting Name does not prevent them. "
                    f"THERE IS A ROUTE: catia_delete_feature with "
                    f"names=['{target}'] deletes EVERY element carrying that name "
                    f"and says how many it took, so use that to clear the "
                    f"duplicates, then build again with a name that is free."
                )
        if feature is None:
            # A BODY IS NOT INSIDE A BODY, so this search cannot see it - and the
            # reply used to say only "No feature ... named X" for an empty body
            # sitting right there in the tree. The 2026-11-16 report was blocked
            # by exactly that: a body left behind by a failed loft could be seen
            # with catia_list_bodies and not removed, and the answer read as if
            # the name did not exist. The tool for it has existed all along
            # (catia_delete_body); what was missing was the pointer to it.
            body = None
            try:
                for position in range(1, int(bodies.Count) + 1):
                    candidate = bodies.Item(position)
                    if str(candidate.Name) == target:
                        body = candidate
                        break
            except Exception:  # noqa: BLE001 - no readable body list, no claim
                body = None
            if body is not None:
                try:
                    held = int(body.Shapes.Count)
                except Exception:  # noqa: BLE001 - unknown, so do not claim a count
                    held = -1
                raise ArgumentRejected(
                    f"'{target}' is a solid BODY, not a feature - bodies are not "
                    "inside a body, so this tool's search cannot reach it, and that "
                    "is why every earlier call answered 'No feature named'. "
                    + (f"It holds {held} feature(s). " if held > 0 else
                       "It is empty. " if held == 0 else "")
                    + "Use catia_delete_body with the same name"
                    + ("" if held == 0 else " and force=true if those features are "
                                            "meant to go with it")
                    + ", or catia_set_active_body if you meant to build in it. "
                    "Nothing was deleted.",
                    code="BODY_NOT_FEATURE", stage="execute", modified=False,
                    data={"body": target, "features_held": None if held < 0 else held},
                    next_actions=[f"catia_delete_body(name='{target}'"
                                  + (")" if held <= 0 else ", force=true)")])
            raise ArgumentRejected(
                f"No feature, sketch, GSD element or body named '{target}' in the "
                "active part - searched every body's feature list, its sketches, "
                "every geometrical set (nested ones included), and the body list. "
                "Use catia_list_features to see feature names; 'sketches' and "
                "'geometrical_sets' list the rest."
            )
        # Deleting a solid feature takes its support sketch with it (CATIA
        # cascades), and the old reply said nothing about it - the reporter lost
        # 草图.3 with 凹槽.1 and only found out on the next pocket call.
        before = self._sketch_names()
        # THE MODAL DIALOG IS PREVENTED HERE, NOT EXPLAINED AFTERWARDS. Deleting
        # the sketch this session has open in the editor makes CATIA put up a
        # message box ("剪切数据时发生错误 / 无法删除正在编辑的草图") and the COM
        # call does not return until a human dismisses it - the session is frozen
        # for as long as nobody is at the keyboard (2026-11-23 report, P0-3: a
        # run_plan rollback hung there). This used to be diagnosed in the
        # `except` below, i.e. after the dialog. Asking first costs one attribute
        # read and the dialog never appears.
        if kind == "sketch":
            open_sketch = getattr(self.conn, "open_sketch", None)
            if open_sketch and str(open_sketch) == str(target):
                raise CodedError(
                    f"SKETCH_IS_OPEN: '{target}' is the sketch this session is "
                    "editing, and CATIA will not delete a sketch while it is "
                    "open - it answers with a MODAL dialog that no automation can "
                    "dismiss, so the call would hang until someone clicks OK. "
                    "Nothing was deleted. Close it with catia_close_sketch and "
                    "send the same call again (measured on B30 2026-11-12: that is "
                    "all it takes). Do NOT close or re-open the document: that "
                    "loses the sketch and anything not saved.",
                    code="SKETCH_IS_OPEN", stage="execute", modified=False,
                    next_actions=[
                        "catia_close_sketch, then catia_delete_feature again with "
                        "the same name",
                    ],
                )
        # THE PLANE HAS TO BE READ NOW. After the delete there is nothing left to
        # ask, and the frame cleanup needs to know whether a surviving sketch was
        # standing on this datum or merely somewhere in the same body.
        self._deleted_sketch_datums.pop(target, None)
        if kind == "sketch":
            # A SKETCH A FEATURE CONSUMES IS NOT A SKETCH THIS TOOL MAY DELETE.
            # Measured both ways on B30 2026-11-18: CATIA sometimes refuses
            # (「方法 Delete 失败」, which the old reply read as a dead reference
            # and sent the reporter round an update-retry loop that could not
            # help), and sometimes accepts and leaves the consuming feature in
            # error (probe v0462: 凸台.1 then fails every later update). Both are
            # worse than not trying, and the Part Design features answer
            # ``.Sketch`` - 凸台.1 -> 'SK_A' - so this can be asked first.
            consumers, unasked = self._sketch_consumers(target)
            sketch_scan = (len(unasked) + len(consumers), len(unasked))
            still = [name for name in consumers
                     if not (also and name in also)]
            if still:
                raise ArgumentRejected(
                    f"SKETCH_CONSUMED_BY: '{target}' is the profile of "
                    f"{' and '.join(repr(name) for name in still)}. CATIA does "
                    "not let a consumed sketch go on its own - measured both "
                    "ways on B30 2026-11-18: it can refuse the delete outright, "
                    "and it can accept it and leave the feature in error so that "
                    "every later update stops there. Either way the sketch is "
                    f"not the thing to delete. Nothing was deleted. Delete "
                    f"{' or '.join(repr(name) for name in still)} instead - the "
                    "sketch goes with it - or pass both names in one call "
                    "('names'), which removes the feature and takes the sketch "
                    "with it.",
                    code="SKETCH_CONSUMED_BY", stage="execute", modified=False,
                    data={"sketch": target, "consumed_by": still,
                          "features_asked": len(unasked) + len(consumers)},
                    next_actions=[
                        f"catia_delete_feature(feature_name='{still[0]}')",
                    ],
                )
            self._deleted_sketch_datums[target] = self._sketch_axes(feature)
        sel = self.conn.hso
        try:
            sel.Clear()
            sel.Add(feature)
            sel.Delete()
        except Exception as error:  # noqa: BLE001 - the diagnostic is the point
            # THE ELEMENT WAS FOUND BUT CATIA WOULD NOT TAKE IT. Two different
            # things do this and the reply used to assert one of them:
            #
            #   * the sketch is OPEN in edit mode - CATIA refuses outright, and
            #     closing it makes the same delete work (measured 2026-11-12:
            #     DELETE_FAILED while editing 草图.1, "Deleted sketch '草图.1'"
            #     immediately after catia_close_sketch). This is the common one:
            #     the 2026-11-12 report hit it by drawing text and deleting the
            #     sketch without closing it, and was told it had a dead reference
            #     and should consider re-opening the document - advice that costs
            #     the caller their work.
            #   * a feature that failed its update leaves a dead reference the
            #     tree still lists and the selection cannot take (measured
            #     2026-11-12, three calls after a pad reported UNVERIFIED).
            #
            # Which one it is can be read when this session opened the sketch:
            # InWorkObject stays on the body while a sketch is in edit mode, so
            # the skim of the COM tree does not answer it - CATIAConnection
            # carries the name instead.
            self.conn.refresh_display()
            open_sketch = getattr(self.conn, "open_sketch", None)
            if open_sketch and str(open_sketch) == str(target):
                raise CodedError(
                    f"SKETCH_IS_OPEN: '{target}' is the sketch this session is "
                    "editing, and CATIA will not delete a sketch while it is "
                    f"open ({str(error).splitlines()[0][:120]}). Nothing was "
                    "deleted. Close it with catia_close_sketch and send the same "
                    "call again - measured on B30 2026-11-12, that is all it "
                    "takes. Do NOT close or re-open the document: that loses the "
                    "sketch and anything not saved.",
                    code="SKETCH_IS_OPEN", stage="execute", modified=False,
                    next_actions=[
                        "catia_close_sketch, then catia_delete_feature again with "
                        "the same name",
                    ],
                ) from error
            try:
                remaining = self._sketch_names()
            except Exception:                                 # noqa: BLE001
                remaining = []
            still_there = target in remaining
            # A CONSUMED SKETCH IS THE FIRST HYPOTHESIS WHEN THE ELEMENT IS A
            # SKETCH, because it is the one that needs a different NEXT action.
            # The 2026-11-18 report walked the old advice end to end - update
            # (succeeded), retry the same name (refused again), suspect the file
            # - while the answer was 凸台.1 all along. The features here were
            # asked before the delete (``_sketch_consumers``), so this sentence
            # can say how many of them answered and where the blind spot is.
            about_sketch = ""
            if kind == "sketch":
                seen, blind = sketch_scan
                about_sketch = (
                    "A SKETCH IS CONSUMED BY SOMETHING, USUALLY. This tool already "
                    f"asked the {seen} Part Design feature(s) in this part which "
                    "profile is theirs, and none named this one"
                    + (f" ({blind} of them do not answer .Sketch at all, so the "
                       "question is unanswered for those)" if blind else "")
                    + ". What that leaves is a consumer outside the body's "
                    "features: a GSD element (measured on B30 2026-11-18 - a "
                    "sketch fed to catia_gsd_extrude was deleted without a "
                    "refusal, and 拉伸.1 then failed every later update). ")
            raise CodedError(
                f"'{target}' is listed in the tree, but CATIA refused to delete "
                f"it ({str(error).splitlines()[0][:160]}). This call did NOT have "
                "that sketch open. "
                + about_sketch
                + "Otherwise the likeliest cause is what a failed update "
                "leaves behind: a dead reference, which the tree still lists and "
                "the selection cannot take. "
                + (f"It is still listed among the sketches after the attempt. "
                   if still_there else
                   "It is NOT listed among the sketches any more, so it may "
                   "already be gone - the delete failed on a stale handle. ")
                + "NEXT: catia_update_part names the element that fails on its "
                "own if there is one, and catia_list_features shows what is left. "
                "If a sketch of that name is open in CATIA's window, close it "
                "first - an open sketch cannot be deleted. Do not keep retrying "
                "the same name; re-open the document if the tree and the geometry "
                "disagree.",
                code="DELETE_FAILED", stage="execute", modified=False,
            ) from error
        finally:
            try:
                sel.Clear()
            except Exception:
                pass
        if defer_update:
            # The caller updates once, for the whole list. Saying anything about
            # the part's state here would be a verdict on a half-applied batch.
            return self._deleted_message(kind, target, before, args)
        try:
            part.Update()
        except Exception as e:
            self.conn.refresh_display()
            # NAME the casualties. "features that depended on it are now in
            # error" sent the 2026-11-10 reporter hunting by hand - the culprit
            # was a projection in a geometrical set, which the old probe (bodies
            # only) could not see. It costs one walk, and only on this path.
            culprits = failing_elements(part)
            self._note_part_error(str(e))
            lost = [n for n in before
                    if n not in self._sketch_names() and n != target]
            return (
                f"Feature '{target}' deleted, and the part update after it failed "
                f"({e}). This is a TERMINAL state: the delete happened, this call is "
                "finished. "
                + (describe(culprits) + " IN THIS STATE - which is what a feature "
                   "looks like when what it referenced is the thing that just went, "
                   "not proof that it is broken. "
                   if culprits else
                   "No single element fails on its own, so this is a relation "
                   "between features rather than one broken node. ")
                # The imperative that used to be here ("delete those too") erased
                # six healthy downstream features for a 2026-11-13 reporter. Say
                # what each route costs instead of which button to press.
                + "Two ways out, and only one is reversible: rebuild the feature "
                "that was removed (or edit the downstream feature that lost its "
                "support), or delete the named elements - DESTRUCTIVE, and it "
                "removes model features. "
                + "The part is IN ERROR until this is cleared - catia_update_part "
                "says whether it is, and catia_list_bodies marks its numbers "
                "stale instead of reporting them as current."
                + (f" Sketches removed with it: {', '.join(lost)}." if lost else "")
            )
        self._note_part_error(None)
        self.conn.refresh_display()
        message = self._deleted_message(kind, target, before, args)
        if kind == "sketch":
            message += self._cleanup_after_sketch(
                target, args.get("cleanup_frame_datum", "auto"),
                datum=self._deleted_sketch_datums.pop(target, None))
        return message

    def _cleanup_after_sketch(self, sketch_name: str, mode: Any,
                              update: bool = True, quiet: bool = False,
                              datum: Any = None) -> str:
        """Whether this sketch's frame scaffolding may go, and why.

        ``"auto"`` (the default) cleans up unless another sketch in the part
        stands on the SAME DATUM - the same absolute axis system, origin and
        directions both. That is the criterion since 0.3.149. Two earlier ones
        are recorded here because each was measured to be wrong:

        * "are there other sketches at all" was not about the datum, and the
          2026-11-18 report said so from its own project - 20+ frame sketches in
          one part, every one of them with its own ``_FRAME``, so ``auto`` never
          fired in exactly the parts that needed it;
        * "is another sketch on the same PLANE" (0.3.148) was a proxy that
          over-refused, and the same reporter measured it: two frame sketches on
          z = 0 with datum points 50 mm apart are two datums, and deleting the
          first one's frame left the part updating normally. A plane is an
          infinite set; a datum is one axis system.

        Comparing axis systems is safe in the direction that matters because the
        thing that breaks is measured: probe v0457 on B30 2026-11-18 built a
        second sketch ON ``A_FRAME`` by name, deleted the datum, and CATIA let it
        go - the survivor then reads 「需要修改绝对轴定义以解决这些问题」 and
        ``part.Update`` fails. Probe v0463 the same day measured that the two
        orientations report differently: a sketch built on the frame reports the
        identical ``(origin, H, V)`` triple (gap 0.000000 mm), while a sketch on
        a coplanar DIFFERENT datum reports the same H and V but an origin 50 mm
        away. ``Sketch.GetAbsoluteAxisData`` - through the CATScript route in
        ``sketch_frames.read_absolute_axes`` - is the only way this build answers
        where a sketch sits; ``Sketch.Reference`` / ``.Support`` / ``.Plane`` look
        like members to ``getattr`` and are not (they answer a placeholder whose
        ``.Name`` raises, probe v0462).

        ``datum`` is the deleted sketch's own ``(origin, H, V)``, captured BEFORE
        the delete - there is nothing to read afterwards. ``None`` means unknown,
        and unknown refuses rather than guesses.

        ``true`` forces the cleanup and says the risk out loud; ``false`` does
        nothing at all.
        """
        forced = mode is True
        if isinstance(mode, str):
            forced = mode.strip().lower() in ("true", "force", "yes")
            if mode.strip().lower() in ("false", "no", "keep", ""):
                return ""
        elif mode is False:
            return ""
        elsewhere: list[tuple[str, float | None]] = []
        if not forced:
            if not self._has_frame_scaffold(sketch_name):
                return ""
            try:
                others = [name for name in self._sketch_names()
                          if name != sketch_name]
            except Exception:  # noqa: BLE001 - unknown, so do not delete
                others = ["(the sketch list could not be read)"]
            if others:
                same, elsewhere, why = self._sketches_on_datum(others, datum)
                if same is None:
                    if quiet:
                        return ""
                    return (f" Its frame scaffolding was NOT removed: whether "
                            f"'{sketch_name}_FRAME' is still a support could not "
                            f"be decided ({why}). Deleting a datum under a live "
                            "sketch leaves that sketch unsupported and the update "
                            "failing (measured on B30 2026-11-18), so nothing was "
                            "deleted. Pass cleanup_frame_datum=true if you know "
                            "it is orphaned.")
                if same:
                    if quiet:
                        return ""
                    return (f" Its frame scaffolding was left alone: "
                            f"{len(same)} other sketch(es) share this datum - "
                            f"their absolute axis system is the same as this "
                            f"one's, origin and directions both "
                            f"({_describe_axes(datum)}): {', '.join(same[:4])}"
                            f"{', ...' if len(same) > 4 else ''}. A sketch built "
                            f"on '{sketch_name}_FRAME' reports exactly this "
                            "triple (measured on B30 2026-11-18), and deleting "
                            "the datum under it leaves it unsupported and the "
                            "update failing (需要修改绝对轴定义以解决这些问题). "
                            "Pass cleanup_frame_datum=true if you know that "
                            "datum is orphaned.")
        note = "" if not forced else (
            " Forced cleanup: a frame datum can be a shared support, so if a "
            "sketch built on it now fails, that is why.")
        return (self._delete_frame_scaffold(sketch_name, update=update,
                                            quiet=quiet)
                + note
                + self._coplanar_note(elsewhere))

    @staticmethod
    def _coplanar_note(elsewhere: list[tuple[str, float | None]]) -> str:
        """Why sketches on the same plane did not stop the cleanup.

        The 0.3.148 criterion refused on these, so saying which ones were
        considered and why they were let go is the difference between a decision
        and an omission - and the numbers are the evidence (2026-11-18 report,
        P2: two datums 50 mm apart on z = 0).
        """
        if not elsewhere:
            return ""
        gaps = [gap for _name, gap in elsewhere if gap is not None]
        worst = (f", {min(gaps):.4f} mm apart at the closest" if gaps else "")
        names = ", ".join(name for name, _gap in elsewhere[:4])
        return (f" {len(elsewhere)} other sketch(es) lie on the same plane but on "
                f"a different datum, so they do not stand on it ({names}"
                f"{', ...' if len(elsewhere) > 4 else ''}{worst}): a datum is an "
                "axis system, not a plane.")

    def _profile_map(self) -> tuple[dict[str, list[str]], list[str]]:
        """``{sketch name: [features whose profile it is]}``, and what would not
        answer.

        THE ONE BACK-REFERENCE B30 HAS. Measured on B30 2026-11-18 (probe v0463):
        ``凸台.1.Sketch.Name`` answers 'SK_P' and ``凹槽.1.Sketch.Name`` answers
        'SK_Q', while every non-sketch-based feature answers a placeholder whose
        ``.Name`` raises - so ``getattr`` alone would be a lie and every candidate
        has to be dismantled before it counts. Those placeholders are returned as
        "could not be asked" instead of being silently skipped: a caller that is
        told "no feature names it" deserves to know how many were asked.

        What this CANNOT see: a GSD element that consumed the sketch
        (``catia_gsd_extrude`` with profile='SK_G' built 拉伸.1, and the sketch
        was in that set, not in any body's feature list). That case is why the
        delete-failure reply still lists "consumed somewhere it cannot read"
        before it mentions a dead reference. It is also why this returns a map
        rather than a single answer - one walk serves a whole batch.
        """
        profiles: dict[str, list[str]] = {}
        unasked: list[str] = []
        try:
            bodies = self.conn.get_active_part().Bodies
            for index in range(1, int(bodies.Count) + 1):
                shapes = bodies.Item(index).Shapes
                for position in range(1, int(shapes.Count) + 1):
                    shape = shapes.Item(position)
                    name = str(getattr(shape, "Name", "") or "?")
                    try:
                        profile = str(shape.Sketch.Name)
                    except Exception:  # noqa: BLE001 - not a sketch-based shape
                        unasked.append(name)
                        continue
                    profiles.setdefault(profile, []).append(name)
        except Exception:  # noqa: BLE001 - unknown, and unknown must not refuse
            return profiles, unasked
        return profiles, unasked

    def _sketch_consumers(self, sketch_name: str
                          ) -> tuple[list[str], list[str]]:
        """Features naming ``sketch_name`` as their profile, and the rest."""
        profiles, unasked = self._profile_map()
        return list(profiles.get(sketch_name, ())), unasked

    def _batch_order(self, names: list[str]) -> list[str]:
        """The batch's names, every consumer before the sketch it consumes.

        ORDER MATTERS INSIDE ONE CALL. A caller who writes ``names=["SK_Q",
        "凸台.2"]`` wants both gone, and doing it in that order would delete the
        profile out from under a live pad - which CATIA either refuses or accepts
        and breaks the part with (measured both ways on B30 2026-11-18). So the
        pad goes first, whatever order the list arrived in, and the sketch is
        already gone when its turn comes; ``_delete_feature`` counts that as
        removed-with rather than as a failure.
        """
        profiles, _unasked = self._profile_map()
        ordered: list[str] = []
        remaining = list(names)
        while remaining:
            ready = [name for name in remaining
                     if not (set(profiles.get(name, ())) & set(remaining)
                             - {name})]
            if not ready:
                # A cycle cannot happen with a profile relation, but a name that
                # consumes itself would make this loop run forever - say nothing
                # and keep the caller's order instead.
                ordered.extend(remaining)
                break
            ordered.extend(ready)
            remaining = [name for name in remaining if name not in ready]
        return ordered

    def _capture_sketch_datums(self, part: Any, names: list[str]) -> None:
        """Record where each named sketch's own axes are, before anything goes.

        A sketch deleted BY ITS FEATURE never reaches ``_delete_one``, so its
        axes were never captured, and ``cleanup_frame_datum='auto'`` then has to
        refuse the frame scaffolding it left behind - "unknown must not delete",
        which is the right rule applied to a question this call could have
        answered a second earlier (2026-11-23 report, P0-3). One read here and
        the rollback takes the ``_FRAME`` with the sketch.

        Only sketches that HAVE a frame scaffold are read, because the read is a
        CATScript evaluation per sketch and there is nothing to decide about a
        sketch that leaves no scaffolding. Everything is best-effort: a read that
        fails leaves the datum unknown, which is the state this call found it in.
        """
        wanted = {str(name) for name in names}
        candidates: list[tuple[str, Any]] = []
        try:
            bodies = part.Bodies
            for index in range(1, int(bodies.Count) + 1):
                sketches = bodies.Item(index).Sketches
                for position in range(1, int(sketches.Count) + 1):
                    sketch = sketches.Item(position)
                    name = str(getattr(sketch, "Name", "") or "")
                    if (name in wanted and name not in self._deleted_sketch_datums
                            and self._has_frame_scaffold(name)):
                        candidates.append((name, sketch))
        except Exception:  # noqa: BLE001 - no list, no capture
            return
        if not candidates:
            return
        try:
            axes = sketch_frames.read_absolute_axes(
                self.conn.app, [sketch for _name, sketch in candidates])
        except Exception:  # noqa: BLE001 - unknown stays unknown
            return
        if not axes or len(axes) != len(candidates):
            return
        for (name, _sketch), one in zip(candidates, axes):
            if one:
                self._deleted_sketch_datums[name] = one

    def _sketch_axes(self, sketch: Any) -> Any:
        """``(origin, H, V)`` of a sketch's own axes, or None.

        ``Sketch.GetAbsoluteAxisData`` is the only handle on this build, and it
        needs a by-ref out array - so it goes through the CATScript route in
        ``sketch_frames.read_absolute_axes``. Measured on B30 2026-11-18: a
        sketch on a frame plane at (0, 0, 1000) answers
        ``[0, 0, 1000, 1, 0, 0, 0, 1, 0]``; the plain attribute/method call
        answers 「方法 GetAbsoluteAxisData 失败」.
        """
        try:
            axes = sketch_frames.read_absolute_axes(self.conn.app, [sketch])
        except Exception:  # noqa: BLE001 - unknown, and unknown must not delete
            return None
        if not axes:
            return None
        return axes[0]

    def _has_frame_scaffold(self, sketch_name: str) -> bool:
        """Whether ``<sketch>_FRAME`` (or a numbered variant) is in the tree."""
        base = f"{sketch_name}_FRAME"
        try:
            bodies = self.conn.get_active_part().Bodies
            for index in range(1, int(bodies.Count) + 1):
                try:
                    shapes = bodies.Item(index).HybridShapes
                except Exception:  # noqa: BLE001 - a body without hybrid shapes
                    continue
                for position in range(1, int(shapes.Count) + 1):
                    name = str(getattr(shapes.Item(position), "Name", "") or "")
                    if name == base or name.startswith(base + "."):
                        return True
        except Exception:  # noqa: BLE001 - unknown, so do not claim there is none
            return True
        return False

    def _sketches_on_datum(self, names: list[str], datum: Any
                           ) -> tuple[list[str] | None,
                                      list[tuple[str, float | None]], str]:
        """Which of ``names`` stand on ``datum``, which merely share its plane.

        Returns ``(same_datum, coplanar_different_datum, why)``. The first is
        ``None`` when the question could not be answered - an axis system that
        will not read - and every caller treats that as "do not delete", never as
        "none of them". The second is only used to explain a decision that was
        taken; it never stops a delete on its own (0.3.148 did stop on it, and
        the 2026-11-18 report measured that as a false refusal).
        """
        if datum is None:
            return (None, [], "this sketch's own axes could not be read before "
                              "it was deleted")
        sketches = []
        by_name: dict[str, Any] = {}
        try:
            bodies = self.conn.get_active_part().Bodies
            for index in range(1, int(bodies.Count) + 1):
                held = bodies.Item(index).Sketches
                for position in range(1, int(held.Count) + 1):
                    sketch = held.Item(position)
                    by_name[str(sketch.Name)] = sketch
        except Exception as error:  # noqa: BLE001 - reported, not raised
            return (None, [], "the sketches could not be listed "
                              f"({str(error).splitlines()[0][:80]})")
        for name in names:
            if name in by_name:
                sketches.append(by_name[name])
        if not sketches:
            return [], [], ""
        try:
            axes = sketch_frames.read_absolute_axes(self.conn.app, sketches)
        except Exception as error:  # noqa: BLE001 - reported, not raised
            return (None, [], "CATIA would not report where the remaining "
                              f"sketches sit ({str(error).splitlines()[0][:80]})")
        if axes is None:
            return (None, [], "CATIA would not report where the remaining "
                              "sketches sit (Sketch.GetAbsoluteAxisData through "
                              "the CATScript route)")
        same: list[str] = []
        elsewhere: list[tuple[str, float | None]] = []
        for sketch, system in zip(sketches, axes):
            verdict = sketch_frames.same_datum(datum, system)
            if verdict is None:
                return None, [], f"where '{sketch.Name}' sits could not be read"
            if verdict:
                same.append(str(sketch.Name))
                continue
            # Same plane, different axis system - worth saying only because the
            # previous criterion refused on it.
            try:
                signature = sketch_frames.plane_signature(system)
                mine = sketch_frames.plane_signature(datum)
                coplanar = (signature is not None and mine is not None
                            and sketch_frames.same_plane(mine, signature) is True)
            except Exception:  # noqa: BLE001 - a degenerate axis system
                coplanar = False
            if coplanar:
                elsewhere.append((str(sketch.Name),
                                  sketch_frames.origin_gap(datum, system)))
        return same, elsewhere, ""

    def _delete_frame_scaffold(self, sketch_name: str,
                               update: bool = True, quiet: bool = False) -> str:
        """Remove the datum plane and the 3 points a frame sketch left behind.

        NAMED, NOT GUESSED. Since 0.3.146 ``catia_create_sketch`` names them
        ``<sketch>_FRAME`` and ``<sketch>_FRAME.O`` / ``.U`` / ``.V``, so this
        can find them by name alone. It has to: on B30 2026-11-18 (probe v0454)
        the ownership cannot be asked of CATIA at all - ``Sketch.Plane`` and
        ``Sketch.Support`` do not exist ('Add.Plane'), ``AbsoluteAxis`` answers
        no GetAxisData / Origin, and the plane built by ``AddNewPlane3Points``
        has no FirstPoint / SecondPoint / ThirdPoint ('Item.FirstPoint'). The
        three points are anonymous 点.N.

        A sketch made before 0.3.146 therefore carries ``Datum_frame.N`` and
        three 点.N that cannot be tied to it. That case is REPORTED, with the
        candidates listed - deleting the nearest-looking plane would take
        another sketch's support with it.

        Order matters and was measured: the plane goes first, then the points.
        With the plane still there, the points are its definition and the update
        after deleting them fails.
        """
        part = self.conn.get_active_part()
        base = f"{sketch_name}_FRAME"
        planes: list[tuple[str, Any]] = []
        points: list[tuple[str, Any]] = []
        ignored: list[str] = []
        try:
            bodies = part.Bodies
            for index in range(1, int(bodies.Count) + 1):
                holder = bodies.Item(index)
                try:
                    shapes = holder.HybridShapes
                except Exception:  # noqa: BLE001 - a body without hybrid shapes
                    continue
                for position in range(1, int(shapes.Count) + 1):
                    node = shapes.Item(position)
                    name = str(getattr(node, "Name", ""))
                    if not name.startswith(base):
                        continue
                    # '<sketch>_FRAME', '<sketch>_FRAME.2' for a retried frame,
                    # and '<sketch>_FRAME[.2].O|U|V' for the points. Anything
                    # else that merely starts with the base is NOT touched: a
                    # name this call does not understand is not a licence to
                    # delete it.
                    rest = name[len(base):]
                    segments = [part for part in rest.split(".") if part]
                    numbers = all(part.isdigit() for part in segments[:-1])
                    if segments and segments[-1] in ("O", "U", "V") and numbers:
                        points.append((name, node))
                    elif not segments or (len(segments) == 1
                                          and segments[0].isdigit()):
                        planes.append((name, node))
                    else:
                        ignored.append(name)
        except Exception as error:  # noqa: BLE001 - reported, not raised
            return (f" Its frame scaffolding was NOT removed: the tree could not "
                    f"be walked ({str(error).splitlines()[0][:100]}).")
        if not planes and not points:
            if quiet:
                # A batch over skins that are mostly 'xy' sketches: saying "no
                # scaffolding found" once per name would bury the ones that did.
                return ""
            candidates: list[str] = []
            try:
                bodies = part.Bodies
                for index in range(1, int(bodies.Count) + 1):
                    shapes = bodies.Item(index).HybridShapes
                    for position in range(1, int(shapes.Count) + 1):
                        name = str(getattr(shapes.Item(position), "Name", ""))
                        if "FRAME" in name.upper() or name.startswith("Datum_frame"):
                            candidates.append(name)
            except Exception:  # noqa: BLE001 - the list is a courtesy
                candidates = []
            return (" Its frame scaffolding could not be identified: nothing named "
                    f"'{base}*' is in the tree. A frame sketch made before 0.3.146 "
                    "left 'Datum_frame.N' and three unnamed 点.N instead, and no "
                    "call can say which of them belongs to this sketch - deleting "
                    "the nearest-looking one would take another sketch's support "
                    "with it. "
                    + (f"Scaffolding-like names still in the tree: "
                       f"{', '.join(candidates[:8])}. " if candidates else "")
                    + "catia_list_features lists them; delete the ones you know "
                      "are orphaned with catia_delete_feature(names=[...]).")
        selection = self.conn.hso
        removed: list[str] = []
        failed: list[str] = []
        for name, node in planes:
            try:
                selection.Clear()
                selection.Add(node)
                selection.Delete()
                removed.append(name)
            except Exception:  # noqa: BLE001 - reported per element
                failed.append(name)
        if points:
            try:
                selection.Clear()
                for _name, node in points:
                    selection.Add(node)
                selection.Delete()
                removed.extend(name for name, _node in points)
            except Exception:  # noqa: BLE001 - fall back to one at a time
                for name, node in points:
                    try:
                        selection.Clear()
                        selection.Add(node)
                        selection.Delete()
                        removed.append(name)
                    except Exception:  # noqa: BLE001 - reported per element
                        failed.append(name)
        try:
            selection.Clear()
        except Exception:  # noqa: BLE001
            pass
        note = ""
        if update:
            try:
                part.Update()
            except Exception as error:  # noqa: BLE001 - reported, not raised
                note = (f" The part update after removing them failed "
                        f"({str(error).splitlines()[0][:120]}).")
        text = (f" Also removed the frame scaffolding it left behind: "
                f"{len(removed)} element(s) ({', '.join(removed[:6])}"
                f"{', ...' if len(removed) > 6 else ''}).")
        if failed:
            text += (f" {len(failed)} could NOT be removed and are still in the "
                     f"tree: {', '.join(failed[:6])}.")
        if ignored:
            text += (f" Left alone because they are neither this frame's datum nor "
                     f"its points: {', '.join(ignored[:6])}.")
        return text + note

    def _deleted_message(self, kind: str, target: str, before: list[str],
                         args: dict[str, Any]) -> str:
        """What one successful delete did, shared by both call shapes.

        Split out so a batch (which must not describe the part's state until its
        single final update) and a single delete report the same facts the same
        way: which sketch went with it, and which construction elements it left.
        """
        # A sketch deleted on purpose is not a casualty of the delete.
        lost = [n for n in before
                if n not in self._sketch_names() and n != target]
        message = f"Deleted {kind} '{target}'."
        if lost:
            message += (f" CATIA also removed {len(lost)} sketch(es) that belonged "
                        f"to it: {', '.join(lost)}. Re-create the profile before "
                        "cutting with it again.")
        # Construction elements this feature created - the datum points behind a
        # coordinate spline, above all. CATIA does NOT delete them with it: six
        # points stayed in the geometrical set after their spline was deleted
        # (measured on B30 2026-11-03, and a real session grew from 666 to 801
        # elements that way). Only what THIS session created can be named, which
        # is why the reply says so rather than claiming the tree is clean.
        owned = []
        try:
            owned = self.conn.owned_by(target)
        except Exception:  # noqa: BLE001 - bookkeeping only
            owned = []
        if owned and not args.get("cascade"):
            message += (f" It created {len(owned)} construction element(s) - "
                        f"{', '.join(owned[:6])}{', ...' if len(owned) > 6 else ''} - "
                        "which are still in the tree; pass cascade=true to remove "
                        "them too.")
        elif owned:
            removed, kept = self._delete_owned(owned)
            message += (f" Also removed {len(removed)} construction element(s) it "
                        "had created")
            if kept:
                message += (f"; {len(kept)} could not be removed: "
                            f"{', '.join(kept[:6])}{', ...' if len(kept) > 6 else ''}")
            message += "."
            try:
                self.conn.forget_owned(target)
            except Exception:  # noqa: BLE001 - bookkeeping only
                pass
        return message

    def _final_update_note(self, removed: list[str]) -> str:
        """Update the part ONCE after a batch, and judge only the FINAL state.

        The 2026-11-13 report (P0-1) is why this exists and why the wording is
        what it is. A batch of two paired one-sided pockets, updated after each
        name, answered:

            Feature '凹槽.1' deleted, but the part update failed afterwards ...
            8 elements fail on their own: '凹槽.2', '凹槽.3', '移除.1', ...
            - every later update stops there; delete those too

        and the model was healthy the whole time: 凹槽.2 still referenced the
        feature that had just gone, so the update BETWEEN the two deletes could
        not succeed. A caller who took that instruction deleted six healthy
        downstream features - an unrecoverable loss that was only survived
        because the reporter had copied the file first.

        So: one update, and if it fails, the reply says this is the terminal
        state, names what fails IN THAT STATE, and puts the destructive route
        last with the loss spelled out. It never says "delete those too".
        """
        part = self.conn.get_active_part()
        names = ", ".join(f"'{name}'" for name in removed[:6])
        if len(removed) > 6:
            names += f" and {len(removed) - 6} more"
        try:
            part.Update()
        except Exception as error:
            self.conn.refresh_display()
            self._note_part_error(str(error))
            culprits = failing_elements(part)
            return (
                f" THE FINAL UPDATE FAILED ({str(error).splitlines()[0][:140]}). This "
                f"is a TERMINAL state, not an in-between step: {len(removed)} "
                f"element(s) were removed ({names}) and this call is finished. "
                + (describe(culprits) + " IN THIS STATE - which is what a feature "
                   "looks like when the element it referenced is the one that just "
                   "went, not proof that it is broken. "
                   if culprits else
                   "No single element fails on its own, so this is a relation "
                   "between features rather than one broken node. ")
                + "Two ways out, and only one of them is reversible: (1) put back "
                "what was removed - rebuild that feature, or edit the downstream "
                "feature that lost its support; (2) delete the named elements with "
                "catia_delete_feature, which is DESTRUCTIVE and removes model "
                "features, and is what erased six healthy features for the "
                "2026-11-13 reporter who followed an earlier version of this "
                "sentence. catia_update_part says whether the part is still in "
                "error; catia_list_bodies marks its numbers stale while it is.")
        self._note_part_error(None)
        self.conn.refresh_display()
        return (f" ONE update at the end: the part updates, {len(removed)} "
                f"element(s) removed ({names}).")

    def _delete_owned(self, names: list[str]) -> tuple[list[str], list[str]]:
        """Delete the construction elements a feature created, in ONE selection.

        Measured on B30 2026-11-03: 91 orphan points took **49.9 s** the old way,
        because each name meant a full depth-first walk of the tree to find it and
        then three COM round trips to select and delete it - and the tree walk got
        slower as the list shrank past hundreds of items. The names are all known
        before the first delete, so the tree is walked once into a name -> object
        map and the objects go into a single selection. Elements that are already
        gone count as removed: that is the outcome that was asked for.

        Returns ``(removed, kept)``. An object that something else still uses
        cannot be deleted and is reported rather than silently skipped, so a
        batch that half-succeeds still tells the truth.
        """
        part = self.conn.get_active_part()
        sel = self.conn.hso
        wanted = [name for name in dict.fromkeys(names)]
        found = self._map_geometrical_sets(part, set(wanted))

        removed = [name for name in wanted if name not in found]
        kept: list[str] = []
        # A name can appear more than once (CATIA allows it), so this is a list
        # of OBJECTS, not of names: a delete that took one and left the twin
        # would keep answering to a name the caller thinks is gone.
        targets = [(name, obj) for name in wanted
                   for obj in found.get(name, [])]

        if targets:
            try:
                sel.Clear()
                for _name, obj in targets:
                    sel.Add(obj)
                sel.Delete()
                removed.extend(name for name, _obj in targets)
            except Exception:  # noqa: BLE001 - fall back to one at a time
                try:
                    sel.Clear()
                except Exception:  # noqa: BLE001
                    pass
                for name, obj in targets:
                    try:
                        sel.Clear()
                        sel.Add(obj)
                        sel.Delete()
                        removed.append(name)
                    except Exception:  # noqa: BLE001 - reported, not fatal
                        kept.append(name)
            finally:
                try:
                    sel.Clear()
                except Exception:  # noqa: BLE001
                    pass
        try:
            part.Update()
        except Exception:  # noqa: BLE001 - the elements are gone either way
            pass
        self.conn.refresh_display()
        # Per OBJECT, not per name: two elements sharing a name are two removals.
        return removed, kept

    @staticmethod
    def _map_geometrical_sets(part: Any, wanted: set[str]) -> dict[str, list]:
        """One depth-first walk: every wanted name that is in the tree.

        The plural twin of ``_find_in_geometrical_sets``, for callers that have a
        whole list of names: looking each one up separately re-walked the tree per
        name, which is what made deleting 91 points take 50 s.

        The value is a LIST because CATIA allows duplicates: two elements can
        share a name, and a delete that silently picked one of them left the
        caller with an element they believed gone (2026-11-03 - the state that
        made a rebuild look ineffective).
        """
        found: dict[str, list] = {}

        def walk(sets: Any, depth: int = 0) -> None:
            for index in range(1, sets.Count + 1):
                hybrid_body = sets.Item(index)
                name = str(hybrid_body.Name)
                if name in wanted:
                    found.setdefault(name, []).append(hybrid_body)
                for collection in ("HybridShapes", "HybridSketches"):
                    try:
                        items = getattr(hybrid_body, collection)
                    except Exception:  # noqa: BLE001 - not every set has both
                        continue
                    for item_index in range(1, items.Count + 1):
                        item = items.Item(item_index)
                        name = str(item.Name)
                        if name in wanted:
                            found.setdefault(name, []).append(item)
                try:
                    nested = hybrid_body.HybridBodies
                except Exception:  # noqa: BLE001 - a plain set has no children
                    continue
                if nested is not None and nested.Count and depth < 8:
                    walk(nested, depth + 1)

        walk(part.HybridBodies)
        return found

    @staticmethod
    def _find_in_geometrical_sets(part: Any, target: str
                                  ) -> tuple[Any, str, list]:
        """Find a GSD element, a sketch inside a set, or the set itself.

        Depth-first, because CATIA nests geometrical sets freely and a failed
        loft often ends up one level below the set that was created for it.
        Returns ``(found, kind, every_match)`` - the third element is why this
        is a list and not a single object: CATIA lets two elements carry the SAME
        name, and this server used to create them (setting ``Name`` on a new
        element does not check that the name is free). A tree with two
        ``P_04_06`` makes every later call by name ambiguous - ``FindObjectByName``
        returns one of them, a delete removes one of them - which is exactly the
        state that looks like "deleted and rebuilt, but the join still fails"
        (measured on B30 2026-11-03: two elements named C_P, delete removed one,
        the other stayed). ``(None, "", [])`` when the name is not there.
        """
        hits: list[tuple[Any, str]] = []

        def walk(sets: Any, depth: int = 0) -> None:
            for index in range(1, sets.Count + 1):
                hybrid_body = sets.Item(index)
                if str(hybrid_body.Name) == target:
                    hits.append((hybrid_body, "geometrical set"))
                for collection, label in (("HybridShapes", "GSD element"),
                                          ("HybridSketches", "sketch in a set")):
                    try:
                        items = getattr(hybrid_body, collection)
                    except Exception:  # noqa: BLE001 - not every set has both
                        continue
                    for item_index in range(1, items.Count + 1):
                        item = items.Item(item_index)
                        if str(item.Name) == target:
                            hits.append((item, label))
                try:
                    nested = hybrid_body.HybridBodies
                except Exception:  # noqa: BLE001 - a plain set has no children
                    continue
                if nested is not None and nested.Count and depth < 8:
                    walk(nested, depth + 1)

        walk(part.HybridBodies)
        if not hits:
            return None, "", []
        return hits[0][0], hits[0][1], hits

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
    # back as 0). The NLS name prefix is reliable, so canonicalise from there.
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
        # CATIA's Chinese name for Shell is 盒体, which was missing from this
        # table: a shell built by catia_shell listed as type "unknown" while the
        # pad above it said "pad" (reported 2026-10-28, section 5.4).
        ("盒体", "shell"),
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

    #: The kinds that REMOVE material. A mirror of the whole body unions the
    #: image with the original, so on a body whose last feature is one of these
    #: the union can put the removed material back - measured, 2026-11-18.
    _CUT_KINDS = ("pocket", "groove", "hole", "split", "shell", "thickness")

    #: Which members a feature's FULL interface carries, for the case the name
    #: prefix cannot answer: a feature the caller renamed. `body.Shapes.Item(i)`
    #: hands back a bare Item (21 members, no `Type`), so nothing about it is
    #: readable there - but `part.FindObjectByName` returns the real interface
    #: (78 members for a loft, measured on B30 2026-11-14), and a solid loft is
    #: the one that answers `AddSectionToLoft`.
    _RICH_SIGNATURES = (
        ("loft", ("AddSectionToLoft", "GetSectionFromLoft")),
    )

    def _feature_kind_resolved(self, part: Any, shape: Any) -> str:
        """The feature's kind, from its name if possible and from the object if not.

        The 2026-11-14 report (P2-4) renamed its solid loft to 'AirPods_Body' and
        got `"type": "unknown", "catia_type": null`, so anything branching on the
        feature type silently skipped it. The prefix table cannot help with a
        name the caller chose - the OBJECT can.
        """
        name = str(getattr(shape, "Name", ""))
        kind = self._feature_kind(name)
        if kind != "unknown":
            return kind
        # A boolean named by the caller has no NLS prefix to read: AddNewAdd
        # produces a feature whose only name is the one the caller gave it, so
        # 'AirPods1_Unified' came out "unknown" while the pad above it said "pad"
        # (2026-11-15 report C6). The session that built it knows.
        remembered = self._remembered_boolean(name)
        if remembered:
            return remembered
        return self._kind_from_members(part, name) or "unknown"

    def _remembered_boolean(self, name: str) -> str | None:
        """The boolean operation this session built under ``name``, if any."""
        book = getattr(self.conn, "boolean_features", None)
        if not isinstance(book, dict):
            return None
        try:
            return book.get((str(self.conn.active_document.Name), str(name)))
        except Exception:  # noqa: BLE001 - no document, no record
            return None

    def _remember_boolean(self, feature: Any, operation: str) -> None:
        """Record what this call built, under the name the caller asked for.

        Document-keyed, like the sketch outline record: the same name in another
        part is a different feature.
        """
        try:
            key = (str(self.conn.active_document.Name), str(feature.Name))
        except Exception:  # noqa: BLE001 - a record is a courtesy
            return
        book = getattr(self.conn, "boolean_features", None)
        if not isinstance(book, dict):
            book = {}
            self.conn.boolean_features = book
        book[key] = "boolean"

    def _kind_from_members(self, part: Any, name: str) -> str | None:
        try:
            richer = part.FindObjectByName(name)
        except Exception:  # noqa: BLE001 - no lookup is not "no type"
            return None
        if richer is None:
            return None
        try:
            info = richer._oleobj_.GetTypeInfo()
            members = {str(info.GetNames(info.GetFuncDesc(i).memid)[0])
                       for i in range(info.GetTypeAttr().cFuncs)}
        except Exception:  # noqa: BLE001
            return None
        for kind, signatures in self._RICH_SIGNATURES:
            if all(signature in members for signature in signatures):
                return kind
        return None

    @classmethod
    def _feature_type_name(cls, name: str) -> str | None:
        """The prefix this build actually used ('凸台', 'Pocket'), or None.

        Published as ``catia_type``. That field used to read ``shape.Type``,
        which answers None on this API (no attribute) or a raw id (a hole came
        back as 0) - so it was null on every feature of every part, forever. A
        field that can never hold a value is the same defect as a constant that
        always holds the same one ("a constant that carries no information is
        worse than no field", 0.3.35, and reported again 2026-10-28, §5.5). The
        prefix IS the host's own word for the feature, and it is what the `type`
        mapping read, so a caller can audit that mapping instead of trusting it.
        """
        for prefix, _kind in cls._FEATURE_PREFIXES:
            if name.startswith(prefix):
                return prefix
        return None

    def _target_part(self, document: str | None,
                     activate: bool) -> tuple[Any, str]:
        """The Part to act on: the active one, or the one the caller named.

        Reported 2026-11-22 (P1-17): every feature tool works on the ACTIVE
        document, so after opening an assembly the next call either lands on the
        product or has to be preceded by ``catia_activate_document``. The two
        tools that only needed to READ a tree, and the one that deletes by name,
        now take the document instead - which also removes a silent-wrong-target
        failure mode, not just a round trip.

        ``activate=False`` is for readers: ``part_of(doc)`` gives the part and
        nothing the caller sees moves. ``activate=True`` switches first and
        CHECKS that it switched, because a part held by an open product accepts
        ``Activate()`` and stays in the background (measured 0.3.74) - a write
        tool that silently wrote to the previous document would be worse than
        refusing.

        The body is NOT chosen here: the callers differ (the active body may be a
        side body the caller set up; a named document gets its MainBody).
        """
        if not document:
            return self.conn.get_active_part(), ""
        doc, matches = self.conn.resolve_document(str(document))
        if doc is None:
            rows = self.conn.describe_matches(matches)
            detail = (" It matches " + str(len(rows)) + " open documents: "
                      + self.conn.format_matches(rows)
                      + " Pass the full path of the one you mean." if rows else
                      " catia_list_documents lists what is open.")
            raise CodedError(
                f"No open document matches '{document}', so nothing was measured "
                "or changed." + detail,
                code="DOCUMENT_NOT_FOUND", stage="validate", modified=False,
                next_actions=[
                    "Pass a name from catia_list_documents (with its extension), "
                    "or the full path when two documents share a name",
                ])
        try:
            part = self.conn.part_of(doc)
        except Exception as exc:  # noqa: BLE001 - a Product, most likely
            raise CodedError(
                f"'{doc.Name}' is not a Part document, so this tool has nothing to "
                f"act on ({str(exc).splitlines()[0][:90]}). Nothing was changed.",
                code="NOT_A_PART_DOCUMENT", stage="validate", modified=False,
                next_actions=[
                    "Point this tool at a .CATPart - catia_list_documents shows "
                    "which open documents are Parts",
                    "To work inside an assembly, open the part on its own with "
                    "catia_open_document(name=<its file>) and pass that name",
                ]) from exc
        name = str(doc.Name)
        if not activate:
            return part, name
        try:
            before = str(self.conn.app.ActiveDocument.Name)
        except Exception:  # noqa: BLE001 - nothing active at all
            before = ""
        if before != name:
            try:
                doc.Activate()
            except Exception as exc:  # noqa: BLE001 - reported below
                raise CodedError(
                    f"CATIA refused to activate '{name}' ({str(exc).splitlines()[0][:80]}), "
                    "so this call would have acted on the wrong document. Nothing "
                    "was changed.",
                    code="ACTIVATION_FAILED", stage="validate", modified=False,
                ) from exc
            try:
                after = str(self.conn.app.ActiveDocument.Name)
            except Exception:  # noqa: BLE001
                after = ""
            if after != name:
                holders = []
                try:
                    holders = self.conn.component_usage(deep=True).get(name) or []
                except Exception:  # noqa: BLE001 - the sentence survives
                    holders = []
                raise CodedError(
                    f"CATIA did not switch to '{name}': ActiveDocument is still "
                    f"'{after}', so this call would have acted on that document "
                    "instead. Nothing was changed. "
                    + (f"'{name}' is a component of "
                       + ", ".join(f"'{user}'" for user in holders)
                       + ", which keeps it in the background. "
                       if holders else
                       "'" + name + "' has no window of its own. ")
                    + "Close what holds it (or call catia_activate_document and "
                      "check the answer), then retry without 'document'.",
                    code="ACTIVATION_FAILED", stage="validate", modified=False,
                    data={"requested_document": name, "active_document": after,
                          "held_by": holders})
        return part, name

    def _list_features(self, document: str | None = None) -> str:
        self.conn.ensure_connected()
        part, named = self._target_part(document, activate=False)
        if named:
            # A READER does not need the session's remembered body: the caller
            # asked about a document, and its MainBody is the only body this call
            # can claim to know.
            body = part.MainBody
        else:
            body = self.conn.get_active_part_body()
        shapes = body.Shapes

        features = []
        for i in range(1, shapes.Count + 1):
            shape = shapes.Item(i)
            features.append({
                "index": i,
                "name": shape.Name,
                "type": self._feature_kind_resolved(part, shape),
                # The host's own name for it - not shape.Type, which is None or 0
                # on this API and made this field a permanent null (§5.5).
                "catia_type": self._feature_type_name(shape.Name),
            })

        # ALWAYS an object, even with count 0. This used to return the bare
        # string "No features in the active body", so the reply's SHAPE depended
        # on whether the part had been built yet - and the moment that happens is
        # exactly when a caller asks this tool, because the description says
        # "this is how to check whether a sketch still exists after a failed
        # pad/pocket/hole". A caller parsing resp["sketches"] got a string and an
        # IndexError instead of its own sketch (reported 2026-10-28, P1).
        notes: list[str] = []
        payload: dict[str, Any] = {
            # WHICH DOCUMENT this was read from. The 2026-11-14 report's N11 was
            # this tool answering with another part's body while
            # catia_get_active_document_info answered with the active one - two
            # tools, same moment, different documents, and nothing in either
            # reply said so. The name costs nothing and settles it.
            "document": named or str(
                getattr(getattr(self.conn, "active_document", None), "Name", "")
                or ""),
            "body": str(body.Name),
            "count": len(features),
            "bodies": part.Bodies.Count,
            "features": features,
        }
        if not features:
            notes.append(
                "the active body holds no Part Design feature yet, so 'features' "
                "is empty and 'count' is 0 - this is not an error, and the "
                "sketches in it are listed below")
        if named:
            notes.append(
                f"read from '{named}' by name, so the ACTIVE document was not "
                "changed and its own work body (which may be a side body) is not "
                "what this lists: these are the features of this part's MainBody.")
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
            notes.append(
                "geometrical_sets are wireframe containers, not Part Design "
                "features, and they are not counted in 'count'. Each circular "
                "pattern adds a line (plus its two points) to the 'PatternAxis' "
                "set and that geometry must stay: the pattern holds a reference "
                "to it, so deleting it deletes the pattern."
            )
        # Sketches were not listed at all, so "what is in the tree" could only be
        # read side-on, from the `Available:` list a failed pad prints - and that
        # channel was itself lying about survival until 0.3.30 (reported
        # 2026-10-27). They are not features and stay out of 'count'.
        try:
            finder = getattr(self.conn, "sketch_aliases_of", None)
            sketches = []
            for i in range(1, body.Sketches.Count + 1):
                live = str(body.Sketches.Item(i).Name)
                entry: dict[str, Any] = {"index": i, "name": live}
                # A rebuilt sketch answers to its old name, so the listing has to
                # show that name too - otherwise the caller reads "my sketch is
                # gone" from the one place that is supposed to be authoritative
                # (reported 2026-10-27: 草图.4 worked and was not listed).
                olds = finder(live) if finder is not None else []
                if olds:
                    entry["aliases"] = olds
                sketches.append(entry)
        except Exception:  # noqa: BLE001 - a listing must not fail over this
            sketches = []
        payload["sketches"] = sketches
        payload["sketch_count"] = len(sketches)
        # A name shared by two sketches makes every by-name tool ambiguous -
        # loft sections, pad's feature_name, delete_feature, sketch_edit all
        # resolve by name, and CATIA's own numbering does not check for
        # conflicts (reported 2026-11-08, N2: one part held two 草图.1 and two
        # 草图.2, and a section resolved to the other object mid-session).
        shared = self._shared_sketch_names(part)
        if shared:
            for entry in sketches:
                count = shared.get(entry["name"], 0)
                if count > 1:
                    entry["name_shared_by"] = count
            payload["sketch_name_conflicts"] = [
                {"name": name, "count": count}
                for name, count in sorted(shared.items()) if count > 1]
            payload["sketch_name_conflict_note"] = (
                "Two or more sketches in this part answer to the same name, so "
                "every by-name argument ('sections', 'feature_name', "
                "'sketch_name') may reach either of them. Rename one by hand, or "
                "address the feature by index. New sketches created by this "
                "server are given a free name from 0.3.78 on, so a conflict that "
                "is still here came from an older session or from CATIA's own "
                "numbering.")
        # THE FRAME SCAFFOLDING, COUNTED RATHER THAN HIDDEN (2026-11-19 report,
        # P2-5). Every sketch built on a frame leaves a datum plane and three
        # construction points in body.HybridShapes, and this listing never
        # mentioned them: the reporter's four frame sketches left 16 tree
        # elements that were visible in CATIA's tree and absent from every reply.
        # They are not features and stay out of 'count', but their number and
        # their owner are worth one paragraph - that is what "collapsed" can
        # honestly mean here, since this tool cannot hide anything in the tree.
        scaffold = self._frame_scaffold_inventory(body)
        if scaffold["element_count"]:
            payload["frame_scaffolding"] = scaffold
            payload["frame_scaffolding_note"] = (
                f"{scaffold['element_count']} element(s) belong to frame datums, "
                "not to the model: one plane and three construction points per "
                "sketch created with plane={type:'frame'}. They stay after the "
                "sketch is deleted, and catia_delete_feature("
                "cleanup_frame_datum='auto') removes them with it - 'auto' keeps "
                "them only while another sketch stands on the same datum. They "
                "are NOT counted in 'count' and they are not in 'features'."
            )
        if sketches:
            payload["sketches_note"] = (
                "sketches live in the body alongside the features (a sketch "
                "consumed by a pad is still here), and are not counted in 'count'. "
                "An entry gains an 'aliases' array only when it has one: after a "
                "rollback deleted a sketch, this server rebuilds it under a new "
                "name and the old name keeps resolving, so 'aliases' lists those "
                "older names. Absent means the sketch never had another name - not "
                "that the field could not be read")
        else:
            payload["sketches_note"] = (
                "the body holds no sketch either, so there is nothing drawn to "
                "build on yet")
        if notes:
            payload["note"] = " ".join(notes)
        return json.dumps(payload, indent=2)

    def _frame_leftovers(self, sketch_names: list[str]) -> dict[str, int]:
        """Scaffold elements still in the tree for ``sketch_names``, by owner.

        Read after the deletes, by name: ``<sketch>_FRAME`` and ``.O/.U/.V`` are
        the only handle CATIA gives (measured 2026-11-18 - ``Sketch.Plane`` and
        ``Sketch.Support`` do not exist), and it is also the question the caller
        asks - "is anything left of that sketch".
        """
        wanted = set(sketch_names)
        left: dict[str, int] = {}
        try:
            bodies = self.conn.get_active_part().Bodies
            count = int(bodies.Count)
        except Exception:  # noqa: BLE001 - nothing to report, nothing to claim
            return left
        for index in range(1, count + 1):
            try:
                inventory = self._frame_scaffold_inventory(bodies.Item(index))
            except Exception:  # noqa: BLE001 - one unreadable body
                continue
            for entry in inventory.get("sketches") or []:
                owner = entry.get("sketch")
                if owner in wanted:
                    left[owner] = left.get(owner, 0) + int(entry.get("elements") or 0)
        return left

    def _frame_scaffold_inventory(self, body: Any) -> dict[str, Any]:
        """How many frame-datum elements the body holds, grouped by owner.

        Named since 0.3.146 (``<sketch>_FRAME`` and ``.O/.U/.V``), so this is a
        name walk, not a guess - the same names ``_delete_frame_scaffold``
        deletes. Older parts carry ``Datum_frame.N`` and are counted separately
        rather than attributed to a sketch they cannot be tied to.
        """
        owners: dict[str, int] = {}
        legacy = 0
        total = 0
        try:
            shapes = body.HybridShapes
            for index in range(1, int(shapes.Count) + 1):
                name = str(getattr(shapes.Item(index), "Name", "") or "")
                if "_FRAME" in name:
                    owner = name.split("_FRAME", 1)[0]
                    owners[owner] = owners.get(owner, 0) + 1
                    total += 1
                elif name.startswith("Datum_frame"):
                    legacy += 1
                    total += 1
        except Exception:  # noqa: BLE001 - a listing must not fail over this
            return {"element_count": 0, "sketches": [], "legacy": 0}
        return {
            "element_count": total,
            "sketches": [{"sketch": owner, "elements": count}
                         for owner, count in sorted(owners.items())],
            "legacy": legacy,
        }

    @staticmethod
    def _shared_sketch_names(part: Any) -> dict[str, int]:
        """How many sketches in the whole part answer to each name.

        All bodies, and geometrical sets nested up to four deep - the same tree
        ``_hide_set_contents`` walks. Used only for reporting: a duplicate is a
        fact about the document, and saying so is the difference between "my
        sketch is there" and "my sketch is there twice".
        """
        counts: dict[str, int] = {}

        def collect(holder: Any, depth: int = 0) -> None:
            if depth > 4:
                return
            for collection in ("Sketches", "HybridSketches"):
                try:
                    items = getattr(holder, collection)
                except Exception:  # noqa: BLE001 - no such list here
                    continue
                for i in range(1, items.Count + 1):
                    try:
                        name = str(items.Item(i).Name)
                    except Exception:  # noqa: BLE001 - one item
                        continue
                    counts[name] = counts.get(name, 0) + 1
            try:
                sets = holder.HybridBodies
            except Exception:  # noqa: BLE001 - nothing nested here
                return
            for i in range(1, sets.Count + 1):
                try:
                    collect(sets.Item(i), depth + 1)
                except Exception:  # noqa: BLE001 - one set
                    continue

        try:
            for i in range(1, part.Bodies.Count + 1):
                collect(part.Bodies.Item(i))
            for i in range(1, part.HybridBodies.Count + 1):
                collect(part.HybridBodies.Item(i))
        except Exception:  # noqa: BLE001 - an unreadable tree reports nothing
            return counts
        return counts

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

    def _closing_points(self, part: Any, spec: Any,
                        sections: list[str]) -> list[Any]:
        """Resolve ``closing_points`` into one reference (or None) per section.

        CATIA couples one section to the next from each section's parameter 0,
        and that point is chosen automatically unless it is given. Where the
        sections move sideways - an AirPods head whose ellipse centres travel
        5 mm - an automatic choice can put parameter 0 on a different side of one
        section than of the next, and the surface then twists on that side while
        the other side stays smooth. That is the 2026-11-15 report's C2, and the
        argument to change it has been in this call since 0.3.77, always None.
        """
        if not spec:
            return []
        if isinstance(spec, (str, dict)):
            raise ArgumentRejected(
                "'closing_points' takes a LIST, one entry per section, in the same "
                "order as 'sections' - a point name, or null to leave that section "
                "automatic. Nothing was created.",
                code="BAD_ARGUMENT", stage="validate")
        values = list(spec)
        if len(values) > len(sections):
            raise ArgumentRejected(
                f"'closing_points' has {len(values)} entries for "
                f"{len(sections)} section(s). One entry per section, in order; "
                "use null where a section should keep the automatic point. "
                "Nothing was created.", code="BAD_ARGUMENT", stage="validate")
        out: list[Any] = []
        for index, value in enumerate(values):
            if value in (None, ""):
                out.append(None)
                continue
            if not isinstance(value, str):
                raise ArgumentRejected(
                    f"closing_points[{index}] must be a point's tree name or null; "
                    f"got {value!r}. Nothing was created.",
                    code="BAD_ARGUMENT", stage="validate")
            element = None
            try:
                element = part.FindObjectByName(str(value))
            except Exception:  # noqa: BLE001 - reported as not found
                element = None
            if element is None:
                raise ArgumentRejected(
                    f"No element named '{value}' to use as the closing point of "
                    f"section '{sections[index] if index < len(sections) else '?'}'. "
                    "Build it first with catia_gsd_point - the point has to lie ON "
                    "that section. Nothing was created.",
                    code="CLOSING_POINT_NOT_FOUND", stage="validate",
                    data={"name": str(value), "index": index})
            out.append((part.CreateReferenceFromObject(element), str(value)))
        return out

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
            raise ArgumentRejected(
                "A loft needs at least two sections - one section is a face, not "
                "a solid. Nothing was created.",
                code="NOT_ENOUGH_SECTIONS", stage="validate")
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = self._prepare_body(part)
        sf = part.ShapeFactory

        refs = [self._section_reference(name) for name in sections]

        # What this session knows about each section, from the sketch record -
        # the only source: a sketch's points cannot be read back through
        # automation. Two facts matter before the build, and neither was
        # reported before 0.3.77:
        #   * a section that is several curves is why AddSectionToLoft/Update
        #     fails (reported 2026-11-07, P1: a 3-line + 4-arc deck outline was
        #     refused with a generic "not ONE curve" hint);
        #   * two closed splines with DIFFERENT point counts couple by parameter,
        #     and CATIA then twists the surface: 59 points against 60 gave a
        #     +49.8% bulge at the stern (min y -10335 mm against a nominal
        #     -6900) while answering ok:true, a volume that looked sane and no
        #     warning at all (P2, the worst item in that report). The counts are
        #     in the record, so the mismatch can be named instead of discovered.
        facts = [(name, self._section_facts(name)) for name in sections]
        facts_dict = {name: fact for name, fact in facts if fact}

        before, volume_note = self._volume_probe(part, body)
        if before is None and self._body_is_empty(body):
            # An empty body has no volume to measure, which is not a failure:
            # the loft's own volume is the whole of it, and saying "not verified"
            # there would hide the one number the caller wants.
            before, volume_note = 0.0, None
        part.InWorkObject = body
        feature = sf.AddNewLoft()
        inner = feature.HybridShape
        closing = self._closing_points(part, args.get("closing_points"), sections)
        used: list[str] = []
        for index, ref in enumerate(refs):
            entry = closing[index] if index < len(closing) else None
            point = entry[0] if entry else None
            if entry:
                used.append(f"'{entry[1]}' for '{sections[index]}'")
            # (section, orientation, closing point) - the closing point decides
            # where each section's parameter 0 sits, which is what couples one
            # section to the next. It was always None ("automatic") until 0.3.137;
            # measured on B30 2026-11-15, two eccentric ellipses coupled from a
            # point at their +Y extreme instead of CATIA's own choice change the
            # solid (4523.8263 mm3 against 4523.6938).
            try:
                inner.AddSectionToLoft(ref, 1, point)
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
            gone = self._remove_feature(part, feature, body)
            multi = [name for name, fact in facts
                     if fact and fact["curves"] > 1]
            raise FeatureRejected(
                f"CATIA rejected the solid loft"
                + (" and it was removed from the tree again (rolled_back: true)"
                   if gone else " and it could NOT be removed (rolled_back: false)")
                + ". Sections: " + ", ".join(sections)
                + (". THIS loft has the cause the generic hint describes: "
                   + ", ".join(f"'{name}' holds "
                               f"{facts_dict[name]['curves']} separate curve(s) "
                               f"({facts_dict[name]['curves']} from "
                               f"{facts_dict[name]['elements']} drawing call(s))"
                               for name in multi)
                   + " - several curves, and an Extract of several is still "
                   "several. THE CHEAPEST WAY OUT IS ONE CALL: "
                   + ", ".join(f"catia_gsd_spline(element='{name}', closed=true)"
                               for name in multi)
                   + " - it takes the corners of a sketch THIS SERVER drew in "
                   "this session (which is how an imported drawing arrives) and "
                   "returns ONE closed curve; pass that curve's name as the "
                   "section instead of the sketch. It is interpolating, so it "
                   "passes through every point and rounds every corner. If the "
                   "section came from catia_import_drawing, profile_mode='spline' "
                   "does the same thing during the import. Redrawing it by hand "
                   "as one closed catia_sketch_spline also works (measured "
                   "2026-11-07 on B30: a 4-line rectangle section is refused, the "
                   "same four points as a closed spline build)." if multi else
                   ". The usual cause is a section that is not ONE curve - a "
                   "polygon of separate sketch lines is several curves, and an "
                   "Extract of it is still several; use a closed "
                   "catia_sketch_spline or catia_gsd_spline. No section in this "
                   "call was recorded as multi-element, so look at the second "
                   "cause instead")
                + ". Second cause, seen with the surface loft on the same build: "
                "the loft is session-sensitive, so retry once in a fresh session "
                "before rebuilding the sections."
                + (f" Recorded sections: {self._describe_facts(facts)}."
                   if any(fact for _name, fact in facts) else "")
                + f" Original error: {error}",
                rolled_back=gone,
                # Named, not inferred: this failure answered STEP_EXCEPTION
                # before 0.3.77 and COM_ERROR after it, purely because the
                # message was rewritten - a silent change for a caller that
                # branches on the code (reported 2026-11-08).
                code="LOFT_REJECTED",
            ) from error

        self.conn.refresh_display()
        after, after_note = self._volume_probe(part, body)
        detail = ""
        if before is not None and after is not None:
            detail = f" Volume {before:.1f} -> {after:.1f} mm3."
        elif after_note or volume_note:
            detail = f" Volume not verified ({after_note or volume_note})."
        section_note = (f" Sections: {self._describe_facts(facts)}."
                        if facts_dict else "")
        twist = self._coupling_warning(facts_dict)
        kept, kept_note = self._loft_sections_used(inner, sections)
        count = len(kept) if kept else len(sections)
        # Two semantics this feature has always had and never said out loud, both
        # reported as "undocumented" by the 0.3.120 acceptance reports - one of
        # them for the THIRD time (2026-11-13, twice in one day):
        #   * a solid loft in a body that already holds material is UNIONED with
        #     it, no catia_boolean involved, so the volume in this reply is the
        #     BODY's total and not the loft's own share;
        #   * a chain of sections that does not close back on itself ends at the
        #     first and last section's plane - those ARE the end faces. There is
        #     no open-shell result from this tool.
        merge = ""
        if before:
            merge = (f" THIS FEATURE JOINED '{body.Name}': a Part Design solid built "
                     f"into a body that already holds {before:.1f} mm3 is UNIONED "
                     "with it - no catia_boolean, no second body, and the volume "
                     "above is the body's total, not the loft's own share. Do not "
                     "add body volumes up.")
        caps = (" The ends: a section chain that does not close back on itself ends "
                "at the FIRST and LAST section's plane, and those planes are the "
                "end faces - the result is always a solid. For an open shell, sweep "
                "a surface instead (catia_gsd_sweep, or catia_solid_tube for a pipe).")
        closing_note = ""
        if used:
            closing_note = (" Closing points: " + "; ".join(used)
                            + " - where the sections move sideways this is what "
                              "stops one side of the surface twisting against the "
                              "other.")
        return (
            f"Solid loft created through {count} section(s)"
            + (f" ({'; '.join(tangency)})" if tangency else "")
            + f". Feature: '{feature.Name}' in '{body.Name}', a Part Design solid, "
            "not a surface - no thickening step is needed."
            + detail
            + merge
            + caps
            + kept_note
            + section_note
            + twist
            + closing_note
        )

    @staticmethod
    def _loft_sections_used(inner: Any,
                            requested: list[str]) -> tuple[list[str], str]:
        """The sections CATIA ITSELF holds, walked index by index.

        Why this exists: the 2026-11-12 report fed 45 sections into one solid
        loft and had no way to tell whether any had been dropped - it compared
        its own numeric integral (69976 mm3) with the volume the tool reported
        (70047.2, +0.1 %) and called that agreement. A number that agrees is not
        a section list, and the report asked for ``sections_used``.

        MEASURED on B30 2026-11-13 (scripts/b30_v0418_probe_loft_sections.py):
        ``HybridShapeLoft.GetSectionFromLoft(i)`` is **1-based**, returns
        ``(reference, orientation, closing point)``, and RAISES past the last
        section - 3 sections answered at 1, 2 and 3 and raised at 0, 4 and 5.
        ``reference.DisplayName`` is the section's own name ('草图.1'), so the
        list can be compared with what was asked for by name, not by count.

        CAVEAT, because it is the difference between a measurement and a guess:
        the count is only readable on the ``Loft`` this call created. Doing it
        later is not possible - ``Part.FindObject`` does not exist on this build
        and a shape read out of ``body.Shapes`` comes back as a bare base
        ``Item`` with no ``HybridShape`` (both measured in the same probe). That
        is why the readback lives inside the build and not in a separate call.
        """
        names: list[str] = []
        try:
            dynamic = CATIAConnection._dynamic(inner)
        except Exception:  # noqa: BLE001 - ask the object we have
            dynamic = inner
        for index in range(1, len(requested) + 3):
            try:
                got = dynamic.GetSectionFromLoft(index)
            except Exception:  # noqa: BLE001 - past the end, which is the answer
                break
            label = ""
            try:
                label = str(getattr(got[0], "DisplayName", "") or "")
            except Exception:  # noqa: BLE001
                label = ""
            names.append(label or f"section {index}")
        if not names:
            return [], (
                " How many sections CATIA kept could NOT be read back "
                "(GetSectionFromLoft refused index 1 on this object), so the "
                f"count above is the {len(requested)} that were SENT, not a count "
                "read back from the loft. Check the shape before trusting it."
            )
        listed = ", ".join(f"'{name}'" for name in names)
        if len(names) == len(requested):
            return names, (
                f" CATIA's own section list holds {len(names)}: {listed} - read "
                "back with GetSectionFromLoft, so none was silently dropped."
            )
        missing = [name for name in requested if name not in names]
        return names, (
            f" WARNING: {len(requested)} section(s) were sent but CATIA holds only "
            f"{len(names)}: {listed}"
            + (". Not in the loft: " + ", ".join(f"'{name}'" for name in missing)
               if missing else "")
            + ". The volume above is the volume of what it DID build, so do not "
            "take it as agreement with the sections you meant."
        )

    @staticmethod
    def _coupling_warning(facts: dict[str, dict[str, Any]]) -> str:
        """Name the one thing a caller cannot see: a right volume, a wrong shape.

        CATIA pairs loft sections by PARAMETER, so two closed splines sampled
        differently are lofted index-by-index, not geometry-by-geometry: 59
        points against 60 put min y at -10335 mm where the sections' own -6900
        was meant, and the call still answered ok:true with a plausible volume
        (reported 2026-11-07, P2). The point counts come from the sketch record,
        so the mismatch is knowable before anyone opens a screenshot.
        """
        counts = {name: fact["points"] for name, fact in facts.items()
                  if fact.get("points")}
        if len(counts) < 2 or len(set(counts.values())) == 1:
            return ""
        listed = ", ".join(f"'{name}' {count}" for name, count in counts.items())
        return (f" WARNING: the sections do not hold the same number of points "
                f"({listed}). CATIA pairs sections by PARAMETER, so the surface "
                "twists between them even when the volume looks plausible - "
                "measured on B30 2026-11-07: 59 against 60 points bulged the "
                "stern by +49.8% (min y -10335 mm against a nominal -6900) and "
                "this tool answered ok:true. Sample both sections the same way - "
                "same segment counts, same start point, same direction - and "
                "rebuild before trusting the shape.")

    # How many CURVES each recorded drawing call leaves in the sketch. A
    # rectangle is one entry in the record and four curves in the sketch, and
    # that difference is the whole question a loft section asks - counting
    # record entries called a 4-line rectangle "one element" and let the
    # generic error through on exactly the case the reporter hit (2026-11-07,
    # P1). A closed spline is ONE curve: it repeats its first point rather than
    # closing with a line.
    _CURVES_PER_KIND = {"line": 1, "rectangle": 4, "arc": 1, "circle": 1,
                        "ellipse": 1, "spline": 1, "point": 0}

    def _section_facts(self, name: str) -> dict[str, Any] | None:
        """What the sketch record says about one loft section, or None.

        Only sketches drawn in THIS session have a record, and only they can be
        described: ``Sketch`` gives no readable geometry back. ``None`` is
        therefore "not known", never "single curve".
        """
        outlines = getattr(self.conn, "sketch_outlines", None) or {}
        try:
            document = str(self.conn.active_document.Name)
        except Exception:  # noqa: BLE001 - no record under a guessed key
            return None
        record = outlines.get(f"{document}::{name}")
        if not record:
            return None
        elements = record.get("elements") or []
        kinds: dict[str, int] = {}
        curves = 0
        points = 0
        for element in elements:
            kind = str(element.get("kind"))
            kinds[kind] = kinds.get(kind, 0) + 1
            curves += self._CURVES_PER_KIND.get(kind, 1)
            count = element.get("points")
            if isinstance(count, int) and not isinstance(count, bool):
                points += count
        return {"elements": len(elements), "curves": curves,
                "kinds": kinds, "points": points}

    @staticmethod
    def _describe_facts(facts: list[tuple[str, dict[str, Any] | None]]) -> str:
        """One line per section that has a record; unknown ones are left out."""
        parts = []
        for name, fact in facts:
            if not fact:
                continue
            kinds = ", ".join(f"{count} {kind}(s)"
                              for kind, count in sorted(fact["kinds"].items()))
            curves = fact["curves"]
            if curves == 1 and set(fact["kinds"]) == {"spline"}:
                curve = "one closed curve"
            elif curves == 1:
                curve = f"one curve ({kinds})"
            else:
                curve = f"{curves} curves ({kinds})"
            points = f", {fact['points']} points" if fact["points"] else ""
            parts.append(f"'{name}' {curve}{points}")
        return "; ".join(parts) if parts else "no drawing record for any section"

    def _section_reference(self, name: str) -> Any:
        """A Reference for a loft section: sketch, GSD curve, or Edge.N / Face.N."""
        part = self.conn.get_active_part()
        body = self.conn.get_active_part_body()
        cleaned = (name or "").strip()
        if re.fullmatch(r"(Edge|Face|Vertex)\.\d+", cleaned, re.I):
            kind = cleaned.split(".")[0].capitalize()
            index = int(cleaned.split(".")[1])
            return topology_reference(self.conn, body, kind, index).reference
        # A rebuilt sketch is the same profile under a new name, so a section may
        # name it by either - the same rule catia_pad and the sketcher follow
        # (reported 2026-10-28: the resolver reached every tool except this one).
        wanted = [cleaned]
        alias = self._sketch_alias(cleaned)
        if alias and alias != cleaned:
            wanted.append(alias)
        for target in wanted:
            for i in range(1, part.Bodies.Count + 1):
                candidate = part.Bodies.Item(i)
                try:
                    return part.CreateReferenceFromObject(
                        candidate.Sketches.Item(target))
                except Exception:  # noqa: BLE001 - not this body
                    pass
            for i in range(1, part.HybridBodies.Count + 1):
                hb = part.HybridBodies.Item(i)
                for collection in ("HybridShapes", "HybridSketches"):
                    try:
                        element = getattr(hb, collection).Item(target)
                    except Exception:  # noqa: BLE001
                        continue
                    return part.CreateReferenceFromObject(element)
        # ASK THE PART WHAT IT HAS before refusing: a plan written before the run
        # cannot know that `catia_create_sketch` renamed its sketch to 草图.1#2
        # (2026-11-15 report, R3), and "go and query it yourself" turns a naming
        # detail into a round trip. The list is capped - a 200-sketch part does
        # not belong in an error message.
        available: list[str] = []
        for index in range(1, part.Bodies.Count + 1):
            try:
                sketches = part.Bodies.Item(index).Sketches
                for item in range(1, int(sketches.Count) + 1):
                    try:
                        available.append(str(sketches.Item(item).Name))
                    except Exception:  # noqa: BLE001 - one item, not the rest
                        continue
            except Exception:  # noqa: BLE001 - a body without a sketch list
                continue
        for index in range(1, part.HybridBodies.Count + 1):
            holder = part.HybridBodies.Item(index)
            for collection in ("HybridShapes", "HybridSketches"):
                try:
                    items = getattr(holder, collection)
                    for item in range(1, int(items.Count) + 1):
                        available.append(str(items.Item(item).Name))
                except Exception:  # noqa: BLE001 - not this collection
                    continue
        listed = ", ".join(available[:40]) + (
            f" ... and {len(available) - 40} more" if len(available) > 40 else "")
        raise ArgumentRejected(
            f"No sketch or geometrical element named '{name}' to use as a loft "
            "section. What this part actually holds: "
            + (listed if available else "nothing - no sketch and no element")
            + ". catia_list_features (its 'sketches' array) and "
              "catia_gsd_list_elements list the same names. Names are exact: "
              "catia_create_sketch renames a colliding default name to 草图.N#2, "
              "so a plan written before the run can name a sketch that does not "
              "exist - pass catia_create_sketch(name=...) to pin it down."
            + (f" '{cleaned}' was re-created as '{alias}', which is not in the "
               "tree either." if alias and alias != cleaned else "")
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
            raise ArgumentRejected(
                f"No solid body named '{name}'. Bodies: {', '.join(names) or 'none'}."
            )
        if str(target.Name) == str(part.MainBody.Name):
            raise PreconditionRejected(
                f"'{name}' is the MainBody and cannot be deleted: every part needs "
                "one. Delete its features instead, or build the geometry in another "
                "body and leave this one empty."
            )
        try:
            count = target.Shapes.Count
        except Exception:  # noqa: BLE001 - unknown, so do not force
            count = None
        if count and not force:
            raise ArgumentRejected(
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
            raise CodedError(
                f"CATIA accepted the deletion but '{name}' is still there "
                f"({before} -> {after} bodies). Close the document without saving "
                "and rebuild, or empty the body and leave it."
            , code="DELETE_NOT_CONFIRMED", stage="execute")
        return (f"Body '{name}' deleted ({before} -> {after} bodies)."
                + (f" Its {count} feature(s) went with it." if count else ""))

    # ────────────────────────────────────────────────────────────
    # Moving a solid body — boundary translate + CloseSurface
    # ────────────────────────────────────────────────────────────

    #: A rigid move changes neither volume nor area; these are the RELATIVE
    #: tolerances those two comparisons use. 1e-6 is a millionth of the body, far
    #: below any real change of shape and far above the double-precision noise of
    #: a measurable (measured 2026-11-23: 90000.0000 vs 90000.0000).
    MOVE_VOLUME_TOL = 1e-6
    #: ... and the centre of gravity has to move by the vector asked for, in mm.
    #: A translation moves it EXACTLY, so this is loose only for the measurable's
    #: own rounding.
    MOVE_COG_TOL = 5e-3

    def _solid_stats(self, part: Any, body: Any
                     ) -> tuple[float | None, float | None, list[float] | None,
                                str]:
        """``(volume_mm3, area_mm2, centre_mm, note)`` for one body's solid."""
        volume, cog, note = self._mass_probe(part, body)
        if volume is None:
            return None, None, None, note or "the body holds no solid to measure"
        area = None
        try:
            doc = part.Parent
            spa = doc.GetWorkbench("SPAWorkbench")
            measurable = spa.GetMeasurable(part.CreateReferenceFromObject(body))
            area = float(measurable.Area) * 1e6            # m2 -> mm2
        except Exception:  # noqa: BLE001 - the volume still answers
            area = None
        return volume, area, cog, ""

    def _free_body_name(self, part: Any, wanted: str) -> str:
        """``wanted`` or ``wanted.2``/``.3``… - a name no body or set already has.

        Nothing in CATIA stops two tree nodes from sharing a name, and this
        server has produced duplicates before (see GSDTools._free_name). The
        moved body is meant to be ADDRESSABLE by the reply's own sentence, so the
        name is chosen against the live tree rather than assumed.
        """
        taken = set()
        for collection in (part.Bodies, part.HybridBodies):
            try:
                for index in range(1, collection.Count + 1):
                    taken.add(str(collection.Item(index).Name))
            except Exception:  # noqa: BLE001 - a collection that will not enumerate
                taken.add(str(wanted))
        candidate = str(wanted)
        for index in range(2, 52):
            if candidate not in taken:
                return candidate
            candidate = f"{wanted}.{index}"
        return candidate

    def _default_body(self, part: Any) -> Any:
        """The body a call acts on when no name was given: active, else MainBody.

        ``catia_new_body`` switches the active body, so "the one features are
        going into" is what a caller means by "the body" - but the connection
        remembers no body until something asks, and the MainBody is the only
        answer that always exists.
        """
        try:
            active = self.conn.get_active_part_body()
        except Exception:  # noqa: BLE001 - the MainBody still answers
            active = None
        return active if active is not None else part.MainBody

    def _hide_node(self, node: Any) -> bool | None:
        """Hide one tree node; ``True``/``False`` from the read-back, None if it
        could not even be asked. SetShow(0) shows and SetShow(1) hides here."""
        sel = self.conn.hso
        try:
            sel.Clear()
            sel.Add(node)
            sel.VisProperties.SetShow(1)
            try:
                state = sel.VisProperties.GetShow()
            except Exception:  # noqa: BLE001 - hiding still happened
                return None
            if isinstance(state, (tuple, list)):
                return bool(state[1]) if len(state) > 1 else bool(state[0])
            return bool(state)
        except Exception:  # noqa: BLE001
            return None
        finally:
            try:
                sel.Clear()
            except Exception:  # noqa: BLE001
                pass

    def _move_body(self, args: dict[str, Any]) -> str:
        """Move a whole solid body, and prove the shape came with it unchanged.

        The 2026-11-23 report (P1-4) lost two full rebuilds of a 15-feature part
        to a first pad being 5 mm out, and asked for exactly this call: "with
        only this, one call would have fixed it".

        There is no solid transform in V5's automation API, and the three routes
        tried on B30 2026-11-23 all failed in their own way:

        * ``Part``/``Body``/``Shape`` declare no move/translate/position member
          (TypeInfo, 29/41 members read out by name — the earlier "0 members"
          reading came from calling GetDocumentation, which raises here);
        * ``ShapeFactory.AddNewTranslate2(distance)`` and
          ``AddNewAxisToAxis2(ref, target)`` DO return a feature
          (``平移.1`` / ``定位变换.1`` appended to ``body.Shapes``), but that
          feature rebuilds nowhere: ``UpdateObject`` and ``part.Update`` both
          fail on the body, on the pad, and with the body in the selection, and
          the object exposes no ``Distance``/``Vector``/``ElemToTranslate`` to
          set, so the missing argument cannot be supplied;
        * GSD's ``AddNewTranslate(bodyRef, direction, distance)`` is accepted and
          produces the body's BOUNDARY - measured: Area 13450.0000 mm2 (the
          solid's own), Volume raises, part volume unchanged.

        So the boundary is what this tool moves, and ``CloseSurface`` turns it
        back into a solid. Measured on B30 2026-11-23: a 25 x 45 x 80 block moved
        5 mm in x gives V 90000.0000 / A 13450.0000 (both unchanged, to six
        decimals) with the centre of gravity at (17.5000, 22.5000, 40.0000) -
        exactly (12.5, 22.5, 40) + (5, 0, 0); a body with a through hole
        83716.8147 -> 83716.8147; two disjoint pads in one body 102000.0000 ->
        102000.0000 with the envelope x 0..110 -> 0..120; a filleted block
        44690.9734 -> 44690.9734. Three of those four are the cases where a
        careless version would have lost material silently, which is why the
        checks below are refusals and not warnings.

        The costs, in the reply as well as here: the moved body is a dead solid
        (one CloseSurface), it lands in a NEW body because a CloseSurface in the
        same body as the surface it closes is circular and fails to update, the
        source body keeps every feature (hidden by default so the picture and the
        mesh envelope show one solid), and a feature that referenced the old
        body's faces does not move.
        """
        self.conn.ensure_connected()
        delta = (float(args.get("dx") or 0.0), float(args.get("dy") or 0.0),
                 float(args.get("dz") or 0.0))
        distance = math.sqrt(sum(component * component for component in delta))
        if distance <= 1e-9:
            raise ArgumentRejected(
                "dx, dy and dz are all zero, so there is nothing to move. Pass at "
                "least one non-zero component in millimetres.",
                code="BAD_ARGUMENT", stage="validate", modified=False)

        part, target_note = self._target_part(args.get("document"), activate=True)
        body = (self._body_named(part, str(args["name"])) if args.get("name")
                else self._default_body(part))
        body_name = str(body.Name)
        volume_before, area_before, cog_before, why = self._solid_stats(part, body)
        if volume_before is None:
            raise PreconditionRejected(
                f"'{body_name}' has no solid to move: {why}. A body that only "
                "holds sketches or surfaces has nothing to translate - nothing "
                "was changed.",
                code="BODY_HAS_NO_SOLID", stage="validate", modified=False,
                next_actions=[
                    "catia_list_bodies shows each body's volume and feature count",
                    "if the geometry is in a geometrical set, it is a surface: "
                    "catia_gsd_translate moves that on its own",
                ])

        wanted_body = str(args.get("new_body_name") or f"{body_name}_MOVED")
        moved_name = self._free_body_name(part, wanted_body)
        geoset_name = self._free_body_name(part, f"{body_name}_MOVE")
        features_before = []
        try:
            features_before = [str(shape.Name) for shape in body.Shapes]
        except Exception:  # noqa: BLE001 - the count is a courtesy
            features_before = []

        factory = part.HybridShapeFactory
        saved_in_work = getattr(part, "InWorkObject", None)
        translate = None
        closed = None
        try:
            direction = factory.AddNewDirectionByCoord(
                *(component / distance for component in delta))
            geoset = part.HybridBodies.Add()
            geoset.Name = geoset_name
            geoset.AppendHybridShape(direction)
            direction.Name = f"{geoset_name}_dir"
            part.UpdateObject(direction)

            translate = factory.AddNewTranslate(
                part.CreateReferenceFromObject(body), direction, distance)
            geoset.AppendHybridShape(translate)
            translate.Name = f"{geoset_name}_TRANS"
            part.UpdateObject(translate)

            moved = part.Bodies.Add()
            moved.Name = moved_name
            part.InWorkObject = moved
            closed = part.ShapeFactory.AddNewCloseSurface(
                part.CreateReferenceFromObject(translate))
            part.UpdateObject(closed)
        except Exception as exc:  # noqa: BLE001 - the leftovers are named below
            reason = str(exc).splitlines()[0][:200]
            try:
                part.InWorkObject = saved_in_work
            except Exception:  # noqa: BLE001
                pass
            raise CodedError(
                f"CATIA refused to build the moved solid for '{body_name}': "
                f"{reason}. The translate step is the one that usually refuses, "
                "and it does so when the body's boundary cannot be built as a "
                "closed shell - a body with an open surface in it, or one whose "
                f"last feature is broken. Leftovers in the tree: geometrical set "
                f"'{geoset_name}'"
                + (f" and body '{moved_name}'" if closed is None and translate else "")
                + ". Nothing of the original geometry was changed.",
                code="MOVE_FAILED", stage="execute", modified=True,
                data={"body": body_name, "geoset": geoset_name,
                      "moved_body": moved_name if translate else None,
                      "volume_before_mm3": round(volume_before, 4)},
                next_actions=[
                    "catia_delete_feature(names=['" + geoset_name + "_TRANS', "
                    "'" + geoset_name + "_dir']) removes the leftovers",
                    "catia_diagnose reports a body whose last feature fails to "
                    "update",
                ])
        finally:
            try:
                part.InWorkObject = saved_in_work
            except Exception:  # noqa: BLE001
                pass

        volume_after, area_after, cog_after, after_why = self._solid_stats(
            part, part.Bodies.Item(moved_name))
        if volume_after is None:
            raise CodedError(
                f"The moved solid was built in body '{moved_name}' but cannot be "
                f"measured: {after_why}. Do not use it - the source body "
                f"'{body_name}' is untouched.",
                code="MOVE_UNVERIFIED", stage="execute", modified=True,
                data={"body": body_name, "moved_body": moved_name})

        problems = []
        if abs(volume_after - volume_before) > self.MOVE_VOLUME_TOL * max(
                1.0, volume_before):
            problems.append(
                f"volume {volume_before:.4f} -> {volume_after:.4f} mm3")
        if (area_before is not None and area_after is not None
                and abs(area_after - area_before) > self.MOVE_VOLUME_TOL * max(
                    1.0, area_before)):
            problems.append(f"area {area_before:.4f} -> {area_after:.4f} mm2")
        if cog_before and cog_after:
            moved_by = [cog_after[index] - cog_before[index] for index in range(3)]
            if any(abs(moved_by[index] - delta[index]) > self.MOVE_COG_TOL
                   for index in range(3)):
                problems.append(
                    "centre of gravity moved ("
                    + ", ".join(f"{value:.4f}" for value in moved_by)
                    + ") mm, not (" + ", ".join(f"{value:.4f}" for value in delta)
                    + ")")
        if problems:
            raise CodedError(
                f"The move was built but is NOT RIGID: "
                + "; ".join(problems)
                + ". A translate that changes the volume or the area has changed "
                f"the shape, so it is refused rather than reported as a move. The "
                f"result is in body '{moved_name}' and the source '{body_name}' is "
                "untouched - delete the moved body if you do not want it.",
                code="MOVE_NOT_RIGID", stage="execute", modified=True,
                data={"body": body_name, "moved_body": moved_name,
                      "delta": list(delta),
                      "volume_before_mm3": round(volume_before, 4),
                      "volume_after_mm3": round(volume_after, 4),
                      "area_before_mm2": None if area_before is None
                      else round(area_before, 4),
                      "area_after_mm2": None if area_after is None
                      else round(area_after, 4)})

        surface_hidden = self._hide_node(translate) if translate is not None else None
        hide_wanted = bool(args.get("hide_source", True))
        source_hidden = self._hide_node(body) if hide_wanted else None
        self.conn.refresh_display()

        feature_names = []
        try:
            feature_names = [str(shape.Name)
                             for shape in part.Bodies.Item(moved_name).Shapes]
        except Exception:  # noqa: BLE001
            feature_names = []

        moved_by = ([cog_after[index] - cog_before[index] for index in range(3)]
                    if cog_before and cog_after else list(delta))
        reply = (
            f"Moved '{body_name}' by ("
            + ", ".join(f"{value:.4f}" for value in delta)
            + f") mm. The solid is now in body '{moved_name}'"
            + (f" (feature(s): {', '.join(feature_names)})" if feature_names else "")
            + ".\n"
            "WHY IT IS BUILT THIS WAY: CATIA V5 has no transform for a solid. "
            "Part, Body and Shape declare no move/translate/position member; "
            "ShapeFactory's AddNewTranslate2 and AddNewAxisToAxis2 return a feature "
            "that rebuilds nowhere and exposes nothing to set; and GSD's translate "
            "on a body gives its boundary (area equal to the solid's, volume "
            "absent). So the boundary was translated and closed back into a solid "
            "- which is a rigid motion, and is checked as one.\n"
            "CHECKED: volume "
            + f"{volume_before:.4f} -> {volume_after:.4f} mm3"
            + (f", area {area_before:.4f} -> {area_after:.4f} mm2"
               if area_before is not None and area_after is not None else "")
            + ", centre of gravity moved ("
            + ", ".join(f"{value:.4f}" for value in moved_by)
            + ") mm. A move that changed the volume or the area would have been "
            "refused as MOVE_NOT_RIGID.\n"
            "WHAT THIS COSTS: the result is a DEAD SOLID - geometry only, no "
            "sketch, pad or fillet history. The source body is still in the tree "
            "with its "
            f"{len(features_before)} feature(s)"
            + ("" if not hide_wanted else
               " and is now HIDDEN, so the view and the mesh envelope show one "
               "solid; pass hide_source=false to keep both visible"
               if source_hidden else
               " and the hide was REFUSED, so both solids are on screen"
               if source_hidden is False else
               " and whether it is hidden could not be read back")
            + ("" if hide_wanted else
               "; it is still VISIBLE, so the part currently holds both solids "
               "and any envelope you measure covers both")
            + f". Any feature that referenced '{body_name}'s faces did not move. "
            f"The translated surface stays in geometrical set '{geoset_name}' "
            "because the closed solid depends on it"
            + ("." if surface_hidden is not False else
               ", and it is still VISIBLE.")
            + (f" {target_note}." if target_note else "")
        )
        return reply

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
            raise ArgumentRejected(
                f"No solid body named '{name}'. Bodies: {names or 'none'}."
            )
        self.conn.active_body = body
        try:
            part.InWorkObject = body
        except Exception:
            pass
        # Read the assignment back. Setting InWorkObject reports no error while
        # CATIA keeps pointing somewhere else (reported 2026-10-26: after a rolled
        # back pad the status line still said "in_work_object=Datum_xy_20" even
        # though this call had just claimed the body), and a Part Design factory
        # called with a datum plane in work fails with a bare "method failed" -
        # the exact shape the diagnostic reports blame on the sketch.
        in_work = self._in_work_name(part)
        self.conn.refresh_display()
        if in_work and in_work != str(body.Name):
            return (
                f"Body '{body.Name}' is now active ({body.Shapes.Count} feature(s)) "
                f"and later features go into it, but CATIA still reports "
                f"in_work_object='{in_work}'. Re-run this call, or close the "
                "sketch that is still in edition (catia_close_sketch) - a Part "
                "Design factory called with anything but a body in work fails with "
                "a bare 'method failed'."
            )
        return (
            f"Body '{body.Name}' is now active ({body.Shapes.Count} feature(s)). "
            "Later pad / pocket / hole / fillet features go into it."
        )

    @staticmethod
    def _in_work_name(part: Any) -> str:
        """Name of part.InWorkObject, or '' when it cannot be read."""
        try:
            return str(part.InWorkObject.Name)
        except Exception:  # noqa: BLE001
            return ""

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
        # A STALE HANDLE IS NOT A SILENT "NO ACTIVE BODY" (0.3.95). A boolean
        # consumes its tool body; with conn.active_body still pointing at the
        # consumed one, every entry above reads active:false and the caller is
        # left holding three bodies and no idea where the next feature lands
        # (reported 2026-11-10, P2-3: 「全部 active: false，且没有任何一个叫
        # 几何体.5 的 body 被列出」).
        payload["active_body"] = active_name or None
        if active_name and not any(entry["active"] for entry in out):
            payload["active_body_missing"] = True
            payload["active_body_note"] = (
                f"the session's active body is '{active_name}', which is NOT in "
                "this list - a boolean consumes its tool body, and the handle was "
                "left on the consumed one. Nothing here says which body the next "
                "feature would go into; pick one with "
                "catia_set_active_body(name=...).")
        # WHOSE NUMBERS THESE ARE (0.3.93). A part whose update failed keeps
        # answering with the last valid volume and face count - no error, no
        # stale flag, just numbers that describe the state BEFORE the change the
        # caller just made. The 2026-11-10 report trusted 168607.5 mm3 twice on a
        # part that could not update at all. Only this server knows its own update
        # results, so that is what it says; CATIA itself gives no hint.
        part_error = getattr(self.conn, "part_in_error", None)
        if part_error:
            payload = {
                "stale": True,
                "stale_because": ("the last full update of this part failed: "
                                 + str(part_error).splitlines()[0][:160]),
                **payload,
                "stale_note": (
                    "CATIA keeps answering with the last valid volume and face "
                    "count while the tree is in error, so the numbers above may "
                    "describe the state BEFORE the change that broke it. Run "
                    "catia_update_part to see what fails; if a boolean or feature "
                    "just answered success, do not trust that until it does."),
            }
        if total_excluded:
            payload["faces_note"] = (
                f"{total_excluded} face(s) the search also returns are datum-plane "
                "borders or sketch wires, and are excluded here exactly as in "
                "catia_measure_topology and catia_list_faces - the counts agree.")
        return json.dumps(payload, indent=2)

    _VISIBILITY_MAX_DEPTH = 6

    def _visibility_parts(self) -> list[tuple[str, Any, Any]]:
        """``(label, part, selection)`` for the document visibility applies to.

        A Part document answers for itself. A **Product** answers for every part
        under it, and that is not a convenience: hiding the construction geometry
        of a finished assembly is the job, and until 0.3.73 this tool refused it
        with "Active document 'ASM.CATProduct' exposes no 'Part'" - which left the
        reporter 33 calls (11 parts x activate / hide / save) to clean up a
        screenshot, and each of those calls moves the active document
        (reported 2026-11-06, P2-6).

        The parts are reached through the component tree, not through
        ``Documents``: a component that has no window of its own is still in
        ``Documents``, but a sub-assembly's parts only appear by walking. Each part
        is touched through ITS OWN Selection, so the active document never moves
        and the assembly stays in front.
        """
        doc = self.conn.active_document
        try:
            return [(str(doc.Name), self.conn.part_of(doc), doc.Selection)]
        except Exception:  # noqa: BLE001 - not a part; try the product tree
            pass
        try:
            product = self.conn.product_of(doc)
        except Exception as error:  # noqa: BLE001
            raise PreconditionRejected(
                f"'{getattr(doc, 'Name', '?')}' is neither a Part nor a Product, "
                "so there is no tree to walk for visibility. Open a part or an "
                "assembly first (the call changed nothing)."
            ) from error
        found: list[tuple[str, Any, Any]] = []
        self._collect_component_parts(product, 1, found, set())
        if not found:
            raise PreconditionRejected(
                f"'{getattr(doc, 'Name', '?')}' is a Product but no part under it "
                "could be read, so there is nothing to show or hide. An empty "
                "product has no geometry: insert a component first "
                "(catia_add_component) - nothing was changed."
            )
        return found

    def _collect_component_parts(self, product: Any, depth: int,
                                 out: list[tuple[str, Any, Any]],
                                 seen: set[str]) -> None:
        """One level of the component tree, recursing into sub-assemblies."""
        try:
            count = int(product.Products.Count)
        except Exception:  # noqa: BLE001 - an unreadable level is not fatal
            return
        for index in range(1, count + 1):
            try:
                child = product.Products.Item(index)
                child_doc = child.ReferenceProduct.Parent
            except Exception:  # noqa: BLE001 - a dead link
                continue
            try:
                child_part = self.conn.part_of(child_doc)
            except Exception:  # noqa: BLE001 - a sub-assembly, not a part
                child_part = None
            if child_part is not None:
                # One entry per PART, not per instance: hiding a part's sketches
                # twice is the same work, and the 70-instance assembly would
                # otherwise walk the same document 70 times.
                key = str(getattr(child_doc, "Name", "")).casefold()
                if key and key in seen:
                    continue
                seen.add(key)
                out.append((str(getattr(child_doc, "Name", key)),
                            child_part, child_doc.Selection))
                continue
            if depth >= self._VISIBILITY_MAX_DEPTH:
                continue
            try:
                nested = self.conn.product_of(child_doc)
            except Exception:  # noqa: BLE001 - neither part nor product
                continue
            self._collect_component_parts(nested, depth + 1, out, seen)

    def _find_component(self, product: Any, name: str,
                        prefix: str = "") -> tuple[str, Any] | None:
        """The instance whose name (or path) is ``name``, with its path.

        Recurses into sub-assemblies: a 15-instance product usually has two
        levels, and a name that only exists one level down is still the name the
        caller sees in the tree.
        """
        try:
            children = product.Products
        except Exception:  # noqa: BLE001 - a leaf component has no Products
            return None
        try:
            count = int(children.Count)
        except Exception:  # noqa: BLE001 - unreadable list, no claim
            return None
        for index in range(1, count + 1):
            try:
                child = children.Item(index)
                child_name = str(child.Name)
            except Exception:  # noqa: BLE001 - one unreadable instance
                continue
            path = f"{prefix}{child_name}"
            if name in (child_name, path):
                return path, child
            deeper = self._find_component(child, name, f"{path}/")
            if deeper is not None:
                return deeper
        return None

    def _set_component_visibility(self, name: str, show: bool) -> str | None:
        """Hide/show an assembly COMPONENT by instance name; None = not a component.

        The read-back is the point. ``Selection.VisProperties.SetShow`` needs
        something selected, and ``Product`` exposes no readable Visibility member
        on this build (measured 2026-11-23: ``Visibility``, ``IsFixed``,
        ``IsInactive``, ``IsRoot`` and ``IsReference`` all raise ``Item.X``), so
        the only honest way to answer is to select the instance again and ask
        ``GetShow()`` - which answers ``(0, 1)`` hidden, ``(0, 0)`` shown.
        """
        try:
            document = self.conn.active_document
            product = getattr(document, "Product", None)
        except Exception:  # noqa: BLE001 - no document, no components
            return None
        if product is None:
            return None
        found = self._find_component(product, name)
        if found is None:
            return None
        path, instance = found
        selection = self.conn.hso
        try:
            selection.Clear()
            selection.Add(instance)
            selection.VisProperties.SetShow(0 if show else 1)
            selection.Clear()
            selection.Add(instance)
            state = selection.VisProperties.GetShow()
        except Exception as error:  # noqa: BLE001 - the instance is not selectable
            raise CodedError(
                f"'{path}' is a component of this product, but CATIA refused to "
                f"change its visibility ({str(error).splitlines()[0][:120]}). "
                "Nothing was changed. Instance visibility needs the instance "
                "selectable in the product document; catia_set_visibility on the "
                "part under it still works for the part's own tree.",
                code="COMPONENT_VISIBILITY_FAILED", stage="execute",
                modified=False) from error
        finally:
            try:
                selection.Clear()
            except Exception:  # noqa: BLE001 - a selection left behind
                pass
        hidden = None
        try:
            hidden = bool(int(state[1]))
        except Exception:  # noqa: BLE001 - a build that answers a bare int
            hidden = None
        wanted_hidden = not show
        note = ""
        if hidden is not None and hidden != wanted_hidden:
            note = (f" THE READ-BACK DISAGREES: GetShow() answered {tuple(state)}, "
                    f"so it is {'hidden' if hidden else 'shown'}, not "
                    f"{'hidden' if wanted_hidden else 'shown'}.")
        others = 0
        try:
            others = int(product.Products.Count) - 1
        except Exception:  # noqa: BLE001 - a count is a courtesy
            pass
        self.conn.refresh_display()
        return (
            f"Component '{path}' is now {'shown' if show else 'hidden'}"
            + (f" (read back: VisProperties.GetShow() -> {tuple(state)})"
               if hidden is not None else "")
            + note
            + (f" The other {others} component(s) of the product are untouched."
               if others > 0 else "")
            + " Component visibility is session state in the PRODUCT document - "
            "save the product if it has to survive the session."
        )

    def _set_visibility(self, name: str, show: bool) -> str:
        """Show or hide a tree node BY NAME, in one part or in a whole assembly.

        Visibility is not cosmetic here: a GSD surface left visible after
        CloseSurface overlays the solid, and an STL export or the mesh bounding
        box then includes it - so both the picture and the measured envelope come
        out wrong. Field case: a two-blade propeller looked like one blade had an
        extra lobe, the envelope read 45.6 mm wide instead of 30.1, and nothing
        was actually wrong with the geometry.

        0.3.73: a Product walks every part under it instead of refusing. Each part
        is touched through its OWN Selection, so the active document never moves -
        which is the point, because the assembly is what the caller is looking at.

        0.3.161: an ASSEMBLY COMPONENT can be named too, which it could not
        before. `catia_set_visibility(name="Part1.1")` answered ARGUMENT_REJECTED
        ("No tree node named 'Part1.1' in any of the 4 part(s)") because the name
        was only ever looked for in a PART's tree, and a component instance is not
        a tree node of a part - so "hide that side plate and look inside the
        frame" was impossible (2026-11-23 report, P2-1). The instance is selected
        in the PRODUCT's own Selection and its VisProperties.SetShow is used,
        then READ BACK with GetShow: measured on B30 2026-11-23, GetShow answers
        (0, 1) when hidden and (0, 0) when shown, so the reply states a reading
        rather than a wish.
        """
        self.conn.ensure_connected()
        component = self._set_component_visibility(name, show)
        if component is not None:
            return component
        targets = self._visibility_parts()
        try:
            active = str(self.conn.active_document.Name)
        except Exception:  # noqa: BLE001 - the label is a courtesy
            active = "<the active document>"

        hits: list[tuple[str, str, int, str]] = []
        for label, part, sel in targets:
            target, where, parent_set = self._find_tree_node(part, name)
            if target is None:
                continue

            def apply(node, sel=sel):
                sel.Clear()
                sel.Add(node)
                # Field-measured on V5-6R2020 B30, and not what the reference
                # suggests: 0 shows, 1 hides, 2 is accepted and does nothing.
                # Using 2 for "hide" is silently ignored, which is how a set of
                # GSD surfaces stayed on screen (and in every STL export) while
                # the tool reported it had hidden them.
                sel.VisProperties.SetShow(0 if show else 1)

            children = 0
            # EVERY node with this name, not the one Item() returns. P04 of the
            # 2026-11-06 build holds four features called 'Datum_frame' (this
            # server created all four before 0.3.74), and hiding one of them
            # answered "is now hidden" while three white diamonds stayed on the
            # solid - measured on B30 2026-11-07 with exactly that part.
            nodes = self._nodes_named(part, name) or [target]
            same_name = max(0, len(nodes) - 1)
            try:
                for node in nodes:
                    try:
                        apply(node)
                    except Exception:  # noqa: BLE001 - one node, not the rest
                        continue
                # Hiding a geometrical set does not hide what is inside it: each
                # element keeps its own visibility, so the surfaces stay on screen
                # and keep poisoning STL exports. One level of descent was not
                # enough - a set inside a set was skipped, and the bounding box
                # then still measured the wireframe while this tool answered
                # "hidden" (reported 2026-11-07, P8: the reply said 12 element(s)
                # and the export warning still counted the set as shown).
                if where in ("geometrical set", "hybrid body"):
                    children += self._hide_set_contents(target, apply, depth=0)
                # 0.3.115: showing a BODY also shows the features in it. Measured
                # on B30 2026-11-13: after a set-hide took a loft off screen,
                # set_visibility(name='零件几何体', show=true) answered "is now
                # shown" and changed nothing - the body node had never been hidden,
                # its FEATURE had. A caller who asks for the body back means the
                # geometry in it, and the report of 2026-11-12 said so ("实体消失；
                # set_visibility(零件几何体, show=true) 这次没能救回来").
                if show and where == "body":
                    try:
                        shapes = target.Shapes
                        for position in range(1, int(shapes.Count) + 1):
                            try:
                                apply(shapes.Item(position))
                                children += 1
                            except Exception:  # noqa: BLE001 - one feature
                                continue
                    except Exception:  # noqa: BLE001 - a body without Shapes
                        pass
            finally:
                try:
                    sel.Clear()
                except Exception:  # noqa: BLE001 - a selection left behind
                    pass
            # Whether this set CARRIES the solid. A GSD solid (ThickSurface,
            # CloseSurface) lives inside its geometrical set, not in the body, so
            # hiding the set hides the part - and showing the body back does
            # nothing, because the body is empty. The 2026-11-09 report hit
            # exactly that, followed this tool's own advice to hide the set
            # before a screenshot, and then had to guess feature names
            # ("加厚曲面.10", "凸台.1") to get the model back.
            solids = ([] if show else self._solid_carriers(target))
            hits.append((label, where, children, parent_set, same_name, solids))

        if not hits:
            where = ("in '" + active + "'" if len(targets) == 1
                     else f"in any of the {len(targets)} part(s) of '{active}'")
            raise ArgumentRejected(
                f"No tree node named '{name}' {where}. Use catia_list_bodies, "
                "catia_list_features or catia_gsd_list_elements to see names."
            )

        self.conn.refresh_display()
        state = "shown" if show else "hidden"
        if len(targets) == 1:
            _label, where, children, parent_set, same_name, solids = hits[0]
            extra = f" and {children} element(s) inside it" if children else ""
            if same_name:
                extra += (f", and {same_name} other tree node(s) that carry the "
                          "same name - all of them were set")
            # The one thing this reply has to say about hiding a SET: where the
            # model lives. Measured on B30 2026-11-09 (scripts/
            # b30_v0384_visibility.py): with a pad solid in the body and one
            # surface in the set, hiding the set left the solid alone - the
            # exported STL went 3778 B -> 3244 B (the surface's triangles out,
            # the solid's kept). So the failure mode the 2026-11-09 report hit is
            # NOT "a set hides the body"; it is "the element I was looking at was
            # inside that set", and the recovery is to show the SET, not the body.
            solid_note = ""
            if not show and where in ("geometrical set", "hybrid body"):
                if solids:
                    solid_note = (
                        " WARNING: this set holds what looks like the SOLID itself ("
                        + ", ".join(f"'{name}'" for name in solids[:4])
                        + (" ..." if len(solids) > 4 else "")
                        + "). If the view goes empty, show the SET again - a solid "
                        "kept inside a geometrical set is not reachable through "
                        "'零件几何体'.")
                else:
                    # 0.3.115 re-measured this on a LOFT, because the 2026-11-12
                    # report said the solid went with the set. It does not:
                    # measured on B30 2026-11-13 (scripts/b30_v0417_visibility_fix.py),
                    # a 2-section solid loft whose circles live in GSD_Set stayed
                    # on screen when the set was hidden, and the picture came back
                    # identical when it was shown again. What DOES take the solid
                    # is kind='construction' - those section curves are
                    # construction elements, and CATIA takes the feature's display
                    # with its input. That path warns for itself.
                    solid_note = (
                        " Note: a geometrical set is a separate branch from the body, "
                        "so this leaves a solid built in the body alone - "
                        "re-measured on B30 2026-11-13 on a solid LOFT whose section "
                        "curves live in this very set: the solid stayed on screen, "
                        "and showing the set again restored the picture byte for "
                        "byte. If the view DOES go empty, the geometry you were "
                        "looking at is an element INSIDE this set; "
                        "show the set again"
                        " with this same call and show=true, or name that one "
                        "element - measured 2026-11-09 on a thickened surface inside "
                        "a hidden set: set_visibility(name='加厚曲面.1', show=true) "
                        "brought the solid back without touching the rest of the "
                        "set. What is NOT safe is kind='construction': a Part "
                        "Design feature built on construction elements goes off "
                        "screen with them.")

            # An element inside a HIDDEN set stays invisible however it is set:
            # the UI greys the lightbulb and CATIA keeps the parent's visibility.
            # The reply used to say "is now shown" and leave it at that (reported
            # 2026-11-06, twice). The parent's own state cannot be read cheaply
            # (`HybridShape.Visible` raises), so the note names the parent instead
            # of guessing its state.
            parent_note = (
                f" Note: it lives in geometrical set '{parent_set}', and a hidden "
                f"set hides its contents - if it is still not on screen, show the "
                f"set first (catia_set_visibility(name='{parent_set}', "
                f"show=true))."
                if (show and parent_set) else "")
            return (f"'{name}' ({where}) is now {state}{extra}.{solid_note}"
                    f"{parent_note}")

        where = hits[0][1]
        children = sum(hit[2] for hit in hits)
        same_name = sum(hit[4] for hit in hits)
        solids = [name for hit in hits for name in hit[5]]
        labels = ", ".join(hit[0] for hit in hits[:8])
        if len(hits) > 8:
            labels += f", ... ({len(hits) - 8} more)"
        extra = (f", plus {children} element(s) inside the set(s)" if children
                 else "")
        if same_name:
            extra += (f", plus {same_name} other tree node(s) carrying the same "
                      "name")
        parents = sorted({hit[3] for hit in hits if hit[3]})
        note = (
            f" Note: {len(parents)} geometrical set(s) hold them "
            f"({', '.join(parents[:4])}): a hidden set hides its contents, so show "
            "the set as well when you want them back."
            if (show and parents) else "")
        if solids:
            note += (" WARNING: the set(s) being hidden hold the SOLID itself ("
                     + ", ".join(f"'{name}'" for name in solids[:4])
                     + (" ..." if len(solids) > 4 else "")
                     + "): a surface-generated solid lives in the geometrical set, "
                     "not in the body, so the body has nothing to show back - "
                     "re-show the set instead.")
        return (f"'{name}' ({where}) is now {state} in {len(hits)} of "
                f"{len(targets)} part(s) of '{active}': {labels}{extra}.{note}")

    # A solid made BY a surface operation is a GSD element, and it lives in the
    # geometrical set that owns it - not in the body. Hiding that set therefore
    # hides the part, which is what the 2026-11-09 report walked into while
    # following this tool's own advice. Neither the type name nor the localised
    # element name is reliable on its own (a typelib may answer "IDispatch"), so
    # both are consulted and a hit from either counts.
    _SOLID_TYPE_HINTS = ("ThickSurface", "CloseSurface", "Solid")
    _SOLID_NAME_HINTS = ("加厚曲面", "封闭曲面", "ThickSurface", "CloseSurface")

    @classmethod
    def _solid_carriers(cls, node: Any, depth: int = 0) -> list[str]:
        """Names of elements INSIDE ``node`` that are the solid, not decoration."""
        if depth > 4:
            return []
        found: list[str] = []
        for collection in ("HybridShapes", "HybridBodies"):
            try:
                items = getattr(node, collection)
            except Exception:  # noqa: BLE001 - the set does not hold this kind
                continue
            for index in range(1, items.Count + 1):
                try:
                    child = items.Item(index)
                except Exception:  # noqa: BLE001 - one item, not the rest
                    continue
                try:
                    name = str(getattr(child, "Name", "") or "")
                except Exception:  # noqa: BLE001 - a half-dead node
                    name = ""
                try:
                    kind = str(child._oleobj_.GetTypeInfo()
                               .GetDocumentation(-1)[0])
                except Exception:  # noqa: BLE001 - no type information
                    kind = ""
                if (any(hint in kind for hint in cls._SOLID_TYPE_HINTS)
                        or any(hint in name for hint in cls._SOLID_NAME_HINTS)):
                    found.append(name or kind or "<unnamed>")
                elif collection == "HybridBodies":
                    found.extend(cls._solid_carriers(child, depth + 1))
        return found

    @classmethod
    def _hide_set_contents(cls, node: Any, apply: Any, depth: int) -> int:
        """Set the visibility of everything inside a geometrical set, at any depth.

        A set's own visibility does not reach its contents: every element carries
        its own flag, so hiding the set leaves the surfaces on screen and in
        every STL export. Descending one level was the 0.3.73 fix and it was not
        enough - a set holding another set was skipped, which is the worst shape
        of bug for this tool: the reply says "hidden and N element(s) inside it"
        while the bounding box still measures the wireframe (reported 2026-11-07,
        P8). Recursion is bounded at four levels: deeper than that it is not a
        modelling tree, it is a cycle.
        """
        if depth > 4:
            return 0
        done = 0
        for collection in ("HybridShapes", "HybridSketches", "HybridBodies"):
            try:
                items = getattr(node, collection)
            except Exception:  # noqa: BLE001 - the set does not hold this kind
                continue
            for i in range(1, items.Count + 1):
                try:
                    child = items.Item(i)
                except Exception:  # noqa: BLE001 - one item, not the rest
                    continue
                try:
                    apply(child)
                    done += 1
                except Exception:  # noqa: BLE001 - one item, not the rest
                    continue
                if collection == "HybridBodies":
                    done += cls._hide_set_contents(child, apply, depth + 1)
        return done

    @staticmethod
    def _find_tree_node(part: Any, name: str) -> tuple[Any, str, str]:
        """``(node, kind, parent_set)`` for a tree node called ``name``, or None.

        Shared by everything that takes a tree name - ``catia_set_visibility``
        and the colour tools - so the walk exists ONCE: the list of places a
        feature can live is exactly the kind of thing that goes stale in one copy
        and not the other, and 0.3.74 already paid for that (the walk did not
        cover ``body.HybridShapes``, so the tool could not hide the datum plane it
        had created itself).

        ``Item(name)`` returns the FIRST match and says nothing about the rest;
        callers that must reach all of them use :meth:`_nodes_named` as well.
        """
        target, where, parent_set = None, "", ""

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
            # Hybrid shapes INSERTED INTO A SOLID BODY. The frame sketch's datum
            # plane and its three points go in there on purpose (a plane in a
            # geometrical set poisons the fillet on the pad built on it), and
            # this walk only covered geometrical sets - so the tool that hides
            # things by name could not see the very features this server creates,
            # and answered "No tree node named" for a name it had picked itself
            # (reported 2026-11-07).
            try:
                hybrids = body.HybridShapes
            except Exception:      # noqa: BLE001 - body without hybrids
                hybrids = None
            if hybrids is not None:
                got, lbl = try_get(hybrids, "datum element")
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
                    target, where, parent_set = got, lbl, str(hb.Name)
                    break
                got, lbl = try_get(hb.HybridSketches, "sketch")
                if got is not None:
                    target, where, parent_set = got, lbl, str(hb.Name)
                    break
        return target, where, parent_set

    @staticmethod
    def _nodes_named(part: Any, name: str) -> list[Any]:
        """EVERY tree node in one part called ``name``, in tree order.

        Not what the by-name walk does, and the difference is the whole point:
        ``Collection.Item(name)`` hands back the first match and stays silent
        about the rest. A part can hold several nodes with one name - a body's
        features, the hybrid shapes a frame sketch puts in it, two sketches
        after a rebuild - and "hidden" while three others are still on screen is
        the kind of success report this server keeps having to withdraw.
        """
        found: list[Any] = []

        def scan(collection) -> None:
            try:
                count = int(getattr(collection, "Count", 0))
            except Exception:  # noqa: BLE001 - an unreadable collection
                return
            for index in range(1, count + 1):
                try:
                    node = collection.Item(index)
                    if str(node.Name) == name:
                        found.append(node)
                except Exception:  # noqa: BLE001 - one unreadable member
                    continue

        try:
            bodies = part.Bodies
        except Exception:  # noqa: BLE001 - no bodies at all
            bodies = None
        if bodies is not None:
            for index in range(1, int(getattr(bodies, "Count", 0)) + 1):
                try:
                    body = bodies.Item(index)
                except Exception:  # noqa: BLE001
                    continue
                try:
                    if str(body.Name) == name:
                        found.append(body)
                except Exception:  # noqa: BLE001
                    pass
                for collection in ("Shapes", "Sketches", "HybridShapes"):
                    try:
                        scan(getattr(body, collection))
                    except Exception:  # noqa: BLE001 - not on this build
                        continue
        try:
            sets = part.HybridBodies
        except Exception:  # noqa: BLE001
            sets = None
        if sets is not None:
            for index in range(1, int(getattr(sets, "Count", 0)) + 1):
                try:
                    group = sets.Item(index)
                except Exception:  # noqa: BLE001
                    continue
                try:
                    if str(group.Name) == name:
                        found.append(group)
                except Exception:  # noqa: BLE001
                    pass
                for collection in ("HybridShapes", "HybridSketches"):
                    try:
                        scan(getattr(group, collection))
                    except Exception:  # noqa: BLE001
                        continue
        return found

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

        0.3.73: a Product walks every part under it, through each part's own
        Selection. That is the case this was asked for - cleaning 11 parts took 33
        manual calls (activate / hide / save each), and the product-level call
        answered PRECONDITION_FAILED (reported 2026-11-06, P2-6).
        """
        self.conn.ensure_connected()
        targets = self._visibility_parts()
        try:
            active = str(self.conn.active_document.Name)
        except Exception:  # noqa: BLE001 - the label is a courtesy
            active = "<the active document>"
        total = {"sketch": 0, "datum_plane": 0, "datum_element": 0}
        touched: list[str] = []
        kept_material: list[str] = []

        want_sketch = kind in ("sketch", "construction")
        want_plane = kind in ("datum_plane", "construction")

        for label, part, sel in targets:
            counts = {"sketch": 0, "datum_plane": 0, "datum_element": 0}

            def apply(node, sel=sel) -> None:
                sel.Clear()
                sel.Add(node)
                sel.VisProperties.SetShow(0 if show else 1)

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
                    # ... and the datum elements that live INSIDE a solid body.
                    # A frame sketch puts its three points and its plane there
                    # (deliberately: a plane in a geometrical set poisons the fillet
                    # on the pad built on it), and this branch only ever counted the
                    # three origin planes - 33 for 11 parts, which is exactly what
                    # the 2026-11-08 report saw while a white frame stayed on screen
                    # (P2-3). Nothing in a BODY but the solid is construction, so the
                    # whole collection goes; geometrical sets are left alone, because
                    # there a surface is usually the design.
                    #
                    # 0.3.126: "nothing in a body but the solid is construction" was
                    # the wrong sentence in the right place - the SOLID IS IN THIS
                    # COLLECTION TOO. Measured on B30 2026-11-14 on a two-sketch
                    # solid loft: body.HybridShapes = ['Datum_xy_20', 'Body'], and
                    # hiding it left the window with nothing but the ground grid.
                    # That is the real cause of the effect 0.3.115 wrote down as
                    # "hiding the construction takes the solid with it" and blamed
                    # on the section curves being construction. They are not the
                    # cause: hiding the sketches ALONE leaves the solid fully
                    # visible (same measurement, picture compared by hash), and
                    # setting the loft's own flag back does not undo this, because
                    # it is the same node that was just switched off. So material
                    # features are skipped by name.
                    for i in range(1, part.Bodies.Count + 1):
                        try:
                            hybrids = part.Bodies.Item(i).HybridShapes
                        except Exception:      # noqa: BLE001 - body without any
                            continue
                        material = _material_names(part.Bodies.Item(i))
                        for j in range(1, hybrids.Count + 1):
                            try:
                                node = hybrids.Item(j)
                            except Exception:  # noqa: BLE001 - one element
                                continue
                            try:
                                label = str(node.Name)
                            except Exception:  # noqa: BLE001 - an unnamed node
                                label = ""
                            if label and label in material:
                                kept_material.append(label)
                                continue
                            try:
                                apply(node)
                                counts["datum_element"] += 1
                            except Exception:  # noqa: BLE001 - one element
                                pass
            finally:
                try:
                    sel.Clear()
                except Exception:  # noqa: BLE001 - a selection left behind
                    pass
            if counts["sketch"] or counts["datum_plane"] or counts["datum_element"]:
                touched.append(label)
                total["sketch"] += counts["sketch"]
                total["datum_plane"] += counts["datum_plane"]
                total["datum_element"] += counts["datum_element"]

        self.conn.refresh_display()

        if not total["sketch"] and not total["datum_plane"] \
                and not total["datum_element"]:
            where = ("the part has no sketches"
                     if len(targets) == 1 else
                     f"none of the {len(targets)} part(s) of '{active}' has any")
            raise PreconditionRejected(
                f"Nothing of kind '{kind}' found to {'show' if show else 'hide'}: "
                f"{where} (or no datum planes). Use catia_list_features to see "
                "what the tree holds."
            )
        counted = []
        if total["sketch"]:
            counted.append(f"{total['sketch']} sketch(es)")
        if total["datum_plane"]:
            counted.append(f"{total['datum_plane']} datum plane(s)")
        if total["datum_element"]:
            counted.append(f"{total['datum_element']} datum element(s) inside bodies")
        body = " and ".join(counted)
        # 0.3.115 WROTE A WARNING HERE THAT 0.3.126 MEASURED TO BE ABOUT THE WRONG
        # CAUSE. The old text said a solid loft "disappeared with" its construction
        # section curves, because CATIA stops displaying a feature whose input is
        # hidden. Measured on B30 2026-11-14, four captures of one document compared
        # by SHA-256:
        #   sketches hidden .................. solid VISIBLE, profiles gone
        #   3 origin planes hidden ........... picture identical to "all shown"
        #   body.HybridShapes hidden ......... SOLID GONE (grid only)
        #   SetShow(1) on the loft after ..... identical to "solid gone"
        # body.HybridShapes = ['Datum_xy_20', 'Body'] there - the loft itself is a
        # member of the collection the old loop emptied. So the solid was never
        # removed by a display dependency on its sections; it was switched off
        # directly, which is also why setting its flag back could not undo it.
        # That is fixed above (material names are skipped), so the warning is now
        # about what is still true: the sections' OWN curves go with them.
        skipped = (f" Kept {', '.join(repr(k) for k in kept_material[:3])}"
                   f"{f' and {len(kept_material) - 3} more' if len(kept_material) > 3 else ''}"
                   " visible on purpose: that name is also a feature in the body's"
                   " Shapes, i.e. it makes material. body.HybridShapes mixes the"
                   " two, and hiding the solid's own entry leaves the window empty"
                   " (measured on B30 2026-11-14)."
                   if kept_material else "")
        vanish = "" if show else (
            " The sketches and datum elements go with their profile curves; the"
            " SOLID stays exactly where it is - measured on B30 2026-11-14. (An"
            " earlier version of this sentence warned that the solid could vanish"
            " too. It could, and the cause was this call hiding the loft's own"
            " entry in body.HybridShapes - fixed, and the measurement is in the"
            " code comment.)" + skipped)
        if len(targets) == 1:
            return (f"{body} now {'shown' if show else 'hidden'}." + vanish)
        return (f"{body} now {'shown' if show else 'hidden'} in {len(touched)} of "
                f"{len(targets)} part(s) of '{active}': "
                + ", ".join(touched[:8])
                + (f", ... ({len(touched) - 8} more)" if len(touched) > 8 else "")
                + ". Nothing was saved: this is each part document's session "
                "state - save the parts (catia_save_document) if you need it "
                "persisted." + vanish)

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
            raise ArgumentRejected(
                f"No body named '{args['tool_body']}'. Bodies: {names or 'none'}."
            ) from None

        if args.get("target_body"):
            # ALIASES, because the body a part is born with is called whatever the
            # INSTALLATION calls it ('零件几何体' on a Chinese B30) and the 2026-11-19
            # report had to run catia_list_bodies purely to learn that string.
            # Only the two names a caller can mean without ambiguity are aliases;
            # anything else is still a name and is looked up as one.
            wanted = str(args["target_body"]).strip()
            alias = wanted.lower()
            if alias in ("active", "current"):
                target = self.conn.get_active_part_body()
            elif alias in ("first", "main", "mainbody", "partbody"):
                target = part.MainBody
            else:
                try:
                    target = bodies.Item(wanted)
                except Exception:
                    raise ArgumentRejected(
                        f"No body named '{wanted}'."
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
                raise ArgumentRejected(
                    f"'{tool.Name}' is the only candidate; pass target_body "
                    f"explicitly to say which body should survive."
                )

        if str(tool.Name) == str(target.Name):
            raise ArgumentRejected(
                "tool_body and target_body are the same body - a boolean needs two."
            )

        # THE NAME THE CALLER USED, KEPT APART FROM CATIA'S (0.3.95). A tool body
        # consumed by the boolean answers with an internal name afterwards
        # ('TouchTool' read back as '几何体.5'), and a message that silently
        # swaps them reads like a different body was operated on (reported
        # 2026-11-10, P2-2).
        requested_tool = str(args["tool_body"])
        tool_names_note = ("" if requested_tool == str(tool.Name) else
                           f" (CATIA now calls it '{tool.Name}')")

        # These four take the Body itself, not a Reference: passing a Reference
        # is accepted for add/remove/assemble and then does nothing, and
        # AddNewIntersect rejects it outright with a type mismatch.
        volume_before = self._body_volume_mm3(part, target)

        # A CUT WHOSE TOOL BODY NEVER REACHES THE TARGET CANNOT CUT (0.3.94).
        #
        # 0.3.93 found this out AFTER the fact - it compared volumes and reported
        # NO_MATERIAL, which is honest but leaves the caller with a call that had
        # to fail to learn that. The distance is measurable BEFORE anything is
        # built, and the test is one-sided: a positive gap proves the
        # intersection is empty, while 0 proves nothing (two bodies can touch at
        # an edge or a face and still share no volume - the same reason "distance
        # 0" was not accepted as "fits" for the split cutter in 0.3.89). So only
        # the positive case refuses here; everything else goes on to be measured.
        if operation in ("remove", "intersect"):
            gap = self._body_gap_mm(part, target, tool)
            if gap is not None and gap > 1e-6:
                raise FeatureRejected(
                    f"Nothing to {operation}: '{requested_tool}'{tool_names_note} is "
                    f"{gap:.4f} mm away from '{target.Name}' at its closest point, "
                    "so the two do not touch and the result would be "
                    + ("an unchanged body" if operation == "remove"
                       else "an EMPTY body")
                    + ". No feature was created - this was measured before "
                    "anything was built. A cut that stops a fraction of a "
                    "millimetre short looks exactly like this.",
                    rolled_back=False, code="TOOL_DOES_NOT_TOUCH",
                    next_actions=[
                        "catia_list_bodies shows each body's own volume and feature "
                        "count: if the tool body holds what you built, the problem "
                        "is where it sits, not whether it exists",
                        "If the tool body was meant to cut into a face, check the "
                        "pad direction and height that built it "
                        "(catia_list_features reports its depth), not the boolean",
                    ],
                )

        part.InWorkObject = target
        feature = getattr(part.ShapeFactory, method)(tool)
        if args.get("name"):
            feature.Name = args["name"]
        self._safe_update(part, feature)
        # BEFORE the no-material judgement below, and independently of it: the
        # type is a fact about the feature, not about whether it moved volume.
        self._remember_boolean(feature, operation)
        volume_after = self._body_volume_mm3(part, target)

        # A 'remove' THAT REMOVED NOTHING IS NOT A SUCCESS (0.3.93).
        #
        # The 2026-11-10 report answered "Boolean 'remove': ... feature
        # '图案_刻蚀'" twice on a tool body that stopped 0.3 mm short of the
        # material: the volume never moved (168607.5 both times), and the reply
        # carried no warning at all. UpdateObject had accepted the feature - a
        # boolean with an empty intersection is a perfectly valid feature - so
        # only the measurement can tell. The feature is taken back out here, for
        # the same reason pad and pocket roll back: a caller who is told "no
        # material" must not be left with a node in the tree that did nothing.
        #
        # Only 'remove' is judged this way: a union with a body that lies inside
        # the target adds no volume either, and an intersection with a larger
        # tool leaves the volume unchanged - both are correct results.
        if (operation == "remove" and volume_before is not None
                and volume_after is not None
                and volume_before - volume_after <= 1e-6):
            gone = self._remove_feature(part, feature, target)
            # THE TOOL BODY IS GONE, SO THE HANDLE MUST MOVE (0.3.95). A boolean
            # consumes its tool body; leaving `active_body` pointing at the
            # consumed one made every later call route into a body CATIA no
            # longer lists, and `catia_list_bodies` then reported all bodies
            # `active: false` with no explanation (reported 2026-11-10, P2-3).
            self.conn.active_body = target
            try:
                part.InWorkObject = target
            except Exception:  # noqa: BLE001 - the next call reports it
                pass
            error = FeatureRejected(
                f"The boolean removed no material: '{target.Name}' still measures "
                f"{volume_after:.1f} mm3, the same as before the call "
                f"({volume_before:.1f} mm3), so '{requested_tool}'"
                f"{tool_names_note} shares no VOLUME with it. The feature was "
                + ("taken back out of the tree" if gone else
                   "left in the tree (it could NOT be deleted - close the document "
                   "without saving)")
                + ". CATIA accepted it because a boolean with an empty "
                "intersection is a valid feature; the volume is what disagrees. "
                "\"Shares no volume\" is not the same as \"does not touch\": two "
                "bodies that meet along a face measure 0.0000 mm apart and still "
                "remove nothing, which is exactly what the pre-flight distance "
                "check lets through. The tool body was consumed by the call.",
                rolled_back=bool(gone),
                code="NO_MATERIAL",
                next_actions=[
                    "catia_list_bodies lists the bodies that are left and each "
                    "one's volume; the tool body is NOT among them any more (a "
                    "boolean consumes it)",
                    "If the tool body was meant to cut into a face, check the pad "
                    "direction and height that built it (catia_list_features "
                    "reports its depth), not the boolean",
                ])
            error.modified = False
            error.data = {"operation": operation,
                          "tool_body_requested": requested_tool,
                          "tool_body_internal": str(tool.Name),
                          "target_body": str(target.Name),
                          "volume_before_mm3": round(volume_before, 1),
                          "volume_after_mm3": round(volume_after, 1),
                          "rolled_back": bool(gone),
                          "tool_body_consumed": True}
            raise error

        # UpdateObject checks the feature, NOT the tree. A boolean can be accepted
        # and still leave relations broken, and the failure then surfaces at the
        # NEXT call - which is how the report's part went bad while this tool kept
        # answering success. Ask for the whole update before claiming anything.
        try:
            part.Update()
        except Exception as error:  # noqa: BLE001 - named below
            culprits = failing_elements(part)
            self._note_part_error(str(error))
            raise FeatureRejected(
                f"The boolean '{feature.Name}' was accepted, but the part no longer "
                "updates: " + (describe(culprits) + " - every later update stops "
                               "there, so this is not the boolean alone. "
                               if culprits else
                               "no single element fails on its own, so the damage is "
                               "a relation between features. ")
                + "The feature was LEFT in the tree (deleting it would hide the "
                "evidence), and catia_list_bodies now reports its numbers as "
                f"stale. Original error: {error}",
                rolled_back=False,
                code="PART_UPDATE_FAILED",
                next_actions=[
                    "Run catia_update_part to see the list again, then delete the "
                    "named element(s) with catia_delete_feature",
                    "If the named element is the boolean you just made, delete it "
                    "and rebuild the tool body so it actually overlaps the target",
                ]) from error

        self._note_part_error(None)
        # The tool body is consumed by CATIA, so it can no longer be the target
        # of new features.
        self.conn.active_body = target
        volume_note = ""
        if volume_before is not None and volume_after is not None:
            volume_note = (f" Volume of '{target.Name}': {volume_before:.1f} -> "
                           f"{volume_after:.1f} mm3.")
        return (
            f"Boolean '{operation}': '{tool.Name}' into '{target.Name}' "
            f"(feature '{feature.Name}').{volume_note} Active body is now "
            f"'{target.Name}'."
        )

    def _split_solid(self, args: dict[str, Any]) -> str:
        """Cut a solid body in two with a surface and keep one half.

        Measured on B30 2026-11-10 before this was written, because the two
        things a caller cannot guess are exactly the two things that decide
        whether the call works:

        * the cutter must divide the body COMPLETELY. A wall 40 mm wide against a
          100 mm block failed at update, the same wall extended to 400 mm split
          it into 70000 / 30000 mm3. CATIA's message is the bare "method Update
          failed", so this tool has to say it instead.
        * `iSplitSide` is 0 or 1 and which half each one keeps depends on the
          cutter's own normal: a plane offset +Z with side 0 kept Z 0..3 of a
          0..10 block, an extruded wall with side 0 kept the side its normal
          faces. Hence 'front'/'back' rather than a number, and both volumes in
          the reply.
        """
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        body = (self._body_named(part, str(args["body"])) if args.get("body")
                else self._prepare_body(part))
        cutter_ref, cutter_note = self._cutter_reference(part, str(args["cutter"]))
        keep = str(args.get("keep") or "front").strip().lower()
        if keep not in ("front", "back"):
            raise ArgumentRejected(
                f"keep must be 'front' or 'back', not '{args.get('keep')}'. "
                "'front' is the side the cutter's own normal points to.",
                code="BAD_ARGUMENT", stage="validate",
            )
        side = 0 if keep == "front" else 1

        volume_before, why_not = self._volume_probe(part, body)
        try:
            part.InWorkObject = body
        except Exception:  # noqa: BLE001 - read back below via the outcome
            pass
        try:
            feature = part.ShapeFactory.AddNewSplit(cutter_ref, side)
        except Exception as error:  # noqa: BLE001 - reported with the facts
            raise FeatureRejected(
                f"CATIA refused to create a Split of '{body.Name}' with "
                f"'{args['cutter']}' ({cutter_note}). Nothing was changed. "
                f"Original error: {error}",
                rolled_back=False, code="SPLIT_FAILED",
                next_actions=[
                    "Confirm the cutter is a SURFACE or a PLANE, not a curve or a "
                    "solid: catia_gsd_list_elements lists what the geometrical "
                    "sets hold",
                    "A closed shape (CloseSurface) and a datum plane both cut; a "
                    "wire does not",
                ],
            ) from error
        if args.get("name"):
            try:
                feature.Name = str(args["name"])
            except Exception:  # noqa: BLE001 - the feature keeps its own name
                pass

        try:
            part.Update()
        except Exception as error:  # noqa: BLE001 - rolled back below
            removed = self._remove_feature(part, feature, body)
            culprits = failing_elements(part)
            self._note_part_error(str(error))
            others = [c for c in culprits if str(c) != str(feature.Name)]
            raise FeatureRejected(
                f"The Split with '{args['cutter']}' was created but the part does "
                "not update, so the split was NOT made. "
                + (f"The cutter does not divide '{body.Name}' completely - that is "
                   "the failure mode measured on this build: a patch covering part "
                   "of the cross-section leaves no valid second half. Extend the "
                   "surface past the body on every side of the cut. "
                   if not others else
                   f"{describe(others)} also fails on its own, so the part was "
                   "already broken before this call - fix that first. ")
                + ("The feature was taken back out."
                   if removed else
                   "The dead feature could NOT be deleted: the part is in error "
                   "until it is; close the document without saving.")
                + f" Original error: {error}",
                rolled_back=bool(removed), code="SPLIT_FAILED",
                next_actions=[
                    "Look at the cutter's extent against the body's: "
                    "catia_get_bounding_box on the body, then check the surface "
                    "reaches past it on every side",
                    "If only PART of the body should be cut away, this is not a "
                    "Split - build a solid tool body and use "
                    "catia_boolean(remove)",
                ],
            ) from error

        volume_after, _ = self._volume_probe(part, body)
        if (volume_before is not None and volume_after is not None
                and abs(volume_after - volume_before) < 1e-6):
            removed = self._remove_feature(part, feature, body)
            raise FeatureRejected(
                f"The Split changed nothing: '{body.Name}' still measures "
                f"{volume_after:.1f} mm3, exactly as before the call. A cutter "
                "that does not pass through the body at all produces a valid "
                "feature with nothing to cut (measured 2026-11-10), which is why "
                "this is checked by volume and not by the return value. The "
                "feature was "
                + ("taken back out." if removed else
                   "left in the tree (it could not be deleted)."),
                rolled_back=bool(removed), code="SPLIT_NO_EFFECT",
                next_actions=[
                    f"Measure it: catia_measure_distance('{args['cutter']}', "
                    f"'{body.Name}') - a surface that never meets the body reads "
                    "a positive gap",
                    "catia_get_bounding_box on both, and compare the extents",
                ],
            )
        if volume_before and volume_after is not None and volume_after <= 1e-9:
            removed = self._remove_feature(part, feature, body)
            raise FeatureRejected(
                f"The Split kept the wrong half: '{body.Name}' now measures "
                f"0.0 mm3 - everything was on the side keep='{keep}' discards. "
                "The feature was "
                + ("taken back out; run the same call with keep="
                   + ("'back'." if keep == "front" else "'front'.")
                   if removed else "left in the tree (it could not be deleted)."),
                rolled_back=bool(removed), code="SPLIT_KEPT_NOTHING",
                next_actions=[f"Retry with keep=" +
                              ("'back'" if keep == "front" else "'front'")],
            )

        self._note_part_error(None)
        self.conn.active_body = body
        volumes = ""
        if volume_before is not None and volume_after is not None:
            # BOTH SIDES, because the description promises both (0.3.95). 0.3.94
            # printed before -> after and a percentage, so the other half had to
            # be subtracted by the caller - the report asked for the number the
            # promise implies (2026-11-10, P3-4).
            share = (volume_after / volume_before * 100) if volume_before else 0.0
            kept = volume_after
            discarded = volume_before - volume_after
            volumes = (f" Volume of '{body.Name}': {volume_before:.1f} mm3 before; "
                       f"kept {kept:.1f} mm3, discarded {discarded:.1f} mm3 "
                       f"({share:.1f}% kept).")
        else:
            volumes = " Volume not measured (" + str(why_not) + ")."
        return (
            f"Split '{body.Name}' with '{args['cutter']}' ({cutter_note}), keeping "
            f"the '{keep}' side"
            + (" - the side the cutter's normal points to" if keep == "front"
               else " - the side the cutter's normal points away from")
            + f" (feature '{feature.Name}').{volumes} The cutter is still in its "
            "geometrical set. If that is the wrong half, "
            f"catia_delete_feature(feature_name='{feature.Name}') and run again "
            f"with keep='{'back' if keep == 'front' else 'front'}'."
        )

    def _body_named(self, part: Any, name: str) -> Any:
        """An existing body by name, or a refusal that lists them."""
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
                f"No body named '{name}'. Bodies: {names or 'none'}.",
                code="BODY_NOT_FOUND", stage="validate",
            ) from None

    def _cutter_reference(self, part: Any, name: str) -> tuple[Any, str]:
        """A reference to a surface or plane, plus where it was found.

        Surfaces live in geometrical sets, not in bodies, and a Split with a
        body-shaped reference fails at the factory. The three origin planes are
        accepted by the same aliases the GSD tools use, because 'zx' is what a
        caller says and 'PlaneZX' is what CATIA calls it.
        """
        key = name.strip()
        alias = {"xy": "PlaneXY", "yz": "PlaneYZ", "zx": "PlaneZX",
                 "planexy": "PlaneXY", "planeyz": "PlaneYZ", "planezx": "PlaneZX",
                 "x": "PlaneYZ", "y": "PlaneZX", "z": "PlaneXY"}
        lowered = key.lower().replace(" ", "")
        if lowered in alias:
            plane = getattr(part.OriginElements, alias[lowered])
            return part.CreateReferenceFromObject(plane), \
                f"the origin plane {alias[lowered]}"

        found: list[tuple[Any, str]] = []
        seen: list[str] = []

        def walk(sets: Any, path: str) -> None:
            for i in range(1, int(getattr(sets, "Count", 0)) + 1):
                try:
                    geoset = sets.Item(i)
                except Exception:  # noqa: BLE001 - a nested set that will not open
                    continue
                where = f"{path}{getattr(geoset, 'Name', '?')}"
                try:
                    shapes = geoset.HybridShapes
                except Exception:  # noqa: BLE001 - empty set
                    shapes = None
                for j in range(1, int(getattr(shapes, "Count", 0)) + 1):
                    try:
                        element = shapes.Item(j)
                        element_name = str(element.Name)
                    except Exception:  # noqa: BLE001
                        continue
                    seen.append(element_name)
                    if element_name == key:
                        found.append((element, where))
                try:
                    walk(geoset.HybridBodies, where + "/")
                except Exception:  # noqa: BLE001 - leaf set
                    pass

        walk(part.HybridBodies, "")
        if found:
            element, where = found[0]
            return (part.CreateReferenceFromObject(element),
                    f"the element '{key}' in geometrical set '{where}'")
        raise ArgumentRejected(
            f"No surface or plane named '{key}' to cut with. A Split needs a "
            "GSD surface or a datum plane; geometrical sets in this part hold: "
            f"{', '.join(seen[:12]) if seen else 'nothing'}"
            + (" ..." if len(seen) > 12 else "")
            + ". The three origin planes answer to 'xy', 'yz' and 'zx'.",
            code="CUTTER_NOT_FOUND", stage="validate",
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

    def _cell_measure(self, spa: Any, cell: Any, prop: str,
                      measurable: Any = None) -> float | None:
        """Read Area/Length off an HSO cell, or None when the value is not real.

        ``measurable`` lets a caller that has already built one pass it in:
        ``GetMeasurable`` is the expensive call (53 ms per edge on the first
        pass, B30 2026-11-19), not the property read.

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
            if measurable is None:
                measurable = spa.GetMeasurable(cell.Reference)
            raw = getattr(measurable, prop)
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
            raise PreconditionRejected(
                f"Body '{body.Name}' has no solid shape yet."
            )
        cells = topology_cells(self.conn, body, kind)
        return cells, len(cells)

    def _list_faces(self, body_name: str | None = None,
                    with_geometry: bool = True) -> str:
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
                raise ArgumentRejected(
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
                if with_geometry:
                    entry.update(self._face_geometry(spa, cell))
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
        if with_geometry:
            payload["geometry"] = (
                "centroid is the FACE's own centre of gravity (measured on a "
                "60x40x40 block: top face (30,20,40), bottom (30,20,0) - the solid's "
                "is (30,20,20), so these are not body values). normal exists on "
                "PLANAR faces only and its SENSE is CATIA's own - the plane is "
                "exact, but the sign is not guaranteed to point out of the "
                "material (5 of a plain block's 6 faces pointed outward, the "
                "bottom one inward), so read it as a line, not as an arrow. No "
                "bounding box is reported because a face "
                "has none: Measurable exposes 44 members and no box, Inertia on a "
                "face has no box, and searching Vertex/Edge under a face cell "
                "returns 0 (all measured on B30 2026-11-13).")
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

    # One VBS round trip per face instead of two: a 9-face part cost 3.5 s with
    # read_measurable_array twice per face, and this halves it. Element 12/13 are
    # the two success flags, because "GetPlane failed" and "the plane is at the
    # origin" are different answers and zeros cannot tell them apart.
    _FACE_GEOMETRY_VBS = (
        "Public Function catia_mcp_facegeom(oMeasurable)\n"
        "    Dim c(2), p(8), a(13), i\n"
        "    On Error Resume Next\n"
        "    Err.Clear\n"
        "    oMeasurable.GetCOG c\n"
        "    a(12) = 0\n"
        "    If Err.Number = 0 Then a(12) = 1\n"
        "    For i = 0 To 2\n"
        "        a(i) = c(i)\n"
        "    Next\n"
        "    Err.Clear\n"
        "    oMeasurable.GetPlane p\n"
        "    a(13) = 0\n"
        "    If Err.Number = 0 Then a(13) = 1\n"
        "    For i = 0 To 8\n"
        "        a(i + 3) = p(i)\n"
        "    Next\n"
        "    catia_mcp_facegeom = a\n"
        "End Function\n"
    )

    def _face_geometry(self, spa: Any, cell: Any) -> dict[str, Any]:
        """One face's centroid, and its plane and normal when it is planar.

        Measured on B30 2026-11-13 on a 60x40x40 block (scripts/
        b30_v0423_probe_faces.py): ``GetCOG`` on a TOPOLOGICAL FACE reference
        answers that face's own centre - (30,20,40) for the top, (30,20,0) for the
        bottom, while the solid's centre is (30,20,20). The 2026-11-10 note that
        "GetMeasurable reads the body that carries the reference" holds for HYBRID
        references; a face is measured as a face. That is why list_faces could not
        publish a centroid then and can now.

        THE NORMAL IS A LINE, NOT AN ARROW. It is the cross product of GetPlane's
        two directions, unflipped - CATIA's own sign. The first version flipped it
        to point away from the solid's centre of gravity, and that was measured
        WRONG the same day: on a 40x40x4 plate with a tube standing 20 mm above it
        the body's centre is above the plate's top face, so the flip turned the
        top face's normal inward. Six faces of a plain block came back 6/6 outward
        with the flip and 5/6 without (only the bottom face pointed inward), and
        there is no inside/outside test in this API to decide it per face - so the
        raw sense is published with the caveat instead of a guess dressed as a
        fact.
        """
        out: dict[str, Any] = {}
        if spa is None:
            out["geometry_unknown"] = "SPAWorkbench is unavailable"
            return out
        try:
            measurable = spa.GetMeasurable(cell.Reference)
            raw = self.conn.app.SystemService.Evaluate(
                self._FACE_GEOMETRY_VBS, 0, "catia_mcp_facegeom", [measurable])
            # A failed GetPlane leaves its nine slots EMPTY (None), not zero - the
            # success flag is what separates "no plane" from "plane at the origin",
            # and float(None) must not cost the centroid that was read fine.
            values = [0.0 if v is None else float(v) for v in raw]
        except Exception as error:  # noqa: BLE001 - reported per face
            out["geometry_unknown"] = str(error).splitlines()[0][:70]
            return out
        if len(values) < 14:
            out["geometry_unknown"] = f"expected 14 values, got {len(values)}"
            return out
        centroid: list[float] | None = None
        if values[12] >= 0.5:
            centroid = [round(v, 4) for v in values[:3]]
            out["centroid"] = centroid
        else:
            out["centroid_unknown"] = True
        if values[13] < 0.5:
            # Not planar. GetPlane raises a CATScript stack on a cylinder, and the
            # area has already been read - a curved face simply has no normal.
            return out
        plane = values[3:12]
        if not any(abs(v) > 0 for v in plane):
            return out
        out["plane_origin"] = [round(v, 4) for v in plane[:3]]
        d1, d2 = plane[3:6], plane[6:9]
        normal = [d1[1] * d2[2] - d1[2] * d2[1],
                  d1[2] * d2[0] - d1[0] * d2[2],
                  d1[0] * d2[1] - d1[1] * d2[0]]
        length = math.sqrt(sum(c * c for c in normal))
        if length < 1e-12:
            return out
        out["normal"] = [round(c / length, 4) for c in normal]
        return out

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

    def _attach_edge_samples(self, part: Any, rows: list[dict[str, Any]],
                             edges: list[dict[str, Any]],
                             samples: int) -> str | None:
        """N points spread along each listed edge, so a curve can be rebuilt.

        The 2026-11-13 report (13th round, P1-5) asked for this because ONE point
        per edge cannot reconstruct anything: its author had to recompute every
        contour from side files. The limit this was expected to hit is not the
        limit it met - measured on B30 2026-11-15, a topological edge DOES accept
        `AddNewPointOnCurveFromPercent`:
        12/12 edges of a padded plate, 18/18 once four corners were filleted
        (so arcs work too), and five points at 0/25/50/75/100 % of a 40 mm edge
        came back exactly 10.000 mm apart. What refuses is a CLOSED rim - the two
        rims of a padded circle went 0/2 - because a point-on-curve parameter
        needs a curve with two ends, the same matrix the engrave code measured.

        An edge that refuses is MARKED, not dropped: the listing still answers
        every other edge, and `samples_unavailable` says which one could not and
        why. Guessing a point that is not on the edge would be worse than saying
        so - a rebuilt contour from invented points is a profile the part never
        had.

        `UpdateObject` per point, not one `part.Update()` for the batch: a single
        closed rim would poison the rebuild for every other edge, and the whole
        call would report nothing.
        """
        try:
            from catia_mcp.tools.measurement import _BATCH_POINTS  # noqa: PLC0415
        except Exception:  # noqa: BLE001 - the listing still stands without it
            return "samples were not reported: the point reader is unavailable."
        spa = self._topo_measure(part)
        if spa is None:
            return ("samples were not reported: SPAWorkbench is unavailable, so a "
                    "coordinate cannot be read back.")
        ratios = [index / (samples - 1) for index in range(samples)]
        probe = self._edge_probe_points(part)
        refused = 0
        planned: list[tuple[dict[str, Any], list[Any]]] = []
        try:
            for entry, item in zip(rows, edges):
                source = entry["cell"]
                value = getattr(source, "Value", None) or source
                placed: list[Any] = []
                failure = ""
                for ratio in ratios:
                    try:
                        point = probe.add_on_edge(value, ratio)
                        part.UpdateObject(point)
                    except Exception as error:  # noqa: BLE001 - one edge
                        failure = str(error).splitlines()[0][:90]
                        break
                    placed.append(point)
                if failure:
                    refused += 1
                    item["samples_unavailable"] = failure
                    continue
                planned.append((item, placed))
            if planned:
                refs = [part.CreateReferenceFromObject(point)
                        for _item, placed in planned for point in placed]
                raw = self.conn.app.SystemService.Evaluate(
                    _BATCH_POINTS, 0, "catia_mcp_points", [spa, refs])
                values = [float(v) for v in raw]
                cursor = 0
                for item, placed in planned:
                    item["samples_mm"] = [
                        [round(values[cursor + 3 * index + axis], 4)
                         for axis in range(3)]
                        for index in range(len(placed))]
                    item["sample_ratios"] = [round(r, 4) for r in ratios]
                    cursor += 3 * len(placed)
        finally:
            probe.close()
        note = (
            f"samples_mm holds {samples} points spread evenly along each edge "
            "(ratios in sample_ratios), measured on the edge itself - enough to "
            "rebuild the curve instead of only locating it. It works on OPEN "
            "edges: straight AND arc, measured on B30 2026-11-15, 18/18 on a "
            "filleted plate. It does NOT work on a CLOSED rim (a hole's rim: 0/2 "
            "on a padded circle) - a point-on-curve parameter needs a curve with "
            "two ends - and such an edge carries 'samples_unavailable' with "
            "CATIA's own words instead of invented points."
        )
        if refused:
            note += (f" {refused} of {len(rows)} edge(s) refused and carry "
                     "samples_unavailable; their point_on_edge (if asked for) is "
                     "still valid.")
        return note

    @staticmethod
    def _bounded_by_flags(faces: list[str]) -> dict[str, Any]:
        """Mark an edge whose fingerprint names fewer than two bounding faces.

        A solid edge is bounded by two faces, so one is an incomplete record
        rather than a strange part - and that missing face is the whole question
        behind "is this edge bounded by three or more faces", which is what a
        fillet refusal turns on. Reported 2026-11-13 (P2-2): a hole rim came back
        as 'Sketch.5;1' alone, and nothing said the list was short.
        """
        if len(faces) < 2:
            return {"bounded_by_incomplete": True}
        return {}

    def _list_edges(self, min_length: float | None = None,
                    max_length: float | None = None,
                    faces: list[str] | None = None,
                    offset: int = 0, limit: int = 0,
                    include_nls: bool = True,
                    coords: bool = False,
                    samples: int = 0) -> str:
        """List solid edges, filterable and pageable, with a stable fingerprint.

        The fingerprint is the point: ``Edge.N`` moves when the topology changes,
        the pair of bounding faces plus the length does not. Feed it back to
        catia_fillet / catia_chamfer instead of an index and the "fillet one,
        re-list everything, fillet the next" loop disappears.

        A part with 67 edges produced ~50 000 characters of NLS text per call
        (reported 2026-10-23), so ``include_nls=False`` and the paging arguments
        exist for exactly that case.
        """
        # A typo here costs N points per edge to discover otherwise, and the
        # number of points is the one thing this call cannot take back.
        try:
            samples = int(samples or 0)
        except (TypeError, ValueError):
            raise ArgumentRejected(
                f"samples has to be a whole number of points per edge; got "
                f"{samples!r}. 0 (the default) reports no samples. Nothing was "
                "read.", code="BAD_ARGUMENT", stage="validate")
        if samples and samples < 2:
            raise ArgumentRejected(
                f"samples={samples} cannot describe an edge: two points are the "
                "minimum (its two ends). Pass 2 or more, or leave it out. Nothing "
                "was read.", code="BAD_ARGUMENT", stage="validate")
        if samples > 50:
            raise ArgumentRejected(
                f"samples={samples} is more than this will place per edge: every "
                "point is a real object in a scratch set and a rebuild of its own, "
                "so 50 is the ceiling. Nothing was read.",
                code="BAD_ARGUMENT", stage="validate")
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

        if samples and rows and len(rows) * samples > 400:
            raise ArgumentRejected(
                f"that is {len(rows)} edge(s) x {samples} point(s) = "
                f"{len(rows) * samples} scratch points, above the 400 this will "
                "place in one call. Narrow with min_length / faces, page with "
                "offset+limit, or ask for fewer samples per edge. Nothing was "
                "read.", code="TOO_MANY_SAMPLES", stage="validate",
                data={"edges": len(rows), "samples": samples})

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
            # A REAL EDGE HAS TWO BOUNDING FACES. When the fingerprint names
            # one, the list is incomplete rather than the part being odd - and
            # that missing face is exactly what tells a caller whether the edge
            # carries three or more faces, which is why a fillet refuses it.
            # Reported 2026-11-13 (P2-2) on a hole rim that came back as
            # "Sketch.5;1" alone. Saying it beats letting the field look complete.
            item.update(self._bounded_by_flags(entry["bounded_by"]))
            if include_nls and entry.get("nls"):
                item["nls"] = entry["nls"]
                item["created_by"] = nls_features(entry["nls"])
            edges.append(item)

        coordinates_note = None
        if coords and rows:
            coordinates_note = self._attach_edge_points(part, rows, edges)
        samples_note = None
        if samples and rows:
            samples_note = self._attach_edge_samples(part, rows, edges, samples)

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
        if samples_note:
            payload["samples"] = samples_note
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
