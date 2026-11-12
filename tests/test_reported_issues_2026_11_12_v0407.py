"""0.3.107: the all-edges fillet names edges instead of throwing a bare COM error.

The 2026-11-11 eighth report (§2.2) sent catia_fillet(radius=1.5) and (0.5) to a
129-face polyline part and got the same bare COM_ERROR twice - no edge, no
length, no next step - while the one-at-a-time machinery that would have named
them sat unused on the other branch.

Measured on B30 2026-11-12, and the reason the old branch is gone rather than
fixed: passing a Reference to the last feature into
AddNewSolidEdgeFilletWithConstantRadius fails AT CONSTRUCTION on every shape
tried - a 2-edge cylinder, a 12-edge rectangle, a 54-edge and a 246-edge
polyline - with "方法 AddNewSolidEdgeFilletWithConstantRadius 失败". Feature
reference versus whole body, propagation 0 versus 1: four cells, four failures.
The entry never worked.

These tests pin the replacement without needing CATIA:
  1  the edges are enumerated, and the specs carry index + faces + length
  2  at or below max_edges the batch path retries one at a time
  3  above max_edges it makes ONE attempt and says why it stopped
  4  max_edges is validated instead of being passed through
  5  a long reply is truncated, because 200 rejection lines is not information
  6  _fillet_one no longer builds a feature reference (the dead end)
  7  the schema stops promising "all edges of the last feature"
"""
from __future__ import annotations

import inspect

import pytest

from catia_mcp.results import ArgumentOutOfRange, ArgumentRejected
from catia_mcp.tools.part_design import PartDesignTools


class _Body:
    Name = "零件几何体"


class _Part:
    MainBody = _Body()


class _Conn:
    def ensure_connected(self) -> None:
        pass

    def get_active_part(self) -> _Part:
        return _Part()

    def refresh_display(self) -> None:
        pass


def _tool_with_edges(count: int, length: float = 9.6047) -> PartDesignTools:
    tools = PartDesignTools(connection=None)
    tools.conn = _Conn()
    tools._prepare_body = lambda part: _Body()          # type: ignore[assignment]
    tools._clear_selection = lambda: None               # type: ignore[assignment]
    tools._edge_table = lambda body: [                 # type: ignore[assignment]
        {"index": index, "name": f"Edge.{index}", "cell": object(),
         "nls": "", "bounded_by": ["Pad.1;2", "Sketch.1;1"],
         "length": length, "fingerprint": f"L{length}|Pad.1;2|Sketch.1;1"}
        for index in range(1, count + 1)
    ]
    return tools


def _capture(tools: PartDesignTools) -> list[dict]:
    seen: list[dict] = []

    def fake(radius, specs, propagation=0, sequential_retry=True):
        seen.append({"radius": radius, "specs": specs,
                     "propagation": propagation,
                     "sequential_retry": sequential_retry})
        return f"Fillet R{radius}: stub"

    tools._fillet_many = fake                           # type: ignore[assignment]
    return seen


def test_all_edges_enumerates_the_body_instead_of_the_feature():
    tools = _tool_with_edges(3)
    seen = _capture(tools)

    reply = tools._fillet_all_edges(1.5, 0, 200)

    assert len(seen) == 1
    specs = seen[0]["specs"]
    assert [spec["index"] for spec in specs] == [1, 2, 3]
    # The length and the faces travel with the index: an index alone is
    # renumbered by the first fillet that rebuilds.
    assert specs[0]["faces"] == ["Pad.1;2", "Sketch.1;1"]
    assert specs[0]["length_mm"] == pytest.approx(9.6047)
    assert "Every edge of '零件几何体' (3 edge(s))" in reply


def test_all_edges_retries_below_the_budget():
    tools = _tool_with_edges(12)
    seen = _capture(tools)

    reply = tools._fillet_all_edges(1.5, 0, 40)

    assert seen[0]["sequential_retry"] is True
    assert "max_edges" not in reply


def test_all_edges_makes_one_attempt_above_the_budget_and_says_so():
    tools = _tool_with_edges(246, length=1.5285)
    seen = _capture(tools)

    reply = tools._fillet_all_edges(1.5, 0, 200)

    assert seen[0]["sequential_retry"] is False
    assert "more than max_edges=200" in reply.lower()
    assert "NOT retried one at a time" in reply
    # The sentence that explains the failure has to carry numbers, not advice.
    assert "Shortest edge 1.5285 mm" in reply
    assert "246" in reply and "shorter than 2*R = 3 mm" in reply
    assert "NEXT:" in reply


