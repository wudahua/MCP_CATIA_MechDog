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
from catia_mcp.results import CodedError
from catia_mcp.results import reject_validation
from catia_mcp.results import failure_code as shared_failure_code
from catia_mcp.topo_search import topology_cells

#: Result text longer than this is truncated in the per-step report, so a plan
#: with fifty steps does not return a megabyte of chatter.
MAX_RESULT_CHARS = 600


def _failure_code(message: str, error: BaseException | None = None) -> str:
    """Kept as the planner's name for the shared classifier.

    The table lives in ``catia_mcp.results.failure_code`` because it used to live
    HERE only: a failed step read the message while a bare call read the
    exception type, so one failed pad answered ``NO_MATERIAL`` in a plan and
    ``COM_ERROR`` on its own (reported 2026-10-28). Two paths, one table.
    """
    return shared_failure_code(message, error)


class PlannerTools:
    """Batch executor for build plans."""

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection
        self._router: dict[str, Any] | None = None
        self._schemas: dict[str, dict[str, Any]] | None = None

    def bind(self, router: dict[str, Any]) -> None:
        """Give the planner access to every registered tool module."""
        self._router = router
        self._schemas = None

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
                    "replaying dozens of individual calls. "
                    "NOT nestable: 'catia_run_plan' and 'catia_measure_topology' - a plan "
                    "containing either is rejected before any step runs, with the step "
                    "number and the tool named (a topology count is already part of the "
                    "measurements, and nesting a plan gives nothing but ambiguity). "
                    "Every step's args are validated against that tool's own schema "
                    "before the first step runs: a missing argument comes back as "
                    "code BAD_ARGS with details[{step, tool, missing, path, schema}], "
                    "not as a KeyError halfway through the model."
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
                                    "measure": {
                                        "type": "boolean",
                                        "default": False,
                                        "description": (
                                            "Measure the active part after THIS step "
                                            "and store it in the step's report as "
                                            "'measurements'. That is how a plan that "
                                            "builds several parts keeps one volume "
                                            "per part without thirty separate calls."
                                        ),
                                    },
                                },
                                "required": ["tool"],
                            },
                        },
                        "expect": {
                            "anyOf": [
                                {"type": "object"},
                                {"type": "array", "items": {"type": "object"}},
                            ],
                            "description": (
                                "Assertions measured after the last step. An object "
                                "checks the last active document. A LIST checks one "
                                "entry each, and any entry carrying "
                                "'document': 'P06' is measured on THAT open document - "
                                "so one plan can build thirty parts and assert all "
                                "thirty (reported 2026-10-25: a merged plan returned "
                                "only the last part's numbers)."
                            ),
                            "additionalProperties": False,
                            "properties": {
                                "document": {
                                    "type": "string",
                                    "description": (
                                        "Measure this OPEN document instead of the "
                                        "active one (alias: doc)."
                                    ),
                                },
                                "doc": {"type": "string",
                                        "description": "Alias of 'document'."},
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
                continue
            # The same check the server makes for a bare call, applied to a step:
            # without it a plan could pass an argument the tool never reads and
            # get a plan of successful, wrong steps back. Plan validation is the
            # only place a dry run can catch it, so it belongs here rather than
            # at execution time (reported 2026-10-31, P2).
            schema = self._tool_schemas().get(tool) or {}
            props = schema.get("properties")
            if isinstance(props, dict):
                args = step.get("args") or {}
                unknown = [key for key in args if key not in props]
                if unknown:
                    problems.append(
                        f"step {index}: {tool} does not read "
                        f"{', '.join(repr(k) for k in sorted(unknown))} - it takes "
                        f"{', '.join(sorted(props)) or 'no arguments'}")
        return problems

    def _tool_schemas(self) -> dict[str, dict[str, Any]]:
        """Every registered tool's inputSchema, keyed by tool name."""
        if self._schemas is not None:
            return self._schemas
        schemas: dict[str, dict[str, Any]] = {}
        modules = {}
        for module in (self._router or {}).values():
            modules[id(module)] = module
        for module in modules.values():
            try:
                definitions = module.get_tool_definitions()
            except Exception:  # noqa: BLE001 - a schema is a courtesy
                continue
            for definition in definitions:
                schemas[definition["name"]] = definition.get("inputSchema") or {}
        self._schemas = schemas
        return schemas

    def _validate_args(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Check each step's args against its own tool schema before running it.

        A step with a missing argument used to surface as a bare
        ``STEP_FAILED / KeyError: 'name'`` after the plan had already changed the
        model (reported 2026-10-25), while the same call made directly to the
        tool was rejected with a readable message. Same check, same wording,
        before anything runs.
        """
        from jsonschema import Draft7Validator  # noqa: PLC0415 - only for plans

        schemas = self._tool_schemas()
        problems: list[dict[str, Any]] = []
        for index, step in enumerate(steps, start=1):
            tool = step.get("tool")
            schema = schemas.get(tool) if tool else None
            if not schema:
                continue
            args = step.get("args") or {}
            try:
                errors = sorted(
                    Draft7Validator(schema).iter_errors(args),
                    key=lambda error: [str(p) for p in error.path],
                )
            except Exception:  # noqa: BLE001 - never fail a plan over this
                continue
            if not errors:
                continue
            required = list(schema.get("required") or [])
            # A schema may accept several spellings (catia_activate_document takes
            # 'name', 'document' or 'document_name') and express it as anyOf of
            # required lists. Name the shortest unsatisfied branch, so the message
            # still says "missing: name" instead of listing every alternative.
            alternatives = [
                list(branch.get("required") or [])
                for branch in (schema.get("anyOf") or [])
                if isinstance(branch, dict) and branch.get("required")
            ]
            missing = [name for name in required if name not in args]
            if not required and alternatives:
                unsatisfied = [branch for branch in alternatives
                               if any(name not in args for name in branch)]
                if unsatisfied:
                    best = min(unsatisfied, key=len)
                    required, missing = list(best), list(best)
            first = errors[0]
            where = "/".join(str(part) for part in first.path) or "args"
            problems.append({
                "step": index,
                "tool": tool,
                "code": "BAD_ARGS",
                "missing": missing,
                "path": where,
                "message": (
                    f"step {index} ({tool}): {first.message}"
                    + (f" [missing: {', '.join(missing)}]" if missing else "")
                ),
                "schema": {
                    "required": required,
                    "properties": sorted((schema.get("properties") or {}).keys()),
                    **({"alternatives": alternatives} if alternatives else {}),
                },
            })
        return problems

    #: `expect` keys whose value has to be a NUMBER. A string here used to
    #: reach `float()` inside `_check`, i.e. AFTER the model had been built, and
    #: surfaced as an assertion failure about a measurement that was fine.
    _EXPECT_NUMBERS = ("volume_mm3", "area_mm2", "volume_tolerance_pct",
                       "area_tolerance_pct", "cog_tolerance_mm", "mass_kg",
                       "density_kg_m3")

    def _validate_expect(self, expect: Any) -> list[str]:
        """Everything wrong with `expect`, before a single step runs.

        `expect` is checked with the same seriousness as `steps`, because it is
        the half of a plan that says whether the plan WORKED. Its failures used
        to be discovered after the model had changed, and reported as
        `ASSERTION_FAILED` (a verdict on the model) or `COM_ERROR` (a verdict on
        CATIA) for what is a typo in the request (reported 2026-10-29, §5.5).
        """
        if expect is None:
            return []
        if isinstance(expect, dict):
            entries: list[Any] = [expect]
        elif isinstance(expect, list):
            entries = expect
        else:
            return [
                f"expect: must be an object (or a list of objects), not "
                f"{type(expect).__name__} - nothing was executed"]
        problems: list[str] = []
        for index, entry in enumerate(entries, start=1):
            where = f"expect[{index}]" if len(entries) > 1 else "expect"
            if not isinstance(entry, dict):
                problems.append(
                    f"{where}: must be an object, not {type(entry).__name__} - it "
                    "used to be skipped silently, so the plan answered PLAN_OK "
                    "with no assertions at all")
                continue
            for key in self._EXPECT_NUMBERS:
                if key not in entry:
                    continue
                value = entry[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    problems.append(
                        f"{where}.{key}: must be a number, got {value!r} - a "
                        "string here fails inside the comparison, after the model "
                        "has been built")
            if "cog" in entry:
                cog = entry["cog"]
                if (not isinstance(cog, (list, tuple)) or len(cog) != 3
                        or any(isinstance(v, bool) or not isinstance(v, (int, float))
                               for v in cog)):
                    problems.append(
                        f"{where}.cog: must be three numbers [x, y, z], got "
                        f"{cog!r}")
        return problems

    def _run_plan(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._router is None:
            raise RuntimeError("Planner is not bound to the tool router")
        steps = arguments.get("steps") or []
        if not steps:
            # Used to raise ValueError, which the server turned into COM_ERROR
            # plus "inspect the CATIA model before retrying" and a recovery block
            # - for a plan that never opened a document (reported 2026-10-29,
            # report B §5.1). A missing argument is an argument problem.
            return reject_validation(
                tool="catia_run_plan",
                code="INVALID_PLAN",
                message=(
                    "Plan rejected before touching the model: 'steps' is empty. A "
                    "plan needs at least one step; to check a document without "
                    "changing it, call catia_measure_topology or "
                    "catia_get_inertia directly."),
                next_actions=[
                    "Pass 'steps' as a list of {tool, args} objects - nothing "
                    "was opened, measured or changed by this call",
                    "If you only want to read the model, use the reading tools: "
                    "catia_measure_topology, catia_get_inertia, "
                    "catia_get_bounding_box",
                ],
            )

        problems = self._validate_steps(steps)
        arg_problems = self._validate_args(steps)
        expect_problems = self._validate_expect(arguments.get("expect"))
        if problems or arg_problems or expect_problems:
            messages = (problems + [p["message"] for p in arg_problems]
                        + expect_problems)
            # "BAD_ARGS" is the honest code when the only thing wrong is a step's
            # arguments: the plan itself is fine, and the caller has to fix a
            # call, not a sequence. "INVALID_EXPECT" separates the assertion half
            # from the step half - they are fixed in different places.
            if problems:
                code = "INVALID_PLAN"
            elif arg_problems:
                code = "BAD_ARGS"
            else:
                code = "INVALID_EXPECT"
            return reject_validation(
                tool="catia_run_plan",
                code=code,
                message="Plan rejected before touching the model: "
                        + "; ".join(messages[:3]),
                details=arg_problems or None,
                data={"problems": messages,
                      "expect_problems": expect_problems},
                next_actions=[
                    "Fix the named argument and re-submit: this plan was rejected "
                    "before its first step, so nothing was opened, measured or "
                    "changed",
                    "catia_run_plan(dry_run=true) checks a plan without running "
                    "it - the same validation, no execution",
                ],
            )
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
                message = str(error)
                # `RuntimeError: …` in front of a sentence that already explains
                # itself is noise, and every failed step used to carry the same
                # code, so a script could not branch at all (reported 2026-10-27).
                # The class name is kept as its own field, and the handful of
                # failures this server produces on purpose get their own code.
                entry.update(
                    ok=False,
                    code=_failure_code(message, error),
                    error=message,
                    error_type=type(error).__name__,
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
            if entry["ok"] and step.get("measure"):
                # Opt-in per step: measuring costs an inertia call, but it is the
                # only way one plan can report one volume per part it built.
                try:
                    entry["measurements"] = self._measure_part()
                except Exception as error:  # noqa: BLE001 - reported, not fatal
                    entry["measurements_error"] = f"{type(error).__name__}: {error}"
            entry["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
            report.append(entry)

            if not entry["ok"]:
                failed_at = index
                if stop_on_error:
                    break

        measure = arguments.get("measure", True) or bool(arguments.get("expect"))
        measurements: dict[str, Any] = {}
        assertions: list[dict[str, Any]] = []
        assertions_skipped_because = None
        measurements_note = None
        ran_to_the_end = (len(report) == len(steps))
        if measure and failed_at is None:
            measurements = self._measure_part()
            assertions = self._check_all(arguments.get("expect"), measurements)
        elif measure and failed_at is not None and not stop_on_error and ran_to_the_end:
            # The plan went on after a failure and reached its last step, so the
            # document HAS a final state - and an empty `measurements` made
            # "everything after the failure is unknown" out of "one step failed
            # and the rest ran and saved" (reported 2026-10-27). Measure it, but
            # do not evaluate `expect`: a broken plan cannot pass its assertions.
            try:
                measurements = self._measure_part()
                measurements_note = (
                    f"final state of the document after all {len(steps)} step(s) "
                    f"ran ({sum(1 for entry in report if not entry['ok'])} failed). "
                    "'expect' was NOT evaluated against it - a plan with a failed "
                    "step cannot pass its assertions")
            except Exception as error:  # noqa: BLE001 - reported, not fatal
                measurements_note = (
                    "the final state could not be measured: "
                    f"{type(error).__name__}: {error}")
        if arguments.get("expect") and failed_at is not None:
            # Say it out loud, whether or not a final measurement was taken.
            # Returning an empty `assertions` after a failed step is correct - the
            # plan is already broken - but a caller doing
            # `assert r["assertions"][0]["pass"]` then gets an IndexError, and one
            # reading `if r["assertions"]` reads it as "no assertions were asked
            # for" (reported 2026-10-27).
            failed = sum(1 for entry in report if not entry["ok"])
            assertions_skipped_because = (
                f"{failed} step(s) failed, so the plan's expect was not evaluated - "
                "'assertions' is empty because it was skipped, not because none "
                "were requested")

        failed_assertions = [a for a in assertions if not a["pass"]]
        ok = failed_at is None and not failed_assertions
        if failed_at is not None:
            code = "STEP_FAILED"
            message = self._failure_summary(report, stop_on_error, len(steps))
            if assertions_skipped_because:
                message += f" (assertions not evaluated: {len(report)} step(s) ran, " \
                           f"{sum(1 for e in report if not e['ok'])} failed)"
        elif failed_assertions:
            code = "ASSERTION_FAILED"
            message = "; ".join(self._assertion_line(a) for a in failed_assertions)
        else:
            code = "PLAN_OK"
            message = (
                f"{len(report)} steps executed"
                + (f", {len(assertions)} assertions passed" if assertions else "")
            )

        # Say WHY `measurements` is empty when it is. `assertions_status` got this
        # treatment on 2026-10-27 for the same reason, and the identical hole was
        # left one field over: a stopping plan (stop_on_error, failed at step 2)
        # answered `measurements: {}` with nothing to distinguish "skipped on
        # purpose" from "the measurement failed silently" (reported 2026-10-28,
        # §7.1).
        if measurements:
            measurements_status = "taken"
        elif measure and failed_at is not None:
            measurements_status = "not_taken"
            measurements_note = (
                f"stop_on_error aborted the plan at step {len(report)} of "
                f"{len(steps)}, so the document's final state was NOT measured - "
                "'measurements' is empty because it was skipped, not because "
                "nothing was asked for")
        else:
            measurements_status = "not_requested"
        data: dict[str, Any] = {
            "steps": report,
            "measurements": measurements,
            "measurements_status": measurements_status,
            "assertions": assertions,
            "executed": len(report),
            "planned": len(steps),
        }
        if assertions_skipped_because:
            data["assertions_status"] = "skipped"
            data["assertions_skipped_because"] = assertions_skipped_because
        if measurements_note:
            data["measurements_note"] = measurements_note
        return {
            "ok": ok,
            "code": code,
            "tool": "catia_run_plan",
            "message": message,
            "data": data,
        }

    @staticmethod
    def _failure_summary(report: list[dict[str, Any]], stop_on_error: bool,
                         planned: int | None = None) -> str:
        """Name every failed step, not just the last one, when the plan ran on.

        ``stop_on_error: false`` means "keep going after a failure", so a plan can
        finish with two or three broken steps. ``data.steps`` always listed them
        all, but the top-level ``message`` named one - and the message is often the
        only field that reaches a log (reported 2026-10-26: a 5-step plan with two
        failures read as one failure).

        A stopping plan says how many steps it never reached: "Step 13 (catia_pad)
        failed" leaves a caller unable to tell a 13-step plan from a 60-step one,
        and the report that asked for the count form asked for this too
        (2026-10-27, §5.1).
        """
        failed = [entry for entry in report if not entry["ok"]]
        first = failed[0]
        if stop_on_error:
            total = planned if planned is not None else len(report)
            not_run = max(total - len(report), 0)
            message = (f"1 of {total} planned steps failed at step "
                       f"{first['index']} ({first['tool']})")
            if not_run:
                message += f"; {not_run} step(s) were not run"
            return message
        listed = ", ".join(f"step {e['index']} ({e['tool']})" for e in failed)
        # One failure gets the same shape as two, because a script that parses this
        # message should not have to handle two grammars (reported 2026-10-27).
        return f"{len(failed)} of {len(report)} steps failed: {listed}"

    def _topology_target(self, document: str | None) -> tuple[Any, Any]:
        """The part and body to count: the named open document, or the active one."""
        if not document:
            return self.conn.get_active_part(), self.conn.get_active_part_body()
        doc, matches = self.conn.resolve_document(document)
        if doc is not None:
            try:
                part = self.conn.part_of(doc)
                return part, part.MainBody
            except Exception as exc:  # noqa: BLE001 - a product, most likely
                raise RuntimeError(
                    f"'{doc.Name}' is open but is not a Part document, so there is "
                    f"no solid to count ({str(exc).splitlines()[0][:80]}). Use "
                    "catia_measure_topology without 'document' for an assembly."
                ) from exc
        if matches:
            # Same refusal as the measurement route, from the same resolver: this
            # used to be a second copy of the "first hit wins" loop (reported
            # 2026-10-28, P1).
            rows = self.conn.describe_matches(matches)
            raise CodedError(
                f"'{document}' matches {len(rows)} open documents: "
                # The same formatter catia_get_inertia uses: dropping the
                # "never saved" clause here left the caller hunting for a path
                # that does not exist (reported 2026-10-29, §5.2).
                + self.conn.format_matches(rows)
                + ". Nothing was counted - pass the full path of the one you mean.",
                code="DOCUMENT_NAME_AMBIGUOUS",
                modified=False,
                data={"candidates": rows},
            )
        docs = self.conn.documents
        names = []
        for i in range(1, docs.Count + 1):
            try:
                names.append(str(docs.Item(i).Name))
            except Exception:  # noqa: BLE001
                continue
        raise CodedError(
            f"No open document matches '{document}'. Open documents: "
            f"{', '.join(names) or 'none'}. A part that exists only as a component "
            "of an open assembly is not in this list - open it with "
            "catia_open_document first.",
            # Same reasoning as the measurement route: a name that is not open is
            # not a CATIA failure (reported 2026-10-28, §5.3).
            code="DOCUMENT_NOT_FOUND",
            modified=False,
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
                # Same enumeration as catia_list_faces / catia_list_edges, which is
                # what Face.N / Edge.N address - now literally the same function,
                # because this counter used to apply its own filter and the two
                # drifted apart (reported three releases running, 2026-10-24).
                # It also handles the two failure modes reported on 2026-10-26: a
                # selection CATIA refuses to hand over, and a transient state in
                # which nearly every solid cell looks like construction (the same
                # untouched part answered "edges 1" and then "edges 12").
                stats: dict[str, Any] = {}
                cells = topology_cells(self.conn, body, kind, stats=stats)
                counts[key] = len(cells)
                if stats.get("raw", len(cells)) != len(cells):
                    by_reason = dict(stats.get("excluded") or {})
                    counts[f"{key}_excluded"] = {
                        "total": stats["raw"] - len(cells),
                        "sketch_wire": sum(v for reason, v in by_reason.items()
                                           if "wire" in reason),
                        "datum_plane_borders": sum(v for reason, v in by_reason.items()
                                                   if "border" in reason
                                                   or "corner" in reason),
                        "raw_total": stats["raw"],
                        "by_reason": by_reason,
                    }
                if stats.get("retried"):
                    counts[f"{key}_retried"] = (
                        "the first enumeration of this part made almost every cell "
                        "look like construction geometry, so it was repeated and "
                        "the larger answer kept. The number below is the second "
                        "one - if it disagrees with an earlier reading of the same "
                        "untouched part, treat the earlier one as the broken one.")
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

    def _measure_part(self, document: str | None = None) -> dict[str, Any]:
        if self._router is None:
            return {}
        if document:
            # A Product cannot be measured "by document": get_inertia needs a Part,
            # and the assembly's volume only comes from the aggregation route that
            # runs on the ACTIVE document. Activate, measure, restore (reported
            # 2026-10-26: `expect: {"document": "M113_asm", "volume_mm3": …}`
            # answered "exposes no 'Part'", so an assembly could not be asserted
            # at all in the per-document form).
            try:
                named = self._named_document(document)
            except Exception:  # noqa: BLE001 - let the named path report it
                named = None
            if named is not None and self._is_product_document(named):
                return self._measure_active_product(str(document))
        measurements: dict[str, Any] = {}
        target = {"document": document} if document else {}
        if document:
            measurements["document"] = document
        try:
            raw = self._router["catia_get_inertia"].execute("catia_get_inertia", target)
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
            #
            # The test is the DOCUMENT TYPE, not "does .Product resolve":
            # PartDocument carries a stub Product, so probing for the property
            # made every part report "n/a (Product document)" and threw away its
            # face/edge/vertex counts (found 2026-10-25 while adding per-document
            # assertions - the same trap that made add_component fail on a Part).
            if not self._is_product_document(self._named_document(document)):
                raise RuntimeError("not a Product document")
            measurements["topology"] = (
                "n/a (Product document: faces/edges/vertices belong to each part - "
                "run catia_measure_topology while a part is active)")
            # An assembly's surface area is not "missing data", it is not a
            # thing a Product has. A bare null read as "this assembly has no
            # area" (reported 2026-10-25).
            if measurements.get("area_mm2") is None:
                measurements["area_mm2"] = "n/a (Product document)"
        except Exception:  # noqa: BLE001 - not a Product, so count them
            try:
                counts = self._count_topology(document)
                measurements.update(
                    faces=counts.get("faces"),
                    edges=counts.get("edges"),
                    vertices=counts.get("vertices"),
                )
            except Exception as error:  # noqa: BLE001
                measurements["topology_error"] = f"{type(error).__name__}: {error}"
        return measurements

    def _measure_active_product(self, document: str) -> dict[str, Any]:
        """Measure an assembly by making it active, then put the session back."""
        previous = None
        try:
            previous = str(self.conn.active_document.Name)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._router["catia_activate_document"].execute(
                "catia_activate_document", {"name": document})
            data = self._measure_part()
        finally:
            if previous and previous != document:
                try:
                    self._router["catia_activate_document"].execute(
                        "catia_activate_document", {"name": previous})
                except Exception:  # noqa: BLE001 - the numbers are the point
                    pass
        data["document"] = document
        return data

    @staticmethod
    def _is_product_document(doc: Any) -> bool:
        """Whether ``doc`` is an assembly, decided by its type - not by ``.Product``."""
        try:
            kind = str(doc.Type)
        except Exception:  # noqa: BLE001 - fall back to the file name
            kind = ""
        if kind:
            return "PRODUCT" in kind.upper()
        try:
            return str(doc.FullName).upper().endswith(".CATPRODUCT")
        except Exception:  # noqa: BLE001 - unsaved and untyped: assume no
            return False

    def _named_document(self, document: str | None) -> Any:
        """The open document called ``document``, or the active one when None."""
        if not document:
            return self.conn.active_document
        docs = self.conn.documents
        key = document.strip().casefold()
        for i in range(1, docs.Count + 1):
            doc = docs.Item(i)
            try:
                names = {str(doc.Name).casefold(), str(doc.Name).casefold()[:-8]}
            except Exception:  # noqa: BLE001
                continue
            if key in names:
                return doc
        raise RuntimeError(
            f"'{document}' is not open. Documents: "
            + ", ".join(str(docs.Item(i).Name) for i in range(1, docs.Count + 1))
        )

    # ---- assertions ---------------------------------------------------------
    def _check_all(self, expect: Any, active: dict[str, Any]
                   ) -> list[dict[str, Any]]:
        """Assertions for one expect object, or one per entry of a list of them.

        An entry may name a document, which is measured on its own instead of the
        active one - the difference between "one plan per part" and one plan that
        builds and verifies thirty parts in a single submission (reported
        2026-10-25).
        """
        if not expect:
            return []
        entries = expect if isinstance(expect, list) else [expect]
        checks: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            document = entry.get("document") or entry.get("doc")
            actual = active
            if document:
                actual = self._measure_part(str(document))
            for check in self._check(entry, actual):
                if document:
                    check["document"] = str(document)
                checks.append(check)
        return checks

    @staticmethod
    def _assertion_line(check: dict[str, Any]) -> str:
        """One failed assertion, in words that say what actually happened.

        "expected 1.0, got None" is literally true and useless: it does not say
        whether the model is wrong or the measurement never happened.
        """
        where = f"{check['document']}: " if check.get("document") else ""
        if check.get("unavailable"):
            return (f"{where}{check['name']}: expected {check['expected']}, but it "
                    f"could not be measured ({check['unavailable']})")
        return (f"{where}{check['name']}: expected {check['expected']}, "
                f"got {check['actual']}")

    def _check(self, expect: dict[str, Any], actual: dict[str, Any]) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []

        def tolerance(value: float, target: float, pct: float) -> bool:
            return abs(value - target) <= abs(target) * pct / 100.0

        def why_missing() -> str:
            return str(actual.get("inertia_error") or
                       actual.get("topology_error") or
                       "the measurement returned nothing for this key")

        def unavailable(name: str, expected: Any, reason: str) -> None:
            # An assertion that cannot be evaluated is a FAILED assertion, never a
            # skipped one. 0.3.20 dropped it instead: `expect: {"document": …,
            # "volume_mm3": 1.0}` on an assembly answered PLAN_OK with an empty
            # `assertions` list, so a wrong expectation of 1.0 against 76 million
            # mm3 was reported as a pass (reported 2026-10-26).
            checks.append({
                "name": name,
                "expected": expected,
                "actual": None,
                "pass": False,
                "unavailable": reason,
            })

        if "volume_mm3" in expect:
            if actual.get("volume_mm3") is None:
                unavailable("volume_mm3", expect["volume_mm3"], why_missing())
            else:
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

        if "area_mm2" in expect:
            value = actual.get("area_mm2")
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                # "n/a (Product document)" lands here - a Product has no area, so
                # an area assertion against one cannot pass.
                unavailable("area_mm2", expect["area_mm2"],
                            value if isinstance(value, str) else why_missing())
            else:
                target = float(expect["area_mm2"])
                pct = float(expect.get("area_tolerance_pct", 1.0))
                checks.append(
                    {
                        "name": "area_mm2",
                        "expected": target,
                        "actual": round(float(value), 4),
                        "delta_pct": round((float(value) - target) / target * 100.0, 4) if target else None,
                        "tolerance_pct": pct,
                        "pass": tolerance(float(value), target, pct),
                    }
                )

        for key in ("faces", "edges", "vertices"):
            if key in expect:
                value = actual.get(key)
                # Counts are exact: a partial feature or a missing fillet shows up here
                # even when the volume is within tolerance.
                check: dict[str, Any] = {
                    "name": key,
                    "expected": expect[key],
                    "actual": value,
                    "pass": value == expect[key],
                }
                if value is None:
                    check["unavailable"] = why_missing()
                checks.append(check)

        if "cog" in expect:
            value = actual.get("cog")
            if not isinstance(value, (list, tuple)) or None in list(value):
                unavailable("cog", expect["cog"], why_missing())
            else:
                target = [float(v) for v in expect["cog"]]
                vector = [float(v) for v in value]
                distance = sum((a - b) ** 2 for a, b in zip(vector, target)) ** 0.5
                limit = float(expect.get("cog_tolerance_mm", 0.5))
                checks.append(
                    {
                        "name": "cog",
                        "expected": target,
                        "actual": [round(v, 4) for v in vector],
                        "distance_mm": round(distance, 4),
                        "tolerance_mm": limit,
                        "pass": distance <= limit,
                    }
                )
        return checks
