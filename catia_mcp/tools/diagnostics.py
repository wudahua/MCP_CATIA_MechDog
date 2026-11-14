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
        "member": "hiding construction elements (kind='construction') on a solid",
        "status": "AVAILABLE_WITH_LIMIT",
        "detail": (
            "It USED to take the solid off the screen with them, and 0.3.126 found "
            "out why - it was not the dependency this entry used to blame. THE OLD "
            "OBSERVATION STANDS (B30 2026-11-13, scripts/b30_v0417_visibility_fix.py): "
            "kind='construction' hidden on a 2-section solid loft gave an empty "
            "viewport at 61 008 bytes against 53 978 with the solid, while the call "
            "still reported how many elements it had hidden; hiding a geometrical "
            "SET does not do this; and catia_screenshot(clean=true) saved the day by "
            "hiding and restoring around the capture. THE CAUSE WAS WRONG, measured "
            "on B30 2026-11-14 (scripts/b30_v0428_probe_clean2.py, _clean3.py) with "
            "four captures of one fresh document - two sketch ellipses and a solid "
            "loft - compared by SHA-256: hiding ONLY the sketches left the solid "
            "fully visible with its profiles gone (47 066 bytes); hiding ONLY the "
            "three origin planes produced a picture IDENTICAL to 'all shown' "
            "(55 726 bytes); hiding body.HybridShapes emptied the viewport (46 957 "
            "bytes); and SetShow(1) on the loft afterwards produced a picture "
            "IDENTICAL to the empty one. body.HybridShapes on that part read "
            "['Datum_xy_20', 'Body'] - the loft itself is a member of the collection "
            "the call was emptying, so the solid was switched off DIRECTLY, not "
            "withdrawn because its section curves are construction. That also "
            "explains why setting its own flag back did nothing. "
            "THE FIX (0.3.126): catia_set_visibility skips any HybridShapes entry "
            "whose name is also a body.Shape (material - see _material_names), so "
            "hiding construction no longer removes the solid: re-measured 2026-11-14 "
            "at 83.5 % of the centre box still light, against 14 % for the old "
            "failure. The 0.3.115 half stands unchanged: hiding a geometrical set "
            "never did this, and showing a body does show the features inside it."
        ),
    },
    {
        "member": "two mirrored chamfers on one face (report: the second always fails)",
        "status": "SEEN_ONCE_NOT_REPRODUCED",
        "detail": (
            "The 2026-11-13 report measured 3 of 3: on a four-lobe column top, two "
            "symmetric edges 8 mm apart at 70 degrees, the first chamfer builds and "
            "the second always answers 'UpdateObject failed' - whichever one is "
            "built first, and also as one call with both anchors (1 of 2 done). "
            "NOT REPRODUCED on B30 2026-11-13 (scripts/b30_v0417_paired_chamfer.py): "
            "a 40x10x30 block with two mirrored 10 mm top edges, chamfer 2 mm, "
            "A-then-B and B-then-A, at 45 degrees AND at the report's 70 degrees, "
            "plus both anchors in one call - six cells, all of them succeeded "
            "(12 000 -> 11 980 -> 11 960 mm3 at 45 deg, each chamfer removing the "
            "analytic 20 mm3). So 'a pair of chamfers' is not the trigger by "
            "itself, and guessing a cause here would be worth less than the "
            "measurement. What is left as UNKNOWN is which part of that geometry "
            "does it - two bevels whose faces meet across a narrow top, or a "
            "document that had a chamfer deleted and rebuilt first. Ask for the "
            "part file rather than trusting a generic 'topology' hint."
        ),
    },
    {
        "member": "cutting a tip off a solid along an inclined plane",
        "status": "AVAILABLE_WITH_LIMIT",
        "detail": (
            "catia_split_solid does this, and the 2026-11-12 report that asked for "
            "a trim-by-plane tool had not tried it: the tool description's "
            "'split is NOT a local cut' is about a patch that covers only part of the "
            "cross-section, not about keeping one side of a full cut. MEASURED on "
            "B30 2026-11-13: a 40x40x40 pad, cut by a plane through three points "
            "tilted 20 degrees about Y (z = 20 + x*tan20), keep='front' - volume "
            "20 352.95 mm3 against a hand calculation of 20 353.0. The plane was a "
            "GSD plane in GSD_Set and needed no extension, because a datum plane is "
            "already infinite. Two things to know: the reply names the half it kept "
            "and both volumes, and 'front' is the side the cutter's NORMAL points to "
            "- for a 3-point plane that is (p2-p1) x (p3-p1), so check the volume "
            "before building on the shape."
        ),
    },
    {
        "member": "a B-spline given as control points + knots",
        "status": "UNSUPPORTED_ON_B30",
        "detail": (
            "catia_sketch_spline and catia_gsd_spline INTERPOLATE: the curve passes "
            "through every point given. A source spec that lists control points and a "
            "knot vector (2026-11-13 report, [B-SPLINE] with order=4, count=4 and "
            "knots 0,0,0,0,1,1,1,1) describes a different curve - the polygon is not "
            "the curve - so feeding those points to the interpolating entry draws "
            "something else, and the difference is a shape error nothing in the reply "
            "warns about. The interpolation cost is measured and large: six points of "
            "an aerofoil-like outline padded +29.3 % in volume against +0.48 % for the "
            "same points joined with straight lines. There is no control-point entry in "
            "this package, and HybridShapeSpline has not been measured to expose one "
            "here; treat 'control points in, same curve out' as unavailable and say so "
            "rather than passing them as interpolation points."
        ),
    },
    {
        "member": "sheet metal (wall / flange / bend / unfold / flat pattern)",
        "status": "UNSUPPORTED_ON_B30",
        "detail": (
            "No tool: the server has no Generative Sheetmetal entry point. The "
            "workbench itself is installed on the 2026-11-13 reporter's machine "
            "('installed: yes'), so this is a packaging gap, not a CATIA one - the "
            "geometries exist and cannot be reached. What a caller does instead is "
            "extrude + cut, and the price is measured: the report's Arm2 came out at "
            "822.137 mm3 against 792.323 (+3.76 %, the mitre corner's 2x2x20 mm3 of "
            "extra material) with 15 faces where the source had 21, because a bend is "
            "a pair of fillets that an extrude cannot have. Do not present an "
            "extrude+cut part as a sheet-metal equivalent."
        ),
    },
    {
        "member": "AddNewPointOnSurfaceWithReference (putting a point on a surface)",
        "status": "AVAILABLE_WITH_LIMIT",
        "detail": (
            "This is the primitive the 2026-11-11 report was missing, and it is "
            "wrapped as catia_curve_on_surface. MEASURED on B30 2026-11-11: "
            "AddNewPointOnSurfaceWithReference(face, point, direction, 0) puts the "
            "point on the NEAREST point of that surface - a point already on the "
            "surface moved 0.0022 mm, and a point 5 mm outside a r=30 cylinder "
            "landed exactly on it. Three things do NOT work the way the signature "
            "suggests: (1) the DIRECTION is ignored - +Y, -Y, +Z and +X all moved "
            "the same point 26.0052 mm; (2) WHICH FACE you name is ignored too - "
            "Face.1, Face.2 and Face.3 of one body gave the same 26.0052 mm, so the "
            "support only selects the BODY (RE-MEASURED 2026-11-13 with a wall "
            "0.6 mm away, which is the case where it would matter: an outline 0.6 "
            "mm in front of one wall of a 40x20x10 block moved 0.6000 mm with no "
            "support and 0.6000 mm with an END face named; on a block with a 0.6 mm "
            "recess, an outline on the recess floor moved 0.0000 mm with no "
            "support, with the floor named and with a side wall named); (3) a SOLID "
            "BODY reference is refused "
            "outright (every point failed 'UpdateObject 失败'), so it has to be a "
            "face or a surface. What follows from (1) and (2) is worth stating "
            "plainly: a point cannot be told which of two nearby walls to land on, "
            "so it has to be placed nearer the one that is meant. The 2026-11-12 "
            "report DID see a named face change the landing (0.6000 with Face.1 vs "
            "0.0000 with Face.24 on a 56-face lofted body), which the cells above do "
            "not reproduce - so the reply prints which support it used and the "
            "spread of the movements, and a caller who sees a uniform non-zero move "
            "should treat the landing wall as unconfirmed. Coordinates "
            "cannot be read back (GetCoordinates fails on a plain point and returns "
            "zeros on a point-on-surface), which is why the tool reports how far "
            "each point MOVED, measured with the SPA workbench, instead of where it "
            "ended up. End to end: an 18-point 'C' snapped from a plane 60 mm in "
            "front of a closed lofted bottle, threaded into one open spline, "
            "engraved 0.4 mm deep - removed 3.026 mm3 against a 3.042 mm3 "
            "prediction, ratio 0.99, with no offset tuning."
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
        "member": "catia_gsd_project - the first projection in a document, and which supports accept a planar element",
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
            "WHICH SUPPORTS ACCEPT A PLANAR ELEMENT - re-measured 2026-11-12 "
            "because the earlier answer was wrong in two places. One document, one "
            "40x40 sketch, the file saved, closed and REOPENED, InWorkObject on the "
            "geometrical set, four supports: the face the sketch LIES ON (0.0000 mm "
            "away) 100 %, that same face EXTRACTED as a SURFACE 100 %, a plane "
            "PARALLEL to the sketch 100 %, and one of that face's edges EXTRACTED as "
            "a CURVE refused (PROJECT_FAILED). So neither 'parallel planes fail' "
            "(2026-11-10) nor 'a curve extracted from a face is accepted' "
            "(2026-11-11) reproduced: the only refusal was the CURVE, and a "
            "projection runs along one normal, which a curve does not have. "
            "REOPENING is not a variable either: the same sketch, source and target "
            "measured 84.0 % in a fresh document, 84.0 % after the reopen with the "
            "body as InWorkObject, and 84.0 % with the geometrical set as "
            "InWorkObject - so the 2026-11-11 report's three PROJECT_FAILEDs came "
            "from something else, and scripts/b30_v0410_audit.py is the case that "
            "separates those variables. What a CURVED support still cannot do is "
            "KEEP MUCH: the same 26.164 mm glyph retained 16.0 % from zx, 15.1 % "
            "from the frame sketch and 13.6 % on the solid face, against one loft. "
            "A projection lands only where the surface faces the element, so most "
            "of a planar outline misses a curved face - and a fragment that small "
            "engraves as nothing."
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
            "from here is hiding construction geometry: catia_screenshot(clean=true) "
            "does it for a part built from geometrical sets, and "
            "catia_screenshot(clean='solid_only') (0.3.126) does it for a part built "
            "from SKETCHES, which is the case that used to answer 'this part has no "
            "geometrical sets, so nothing was hidden' while seven profile curves "
            "sat on the solid (2026-11-14 report, P1-3). Measured the same day: the "
            "sketch pass is the only one that can be hidden without taking the "
            "solid, so a clean capture of a sketch-section part now exists."
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
    {
        "member": "AddNewSolidEdgeFilletWithConstantRadius on a whole FEATURE",
        "status": "UNSUPPORTED_ON_B30",
        "detail": (
            "'Round every edge of this feature' does not exist through this call. "
            "Passing a Reference to the last feature - which catia_fillet used to "
            "do when edge_name was omitted, and called 'all edges of the last "
            "feature' - fails AT CONSTRUCTION on every shape tried on B30 "
            "2026-11-12: a 2-edge cylinder, a 12-edge rectangle, a 54-edge and a "
            "246-edge polyline, all answering '方法 "
            "AddNewSolidEdgeFilletWithConstantRadius 失败'. Four controlled cells "
            "rule out the arguments: feature reference versus whole-body "
            "reference, propagation 0 versus 1 - four failures, so neither the "
            "reference kind nor the propagation is the reason. The failure also "
            "arrives as a bare COM_ERROR that names no edge, which is what a "
            "2026-11-11 report hit twice. catia_fillet therefore ENUMERATES the "
            "body's edges and builds one feature per edge; see the next entry for "
            "why even that cannot round every edge."
        ),
    },
    {
        "member": "rounding every edge of one solid at one radius",
        "status": "AVAILABLE_WITH_LIMIT",
        "detail": (
            "Edges that share a vertex cannot all take a round in one pass, and "
            "the radius is not the variable. Measured on B30 2026-11-12 on an "
            "80x40x20 box, all 12 edges, five separate documents: R0.25 gave 4 "
            "edges and removed 2.1460 mm3, R0.5 gave 4 and 8.5841, R1.0 gave 4 and "
            "34.3363, R1.5 gave 4 and 77.2567, R3.0 gave 4 and 309.0266 - always "
            "the same four 40 mm edges, and every removed volume is exactly 4 "
            "edges' worth (4*40*(R^2 - pi*R^2/4)). The eight that refused are not "
            "short: the shortest edge on the box is 20 mm and 2*R is 3 mm. What "
            "the refusal is about is the material at a shared vertex, which is why "
            "lowering the radius does not help and why catia_fillet's reply names "
            "the edges that took and the edges that did not instead of promising "
            "a number. On a 246-edge polyline, 44 edges took and 202 refused. "
            "THE REAL PART, measured 2026-11-12 on the SiphonBottle.CATPart from "
            "the 2026-11-11 eighth report (2 features, 129 faces, 67278.8 mm3): it "
            "has 381 EDGES, not 129 - a face count is not an edge count and this "
            "one is off by 3x. Shortest edge 0.5770 mm, longest 22.0000 mm, and "
            "166 of the 381 are shorter than 2*R at R1.5. R1.5 with the default "
            "max_edges=200 took 186 s for its single batch attempt and rounded "
            "0 of 381. R0.25 with max_edges=400 - where 2*R = 0.5 mm and EVERY "
            "edge is long enough - rounded 101 of 381 in 458 s and left 103 "
            "shapes, removing 3.73 mm3. So on a real outline the short edges and "
            "the shared vertices both bite, and a 381-edge part cannot be rounded "
            "by asking for a radius: the outline has to lose corners first. "
            "Cost: about 0.6 s per edge that takes, and longer for the ones that "
            "refuse - 381 edges came to 458 s, not the 0.59 s/edge that a run of "
            "successful edges suggests."
        ),
    },
    {
        "member": "a sketch profile that is straight in places and curved in others",
        "status": "AVAILABLE_WITH_LIMIT",
        "detail": (
            "This is ONE call - catia_sketch_profile - and the 2026-11-11 eighth "
            "report listed it as a P0 gap because it had tried the two extremes: "
            "catia_sketch_spline rounds the straight stretches (it interpolates "
            "everywhere) and catia_sketch_strokes leaves the curved ones faceted "
            "(every segment is a chord). Both are true. Drawing each element with "
            "the primitive for its own kind was never tried, and it works: "
            "MEASURED on B30 2026-11-12, a 60x40 stadium as two 60 mm lines and two "
            "r=20 half circles gave catia_close_sketch the answer '2 arc(s), 2 "
            "line(s), outline 245.664 mm', and the 10 mm pad came out at 36566.37 "
            "mm3 - exactly 60*40*10 + pi*20^2*10. "
            "THE JUNCTION CHECK HAD TWO STANDARDS UNTIL 0.3.113, and the repair is "
            "the thing to know here. An arc is created from centre + radius + "
            "angles, so CATIA starts it at the RADIAL PROJECTION of the caller's "
            "p1 onto that circle. This tool compared the p1 it was GIVEN; "
            "catia_close_sketch compares what was DRAWN. Measured on B30 "
            "2026-11-12: a 73-segment fitted profile answered 'every one of the 73 "
            "junction(s) meets within 0.0000 mm' and then 'not closed - 60 loose "
            "endpoint(s) in 30 break(s), smallest gap 0.0043, widest 0.0960'. "
            "Reproduced 2026-11-12 on a 4-element stadium whose arc ends sat "
            "0.05 mm off the circle: 0.0000 mm here, '8 loose endpoint(s) in 4 "
            "break(s), 0.0500 mm' there. Now the planner rebuilds each arc's "
            "circle through its two endpoints and its own midpoint, so the ends "
            "that are drawn ARE the ends that were asked for: the 0.05 mm cell "
            "went to a 0.0000 mm junction and a pad that closed (5736.1 mm3 "
            "against the nominal 5716.8, +0.34 %, the radius having grown "
            "R20.0000 -> R20.1720 to take up the error). The reply states every "
            "circle it changed. A rebuild that would move the radius more than "
            "5 % is NOT made - that is bad data, not rounding - and the reply says "
            "where the ends will land instead. A junction gap wider than "
            "'tolerance' is still reported with both coordinates named, so what "
            "this tool says and what close_sketch says can no longer disagree. "
            "An arc's sweep can still be flipped by the short-way-round rule, in "
            "which case catia_sketch_arc says so in its own reply - read the two "
            "together."
        ),
    },
    {
        "member": "catia_fillet - rounding a C0 edge chain as ONE feature",
        "status": "UNSUPPORTED_ON_B30",
        "detail": (
            "Asked for by the 2026-11-12 report for a 73-segment outline: 146 "
            "edges around its two faces, and consecutive segments meet at C0 "
            "(each arc was fitted separately, so the tangents do not match). No "
            "parameter of this project takes an edge chain, and the two routes "
            "that do exist were both measured, both on 2026-11-12. "
            "(a) The WHOLE-FEATURE call - handing the factory a reference to the "
            "feature - fails AT CONSTRUCTION on every shape tried: a 2-edge "
            "cylinder, a 12-edge rectangle, a 54- and a 246-edge polyline, "
            "whatever the reference kind or the propagation. Four cells, four "
            "failures, which is why catia_fillet now enumerates the body's edges. "
            "(b) One edge at a time: on an 80x40x20 box, 12 edges and five radii "
            "from R0.25 to R3.0, exactly FOUR edges took every time - the ones "
            "that share no vertex with another that took - so the corners are "
            "left with gaps. propagation='tangency' is passed through, but it "
            "follows TANGENT continuity, which a fitted outline does not have. "
            "Whether CATIA offers a chain selection that follows G0 is UNKNOWN: "
            "it has not been probed, and this entry says so rather than claiming "
            "an API is absent. What does work on such a part is removing the "
            "corners in the sketch - 0.3.108's catia_sketch_profile draws the arcs "
            "directly, which took one real bottle from 381 edges to 147 and from "
            "0/381 rounded at R1.0 to 46/147 (reported 2026-11-12)."
        ),
    },
    {
        "member": "a B-spline through CONTROL POINTS rather than through the points given",
        "status": "UNSUPPORTED_ON_B30",
        "detail": (
            "HybridShapeFactory exposes ONE spline constructor out of its 160 "
            "members - AddNewSpline() - and no control-point variant (type info "
            "read on B30 2026-11-14). The spline object itself has 56 members "
            "including SetSplineType, GetSplineType, GetNbControlPoint and "
            "RemoveControlPoint, so a control-point mode looked likely; it is not "
            "one. Measured with the same four points (0,0,0) (10,25,0) (30,25,0) "
            "(40,0,0): type 0 gives a 79.1304 mm curve 0.0000 mm from the second "
            "point, type 1 gives 80.2511 mm and is STILL 0.0000 mm from it - a "
            "curve that passes through the point is interpolating, so type 1 "
            "changes the parametrisation, not the interpolation. What to do "
            "instead: accept interpolation and expect the shape to differ (a "
            "2026-11-12 report measured +29.3 % volume against the control-point "
            "curve it wanted), or pass the control points to catia_gsd_spline and "
            "get a curve through them. The 2D SKETCH spline has no such switch at "
            "all: Sketch.GeometricElements.Item(1).SetSplineType(0) answers just "
            "'Item.SetSplineType'."
        ),
    },
    {
        "member": "building sheet-metal geometry (walls, flanges, bends, unfold)",
        "status": "UNSUPPORTED_ON_B30",
        "detail": (
            "The Part object DOES expose SheetMetalFactory - measured on B30 "
            "2026-11-14, two of its 54 members are SheetMetalFactory and "
            "SheetMetalParameters - so 'no sheet metal in the API' would be wrong. "
            "What it can do is one thing: of the factory's 23 members, 22 are the "
            "COM base (QueryInterface ... Parent, Name, GetItem) and the only real "
            "one is CreateSheetMetalParameters(). That call succeeds and returns an "
            "object whose only member beyond the same base set is GetThickness - no "
            "wall, no flange, no bend, no unfold, no AddNew* of any kind. So the "
            "parameters can be read and nothing can be built from them, and no "
            "workbench route helps: the factory is there without the Sheet Metal "
            "Design workbench being loaded. A 2026-11-13 report replaced a "
            "sheet-metal part with a solid of 15 faces against 21 and +3.76 % "
            "volume; that solid is the measured substitute, and it is what "
            "catia_solid_loft / catia_pad / catia_thickness build."
        ),
    },
    {
        "member": "sampling a curve along its length",
        # AVAILABLE_WITH_LIMIT, not AVAILABLE: the vocabulary of this list has no
        # plain "works" status, and the three limits are real - a point per
        # sample, a point cannot be re-used, and only open curves take one.
        "status": "AVAILABLE_WITH_LIMIT",
        "detail": (
            "It works, and until 0.3.119 a message in catia_gsd_split said it did "
            "not. Measured on B30 2026-11-14 (scripts/b30_v0421_probe_deviation.py): "
            "HybridShapeFactory.AddNewPointOnCurveFromPercent(curve, ratio, 0) "
            "creates a point at that fraction, part.UpdateObject(point) updates it, "
            "and GetMeasurable(point_ref).GetMinimumDistance(target) reads its "
            "distance. On a 50 mm line rising from a face to 3 mm above it the five "
            "samples read 0.000000 / 0.750000 / 1.500000 / 2.250000 / 3.000000 mm - "
            "exactly 3*t, so the sample really sits at that fraction. A point CANNOT "
            "be re-used: HybridShapePointOnCurve.Ratio exists but answers "
            "\"Property ... can not be set\", so N samples need N points. The points "
            "can be deleted again - they are referenced by nothing, 4/4 went and "
            "part.Update() stayed fine with the volume unchanged. catia_measure_"
            "deviation is built on all of this; the same rule as engrave decides "
            "which curves accept a point: open line / open spline / projected "
            "outline yes, circle or closed spline no. THE REASON THIS MATTERS: a "
            "minimum distance is 0 for any curve that touches once, so "
            "catia_measure_distance can never answer 'does the contour follow the "
            "surface' - the maximum, sampled, can."
        ),
    },
    {
        "member": "the elements one catia_surface_engrave stroke leaves behind",
        "status": "AVAILABLE_WITH_LIMIT",
        "detail": (
            "They can be HIDDEN, not DELETED, and the difference was measured. "
            "Each stroke leaves four helpers in the geometrical set - the "
            "point-on-curve, the normal plane, the r=depth circle and the swept "
            "tube - plus the CloseSurface and the boolean; the 2026-11-12 report "
            "counted ~194 elements after 10 strokes. Measured on B30 2026-11-13 "
            "(scripts/b30_v0419_probe_engrave_cleanup.py), in FOUR separate "
            "documents: deleting any ONE of the four (tube, plane, point, circle) "
            "answers \"Feature 'X' deleted, but the part update failed afterwards\" "
            "and the part never updates again - the volume only looks right because "
            "a failed update keeps the last good state. So they are load-bearing, "
            "and the report's proposed test (\"only the boolean references them, so "
            "they can go\") is backwards: being referenced by that one feature is "
            "what keeps them. HIDING them is safe and is now the default "
            "(helpers='hide'): all four hidden, the same part answered 'Part "
            "updated successfully' with its volume unchanged (23987.4336290779 mm3 "
            "either side). There is still no purge, on purpose - a prefix purge "
            "would delete exactly the elements that cannot go. catia_delete_feature "
            "on any of the four is what breaks the tree; catia_set_visibility(name="
            "'<helper>', show=true) is how one comes back into view."
        ),
    },
    {
        "member": "joining several swept tubes into ONE cutter for a batch engrave",
        "status": "UNSUPPORTED_ON_B30",
        "detail": (
            "catia_gsd_join will not fuse disjoint tubes, so 'one shell for the "
            "whole word' is not a route. Measured on B30 2026-11-13 "
            "(scripts/b30_v0420_probe_batch_engrave.py), one fresh document per "
            "cell, three 50 mm traces 0.4 mm deep on a 60x40x10 block: joining the "
            "three tubes raised 'CATIA rejected the feature (update failed) and it "
            "was removed from the tree' with 方法 UpdateObject 失败 - the tubes do "
            "not touch, and Join needs them to. THE ROUTE THAT WORKS IS A SHARED "
            "TOOL BODY: three CloseSurfaces inside ONE body measured 75.3982 mm3, "
            "and one boolean removed 37.699 mm3 - the same cut three separate "
            "engraves made (37.698 mm3), from one boolean instead of three. That is "
            "what catia_surface_engrave(curves=[...]) does, measured end to end in "
            "scripts/b30_v0420_audit.py 5/5: 24000.0 -> 23962.3 mm3, tree ends with "
            "ONE 移除.1 and no leftover cutter body. What it does NOT save is the "
            "four helpers per curve - the sweep still needs them."
        ),
    },

    {
        "member": "the text of a CATIA message box",
        "status": "NOT_IN_TYPELIB",
        "detail": (
            "catia_surface_engrave and catia_curve_on_surface answer the boxes "
            "themselves - an unanswered one freezes the whole session, not just "
            "the call - and report HOW MANY they answered. What the box SAID is "
            "not readable: it is a Win32 dialog owned by another process, and the "
            "only call that would return its text, WM_GETTEXT across processes, is "
            "exactly what a modal box does not answer while it waits for a click. "
            "Application.Caption returns the application's title, not the box's, so "
            "it is not a substitute. Since 0.3.113 the reply carries a "
            "DEGRADED(result unverified) marker plus what to re-check "
            "(catia_gsd_list_elements, for the count and names the call should have "
            "added), because 'one box was answered' and 'the call was clean' are "
            "different claims."
        ),
    },
    {
        "member": "a pad or pocket from a sketch that also holds an open curve",
        "status": "UNSUPPORTED_ON_B30",
        "detail": (
            "It removes nothing, and the open curve does not have to divide "
            "anything. Measured on B30 2026-11-13, four fresh documents with one "
            "variable each (scripts/b30_v0419_probe_multiregion.py), a 60x40x10 "
            "block cut from a sketch on its top face: a circle alone removed 603.2 "
            "mm3; the SAME circle plus an open spline THROUGH it removed 0 "
            "(NO_MATERIAL); plus an open spline entirely INSIDE it, 0; plus an open "
            "LINE through it, 0. So it is not the region topology and not the "
            "spline - one free endpoint anywhere in the sketch makes the whole "
            "profile unusable, which is why the 2026-11-13 report's 'closed region "
            "+ 2 dividing splines' could never cut. catia_pad and catia_pocket now "
            "refuse such a sketch BEFORE CATIA runs (SKETCH_HAS_OPEN_CURVES), "
            "naming the free endpoints, so the failure costs neither a rollback nor "
            "a feature node; allow_open_curves=true bypasses the check. There is no "
            "'cut only this region' argument, and none is planned: with the open "
            "curves gone the sketch is ONE closed contour, so a sub-region has to "
            "be drawn as its own sketch."
        ),
    },
    {
        "member": "how many sections a solid loft actually holds",
        "status": "AVAILABLE_WITH_LIMIT",
        "detail": (
            "Readable, but ONLY while the loft that made them is still in hand. "
            "Measured on B30 2026-11-13 (scripts/b30_v0418_probe_loft_sections.py): "
            "HybridShapeLoft.GetSectionFromLoft(i) is 1-BASED, returns "
            "(reference, orientation, closing point), and RAISES past the last "
            "section - a 3-section loft answered at 1, 2 and 3 and raised at 0, 4 "
            "and 5, and reference.DisplayName gave '草图.1', '草图.2', '草图.3'. So "
            "catia_solid_loft now walks that list after its own update and names "
            "what CATIA kept, instead of leaving the caller to compare a volume "
            "with an integral - which is what the 2026-11-12 report had to do with "
            "45 sections in and 70047.2 mm3 out. AFTERWARDS IT IS GONE: "
            "Part.FindObject does not exist on this build (raises "
            "'<unknown>.FindObject'), and a shape read out of body.Shapes.Item(i) "
            "comes back as a bare base Item with no HybridShape - so a loft "
            "reopened from disk cannot be asked this question at all."
        ),
    },
    {
        "member": "closing CATIA's own sample catalogues",
        "status": "UNSUPPORTED_ON_B30",
        "detail": (
            "A .CATfct catalogue accepts Close() and stays open. Measured on B30 "
            "2026-11-13 with catia_close_document(all_unmodified=true) over a "
            "47-document session: 15 catalogues from the B30 resource directory "
            "(CATRacewaySample.CATfct, CATWaveguideSample.CATfct and so on) were "
            "reported unmodified and on disk, Close() returned without raising, "
            "and every one of them was still in Documents afterwards - the same "
            "deferral an assembly-held part gets, without a holder to name. The "
            "reply lists them as STILL OPEN instead of claiming they closed."
        ),
    },
    {
        "member": "Document.Saved as a test of 'is this safe to close'",
        "status": "MEASURED_UNEXPLAINED",
        "detail": (
            "Saved is honest about EDITS and wrong about EXISTENCE. Measured on "
            "B30 2026-11-13: a part answered Saved=False after a feature was added "
            "and Saved=True straight after catia_save_document, which is what the "
            "name promises - but a brand-new never-saved part AND a brand-new "
            "product both answered Saved=True while their FullName was a bare "
            "'Fresh.CATPart' / 'Prod.CATProduct' with no directory, and CATIA's own "
            "sample parts do the same ('CATPspPlantShipTypeLibraryManager.CATPart'). "
            "So Saved alone would have let a batch close work that exists nowhere. "
            "catia_close_document(all_unmodified=true) therefore also requires an "
            "absolute FullName that is a real file. Why CATIA reports an empty "
            "unsaved document as saved is UNKNOWN and not guessed at here."
        ),
    },
    {
        "member": "a named view that is actually square-on",
        "status": "AVAILABLE_WITH_LIMIT",
        "detail": (
            "The directions were always right; the PROJECTION was not, and that is "
            "what both 0.3.120 acceptance reports saw as 'front is 15-20 degrees "
            "off, the bottom of the bottle is visible'. Measured on B30 2026-11-13: "
            "right after PutSightDirection(0,1,0) and PutUpDirection(0,0,1), "
            "GetSightDirection reads back (0,1,0) and GetUpDirection (0,0,1) - the "
            "camera IS on -Y. Viewpoint3D.ProjectionMode reads 0 on this install, "
            "which is PERSPECTIVE, and a perspective eye point above a 60x40x4 "
            "plate shows that plate's top face in a front view, with the plate's "
            "edges converging. Set to 1 (PARALLEL) the same plate is a clean "
            "elevation - two captures differing in nothing else, "
            "scripts/b30_v0423_probe_view2.py. ProjectionMode=2 is not a third mode "
            "on B30: it is silently kept at 1. So catia_set_view forces parallel "
            "unless projection='perspective' is asked for, and reads the camera "
            "back through GetSightDirection/GetUpDirection (the by-ref arrays do "
            "not marshal late-bound, so SystemService.Evaluate is required) so an "
            "ignored Put is reported instead of photographed. THE REMAINING LIMIT: "
            "the seven view names are in the DOCUMENT's axes, not the model's, and "
            "nothing reports the current screen right/up the way the UI's 'Normal "
            "to' does - use direction=[x,y,z] to look along an arbitrary normal."
        ),
    },
    {
        "member": "a periodic 2D sketch spline",
        "status": "UNSUPPORTED_ON_B30",
        "detail": (
            "catia_sketch_spline(closed=true) closes a loop by REPEATING the first "
            "point, so the seam has a tangent discontinuity - reported 2026-11-13 by "
            "a user who saw it as a faint transverse line on a hole wall (with a "
            "'both' pocket there is also the join of the two one-sided cuts at the "
            "sketch plane). The sketch-side spline type exposes IsPeriodic as "
            "READ-ONLY and has no SetClosing (reporter-measured), while the GSD side "
            "does get a true periodic curve from HybridShapeSpline.SetClosing(1) - "
            "but a GSD curve cannot be used as the profile of a Part Design pocket. "
            "So: for a smooth closed profile cut with a pocket, use a GSD closed "
            "spline and a surface route (extrude/sweep + split), or accept the seam; "
            "for a closed profile that must be a SKETCH, accept the seam too. Not "
            "worked around here, and not claimed to be."
        ),
    },
    {
        "member": "fillet radii that differ between the two rims of one hole",
        "status": "UNSUPPORTED_ON_B30",
        "detail": (
            "A through-hole leaves two rims, but when the hole is cut 'both' ways "
            "CATIA can report ONE edge for both of them: the reporter measured "
            "Edge.64 at 116.1574 mm on a hole whose outline is 58.0787 mm - exactly "
            "twice - with one of the two rims' coordinates answering for both. Then "
            "one fillet rounds both rims and there is no way to give the front and "
            "back different radii (the reference picture needed 2:1). Measured by "
            "the reporter on B30 2026-11-13. The listing now marks an edge whose "
            "fingerprint names fewer than two bounding faces "
            "(bounded_by_incomplete), which is the visible symptom; splitting a "
            "fused edge per rim has no tool here and was not measured in this "
            "project, so catia_fillet still rounds both or neither."
        ),
    },
    {
        "member": "a face's own world bounding box",
        "status": "UNSUPPORTED_ON_B30",
        "detail": (
            "There is none to read, and three routes were tried on B30 2026-11-13 "
            "(scripts/b30_v0423_probe_faces.py, b30_v0423_probe_face_verts.py) on "
            "a 60x40x40 block: Measurable exposes 44 members and no bounding box "
            "(GetBoundingBox raises); SPAWorkbench.Inertias.Add(face) has none "
            "either; and adding a FACE cell to the selection and searching "
            "Vertex or Edge under it returns 0 hits on every face - the search "
            "resolves a body, not a face. WHAT A FACE DOES ANSWER, also measured "
            "there: GetCOG gives the FACE's own centre (top (30,20,40), bottom "
            "(30,20,0), while the solid's is (30,20,20)), and GetPlane gives a "
            "plane's origin and two directions on a planar face, which is a normal "
            "up to sign. Both are published per face by catia_list_faces, so a "
            "face can be picked by WHERE it is instead of by guessing area - the "
            "ask of both 0.3.120 reports. A curved face has a centroid and no "
            "plane (GetPlane raises), and its extent cannot be bounded by any of "
            "this; export an STL mesh for that."
        ),
    },
    {
        "member": "a solid tube whose section changes along the spine",
        "status": "AVAILABLE_WITH_LIMIT",
        "detail": (
            "catia_solid_sweep_variable (0.3.124) builds it: at each station it "
            "places a point on the spine, a plane normal to it and a circle of that "
            "station's radius, then ONE catia_solid_loft runs through the circles. "
            "Measured on B30 with a 50 mm line, r 10 -> 7 -> 4: 8 167 mm3 against "
            "the ruled sum 8 167 mm3. ONE limit remains and it is real: the spine "
            "has to carry a point-on-curve parameter (an open line or an open "
            "spline - a circle and a closed spline do not, measured 2026-11-11). "
            "AN ELLIPTIC SECTION IS SUPPORTED SINCE 0.3.126: sections take "
            "[[t, rx, ry], ...], where rx and ry are SEMI-axes (the 2026-11-14 "
            "report's earbud stem is 5.4 x 4.8 mm across). GSD has no ellipse "
            "primitive - measured on B30 2026-11-14, HybridShapeFactory answers no "
            "to AddNewEllipse and yes to AddNewConic and AddNewSpline, and a conic "
            "is an OPEN curve so a loft through it does not close - so an elliptic "
            "station is a CLOSED SPLINE through 64 points that lie on the ellipse. "
            "Points on a curve make the spline that curve: a 10 x 6 ellipse came "
            "back 51.0540 mm against the exact 51.0543 (-0.001 %), and a 10x6 -> "
            "5x3 loft held 1099.56 mm3 against the analytic 1099.56. Stations are "
            "oriented by the plane CATIA already builds (GetPlane, read through "
            "SystemService.Evaluate - it does not marshal late-bound) and carried "
            "station to station, so a bent spine does not twist the loft. "
            "The sketch route still works and is still the way to get a REAL "
            "ellipse rather than a dense one: catia_sketch_ellipse + "
            "catia_solid_loft."
        ),
    },
    {
        "member": "reading a 2D drawing (DXF / SVG / PDF) into a sketch",
        "status": "AVAILABLE_WITH_LIMIT",
        "detail": (
            "catia_read_drawing and catia_import_drawing (0.3.128) close the gap "
            "the 2026-11-14 report opened as P0-1. DXF (ASCII), SVG and VECTOR "
            "PDF are read with no external library and no rasterising: lines stay "
            "lines, arcs stay arcs (DXF bulges included) and circles stay "
            "circles, so a pad on the import is not faceted. Measured on B30 "
            "2026-11-14: an SVG plate 40x20 with a D6 hole padded to 7 717.26 "
            "mm3 against the analytic 7 717.26; a DXF stadium whose caps are "
            "bulge=1 arcs padded to 5 570.80 against 5 570.80 (chords would have "
            "given 4 000); a 1 x 0.5 INCH rectangle read through $INSUNITS landed "
            "at 25.4 x 12.7 mm and padded to 645.16. Three limits, all measured: "
            "(1) DWG is refused outright - it is a binary container and CATIA "
            "itself needs a translator, so the reply says to re-save as DXF. "
            "(2) RASTER images cannot be read at all: a scanned sheet has no "
            "geometry, only pixels. (3) TEXT drawn as text is not converted - a "
            "glyph's outline lives in the font program - and the reply counts the "
            "text runs it skipped, so \"the dimensions are missing\" is stated "
            "rather than discovered. A drawing whose text was converted to curves "
            "has no text runs and imports whole. One more, and it is the one that "
            "surprises: a CAD export writes one entity per EDGE, so almost "
            "nothing is a closed profile (the A3 sample: 672 contours, 83 closed, "
            "and all 83 were 3.4-4.6 mm arrowheads). The parsers therefore STITCH "
            "edges that share an endpoint, and where more than one unused edge "
            "meets a joint they leave it open and report the count instead of "
            "guessing - a wrong guess is a profile the drawing never had, and it "
            "pads to a plausible-looking solid."
        ),
    },
]


class DiagnosticsTools:
    """Diagnostics for the running CATIA installation."""

    #: Tools the server must NOT open a session for. Without this the server
    #: connected before dispatching everything, so catia_server_info - whose
    #: whole point is answering "there is no session" without starting one -
    #: launched CATIA to look up a version. Measured 2026-11-14: CI has no
    #: CATIA and every call through the server died at the connect stage.
    NO_SESSION = frozenset({"catia_server_info"})

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
            {
                "name": "catia_server_info",
                "description": (
                    "The cheap answer to 'what am I talking to': server name, "
                    "version, the number of tools registered, and the CATIA release "
                    "+ open-document count if a session is connected. "
                    "NO PROBES, which is the whole point: catia_diagnose builds a "
                    "Part Design factory, a GSD factory and a measurement to find "
                    "out what the install supports, and costs a few seconds of COM "
                    "for an answer that changes once per session. The 2026-11-14 "
                    "report asked for exactly this (P2-1) after using diagnose for "
                    "it. "
                    "IT NEVER CONNECTS: with no session open it answers "
                    "connected=false instead of starting one, because a version "
                    "lookup has no business opening a CAD application. "
                    "Nothing in the model is read, so nothing can be changed."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
        ]

    def _server_info_tool(self) -> dict[str, Any]:
        """Server identity plus, when there is one, the CATIA session behind it.

        Deliberately not `_diagnose`: that one probes factories and type libraries
        to answer "what can this INSTALL do". This answers "what am I talking to",
        which needs nothing but a caption and a count - and the 2026-11-14 report
        (P2-1) pointed out that reaching for the expensive tool for the cheap
        question is the wrong shape.
        """
        info = dict(self._server_info())
        catia: dict[str, Any] = {"connected": False}
        try:
            connected = bool(self.conn.is_connected)
        except Exception:  # noqa: BLE001 - a connection object that cannot say
            connected = False
        if connected:
            catia["connected"] = True
            try:
                catia["release"] = self.conn._get_version()
            except Exception:  # noqa: BLE001 - the caption is a courtesy
                catia["release"] = "unknown"
            try:
                catia["documents"] = int(self.conn.documents.Count)
            except Exception:  # noqa: BLE001
                pass
            try:
                catia["active_document"] = str(self.conn.active_document.Name)
            except Exception:  # noqa: BLE001 - no window, or none open
                pass
        info["catia"] = catia
        release = catia.get("release") if catia["connected"] else "not connected"
        return {
            "ok": True,
            "code": "SERVER_INFO",
            "tool": "catia_server_info",
            "message": (
                f"{info['name']} {info['version']} · "
                f"{info['tool_count']} tool(s) · CATIA {release}"
                + (f" · {catia['documents']} document(s)"
                   if catia.get("documents") is not None else "")),
            "data": info,
        }

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        match tool_name:
            case "catia_diagnose":
                return self._diagnose()
            case "catia_server_info":
                return self._server_info_tool()
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
