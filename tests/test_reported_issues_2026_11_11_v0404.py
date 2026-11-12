"""0.3.104: a cut that went somewhere else must not be called a groove.

The 2026-11-11 report engraved three strokes that were buried inside the bottle
and the reply said, in full confidence, "a 0.4 mm deep groove ... HALF of it is
inside the material". The measurement agreed with itself all the way: 5.624 mm3
removed, 2.816 mm3 predicted, ratio 2.00, and the sentence that explains ratio 1
was printed anyway.

So the judgement moved out of a 400-line function into `_engrave_outcome`, which
is what these tests pin. None of them needs CATIA: the numbers are the report's.
"""
from __future__ import annotations

import inspect
import re

import pytest

from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS
from catia_mcp.tools.gsd import (
    _ENGRAVE_BURIED_RATIO,
    _ENGRAVE_FLOATING_RATIO,
    GSDTools,
    _engrave_outcome,
)


# ── the three bands ───────────────────────────────────────────────────────


def test_a_buried_cut_is_named_and_never_called_half_inside():
    """The report's own numbers: 5.624 removed against 2.816 predicted."""
    kind, verdict, tail = _engrave_outcome(5.624, 2.816, 0.4)
    assert kind == "buried"
    assert "BURIED" in verdict
    assert "2.00" in verdict
    assert "CAVITY UNDER THE SURFACE" in verdict
    assert "HALF" not in tail, (
        "the half-inside sentence describes ratio 1 and must not survive into "
        "the case where the whole tube was in the material")
    assert "0.4" in verdict, "it has to say how far to move the curve out"


def test_a_cut_that_removed_the_whole_tube_is_not_reported_as_a_groove():
    """The reply opens with 'Engraved ... a N mm deep groove', so the verdict has
    to be able to contradict that before the caller stops reading."""
    _kind, verdict, _tail = _engrave_outcome(753.982, 376.991, 2.0)
    assert verdict.index("BURIED") < len(verdict), "buried first, detail after"
    assert "NOT the radius" in verdict


def test_an_ordinary_cut_keeps_the_sentence_that_is_true_for_it():
    kind, verdict, tail = _engrave_outcome(376.991, 376.991, 2.0)
    assert kind == "ok"
    assert verdict == ""
    assert "HALF of it is inside the material" in tail
    assert "2 mm" in tail


def test_a_floating_curve_is_called_floating_and_not_given_a_depth():
    kind, verdict, tail = _engrave_outcome(1.0, 10.0, 0.4)
    assert kind == "floating"
    assert "FLOATING" in verdict
    assert "HALF" not in tail


def test_the_usable_band_between_the_two_ends_says_the_depth_varies():
    """0.69 is what the report measured after shifting the curve 0.6 mm out - a
    real, visible groove. It must not be called either failure."""
    kind, verdict, tail = _engrave_outcome(0.869, 1.257, 0.4)
    assert kind == "ok"
    assert "does not follow the surface perfectly" in verdict
    assert "HALF of it is inside the material" in tail


@pytest.mark.parametrize("removed,expected,kind", [
    (1.30 * 10.0, 10.0, "ok"),        # exactly the boundary is still usable
    (1.31 * 10.0, 10.0, "buried"),
    (0.50 * 10.0, 10.0, "ok"),
    (0.49 * 10.0, 10.0, "floating"),
])
def test_the_band_edges(removed, expected, kind) -> None:
    assert _engrave_outcome(removed, expected, 0.4)[0] == kind


def test_without_a_prediction_it_says_so_instead_of_claiming_the_depth():
    """A sketch line's length cannot be read, so a reply that says "the depth is
    the radius" would be claiming something nothing measured (measured on B30
    2026-11-11: `length` was None and the old code printed the sentence)."""
    kind, verdict, tail = _engrave_outcome(753.982, None, 2.0)
    assert kind == "unknown"
    assert "HALF" not in tail
    assert "could not measure" in tail


# ── the wiring ────────────────────────────────────────────────────────────


def test_engrave_judges_through_that_function():
    source = inspect.getsource(GSDTools._surface_engrave)
    assert "_engrave_outcome(" in source
    assert "HALF of it is inside the material" not in source, (
        "the sentence must live in the judged branch only, not in the caller")


def test_the_prediction_comes_from_the_cutter_volume_not_from_a_length():
    source = inspect.getsource(GSDTools._surface_engrave)
    assert "tube_volume / 2.0" in source


def test_the_reply_can_name_the_boolean_feature_to_undo():
    source = inspect.getsource(GSDTools._surface_engrave)
    assert "boolean_reply" in source
    assert "catia_delete_feature" in source


# ── the ceilings ──────────────────────────────────────────────────────────


def test_the_engraving_ceiling_records_both_ends_and_the_numbers():
    entry = next((item for item in _CAPABILITY_CEILINGS
                  if "engraving" in item["member"]), None)
    assert entry is not None
    detail = entry["detail"]
    for number in ("5.624", "2.816", "2.00", "0.5", "1.3"):
        assert number in detail, number
    assert "BURIED" in detail and "FLOATING" in detail
    assert "CUTTER'S measured volume" in detail


def test_the_projection_ceiling_corrects_parallel_and_curve_supports():
    entry = next((item for item in _CAPABILITY_CEILINGS
                  if "first projection" in item["member"]), None)
    assert entry is not None
    detail = entry["detail"]
    assert "13.6" in detail and "15.1" in detail and "16.0" in detail
    # 2026-11-12, four cells on one reopened document: the face the sketch lies
    # on, that face extracted as a SURFACE, and a PARALLEL plane all took the
    # projection at 100 %; only the CURVE support was refused. The sentence this
    # replaces had both halves backwards - "parallel planes fail" and "a curve
    # extracted from that face is accepted".
    assert "PARALLEL to the sketch 100 %" in detail
    assert "CURVE refused" in detail
    # The old sentence is now quoted as the thing that did NOT reproduce, so the
    # phrase itself may appear - what must be gone is the RULE it stated.
    assert "the rule is 'parallel planes fail" not in detail
    assert "not 'non-planar is required'" not in detail


def test_the_project_failure_no_longer_recommends_translate():
    source = inspect.getsource(GSDTools._project)
    advice = re.findall(r'next_actions=\[(.*?)\n\s*\]', source, re.S)
    assert advice, "PROJECT_FAILED lost its next_actions"
    joined = " ".join(advice)
    assert "catia_gsd_translate does NOT help" in joined
    assert "the element has to not be flat" not in joined
