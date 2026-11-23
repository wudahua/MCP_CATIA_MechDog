"""2026-11-16 report: `catia_chamfer` reported `1/2 edge(s) done` as success.

The report's framing was exact - "1/2 done is a partial failure, but the tool
returns a plain prose string, so any caller that branches on status is fooled" -
and it was reported twice (0.3.126 and 0.3.138) before it was fixed. What is
pinned here:

  * `done < requested` raises, with the applied/rejected lists in `data` and
    `modified` telling "this failed" apart from "nothing happened";
  * the geometry that reproduces it is a plain box with two ADJACENT top edges -
    the 2026-11-13 experiment behind the old "not reproduced" ceiling entry
    always used OPPOSITE edges, which do not share a vertex (measured on B30
    2026-11-16: adjacent 1/2, four top edges 2/4, opposite 2/2);
  * the all-edges path still answers in prose, because there the caller did not
    name the set: a bevel of L on an edge shorter than 2L is arithmetic.

The reproduction itself is on the machine (scripts/b30_v0440_chamfer_repro.py and
b30_v0440_chamfer_acceptance.py 14/14); these tests are the shape of it, so a
later change that turns the failure back into a sentence fails here first.
"""
from __future__ import annotations

import inspect

import pytest

from catia_mcp.results import CodedError


class _Part:
    """Enough of a Part for _apply_fillets: one rebuild, no topology."""

    def __init__(self, fail: bool = False):
        self.updates = 0
        self.fail = fail

    def Update(self):
        self.updates += 1
        if self.fail:
            raise RuntimeError("(0, 'CATIPrtPart', '更新失败', None, 0, ...)")


def _tools():
    from catia_mcp.tools.part_design import PartDesignTools

    return PartDesignTools(connection=None)


def test_a_partial_batch_is_a_coded_failure_with_the_two_lists():
    tools = _tools()

    def flaky(ref):
        if ref == "bad":
            raise RuntimeError("(0, 'CATIAShapeFactory', '方法失败', ...)")

    with pytest.raises(CodedError) as caught:
        tools._apply_fillets(8.0, ["Edge.6", "Edge.3"], ["good", "bad"],
                            add=flaky, part=_Part())

    error = caught.value
    assert error.code == "PARTIAL_EDGES_FAILED"
    assert error.modified is True, "one bevel was applied and stayed"
    assert error.data["applied"] == ["Edge.6"]
    assert error.data["rejected"] == ["Edge.3"]
    assert error.data["done"] == 1 and error.data["requested"] == 2
    assert error.data["kind"] == "fillet"
    assert "Edge.3" in error.data["rejected_reasons"]


def test_nothing_applied_is_a_different_code_and_modified_false():
    """"This failed" and "nothing happened" are separate facts; the caller of a
    half-applied bevel needs both, and so does the caller of a no-op."""
    tools = _tools()

    def refuse(ref):
        raise RuntimeError("(0, 'CATIAShapeFactory', '方法失败', ...)")

    with pytest.raises(CodedError) as caught:
        tools._apply_fillets(8.0, ["Edge.1"], ["x"], add=refuse, part=_Part())

    assert caught.value.code == "EDGES_REJECTED"
    assert caught.value.modified is False
    assert caught.value.data["applied"] == []


def test_a_chamfer_is_reported_as_a_chamfer_not_as_a_fillet():
    tools = _tools()

    with pytest.raises(CodedError) as caught:
        tools._apply_fillets(8.0, ["Edge.1"], ["x"],
                             add=lambda ref: (_ for _ in ()).throw(RuntimeError("no")),
                             part=_Part(), label="Chamfer")

    assert caught.value.data["kind"] == "chamfer"
    assert "catia_chamfer" in " ".join(caught.value.next_actions)


