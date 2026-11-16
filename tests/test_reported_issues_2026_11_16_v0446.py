"""2026-11-16 round 16: three defects in `catia_surface_probe`, all reproduced.

P1-1  `inside_solid` was answered from the RAY's convergence in along mode: the
      hedge that turns "on the target" into null was fed `1.0 if converged else
      0.0`, so an inside station whose line missed the face came back null while
      nearest mode said true - "7/7 wrong", and the field then meant two different
      things depending on the mode. It is now fed the QUERY POINT's own distance
      to the target, which the first iteration already measured.

P1-2  a row that did not reach the surface still printed `offset_mm` and
      `surface_point` to six decimals, so a caller reading those built a section
      0.19-0.79 mm off the skin. A miss now answers null / null / `usable: false`
      and keeps what it did reach under `last_iterate_mm`.

P1-3  calibration was pinned to station 1, and on a real surface station 1 is the
      one most likely to sit in the tightest curvature and miss. It now runs on
      the first station that can carry it and says which.

Two more were found while reproducing, and neither report could see them from
outside: the gap was measured at the iterate BEFORE the one the row published
(so `gap_mm` was neither the distance to the surface nor the step taken), and
`inside_solid` asked `part.MainBody` only - measured on B30 2026-11-16 with a
second body named 'Upper' holding a solid 100 mm higher, a station INSIDE 'Upper'
answered false, which is exactly the three false rows the report could not
explain.

What is pinned here, all without CATIA: the mode-free inside flag, the payload of
a miss, the calibration station choice, where the gap is measured, and the
wording the tool now promises.
"""
from __future__ import annotations

import math

import pytest

from catia_mcp import fidelity


# ── surfaces to measure against, all analytic ──────────────────────────────
def plane_z10(where):
    return [float(where[0]), float(where[1]), 10.0], abs(float(where[2]) - 10.0)


def sideways_foot(where):
    """A foot 2.4 mm away at RIGHT ANGLES to a +Z axis: the axis cannot reach it."""
    point = [float(where[0]) - 2.4, float(where[1]), 10.0]
    return point, 2.4


def cylinder_r15(where):
    """The nearest point on the r=15 cylinder about Z, at the query's own z.

    B30 2026-11-16 probed this shape for real (a padded r=15 circle, the lateral
    face) and the along +X height at y=10 came back 18.819017 against the
    analytic 30 - sqrt(15^2 - 10^2) = 18.819660.
    """
    x, y, z = float(where[0]), float(where[1]), float(where[2])
    radius = math.hypot(x, y)
    if radius < 1e-9:
        return [15.0, 0.0, z], 15.0
    return [x * 15.0 / radius, y * 15.0 / radius, z], abs(radius - 15.0)


def slow_foot(where):
    """A foot 0.002 mm away, of which only 0.0005 mm is along the axis.

    This is the shape of the last iteration of a shallow-angle station: the axial
    step has fallen below the tolerance while the point is still a little off.
    """
    tangential = math.sqrt(0.002 ** 2 - 0.0005 ** 2)
    return [where[0] + 0.0005, where[1], where[2] + tangential], 0.002


def always_inside(where):
    return True


def always_outside(where):
    return False


# ── P1-1: the inside flag is a fact about the point, not about the ray ─────
def test_an_inside_station_is_marked_even_when_the_ray_cannot_reach_the_face():
    """The report's exact case: point inside, axis along a line that misses the
    face the caller named. Reproduced on B30 (v0446 cell B) as null."""
    rows = fidelity.along_rows([[0.0, 0.0, 8.0]], [0.0, 0.0, 1.0], sideways_foot,
                               tolerance=0.001, max_iterations=6,
                               inside=always_inside)
    assert rows[0]["converged"] is False
    assert rows[0]["inside_solid"] is True, (
        "the point is in the material; whether the line reached the face does "
        "not change that")


def test_an_outside_station_stays_false_in_along_mode():
    rows = fidelity.along_rows([[0.0, 0.0, 8.0]], [0.0, 0.0, 1.0], sideways_foot,
                               tolerance=0.001, max_iterations=6,
                               inside=always_outside)
    assert rows[0]["inside_solid"] is False


def test_a_station_ON_the_target_is_still_null_in_along_mode():
    """The one ambiguous case, and the only one: the query point lies on the
    target, so "in the material" and "on the skin" are the same spot."""
    rows = fidelity.along_rows([[0.0, 0.0, 10.0]], [0.0, 0.0, 1.0], plane_z10,
                               tolerance=1e-6, inside=always_inside)
    assert rows[0]["start_distance_mm"] == pytest.approx(0.0)
    assert rows[0]["inside_solid"] is None


# ── P1-2: a miss hands out no coordinate ───────────────────────────────────
def test_a_row_that_missed_carries_no_offset_and_no_surface_point():
    row = fidelity.along_rows([[0.0, 0.0, 8.0]], [0.0, 0.0, 1.0], sideways_foot,
                              tolerance=0.001, max_iterations=6)[0]
    assert row["converged"] is False
    assert row["usable"] is False
    assert row["offset_mm"] is None, "six decimals of a non-hit is how round 16 " \
                                     "got stations that read like hits"
    assert "surface_point" not in row
    assert "along_point" not in row


def test_what_the_miss_did_reach_is_kept_but_not_named_as_the_answer():
    row = fidelity.along_rows([[0.0, 0.0, 8.0]], [0.0, 0.0, 1.0], sideways_foot,
                              tolerance=0.001, max_iterations=6)[0]
    assert row["last_iterate_mm"] == pytest.approx(2.0)   # it stepped to z=10
    assert row["last_surface_point"] == pytest.approx([-2.4, 0.0, 10.0])
    assert row["gap_mm"] == pytest.approx(2.4)            # how far off that is


