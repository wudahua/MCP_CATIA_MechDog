"""0.3.119 - sampling a curve, and the false statement that had blocked it.

The question this answers: how far does a curve run from a surface? A minimum
distance cannot say - it is 0 for any curve that touches once. A message in
catia_gsd_split claimed the sampling route did not exist on this build
("AddNewPointOnCurveFromPercent does not update - measured 2026-11-09"). Measured
again on B30 2026-11-14 (scripts/b30_v0421_probe_deviation.py), on a 50 mm line
rising from a face to 3 mm above it:

    t=0 0.000000   t=0.25 0.750000   t=0.5 1.500000   t=0.75 2.250000   t=1 3.000000

3*t to six decimals. So the tool exists, and these tests pin its arithmetic, its
refusals, and the two sentences that must never appear again.
"""

from __future__ import annotations

import inspect

import pytest

from catia_mcp.results import (ArgumentOutOfRange, ArgumentRejected)
from catia_mcp.tools.gsd import GSDTools, _deviation_verdict


# ── the judgement itself ──────────────────────────────────────────────


def test_without_a_tolerance_it_reports_and_does_not_judge() -> None:
    worst, verdict = _deviation_verdict([(0.0, 0.0), (0.5, 1.5), (1.0, 3.0)],
                                        None)

    assert worst == (1.0, 3.0)
    assert "MAXIMUM, 3.0000 mm at t=1.000" in verdict
    assert "No tolerance was given" in verdict
    assert "Within" not in verdict and "WARNING" not in verdict


def test_a_met_tolerance_says_so_and_names_the_worst_sample() -> None:
    _, verdict = _deviation_verdict([(0.0, 0.0), (0.5, 0.19)], 0.2)

    assert "Within the 0.2 mm you allowed" in verdict
    assert "0.1900 mm at t=0.500" in verdict
    assert "WARNING" not in verdict


def test_a_missed_tolerance_names_every_sample_that_missed_it() -> None:
    rows = [(0.0, 0.0), (0.25, 0.4), (0.5, 0.05), (0.75, 0.9), (1.0, 0.6)]
    _, verdict = _deviation_verdict(rows, 0.3)

    assert "3 of 5 sample(s) exceed the 0.3 mm" in verdict
    assert "t=0.750 at 0.9000 mm" in verdict
    assert "t=0.250 at 0.4000 mm" in verdict
    assert "t=1.000 at 0.6000 mm" in verdict
    assert "t=0.500" not in verdict          # it was inside the allowance
    assert "worst 0.9000 mm at t=0.750" in verdict


def test_a_long_list_of_failures_is_cut_short_but_counted() -> None:
    rows = [(index / 9, 1.0) for index in range(10)]
    _, verdict = _deviation_verdict(rows, 0.1)

    assert "10 of 10 sample(s) exceed" in verdict
    assert "and 5 more" in verdict


def test_an_unreadable_profile_says_it_says_nothing() -> None:
    worst, verdict = _deviation_verdict([(0.0, None), (1.0, None)], None)

    assert worst is None
    assert "says nothing" in verdict


def test_a_profile_with_holes_judges_only_the_readable_samples() -> None:
    worst, verdict = _deviation_verdict([(0.0, 0.01), (0.5, None), (1.0, 0.02)],
                                        0.5)

    assert worst == (1.0, 0.02)
    assert "2 readable sample(s)" in verdict


# ── refusals, before CATIA is touched ─────────────────────────────────


def _tools() -> GSDTools:
    return GSDTools(connection=None)


def test_missing_curve_is_a_coded_refusal() -> None:
    with pytest.raises(ArgumentRejected) as caught:
        _tools()._measure_deviation({"samples": 5})
    assert getattr(caught.value, "code", "") == "CURVE_REQUIRED"


def test_the_sample_count_has_a_measured_ceiling() -> None:
    for samples in (1, 52, 0, -3):
        with pytest.raises(ArgumentOutOfRange) as caught:
            _tools()._measure_deviation({"curve": "C", "samples": samples})
        assert getattr(caught.value, "code", "") == "SAMPLES_OUT_OF_RANGE"


def test_a_non_numeric_sample_count_says_what_it_wants() -> None:
    with pytest.raises(ArgumentRejected) as caught:
        _tools()._measure_deviation({"curve": "C", "samples": "nine"})
    assert "samples" in str(caught.value)


def test_a_negative_tolerance_would_fail_everything() -> None:
    with pytest.raises(ArgumentOutOfRange) as caught:
        _tools()._measure_deviation({"curve": "C", "tolerance": -1})
    assert getattr(caught.value, "code", "") == "TOLERANCE_OUT_OF_RANGE"


def test_a_non_numeric_tolerance_is_refused_with_the_way_out() -> None:
    with pytest.raises(ArgumentRejected) as caught:
        _tools()._measure_deviation({"curve": "C", "tolerance": "tight"})
    assert "Omit it" in str(caught.value)


# ── the call, against a recording stand-in ────────────────────────────


