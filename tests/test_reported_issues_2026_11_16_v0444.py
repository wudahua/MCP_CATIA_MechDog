"""2026-11-16 round 15: two credibility defects in `catia_surface_probe`.

P1-A  `calibration.ok` was null on EVERY `direction` call, with the reason "the
      first probe gave no surface point" - which was not even the right reason.
      `along_rows` rows carry no `normal`, and `calibration_row` needs one, so the
      calibration promised by the tool description never ran in the mode the
      report was using. Along mode now has its own: start the same ray 0.5 mm
      earlier, where the expectation is EXACT - the hit cannot move and the offset
      must grow by exactly 0.5.

P1-B  a start point inside the solid produced `offset 0, converged true` beside a
      2.4 mm gap. Reproduced on B30 2026-11-16 and the mechanism is narrower than
      the report's arithmetic: convergence was tested on the AXIAL COMPONENT of
      the gap, so a foot that is displaced tangentially stops the iteration and is
      reported as a hit. It is now the whole gap, and rows whose query point is
      inside the part's solid say so with `inside_solid`.

What is pinned here, all without CATIA: the convergence rule, both calibration
modes, the inside flag, and the wording the tool now promises.
"""
from __future__ import annotations

import pytest

from catia_mcp import fidelity
from catia_mcp.results import ArgumentRejected


# ── surfaces to measure against, all analytic ──────────────────────────────
def plane_z10(where):
    return [float(where[0]), float(where[1]), 10.0], abs(float(where[2]) - 10.0)


def sideways_foot(where):
    """A foot 2.4 mm away at RIGHT ANGLES to a +Z axis, whatever is asked.

    This is the round-15 shape: dot(gap, axis) = 0, so the old rule stopped on the
    first iteration and called it converged while 2.4 mm of gap remained.
    """
    point = [float(where[0]) - 2.4, float(where[1]), 10.0]
    return point, 2.4


# ── P1-B: the convergence rule ─────────────────────────────────────────────
def test_a_tangential_remainder_is_not_a_hit():
    rows = fidelity.along_rows([[0.0, 0.0, 8.0]], [0.0, 0.0, 1.0], sideways_foot,
                               tolerance=0.001, max_iterations=6)
    row = rows[0]
    assert row["converged"] is False, (
        "the axial component vanished but the point is 2.4 mm off the surface")
    assert row["residual_mm"] == 0.0, "the step really did stop moving"
    assert row["gap_mm"] == pytest.approx(2.4)


def test_a_real_hit_still_converges_and_carries_its_gap():
    rows = fidelity.along_rows([[0.0, 0.0, 4.0]], [0.0, 0.0, 1.0], plane_z10,
                               tolerance=1e-6)
    row = rows[0]
    assert row["converged"] is True
    assert row["offset_mm"] == pytest.approx(6.0)
    assert row["gap_mm"] == pytest.approx(0.0)


def test_the_axis_is_a_line_so_a_backwards_hit_is_negative():
    """Measured on B30 2026-11-16: from (20,15,30) along +Z at a face at z=20 the
    offset is -10.0 and the hit is real."""
    rows = fidelity.along_rows([[0.0, 0.0, 14.0]], [0.0, 0.0, 1.0], plane_z10,
                               tolerance=1e-6)
    assert rows[0]["offset_mm"] == pytest.approx(-4.0)
    assert rows[0]["converged"] is True


# ── P1-A: along-mode calibration ───────────────────────────────────────────
def _converged_row(where=(0.0, 0.0, 4.0)):
    return fidelity.along_rows([list(where)], [0.0, 0.0, 1.0], plane_z10,
                               tolerance=1e-6)[0]


def test_along_calibration_passes_on_an_exact_expectation():
    """The ray started 0.5 mm earlier meets the same point, so the offset must
    grow by exactly the stencil - no curvature assumption anywhere."""
    check = fidelity.calibration_along(_converged_row(), [0.0, 0.0, 1.0],
                                       plane_z10, 1e-6, 12, stencil_mm=0.5)
    assert check["ok"] is True
    assert check["mode"] == "along"
    assert check["read_mm"] == pytest.approx(0.5)
    assert check["foot_drift_mm"] == pytest.approx(0.0)


def test_along_calibration_fails_when_the_answer_does_not_survive_a_moved_origin():
    """A reading that always says "you are on it" is not a height: moving the
    origin back changes nothing, so the known step cannot be recovered."""

    def always_on(where):
        return [float(where[0]), float(where[1]), float(where[2])], 0.0

    row = fidelity.along_rows([[0.0, 0.0, 5.0]], [0.0, 0.0, 1.0], always_on,
                              tolerance=1e-6)[0]
    check = fidelity.calibration_along(row, [0.0, 0.0, 1.0], always_on, 1e-6, 12)
    assert check["ok"] is False


