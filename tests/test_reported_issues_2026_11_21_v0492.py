"""0.3.156: the 2026-11-21 report's first pass - four items, all measured.

P0-1 two-distance chamfer. The tool only offered length+angle, and the report had
to cut two triangle sketches to get a 25/20 bevel. `AddNewChamfer`'s third
argument has always selected catLengthLength (mode 0), where the sixth argument
is a second LENGTH - measured on a 100 mm cube: 25/20 removes exactly
25*20/2*100 = 25 000 mm3, against 11 374 mm3 for 25 mm at 20 deg. The capability
was there; only the parameter was missing.

P1-14/P1-16 arc residuals. The refusal printed `1.4e-9 mm` as `0.000000` while
saying the arc was "off its own circle" - a reply that asserts a disagreement and
then shows zero. And the tolerance was a FLAT 1e-9 mm, which on R40 asks for
2.5e-11 of relative accuracy: past what a double holds, so a p1/p2 computed
outside CATIA failed on arithmetic. It now scales with the radius and the gap is
written in scientific notation when it is small.

P1-17 a Part-vs-Product refusal that names the remedy.
P1-18 `catia_get_inertia` refused `scope` while its own reply published it.

No CATIA here: the arithmetic, the schemas and the messages are all data.
"""
from __future__ import annotations

import pytest

from catia_mcp.results import ArgumentRejected, PreconditionRejected
from catia_mcp.tools.measurement import MeasurementTools
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.sketcher import (_fmt_gap, _fmt_gap_signed,
                                     SketcherTools)


# ══ P0-1: the two-distance bevel is a parameter, not a new capability ═════
def test_the_chamfer_schema_offers_a_second_distance() -> None:
    definitions = {d["name"]: d for d in
                   PartDesignTools(connection=None).get_tool_definitions()}
    schema = definitions["catia_chamfer"]["inputSchema"]
    assert "length2" in schema["properties"]
    assert schema["properties"]["length2"]["type"] == "number"
    # Optional: length+angle remains the default call.
    assert schema["required"] == ["length"]


def test_the_description_says_which_face_gets_which_distance_is_not_the_callers() -> None:
    """The measurement proved the volume, not the face assignment. Saying more
    than that would be a claim the probe never tested."""
    definitions = {d["name"]: d for d in
                   PartDesignTools(connection=None).get_tool_definitions()}
    text = definitions["catia_chamfer"]["description"]
    assert "TWO-DISTANCE" in text
    assert "25 000 mm3" in text or "25 000" in text
    assert "11 374 mm3" in text
    assert "follows the edge's own" in text
    # 0.3.159: "swap the two values" became a parameter, and the description
    # now also states the two things this build cannot read back (the lengths)
    # so a caller does not take the reply for a confirmation (report P1-21).
    assert "swap_distances=true" in text
    assert "Chamfer.Length" in text


def test_a_non_positive_second_distance_is_refused_before_catia() -> None:
    tools = PartDesignTools(connection=None)
    with pytest.raises(ArgumentRejected) as refused:
        tools._chamfer({"length": 25, "length2": 0, "edge_name": "Edge.1"})
    assert "Nothing was created" in str(refused.value)


def test_length2_selects_catLengthLength_and_angle_stays_mode_one() -> None:
    """The mode is the whole point: mode 0 reads the 6th argument as a LENGTH."""
    import inspect

    source = inspect.getsource(PartDesignTools)
    assert "(0, length2) if length2 else (1, angle)" in source
    # Three call sites: one edge, the batch, and the anchor path.
    assert source.count("(0, length2) if length2 else (1, angle)") == 3


# ══ P1-14 / P1-16: a residual you can read, at a tolerance that scales ════
def test_a_small_residual_is_written_in_scientific_notation() -> None:
    assert _fmt_gap(1.4e-9) == "1.400e-09"
    assert _fmt_gap(4e-4) == "0.000400"
    assert _fmt_gap(0.0) == "0"
    assert _fmt_gap_signed(-4e-4) == "-0.000400"
    assert _fmt_gap_signed(4e-4) == "+0.000400"


def _arc(radius: float, first: float, second: float) -> dict:
    return {"type": "arc", "center": [0.0, 0.0], "radius": radius,
            "p1": [radius + first, 0.0], "p2": [0.0, radius + second]}


def test_a_nanometre_gap_passes_on_a_small_radius() -> None:
    """The report's tangent fillet: R6, 1.4e-9 mm off. It used to be refused."""
    tools = SketcherTools.__new__(SketcherTools)
    tools._plan_segment(1, _arc(6.0, -1.4e-9, 1.4e-9), True)   # no raise


def test_the_same_gap_passes_on_a_large_radius() -> None:
    """A flat 1e-9 mm asked for 2.5e-11 relative accuracy at R40."""
    tools = SketcherTools.__new__(SketcherTools)
    tools._plan_segment(1, _arc(40.0, -1.4e-9, 1.4e-9), True)


def test_a_real_gap_is_still_refused_and_the_reply_states_the_tolerance() -> None:
    """4e-4 mm is the report's P1-14: geometry, not floating-point noise."""
    tools = SketcherTools.__new__(SketcherTools)
    with pytest.raises(ArgumentRejected) as refused:
        tools._plan_segment(1, _arc(40.0, -4e-4, 4e-4), True)
    text = str(refused.value)
    assert "0.000400" in text
    # The threshold is named, so the caller knows how close is close enough.
    assert "4.000e-08" in text
    assert "1e-9 x max(1 mm, R)" in text


# ══ P1-17: an assembly is not a Part, and the refusal says what to do ═════
def test_a_product_refusal_names_the_remedy() -> None:
    class _Product:
        Name = "Assem2_TXT.CATProduct"

    class _Conn:
        @staticmethod
        def _dynamic(doc):
            return doc

    from catia_mcp.connection import CATIAConnection

    with pytest.raises(PreconditionRejected) as refused:
        CATIAConnection._interface(_Conn(), _Product(), "Part")
    text = str(refused.value)
    assert "catia_activate_document" in text
    assert "assembly" in text.lower()


# ══ P1-18: the parameter its own reply published ══════════════════════════
def test_inertia_accepts_the_words_its_own_reply_uses() -> None:
    """The no-argument route answers 'active_body', the document= route
    'main_body'. Both had to become legal - accepting only one would have
    re-created the same mismatch with the other."""
    definitions = {d["name"]: d for d in
                   MeasurementTools(connection=None).get_tool_definitions()}
    scope = definitions["catia_get_inertia"]["inputSchema"]["properties"]["scope"]
    assert set(scope["enum"]) == {"main_body", "active_body"}
    assert "echoes" in scope["description"]


def test_any_other_scope_is_refused_with_the_reason() -> None:
    tools = MeasurementTools(connection=None)
    with pytest.raises(ArgumentRejected) as refused:
        tools._get_inertia(None, None, "face")
    text = str(refused.value)
    assert "main_body" in text and "active_body" in text
    assert "Nothing was measured" in text
