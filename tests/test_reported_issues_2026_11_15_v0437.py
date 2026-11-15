"""0.3.136 - the two gaps both 2026-11-15 reports left open: a multi-view sheet
with no shared datum, and a conversion whose cost nobody could read back.

Report C2 said "multi-view cannot be aligned"; report R2 said the polyline to
spline conversion "has no deviation_mm, so the caller cannot read back what the
conversion cost". Neither is answered by a promise - both are answered by a
measurement, and what is measured has to be able to come out wrong:

  * the deviation's calibration probes are corners the curve MUST pass through,
    so a reading of 0.0000 there is evidence and 0.25 mm is a failure;
  * the multi-view alignment is one anchor for every view, and the reply compares
    the views' own WORLD boxes on each axis - which is False as soon as two views
    are put on datums that do not share a sheet direction.

The COM half is in scripts/b30_v0437_acceptance.py.
"""

from __future__ import annotations

import math

import pytest

from catia_mcp import blueprint, fidelity
from catia_mcp.results import CodedError
from catia_mcp.tools.blueprint import BlueprintTools

TWO_VIEWS = b'''<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="60mm"
viewBox="0 0 100 60"><rect x="10" y="10" width="20" height="20"/>
<circle cx="60" cy="30" r="10"/></svg>'''


def _drawing(tmp_path, region, name="two.svg") -> dict:
    path = tmp_path / name
    path.write_bytes(TWO_VIEWS)
    return blueprint.read_drawing(str(path), region=region, origin="as_drawn")


# ── where the probes go ────────────────────────────────────────────────────


def test_a_closed_outline_is_probed_on_its_sides_not_on_its_corners():
    """Corners are ON an interpolating spline by construction; sides are not."""
    square = [[0, 0], [10, 0], [10, 10], [0, 10]]
    midpoints, calibration = fidelity.outline_probes(square)
    assert midpoints == [[5.0, 0.0], [10.0, 5.0], [5.0, 10.0], [0.0, 5.0]], (
        "the closing side (last corner back to the first) is a side too")
    assert len(calibration) == 3, "corners are the calibration, not the probes"


def test_the_probe_limit_spreads_over_the_whole_outline_not_over_its_start():
    """A bulge anywhere matters, so 24 probes on a 360-point ring must reach the
    far side: taking the first 24 would cover 24 degrees of it."""
    ring = [[100.0 * math.cos(math.radians(angle)),
             100.0 * math.sin(math.radians(angle))] for angle in range(0, 360, 3)]
    midpoints, _ = fidelity.outline_probes(ring, limit=24)
    assert len(midpoints) == 24
    angles = [math.degrees(math.atan2(point[1], point[0])) % 360
              for point in midpoints]
    assert max(angles) - min(angles) > 300, (
        f"probes only covered {max(angles) - min(angles):.0f} degrees")


def test_a_probe_limit_above_the_point_count_keeps_every_side():
    triangle = [[0, 0], [3, 0], [0, 4]]
    midpoints, _ = fidelity.outline_probes(triangle, limit=24)
    assert len(midpoints) == 3


def test_outline_probes_work_in_3d_because_the_gsd_side_has_no_2d_points():
    cube_face = [[0, 0, 0], [10, 0, 0], [10, 0, 10], [0, 0, 10]]
    midpoints, calibration = fidelity.outline_probes(cube_face)
    assert midpoints[0] == [5.0, 0.0, 0.0]
    assert all(len(point) == 3 for point in calibration)


# ── the summary: a reading, not a claim ────────────────────────────────────


def test_no_probe_answered_is_not_the_same_reading_as_zero():
    summary = fidelity.deviation_summary([None, None, None])
    assert summary["measured"] is False
    assert summary["max_mm"] is None
    assert summary["unreadable"] == 3