class _Ref:
    def __init__(self, name: str, distance=None) -> None:
        self.name = name
        self.distance = distance


class _Body:
    Name = "零件几何体"


class _Part:
    MainBody = _Body()

    def CreateReferenceFromObject(self, obj):
        return obj

    def UpdateObject(self, shape) -> None:
        shape.updated = True


class _Point(_Ref):
    def __init__(self, ratio: float) -> None:
        # The ramp from the probe: 0 at t=0, 3 mm at t=1, linear.
        super().__init__(f"point{ratio}", distance=3.0 * ratio)
        self.ratio = ratio
        self.deleted = False
        self.updated = False


class _Curve(_Ref):
    def __init__(self) -> None:
        super().__init__("Ramp", distance=0.0)


class _Measurable:
    def __init__(self, reference) -> None:
        self.reference = reference
        self.Length = 50.0

    def GetMinimumDistance(self, other):
        return self.reference.distance


class _Doc:
    def GetWorkbench(self, name):
        assert name == "SPAWorkbench"
        return self

    def GetMeasurable(self, reference):
        return _Measurable(reference)


class _Conn:
    def __init__(self) -> None:
        self.active_document = _Doc()

    def ensure_connected(self) -> None:
        return None

    def get_active_part(self):
        return _Part()


class _Factory:
    def __init__(self, log) -> None:
        self.log = log

    def AddNewPointOnCurveFromPercent(self, curve_ref, ratio, orientation):
        point = _Point(ratio)
        self.log.append(point)
        return point


class _GeoSet:
    def AppendHybridShape(self, shape) -> None:
        return None


class _Recording(GSDTools):
    def __init__(self) -> None:
        super().__init__(connection=_Conn())
        self.points: list[_Point] = []
        self.dropped: list[_Point] = []

    def _ref(self, name):
        return _Curve() if str(name) in ("Ramp", "curve") else _Ref(str(name))

    def _factory(self):
        return _Factory(self.points)

    def _get_geoset(self):
        return _GeoSet()

    def _free_name(self, part, wanted):
        return str(wanted), True

    def _drop_element(self, shape):
        shape.deleted = True
        self.dropped.append(shape)

    def _name_gone(self, part, name):
        return True


def test_five_samples_are_five_points_and_the_table_is_the_measurement() -> None:
    tools = _Recording()
    reply = tools._measure_deviation({"curve": "Ramp", "samples": 5})

    assert len(tools.points) == 5
    assert [round(point.ratio, 3) for point in tools.points] == [0.0, 0.25, 0.5,
                                                                0.75, 1.0]
    assert "t=0.250  0.7500 mm" in reply
    assert "t=1.000  3.0000 mm" in reply
    assert "MAXIMUM, 3.0000 mm at t=1.000" in reply


def test_the_points_are_removed_again_by_default() -> None:
    tools = _Recording()
    reply = tools._measure_deviation({"curve": "Ramp", "samples": 3})

    assert len(tools.dropped) == 3
    assert all(point.deleted for point in tools.points)
    assert "The 3 sample point(s) this call created were removed again" in reply


def test_keep_points_leaves_the_scaffolding_in_the_tree() -> None:
    tools = _Recording()
    reply = tools._measure_deviation({"curve": "Ramp", "samples": 3,
                                      "keep_points": True})

    assert tools.dropped == []
    assert "were KEPT" in reply
    assert "Ramp_t00" in reply


def test_the_reply_says_the_grid_is_not_a_proof() -> None:
    reply = _Recording()._measure_deviation({"curve": "Ramp", "samples": 3})

    assert "not a proof" in reply
    assert "between two of them" in reply
    # And says what 0.0000 means when the target is a solid body.
    assert "ON or INSIDE" in reply


def test_the_whole_curves_minimum_is_reported_as_the_thing_that_cannot_help() -> None:
    reply = _Recording()._measure_deviation({"curve": "Ramp", "samples": 3})

    assert "GetMinimumDistance to the same target is 0.0000 mm" in reply
    assert "that is a MINIMUM" in reply
    assert "catia_measure_distance" in reply


# ── the claim that had blocked this, now corrected ───────────────────


def test_the_split_message_no_longer_says_sampling_is_impossible() -> None:
    source = inspect.getsource(GSDTools._split)

    assert "offers no way to sample" not in source
    assert "catia_measure_deviation" in source


def test_the_deviation_docstring_records_the_measurement_not_the_rumour() -> None:
    source = inspect.getsource(GSDTools._measure_deviation)

    assert "does not update" in source          # quoted as the WRONG claim
    assert "that claim is wrong" in source
    assert "0.750000 mm" in source


def test_the_schema_is_registered_with_curve_and_samples() -> None:
    definitions = {item["name"]: item
                   for item in GSDTools(connection=None).get_tool_definitions()}
    schema = definitions["catia_measure_deviation"]["inputSchema"]

    assert schema["required"] == ["curve"]
    assert set(schema["properties"]) == {"curve", "surface", "samples",
                                         "tolerance", "keep_points"}
    assert schema["properties"]["samples"]["default"] == 9
