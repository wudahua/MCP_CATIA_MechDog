# -*- coding: utf-8 -*-
"""The cost breakdown has to close, every run, not nine runs out of ten.

`parts_ms` is split into `measure_ms` + `tensor_ms` + `parts_other_ms`, and the
note beside them promises the three "add up exactly". They did — until three
separate clock readings rounded the wrong way at once, at which point a
`max(..., 0.0)` clamp published 0.0 for the remainder and the reply was 0.1-0.15 ms
over. That is a flake in `test_reported_issues_2026_10_29_v0338.py` (fails about
once per full run, always passes alone), and this file pins the arithmetic so it
cannot come back.

The rule: the last bucket is whatever the others did not take, rounded once.
Clamping it is not allowed - a clamped remainder is a breakdown that does not add
up, which is the one thing a breakdown is for.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from types import SimpleNamespace

import pytest


# ───────────────────────────── 1. the arithmetic

def test_a_positive_remainder_is_just_the_difference():
    from catia_mcp.tools.measurement import _cost_remainder

    remainder, note = _cost_remainder(10.0, 3.5, 2.5)
    assert remainder == pytest.approx(4.0)
    assert note is None


def test_windows_that_round_past_the_total_give_a_negative_remainder():
    """5.1 + 5.0 is 10.1 against a total of 10.0 - the case the clamp hid."""
    from catia_mcp.tools.measurement import _cost_remainder

    remainder, note = _cost_remainder(10.0, 5.1, 5.0)
    assert remainder == pytest.approx(-0.1)
    assert note is not None
    # The sentence has to say what a reader will otherwise assume is a bug.
    assert "no step took negative time" in note
    assert "add up to parts_ms exactly" in note
    # And the three terms now close to the digit, which is the whole point.
    assert round(5.1 + 5.0 + remainder, 1) == pytest.approx(10.0)


def test_the_three_terms_close_for_any_way_the_clock_rounds():
    """Randomised over the real failure mode: values independently rounded to 0.1."""
    from catia_mcp.tools.measurement import _cost_remainder

    rng = random.Random(20261102)
    for _ in range(2000):
        # A total, and two windows inside it, each rounded to 0.1 like the code does
        measure = round(rng.uniform(0.0, 40.0), 1)
        tensor = round(rng.uniform(0.0, 40.0), 1)
        parts = round(measure + tensor + rng.uniform(-0.15, 2.0), 1)
        remainder, _note = _cost_remainder(parts, measure, tensor)
        assert abs(round(measure + tensor + remainder, 1) - parts) <= 0.05


def test_the_remainder_is_never_clamped_in_the_source():
    """A guard against the clamp coming back with a tidier-looking number."""
    source = (Path(__file__).resolve().parents[1]
              / "catia_mcp" / "tools" / "measurement.py").read_text(encoding="utf-8")
    assert "max(parts_ms - measure_ms - tensor_ms" not in source


# ───────────────────────────── 2. both routes publish it

class _Measurable:
    Volume = 8.0e-6
    Area = 2.0e-3

    def GetCOG(self):
        return [0.0, 0.0, 2.5]


def _tools():
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None, app=None,
                                 component_usage=lambda: {}, active_document=None)
    tools._read_measurable_array = lambda measurable, method, count: [0.0, 0.0, 2.5]
    tools._tensor_for = lambda part: None
    return tools


def _part():
    part = SimpleNamespace(
        Name="COST.CATPart",
        MainBody=SimpleNamespace(Name="PartBody",
                                 Shapes=SimpleNamespace(Count=1)))
    part.Parent = SimpleNamespace(
        Name="COST.CATPart", FullName="COST.CATPart",
        GetWorkbench=lambda name: SimpleNamespace(
            GetMeasurable=lambda ref: _Measurable()))
    part.CreateReferenceFromObject = lambda obj: obj
    return part


def _assert_closes(reply: dict) -> None:
    total = reply["measure_ms"] + reply["tensor_ms"] + reply["parts_other_ms"]
    assert abs(round(total, 1) - reply["parts_ms"]) <= 0.05, (
        f"the breakdown is {round(total, 1)} against parts_ms {reply['parts_ms']}: "
        "the remainder was clamped instead of absorbing the rounding")


def test_the_named_document_route_closes():
    reply = json.loads(_tools()._part_inertia(_part(), None, "COST.CATPart"))
    _assert_closes(reply)
    assert "parts_other_ms" in reply
    # A normal call leaves no note: the negative case is the exception, not the shape.
    assert "parts_other_note" not in reply


def test_the_active_part_route_closes():
    tools = _tools()
    part = _part()
    tools.conn = SimpleNamespace(
        ensure_connected=lambda: None, app=None, active_document=None,
        component_usage=lambda: {},
        get_active_part=lambda: part, get_active_part_body=lambda: part.MainBody)
    tools._document_kind = lambda: "part"
    tools._spa_workbench = lambda: SimpleNamespace(
        GetMeasurable=lambda ref: _Measurable())
    tools._evaluate_floats = lambda script, name, args: (
        [0.0155, 1.0e-6, 0.0, 0.0, 0.0, 2.6e-6, 0.0, 0.0, 0.0, 2.6e-6])
    reply = json.loads(tools._get_inertia())
    _assert_closes(reply)
    assert round(reply["parts_ms"] + reply["overhead_ms"], 1) == reply["elapsed_ms"]


def test_the_note_appears_only_when_the_remainder_is_negative(monkeypatch):
    """Drive the clock so the two windows round past the total, and read the reply.

    Monotonic and physically possible on purpose: both windows really are inside the
    total (5.06 ms + 5.06 ms inside 10.12 ms), and only the rounding to 0.1 makes
    their printed sum 10.2 against a printed total of 10.1. That is the whole
    failure mode - not a clock that went backwards.
    """
    from catia_mcp.tools import measurement as M

    readings = iter([0.0, 0.0, 0.00506, 0.00506, 0.01012, 0.01012])

    def fake_clock():
        try:
            return next(readings)
        except StopIteration:  # later callers (the bookkeeping) get a real number
            return 0.01012

    monkeypatch.setattr(M.time, "perf_counter", fake_clock)
    reply = json.loads(_tools()._part_inertia(_part(), None, "COST.CATPart"))
    assert (reply["measure_ms"], reply["tensor_ms"], reply["parts_ms"]) == (5.1, 5.1, 10.1)
    assert reply["parts_other_ms"] == -0.1
    assert "no step took negative time" in reply["parts_other_note"]
    _assert_closes(reply)