def test_a_legitimate_zero_survives_the_summary():
    """`if max_mm:` would turn a measured 0.0000 into "nothing was measured"."""
    summary = fidelity.deviation_summary([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
    assert summary["measured"] is True
    assert summary["max_mm"] == 0.0
    assert summary["mean_mm"] == 0.0
    assert summary["calibration_ok"] is True


def test_one_unreadable_probe_does_not_hide_the_ones_that_answered():
    summary = fidelity.deviation_summary([1.5, None, 2.5])
    assert summary["read"] == 2
    assert summary["unreadable"] == 1
    assert summary["max_mm"] == 2.5


def test_calibration_is_the_line_between_a_reading_and_a_guess():
    assert fidelity.deviation_summary([30.0], [0.0004])["calibration_ok"] is True
    assert fidelity.deviation_summary([30.0], [0.25])["calibration_ok"] is False
    assert fidelity.deviation_summary([30.0], [])["calibration_ok"] is None


# ── the sentence the caller reads ──────────────────────────────────────────


def test_the_sentence_quotes_the_maximum_and_its_evidence():
    summary = fidelity.deviation_summary([33.1387, 12.0], [0.0, 0.0, 0.0])
    text = fidelity.deviation_sentence(summary)
    assert "33.1387" in text
    assert "calibrated" in text and "NOT calibrated" not in text


def test_a_failed_calibration_forbids_quoting_the_maximum():
    summary = fidelity.deviation_summary([33.1387], [0.25])
    text = fidelity.deviation_sentence(summary)
    assert "NOT calibrated" in text
    assert "do not quote" in text


def test_a_mean_of_zero_is_printed_rather_than_dropped():
    summary = fidelity.deviation_summary([0.0], [0.0])
    assert "mean 0.0000 mm" in fidelity.deviation_sentence(summary)


def test_an_error_is_reported_as_a_missing_number_and_says_what_to_do():
    text = fidelity.deviation_sentence({"error": "方法 UpdateObject 失败",
                                        "samples": 0})
    assert "could NOT be measured" in text
    assert "catia_gsd_spline" in text


def test_surviving_probe_points_are_named_as_clutter():
    summary = fidelity.deviation_summary([1.0], [0.0])
    summary["left_behind"] = 2
    assert "still in the tree" in fidelity.deviation_sentence(summary)


# ── one anchor for every view ──────────────────────────────────────────────


def test_two_views_keep_their_relative_position_through_one_anchor(tmp_path):
    """The whole point of C2: `bbox` per view would put both at (0,0).

    The regions are in DRAWING mm, and an SVG's y comes back flipped (y up):
    the rectangle is 10..30 x -30..-10 and the circle 40..60 x -40..-20, which
    is what `read_drawing` reports for this file with no filter at all.
    """
    first = _drawing(tmp_path, [0, -60, 40, 0])
    second = _drawing(tmp_path, [40, -60, 100, 0])
    anchor = (min(blueprint.contour_bbox(c)[0]
                  for drawing in (first, second) for c in drawing["contours"]),
              min(blueprint.contour_bbox(c)[1]
                  for drawing in (first, second) for c in drawing["contours"]))
    assert anchor == (10.0, -40.0)
    for drawing in (first, second):
        blueprint.move_to_anchor(drawing, anchor)
    boxes = [blueprint.contour_bbox(drawing["contours"][0])
             for drawing in (first, second)]
    assert boxes[0] == pytest.approx((0.0, 10.0, 20.0, 30.0))
    assert boxes[1] == pytest.approx((40.0, 0.0, 60.0, 20.0)), (
        "the circle's sheet box is x 50..70, y -40..-20: 40 mm right of the "
        "rectangle's left edge and 10 mm below its bottom, and ONE anchor "
        "keeps both offsets - per-view `bbox` would have put it at (0,0)")


def test_move_to_anchor_records_the_shift_it_applied(tmp_path):
    drawing = _drawing(tmp_path, [0, -60, 40, 0])
    shift = blueprint.move_to_anchor(drawing, (10.0, -40.0))
    assert shift == [-10.0, 40.0]
    assert drawing["origin_shift_mm"] == [-10.0, 40.0]
    assert drawing["origin"] == "anchor"


def test_alignment_is_false_when_two_views_do_not_share_an_axis():
    left = [0.0, 0.0, 0.0, 60.0, 40.0, 0.0]
    shifted = [0.0, 0.0, 0.0, 60.0, 40.0, 25.0]
    moved = [5.0, 0.0, 0.0, 65.0, 25.0, 0.0]
    assert fidelity.aligned(left, shifted, 0) is True
    assert fidelity.aligned(left, moved, 0) is False


# ── arguments are refused before the file is opened ────────────────────────


def _import(arguments: dict):
    return BlueprintTools(connection=None).execute("catia_import_drawing", arguments)


@pytest.mark.parametrize("views", [{"page": 1}, "two", 3, []])
def test_views_must_be_a_non_empty_list(views, tmp_path):
    path = tmp_path / "missing.svg"          # never opened: the shape is wrong
    with pytest.raises(CodedError) as error:
        _import({"file_path": str(path), "views": views})
    assert error.value.code == "BAD_ARGUMENT"
    assert "Nothing was read" in str(error.value)


def test_a_single_view_argument_beside_views_is_refused_not_guessed(tmp_path):
    path = tmp_path / "missing.svg"
    with pytest.raises(CodedError) as error:
        _import({"file_path": str(path),
                 "region": [0, 0, 10, 10],
                 "views": [{"page": 1, "region": [0, 0, 10, 10]}]})
    assert error.value.code == "BAD_ARGUMENT"
    assert "region" in str(error.value)
    assert "belong to one view" in str(error.value)


def test_bbox_origin_is_refused_with_views_because_it_would_unalign_them(tmp_path):
    path = tmp_path / "missing.svg"
    with pytest.raises(CodedError) as error:
        _import({"file_path": str(path), "origin": "bbox",
                 "views": [{"region": [0, 0, 10, 10]}]})
    assert error.value.code == "BAD_ARGUMENT"
    assert "destroys the alignment" in str(error.value)


def test_as_drawn_origin_is_accepted_with_views_because_it_is_the_same_frame(tmp_path):
    """It would be a trap if the honest value were refused: `as_drawn` is what
    a multi-view import does by default, so naming it must not be an error."""
    path = tmp_path / "missing.svg"
    with pytest.raises(CodedError) as error:
        _import({"file_path": str(path), "origin": "as_drawn",
                 "views": [{"region": [0, 0, 10, 10]}]})
    # It gets as far as the FILE, which is the proof the arguments passed.
    assert error.value.code == "FILE_NOT_FOUND"


def test_an_anchor_must_be_two_numbers(tmp_path):
    path = tmp_path / "missing.svg"
    with pytest.raises(CodedError) as error:
        _import({"file_path": str(path), "anchor": [1, 2, 3],
                 "views": [{"region": [0, 0, 10, 10]}]})
    assert error.value.code == "BAD_ARGUMENT"
    assert "two numbers" in str(error.value)


def test_a_view_entry_that_is_not_an_object_is_refused_by_its_index(tmp_path):
    path = tmp_path / "missing.svg"
    with pytest.raises(CodedError) as error:
        _import({"file_path": str(path),
                 "views": [{"region": [0, 0, 10, 10]}, "the top one"]})
    assert error.value.code == "BAD_ARGUMENT"
    assert "views[2]" in str(error.value)


# ── the schema says what the code accepts ──────────────────────────────────


def _schema() -> dict:
    tools = BlueprintTools(connection=None)
    return [d for d in tools.get_tool_definitions()
            if d["name"] == "catia_import_drawing"][0]["inputSchema"]["properties"]


def test_the_import_schema_offers_views_anchor_and_deviation():
    schema = _schema()
    assert schema["views"]["type"] == "array"
    assert "anchor" in schema and schema["anchor"]["maxItems"] == 2
    assert schema["deviation"]["default"] is True
    # Each view must be able to say where it goes, or the alignment is nobody's
    # decision.
    entry = schema["views"]["items"]["properties"]
    for key in ("page", "region", "plane", "offset", "name"):
        assert key in entry, f"a view cannot set '{key}'"


def test_the_gsd_spline_schema_offers_deviation():
    from catia_mcp.tools.gsd import GSDTools

    tools = [d for d in GSDTools(connection=None).get_tool_definitions()
             if d["name"] == "catia_gsd_spline"][0]
    assert tools["inputSchema"]["properties"]["deviation"]["default"] is True


def test_the_deviation_description_states_both_measurements_it_is_calibrated_on():
    """The numbers in a description are claims: these two came from B30 and must
    match the probe script, or the description is fiction."""
    from catia_mcp.tools.gsd import GSDTools

    tools = [d for d in GSDTools(connection=None).get_tool_definitions()
             if d["name"] == "catia_gsd_spline"][0]
    text = tools["inputSchema"]["properties"]["deviation"]["description"]
    assert "25.6325" in text, "the 3D spline's side-midpoint reading"
    assert "33.1387" in text, "the 2D sketch spline's reading of the SAME corners"
    assert "differ" in text, "and the reason they are both quoted"


def test_the_multi_view_description_promises_the_axis_that_was_measured():
    tools = BlueprintTools(connection=None)
    definition = [d for d in tools.get_tool_definitions()
                  if d["name"] == "catia_import_drawing"][0]
    assert "MULTI-VIEW" in definition["description"]
    assert "0..60" in definition["description"]


def test_the_sentence_is_built_from_the_reading_not_typed_in():
    """A maximum and a mean that only ever appear together prove both come from
    the summary; the GSD side and the import side share this one builder."""
    summary = fidelity.deviation_summary([2.0, 4.0], [0.0])
    sentence = fidelity.deviation_sentence(summary)
    assert "max 4.0000 mm" in sentence
    assert "mean 3.0000 mm" in sentence
    assert "2 side-midpoint probe(s)" in sentence
