"""0.3.155: the bounding-box description now carries a budget, not a warning.

0.3.153 made the 17-instance assembly from the 2026-11-20 report cost 87 s cold
instead of 200 s, and 0.3.154 measured that scene to say so. What neither version
did was tell a caller what to expect BEFORE the call - the description said
"minutes" for a 70-instance assembly and nothing about the parts that make it up.

The 2026-11-20 measurement is the budget: 7 parts, 17 instances, 1.8-19.0 s for
the first touch of each part, 87 s cold and 13 s warm on method='vertices', 128 s
cold on the default mesh route. Instances are free once their part is bounded,
which is the one thing a caller can plan with.

These tests pin the numbers AND the two claims that stop someone "optimising" it
again: there is no bounding-box member to switch to, and the STL precision cannot
be passed per call.

No CATIA here.
"""
from __future__ import annotations

from catia_mcp.tools.measurement import MeasurementTools


def _box_description() -> str:
    definitions = {d["name"]: d
                   for d in MeasurementTools(connection=None).get_tool_definitions()}
    return definitions["catia_get_bounding_box"]["description"]


def test_the_budget_is_per_part_and_the_numbers_are_the_measured_ones() -> None:
    text = _box_description()
    assert "BUDGET BY PART, NOT BY INSTANCE" in text
    for number in ("17 instances over 7", "87 s cold", "13 s warm",
                   "128 s cold", "1.8 s", "19.0 s"):
        assert number in text, number


def test_it_names_the_route_each_number_belongs_to() -> None:
    """87 s is the vertices route and 128 s is the DEFAULT - a description that
    blurred them would read as 'the default is the fast one', which is backwards.
    """
    text = _box_description()
    assert "method='vertices'" in text
    assert "default mesh route" in text
    # The cold vertices figure must be attached to its route, not left floating.
    assert "87 s cold and 13 s warm on method='vertices'" in text


def test_it_says_why_no_route_is_cheaper() -> None:
    """Without this, the next round re-probes the same three dead ends."""
    text = _box_description()
    for said in ("Measurable", "SPAWorkbench", "no bounding-box member",
                 "global CATIA setting", "cannot be passed per call"):
        assert said in text, said


def test_instances_are_stated_to_be_free() -> None:
    """The model a caller plans with: bounding 7 parts costs the same as bounding
    their 17 instances."""
    text = _box_description()
    assert "instances are free once their part is bounded" in text.lower()
