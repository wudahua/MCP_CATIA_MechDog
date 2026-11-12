"""0.3.111: the short-edge count is arithmetic, so it comes before the attempt.

The eighth report's own part (SiphonBottle.CATPart) was finally run end to end on
B30 2026-11-12. It has 2 features, 129 faces, 67 278.8 mm3 - and 381 EDGES, three
times the face count the report quoted. Measured:

    R1.5, default max_edges=200   0/381, 186 s for the single batch attempt
    R0.25, max_edges=400        101/381, 458 s, 103 shapes, -3.73 mm3

Two things were wrong with the reply rather than the geometry:

  * "166 of 381 are shorter than 2*R" arrived AFTER the 186 s attempt, although
    it is pure arithmetic over lengths the call already holds;
  * the cost estimate said 0.59 s per edge, which is the cost of an edge that
    TAKES - on a part where most refuse, 381 edges came to 458 s.

These tests pin the order and the numbers without needing CATIA.
"""
from __future__ import annotations

from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS
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


def _tools(monkeypatch, lengths):
    tools = PartDesignTools(connection=_Conn())
    tools.conn = _Conn()
    monkeypatch.setattr(tools, "_prepare_body", lambda part: _Body())
    table = [{"index": index, "bounded_by": ["A", "B"], "length": value}
             for index, value in enumerate(lengths, start=1)]
    monkeypatch.setattr(tools, "_edge_table", lambda body: table)
    seen = {}

    def fake_many(radius, specs, propagation, sequential_retry=True):
        seen["sequential"] = sequential_retry
        seen["count"] = len(specs)
        return f"0/{len(specs)} edge(s) done."

    monkeypatch.setattr(tools, "_fillet_many", fake_many)
    return tools, seen


def test_the_short_edge_count_is_reported_before_the_max_edges_paragraph(monkeypatch):
    # 381 edges, 166 of them shorter than 2*R at R1.5 — the report's own numbers.
    lengths = [1.0] * 166 + [5.0] * 215
    tools, seen = _tools(monkeypatch, lengths)
    reply = tools._fillet_all_edges(1.5, 0, 200)
    assert seen["sequential"] is False
    assert "166 of 381" in reply
    assert reply.index("shorter than") < reply.index("max_edges"), (
        "the arithmetic has to be readable before the paragraph about the budget")


def test_the_count_is_there_even_when_the_edges_are_retried_one_at_a_time(monkeypatch):
    # Below max_edges the old code never mentioned short edges at all.
    lengths = [1.0] * 40 + [5.0] * 60
    tools, seen = _tools(monkeypatch, lengths)
    reply = tools._fillet_all_edges(1.5, 0, 200)
    assert seen["sequential"] is True
    assert "40 of 100" in reply
    assert "2*R = 3" in reply


def test_the_cost_of_the_batch_attempt_is_stated(monkeypatch):
    lengths = [1.0] * 166 + [5.0] * 215
    tools, _ = _tools(monkeypatch, lengths)
    reply = tools._fillet_all_edges(1.5, 0, 200)
    assert "186 s" in reply
    assert "0 fillets" in reply


def test_the_old_estimate_of_0_59_s_is_gone_from_the_fillet_paths():
    import inspect

    source = inspect.getsource(PartDesignTools)
    # 458 s / 381 edges is 1.2 s per edge once refusals are counted; quoting the
    # cost of a successful edge alone understates a part like this by 2x.
    assert "about 0.59 s per edge - 246 edges take 145 s" not in source
    assert "measured: about 0.59 s per edge), and this call was" not in source
    assert "381 edges of a real part took 458 s" in source


def test_the_chamfer_path_warns_about_corners_without_claiming_certainty(monkeypatch):
    tools = PartDesignTools(connection=_Conn())
    tools.conn = _Conn()
    monkeypatch.setattr(tools, "_prepare_body", lambda part: _Body())
    table = [{"index": index, "bounded_by": ["A", "B"], "length": 0.4}
             for index in range(1, 21)]
    monkeypatch.setattr(tools, "_edge_table", lambda body: table)
    monkeypatch.setattr(tools, "_chamfer_many",
                        lambda length, angle, specs, sequential_retry=True:
                        f"0/{len(specs)} edge(s) done.")
    reply = tools._chamfer_all_edges(1.0, 45.0, 200)
    assert "shorter than 2*L = 2" in reply
    assert "expect those to be refused" in reply
    # A bevel of L fits on an edge of length L; only the NEIGHBOUR needs 2*L, so
    # the reply must not say these edges cannot be bevelled at all.
    assert "no chamfer" not in reply.lower()


def test_the_ceilings_carry_the_real_part_and_both_radii():
    entry = next(item for item in _CAPABILITY_CEILINGS
                 if item["member"] == "rounding every edge of one solid at one radius")
    detail = entry["detail"]
    assert "381 EDGES" in detail
    assert "0 of 381" in detail
    assert "101 of 381" in detail
    assert "458 s" in detail
    assert "67278.8" in detail