def test_the_all_edges_path_now_reports_a_partial_result_as_a_failure():
    """0.3.160 answered ok:true here. 0.3.161 does not, and the old pin is withdrawn.

    This test used to assert ``strict=False`` in both all-edges methods, on the
    reasoning that "a short edge being refused is arithmetic, not a failed call".
    The reasoning was right about the CAUSE and wrong about the CALLER: the
    2026-11-23 report (P0-2) asked ``catia_fillet(radius=2)`` on a 12-edge box,
    got ``ok: true`` reading "Every edge ... enumerated", and had 4 of the 12
    built - a 229.6 mm3 difference it found by comparing volumes afterwards.
    Arithmetic that the caller cannot see is indistinguishable from a success
    they can act on. The batch now raises with ``PARTIAL_EDGES_FAILED`` (or
    ``EDGES_REJECTED`` when nothing built), carries ``modified`` = whether
    anything did, and the all-edges layer adds ``self_enumerated`` plus the
    measured tangency-rim recipe.
    """
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._fillet_all_edges)
    assert "strict=False" not in source
    assert "strict=True" in source
    assert "_as_enumerated" in source
    chamfer = inspect.getsource(PartDesignTools._chamfer_all_edges)
    assert "strict=False" not in chamfer
    assert "_as_enumerated" in chamfer


def test_the_enumeration_layer_says_the_set_was_chosen_by_the_tool():
    """`data.self_enumerated` is what separates "you named these" from "I did"."""
    from catia_mcp.results import CodedError as Refusal
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    original = Refusal("Fillet R2: 4/12 edge(s) done (...)",
                       code="PARTIAL_EDGES_FAILED", modified=True, stage="execute",
                       data={"requested": 12, "done": 4})
    enriched = tools._as_enumerated(original, 12, "fillet")
    assert enriched.data["self_enumerated"] is True
    assert enriched.data["done"] == 4              # the numbers survive
    assert enriched.code == "PARTIAL_EDGES_FAILED"
    assert enriched.modified is True
    assert "CHOSEN BY THE TOOL, NOT BY YOU" in str(enriched)
    assert "tangency" in " ".join(enriched.next_actions)
    assert "4/12" in str(enriched)


def test_the_anchor_report_travels_with_the_failure():
    """The anchor list says which coordinate landed on which edge, which is the
    only way to read a rejection when the edge numbering has just changed."""
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._edges_by_anchor)
    assert "context=report" in source

    tools = _tools()
    with pytest.raises(CodedError) as caught:
        tools._apply_fillets(8.0, ["Edge.6", "Edge.3"], ["good", "bad"],
                             add=lambda ref: (_ for _ in ()).throw(RuntimeError("no")),
                             part=_Part(),
                             context="Edge anchors: Anchor [27.5, 0.0, 55.8] -> Edge.6")

    assert "Anchor [27.5, 0.0, 55.8] -> Edge.6" in str(caught.value)


def test_the_batch_rebuild_failure_is_still_retried_one_edge_at_a_time():
    """The fix must not have removed the retry: three edges of one face fail as a
    batch and succeed one by one (measured 2026-10-23), and that retry is the only
    reason the adjacent-edge case gets 1 of 2 instead of 0 of 2."""
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._apply_fillets)
    assert "one-at-a-time retry" in source
    assert source.index("_remove_created(created)") < source.index(
        "one-at-a-time retry")


def test_the_ceiling_names_the_shared_vertex_not_a_four_lobe_column():
    from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS

    entry = next(item for item in _CAPABILITY_CEILINGS
                 if item["member"].startswith("two chamfers that share a VERTEX"))
    assert entry["status"] == "MEASURED_UNEXPLAINED"
    detail = entry["detail"]
    assert "REPRODUCED on B30 2026-11-16" in detail
    assert "ADJACENT" in detail
    assert "1/2" in detail and "2/4" in detail and "2/2" in detail
    assert "unexplained" in detail, "the mechanism is still unknown"
    assert "catia_list_edges" in detail, "a ceiling has to point somewhere"
