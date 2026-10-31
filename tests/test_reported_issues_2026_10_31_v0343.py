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
    """Every string used as a dict key anywhere in the package.

    Two spellings, because a payload is built both ways and only one of them used
    to be counted: ``{"volume_mm3": v}`` is a Dict key, ``payload["volume_mm3"] =
    v`` is a Subscript store. The first version of this guard read only Dicts
    (435 keys where the package really publishes 548), which is a hole in the
    *guard* rather than in the product: a field that only ever appears as
    ``payload["x"] = ...`` and is then quoted in a description would have been
    reported as a ghost (reported 2026-10-31, observation 1).
    """
    keys: set[str] = set()
    for path in (Path(__file__).resolve().parents[1] / "catia_mcp").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        keys.add(key.value)
            elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
                index = node.slice
                if isinstance(index, ast.Constant) and isinstance(index.value, str):
                    keys.add(index.value)
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


# Names this package has retired, and what replaced them. A renamed field leaves
# two traces: the new name in the code, the old one in prose that nobody re-read.
# The quoted-token guard above catches the old name only when it is written in
# quotes - and `cold_parts`, the ghost that lived three versions, was never quoted
# anywhere ("parts_measured, cold_parts, elapsed_ms"). A reviewer proved it by
# putting the bare word back: the guard passed (reported 2026-10-31, P2-B).
#
# So retired names are listed here and searched for as bare words. Adding to this
# dict is part of renaming a field - which is why the entry is checked below: the
# replacement has to exist, or the list itself has rotted.
RETIRED_FIELD_NAMES = {
    "cold_parts": "session_first_parts",
}


def test_no_schema_mentions_a_retired_field_name_even_bare():
    """`cold_parts` survived three versions because nothing looked for it plainly."""
    mentioned = _schema_mentions()
    retired = {
        old: sorted(where)
        for old, where in (
            (old, {name for name, text in _schema_texts().items() if old in text})
            for old in RETIRED_FIELD_NAMES)
        if where
    }
    assert not retired, (
        "schema text still names retired fields: "
        + ", ".join(f"{old!r} (in {where[:2]})" for old, where in sorted(retired.items()))
        + " - rename it to " + ", ".join(sorted(RETIRED_FIELD_NAMES.values())))
    # The list is only useful while its replacements are real.
    emitted = _declared_emit_keys()
    for old, new in RETIRED_FIELD_NAMES.items():
        assert new in emitted, (
            f"RETIRED_FIELD_NAMES maps {old!r} to {new!r}, which the package never "
            "emits - the list has rotted")


def _schema_texts() -> dict[str, str]:
    """Tool name -> all of the prose it publishes, as one string."""
    from catia_mcp.server import CATIAMCPServer

    server = CATIAMCPServer()
    texts: dict[str, str] = {}
    for module in server._tool_modules:
        for definition in module.get_tool_definitions():
            blob = [definition.get("description") or ""]
            for prop in (definition.get("inputSchema") or {}).get("properties", {}).values():
                blob.append(prop.get("description") or "")
            texts[definition["name"]] = " ".join(blob)
    return texts


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


# ────────────────── 4. the spellings a tool accepts must be the ones it declares
#
# 0.3.43 introduced the unknown-argument check and immediately refused `elem1`
# on catia_measure_distance - a spelling that tool's own description listed as
# accepted, because `_pick` knew it and the schema did not (reported 2026-10-31,
# P2-A). Fixed by giving both sides one list, in catia_mcp/arguments.py.


def test_every_spelling_measure_distance_accepts_is_declared():
    from catia_mcp.arguments import ELEMENT1_NAMES, ELEMENT2_NAMES
    from catia_mcp.server import CATIAMCPServer

    props = CATIAMCPServer()._schemas["catia_measure_distance"]["properties"]
    for name in ELEMENT1_NAMES + ELEMENT2_NAMES:
        assert name in props, f"{name} is accepted by _pick and missing from the schema"


def test_a_short_alias_reaches_the_resolver_now():
    """The exact call the report made: elem1 must not be an unknown argument."""
    from catia_mcp.server import CATIAMCPServer

    reply = _envelope(CATIAMCPServer()._execute_tool(
        "catia_measure_distance",
        {"element1": "Pad.1", "element2": "Pad.2", "elem1": "Pad.1"}))
    # Not a CATIA failure about the KEY - whatever happens next, it got past the
    # unknown-argument check, which is the regression being tested.
    assert reply["code"] != "UNKNOWN_ARGUMENT", reply


