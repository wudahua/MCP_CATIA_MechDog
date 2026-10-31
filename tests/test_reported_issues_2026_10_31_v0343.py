"""0.3.43 - the two 2026-10-31 acceptance reports, against 0.3.42.

REPORT 1, P1: "a rejection the tool makes itself still answers COM_ERROR".
0.3.39 promised one envelope for every pre-execution refusal and shipped three of
the four entry points. The missing one is the largest: the lookups a TOOL does
for itself after the schema passed - "Edge.99 is out of range: the final shape
exposes 12 edge(s)", "was called without 'feature_name'". Those raised a bare
RuntimeError, so the server saw no declared code, called it a communication
failure, reported `failure_stage: "execute"`, left `modified: null` for a call
that provably changed nothing, and appended "check in_work_object, it may be a
geometrical set" under a message that had just said the edge number was too high.
The reviewer's row T3 (a fixable argument) and R2 (a feature CATIA rejected and
the tool rolled back) were indistinguishable to anything branching on fields.

REPORT 1, P2: "catia_get_inertia silently drops component=".
It answered 5760.0 mm3 - the ACTIVE part's volume - for {"component": "NP42.1"},
whose part is 1536.0 mm3, with isError=false and no warning. `component` is
declared by catia_get_bounding_box and read by nobody in the measurement module,
so a habit learned from the sister tool became a silent wrong number.

REPORT 2, P2: `cold_parts` was still named in two schema descriptions three
versions after the field became `session_first_parts`. The reason it survived is
worth more than the fix: a 0.3.30 test asserted that the description contained
the string "cold_parts", so the suite was pinning the ghost in place.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest


# ────────────────────────────── 1. the envelope for a tool's own refusal


class _Shapes:
    """Two features, numbered the way CATIA numbers them."""

    Count = 2

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def Item(self, index: int):
        return SimpleNamespace(Name=f"Pad.{index}")


class _Body:
    def __init__(self) -> None:
        self.Shapes = _Shapes()


def _server_with_a_body():
    """A server whose connection reaches a two-feature body and no CATIA."""
    from catia_mcp.server import CATIAMCPServer

    server = CATIAMCPServer()
    body = _Body()
    part = SimpleNamespace(Bodies=SimpleNamespace(Count=1, Item=lambda i: body))
    server.connection.ensure_connected = lambda: None
    server.connection.get_active_part = lambda: part
    server.connection.get_active_part_body = lambda: body
    return server, body


def _envelope(result) -> dict:
    return json.loads(result.content[0].text)


@pytest.mark.parametrize("arguments,expected_code", [
    ({}, "ARGUMENT_REJECTED"),                  # T1: no feature_name, no index
    ({"index": 99}, "ARGUMENT_OUT_OF_RANGE"),   # T2: past the end of the tree
])
def test_a_tool_that_refuses_its_own_arguments_uses_the_validate_envelope(
        arguments, expected_code):
    """The six properties 0.3.39 promised, now on the fourth entry point too."""
    server, _body = _server_with_a_body()
    reply = _envelope(server._execute_tool("catia_delete_feature", arguments))

    assert reply["ok"] is False
    assert reply["code"] == expected_code
    assert reply["data"]["failure_stage"] == "validate"
    assert reply["effects"]["modified"] is False
    assert "recovery" not in reply, reply.get("recovery")
    # The sentence that made T3 unreadable: an edge index and a poisoned
    # in-work object are not the same failure, and the old envelope said both.
    assert "in_work_object" not in reply["message"]
    assert "model changes were not verified" not in json.dumps(reply)
    assert reply["data"]["next_actions"]


def test_the_message_survives_verbatim_because_it_was_already_right():
    """Only the envelope was wrong. A rewrite of these messages would lose detail."""
    server, _body = _server_with_a_body()
    reply = _envelope(server._execute_tool("catia_delete_feature", {"index": 99}))
    assert "index 99 is out of range" in reply["message"]
    assert "2 feature(s)" in reply["message"]


def test_a_real_catia_rejection_is_still_an_execution_failure():
    """The distinction must survive the fix: R2 is not a validate-stage refusal."""
    from catia_mcp.results import FeatureRejected

    server, _body = _server_with_a_body()

    def refuse(name, arguments):
        raise FeatureRejected("CATIA rejected the fillet", rolled_back=True)

    server.part_design_tools.execute = refuse
    reply = _envelope(server._execute_tool("catia_fillet", {"radius": 400.0}))
    assert reply["data"]["failure_stage"] == "execute"
    assert reply["recovery"]["rolled_back"] is True
    assert reply["effects"]["modified"] is False


# ────────────────────────────── 2. an argument nobody reads is refused


def test_an_undeclared_argument_is_refused_instead_of_dropped():
    """P2 of report 1: `component` on catia_get_inertia measured the wrong part."""
    from catia_mcp.server import CATIAMCPServer

    server = CATIAMCPServer()
    reply = _envelope(server._execute_tool(
        "catia_get_inertia", {"component": "NP42.1"}))
    assert reply["ok"] is False
    assert reply["code"] == "UNKNOWN_ARGUMENT"
    assert reply["data"]["failure_stage"] == "validate"
    assert reply["data"]["unknown_arguments"] == ["component"]
    assert "document" in reply["data"]["declared_arguments"]
    assert "density" in reply["data"]["declared_arguments"]


def test_the_alias_the_tool_really_reads_is_declared():
    """`document_name` is read by the dispatcher, so it may not look like a typo."""
    from catia_mcp.server import CATIAMCPServer

    props = CATIAMCPServer()._schemas["catia_get_inertia"]["properties"]
    assert "document_name" in props
    assert "alias" in props["document_name"]["description"].casefold()


def test_a_tool_that_declares_nothing_is_left_alone():
    """No properties block means "cannot tell an alias from a typo", not "reject all"."""
    from catia_mcp.server import CATIAMCPServer

    server = CATIAMCPServer()
    server._schemas["catia_screenshot"] = {"type": "object"}
    assert server._declared_arguments("catia_screenshot") is None


def test_a_plan_step_with_an_undeclared_argument_fails_validation():
    """A dry run is the only place this can be caught before steps execute."""
    from catia_mcp.tools.planner import PlannerTools

    planner = PlannerTools(connection=None)
    planner.bind({"catia_get_inertia": SimpleNamespace()})
    planner._schemas = {"catia_get_inertia": {
        "properties": {"density": {}, "document": {}}}}
    problems = planner._validate_steps(
        [{"tool": "catia_get_inertia", "args": {"component": "NP42.1"}}])
    assert problems and "does not read 'component'" in problems[0]


# ────────────────────────────── 3. no schema may name a field that does not exist


_FIELD_SHAPE = re.compile(r"(_parts|_ms|_total|_note|_reason|_measured|_count)$")


def _declared_emit_keys() -> set[str]:
    """Every string used as a dict key anywhere in the package."""
    keys: set[str] = set()
    for path in (Path(__file__).resolve().parents[1] / "catia_mcp").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        keys.add(key.value)
    return keys


def _schema_mentions() -> dict[str, set[str]]:
    """Quoted snake_case tokens in every tool's description, with who says them."""
    from catia_mcp.server import CATIAMCPServer

    server = CATIAMCPServer()
    mentioned: dict[str, set[str]] = {}
    for name, schema in server._schemas.items():
        blobs = [schema.get("description") or ""]
        for prop in (schema.get("properties") or {}).values():
            blobs.append(prop.get("description") or "")
        for blob in blobs:
            for token in re.findall(r"[`'\"]([A-Za-z][A-Za-z0-9_]{2,})[`'\"]", blob):
                mentioned.setdefault(token, set()).add(name)
    return mentioned


