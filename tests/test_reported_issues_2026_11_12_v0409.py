"""0.3.109: the outline's self-crossings, before the feature finds them.

The 2026-11-11 eighth report wrote its own O(n^2) segment-intersection test in
Python before feeding a 100-point outline to catia_pad, because a hook that
curled into a loop showed up as "the shape looks wrong" and then as a feature
that would not build - never as a warning.

catia_close_sketch now reports it, computed from the same drawing record it
already uses for the outline echo and the break list. These tests are the
arithmetic only, so every case runs without CATIA:

  1  two lines that cross, and two that only meet at an endpoint
  2  a T junction - one line ending on another's middle - is a crossing
  3  parallel lines, and a line through a circle
  4  a line through a HALF circle hits it once, not twice
  5  two circles cross twice
  6  a rectangle crossed by a line gives two points, not four
  7  a point chain that doubles back on itself is caught
  8  the sentence says what it checked, and refuses to claim a clean sketch
     when nothing was recorded
"""
from __future__ import annotations

import math

from catia_mcp.geometry2d import crossings, describe, segments_of
from catia_mcp.tools.sketcher import SketcherTools


def line(x1, y1, x2, y2):
    return {"kind": "line", "x1": x1, "y1": y1, "x2": x2, "y2": y2}


def arc(cx, cy, r, start_rad, sweep_rad):
    return {"kind": "arc", "cx": cx, "cy": cy, "radius": r,
            "start_rad": start_rad, "sweep_rad": sweep_rad}


def test_two_crossing_lines_report_the_point():
    found = crossings(segments_of([line(0, 0, 10, 10), line(0, 10, 10, 0)]))
    assert len(found) == 1
    assert found[0]["point"] == (5.0, 5.0)


def test_two_lines_that_share_an_endpoint_are_not_a_crossing():
    found = crossings(segments_of([line(0, 0, 10, 0), line(10, 0, 10, 10)]))
    assert found == []


def test_a_t_junction_is_a_crossing_because_it_has_no_area_either_side():
    found = crossings(segments_of([line(0, 0, 10, 0), line(5, 0, 5, 10)]))
    assert len(found) == 1
    assert found[0]["point"] == (5.0, 0.0)


def test_parallel_lines_and_a_miss_are_not_crossings():
    assert crossings(segments_of([line(0, 0, 10, 0), line(0, 2, 10, 2)])) == []
    assert crossings(segments_of([line(0, 20, 10, 20), line(0, 0, 10, 0)])) == []


def test_a_line_through_a_circle_crosses_it_twice():
    circle = {"kind": "circle", "cx": 5, "cy": 0, "radius": 3}
    found = crossings(segments_of([circle, line(0, 0, 10, 0)]))
    assert sorted(item["point"][0] for item in found) == [2.0, 8.0]


def test_a_line_crosses_a_half_circle_once_because_the_other_hit_is_off_the_arc():
    half = arc(0, 0, 5, 0.0, math.pi)          # the upper half, ccw from +X
    found = crossings(segments_of([half, line(0, -10, 0, 10)]))
    # The vertical line meets the full circle at (0, 5) and (0, -5); only the
    # first lies on the upper half, and the lower one must not be reported.
    assert [item["point"] for item in found] == [(0.0, 5.0)]


def test_two_circles_cross_twice():
    one = {"kind": "circle", "cx": 0, "cy": 0, "radius": 5}
    other = {"kind": "circle", "cx": 6, "cy": 0, "radius": 5}
    found = crossings(segments_of([one, other]))
    assert sorted(item["point"] for item in found) == [(3.0, -4.0), (3.0, 4.0)]


def test_a_line_through_a_rectangle_gives_two_points_not_four():
    box = {"kind": "rectangle", "x1": 0, "y1": 0, "x2": 10, "y2": 10}
    found = crossings(segments_of([box, line(-5, 5, 15, 5)]))
    assert sorted(item["point"] for item in found) == [(0.0, 5.0), (10.0, 5.0)]


def test_a_point_chain_that_doubles_back_is_caught():
    figure_eight = {"kind": "spline", "closed": True,
                    "pts": [[0, 0], [10, 10], [0, 10], [10, 0]]}
    found = crossings(segments_of([figure_eight]))
    assert found, "the two diagonals cross in the middle"
    assert all(item["a"] == item["b"] == 1 for item in found)


def test_the_sentence_names_the_elements_and_the_point():
    found = crossings(segments_of([line(0, 0, 10, 10), line(0, 10, 10, 0)]))
    text = describe(found, 2)
    assert "CROSSES ITSELF" in text
    assert "(5.0000, 5.0000)" in text
    assert "no single inside" in text


def test_a_clean_outline_says_what_was_checked():
    text = describe([], 4)
    assert "does not cross itself" in text
    assert "4 segment(s) checked" in text


def test_no_record_means_not_checked_rather_than_clean():
    tools = SketcherTools(connection=None)
    text, data, checked = tools._self_crossing_report("nothing-here")
    assert (text, data, checked) == ("", [], False)


def test_the_record_is_read_for_the_named_sketch():
    tools = SketcherTools(connection=None)
    key = tools._sketch_key("草图.1")
    tools._drawn[key] = [line(0, 0, 10, 10), line(0, 10, 10, 0)]
    text, data, checked = tools._self_crossing_report("草图.1")
    assert checked is True
    assert len(data) == 1
    assert "CROSSES ITSELF" in text
