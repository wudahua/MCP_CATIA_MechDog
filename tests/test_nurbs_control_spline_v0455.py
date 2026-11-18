"""2026-11-17 report, item 1: control points, not interpolation points.

The report's P05 row is the anchor for everything here: the same three control
points drawn as INTERPOLATION points gave 10 269 199.4647 mm3 against a source of
9 855 329.47 (+4.20 %), while sampling the real quadratic curve at 17 points gave
9 855 303.1870 (-0.00027 %). Its analytic witness is a single number - the middle
control point (49.398155, 391.693203) sits 7.1 mm off the curve, whose t=0.5 point
is (56.502734, 393.643704) - and that number is what the evaluator is pinned to
first, before any COM call is involved.

What is pinned here, all without CATIA:

* the evaluator reproduces the report's own mid-curve value to the digit;
* clamped knots are the vectors the report writes out (0,0,0,1,1,1 and
  0,0,0,0,1,1,1,1), so a "Bezier" is really a Bezier;
* the sampling error is measured on probe points BETWEEN the samples - a probe
  grid aligned with the sampling grid reported exactly 0.000000 mm on a visibly
  bowed curve while this was being written;
* the adaptive count meets a 0.01 mm tolerance, and the reply's numbers say how
  many points that costs (each one is a sketch element with no Remove);
* the tool refuses 'points' and 'control_points' together, and refuses
  closed=true with control points.
"""
from __future__ import annotations

import pytest

from catia_mcp import nurbs
from catia_mcp.results import CodedError
from catia_mcp.tools.sketcher import SketcherTools

# The report's SP15 (degree 2, 3 control points) and SP16 (degree 3, 4 points),
# verbatim from 2026-11-17-CATIA-v0.3.144-样条控制点缺失与镜像切除失效.md.
SP15 = [(37.214626, 336.867322), (49.398155, 391.693203), (90.0, 454.321087)]
SP16 = [(66.0, 347.067257), (64.475007, 391.287947),
        (100.837551, 405.962001), (101.0, 432.076285)]


