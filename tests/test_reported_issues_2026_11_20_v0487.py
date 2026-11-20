"""The 2026-11-20 re-test of 0.3.152: P0 refused by declaration, P2 without the
sample, P6 with one story.

The report's sharpest sentence is the one worth pinning: "工具仍可被调到并返回
COM_ERROR" - a tool that describes itself as unavailable and then goes to CATIA
anyway teaches the caller nothing at the moment they need it. So the refusal is a
decision this server makes, before the first COM call, and `force: true` is the
documented way back in.

No CATIA here: the guard, the schemas and the description are all data.
"""
from __future__ import annotations

import json

import pytest

from catia_mcp.results import CodedError
from catia_mcp.tools.assembly import AssemblyTools
from catia_mcp.tools.diagnostics import DiagnosticsTools
from catia_mcp.tools.measurement import MeasurementTools


# ══ P0: the declaration comes first, and the COM call does not happen ═════
@pytest.mark.parametrize("tool,args", [
    ("catia_fix_constraint", {"component_name": "C.1"}),
    ("catia_coincidence_constraint", {"component1": "C.1", "component2": "C.2"}),
    ("catia_offset_constraint", {"component1": "C.1", "component2": "C.2",
                                 "offset": 5}),
    ("catia_angle_constraint", {"component1": "C.1", "component2": "C.2",
                                "angle": 30}),
])
def test_the_mate_tools_refuse_before_catia(tool, args) -> None:
    tools = AssemblyTools(connection=None)
    with pytest.raises(CodedError) as refused:
        tools.execute(tool, args)
    error = refused.value
    assert error.code == "UNSUPPORTED_CAPABILITY"
    assert getattr(error, "stage", "") == "validate"
    assert getattr(error, "modified", None) is False
    text = str(error)
    assert "NOT AVAILABLE ON THIS BUILD" in text
    assert "nothing was changed" in text
    for said in ("Selection.Item(1).Reference", "GetWorkbenchId", "SKETCH",
                 "unknown command", "catia_probe_assembly_constraints"):
        assert said in text, said
    assert any("force: true" in action for action in error.next_actions)


def test_force_is_the_way_through_to_the_old_path() -> None:
    """`force: true` must not raise at the guard - CATIA is reached instead."""
    tools = AssemblyTools(connection=None)
    tools._refuse_constraint("Fix", {"force": True})   # no raise
    with pytest.raises(CodedError):
        tools._refuse_constraint("Fix", {})


def test_every_mate_schema_declares_force() -> None:
    definitions = {d["name"]: d for d in
                   AssemblyTools(connection=None).get_tool_definitions()}
    for name in AssemblyTools.CONSTRAINT_TOOLS:
        properties = definitions[name]["inputSchema"]["properties"]
        assert properties["force"]["default"] is False, name
        assert "refuses before CATIA" in properties["force"]["description"], name


def test_the_description_no_longer_calls_itself_available() -> None:
    definitions = {d["name"]: d for d in
                   AssemblyTools(connection=None).get_tool_definitions()}
    for name in AssemblyTools.CONSTRAINT_TOOLS:
        text = definitions[name]["description"]
        assert "UNAVAILABLE_ON_THIS_BUILD" in text, name
        assert "force" in text, name


# ══ P0: the one-call probe ════════════════════════════════════════════════
def test_the_probe_is_registered_and_costs_nothing_to_run() -> None:
    definitions = {d["name"]: d for d in
                   DiagnosticsTools(connection=None).get_tool_definitions()}
    probe = definitions["catia_probe_assembly_constraints"]
    assert probe["inputSchema"]["properties"] == {}
    text = probe["description"]
    for said in ("verdict", "THROWAWAY", "model_untouched", "sketch",
                 "message box"):
        assert said in text, said


def test_the_probe_has_to_open_a_session() -> None:
    """It measures CATIA, so it must not be in NO_SESSION."""
    assert "catia_probe_assembly_constraints" not in DiagnosticsTools.NO_SESSION


def test_the_probe_refuses_nothing_and_never_dies_on_a_com_error(monkeypatch) -> None:
    """Every step is recorded, and a broken session still gets a verdict."""
    tools = DiagnosticsTools(connection=None)

    class _NoApplication:
        def __getattr__(self, name):
            raise RuntimeError("no session")

    monkeypatch.setattr(tools, "conn", type("C", (), {
        "app": _NoApplication(),
        "active_document": None,
        "ensure_connected": lambda self: None,
    })())
    reply = tools._probe_assembly_constraints()
    assert reply["ok"] is True
    assert reply["verdict"] in {"INCONCLUSIVE", "ENVIRONMENT_LIMITED", "AVAILABLE"}
    assert reply["data"]["model_untouched"] is True
    assert reply["data"]["steps"], "a probe with no steps would answer nothing"


# ══ P2: a part with too many vertices is not answered with a sample ═══════
def test_allow_truncated_is_declared_and_off_by_default() -> None:
    schema = next(d for d in MeasurementTools(connection=None).get_tool_definitions()
                  if d["name"] == "catia_get_bounding_box")
    truncated = schema["inputSchema"]["properties"]["allow_truncated"]
    assert truncated["default"] is False
    assert "vertex_sweep_truncated" in truncated["description"]
    assert "200" in truncated["description"]


def test_the_vertex_reader_takes_a_limit_and_a_way_to_sample(monkeypatch) -> None:
    import inspect

    signature = inspect.signature(MeasurementTools._vertex_points)
    assert signature.parameters["limit"].default == 200
    assert signature.parameters["allow_truncated"].default is False


# ══ P6: one story about `auto` on an assembly ════════════════════════════
def test_the_auto_route_is_described_the_way_the_code_behaves() -> None:
    """`force_mesh = method in ('mesh', 'auto')` - the description must agree."""
    schema = next(d for d in MeasurementTools(connection=None).get_tool_definitions()
                  if d["name"] == "catia_get_bounding_box")
    description = schema["inputSchema"]["properties"]["method"]["description"]
    assert "auto is the MESH route on an assembly" in description
    # The stale sentence claimed auto reads vertices on an assembly; if it ever
    # comes back, this fails.
    assert "auto reads each component's VERTICES" not in description
    assert "force_mesh = method in ('mesh', 'auto')" in description

    source = __import__("inspect").getsource(MeasurementTools._assembly_box)
    assert '"force_mesh": method in ("mesh", "auto")' in source
