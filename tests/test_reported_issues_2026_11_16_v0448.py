"""2026-11-16 round 17's data, read closely: a stalled walk is not a missed line.

The all-green report shows four inside stations answering
`offset_mm: null, usable: false, start_distance_mm 7.02, gap_mm 7.02`. A gap
equal to the start distance means the iteration never moved: the nearest point sat
at right angles to the axis, so the axial step was zero on the first read.

The tool's tip then said the line "does not come within 0.001 mm of the surface",
which is a claim about geometry - and it was false. On a r=15 cylinder probed
along -Y, measured on B30 2026-11-16:

    start (0,0,25) inside   stalled, gap 15.0   the skin is at y=-15, 15.0 mm on
    start (5,0,25) inside   stalled, gap 10.0   the skin is at y=-14.142, 14.142 mm on

Both analytic answers sit 0.000000 mm from the face. The walk could not take a
step, but the line certainly crossed.

So the walk now RESTARTS at the distance it has already measured - which is the
right size of jump, because that number IS how far away the surface was found to
be - forward first, and the opposite end of the line only when forward is not a
crossing. What is pinned here, all without CATIA: that restart, its direction
rule, that it happens once, and that a restart which cannot converge is still
reported as a miss rather than dressed up as a hit.
"""
from __future__ import annotations

import math

import pytest

from catia_mcp import fidelity


def cylinder_r15(where):
    """The nearest point on the r=15 cylinder about Z, at the query's own z."""
    x, y, z = float(where[0]), float(where[1]), float(where[2])
    radius = math.hypot(x, y)
    if radius < 1e-9:
        return [15.0, 0.0, z], 15.0
    return [x * 15.0 / radius, y * 15.0 / radius, z], abs(radius - 15.0)


def plane_z10(where):
    return [float(where[0]), float(where[1]), 10.0], abs(float(where[2]) - 10.0)


def test_a_stalled_inside_station_answers_the_crossing_it_could_not_walk_to():
    row = fidelity.along_rows([[0.0, 0.0, 25.0]], [0.0, -1.0, 0.0], cylinder_r15,
                              tolerance=0.001, max_iterations=12)[0]
    assert row["stalled"] is True, "the walk stopped with no axial component"
    assert row["converged"] is True, "the line crosses the skin 15 mm along -Y"
    assert row["offset_mm"] == pytest.approx(15.0, abs=1e-6)
    assert row["along_point"] == pytest.approx([0.0, -15.0, 25.0], abs=1e-6)
    assert row["restart_mm"] == pytest.approx(15.0)
    assert row["gap_mm"] == pytest.approx(0.0, abs=1e-6)


def test_the_restart_prefers_the_direction_that_was_asked_for():
    """Both ends of the line are crossings here (y = +/-14.142), symmetric to
    within a millionth. Comparing the two candidates by gap made the answer depend
    on that last digit and returned -14.142 for a station whose requested crossing
    is 14.142 in front of it."""
    row = fidelity.along_rows([[5.0, 0.0, 25.0]], [0.0, -1.0, 0.0], cylinder_r15,
                              tolerance=0.001, max_iterations=12)[0]
    assert row["converged"] is True
    assert row["offset_mm"] == pytest.approx(math.sqrt(225.0 - 25.0), abs=1e-3)
    assert row["offset_mm"] > 0, "a positive offset is along the axis asked for"
    assert row["restart_mm"] > 0
    assert row["along_point"][1] < 0, "the hit is in front of the start point"


def test_a_restart_that_cannot_converge_is_still_a_miss():
    """A line parallel to the target: no restart size can help, and the row must
    not pretend otherwise."""
    row = fidelity.along_rows([[0.0, 0.0, 4.0]], [0.0, 1.0, 0.0], plane_z10,
                              tolerance=0.001, max_iterations=12)[0]
    assert row["stalled"] is True
    assert row["converged"] is False
    assert row["offset_mm"] is None
    assert row["usable"] is False
    assert "restart_mm" not in row, "nothing improved, so nothing was jumped to"
    assert row["gap_mm"] == pytest.approx(6.0)


def test_the_restart_happens_once_per_station():
    """A restart that stalls again must stop, not walk the same ground forever."""
    def sideways_foot(where):
        return [float(where[0]) - 2.4, float(where[1]), 10.0], 2.4

    row = fidelity.along_rows([[0.0, 0.0, 8.0]], [0.0, 0.0, 1.0], sideways_foot,
                              tolerance=0.001, max_iterations=12)[0]
    assert row["converged"] is False
    assert row["iterations"] <= 4


def test_a_plain_miss_does_not_carry_the_stall_marker():
    """`stalled` says WHY the walk stopped. A line that simply runs out of
    iterations is a different fact and must not be labelled the same."""
    row = fidelity.along_rows([[-30.0, 20.0, 0.0]], [1.0, 0.0, 0.0], cylinder_r15,
                              tolerance=0.001, max_iterations=3)[0]
    assert row["converged"] is False
    assert not row.get("stalled"), "this one kept taking steps and ran out"


def test_the_description_no_longer_claims_a_stall_means_a_missed_line():
    from catia_mcp.tools.gsd import GSDTools

    tools = {tool["name"]: tool for tool in
             GSDTools.get_tool_definitions(GSDTools.__new__(GSDTools))}
    text = tools["catia_surface_probe"]["description"]
    assert "A VANISHED STEP IS NOT A MISSED SURFACE" in text
    assert "14.142016" in text and "14.142136" in text
    assert "restart_mm" in text


def test_calibration_declines_a_station_whose_line_has_two_crossings():
    """Moving the origin 0.5 mm back can put the walk on the OPPOSITE wall of a
    closed shell (measured on B30: base hit y=-15.000, moved hit y=+15.000, the
    offset "grew" by -29.5). The exact 0.5 mm expectation does not apply there, so
    the answer is "cannot be checked this way" - not "the reading is bad"."""
    row = fidelity.along_rows([[0.0, 0.0, 25.0]], [0.0, -1.0, 0.0], cylinder_r15,
                              tolerance=0.001, max_iterations=12)[0]
    check = fidelity.calibration_along(row, [0.0, -1.0, 0.0], cylinder_r15,
                                       0.001, 12)
    assert check["ok"] is None
    assert "more than once" in check["reason"]
    assert check["foot_drift_mm"] > 1.0, "the two hits are on different walls"


def test_calibration_still_passes_where_there_is_one_crossing():
    row = fidelity.along_rows([[0.0, 0.0, 4.0]], [0.0, 0.0, 1.0], plane_z10,
                              tolerance=1e-6)[0]
    check = fidelity.calibration_along(row, [0.0, 0.0, 1.0], plane_z10, 1e-6, 12)
    assert check["ok"] is True
    assert check["read_mm"] == pytest.approx(0.5)
    assert check["foot_drift_mm"] == pytest.approx(0.0)
