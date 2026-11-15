"""0.3.135 - the 2026-11-15 reports: one curve, a frame plane, and names that say
what they are.

The two reports are the same session read twice: the first concluded "MCP has no
way to turn an imported polyline into one curve", the second found that
`catia_gsd_spline(element=<imported sketch>)` had always done it. Both are right
about the real defect - the capability existed and NOTHING said so, starting with
a reply that reported `closed: true, free_ends: 0` for a contour a loft cannot
use.

What is tested here is the part that does not need CATIA: the contour metadata
that now answers "can this be lofted?", the argument validation, the name
refusal, and the diagnosis that must list real names instead of pointing at a
tool. The COM half is in scripts/b30_v0436_audit.py (12/12 on B30).
"""

from __future__ import annotations

import json

import pytest

from catia_mcp import blueprint
from catia_mcp.results import ArgumentRejected, CodedError
from catia_mcp.tools.blueprint import BlueprintTools


# ── the contour metadata: closed is not the same question as loftable ───────


def _square() -> dict:
    return {"pieces": [
        {"t": "line", "p1": (0, 0), "p2": (10, 0)},
        {"t": "line", "p1": (10, 0), "p2": (10, 10)},
        {"t": "line", "p1": (10, 10), "p2": (0, 10)},
        {"t": "line", "p1": (0, 10), "p2": (0, 0)},
    ], "closed": True}


def test_a_closed_outline_of_four_lines_is_four_curves_not_one():
    """The number that decides pad vs loft, and it is not `elements` for a circle."""
    assert blueprint.curve_count(_square()) == 4
    assert blueprint.curve_count({"pieces": [{"t": "circle", "c": (0, 0), "r": 5}]}) == 1
    assert blueprint.curve_count({"pieces": [{"t": "ellipse", "c": (0, 0),
                                              "rx": 3, "ry": 2}]}) == 1


def test_a_circle_and_its_four_arcs_are_both_one_curve():
    four_arcs = {"pieces": [{"t": "arc", "c": (0, 0), "r": 5, "a0": 0, "a1": 90,
                             "ccw": True}] * 4}
    assert blueprint.curve_count(four_arcs) == 4, (
        "four arcs are four curves even when they form one circle - that is the "
        "distinction a loft cares about")


def test_the_spline_chain_drops_the_repeated_closing_point():
    """A closed spline joins itself; a repeated first point is a zero-length leg."""
    chain = blueprint.spline_points(_square())
    assert chain == [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]


def test_the_corner_report_names_the_turn_a_spline_would_round():
    report = blueprint.corner_report(blueprint.spline_points(_square()))
    assert report["sharpest_turn_deg"] == pytest.approx(90.0)
    assert report["corner_count"] == 4
    # A 24-point circle has no corner worth warning about.
    circle = {"pieces": [{"t": "circle", "c": (0, 0), "r": 5}]}
    smooth = blueprint.corner_report(blueprint.spline_points(circle))
    assert smooth["corner_count"] == 0


# ── read_drawing answers the loft question ─────────────────────────────────


SVG = b'''<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="60mm"
viewBox="0 0 100 60"><rect x="10" y="10" width="20" height="20"/>
<circle cx="60" cy="30" r="10"/></svg>'''


def _read(tmp_path, name: str = "two.svg") -> dict:
    path = tmp_path / name
    path.write_bytes(SVG)
    reply = BlueprintTools(connection=None).execute(
        "catia_read_drawing", {"file_path": str(path), "origin": "as_drawn"})
    return json.loads(reply)


def test_every_contour_says_how_many_curves_it_is_and_whether_a_loft_can_use_it(tmp_path):
    report = _read(tmp_path)
    by_shape = {}
    for contour in report["contours"]:
        # By POSITION, not by size: the square is 20x20 and the circle is 20
        # across, so a width test cannot tell them apart (it picked the circle
        # twice on the first run).
        by_shape["square" if contour["bbox_mm"][0] < 40 else "circle"] = contour
    assert by_shape["square"]["curves"] == 4
    assert by_shape["square"]["loft_ready"] is False
    assert by_shape["square"]["closed"] is True, (
        "both facts are true at once - that is the whole point")
    assert by_shape["circle"]["curves"] == 1
    assert by_shape["circle"]["loft_ready"] is True


def test_the_page_wide_curve_statistics_are_labelled_as_page_wide(tmp_path):
    """'775 arcs fitted but the profile is 110 lines' read like a bug twice."""
    report = _read(tmp_path)
    assert "page_level_stats" in report
    assert report["page_level_stats"] == report["stats"]


# ── arguments are refused before anything is read or drawn ─────────────────