def test_all_edges_reports_the_edges_it_measured_when_there_are_none():
    tools = _tool_with_edges(0)
    _capture(tools)

    with pytest.raises(Exception) as caught:
        tools._fillet_all_edges(1.5, 0, 200)
    assert "no edges" in str(caught.value).lower()


def test_max_edges_accepts_a_number_and_refuses_nonsense():
    tools = PartDesignTools(connection=None)
    assert tools._edge_budget({}) == tools._ALL_EDGES_RETRY_LIMIT
    assert tools._edge_budget({"max_edges": 250}) == 250
    with pytest.raises(ArgumentOutOfRange):
        tools._edge_budget({"max_edges": 0})
    with pytest.raises(ArgumentRejected):
        tools._edge_budget({"max_edges": "many"})


def test_a_long_reply_is_truncated_instead_of_printing_every_edge():
    tools = PartDesignTools(connection=None)
    names = [f"Edge.{index} (L9.6047|Pad.1;2|Sketch.1;1)" for index in range(1, 31)]

    report = tools._apply_fillets(
        1.5, names, [object()] * 30, add=lambda ref: None)

    assert "30/30" in report
    assert "and 18 more" in report
    assert len(report) < 1200


def test_a_long_rejection_list_is_counted_not_printed():
    tools = PartDesignTools(connection=None)
    names = [f"Edge.{index}" for index in range(1, 21)]

    def refuse(ref):
        raise RuntimeError("(-2147352567, '发生意外。', (0, 'CATIAShapeFactory', ...)")

    report = tools._apply_fillets(1.5, names, [object()] * 20, add=refuse)

    assert "0/20" in report
    assert "and 12 more edge(s) refused" in report
    assert len(report) < 1600


def test_fillet_one_no_longer_builds_a_feature_reference():
    tools = _tool_with_edges(0)

    with pytest.raises(ArgumentRejected) as caught:
        tools._fillet_one(1.5, None)
    assert "edge_name" in str(caught.value)
    source = inspect.getsource(PartDesignTools._fillet_one)
    assert "CreateReferenceFromObject(self._get_last_shape())" not in source


def test_the_schema_no_longer_promises_all_edges_of_the_last_feature():
    schema = next(tool for tool in PartDesignTools(connection=None).get_tool_definitions()
                  if tool["name"] == "catia_fillet")
    edge_name = schema["inputSchema"]["properties"]["edge_name"]["description"]
    assert "all edges of the last feature" not in edge_name
    assert "EVERY edge of the body" in edge_name
    assert schema["inputSchema"]["properties"]["max_edges"]["default"] == 200
    assert "enumerates" in schema["description"]


def test_chamfer_had_the_same_dead_end_and_gets_the_same_treatment():
    # Measured separately on B30, because AddNewChamfer is a different method:
    # feature reference and body reference both fail at construction on a
    # 2-edge cylinder and a 12-edge rectangle, with the volume not moving.
    tools = _tool_with_edges(3)
    seen: list[dict] = []

    def fake(length, angle, specs, sequential_retry=True):
        seen.append({"length": length, "angle": angle, "specs": specs,
                     "sequential_retry": sequential_retry})
        return "Chamfer 2: stub"

    tools._chamfer_many = fake                          # type: ignore[assignment]

    reply = tools._chamfer_all_edges(2.0, 45, 200)

    assert seen[0]["specs"][0]["index"] == 1
    assert seen[0]["sequential_retry"] is True
    assert "Every edge of '零件几何体' (3 edge(s))" in reply
    # The zero-budget case must stop, and say that it stopped.
    tools._chamfer_all_edges(2.0, 45, 1)
    assert seen[1]["sequential_retry"] is False


def test_chamfer_one_no_longer_builds_a_feature_reference():
    tools = _tool_with_edges(0)
    with pytest.raises(ArgumentRejected) as caught:
        tools._chamfer_one(2.0, 45, None)
    assert "edge_name" in str(caught.value)
    assert "CreateReferenceFromObject(self._get_last_shape())" not in \
        inspect.getsource(PartDesignTools._chamfer_one)
    schema = next(tool for tool in PartDesignTools(connection=None).get_tool_definitions()
                  if tool["name"] == "catia_chamfer")
    edge_name = schema["inputSchema"]["properties"]["edge_name"]["description"]
    assert "all edges of the last feature" not in edge_name
