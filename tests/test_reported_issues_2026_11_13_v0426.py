# -*- coding: utf-8 -*-
"""0.3.124 - section chains from a file, and a radius that changes along the spine.

Both come from one acceptance report (2026-11-13, P1-4), which had to paste 380
coordinates into a call for a 17-section handle dome and then 1 440 more for a
15-section flare body. Neither is a new CATIA capability:

  * ``catia_gsd_splines(source_file=...)`` reads a JSON or CSV file into the SAME
    entries list the inline form takes, so it cannot accept what that rejects;
  * ``catia_solid_sweep_variable`` generates one circle per station and runs ONE
    ``catia_solid_loft`` through them.

Everything here is offline. The B30 numbers these pin (measured 2026-11-14) are:
a 50 mm line with r 10 -> 7 -> 4 at t=0/0.5/1 gives 8168.2 mm3 against a ruled
sum of 8168.1; the same line with [8, 8, 8] gives 10052.7 against pi*64*50 =
10053.1; a bent 55.642 mm spline with r 6 -> 9 -> 3 gives 10119.1, which is
+20.60 % above the ruled sum - the reason the reply does not call that a failure.
"""

from __future__ import annotations

import json
import math

import pytest

from catia_mcp.tools.gsd import GSDTools


# ── the file entrance ────────────────────────────────────────────────────────


def test_a_json_object_carries_the_entries(tmp_path):
    path = tmp_path / "sections.json"
    path.write_text(json.dumps({"entries": [
        {"name": "S01", "points": [[0, 0, 0], [10, 0, 0]], "closed": True}]}),
        encoding="utf-8")
    entries, note = GSDTools._entries_from_file(str(path))
    assert entries == [{"name": "S01", "points": [[0, 0, 0], [10, 0, 0]],
                        "closed": True}]
    assert "1 entries" in note and "JSON" in note


def test_a_bare_point_list_is_ONE_section_not_one_per_point(tmp_path):
    """[[0,0,0],[5,0,0]] has two readings; the shape picks one.

    Treating it as two sections of one point each would turn a 12-point ring into
    12 splines and still answer ok - the kind of silent wrong answer this whole
    project keeps finding.
    """
    path = tmp_path / "one.json"
    path.write_text(json.dumps([[0, 0, 0], [5, 0, 0]]), encoding="utf-8")
    entries, note = GSDTools._entries_from_file(str(path))
    assert len(entries) == 1
    assert entries[0]["points"] == [[0, 0, 0], [5, 0, 0]]
    assert "1 section" in note


def test_a_nested_list_is_several_sections(tmp_path):
    path = tmp_path / "two.json"
    path.write_text(json.dumps([[[0, 0, 0], [5, 0, 0]], [[0, 0, 9], [5, 0, 9]]]),
                    encoding="utf-8")
    entries, _ = GSDTools._entries_from_file(str(path))
    assert len(entries) == 2
    assert all(len(entry["points"]) == 2 for entry in entries)


def test_csv_groups_rows_by_name_and_by_blank_line(tmp_path):
    path = tmp_path / "rings.csv"
    path.write_text(
        "# two stations\n"
        "S01, 0, 0, 0\n"
        "S01, 10, 0, 0\n"
        "\n"
        "0, 0, 30\n"
        "10, 0, 30\n",
        encoding="utf-8")
    entries, note = GSDTools._entries_from_file(str(path))
    assert len(entries) == 2
    assert entries[0]["name"] == "S01"
    assert entries[0]["points"] == [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]]
    assert "name" not in entries[1], "an unnamed block stays unnamed"
    assert "CSV" in note


def test_csv_marks_a_ring_closed_when_it_repeats_its_first_point(tmp_path):
    """Without this a CSV ring builds as an OPEN spline, and a loft refuses it.

    Measured on B30 2026-11-14: three 18-point rings read as "18 point(s), open"
    and catia_solid_loft answered 方法 Update 失败. CSV has no boolean column, so
    the repeat IS the flag.
    """
    path = tmp_path / "ring.csv"
    path.write_text("R01, 10, 0, 0\nR01, 0, 10, 0\nR01, -10, 0, 0\nR01, 10, 0, 0\n",
                    encoding="utf-8")
    entries, note = GSDTools._entries_from_file(str(path))
    assert entries[0].get("closed") is True
    assert len(entries[0]["points"]) == 4, "the repeat is kept for _splines to drop"
    assert "1 closed" in note


def test_csv_closed_directive_marks_every_section(tmp_path):
    path = tmp_path / "ring.csv"
    path.write_text("# closed\nR01, 10, 0, 0\nR01, 0, 10, 0\nR01, -10, 0, 0\n",
                    encoding="utf-8")
    entries, note = GSDTools._entries_from_file(str(path))
    assert entries[0].get("closed") is True
    assert "1 closed" in note


