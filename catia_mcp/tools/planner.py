"""Run a whole build plan in a single tool call, with geometric assertions.

Why this exists
---------------
Every MCP tool call is one round trip through the agent. Building one revolved
part from a spec takes 4-8 calls (sketch, close, revolve, close surface,
pocket...), so a 9-part assembly costs dozens of round trips and the model is
irreproducible: the recipe lives in the conversation, not in an artefact.

``catia_run_plan`` takes the recipe as data — a list of ``{tool, args}`` steps —
executes it serially on the worker thread, then verifies the result against
expected geometry. The expectations are the point: a volume check alone lets a
part grow an isolated boss, cut a pocket the wrong way, or drill a hole into
air without changing the volume by more than a rounding error. Face/edge/vertex
counts and the centre of gravity catch those.
"""

from __future__ import annotations

import json
import time
from typing import Any

from catia_mcp.connection import CATIAConnection
from catia_mcp.topo_search import is_construction, nls_of, search_topology

#: Result text longer than this is truncated in the per-step report, so a plan
#: with fifty steps does not return a megabyte of chatter.
MAX_RESULT_CHARS = 600


class PlannerTools:
    """Batch executor for build plans."""

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection
        self._router: dict[str, Any] | None = None

    def bind(self, router: dict[str, Any]) -> None:
        """Give the planner access to every registered tool module."""
        self._router = router

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "catia_run_plan",
                "description": (
                    "Execute a build plan as one call: an ordered list of {tool, args} steps "
                    "(any catia_* tool except this one), followed by optional geometric "
                    "assertions. Returns per-step outcomes plus measured volume, area, "
                    "face/edge/vertex counts and centre of gravity compared against the "
                    "expected values. Use it to make a build reproducible instead of "
                    "replaying dozens of individual calls."
                ),
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "steps": {
                            "type": "array",
                            "description": "Ordered steps; each is {tool, args, id?}",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "tool": {"type": "string"},
                                    "args": {"type": "object"},
                                    "id": {
                                        "type": "string",
                                        "description": "Label echoed back in the report",
                                    },
                                },
                                "required": ["tool"],
                            },
                        },
                        "expect": {
                            "type": "object",
                            "description": "Assertions measured after the last step",
                            "additionalProperties": False,
                            "properties": {
                                "volume_mm3": {"type": "number"},
                                "volume_tolerance_pct": {"type": "number", "default": 1.0},
                                "area_mm2": {"type": "number"},
                                "area_tolerance_pct": {"type": "number", "default": 1.0},
                                "faces": {"type": "integer"},
                                "edges": {"type": "integer"},
                                "vertices": {"type": "integer"},
                                "cog": {
                                    "type": "array",
                                    "items": {"type": "number"},
                                    "description": "Expected centre of gravity (local, mm)",
                                },
                                "cog_tolerance_mm": {"type": "number", "default": 0.5},
                            },
                        },
                        "measure": {
                            "type": "boolean",
                            "default": True,
                            "description": "Measure the part even when `expect` is absent",
                        },
                        "stop_on_error": {
                            "type": "boolean",
                            "default": True,
                            "description": "Abort the remaining steps after the first failure",
                        },
                        "dry_run": {
                            "type": "boolean",
                            "default": False,
                            "description": "Validate the plan without executing it",
                        },
                    },
                    "required": ["steps"],
                },
            },
            {
                "name": "catia_measure_topology",
                "description": (
                    "Count faces, edges and vertices of the active part. These counts are "
                    "the cheapest guard against a part that has the right volume but the "
                    "wrong shape: an isolated boss, an inverted pocket or a hole drilled "
                    "into air all leave the volume nearly unchanged while changing the "
                    "topology. Pass 'document' to count another OPEN part by name without "
                    "activating it - the same argument catia_get_inertia and "
                    "catia_get_bounding_box take, and the reason checking nine parts used "
                    "to cost nine open + activate pairs (reported 2026-10-25). A part "
                    "that exists only as a component of an open assembly is not in "
                    "Documents and cannot be counted this way: open it first."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "document": {
                            "type": "string",
                            "description": (
                                "Name or stem of an open document to measure "
                                "(e.g. 'P01' or 'P01.CATPart'). Default: the "
                                "active document."
                            ),
                        }
                    },
                },
            },
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        match tool_name:
            case "catia_run_plan":
                return self._run_plan(arguments)
            case "catia_measure_topology":
                return json.dumps(self._count_topology(arguments.get("document")),
                                 indent=2, ensure_ascii=False)
            case _:
                raise ValueError(f"Unknown planner tool: {tool_name}")

    # ---- plan execution -----------------------------------------------------
    def _validate_steps(self, steps: list[dict[str, Any]]) -> list[str]:
        if self._router is None:
            raise RuntimeError("Planner is not bound to the tool router")
        problems: list[str] = []
        for index, step in enumerate(steps, start=1):
            tool = step.get("tool")
            if not tool:
                problems.append(f"step {index}: missing 'tool'")
                continue
            if tool in {"catia_run_plan", "catia_measure_topology"}:
                problems.append(f"step {index}: '{tool}' cannot be nested")
                continue
            if tool not in self._router:
                problems.append(f"step {index}: unknown tool '{tool}'")
        return problems

    def _run_plan(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._router is None:
            raise RuntimeError("Planner is not bound to the tool router")
        steps = arguments.get("steps") or []
        if not steps:
            raise ValueError("steps must contain at least one entry")

        problems = self._validate_steps(steps)
        if problems:
            return {
                "ok": False,
                "code": "INVALID_PLAN",
                "tool": "catia_run_plan",
                "message": "Plan rejected before touching the model",
                "data": {"problems": problems},
            }
        if arguments.get("dry_run"):
            return {
                "ok": True,
                "code": "PLAN_VALID",
                "tool": "catia_run_plan",
                "message": f"Plan of {len(steps)} steps is valid; nothing executed",
                "data": {
                    "steps": [
                        {"index": i, "tool": s["tool"], "id": s.get("id")}
                        for i, s in enumerate(steps, start=1)
                    ]
                },
            }

        stop_on_error = arguments.get("stop_on_error", True)
        report: list[dict[str, Any]] = []
        failed_at: int | None = None

        for index, step in enumerate(steps, start=1):
            tool = step["tool"]
            args = step.get("args") or {}
            entry: dict[str, Any] = {"index": index, "tool": tool, "id": step.get("id")}
            started = time.perf_counter()
            try:
                raw = self._router[tool].execute(tool, args)
            except Exception as error:  # noqa: BLE001 - COM raises opaque errors
                entry.update(
                    ok=False,
                    code="STEP_EXCEPTION",
                    error=f"{type(error).__name__}: {error}",
                )
            else:
                if isinstance(raw, dict):
                    entry.update(
                        ok=raw.get("ok") is True,
                        code=raw.get("code"),
                        result=raw.get("message"),
                    )
                    if raw.get("ok") is not True:
                        entry["error"] = raw.get("message")
                else:
                    entry.update(ok=True, result=self._trim(raw))
            entry["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
            report.append(entry)

            if not entry["ok"]:
                failed_at = index
                if stop_on_error:
                    break

        measure = arguments.get("measure", True) or bool(arguments.get("expect"))
        measurements: dict[str, Any] = {}
        assertions: list[dict[str, Any]] = []
        if measure and failed_at is None:
            measurements = self._measure_part()
            assertions = self._check(arguments.get("expect") or {}, measurements)

        failed_assertions = [a for a in assertions if not a["pass"]]
        ok = failed_at is None and not failed_assertions
        if failed_at is not None:
            code = "STEP_FAILED"
            message = f"Step {failed_at} ({report[failed_at - 1]['tool']}) failed"
        elif failed_assertions:
            code = "ASSERTION_FAILED"
            message = "; ".join(
                f"{a['name']}: expected {a['expected']}, got {a['actual']}" for a in failed_assertions
            )
        else:
            code = "PLAN_OK"
            message = (
                f"{len(report)} steps executed"
                + (f", {len(assertions)} assertions passed" if assertions else "")
            )

        return {
            "ok": ok,
            "code": code,
            "tool": "catia_run_plan",
            "message": message,
            "data": {
                "steps": report,
                "measurements": measurements,
                "assertions": assertions,
                "executed": len(report),
                "planned": len(steps),
            },
        }

    def _topology_target(self, document: str | None) -> tuple[Any, Any]:
        """The part and body to count: the named open document, or the active one."""
        if not document:
            return self.conn.get_active_part(), self.conn.get_active_part_body()
        docs = self.conn.documents
        key = document.strip().casefold()
        for i in range(1, docs.Count + 1):
            doc = docs.Item(i)
            try:
                name = str(doc.Name)
            except Exception:  # noqa: BLE001
                continue
            if key not in (name.casefold(), name.rsplit(".", 1)[0].casefold()):
                continue
            try:
                part = self.conn.part_of(doc)
                return part, part.MainBody
            except Exception as exc:  # noqa: BLE001 - a product, most likely
                raise RuntimeError(
                    f"'{name}' is open but is not a Part document, so there is no "
                    f"solid to count ({str(exc).splitlines()[0][:80]}). Use "
                    "catia_measure_topology without 'document' for an assembly."
                ) from exc
        names = []
        for i in range(1, docs.Count + 1):
            try:
                names.append(str(docs.Item(i).Name))
            except Exception:  # noqa: BLE001
                continue
        raise RuntimeError(
            f"No open document matches '{document}'. Open documents: "
            f"{', '.join(names) or 'none'}. A part that exists only as a component "
            "of an open assembly is not in this list - open it with "
            "catia_open_document first."
        )

    # Published in every topology answer: the counting rule changed twice, and a
    # stored expectation that is not re-baselined then reads as "the model is
    # wrong" (reported 2026-10-25).
    _COUNTING_RULE = (
        "These counts cover the SOLID only. Since 0.3.11 sketch wires and "
        "datum-plane borders are excluded, and since 0.3.15 sketch-wire vertex "
        "corners are too, which changed the numbers reported for an unchanged "
        "part (measured 0.3.10 -> 0.3.14: vertices 72 -> 96, faces 48 -> 46, "
        "edges 109 -> 101). Any expect.vertices / expect.faces / expect.edges "
        "stored before that has to be re-baselined before it is used as an "
        "acceptance criterion."
    )

    @staticmethod
    def _trim(value: Any) -> str:
        text = value if isinstance(value, str) else str(value)
        if len(text) <= MAX_RESULT_CHARS:
            return text
        return text[:MAX_RESULT_CHARS] + f"... ({len(text)} chars)"

    # ---- measurement --------------------------------------------------------
    def _is_wireframe(self, selection_item: Any) -> bool:
        """True for sketch geometry that a topology search also returns.

        ``Topology.Edge`` on a body matches both solid edges (``Selection_REdge``)
        and the consumed sketch's lines (``Selection_WireREdge``); vertices are
        ``Selection_FVertex`` vs ``Selection_WireFVertex``. Counting ``sel.Count``
        blindly reports a 40x20x10 box as 16 edges / 12 vertices instead of 12 / 8,
        which silently defeats the whole point of comparing counts.
        """
        try:
            name = self.conn._dynamic(selection_item.Value).Name
        except Exception:
            return False
        return isinstance(name, str) and name.startswith("Selection_Wire")

    def _count_topology(self, document: str | None = None) -> dict[str, Any]:
        part, body = self._topology_target(document)
        counts: dict[str, Any] = {}
        try:
            empty = body.Shapes.Count == 0
        except Exception:  # noqa: BLE001 - assume not empty, the counts will say
            empty = False
        if empty:
            # Nothing to search: an empty body answers "0 0 0", and the answer
            # needs to say why (reported 2026-10-25 - "0 faces" and "this part
            # holds no solid" are different verdicts, and only the second means
            # the counts cannot be used at all).
            counts.update({"faces": 0, "edges": 0, "vertices": 0, "part": part.Name})
            if document:
                counts["document"] = document
            counts["solid"] = False
            counts["note_empty"] = (
                f"Body '{body.Name}' holds no solid at all, so these zeroes mean "
                "'nothing to count', not 'counted and found empty'. The part may "
                "hold GSD surfaces, curves or sketches - measure those by name, and "
                "check catia_list_features for the tree."
            )
            counts["counting_rule"] = self._COUNTING_RULE
            return counts
        sel = self.conn.hso
        try:
            for kind, key in (("Face", "faces"), ("Edge", "edges"),
                              ("Vertex", "vertices")):
                # Same definition as catia_list_faces / catia_list_edges, which is
                # what Face.N / Edge.N address. This counter used to apply its own
                # filter (dynamic type starts with 'Selection_Wire') while the
                # listings filtered on the NLS, so one part reported 18 edges here
                # and 14 there - 109 vs 101 on the reporter's part, three releases
                # running (2026-10-24). The two rules differ by exactly the datum
                # planes' own borders ('Selection_BorderREdge' on
                # 'Brp:(GSMPlane.N;k)'), which are not edges of any solid: a plain
                # 40x20x10 block with a boss reported 18 here and 14 in
                # catia_list_edges, and the four extra were GSMPlane borders.
                sel.Clear()
                sel.Add(body)
                search_topology(sel, kind)
                raw = sel.Count
                solid = 0
                wire = 0
                borders = 0
                for index in range(1, raw + 1):
                    item = sel.Item(index)
                    if not is_construction(nls_of(item)):
                        solid += 1
                    elif self._is_wireframe(item):
                        wire += 1
                    else:
                        borders += 1
                counts[key] = solid
                if raw != solid:
                    counts[f"{key}_excluded"] = {
                        "total": raw - solid,
                        "sketch_wire": wire,
                        "datum_plane_borders": borders,
                        "raw_total": raw,
                    }
        finally:
            try:
                sel.Clear()
            except Exception:  # noqa: BLE001
                pass
        if "edges_excluded" in counts:
            excluded = counts["edges_excluded"]
            counts["note"] = (
                f"{excluded['total']} of {excluded['raw_total']} cells the Edge "
                "search returns are not edges of the solid (consumed sketch wires "
                f"{excluded['sketch_wire']}, datum-plane borders "
                f"{excluded['datum_plane_borders']}), so they are excluded here "
                "exactly as in catia_list_edges - the count, Edge.N and the edge "
                "fingerprints all refer to the same set.")
        counts["part"] = part.Name
        if counts.get("vertices") == 0 and "vertices_excluded" in counts:
            # A revolved solid genuinely has no vertex (a cylinder, a sphere, a
            # cone), and the excluded cells are sketch corners and the datum
            # planes' own borders. Without this sentence the pair reads like a
            # broken measurement: "0 vertices, 20 excluded" (reported 2026-10-25).
            excluded = counts["vertices_excluded"]
            counts["vertices_note"] = (
                "0 vertices is a real answer for a revolved or fully curved solid "
                "- there is no corner to count. The "
                f"{excluded['total']} excluded cell(s) are not missing vertices: "
                f"{excluded['sketch_wire']} sketch corner(s) and "
                f"{excluded['datum_plane_borders']} datum-plane border corner(s) "
                "that the Vertex search also returns."
            )
        if document:
            counts["document"] = document
        try:
            # "0 faces" and "this part holds no solid" are different answers, and
            # only the second one means the counts cannot be used at all. Empty
            # parts came back as three zeroes with no word of warning (reported
            # 2026-10-25); catia_get_inertia already says "holds no solid".
            if body.Shapes.Count == 0:
                counts["solid"] = False
                counts["note_empty"] = (
                    f"Body '{body.Name}' holds no solid at all, so these zeroes mean "
                    "'nothing to count', not 'counted and found empty'. The part may "
                    "hold GSD surfaces, curves or sketches - measure those by name, "
                    "and check catia_list_features for the tree."
                )
            else:
                counts["solid"] = True
        except Exception:  # noqa: BLE001 - the flag is informative only
            pass
        # The counting rule is part of the answer, not a footnote: it changed
        # twice (0.3.11 excluded construction geometry, 0.3.15 excluded
        # sketch-wire vertex corners too), and each time an unchanged part
        # reported different numbers. A stored expectation that is not
        # re-baselined then reads as "the model is wrong" (reported 2026-10-25).
        counts["counting_rule"] = self._COUNTING_RULE
        return counts

    def _measure_part(self) -> dict[str, Any]:
        if self._router is None:
            return {}
        measurements: dict[str, Any] = {}
        try:
            raw = self._router["catia_get_inertia"].execute("catia_get_inertia", {})
            inertia = raw if isinstance(raw, dict) else json.loads(raw)
            measurements["volume_mm3"] = inertia.get("volume_mm3")
            measurements["area_mm2"] = inertia.get("area_mm2")
            center = inertia.get("center_of_gravity_mm")
            if not isinstance(center, dict):
                # get_inertia reports an unavailable centroid as a string; treating
                # that as a dict raised "'str' object has no attribute 'get'".
                center = {}
            measurements["cog"] = [
                center.get("x"),
                center.get("y"),
                center.get("z"),
            ]
        except Exception as error:  # noqa: BLE001
            measurements["inertia_error"] = f"{type(error).__name__}: {error}"
        try:
            # A Product has no Part, so counting topology on it cannot work - but
            # that is not an error of this run, and reporting it as
            # "topology_error" next to a perfectly good volume read like a failed
            # measurement (reported 2026-10-24).
            self.conn.product_of(self.conn.active_document)
            measurements["topology"] = (
                "n/a (Product document: faces/edges/vertices belong to each part - "
                "run catia_measure_topology while a part is active)")
        except Exception:  # noqa: BLE001 - not a Product, so count them
            try:
                counts = self._count_topology()
                measurements.update(
                    faces=counts.get("faces"),
                    edges=counts.get("edges"),
                    vertices=counts.get("vertices"),
                )
            except Exception as error:  # noqa: BLE001
                measurements["topology_error"] = f"{type(error).__name__}: {error}"
        return measurements

    # ---- assertions ---------------------------------------------------------
    def _check(self, expect: dict[str, Any], actual: dict[str, Any]) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []

        def tolerance(value: float, target: float, pct: float) -> bool:
            return abs(value - target) <= abs(target) * pct / 100.0

        if "volume_mm3" in expect and actual.get("volume_mm3") is not None:
            target = float(expect["volume_mm3"])
            value = float(actual["volume_mm3"])
            pct = float(expect.get("volume_tolerance_pct", 1.0))
            checks.append(
                {
                    "name": "volume_mm3",
                    "expected": target,
                    "actual": round(value, 4),
                    "delta_pct": round((value - target) / target * 100.0, 4) if target else None,
                    "tolerance_pct": pct,
                    "pass": tolerance(value, target, pct),
                }
            )

        if "area_mm2" in expect and actual.get("area_mm2") is not None:
            target = float(expect["area_mm2"])
            value = float(actual["area_mm2"])
            pct = float(expect.get("area_tolerance_pct", 1.0))
            checks.append(
                {
                    "name": "area_mm2",
                    "expected": target,
                    "actual": round(value, 4),
                    "delta_pct": round((value - target) / target * 100.0, 4) if target else None,
                    "tolerance_pct": pct,
                    "pass": tolerance(value, target, pct),
                }
            )

        for key in ("faces", "edges", "vertices"):
            if key in expect:
                value = actual.get(key)
                # Counts are exact: a partial feature or a missing fillet shows up here
                # even when the volume is within tolerance.
                checks.append(
                    {
                        "name": key,
                        "expected": expect[key],
                        "actual": value,
                        "pass": value == expect[key],
                    }
                )

        if "cog" in expect and actual.get("cog"):
            target = [float(v) for v in expect["cog"]]
            value = [float(v) for v in actual["cog"]]
            distance = sum((a - b) ** 2 for a, b in zip(value, target)) ** 0.5
            limit = float(expect.get("cog_tolerance_mm", 0.5))
            checks.append(
                {
                    "name": "cog",
                    "expected": target,
                    "actual": [round(v, 4) for v in value],
                    "distance_mm": round(distance, 4),
                    "tolerance_mm": limit,
                    "pass": distance <= limit,
                }
            )
        return checks