@pytest.mark.parametrize("mode", ["as_one", "SPLINES", 3, ""])
def test_an_unknown_profile_mode_is_refused_before_the_file_is_opened(mode, tmp_path):
    path = tmp_path / "x.svg"
    path.write_bytes(SVG)
    with pytest.raises(ArgumentRejected) as error:
        BlueprintTools(connection=None).execute(
            "catia_import_drawing", {"file_path": str(path), "profile_mode": mode})
    assert "'segments' or 'spline'" in str(error.value)


# ── the import schema promises what create_sketch accepts ──────────────────


def test_the_import_accepts_a_frame_plane_because_create_sketch_does():
    tools = BlueprintTools(connection=None)
    import_tool = [d for d in tools.get_tool_definitions()
                   if d["name"] == "catia_import_drawing"][0]
    plane = import_tool["inputSchema"]["properties"]["plane"]
    assert {"type": "string"} in plane["anyOf"]
    assert {"type": "object"} in plane["anyOf"], (
        "the description said 'the same values catia_create_sketch takes' while "
        "the schema refused an object - reported 2026-11-15, C2")


def test_the_import_offers_the_two_profile_modes():
    tools = BlueprintTools(connection=None)
    import_tool = [d for d in tools.get_tool_definitions()
                   if d["name"] == "catia_import_drawing"][0]
    mode = import_tool["inputSchema"]["properties"]["profile_mode"]
    assert mode["enum"] == ["segments", "spline"]
    assert mode["default"] == "segments"


# ── which contour becomes what ─────────────────────────────────────────────


class _Sketcher:
    """Records the call the import makes for each contour."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def _draw_circle(self, cx, cy, radius):
        self.calls.append(("circle", (cx, cy, radius)))
        return "circle"

    def _draw_ellipse(self, cx, cy, rx, ry, angle, construction):
        self.calls.append(("ellipse", (cx, cy, rx, ry)))
        return "ellipse"

    def _draw_spline(self, points, closed=False):
        self.calls.append(("spline", (len(points), closed)))
        return "spline"

    def _draw_profile(self, segments, tolerance=0.05):
        self.calls.append(("profile", len(segments)))
        return "profile"


def test_spline_mode_leaves_a_circle_alone_and_splines_the_polygon():
    tools = BlueprintTools(connection=None)
    sketcher = _Sketcher()
    stats: dict = {"mode": "spline", "curves": 0, "splined": 0, "points": 0,
                   "kept_primitives": 0, "sharp_corners": 0, "sharpest": None}
    tools._draw_contour(sketcher, _square(), {"profile_mode": "spline"},
                        "spline", stats)
    tools._draw_contour(sketcher, {"pieces": [{"t": "circle", "c": (1, 2), "r": 3}]},
                        {"profile_mode": "spline"}, "spline", stats)
    assert sketcher.calls[0] == ("spline", (4, True))
    assert sketcher.calls[1] == ("circle", (1, 2, 3)), (
        "a circle is already ONE curve - turning it into a spline would replace it")
    assert stats["curves"] == 2 and stats["splined"] == 1
    assert stats["kept_primitives"] == 1


def test_segments_mode_still_draws_each_piece_with_its_own_primitive():
    tools = BlueprintTools(connection=None)
    sketcher = _Sketcher()
    stats: dict = {"mode": "segments", "curves": 0}
    tools._draw_contour(sketcher, _square(), {}, "segments", stats)
    assert sketcher.calls == [("profile", 4)]
    assert stats["curves"] == 4


# ── C4: a parameter that is not there is a coded refusal ───────────────────


class _Parameters:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    @property
    def Count(self):
        return len(self._names)

    def Item(self, key):
        if isinstance(key, int):
            return type("P", (), {"Name": self._names[key - 1], "Value": 1.0})()
        if key not in self._names:
            raise RuntimeError("(-2147352567, '发生意外。', (0, 'CATIAParameters', "
                               "'方法 Item 失败', None, 0, -2147467259), None)")
        return type("P", (), {"Name": key, "Value": 1.0})()


class _Conn:
    def __init__(self) -> None:
        self.part = type("Part", (), {"Parameters": _Parameters(
            ["Part1\\零件编号", "Part1\\StemLength"])})()

    def ensure_connected(self):
        return "ok"

    def get_active_part(self):
        return self.part


def test_a_missing_parameter_names_the_ones_that_exist():
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(_Conn())
    with pytest.raises(CodedError) as error:
        tools._set_parameter("NoSuchParam", 1)
    assert error.value.code == "PARAMETER_NOT_FOUND"
    text = str(error.value)
    assert "StemLength" in text, "the names in use are the useful part"
    assert "catia_get_parameters" in text


def test_a_missing_parameter_suggests_the_closest_name():
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(_Conn())
    with pytest.raises(CodedError) as error:
        tools._set_parameter("stemlength", 1)
    assert "StemLength" in str(error.value)
    assert error.value.data["closest"] == ["Part1\\StemLength"]
