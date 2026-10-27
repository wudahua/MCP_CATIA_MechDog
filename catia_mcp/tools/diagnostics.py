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
                    "document at all)."
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
                raise ValueError(f"Unknown diagnostics tool: {tool_name}")

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
        report["factories"] = self._factory_probe()
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
        return snapshot

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

            info["pywin32_build"] = getattr(win32com, "__build__", "unknown")
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
                + " AddNew* methods). The checked list is a licence and "
                "regression sample, NOT the capability list - a method absent "
                "from it can still exist and work, so read typeinfo_addnew "
                "before concluding CATIA cannot do something."
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
