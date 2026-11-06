"""One envelope for every pre-execution refusal - and a guard that keeps it.

Reported 2026-11-05 (P1), against v0.3.66:

    catia_fillet {radius:3, edge_name:"NO_SUCH_EDGE"}
      -> COM_ERROR / execute / modified:null / recovery block / no next_actions

0.3.43 had a table with that exact call in it. The envelope was fixed by
converting fifteen raise sites by hand; the sixteenth was missed, and every
module written since (Knowledge in 0.3.63, Drafting in 0.3.64-0.3.66) started
from the old shape again.

Why this file is not just "convert the rest":

  * A server-layer INFERENCE was tried first and reverted the same hour -
    ``test_server_runtime`` raises ``ValueError("Wrong sketch")`` as its stand-in
    for an ordinary failure, and the inference turned that into
    ``ARGUMENT_REJECTED`` / ``modified: false``: a claimed fact about a model
    nobody had read. A Python type is not evidence that a tool only looked at
    its arguments.
  * So the envelope is DECLARED by the raise site, and this file does two things:
    it asserts the shape for every case the fix touched, and it FAILS when a new
    bare raise starts talking about an argument. That second test is the one
    that matters - it is what stops a module written next month from silently
    reintroducing the defect.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
from types import SimpleNamespace

import pytest

from catia_mcp.results import ArgumentRejected
from catia_mcp.server import CATIAMCPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOLS = ROOT / "catia_mcp" / "tools"
PACKAGE = ROOT / "catia_mcp"


# ── the scan: a bare raise outside an except block ──────────────────────────
#
# 0.3.69 replaced the keyword scan. The old guard matched a message against
# ("must", "unknown", "requires", ...) and kept a baseline of the sites it could
# not fix: 81 entries. Reading the other 88 bare raises by hand showed the table
# was never the problem - "Pass either 'points' or 'element', not both" and "No
# active sketch" were refusals too, invisible only because of their wording.
#
# So the rule is structural now, and a structure cannot be paraphrased away:
# a raise inside an `except` block re-wraps a failure CATIA produced (execute
# stage, no argument to fix); a raise ANYWHERE ELSE is a decision the tool made.
# A decision has a code. There is no baseline to grow, because there is nothing
# left to register.

BARE = {"ValueError", "RuntimeError", "TypeError", "KeyError"}


def _raised_name(node: ast.Raise) -> str:
    if isinstance(node.exc, ast.Call):
        fn = node.exc.func
        return getattr(fn, "id", getattr(fn, "attr", ""))
    if isinstance(node.exc, ast.Name):
        return node.exc.id
    return ""


def _raised_message(node: ast.Raise) -> str:
    if not isinstance(node.exc, ast.Call) or not node.exc.args:
        return ""
    first = node.exc.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    if isinstance(first, ast.JoinedStr):
        return "".join(part.value for part in first.values
                       if isinstance(part, ast.Constant)
                       and isinstance(part.value, str))
    return ""


class _Scan(ast.NodeVisitor):
    def __init__(self, name: str) -> None:
        self.name = name
        self.found: list[tuple[str, int, str, str]] = []
        self._inside_except = 0

    def visit_ExceptHandler(self, node):  # noqa: N802 - ast API name
        self._inside_except += 1
        self.generic_visit(node)
        self._inside_except -= 1

    def visit_Raise(self, node):  # noqa: N802 - ast API name
        if _raised_name(node) in BARE and not self._inside_except:
            self.found.append((self.name, node.lineno, _raised_name(node),
                               _raised_message(node)))
        self.generic_visit(node)


def scan_bare_raises() -> list[tuple[str, int, str, str]]:
    """Every raise of a code-less exception that is NOT re-wrapping a failure.

    Whole package, not just `tools/`: 0.3.70 converted the eleven that lived in
    connection.py, dialogs.py, guard.py, regime.py, server.py, arguments.py and
    topo_search.py. Those answered COM_ERROR for things a caller cannot cause
    ("Duplicate tool", "unknown guarded operation") or cannot fix by changing an
    argument ("no Part document behind the active one").
    """
    found: list[tuple[str, int, str, str]] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        scanner = _Scan(path.name)
        scanner.visit(ast.parse(path.read_text(encoding="utf-8")))
        found.extend(scanner.found)
    return found


def test_no_tool_raises_a_bare_exception_of_its_own():
    """The guard. An unreported refusal is a COM_ERROR for a call that never ran.

    0.3.43 fixed fifteen sites by hand and three modules written afterwards
    started from the old shape again; 0.3.68 converted twenty and left 81
    registered; 0.3.69 converted all 169 that were left. The way that stops
    repeating is a rule the code cannot satisfy by accident - either the raise
    is inside an `except` (a CATIA failure being reported) or it carries a code.
    """
    found = scan_bare_raises()
    assert not found, (
        "these raises carry no code and are the tool's own decision, so they "
        "answer COM_ERROR / modified:null for a call that never reached CATIA:\n  "
        + "\n  ".join(f"{name}:{line} {cls}: {message[:80]!r}"
                      for name, line, cls, message in found)
        + "\nRaise ArgumentRejected (bad argument), PreconditionRejected (model "
          "state), ArgumentOutOfRange (index/name past the end), or "
          "CodedError(..., stage=\"execute\") (CATIA ran and the result was not "
          "what the call asked for)."
    )


# ── the shape itself, through the server ───────────────────────────────────


def _envelope(result) -> dict:
    return json.loads(result.content[0].text)


def _assert_validate_envelope(reply: dict) -> None:
    assert reply["ok"] is False
    assert reply["data"]["failure_stage"] == "validate", reply["data"]
    assert reply["effects"]["modified"] is False
    assert "recovery" not in reply, "a refusal before the first COM call has no model to repair"
    assert "inspect the CATIA model" not in reply["message"]
    assert reply["data"].get("next_actions") or reply["data"].get("next_action_note")


@pytest.mark.parametrize("name,arguments", [
    # A writer missing an argument: the case 0.3.39 was written for.
    ("catia_pad", {}),
    # 0.3.66's four frame refusals - the report's §2.2 - all now schema-level.
    ("catia_drawing_add_frame", {"language": "klingon"}),
    ("catia_drawing_add_frame", {"margin": 0}),
    ("catia_drawing_add_frame", {"margin_left": -5}),
    ("catia_drawing_add_frame", {"title_block": {"width": 0}}),
    # Neighbouring tools whose refusals were the same shape.
    ("catia_drawing_set_sheet", {"paper": "Letter"}),
    ("catia_drawing_set_sheet", {"orientation": "bestfit"}),
    # An argument the tool never declares (0.3.43's P2).
    ("catia_get_inertia", {"component": "X"}),
    # No such tool at all.
    ("catia_nope", {}),
])
def test_every_pre_execution_refusal_has_the_validate_shape(name, arguments):
    """Offline by construction: every case is refused before ensure_connected()."""
    reply = _envelope(CATIAMCPServer()._execute_tool(name, arguments))
    _assert_validate_envelope(reply)


def test_a_refusal_that_needs_a_table_still_arrives_with_a_code():
    """Schema cannot express every rule, so the tool layer must still declare.

    The label keys are one of those: an object's properties cannot say "only
    these four". Drafting needs an open drawing before it can reach its own
    checks, so this one goes through the stub sheet rather than the server -
    what it proves is the exception the envelope reads.
    """
    from test_reported_issues_2026_11_05_v0366 import _Sheet, _tools

    tools = _tools(_Sheet())
    with pytest.raises(ArgumentRejected) as caught:
        tools.execute("catia_drawing_add_frame",
                      {"paper": "A3", "labels": {"tolerance": "0.1"}})
    assert caught.value.code == "ARGUMENT_REJECTED"
    assert caught.value.stage == "validate"
    assert "nothing was drawn" in json.dumps(caught.value.next_actions)


# ── the sites the report names, called directly ────────────────────────────


def test_the_edge_reference_refusal_is_the_one_0_3_43_promised():
    """§2.1: this exact call was the example in the 0.3.43 fix table."""
    from catia_mcp.tools.part_design import PartDesignTools

    with pytest.raises(ArgumentRejected) as caught:
        PartDesignTools(None)._parse_edge_spec("NO_SUCH_EDGE")
    error = caught.value
    assert error.code == "ARGUMENT_REJECTED"
    assert error.stage == "validate"
    assert error.modified is False
    assert error.next_actions
    assert "catia_list_edges" in json.dumps(error.next_actions)


@pytest.mark.parametrize("call", [
    ("catia_shell", {"thickness": 0, "external_thickness": 0}),
])
def test_a_zero_wall_shell_is_a_parameter_refusal(call):
    """§2.3: named in the report as a leak through the old envelope.

    Either code is correct here: `thickness` carries a schema bound, so the
    refusal happens one layer earlier than the tool's own check. What must not
    happen is COM_ERROR / execute / modified:null.
    """
    name, arguments = call
    reply = _envelope(CATIAMCPServer()._execute_tool(name, arguments))
    assert reply["code"] in {"ARGUMENT_REJECTED", "INVALID_ARGUMENT"}
    _assert_validate_envelope(reply)


# ── P2: a failed create_parameter must not leave a parameter behind ────────


class _Params:
    def __init__(self, valuate_error: Exception | None = None) -> None:
        self.created: list[str] = []
        self.removed: list[str] = []
        self.valuate_error = valuate_error

    @property
    def Count(self) -> int:
        return 0

    def CreateDimension(self, name, kind, value):
        self.created.append(name)
        error = self.valuate_error

        def valuate(_text):
            if error is not None:
                raise error

        return SimpleNamespace(Name=name, ValuateFromString=valuate)

    def Remove(self, name):
        self.removed.append(name)


def _knowledge_tools(params):
    from catia_mcp.tools.knowledge import KnowledgeTools

    part = SimpleNamespace(Parameters=params, Update=lambda: None)
    conn = SimpleNamespace(ensure_connected=lambda: None,
                           get_active_part=lambda: part)
    return KnowledgeTools(conn)


def test_a_non_numeric_length_is_refused_before_anything_is_created():
    params = _Params()
    tools = _knowledge_tools(params)
    with pytest.raises(ArgumentRejected) as caught:
        tools._create_parameter({"name": "LEN366B", "type": "length",
                                 "value": "abc"})
    assert params.created == [], (
        "the old order created the parameter with value 0 and then ran "
        "float('abc'), leaving an orphan in the model")
    assert caught.value.code == "ARGUMENT_REJECTED"
    assert "30" in str(caught.value), "the message should show the accepted form"


def test_a_parameter_that_fails_after_creation_is_removed_again():
    params = _Params(valuate_error=RuntimeError("ValuateFromString failed"))
    tools = _knowledge_tools(params)
    with pytest.raises(ArgumentRejected) as caught:
        tools._create_parameter({"name": "ANG1", "type": "angle", "value": 45})
    assert params.created == ["ANG1"]
    assert params.removed == ["ANG1"], "the half-created parameter must be taken back out"
    assert "removed" in str(caught.value) or "unchanged" in str(caught.value)


# ── P3: the same mistake, one code ─────────────────────────────────────────


def test_a_plan_and_a_bare_call_name_the_same_argument_problem():
    """§4: bare call says UNKNOWN_ARGUMENT, the plan said only INVALID_PLAN."""
    server = CATIAMCPServer()
    reply = server.planner_tools._run_plan(
        {"steps": [{"tool": "catia_get_inertia", "args": {"component": "X"}}]})
    assert reply["ok"] is False
    assert reply["code"] == "INVALID_PLAN", "a plan-level refusal keeps its own code"
    assert reply["data"]["problem_code"] == "UNKNOWN_ARGUMENT", (
        "the bare call answers UNKNOWN_ARGUMENT for the same mistake, and a "
        "caller branching on code must not need two paths")
    assert reply["data"]["failure_stage"] == "validate"
    assert reply["effects"]["modified"] is False


# ── 0.3.71: the two exemptions that housed validate-stage failures ─────────

#: Codes that say "what you gave me does not exist" or "fix the argument". An
#: envelope that reports one of these at the execute stage is telling the caller
#: CATIA was asked to do something and failed, which is not what happened.
VALIDATE_CODES = {
    "ARGUMENT_REJECTED", "ARGUMENT_OUT_OF_RANGE", "PRECONDITION_FAILED",
    "INVALID_ARGUMENT", "UNKNOWN_ARGUMENT", "UNKNOWN_TOOL",
    "DOCUMENT_NOT_FOUND", "DOCUMENT_NAME_AMBIGUOUS", "SKETCH_NOT_FOUND",
    "COMPONENT_NOT_FOUND", "PART_NOT_LOADED", "PATH_NOT_ASCII",
    "INTERNAL_ERROR",
}
# DEPENDENCY_MISSING is deliberately absent: it is `connect`, which is also a
# stage that wrote nothing but is not about an argument.

#: A message that prints the list to choose from is a lookup, not a CATIA
#: failure - even when it is raised from inside an `except` that caught the
#: lookup miss. Those were the 9 sites that still answered COM_ERROR for a call
#: that never changed anything (reported 2026-11-06, P2-A).
LOOKUP_MARKERS = ("No active document", "Available:", "Top-level bodies:",
                  "See catia_list_", "Bodies:", ". Open:")


def _raises_inside_except() -> list[tuple[str, int, str, str]]:
    found: list[tuple[str, int, str, str]] = []

    class Scan(ast.NodeVisitor):
        def __init__(self, name: str) -> None:
            self.name = name
            self.depth = 0

        def visit_ExceptHandler(self, node):  # noqa: N802 - ast API name
            self.depth += 1
            self.generic_visit(node)
            self.depth -= 1

        def visit_Raise(self, node):  # noqa: N802 - ast API name
            if self.depth and _raised_name(node) in BARE:
                found.append((self.name, node.lineno, _raised_name(node),
                              _raised_message(node)))
            self.generic_visit(node)

    for path in sorted(PACKAGE.rglob("*.py")):
        Scan(path.name).visit(ast.parse(path.read_text(encoding="utf-8")))
    return found


def test_a_lookup_miss_is_not_reported_as_a_catia_failure():
    """`except` is an exemption for RE-REPORTING, not for translating a miss.

    `connection.active_document` answered COM_ERROR / execute / modified:null for
    "No active document in CATIA" - the first call of every cold session, with a
    message that already said what to do. Eight more sites did the same for a name
    that is not in the tree (reported 2026-11-06, P2-A). A message that prints the
    list to pick from is a lookup.
    """
    offenders = [row for row in _raises_inside_except()
                 if any(marker in row[3] for marker in LOOKUP_MARKERS)]
    assert not offenders, (
        "these print a list to choose from but carry no code, so they answer "
        "COM_ERROR / execute / modified:null for a lookup that changed nothing:/n  "
        + "\n  ".join(f"{name}:{line} {cls}: {message[:80]!r}"
                      for name, line, cls, message in offenders)
        + "\nRaise ArgumentRejected (the name is wrong) or PreconditionRejected "
          "(the document is not in that state).")


def test_a_validate_code_never_arrives_at_the_execute_stage():
    """P2-B: seven `CodedError(code=...modified=False)` had no stage at all.

    `server._execute_tool` defaults to "execute", so `DOCUMENT_NOT_FOUND` and
    `SKETCH_NOT_FOUND` said "CATIA was asked and failed" for a typo - the same
    misreading 0.3.68/0.3.69 set out to end, wearing a different code.
    """
    offenders: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                continue
            func = node.exc.func
            name = getattr(func, "id", getattr(func, "attr", ""))
            if name not in {"CodedError", "ArgumentRejected", "ArgumentOutOfRange",
                            "PreconditionRejected", "InternalError",
                            "ComponentNotFound", "PathNotSupported"}:
                continue
            # The four classes carry their own stage; only a bare CodedError can
            # get this wrong.
            if name != "CodedError":
                continue
            kwargs = {key.arg: key.value for key in node.exc.keywords}
            code_node = kwargs.get("code")
            if not isinstance(code_node, ast.Constant):
                continue
            code = code_node.value
            stage_node = kwargs.get("stage")
            stage = (stage_node.value if isinstance(stage_node, ast.Constant)
                     else None)
            if code in VALIDATE_CODES and stage != "validate":
                offenders.append(f"{path.name}:{node.lineno} {code} stage={stage!r}")
    assert not offenders, (
        "these carry a validate-family code but not stage='validate', so the "
        "envelope still reports the execute stage:/n  " + "\n  ".join(offenders))