def test_every_picked_argument_is_declared_by_some_tool():
    """Guard: no tool may read a spelling its schema does not publish."""
    package = Path(__file__).resolve().parents[1] / "catia_mcp"
    from catia_mcp.server import CATIAMCPServer

    server = CATIAMCPServer()
    declared: set[str] = set()
    for schema in server._schemas.values():
        declared |= set(schema.get("properties") or {})

    picked: dict[str, int] = {}
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if getattr(func, "id", None) != "_pick" and getattr(func, "attr", None) != "_pick":
                continue
            for arg in node.args[1:]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    picked[arg.value] = node.lineno
                elif isinstance(arg, ast.Starred) and isinstance(arg.value, ast.Name):
                    # `*ELEMENT1_NAMES` - the shared list, checked above
                    picked[arg.value.id] = node.lineno
    plain = {name: line for name, line in picked.items() if name.islower() and "_" in name}
    missing = {name: line for name, line in plain.items()
               if name not in declared and name.endswith(("1", "2", "name"))}
    assert not missing, (
        "these spellings are accepted by _pick and declared by no schema: "
        f"{missing} - add them to the tool's inputSchema, or read them from "
        "catia_mcp.arguments so both sides share one list")


def test_coincidence_no_longer_declares_elements_it_never_reads():
    """The reverse defect, found while fixing the first: a declared no-op."""
    from catia_mcp.server import CATIAMCPServer

    props = CATIAMCPServer()._schemas["catia_coincidence_constraint"]["properties"]
    assert set(props) == {"component1", "component2"}, (
        "element1/element2 were declared and never read - AddBiEltCst aligns two "
        "whole components and never touches an element")


# ────────────────────────────── 5. the order of the two checks


def test_an_unknown_key_is_reported_before_a_missing_one():
    """A typo is the cause; the missing argument is its symptom."""
    from catia_mcp.server import CATIAMCPServer

    reply = _envelope(CATIAMCPServer()._execute_tool(
        "catia_coincidence_constraint",
        {"component1": "A.1", "elem2_name": "Face.1"}))
    assert reply["code"] == "UNKNOWN_ARGUMENT"
    assert reply["data"]["unknown_arguments"] == ["elem2_name"]


# ────────────────────────────── 6. a path CATIA cannot write


def test_a_non_ascii_file_name_is_refused_before_catia_is_asked():
    r"""Measured limit, not a guess: a non-ASCII FOLDER works, a NAME does not.

    same folder, same length, only the name differing (V5-6R2020 B30, 2026-10-31):
        ascii_probe.CATPart        -> saved
        中文探针.CATPart           -> DISP_E_EXCEPTION, six attempts, modal dialog
        中文目录\probe.CATPart     -> saved
    """
    from catia_mcp.dialogs import save_as
    from catia_mcp.results import PathNotSupported

    with pytest.raises(PathNotSupported) as caught:
        save_as(None, r"C:/any/中文探针.CATPart")
    assert caught.value.code == "PATH_NOT_ASCII"
    assert caught.value.stage == "validate"
    assert caught.value.modified is False
    assert "rename the file afterwards" in str(caught.value)


def test_a_non_ascii_folder_is_not_refused():
    """The limit is the file name - refusing the folder too would be a new bug."""
    from catia_mcp.dialogs import save_as

    with pytest.raises(Exception) as caught:
        save_as(None, r"C:/中文目录/probe.CATPart")
    assert type(caught.value).__name__ != "PathNotSupported"


# ────────────────────────────── 7. a 285-step plan has to fit


def test_summary_returns_only_the_steps_that_failed():
    """A three-hundred-step reply is truncated; the two failures were inside it."""
    from catia_mcp.tools.planner import PlannerTools

    class _Module:
        def execute(self, name, arguments):
            if arguments.get("fail"):
                raise RuntimeError("boom")
            return "built"

    planner = PlannerTools(connection=None)
    planner.bind({"catia_pad": _Module()})
    planner._schemas = {"catia_pad": {"properties": {"height": {}, "fail": {}}}}
    reply = planner._run_plan({
        "steps": [
            {"tool": "catia_pad", "args": {"height": 1}},
            {"tool": "catia_pad", "args": {"height": 2, "fail": True}},
            {"tool": "catia_pad", "args": {"height": 3}},
        ],
        "measure": False, "stop_on_error": False, "summary": True,
    })
    data = reply["data"]
    assert data["executed"] == 3
    assert [step["index"] for step in data["steps"]] == [2]
    assert data["steps_omitted"] == 2
    assert data["failed_indices"] == [2]
    assert "summary=false" in data["summary_note"] or "without" in data["summary_note"]


def test_without_summary_every_step_is_still_listed():
    """The default must not change: the old behaviour is the detailed one."""
    from catia_mcp.tools.planner import PlannerTools

    class _Module:
        def execute(self, name, arguments):
            return "built"

    planner = PlannerTools(connection=None)
    planner.bind({"catia_pad": _Module()})
    planner._schemas = {"catia_pad": {"properties": {"height": {}}}}
    reply = planner._run_plan({
        "steps": [{"tool": "catia_pad", "args": {"height": i}} for i in range(3)],
        "measure": False,
    })
    assert len(reply["data"]["steps"]) == 3
    assert "steps_omitted" not in reply["data"]