def test_no_schema_names_a_field_that_the_package_never_emits():
    """The ghost-field guard: `cold_parts` lived three versions inside this hole.

    It is a narrow net on purpose - only tokens shaped like a published field
    (`*_parts`, `*_ms`, `*_total`, `*_note`, `*_reason`, ...), because that is the
    shape a renamed field leaves behind. A wider net catches function names and
    CATIA API names and gets ignored.
    """
    emitted = _declared_emit_keys()
    ghosts = {
        token: where
        for token, where in _schema_mentions().items()
        if _FIELD_SHAPE.search(token) and token not in emitted
    }
    assert not ghosts, (
        "schema text names fields the package never emits: "
        + ", ".join(f"{token!r} (in {sorted(where)[:2]})"
                    for token, where in sorted(ghosts.items())))


def test_the_renamed_regime_field_is_named_where_it_is_described():
    """The specific three-version ghost, both places, by name."""
    from catia_mcp.server import CATIAMCPServer

    server = CATIAMCPServer()
    box = server._schemas["catia_get_bounding_box"]
    warm_up = box["properties"]["warm_up"]["description"]
    assert "session_first_parts" in warm_up
    assert "cold_parts" not in warm_up
    # The tool's own description, which lives in the tool definition rather than
    # in the inputSchema the server keeps for validation.
    description = [d for d in server.measurement_tools.get_tool_definitions()
                   if d["name"] == "catia_get_bounding_box"][0]["description"]
    assert "session_first_parts" in description
    assert "cold_parts" not in description