def test_a_converged_row_is_usable_and_says_so():
    row = fidelity.along_rows([[0.0, 0.0, 4.0]], [0.0, 0.0, 1.0], plane_z10,
                              tolerance=1e-6)[0]
    assert row["usable"] is True
    assert row["offset_mm"] == pytest.approx(6.0)
    assert row["surface_point"] == pytest.approx([0.0, 0.0, 10.0])


def test_a_nearest_row_is_usable_too():
    row = fidelity.nearest_rows([[0.0, 0.0, 4.0]], plane_z10)[0]
    assert row["usable"] is True


def test_a_nearest_row_that_raised_is_not_usable():
    def broken(where):
        raise RuntimeError("方法 GetMinimumDistancePoints 失败")

    row = fidelity.nearest_rows([[0.0, 0.0, 4.0]], broken)[0]
    assert row["usable"] is False
    assert "GetMinimumDistancePoints" in row["error"]


def test_the_start_distance_is_the_query_points_own_distance():
    """Free (the first iteration measures it) and load-bearing: it is what the
    inside hedge must use instead of the ray's verdict."""
    row = fidelity.along_rows([[0.0, 0.0, 8.0]], [0.0, 0.0, 1.0], sideways_foot,
                              tolerance=0.001, max_iterations=6)[0]
    assert row["start_distance_mm"] == pytest.approx(math.hypot(2.4, 2.0))


# ── the gap belongs to the iterate that is published ───────────────────────
def test_the_published_gap_is_the_distance_from_the_published_point():
    """The first version measured the gap at t_n and published the point at
    t_(n+1): reported 0.000719 mm for an iterate 0.9 mm short (B30 2026-11-16).
    Re-measure whichever point the row publishes and it has to agree."""
    cases = (([-30.0, 10.0, 0.0], [1.0, 0.0, 0.0]),
             ([-30.0, 20.0, 0.0], [1.0, 0.0, 0.0]),      # misses the cylinder
             ([0.0, 0.0, -20.0], [0.0, 0.0, 1.0]))       # the axis: never meets it
    for query, axis in cases:
        row = fidelity.along_rows([query], axis, cylinder_r15, tolerance=0.001,
                                  max_iterations=12)[0]
        published = row.get("along_point") or row.get("last_along_point")
        foot, _ = cylinder_r15(published)
        true_gap = math.dist(published, foot)
        assert row["gap_mm"] == pytest.approx(true_gap, abs=2e-3), (
            f"published gap {row['gap_mm']} vs {true_gap} re-measured at "
            f"{published} (converged={row['converged']})")


def test_a_small_axial_step_does_not_end_the_search():
    """A step below the tolerance with a remainder just above it is "nearly
    there", not "the axis cannot reach it" - the first rule stopped on it."""
    row = fidelity.along_rows([[0.0, 0.0, 0.0]], [0.0, 0.0, 1.0], slow_foot,
                              tolerance=0.001, max_iterations=8)[0]
    assert row["iterations"] > 1, "it stopped at the first station again"
    assert row["converged"] is False
    assert row["offset_mm"] is None


def test_the_cylinder_height_at_y10_is_the_analytic_one():
    """B30 2026-11-16: 18.819017 measured, 18.819660 analytic."""
    row = fidelity.along_rows([[-30.0, 10.0, 0.0]], [1.0, 0.0, 0.0], cylinder_r15,
                              tolerance=0.001, max_iterations=12)[0]
    assert row["converged"] is True
    assert row["offset_mm"] == pytest.approx(30.0 - math.sqrt(125.0), abs=2e-3)


# ── P1-3: calibration runs on a station that can carry it ──────────────────
def test_calibration_skips_a_station_that_missed():
    rows = fidelity.along_rows([[0.0, 0.0, 8.0], [0.0, 0.0, 4.0]],
                               [0.0, 0.0, 1.0], plane_z10, tolerance=1e-6)
    assert rows[0]["converged"] is True
    check = fidelity._calibration(
        [{"at": [0.0, 0.0, 8.0], "converged": False, "usable": False}] + rows[1:],
        [0.0, 0.0, 1.0], plane_z10, 1e-6, 12)
    assert check["ok"] is True
    assert check["station"] == 2
    assert check["stations"] == 2


def test_calibration_says_when_no_station_could_be_used():
    rows = fidelity.along_rows([[0.0, 0.0, 8.0]], [0.0, 0.0, 1.0], sideways_foot,
                               tolerance=0.001, max_iterations=6)
    check = fidelity._calibration(rows, [0.0, 0.0, 1.0], sideways_foot, 0.001, 6)
    assert check["ok"] is None
    assert check["station"] is None
    assert "none of the 1 station(s)" in check["reason"]


def test_nearest_calibration_skips_a_zero_offset_station():
    rows = fidelity.nearest_rows([[0.0, 0.0, 10.0], [0.0, 0.0, 4.0]], plane_z10)
    assert rows[0]["normal"] is None
    check = fidelity._calibration(rows, None, plane_z10, 1e-3, 12)
    assert check["ok"] is True
    assert check["station"] == 2


# ── what the tool now promises ─────────────────────────────────────────────
def test_the_description_names_the_new_fields_and_the_null_rule():
    from catia_mcp.tools.gsd import GSDTools

    tools = {tool["name"]: tool for tool in
             GSDTools.get_tool_definitions(GSDTools.__new__(GSDTools))}
    text = tools["catia_surface_probe"]["description"]
    assert "usable as false" in text
    assert "last_iterate_mm" in text
    assert "inside_checked_against" in text or "BOTH modes alike" in text
    assert "calibration.station" in text
    assert "NOT PINNED TO STATION 1" in text
