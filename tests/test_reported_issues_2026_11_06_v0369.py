"""0.3.69: every refusal carries a code, and the guard that keeps it structural.

0.3.68 converted twenty sites and registered the other 81 in a JSON baseline.
That baseline was the wrong shape of fix: it made "unfixed" a state you could
add to, and the scan that fed it matched WORDS ("must", "unknown", "requires"),
so "Pass either 'points' or 'element', not both" was invisible.

0.3.69 converts all 168 and replaces the vocabulary scan with a structural one:
a raise inside an `except` re-wraps a CATIA failure (execute stage); a raise
anywhere else is a decision the tool made, and a decision has a code.

What this file pins:

  * the three refusal classes, each with its own code and the same envelope;
  * `PreconditionRejected` is a ValueError, so the 80+ existing `except
    ValueError` handlers keep working;
  * the scan is not vacuously true - it finds a bare raise in source it is fed.
"""

from __future__ import annotations

import ast
import json

import pytest

from catia_mcp.results import (
    ArgumentRejected,
    CodedError,
    ComponentNotFound,
    PreconditionRejected,
)


# ── the three classes ──────────────────────────────────────────────────────


def test_each_refusal_class_says_what_the_caller_must_fix():
    assert ArgumentRejected.code == "ARGUMENT_REJECTED"
    assert PreconditionRejected.code == "PRECONDITION_FAILED"
    assert ComponentNotFound.code == "COMPONENT_NOT_FOUND"
    for refusal in (ArgumentRejected, PreconditionRejected):
        assert refusal.stage == "validate", (
            "both are refusals that wrote nothing; the envelope's stage is what "
            "suppresses the 'inspect the CATIA model' paragraph")
        assert refusal.modified is False


def test_a_precondition_is_still_a_value_error():
    """80+ sites and tests catch ValueError; a new class must not break them."""
    assert issubclass(PreconditionRejected, ValueError)
    assert issubclass(ArgumentRejected, ValueError)
    assert issubclass(PreconditionRejected, RuntimeError)
    with pytest.raises(ValueError):
        raise PreconditionRejected("the body has no solid shape yet")


def test_an_execution_failure_keeps_the_execute_stage():
    error = CodedError("CATIA kept 2 of the 3 sections", code="FEATURE_INCOMPLETE",
                       stage="execute")
    assert error.code == "FEATURE_INCOMPLETE"
    assert error.stage == "execute"
    # modified stays None on purpose: the call ran, and whether it left geometry
    # behind is exactly what the caller must check.
    assert error.modified is None


# ── two real raise sites, through their own methods ───────────────────────


def test_a_non_boolean_is_refused_by_the_knowledge_layer():
    from catia_mcp.tools.knowledge import _as_bool

    with pytest.raises(ArgumentRejected) as caught:
        _as_bool("maybe")
    assert caught.value.code == "ARGUMENT_REJECTED"
    assert "true/false" in str(caught.value)


def test_a_part_with_nothing_to_tessellate_is_a_precondition():
    """It used to be a bare ValueError: COM_ERROR for a call CATIA never saw."""
    from test_reported_issues_2026_11_04_v0360 import _document

    from catia_mcp.tools.export import ExportTools

    with pytest.raises(PreconditionRejected) as caught:
        ExportTools(None)._assert_has_mesh_geometry(_document([], []), "stl")
    error = caught.value
    assert error.code == "PRECONDITION_FAILED"
    assert error.stage == "validate"
    assert error.modified is False
    assert error.next_actions, "a refusal owes the caller the next step"


# ── a class used before it is imported ────────────────────────────────────

_BUILTIN_EXCEPTIONS = {
    "ValueError", "RuntimeError", "TypeError", "KeyError", "Exception",
    "SystemExit", "AssertionError", "NotImplementedError", "StopIteration",
}


def _reachable_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names |= {alias.asname or alias.name for alias in node.names}
        elif isinstance(node, ast.Import):
            names |= {(alias.asname or alias.name).split(".")[0]
                      for alias in node.names}
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {target.id for target in node.targets
                      if isinstance(target, ast.Name)}
        elif isinstance(node, ast.arg):
            names.add(node.arg)
    return names


def test_every_class_a_tool_raises_is_reachable_from_that_module():
    """A NameError raised inside a COM_ERROR envelope hides a good refusal.

    0.3.69 hit this twice: ``sketcher`` and ``document`` raised
    ``PreconditionRejected`` before importing it, and the caller saw
    ``code: COM_ERROR`` / ``stage: execute`` with the message
    "'PreconditionRejected' is not defined". pytest was green throughout,
    because no test walked those paths - the acceptance script on B30 found it.
    This walks the source instead.
    """
    import pathlib

    problems: list[str] = []
    tools = pathlib.Path(__file__).resolve().parent.parent / "catia_mcp"
    for path in sorted(tools.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        known = _reachable_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                continue
            func = node.exc.func
            name = getattr(func, "id", None)
            if not name or not name[0].isupper() or name in _BUILTIN_EXCEPTIONS:
                continue
            if name not in known:
                problems.append(f"{path.name}:{node.lineno} raises {name}")
    assert not problems, "not imported in that module:\n  " + "\n  ".join(problems)


# ── the guard is not vacuous ───────────────────────────────────────────────

def _scan_source(source: str) -> list[tuple[str, int, str, str]]:
    from test_argument_envelopes import _Scan

    scanner = _Scan("probe.py")
    scanner.visit(ast.parse(source))
    return scanner.found


def test_the_scan_finds_an_unreported_refusal():
    found = _scan_source(
        "def f(x):\n"
        "    if x < 0:\n"
        "        raise ValueError('x must be positive')\n")
    assert [(row[2], row[3]) for row in found] == [
        ("ValueError", "x must be positive")]


def test_the_scan_ignores_a_raise_that_reports_a_catia_failure():
    """Inside `except`, a bare raise reports CATIA's failure - that is execute."""
    found = _scan_source(
        "def f(doc):\n"
        "    try:\n"
        "        doc.Update()\n"
        "    except Exception as exc:\n"
        "        raise RuntimeError('CATIA refused the pad') from exc\n")
    assert found == []


def test_the_package_has_no_unreported_refusal():
    """169 conversions, 0 left - and no baseline to add to."""
    from test_argument_envelopes import scan_bare_raises

    assert scan_bare_raises() == []


def test_the_baseline_file_is_gone():
    """A register of unfixed sites was the wrong shape: it could only grow."""
    import pathlib

    stale = pathlib.Path(__file__).resolve().parent / "argument_rejection_baseline.json"
    assert not stale.exists()


# ── the envelope a precondition produces, end to end (offline) ─────────────


def test_a_precondition_still_arrives_with_the_validate_shape(monkeypatch):
    """The server reads `stage` off the exception; this is that path.

    `catia_drawing_add_frame` refuses an oversized title block before drawing,
    through ArgumentRejected - the same code path a PreconditionRejected takes.
    The reply is what a caller branches on, so the reply is what is asserted.
    """
    from catia_mcp.server import CATIAMCPServer

    reply = json.loads(CATIAMCPServer()._execute_tool(
        "catia_drawing_add_frame",
        {"title_block": {"width": 4000}}).content[0].text)
    assert reply["ok"] is False
    assert reply["data"]["failure_stage"] == "validate"
    assert reply["effects"]["modified"] is False
    assert "recovery" not in reply