def test_along_calibration_declines_with_a_reason_when_there_is_no_hit():
    row = fidelity.along_rows([[0.0, 0.0, 8.0]], [0.0, 0.0, 1.0], sideways_foot,
                              tolerance=0.001, max_iterations=4)[0]
    check = fidelity.calibration_along(row, [0.0, 0.0, 1.0], sideways_foot,
                                       0.001, 4)
    assert check["ok"] is None
    assert "did not converge" in check["reason"]


def test_nearest_calibration_says_which_thing_was_missing():
    """The old reason claimed there was no surface point even when there was one
    and only the normal was missing - a message that sends the reader to the
    wrong place."""
    no_normal = fidelity.calibration_row({"surface_point": [0, 0, 10], "normal": None},
                                         plane_z10)
    assert no_normal["ok"] is None
    assert "no normal" in no_normal["reason"]
    no_point = fidelity.calibration_row({"surface_point": None}, plane_z10)
    assert no_point["ok"] is None
    assert "no surface point" in no_point["reason"]


# ── the inside flag ────────────────────────────────────────────────────────
def test_an_inside_query_against_a_face_is_marked():
    """Round 15's case: a start point inside the bottle probing Face.1. The
    distance to that face is a real number, so nothing else in the row would
    tell the caller the station sits in the material."""
    rows = fidelity.nearest_rows([[0.0, 0.0, 5.0]], plane_z10,
                                 inside=lambda where: True)
    assert rows[0]["inside_solid"] is True
    assert rows[0]["distance_mm"] == 5.0


def test_a_point_on_the_target_is_not_labelled_inside():
    """"On the face" is inside the solid's closure too; the honest answer there
    is "cannot tell", not "inside"."""
    rows = fidelity.nearest_rows([[0.0, 0.0, 10.0]], plane_z10,
                                 inside=lambda where: True)
    assert rows[0]["on_surface_or_inside"] is True
    assert rows[0]["inside_solid"] is None


def test_outside_is_reported_as_outside():
    rows = fidelity.nearest_rows([[0.0, 0.0, 12.0]], plane_z10,
                                 inside=lambda where: False)
    assert rows[0]["inside_solid"] is False


def test_without_an_inside_check_the_field_is_absent_not_false():
    rows = fidelity.nearest_rows([[0.0, 0.0, 12.0]], plane_z10)
    assert "inside_solid" not in rows[0]


def test_along_rows_mark_the_query_point_not_the_hit():
    rows = fidelity.along_rows([[0.0, 0.0, 4.0]], [0.0, 0.0, 1.0], plane_z10,
                               tolerance=1e-6, inside=lambda where: True)
    assert rows[0]["inside_solid"] is True
    assert rows[0]["converged"] is True, "a real hit is still a real hit"


def test_an_unanswerable_inside_check_is_null_rather_than_a_guess():
    def broken(where):
        raise RuntimeError("方法 GetMinimumDistance 失败")

    rows = fidelity.nearest_rows([[0.0, 0.0, 12.0]], plane_z10, inside=broken)
    assert rows[0]["inside_solid"] is None
    assert rows[0]["distance_mm"] == 2.0


def test_a_zero_axis_is_still_refused_with_a_code():
    with pytest.raises(ArgumentRejected) as caught:
        fidelity.along_rows([[0.0, 0.0, 0.0]], [0.0, 0.0, 0.0], plane_z10)
    assert caught.value.code == "BAD_ARGUMENT"


# ── what the tool now promises ─────────────────────────────────────────────
def test_the_description_stops_promising_one_calibration_for_two_modes():
    from catia_mcp.tools.gsd import GSDTools

    tools = {tool["name"]: tool for tool in
             GSDTools.get_tool_definitions(GSDTools.__new__(GSDTools))}
    text = tools["catia_surface_probe"]["description"]
    assert "in the mode it is in" in text
    assert "must grow by exactly 0.5000 mm" in text
    assert "in EVERY direction, not just along the axis" in text
    assert "inside_solid" in text
    assert "BEHIND the start point" in text
    # the third defect this round turned up, which neither report could see:
    # "Face.N" resolved to a tangent-propagated Extract of the whole shell
    assert "5200.000" in text and "1200.000" in text


def test_the_deviation_tool_no_longer_implies_a_face_is_one_face():
    from catia_mcp.tools.gsd import GSDTools

    tools = {tool["name"]: tool for tool in
             GSDTools.get_tool_definitions(GSDTools.__new__(GSDTools))}
    text = tools["catia_measure_deviation"]["inputSchema"]["properties"]["surface"][
        "description"]
    assert "EXTRACT" in text
    assert "5200.000" in text
    assert "distance to THIS face" in text