# ── 1. the evaluator, against the report's own numbers ────────────────────
def test_clamped_knots_are_the_vectors_the_report_writes_out():
    assert nurbs.clamped_knots(3, 2) == [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
    assert nurbs.clamped_knots(4, 3) == [0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0]


def test_sp15_at_half_parameter_matches_the_report_to_the_digit():
    # The report says "二次 Bezier 在 t=0.5 的真实点是 (56.502734, 393.643704)".
    # If this drifts, every volume the tool later reports is somebody else's curve.
    x, y = nurbs.evaluate(SP15, 2, 0.5)
    assert x == pytest.approx(56.502734, abs=1e-6)
    assert y == pytest.approx(393.643704, abs=1e-6)


def test_the_middle_control_point_is_the_7_mm_the_report_measured():
    # The reason this parameter exists: interpolating it would put the curve
    # 7.1 mm away from the real one.
    on_curve = nurbs.evaluate(SP15, 2, 0.5)
    gap = ((on_curve[0] - SP15[1][0]) ** 2 + (on_curve[1] - SP15[1][1]) ** 2) ** 0.5
    # The report writes "约 7.1 mm"; the arithmetic says 7.3675. Both are
    # quoted in the reply, and the assertion pins the real one - a rounded
    # number from a report is a measurement, not a specification.
    assert gap == pytest.approx(7.3675, abs=0.005), (
        "the control point is 7.37 mm off its curve, the report's 7.1 is its own rounding")


def test_a_cubic_bezier_passes_through_its_ends_and_midpoint_formula():
    low = nurbs.evaluate(SP16, 3, 0.0)
    high = nurbs.evaluate(SP16, 3, 1.0)
    middle = nurbs.evaluate(SP16, 3, 0.5)
    assert low == pytest.approx(SP16[0])
    assert high == pytest.approx(SP16[-1])
    # B(0.5) = (P0 + 3 P1 + 3 P2 + P3) / 8 for a cubic Bezier.
    expected = tuple((SP16[0][axis] + 3 * SP16[1][axis]
                      + 3 * SP16[2][axis] + SP16[3][axis]) / 8 for axis in (0, 1))
    assert middle == pytest.approx(expected)


def test_sample_endpoints_are_the_control_point_ends_and_the_count_is_17():
    points = nurbs.sample(SP15, 2, per_span=16)
    assert len(points) == 17
    assert points[0] == pytest.approx(SP15[0])
    assert points[-1] == pytest.approx(SP15[-1])


# ── 2. the sampling error, and why the probe points must not align ────────
def test_the_sampling_error_is_not_zero_and_falls_by_four_per_doubling():
    coarse = nurbs.polyline_deviation(SP16, 3, per_span=16)
    finer = nurbs.polyline_deviation(SP16, 3, per_span=32)
    assert coarse == pytest.approx(0.1057, abs=0.002), (
        "16 intervals on the report's SP16 cubic is a 0.106 mm polyline")
    assert finer == pytest.approx(coarse / 4, rel=0.08), (
        "a polyline error is O(h^2): doubling the samples quarters it")


def test_a_probe_grid_aligned_with_the_samples_would_report_zero():
    # This is the trap the first version of polyline_deviation fell into: 64
    # samples probed at 32 intervals gave exactly 0.000000 on a curve that is
    # 0.0016 mm off its polyline. The probe points now sit at 1/4, 1/2, 3/4 of
    # each sampling interval, which cannot coincide with a sample.
    for per_span in (4, 8, 16, 32, 64):
        assert nurbs.polyline_deviation(SP15, 2, per_span=per_span) > 0.0


def test_the_adaptive_count_meets_the_tolerance_and_says_what_it_cost():
    fit = nurbs.fit_samples(SP16, 3, tolerance=0.01)
    assert fit["met"] is True
    assert fit["deviation_mm"] <= 0.01
    assert fit["per_span"] == 64, (
        "SP16 needs 64 intervals per span for 0.01 mm - 16 is not enough, and "
        "the report's 17-point curve is a 0.106 mm approximation at the samples")
    assert len(nurbs.sample(SP16, 3, per_span=fit["per_span"])) == 65

    loose = nurbs.fit_samples(SP16, 3, tolerance=1.0)
    assert loose["per_span"] == 8 and loose["met"] is True


def test_the_tolerance_is_reported_unmet_instead_of_silently_missed():
    fit = nurbs.fit_samples(SP16, 3, tolerance=1e-9, limit=16)
    assert fit["met"] is False
    assert fit["per_span"] == 16
    assert fit["deviation_mm"] > 1e-9


# ── 3. refusals, before any COM call ──────────────────────────────────────
def test_knots_that_do_not_fit_are_refused():
    with pytest.raises(ValueError, match="need 6 knots"):
        nurbs.check_knots([0, 0, 0, 1, 1], 3, 2)
    with pytest.raises(ValueError, match="must not decrease"):
        nurbs.check_knots([0, 0, 0, 1, 0.5, 1], 3, 2)
    with pytest.raises(ValueError, match="spans no interval"):
        nurbs.check_knots([0, 0, 0, 0, 0, 0], 3, 2)


def test_control_points_below_degree_plus_one_are_refused():
    with pytest.raises(ValueError, match="needs at least 4 control points"):
        nurbs.check_control_points([[0, 0], [1, 1], [2, 0]], 3)


def test_the_tool_declares_the_new_parameters_and_no_longer_requires_points():
    definitions = SketcherTools(None).get_tool_definitions()
    schema = next(tool for tool in definitions
                  if tool["name"] == "catia_sketch_spline")["inputSchema"]
    for name in ("control_points", "degree", "knots", "weights", "samples",
                 "tolerance"):
        assert name in schema["properties"], f"{name} is not offered"
    assert "control_points" not in schema.get("required", []), (
        "requiring 'points' made the control-point form unreachable")
    description = next(tool for tool in definitions
                       if tool["name"] == "catia_sketch_spline")["description"]
    assert "+4.20" in description and "-0.00027" in description, (
        "the description has to carry the measurements that justify the split")


def _spline(arguments: dict):
    return SketcherTools(None).execute("catia_sketch_spline", arguments)


def test_control_points_and_interpolation_points_together_are_refused():
    with pytest.raises(CodedError) as caught:
        _spline({"points": [[0, 0], [10, 10], [20, 0]],
                 "control_points": [[0, 0], [10, 10], [20, 0]], "degree": 2})
    assert caught.value.code == "CONTROL_POINTS_AND_POINTS"
    assert "7.37 mm" in str(caught.value)


def test_a_closed_control_spline_is_refused_with_the_way_to_do_it():
    with pytest.raises(CodedError) as caught:
        _spline({"control_points": SP15, "degree": 2, "closed": True})
    assert caught.value.code == "CLOSED_CONTROL_SPLINE"
    assert "repeating the first control point" in str(caught.value)


def test_a_weight_per_control_point_or_none():
    with pytest.raises(CodedError) as caught:
        _spline({"control_points": SP15, "degree": 2, "weights": [1.0, 1.0]})
    assert "one per control point" in str(caught.value)
    with pytest.raises(CodedError) as caught:
        _spline({"control_points": SP15, "degree": 2, "weights": [1.0, 0.0, 1.0]})
    assert "must all be positive" in str(caught.value)


def test_degree_above_the_point_count_is_a_validate_error_not_a_com_trip():
    with pytest.raises(CodedError) as caught:
        _spline({"control_points": [[0, 0], [10, 10], [20, 0]], "degree": 3})
    assert caught.value.stage == "validate"
    assert caught.value.modified is False
    assert "Nothing was created" in str(caught.value)


def test_a_call_with_neither_form_is_refused():
    with pytest.raises(CodedError) as caught:
        _spline({})
    assert caught.value.code == "BAD_ARGUMENT"
    assert "control_points" in str(caught.value)
