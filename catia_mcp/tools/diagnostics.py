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
            "catia_gsd_project now prints before building anything on the curve."
        ),
    },
    {
        "member": "engraving after a document is saved, closed and reopened",
        "status": "MEASURED_UNEXPLAINED",
        "detail": (
            "An engrave that works in the session that built the projection does not "
            "necessarily work after the file has been reopened. Measured on B30 "
            "2026-11-11 on a lofted r=40 cylinder closed into a solid: a r=6 sketch "
            "circle projected onto it, engrave depth 0.6, removed 9.991 mm3 (a 0.6 mm "
            "tube over 17.783 mm predicts 10.056). The document was saved, closed and "
            "reopened, and the SAME projection object - still 17.7831 mm long, still "
            "0.0000 mm from the body - removed NOTHING; a projection made after the "
            "reopen failed as well, and part.Update() then failed too. Which of the "
            "two changes across a reopen is unknown, so this is not reported as a "
            "cause anywhere in the code, only as this measurement. Until it is "
            "explained: make the projection and the engrave in the SAME session, and "
            "treat a NO_MATERIAL on a reopened file as this, not as a bad curve."
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
        "status": "DECLARED_NOT_MEASURED",
        "detail": (
            "Declared on the type library (read there by a 2026-11-10 report). No "
            "tool is wrapped and nobody has measured whether they update on B30, "
            "so treat this as unknown rather than either way - a rib request "
            "currently has to go around as a pad, which is a flat plate, not a "
            "round tube."
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
            "catia_sketch_strokes takes a whole outline in one call as polylines - "
            "the geometry half of the problem, with the font left to the caller."
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
