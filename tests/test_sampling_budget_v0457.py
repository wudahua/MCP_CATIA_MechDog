"""2026-11-18 report, R1 and R2: the sampling budget and what a plan keeps.

Two findings, one cause each:

* **R1** - the control-point path wrote 65-129 point elements into a sketch for
  one curve, and a rebuilt P06 grew from ~400 KB to 491 KB. Those points are the
  spline's DEFINITION (deleting one changes the curve) and `GeometricElements`
  has no Remove, so the caller cannot trim them afterwards. The default now aims
  at 0.03 mm with a hard cap of 32 samples per span, and the reply says what a
  finer curve would cost in the same unit - point elements.
* **R2** - a step's `result` is truncated at 600 chars, and the report found the
  two error numbers the new tool exists to report cut off exactly there. The
  truncation now keeps BOTH ends, and the numbers are written first.

Pinned here without CATIA; the geometry side is in
`scripts/b30_v0458_acceptance_residuals.py`.
"""
from __future__ import annotations

from catia_mcp import nurbs
from catia_mcp.tools import sketcher
from catia_mcp.tools.planner import MAX_RESULT_CHARS, PlannerTools

SP16 = [(66.0, 347.067257), (64.475007, 391.287947),
        (100.837551, 405.962001), (101.0, 432.076285)]


# ── R1: the budget ────────────────────────────────────────────────────────
def test_the_default_tolerance_is_the_one_the_report_asked_for():
    assert sketcher.DEFAULT_SAMPLING_TOLERANCE_MM == 0.03, (
        "0.01 cost 65-129 point elements for one curve (R1)")
    assert sketcher.MAX_SAMPLES_PER_SPAN == 32, (
        "a curve is not allowed to fill a sketch on its own")


def test_the_cap_binds_before_the_tolerance_does_on_the_report_curve():
    fit = nurbs.fit_samples(SP16, 3, tolerance=sketcher.DEFAULT_SAMPLING_TOLERANCE_MM,
                            limit=sketcher.MAX_SAMPLES_PER_SPAN)
    assert fit["per_span"] == 32
    assert fit["met"] is True, "0.0267 mm is inside 0.03"

    tight = nurbs.fit_samples(SP16, 3, tolerance=0.01,
                              limit=sketcher.MAX_SAMPLES_PER_SPAN)
    assert tight["per_span"] == 32 and tight["met"] is False, (
        "0.01 mm cannot be met under the cap - and the reply has to say so "
        "rather than silently spending 64 points")


def test_what_the_default_now_writes_versus_what_it_used_to():
    # 0.01/unbounded was 65 points for this curve; 0.03/32 is 33.
    loose = nurbs.fit_samples(SP16, 3, tolerance=0.01, limit=256)
    assert loose["per_span"] == 64
    assert len(nurbs.sample(SP16, 3, per_span=64)) == 65
    default = nurbs.fit_samples(SP16, 3,
                                tolerance=sketcher.DEFAULT_SAMPLING_TOLERANCE_MM,
                                limit=sketcher.MAX_SAMPLES_PER_SPAN)
    assert len(nurbs.sample(SP16, 3, per_span=default["per_span"])) == 33


def test_a_quadratic_now_costs_17_points():
    sp15 = [(37.214626, 336.867322), (49.398155, 391.693203), (90.0, 454.321087)]
    fit = nurbs.fit_samples(sp15, 2,
                            tolerance=sketcher.DEFAULT_SAMPLING_TOLERANCE_MM,
                            limit=sketcher.MAX_SAMPLES_PER_SPAN)
    assert fit["per_span"] == 16, "0.0252 mm at 16 intervals is inside 0.03"
    assert len(nurbs.sample(sp15, 2, per_span=16)) == 17


# ── R2: what a plan keeps ────────────────────────────────────────────────
def test_a_long_reply_keeps_its_end_as_well_as_its_start():
    tail = " SAMPLING ERROR 0.0063 mm; DRAWN 0.0000 mm."
    text = "head " + ("x" * 1200) + " middle " + ("y" * 400) + tail
    trimmed = PlannerTools._trim(text)
    assert trimmed.startswith("head ")
    assert trimmed.endswith(tail), (
        "the conclusion is at the end - cutting it is what R2 reported")
    assert "dropped from the middle" in trimmed
    assert len(trimmed) > MAX_RESULT_CHARS, "both ends are kept, so it is longer"


def test_a_reply_that_just_fits_is_untouched():
    text = "z" * (MAX_RESULT_CHARS + PlannerTools.KEEP_TAIL_CHARS)
    assert PlannerTools._trim(text) == text


def test_the_spline_reply_puts_its_numbers_before_the_prose():
    # The 600 chars a plan keeps have to contain the two errors, so they are
    # written in the first sentence rather than after the knot vector.
    source = __import__("inspect").getsource(
        sketcher.SketcherTools._draw_control_spline)
    head = source.index("head = (")
    detail = source.index("detail = (")
    assert head < detail
    assert "Sampling error" in source[head:detail]
