"""Diagnostics tool for CATIA V5 installations.

Reports the exact CATIA release, and probes which automation APIs the
running installation currently resolves. Installations differ: old
releases lack some APIs entirely, and configurations without certain
products/licenses (including floating licenses currently in use by
others) may not expose or honor factory creation methods. The probes
test COM name resolution under the server's late-bound dispatch; a
method that resolves can still fail at call time if a license is
missing, so treat results as strong hints, not proof. This tool turns "what CATIA do you have and what
works on it" into a single call whose JSON output can be pasted into a
compatibility report or GitHub issue.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from catia_mcp.connection import CATIAConnection
from catia_mcp.results import ArgumentRejected

# Factory methods probed on an active Part document. These lists mirror
# the methods the real tools call, so a probe that fails here fails the
# same way in the corresponding tool.
_SHAPE_FACTORY_METHODS = [
    "AddNewPad",
    "AddNewPocket",
    "AddNewShaft",
    "AddNewGroove",
    "AddNewSolidEdgeFilletWithConstantRadius",
    "AddNewChamfer",
    "AddNewHole",
    "AddNewRectPattern",
    "AddNewCircPattern",
    "AddNewMirror",
    "AddNewShell",
    "AddNewDraft",
    "AddNewThickness",
    "AddNewThickSurface",
    "AddNewCloseSurface",
]

_HYBRID_SHAPE_FACTORY_METHODS = [
    "AddNewPointCoord",
    "AddNewLinePtPt",
    "AddNewPlaneOffset",
    "AddNewPlane3Points",
    "AddNewSpline",
    "AddNewCircleCtrRad",
    "AddNewDirectionByCoord",
    "AddNewProject",
    "AddNewIntersection",
    "AddNewLoft",
    "AddNewSweepExplicit",
    "AddNewExtrude",
    "AddNewRevol",
    "AddNewFill",
    "AddNewBlend",
    "AddNewOffset",
    "AddNewJoin",
    "AddNewHybridSplit",
    "AddNewHybridTrim",
    "AddNewSymmetry",
]

# WHAT THE TYPE LIBRARY DECLARES AND WHAT B30 DOES WITH IT ARE TWO ANSWERS.
# A member appearing in typeinfo_addnew means DECLARED, and three 2026-11-10
# reports in a row read that as "the capability exists" and spent their
# acceptance runs on it: "建议在 catia_diagnose 里给 UNSUPPORTED_ON_B30 明确结论，
# 避免调用者反复试". This block is that answer, and it is deliberately kept
# separate from the probe because one is a reading and the other is a
# measurement - mixing them is how "declared" got read as "works".
#
# status vocabulary:
#   UNSUPPORTED_ON_B30     construct-and-then-refuse, measured on this machine
#   NOT_IN_TYPELIB         the member (or its parameter) is not in the model
#   DECLARED_NOT_MEASURED  declared, not wrapped, nobody has measured it yet
#   AVAILABLE_WITH_LIMIT   works, with a limit that is easy to get wrong
_CAPABILITY_CEILINGS: list[dict[str, Any]] = [
    {
        "member": "AddNewSewSurface",
        "status": "UNSUPPORTED_ON_B30",
        "detail": (
            "Declared on HybridShapeFactory, and every attempt was refused at "
            "update: 8 of 8 on B30 2026-11-10, across three topologies and both "
            "integer and boolean arguments. No tool is wrapped, on purpose."
        ),
    },
    {
        "member": "AddNewWrapCurve",
        "status": "UNSUPPORTED_ON_B30",
        "detail": (
            "DECLARED, CONSTRUCTIBLE, AND STILL NOT USABLE - the third state. It "
            "builds with no arguments, accepts InsertReferenceCurve / "
            "InsertCurves / SetReferenceDirection, reports "
            "GetNumberOfCurves() == 2, and then refuses UpdateObject every time "
            "(measured 2026-11-10). Same family as AddNewGSDCircPattern (0.3.85): "
            "an object that can be configured and never updated. No tool is "
            "wrapped: the surface-side route it belongs to needs a patch cut out "
            "of a curved surface, and catia_gsd_split refuses a projected curve "
            "there (measured 2026-11-10, three cutters)."
        ),
    },
    {
        "member": "AddNewSplit",
        "status": "AVAILABLE_WITH_LIMIT",
        "detail": (
            "Works, as catia_split_solid, and the limit is the whole point: it "
            "cuts a body in TWO and keeps one half, so the cutter has to divide "
            "the body completely. A patch covering part of the cross-section "
            "leaves no valid second half - split is NOT a local cut, an engraving "
            "or a pocket, whatever the Chinese UI calls it (分割 for both). For "
            "local removal build a tool body and use catia_boolean(remove)."
        ),
    },
    {
        "member": "AddNewSweepExplicit + AddNewCloseSurface (engraving)",
        "status": "AVAILABLE_WITH_LIMIT",
        "detail": (
            "This is the route that reaches 'cut text into a curved face', and it is "
            "wrapped as catia_surface_engrave. Measured on B30 2026-11-10: a r=4 trace "
            "projected onto a r=40 cylinder, a r=0.4 profile on a plane normal to it, "
            "swept along the trace (tube 5.9657 mm3 = 2*pi^2*R*r^2), CloseSurface to a "
            "solid, boolean remove - 2.974 mm3 off, exactly the half of the tube that "
            "sits inside the material. "
            "THE GUIDE MAY BE OPEN - an earlier version of this entry said it had to "
            "be closed, and that was wrong. Measured on B30 2026-11-11 on a solid "
            "r=40 cylinder: a 50 mm segment lying on a generator, 0.5 mm deep, removed "
            "19.5829 mm3 (pi*r^2*L/2 = 19.635); an OPEN spline of 65.682 mm removed "
            "25.6436 (predicted 25.793). CloseSurface closes the two ends of the "
            "swept pipe by itself. The curve still has to LIE ON the body, which this "
            "tool cannot arrange. "
            "WHICH CURVES CAN CARRY THE POINT-ON-CURVE PARAMETER THIS ROUTE NEEDS, "
            "measured 2026-11-11 one FRESH DOCUMENT per case (a failed update is "
            "document-wide, so several cases in one part measure the first failure "
            "over and over): catia_gsd_line YES, extract of a gsd_line YES, an OPEN "
            "gsd_spline YES, a SKETCH outline projected onto a face YES; "
            "catia_gsd_circle NO, its extract NO, a CLOSED gsd_spline NO, and the "
            "projection of any of those onto a face NO - a closed spline's projection "
            "refused too, so 'it came from catia_gsd_project' is not what decides. "
            "HOW FAR THAT HAS BEEN VERIFIED, because the 2026-11-10 bottle "
            "report is the case outside it: the acceptance geometry is an "
            "ANALYTIC cylinder r=40 with a r=4 trace projected onto it. On a "
            "NON-ANALYTIC multi-section lofted bottle face the projection kept "
            "6.3 % of a 63.947 mm outline (4.000 mm), measured 2026-11-10 - and "
            "the engrave then found no material, correctly, because the tube was "
            "4 mm long. So treat this as available on a surface a projection can "
            "cover, not as available everywhere: read the WARNING that "
            "catia_gsd_project now prints before building anything on the curve. "
            "AND CHECK WHAT THE CUT REMOVED AGAINST WHAT IT SHOULD HAVE. A tube "
            "centred ON the surface loses half of itself, so removed/predicted is "
            "1. Two ways it goes wrong and only one of them used to be caught: "
            "under 0.5 the curve is FLOATING or grazing and the groove is shallow "
            "or short; over 1.3 the curve is BURIED, the whole tube was inside the "
            "material, and what was cut is an invisible cavity under the surface - "
            "the model changes and the surface looks untouched. That second case "
            "was measured on B30 2026-11-11 on a lofted bottle face: 5.624 mm3 "
            "against a 2.816 mm3 prediction, ratio 2.00, while the reply said 'a "
            "0.4 mm deep groove ... HALF of it is inside the material'. Since "
            "0.3.104 both ends are judged against the CUTTER'S measured volume "
            "(not against length * pi * r^2, which is not even available when the "
            "curve is a sketch whose length cannot be read), the ordinary sentence "
            "is printed only in the 0.5..1.3 band, and the reply names the boolean "
            "feature so a cut that has to come out can be deleted by name. "
            "Usable ratios measured so far: 0.997 (r=40 cylinder, r=4 trace), 0.997 "
            "(open line on a cylinder), 0.73/0.89/0.69 (hand-calibrated points on a "
            "lofted bottle, all visible), 1.00 (sketch line on a pad's top face), "
            "2.00 (the buried case)"
        ),
    },
    {
        "member": "a groove after a document is saved, closed and reopened",
        "status": "SEEN_ONCE_NOT_REPRODUCED",
        "detail": (
            "SEEN ONCE, AND NOT REPRODUCED. On 2026-11-11 an engrave on a lofted "
            "cylinder closed into a solid removed 9.991 mm3 before the file was "
            "saved, closed and reopened, and nothing at all after it - although the "
            "projection was still 17.7831 mm long and still 0.0000 mm from the body. "
            "That single observation was published with the advice 'make the "
            "projection and the engrave in the SAME session'. "
            "Re-measured on 2026-11-11 for 0.3.105, five cells, two geometries, a "
            "fresh document each time: a 100x100x20 pad with a line on its top face, "
            "depth 2, removed 376.9911 mm3 before the reopen and 376.9911 mm3 after; "
            "the same lofted bottle with a projected 32.111 mm glyph, depth 0.4, "
            "removed 7.9958 mm3 before and 7.9958 mm3 after. part.Update() before "
            "the engrave changed nothing, so 'the reopened tree has not been "
            "recomputed' is not the explanation either. Reopening is not the "
            "variable. The advice is therefore WITHDRAWN: nothing in this package "
            "asks for the two calls to share a session, and a NO_MATERIAL on a "
            "reopened file is reported as NO_MATERIAL like any other. If it is ever "
            "seen again, keep the catia_surface_engrave reply and read the removed "
            "volume against the cutter's volume - that is what separates a fragment "
            "from a curve that is not on the body."
        ),
    },
    {
        "member": "catia_gsd_project - the first projection in a document",
        "status": "AVAILABLE_WITH_LIMIT",
        "detail": (
            "The FIRST projection made in a document answers with a fragment; the "
            "next one answers with the real curve, and every later one repeats it. "
            "Measured on B30 2026-11-11 with EIGHT projections in a row in one "
            "document, same source and same target: a sketch outline gave 2.200 mm "
            "and then 32.111 mm seven times; a translated COPY of the same sketch "
            "gave 2.316 mm all eight times and never improved, reaching 29.951 mm "
            "only once the sketch itself had been projected. Two-call experiments "
            "were not enough to see this - three separate runs disagreed with each "
            "other - which is why the eight-in-a-row shape is recorded here. "
            "0.3.102 projects twice and keeps the second pass, and its low-share "
            "warning now tells callers NOT to move the element with "
            "catia_gsd_translate: 0.3.100 suggested exactly that, and measured, a "
            "copy lands 3.5 % where the sketch lands 48.2 %. A straight cylinder is "
            "the one shape where both passes agree (17.783 mm), so the second pass "
            "has never measured less than the first in any case tried. WHY CATIA "
            "answers differently the first time is unknown. "
            "WHICH SUPPORTS ACCEPT A PLANAR ELEMENT AT ALL, because a 2026-11-11 "
            "report hit PROJECT_FAILED on three configurations and concluded the "
            "tool refuses it: on B30 2026-11-11 a curved support accepted ALL of "
            "them - zx offset -40, a frame sketch with u=X v=Z, a padded "
            "cylinder's side face AND a curve extracted from that face. What a "
            "curved support cannot do is KEEP MUCH: the same 26.164 mm glyph "
            "retained 16.0 % from zx, 15.1 % from the frame sketch and 13.6 % on "
            "the solid face, against one loft. A projection lands only where the "
            "surface faces the element, so most of a planar outline misses a "
            "curved face - and a fragment that small engraves as nothing. What "
            "genuinely fails is a PLANAR element on a PARALLEL PLANAR support "
            "(xy against xy); a plane skewed 45 degrees accepts it. So the rule "
            "is 'parallel planes fail, curved supports accept but keep little', "
            "not 'non-planar is required'."
        ),
    },
    {
        "member": "ShapeFactory.AddNewGSDCircPattern (surface pattern)",
        "status": "UNSUPPORTED_ON_B30",
        "detail": (
            "The SURFACE circular pattern, and there is no single-node substitute "
            "for it. On B30 2026-11-10 its real 14-argument signature on "
            "ShapeFactory refused UpdateObject in 16 of 16 flag "
            "combinations - complete crown on/off x total 72/360 x radius-aligned "
            "on/off x type 0/1 - with a real extracted surface as the source and "
            "again with a solid one, with InWorkObject set. "
            "THE SOLID COUNTERPART DOES WORK: ShapeFactory.AddNewCircPattern is "
            "wrapped as catia_circ_pattern, one node for N instances (measured: a "
            "r=5 boss at r=20, count=5 over 360 deg, pattern area 2356.19 mm2 "
            "against 471.24 for the seed - and it needs part.InWorkObject set, "
            "without which it refuses with one sentence naming no cause). So a "
            "solid can be patterned as one editable node; a surface cannot, which "
            "is why catia_gsd_circ_pattern rotates copies and joins them and says "
            "so."
        ),
    },
    {
        "member": "HybridShapeLoft tangent factor / guide influence",
        "status": "NOT_IN_TYPELIB",
        "detail": (
            "The tutorial's 'Normal to Profile, strength 1.5' and 'To Next Guide' "
            "have no member. HybridShapeLoft exposes 78 members on B30 (read from "
            "its own type information 2026-11-10) and not one of them carries a "
            "tension, factor, weight or per-guide influence. What exists and is "
            "wrapped: SetStartSectionTangent / SetEndSectionTangent through "
            "start_tangent / end_tangent, and AddGuide. A DATUM PLANE is a legal "
            "tangency support - measured the same day: two circles r=10 at z=0 and "
            "r=14 at z=40 loft to 3030.971 mm2 with no condition, 2869.025 with "
            "start_tangent='xy' and 3202.807 with end_tangent='xy'. That is tangency "
            "TO the plane, so the surface leaves flat, which is not the tutorial's "
            "'normal to profile' - the two directions are opposite. "
            "catia_gsd_multi_section_surface accepts normal_to_profile / "
            "tangent_factor / guide_influence as aliases and refuses each with this "
            "measurement, instead of letting JSON Schema answer for it."
        ),
    },
    {
        "member": "Sketch frame / Point2D coordinates (read back)",
        "status": "NOT_IN_TYPELIB",
        "detail": (
            "A closed sketch answers no coordinates: Point2D exposes no readable "
            "X/Y through automation, and a sketch's own frame cannot be read back "
            "either (both measured on B30 2026-11-10, in the report that hit them). "
            "Consequences, stated rather than worked around: catia_sketch_copy "
            "replays the coordinates this session recorded when it drew the "
            "sketch, so a sketch that came from disk, or one whose constraints "
            "solved to different positions, cannot be copied faithfully - the reply "
            "says which of the two it is. catia_sketch_get_geometry says the same. "
            "Ask CATIA instead of the sketch: catia_measure_distance against the "
            "built geometry, catia_list_edges, or catia_get_bounding_box on the "
            "solid - those are read from the model, not from the sketch."
        ),
    },
    {
        "member": "Viewer3D capture size / tree visibility",
        "status": "NOT_IN_TYPELIB",
        "detail": (
            "Viewer3D exposes 58 members on B30 (measured 2026-11-10) and none of "
            "them hides the specification tree: no HideTree and no ShowTree, and "
            "Width/Height answer 'can not be set'. CaptureToFile therefore writes "
            "the 3D window's client area - 300x400 in the session measured, whatever "
            "was requested - tree and axis included. A clean render needs the CATIA "
            "window sized and the tree hidden by hand in the GUI. What CAN be done "
            "from here is hiding construction geometry, which is what "
            "catia_screenshot(clean=true) does."
        ),
    },
    {
        "member": "AddNewRib / AddNewStiffener",
        "status": "AVAILABLE_WITH_LIMIT",
        "detail": (
            "Measured on B30 2026-11-11. They are ShapeFactory methods, not Body "
            "ones: AddNewRib(iSketch, iCenterCurve), AddNewStiffener(iSketch), and "
            "a *FromRef variant of each plus AddNewSlot. A Rib DOES work - an arc "
            "centre line on xy with a r=3 circle on a plane that CROSSES it gave "
            "UpdateObject OK, part.Update() OK, 1175.0633 mm3, and saved - so "
            "'unknown' was the wrong answer. "
            "WHAT IT BUYS: nothing this repo lacks. The same frame through "
            "catia_gsd_sweep + catia_gsd_close_surface measured the SAME "
            "1175.0633 mm3, i.e. one solid, so a Rib wrapper would save one call "
            "and add a prerequisite check. That is why no tool is wrapped. "
            "HOW IT FAILS, which is the reason to say so here: with the profile "
            "plane TANGENT to the centre curve, UpdateObject reports success and "
            "the body measures 0.0000 mm3 - a silent empty feature; with the "
            "profile on the xz plane it refuses with CATIA's generic COM error "
            "because the sketch frame there reads back as unknown. So the profile "
            "plane must CROSS the centre curve, through the crossing point. "
            "ONE NUMBER NOT EXPLAINED: a r=3 circle swept along a 62.8319 mm arc "
            "should be 1776.5288 mm3 as section times length, and measured "
            "1175.0633 - 34 % less, by both routes. The mechanism was not "
            "established, so it is recorded rather than explained. "
            "A 2026-11-11 report said 'the ring handle is still a 9 mm plate, not a "
            "round tube'; that is a modelling detour, not this gap - sweep + close "
            "makes the tube today (see catia_surface_engrave's route for the "
            "pattern)."
        ),
    },
    {
        "member": "Factory2D.CreateText",
        "status": "NOT_IN_TYPELIB",
        "detail": (
            "Factory2D exposes 37 members on B30 and none of them creates text "
            "(measured 2026-11-10). This is not a missing wrapper: a glyph outline "
            "is GEOMETRY that has to be imported or computed, and "
            "catia_drawing_add_text draws on a drawing sheet, never in 3D. "
            "catia_sketch_strokes takes a whole outline in one call as polylines, "
            "and since 0.3.103 the font is no longer the caller's problem either: "
            "catia_text_strokes computes the outlines from the Hershey simplex set "
            "that ships with this server (ASCII 32-126, single-line). What is "
            "still missing is any script outside ASCII - no CJK, no accents."
        ),
    },
    {
        "member": "AddNewPointOnCurveFromPercent on a PROJECTED GLYPH",
        "status": "MEASURED_UNEXPLAINED",
        "detail": (
            "This is what stops engraved text, and it is measured rather than "
            "guessed. A glyph goes into a sketch (catia_text_strokes), is projected "
            "onto a lofted face with catia_gsd_project, and then the engrave route "
            "asks for a point ON that projection - which fails for some glyphs and "
            "not others. B30 2026-11-11, one fresh document per letter, same face "
            "and same sketch plane: OK for H, E, T, L, A, V, C, S, G; FAILED for O, "
            "0, D. "
            "IT IS NOT THE POINT COUNT. A regular n-gon projected onto the same "
            "face took the point at 7, 9, 11, 13, 15, 17, 19 and 21 vertices, and "
            "'S' (20 points) and 'G' (21) passed while 'D' (14) failed. It is not "
            "the shape either: Douglas-Peucker-simplified 'O' failed at 20, 20, 18, "
            "14, 8 and 8 segments. "
            "WHAT DOES MOVE IT IS WHERE THE TEXT SITS, i.e. HOW MUCH OF IT LANDS. "
            "The same 21-point 'O' on the same face: origin (0, 0) 81.4 % landed "
            "FAILED; (5, 0) 82.8 % FAILED; (-9, 0) 57.2 % OK; (-9, -10) 57.6 % OK; "
            "(-20, 0) 18.2 % OK. So a projection that covers almost the whole glyph "
            "is the case that refuses. "
            "THE LIKELY REASON IS NOT CONFIRMED, and is written here as a "
            "possibility, not a cause: a projection landing 80 %+ may come back as "
            "a CLOSED curve, and a closed curve takes no parameter - catia_gsd_"
            "circle's own circle refuses the same call (measured 2026-11-10), and "
            "the 47.2 % projection of a closed outline that DID engrave was an open "
            "fragment. Nothing here measured the closure of the projection directly, "
            "so treat that as the next thing to test, not as the answer. "
            "WHAT TO DO WITH IT: place text so part of it runs off the face, or cut "
            "a closed letter (O, 0, D, any ring) as two open strokes. catia_text_"
            "strokes repeats this in its reply because the caller hits it next."
        ),
    },
    {
        "member": "HybridShapeSpline.SetClosing",
        "status": "AVAILABLE_WITH_LIMIT",
        "detail": (
            "It WORKS, and this entry used to say the opposite. AddNewSpline() takes "
            "no parameters at all, and 0.3.97 read that as 'there is no periodic "
            "flag'. The flag lives on the OBJECT: SetClosing(type). Measured on B30 "
            "2026-11-10, four points at the corners of a 20x15 rectangle: open 61.2161 "
            "mm, SetClosing(1) or (2) 77.8649 mm - closed. catia_gsd_spline(closed="
            "true) has used SetClosing(1) since it was written. The limit is that it "
            "is a 3D GSD spline, not a sketch element."
        ),
    },
    {
        "member": "Spline2D (sketch) periodicity",
        "status": "NOT_IN_TYPELIB",
        "detail": (
            "A SKETCH spline cannot be periodic: Spline2D exposes 46 members on B30 "
            "and IsPeriodic / Period / Continuity are READ-ONLY, with no SetClosing "
            "at all (calling one answers 'CreateSpline.SetClosing', measured "
            "2026-11-10). So catia_sketch_spline(closed=true) repeats its first point "
            "- a closed curve with a seam, not a periodic one. When the curve has to "
            "be genuinely closed (a sweep guide, a loft section), build it with "
            "catia_gsd_spline(closed=true) instead."
        ),
    },
]


class DiagnosticsTools:
    """Diagnostics for the running CATIA installation."""

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection
        # Set by the server once the router is built, so a diagnosis can state
        # the registered tool count instead of the documented one.
        self.tool_count: int | None = None

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "catia_diagnose",
                "description": (
                    "Diagnose the running CATIA V5 installation: exact version, "
                    "release and service pack, Python/pywin32 environment, and a "
                    "probe of which automation APIs (Part Design ShapeFactory, "
                    "GSD HybridShapeFactory, SPAWorkbench measurement) are "
                    "actually exposed. Run this first when tools fail with "
                    "UNSUPPORTED_CAPABILITY, and include its output when "
                    "reporting compatibility issues. Probing factories requires "
                    "an active Part document; without one, only "
                    "application-level info is reported. The first key, mcp, "
                    "states the server name, version and registered tool count, "
                    "so a bug report can quote both sides without reading "
                    "pyproject.toml. The 'session' block answers the state a bare "
                    "'method failed' is usually about - document, its kind, "
                    "in_work_object, body and shape count, selection size, "
                    "geometrical-set count, plus a one-line 'line' form and a "
                    "'note' wherever a reading has no meaning (no Part open, no "
                    "document at all). session.open_documents lists every document "
                    "in the session and how many of them CATIA loaded itself: a "
                    "report measured 2-3x slower calls in a session whose owner "
                    "thought one document was open while 18 sample catalogs were "
                    "(2026-11-04), and nothing in the old block showed them. "
                    "THE FACTORY PROBE IS THE ANSWER TO 'can this CATIA do X'. "
                    "For each factory it emits the object's own type-library "
                    "method table: 'typeinfo_addnew' lists EVERY AddNew* member "
                    "with its parameter names, and 'typeinfo_members' the whole "
                    "member count, so 'is there a pattern/angle-plane/boundary "
                    "member here' is a lookup rather than a guess and not a "
                    "matter of what this server happens to have wrapped. Read "
                    "it that way on both factories before concluding anything: "
                    "a GSD member can be declared on ShapeFactory instead of "
                    "HybridShapeFactory, which is exactly what two reports in a "
                    "row got wrong in opposite directions (resolved 2026-11-09: "
                    "AddNewGSDCircPattern IS on ShapeFactory). The 'checked' "
                    "list beside it is a licence and regression sample, NOT the "
                    "capability list, and a member listed under "
                    "typeinfo_addnew is DECLARED - whether it can be updated "
                    "has to be measured separately (that same member returns an "
                    "object and then refuses UpdateObject on B30). That "
                    "measurement list is the 'capability_ceilings' block, kept "
                    "apart from the probe on purpose: UNSUPPORTED_ON_B30 means it "
                    "was constructed and refused at update (with the date), "
                    "NOT_IN_TYPELIB means the member or parameter is absent, "
                    "DECLARED_NOT_MEASURED means nobody has tried and it is not an "
                    "answer either way, and AVAILABLE_WITH_LIMIT means it works "
                    "but the limit is easy to get wrong."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        match tool_name:
            case "catia_diagnose":
                return self._diagnose()
            case _:
                raise ArgumentRejected(f"Unknown diagnostics tool: {tool_name}")

    def _diagnose(self) -> str:
        self.conn.ensure_connected()
        app = self.conn.app

        # Which server answered, and how many tools it registered: a report that
        # quotes a version should not have to read pyproject.toml to get it, and
        # "documented 94 vs registered 87" happened once already (reported
        # 2026-10-25). This is the FIRST key on purpose - the factory probe
        # returns early when no Part is open, and the block used to sit behind
        # that early return, so a report run on an empty CATIA quoted no version
        # at all (measured 2026-10-25: "mcp": null).
        report: dict[str, Any] = {"mcp": self._server_info()}
        report["python"] = self._python_info(app)
        report["catia"] = self._catia_info(app)
        # The state a bare "method failed" is usually about. It was printed in
        # every failure message and reported by NO diagnose call, so the message
        # pointed here for something this tool did not answer (reported
        # 2026-10-27). Same reader, so the two can never disagree.
        report["session"] = self._session_info()
        report["workbenches"] = self._workbench_probe()
        # Sheet metal is the one capability the server does not have at all, and
        # the 2026-11-03 report had to find that out by modelling a bracket the
        # hard way. This probe answers what can be answered from here.
        report["sheetmetal"] = self._sheetmetal_probe(app)
        report["factories"] = self._factory_probe()
        # A declared member is not a working capability, and the difference cost
        # three acceptance runs in a row (2026-11-10). This block is the other
        # half of the answer the factory probe cannot give.
        report["capability_ceilings"] = _CAPABILITY_CEILINGS
        report["notes"] = self._notes(report)

        return json.dumps(report, indent=2)

    def _session_info(self) -> dict[str, Any]:
        """Document state: which document, which in-work object, which body, what
        is selected - the five readings that explain most CATIA failures."""
        from catia_mcp.connection import session_line, session_snapshot

        try:
            snapshot = session_snapshot(self.conn)
        except Exception as e:  # noqa: BLE001 - a diagnosis never fails
            return {"unavailable": str(e).splitlines()[0][:90]}
        snapshot["line"] = session_line(self.conn)
        snapshot["open_documents"] = self._open_documents(self.conn.app)
        return snapshot

    def _open_documents(self, app: Any) -> dict[str, Any]:
        """Every document in the session, and which of them CATIA loaded itself.

        Reported 2026-11-04: tool calls ran 2-3x slower in a session the caller
        believed held "one document", and the cause was 18 sample catalogs CATIA
        had loaded at startup and never closed. The block used to name only the
        ACTIVE document, so there was no way to see them from here.

        What it does NOT claim: that the count alone is the cost. Same report,
        same author: on B30 eight extra empty Part documents changed nothing
        measurable - 90-point spline 855/872/873/898/904/923/940 ms with one
        document, 858/885/885/994 ms with nine, join 467/541/550/560 vs
        558/600/631 ms (separate CATIA processes, 2026-11-04). So the note says
        what to look at, not what caused it.
        """
        try:
            documents = app.Documents
            count = int(documents.Count)
        except Exception as error:  # noqa: BLE001 - a diagnosis never fails
            return {"unavailable": str(error).splitlines()[0][:90]}

        install_root = ""
        try:
            install_root = str(app.SystemService.Environ("CATInstallPath") or "").lower()
        except Exception:  # noqa: BLE001 - classification is best-effort
            install_root = ""

        names: list[str] = []
        shipped: list[str] = []
        for index in range(1, count + 1):
            try:
                doc = documents.Item(index)
            except Exception:  # noqa: BLE001
                continue
            try:
                name = str(doc.Name)
            except Exception:  # noqa: BLE001
                name = "?"
            names.append(name)
            path = ""
            try:
                path = str(doc.FullName or "")
            except Exception:  # noqa: BLE001
                path = ""
            # A document living under the CATIA installation is one CATIA brought
            # with it (the sample catalogs), not something the caller opened.
            if install_root and path.lower().startswith(install_root):
                shipped.append(name)

        catalogs = [name for name in names if name.lower().endswith(".catfct")]
        block: dict[str, Any] = {
            "count": count,
            "names": names[:20],
            "shipped_with_catia": len(shipped),
            "function_catalogs": len(catalogs),
        }
        if len(shipped):
            block["shipped_names"] = shipped[:20]
        if catalogs:
            block["function_catalog_names"] = catalogs[:20]
        if count > 5:
            block["note"] = (
                f"{count} documents are open"
                + (f", {len(shipped)} of them shipped with CATIA" if shipped else "")
                + ". Closing is worth doing for the ones that HOLD GEOMETRY: measured "
                  "2026-11-04, 15 shipped catalogs plus one Part ran a 90-point spline "
                  "in 1.09 s - the same as a lone Part - while two finished Parts of "
                  "about 2 MB each took it to 1.6 s and a 9-element join from 962 to "
                  "1957 ms. Eight EMPTY Parts changed nothing measurable on B30, so it "
                  "is the geometry that costs, not the count. The .CATfct catalogs "
                  "cannot be released through automation: Documents.Close() returns "
                  "without error and CATIA re-mounts them (reported 2026-11-04) - so "
                  "ignore those and close your own finished models."
            )
        return block


    def _server_info(self) -> dict[str, Any]:
        """Name, version and registered tool count of this server.

        ``tool_count`` is filled in by the server once the router is built, so a
        diagnosis states what actually answered rather than what README claims.
        """
        try:
            from catia_mcp import __version__ as mcp_version
        except Exception:  # noqa: BLE001 - never fail a diagnosis over this
            mcp_version = "unknown"
        return {
            "name": "MCP_CATIA_MechDog",
            "version": mcp_version,
            "tool_count": self.tool_count,
        }

    def _python_info(self, app: Any) -> dict[str, Any]:
        info: dict[str, Any] = {
            "python_version": sys.version.split()[0],
            "com_binding": type(app).__name__,
        }
        try:
            import win32com

            # "unknown" used to be the answer on every machine, because win32com
            # has no __build__ (reported 2026-11-03, P3-1). The DLL's file
            # version carries the same number: on this machine pywin32 answers
            # 3.13.312.0, i.e. built for Python 3.13, build 312.
            build = getattr(win32com, "__build__", None)
            if build is None:
                try:
                    import win32api

                    version = win32api.GetFileVersionInfo(win32api.__file__, "\\")
                    major, minor = version["FileVersionMS"], version["FileVersionLS"]
                    build = (f"{major >> 16}.{major & 0xFFFF}."
                             f"{minor >> 16}.{minor & 0xFFFF}")
                except Exception:  # noqa: BLE001 - a missing number is not a crash
                    build = "unknown"
            info["pywin32_build"] = build
            gen_path = getattr(win32com, "__gen_path__", None)
            if gen_path:
                info["gen_py_cache"] = gen_path
                try:
                    info["gen_py_entries"] = len(os.listdir(gen_path))
                except Exception:
                    pass
        except Exception:
            info["pywin32_build"] = "unavailable"
        return info

    def _catia_info(self, app: Any) -> dict[str, Any]:
        info: dict[str, Any] = {}
        try:
            info["caption"] = app.Caption
        except Exception:
            pass
        try:
            conf = app.SystemConfiguration
            info["version"] = conf.Version
            info["release"] = conf.Release
            info["service_pack"] = conf.ServicePack
        except Exception as e:
            info["system_configuration_error"] = str(e)
        try:
            info["full_name"] = app.FullName
        except Exception:
            pass
        return info

    # The DLLs Generative Sheetmetal Design ships as, beside CNEXT.exe.
    _SHEETMETAL_DLLS = (
        "CATSmdDesignWorkbench.dll",
        "CATSheetMetal.dll",
        "CATSheetmetalDxfExport.dll",
    )

    def _sheetmetal_probe(self, app: Any) -> dict[str, Any]:
        """Is sheet metal usable here? As far as this machine can tell.

        NOT via GetWorkbench: on B32 SP6 it answers 'SPAWorkbench' and nothing
        else - 'Drafting' and 'PartDesign', workbenches nobody doubts, fail the
        same way (measured 2026-11-03). A probe built on it would report "no
        sheet metal" for every machine, which is worse than no answer.

        What is left is honest and useful: the workbench's DLLs ship beside the
        running EXE (installed or not - a fact about the disk, not a licence),
        and the automation objects this server drives have no sheet-metal
        feature at all. Both halves are stated, so nobody reads "installed" as
        "this server can bend metal".
        """
        result: dict[str, Any] = {
            "server_tools": (
                "none - the server has no wall / flange / bend / unfold / "
                "flat-pattern tool; sheet-metal parts are modelled as "
                "extrude + cut, which cannot reach the source part's face count"
            ),
        }
        try:
            exe = str(app.FullName)
            folder = os.path.dirname(exe)
            result["workbench_folder"] = folder
            present = {}
            for dll in self._SHEETMETAL_DLLS:
                present[dll] = "present" if os.path.exists(
                    os.path.join(folder, dll)) else "absent"
            result["files"] = present
            result["installed"] = (
                "yes - Generative Sheetmetal Design is installed beside this "
                "CATIA" if all(v == "present" for v in present.values())
                else "partly - some of the workbench's DLLs are missing")
        except Exception as error:  # noqa: BLE001 - a diagnosis never fails
            result["files"] = f"unreadable: {str(error).splitlines()[0][:80]}"
        result["licence"] = (
            "not readable from disk (DSLS/nodelock). Open Start > Mechanical "
            "Design > Generative Sheetmetal Design once in the UI to find out.")
        return result

    def _workbench_probe(self) -> dict[str, str]:
        # GetWorkbench is a Document method in the V5 automation model, so
        # probing needs an open document. SPAWorkbench is what the
        # measurement tools rely on. Note that "PartDesign" is NOT a valid
        # GetWorkbench id: Part Design is reached through Part.ShapeFactory,
        # so a GetWorkbench("PartDesign") failure proves nothing.
        try:
            doc = self.conn.active_document
        except Exception:
            doc = None
        if doc is None:
            return {
                "skipped": (
                    "No active document. Open or create one, then run "
                    "catia_diagnose again to probe workbenches."
                )
            }
        probes = {}
        for wb in ("SPAWorkbench",):
            try:
                doc.GetWorkbench(wb)
                probes[wb] = "available"
            except Exception as e:
                probes[wb] = f"unavailable: {e}"
        return probes

    def _factory_probe(self) -> dict[str, Any]:
        result: dict[str, Any] = {}

        doc = None
        try:
            doc = self.conn.active_document
        except Exception:
            pass
        part = None
        if doc is not None:
            try:
                part = self.conn.part_of(doc)
            except Exception:
                pass
        if part is None:
            result["skipped"] = (
                "No active Part document. Create or open a CATPart, then run "
                "catia_diagnose again to probe the factories."
            )
            return result

        # Which server answered is reported at the top level (report["mcp"]);
        # this probe only answers "which factory methods work here".
        result["shape_factory"] = self._probe_methods(
            part, "ShapeFactory", _SHAPE_FACTORY_METHODS
        )
        result["hybrid_shape_factory"] = self._probe_methods(
            part, "HybridShapeFactory", _HYBRID_SHAPE_FACTORY_METHODS
        )
        result["measurable"] = self._measurable_probe(doc, part)
        return result

    def _measurable_probe(self, doc: Any, part: Any) -> dict[str, Any]:
        # Old releases miss parts of the Measurable API (field-confirmed on
        # V5R20: GetBoundingBox, GetInertia and minimum distance are absent
        # while Volume/Area/GetCOG work). Probe method-name resolution only;
        # Volume/Area are properties, so reading them would invoke a
        # measurement and is deliberately not done here.
        probe: dict[str, Any] = {}
        try:
            try:
                spa = doc.GetWorkbench("SPAWorkbench")
            except AttributeError:
                spa = self.conn.app.GetWorkbench("SPAWorkbench")
            body = part.Bodies.Item(1)
            ref = part.CreateReferenceFromObject(body)
            measurable = spa.GetMeasurable(ref)
        except Exception as e:
            probe["measurable"] = f"unavailable: {e}"
            return probe
        probe["measurable"] = "available"
        missing = []
        exposed = []
        for m in ("GetCOG", "GetBoundingBox", "GetInertia", "GetMinimumDistance"):
            try:
                getattr(measurable, m)
                exposed.append(m)
            except AttributeError:
                missing.append(m)
            except Exception:
                exposed.append(m)
        probe["exposed_methods"] = exposed
        probe["missing_methods"] = missing
        return probe

    def _probe_methods(
        self, part: Any, factory_attr: str, methods: list[str]
    ) -> dict[str, Any]:
        probe: dict[str, Any] = {}
        try:
            factory = getattr(part, factory_attr)
        except Exception as e:
            probe["factory"] = f"unavailable: {e}"
            return probe

        probe["factory"] = "available"
        # The hand-written list below is a licence / regression sample, and it
        # was read as "the capability list" - a user concluded CATIA had no solid
        # loft because AddNewLoft was not in it, while the object exposes it and
        # it works (measured on B30 2026-10-25: AddNewLoft builds a 18 765.8 mm3
        # solid from two circles). So the probe now also reads the object's own
        # type library, which cannot miss a method.
        probe["typeinfo_addnew"] = self._typeinfo_methods(factory)
        # Both numbers, because they answer different questions and a report
        # spent a round comparing "160 members" with "131 AddNew*" as if they
        # disagreed (2026-11-09). 'count' is every member of the object's type
        # library, 'addnew' is how many start with AddNew.
        probe["typeinfo_members"] = self._typeinfo_member_count(factory)
        exposed: list[str] = []
        missing: list[str] = []
        errored: dict[str, str] = {}
        for m in methods:
            # getattr resolution mirrors how the real tools reach these
            # methods under late-bound dispatch (it does not invoke them).
            try:
                getattr(factory, m)
                exposed.append(m)
            except AttributeError:
                missing.append(m)
            except Exception as e:
                errored[m] = str(e)
        probe["exposed_methods"] = exposed
        probe["missing_methods"] = missing
        if errored:
            probe["errored_methods"] = errored
        return probe

    @staticmethod
    def _typeinfo_methods(obj: Any, prefix: str = "AddNew") -> list[str] | None:
        """Every ``AddNew*`` method the object really exposes, from its type info.

        ``None`` when the type library cannot be read (late-bound dispatch without
        a cached typelib), which is a different answer from "no methods".
        """
        try:
            info = obj._oleobj_.GetTypeInfo()
            count = info.GetTypeAttr().cFuncs
        except Exception:  # noqa: BLE001 - reported as unavailable
            return None
        names = set()
        for index in range(count):
            try:
                name = info.GetNames(info.GetFuncDesc(index).memid)[0]
            except Exception:  # noqa: BLE001 - skip the undocumented ones
                continue
            if name.startswith(prefix):
                names.add(name)
        return sorted(names)

    @staticmethod
    def _typeinfo_member_count(obj: Any,
                               prefix: str = "AddNew") -> dict[str, int] | None:
        """``{"count": all members, "addnew": members starting with prefix}``.

        Two numbers, because they are routinely compared against each other by
        accident: this server's own description said HybridShapeFactory "has 160
        members" while catia_diagnose reported 131, and a report took that for a
        contradiction (2026-11-09). 160 is the typelib's whole member table, 131
        is the AddNew* subset. Reporting both, side by side, ends the question.
        """
        try:
            info = obj._oleobj_.GetTypeInfo()
            count = info.GetTypeAttr().cFuncs
        except Exception:  # noqa: BLE001 - reported as unavailable
            return None
        matching = 0
        for index in range(count):
            try:
                name = info.GetNames(info.GetFuncDesc(index).memid)[0]
            except Exception:  # noqa: BLE001 - skip the undocumented ones
                continue
            if name.startswith(prefix):
                matching += 1
        return {"count": int(count), "addnew": matching}

    def _notes(self, report: dict[str, Any]) -> list[str]:
        notes: list[str] = []
        # Say which list is which, once. A caller read "missing_methods" as the
        # set of things CATIA cannot do, and concluded there was no solid loft.
        factories = report.get("factories", {})
        typed = {key: len(probe.get("typeinfo_addnew") or [])
                 for key, probe in factories.items()
                 if isinstance(probe, dict) and probe.get("typeinfo_addnew")}
        if typed:
            notes.append(
                "typeinfo_addnew is the object's own method table ("
                + ", ".join(f"{k}: {v}" for k, v in sorted(typed.items()))
                + " AddNew* methods). typeinfo_members gives both counts for "
                "the same object - 'count' is every member in its type library "
                "and 'addnew' is the AddNew* subset - so 'has 160 members' and "
                "'131 AddNew*' are not two answers to one question. The checked "
                "list is a licence and regression sample, NOT the capability list "
                "- a method absent from it can still exist and work, so "
                "read typeinfo_addnew before concluding CATIA cannot do "
                "something. Remember that a tool's own factory is not the only "
                "candidate: a GSD pattern lives on ShapeFactory."
            )
        notes.append(
            "capability_ceilings answers the NEXT question - 'declared, but does "
            "it update?' - and it is a measurement list, not a typelib reading. "
            "UNSUPPORTED_ON_B30 means the member was constructed and then refused "
            "at update on this machine, with the date of the measurement; "
            "DECLARED_NOT_MEASURED means nobody has tried, and should not be read "
            "as either answer. Read this block before writing a long chain that "
            "depends on one of these members."
        )
        factories = report.get("factories", {})
        labels = {
            "shape_factory": ("ShapeFactory", "Part Design (MD2/PD1 level)"),
            "hybrid_shape_factory": (
                "HybridShapeFactory",
                "Generative Shape Design (GS1/GSD level)",
            ),
        }
        for key, (name, lic) in labels.items():
            probe = factories.get(key, {})
            status = probe.get("factory", "")
            if status.startswith("unavailable"):
                notes.append(
                    f"{name} itself is not reachable on this installation "
                    f"({status}). The corresponding tools will fail."
                )
            elif probe.get("missing_methods"):
                notes.append(
                    f"{name} exists but some creation methods did not "
                    "resolve. Field-confirmed first suspect: a stale "
                    "pywin32 gen_py cache (close CATIA and the server, "
                    "delete the %TEMP%\\gen_py folder, restart, re-run "
                    "catia_diagnose). Otherwise the " + lic + " license "
                    "may not be active right now (floating licenses come "
                    "and go during the day) or the release predates the "
                    "API. Check Tools > Options > Licensing and compare "
                    "two catia_diagnose runs."
                )
            if probe.get("errored_methods"):
                notes.append(
                    f"Some {name} probes errored (neither exposed nor "
                    "cleanly missing); include this report in a GitHub "
                    "issue."
                )
        meas = factories.get("measurable", {})
        if meas.get("missing_methods"):
            missing = ", ".join(meas["missing_methods"])
            notes.append(
                "The Measurable API lacks " + missing + " on this release. That is "
                "expected and does NOT disable any tool: neither method is used "
                "here - volume / area / centre of gravity come from "
                "Measurable.Volume / .Area / GetCOG, bounding boxes from the final "
                "shape's vertices or an exported STL mesh, and the inertia tensor "
                "from SPAWorkbench.Inertias. catia_get_inertia and "
                "catia_get_bounding_box work normally without GetInertia / "
                "GetBoundingBox. (This note used to say the opposite and talked "
                "callers out of two working tools - reported 2026-10-24, third "
                "release running.)"
            )
        wb = report.get("workbenches", {})
        for wb_name, status in wb.items():
            if wb_name != "skipped" and str(status).startswith("unavailable"):
                notes.append(
                    f"{wb_name} is unavailable; measurement tools "
                    "(distance, inertia, bounding box) will fail."
                )
        release = report.get("catia", {}).get("release")
        if isinstance(release, int) and release < 26:
            notes.append(
                f"CATIA release R{release} is older than the project's "
                "tested baseline (R2016 = R26). Core document/sketch/GSD "
                "tools are known to work on V5R20, but expect gaps; please "
                "report your results in a GitHub issue."
            )
        if not notes:
            notes.append("No compatibility concern detected by the probes.")
        return notes