def test_csv_closed_false_turns_the_directive_off(tmp_path):
    path = tmp_path / "open.csv"
    path.write_text("# closed: false\nR01, 10, 0, 0\nR01, 0, 10, 0\n",
                    encoding="utf-8")
    entries, _ = GSDTools._entries_from_file(str(path))
    assert "closed" not in entries[0]


def test_a_missing_file_is_named_not_guessed(tmp_path):
    with pytest.raises(Exception) as caught:
        GSDTools._entries_from_file(str(tmp_path / "nope.json"))
    assert getattr(caught.value, "code", "") == "FILE_NOT_FOUND"


def test_a_wrong_suffix_is_refused(tmp_path):
    path = tmp_path / "sections.xlsx"
    path.write_text("0,0,0", encoding="utf-8")
    with pytest.raises(Exception) as caught:
        GSDTools._entries_from_file(str(path))
    assert getattr(caught.value, "code", "") == "UNSUPPORTED_FILE"


def test_bad_rows_and_bad_json_say_which_line(tmp_path):
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("0,0\n", encoding="utf-8")
    with pytest.raises(Exception) as caught:
        GSDTools._entries_from_file(str(bad_csv))
    assert getattr(caught.value, "code", "") == "BAD_FILE"
    assert "line 1" in str(caught.value)

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{oops", encoding="utf-8")
    with pytest.raises(Exception) as caught:
        GSDTools._entries_from_file(str(bad_json))
    assert getattr(caught.value, "code", "") == "BAD_FILE"
    assert "not valid JSON" in str(caught.value)


def test_an_empty_file_is_refused(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("# nothing here\n\n", encoding="utf-8")
    with pytest.raises(Exception) as caught:
        GSDTools._entries_from_file(str(empty))
    assert getattr(caught.value, "code", "") == "BAD_FILE"


def test_a_huge_file_is_refused_before_it_is_read(tmp_path, monkeypatch):
    monkeypatch.setattr(GSDTools, "_SPLINE_FILE_MAX_BYTES", 10)
    path = tmp_path / "big.csv"
    path.write_text("0,0,0\n10,0,0\n", encoding="utf-8")
    with pytest.raises(Exception) as caught:
        GSDTools._entries_from_file(str(path))
    assert getattr(caught.value, "code", "") == "FILE_TOO_LARGE"


def test_entries_and_source_file_together_are_refused(tmp_path):
    """Which one wins would be a coin toss, and the loser would look used."""
    path = tmp_path / "sections.json"
    path.write_text(json.dumps([[0, 0, 0], [1, 0, 0]]), encoding="utf-8")
    tools = GSDTools(connection=None)
    with pytest.raises(Exception) as caught:
        tools._splines({"entries": [{"points": [[0, 0, 0], [1, 0, 0]]}],
                        "source_file": str(path)})
    assert getattr(caught.value, "code", "") == "ENTRIES_OR_FILE"


# ── the station table ────────────────────────────────────────────────────────


def test_pairs_and_bare_radii_are_the_same_table():
    # A station is (t, rx, ry) since 0.3.126, not (t, radius): the 2026-11-14
    # report's P0-4 needs an elliptic station, and a circle is the case rx == ry.
    # These two assertions were `[(0.0, 10.0), ...]` when this file was written,
    # and they were right then - the table had one radius because every station
    # was a circle. The change is in the SHAPE of the table, not in what a bare
    # [r0, r1, ...] means.
    assert GSDTools._sweep_stations([[0, 10], [0.5, 7], [1, 4]]) == [
        (0.0, 10.0, 10.0), (0.5, 7.0, 7.0), (1.0, 4.0, 4.0)]
    assert GSDTools._sweep_stations([10, 7, 4]) == [
        (0.0, 10.0, 10.0), (0.5, 7.0, 7.0), (1.0, 4.0, 4.0)]


def test_an_elliptic_station_carries_its_two_semi_axes():
    assert GSDTools._sweep_stations([[0, 5.4, 4.8], [1, 2.7, 2.4]]) == [
        (0.0, 5.4, 4.8), (1.0, 2.7, 2.4)]
    # The forms mix where they agree on t: a round stem that becomes a flat
    # section is the shape this was added for.
    assert GSDTools._sweep_stations([[0, 8], [0.5, 5, 3], [1, 2, 1.5]]) == [
        (0.0, 8.0, 8.0), (0.5, 5.0, 3.0), (1.0, 2.0, 1.5)]


@pytest.mark.parametrize("bad, code", [
    ([[0, 5.4, -4.8], [1, 2.7, 2.4]], "RADIUS_OUT_OF_RANGE"),
    ([[0, 5.4, 4.8, 1], [1, 2.7, 2.4, 1]], "BAD_SECTIONS"),
])
def test_bad_elliptic_stations_are_refused(bad, code):
    with pytest.raises(Exception) as caught:
        GSDTools._sweep_stations(bad)
    assert getattr(caught.value, "code", "") == code


def test_the_elliptic_volume_formula_agrees_with_the_cone_formula():
    # rx == ry must collapse to pi*h/3*(r0^2 + r0*r1 + r1^2) exactly, because that
    # is the number 0.3.124 compared against the documented 8 -> 16 over 40 mm
    # case (18765.8 mm3 measured). If the two disagreed, the circle path would
    # have been silently re-baselined by adding ellipse support.
    stations = GSDTools._sweep_stations([[0, 8], [1, 16]])
    cone = math.pi * 40.0 / 3.0 * (8 * 8 + 8 * 16 + 16 * 16)
    assert abs(GSDTools._swept_volume(stations, 40.0) - cone) < 1e-9

    # And an elliptic pair against its own Simpson value: f = rx*ry is quadratic
    # along a segment, so 1099.56 is analytic, not fitted.
    ellipse = GSDTools._sweep_stations([[0, 10, 6], [1, 5, 3]])
    assert abs(GSDTools._swept_volume(ellipse, 10.0) - math.pi * 350.0) < 1e-9


def test_the_axes_are_carried_and_a_ninety_degree_bend_refuses():
    # Straight ahead: the axis is unchanged.
    carried = GSDTools._carry_frame([1.0, 0.0, 0.0], [0.0, 0.0, 1.0])
    assert carried == [1.0, 0.0, 0.0]
    # A bend: the axis stays in the new plane, and stops being the old vector.
    bent = GSDTools._carry_frame([1.0, 0.0, 0.0], [0.7071068, 0.7071068, 0.0])
    assert abs(sum(a * b for a, b in zip(bent, [0.7071068, 0.7071068, 0.0]))) < 1e-6
    # 90 degrees: no continuous answer, and the caller says so instead of guessing.
    assert GSDTools._carry_frame([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) is None


def test_the_ellipse_ring_lies_on_the_ellipse():
    ring = GSDTools._ellipse_ring([0.0, 0.0, 0.0], [1.0, 0.0, 0.0],
                                  [0.0, 1.0, 0.0], 10.0, 6.0, count=16)
    assert len(ring) == 16
    for x, y, z in ring:
        assert abs((x / 10.0) ** 2 + (y / 6.0) ** 2 - 1.0) < 1e-12
        assert z == 0.0
    # The ring's own extents ARE the semi-axes - the point of building it from
    # points on the curve rather than sampling a bounding box.
    assert abs(max(p[0] for p in ring) - 10.0) < 1e-9
    assert abs(max(p[1] for p in ring) - 6.0) < 1e-9


@pytest.mark.parametrize("bad, code", [
    ([[0, 10]], "BAD_SECTIONS"),
    ([[0, 10], [1, 4], [0.5, 2]], "T_NOT_INCREASING"),
    ([[0.2, 10], [0.1, 4]], "T_NOT_INCREASING"),
    ([[0, 10], [1, -1]], "RADIUS_OUT_OF_RANGE"),
    ([[0, 10], [2, 4]], "T_OUT_OF_RANGE"),
    ([10, [0.5, 7]], "BAD_SECTIONS"),
    ([[0, 10], [1]], "BAD_SECTIONS"),
])
def test_bad_station_tables_are_refused_with_a_reason(bad, code):
    with pytest.raises(Exception) as caught:
        GSDTools._sweep_stations(bad)
    assert getattr(caught.value, "code", "") == code


def test_the_station_ceiling_exists():
    with pytest.raises(Exception) as caught:
        GSDTools._sweep_stations([1.0] * (GSDTools._SWEEP_MAX_STATIONS + 1))
    assert getattr(caught.value, "code", "") == "TOO_MANY_STATIONS"


# ── the tool surface ─────────────────────────────────────────────────────────


def test_the_new_tool_and_its_arguments_exist():
    definitions = {item["name"]: item
                   for item in GSDTools(connection=None).get_tool_definitions()}
    assert "catia_solid_sweep_variable" in definitions
    schema = definitions["catia_solid_sweep_variable"]["inputSchema"]
    assert schema["required"] == ["spine", "sections"]
    # major_axis joined in 0.3.126 with elliptic stations: rx has to run along a
    # direction the caller can name, or the ellipse's orientation is CATIA's.
    assert set(schema["properties"]) == {"spine", "sections", "name", "body",
                                         "helpers", "major_axis"}

    splines = definitions["catia_gsd_splines"]["inputSchema"]
    assert "source_file" in splines["properties"]
    assert splines["required"] == [], (
        "entries and source_file are alternatives, so neither is required")


def test_the_sweep_says_the_ruled_sum_only_holds_on_a_straight_spine():
    """The +20.60 % measured on a bent spine must not be called a failure."""
    definitions = {item["name"]: item
                   for item in GSDTools(connection=None).get_tool_definitions()}
    text = definitions["catia_solid_sweep_variable"]["description"]
    assert "OPEN" in text and "circle and a CLOSED spline do not" in text
    assert "catia_solid_loft" in text
