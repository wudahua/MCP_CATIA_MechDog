"""0.3.70: the eleven raises that lived outside `tools/`.

0.3.69 emptied the tools package: 169 sites, every one with a code. The same
scan, run over `catia_mcp/` instead of `catia_mcp/tools/`, still found eleven -
and they were the ones a caller is least equipped to interpret:

    "Duplicate tool: X"                      a start-up defect in this package
    "unknown guarded operation"              same
    "route= is not one of the N routes"      same
    "unreachable"                            same
    "Active document has no Part"            not a defect, but not an argument
    "SaveAs failed after 6 attempts"         CATIA ran and refused

Reported as `COM_ERROR` / `execute` / `modified:null`, the first four sent the
caller to inspect a model for a bug in this package, and the fifth looked like a
communication failure.

The fifth stage, `internal`, is what stops the last of it: it is neither
"the argument is wrong" nor "CATIA was asked" - it is "this package contradicted
itself, and nothing was written".
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from catia_mcp.results import CodedError, InternalError, PreconditionRejected


def test_an_internal_error_is_its_own_stage():
    assert InternalError.code == "INTERNAL_ERROR"
    assert InternalError.stage == "internal"
    assert InternalError.modified is False
    assert InternalError.next_actions, "the caller still needs to know who to tell"
    assert issubclass(InternalError, ValueError), (
        "guard.run('format_hard_disk') and describe(route='probe') have been "
        "asserted with pytest.raises(ValueError) for several releases")


def test_an_unknown_guarded_operation_names_the_defect():
    from catia_mcp import guard

    with pytest.raises(InternalError) as caught:
        guard.run("format_hard_disk", {}, seconds=1)
    assert caught.value.code == "INTERNAL_ERROR"
    assert "report" in json.dumps(caught.value.next_actions).lower()


def test_an_invented_route_is_internal_not_a_com_failure():
    from catia_mcp.regime import SessionBooks

    with pytest.raises(InternalError) as caught:
        SessionBooks().describe(book="topology", route="probe", measured=True,
                                parts_total=1, session_first=1)
    assert caught.value.stage == "internal"


def test_the_envelope_of_an_internal_error_has_no_model_advice(monkeypatch):
    """The whole point: nothing to inspect, nothing to repair."""
    from catia_mcp.connection import CATIAConnection
    from catia_mcp.server import CATIAMCPServer

    # A bug in this package is a bug whether or not CATIA is running. Without
    # this the server died at the connect stage first and the test asserted the
    # envelope of the WRONG failure - it passed only where CATIA was open
    # (found on CI 2026-11-14, where all 141 runs had been failing).
    monkeypatch.setattr(CATIAConnection, "ensure_connected",
                        lambda self, *a, **k: None)

    class _Boom:
        def get_tool_definitions(self):
            return [{"name": "catia_probe_internal",
                     "description": "raises a server defect",
                     "inputSchema": {"type": "object", "properties": {}}}]

        def execute(self, tool_name, arguments):
            raise InternalError("Duplicate tool: catia_probe_internal")

    server = CATIAMCPServer()
    server._tool_router["catia_probe_internal"] = _Boom()
    server._schemas["catia_probe_internal"] = {"type": "object", "properties": {}}
    reply = json.loads(server._execute_tool("catia_probe_internal", {}).content[0].text)
    assert reply["ok"] is False
    assert reply["code"] == "INTERNAL_ERROR"
    assert reply["data"]["failure_stage"] == "internal"
    assert reply["effects"]["modified"] is False
    assert "recovery" not in reply, "a bug in this package is not a model to repair"
    assert "inspect the CATIA model" not in reply["message"]
    assert reply["data"].get("next_actions")


def test_a_missing_pywin32_is_a_connect_stage_refusal(monkeypatch):
    from catia_mcp import connection

    monkeypatch.setattr(connection, "HAS_COM", False)
    with pytest.raises(CodedError) as caught:
        connection.CATIAConnection().connect()
    assert caught.value.code == "DEPENDENCY_MISSING"
    assert caught.value.stage == "connect"
    assert caught.value.modified is False


def test_a_drawing_has_no_part_behind_it():
    """The refusal that used to be a RuntimeError, now a state, not a failure."""
    from catia_mcp.connection import CATIAConnection

    conn = CATIAConnection()
    with pytest.raises(PreconditionRejected) as caught:
        conn._interface(SimpleNamespace(Name="Drawing1.CATDrawing"), "Part")
    assert caught.value.code == "PRECONDITION_FAILED"
    assert "Part document" in str(caught.value)
