"""0.3.137 - the two 2026-11-15 reports on 0.3.136: a false "aligned", and a
shape nobody could check.

The first report found that the multi-view check added in 0.3.136 could say
"view 1 and view 2 agree on Z (0.000..0.000) ... is one part" about two views
70 mm apart: Z was zero for both because both sketches lay on the SAME PLANE, and
a plane's normal was being compared as if it were a sheet direction. It also
found the world boxes printed with min > max (my own format unpacked the six
numbers in the wrong order) and that a shared anchor cannot close a gap a real
drawing has.

The second found no way to tell a collapsed sweep from a good one, no closing
point for a loft, and two measuring tools disagreeing about whether a part had a
solid at all.

Nothing here needs CATIA; the COM half is scripts/b30_v0438_acceptance.py.
"""

from __future__ import annotations

import pytest

from catia_mcp.results import CodedError
from catia_mcp.tools.blueprint import (BlueprintTools, _anchor_value, _box_text,
                                       view_alignment)
from catia_mcp.tools.measurement import MeasurementTools


def _row(index, box, u, v, origin=(0, 0, 0)):
    return {"index": index, "box": list(box), "frame": (list(origin), list(u), list(v))}


# ── the world box: six numbers in the order the sentence reads them ────────


def test_the_world_box_prints_x_y_z_in_that_order():
    """0.3.136 printed `X 0.000..-0.000 Y 0.000..15.367 Z 23.876..0.000` for a
    15 x 24 sketch: the list is [minx,miny,minz,maxx,maxy,maxz] and it was passed
    to `format(*box)`, so min-x landed against min-y."""
    box = [0.0, 0.0, 0.0, 15.367, 23.876, 0.0]
    text = _box_text(box)
    assert text == "X 0.000..15.367 Y 0.000..23.876 Z 0.000..0.000", text


def test_a_box_never_prints_min_above_max():
    box = [-70.1181, -6.477, 0.0, -58.8292, 36.195, 0.0]
    text = _box_text(box)
    for axis in text.split():
        if ".." in axis:
            low, high = axis.split("..")
            assert float(low) <= float(high), f"{axis} reads backwards"


# ── the alignment verdict: a plane is not a direction ─────────────────────


def test_co_planar_views_are_not_aligned_by_their_normal():
    """Report N1, exactly: two views on 'xy', 70 mm apart in X."""
    left = _row(1, [0.0, 0.0, 0.0, 15.367, 23.876, 0.0], (1, 0, 0), (0, 1, 0))
    right = _row(2, [-70.118, -6.477, 0.0, -58.829, 36.195, 0.0],
                 (1, 0, 0), (0, 1, 0))
    pairs = view_alignment([left, right])
    assert len(pairs) == 1
    pair = pairs[0]
    assert pair["aligned"] is False
    axes = [item["axis"] for item in pair["shared"]]
    assert 2 not in axes, "Z is the plane's normal, not a sheet direction"
    assert pair["reason"] == "shared direction but the ranges do not line up"
    by_axis = {item["axis"]: item for item in pair["shared"]}
    assert by_axis[0]["ratio"] == 0.0, "X ranges do not overlap at all"
    assert by_axis[1]["ratio"] == pytest.approx(1.0), (
        "Y does overlap - which is why one axis agreeing is not enough")


def test_two_orthographic_views_on_one_datum_are_aligned():
    """A front view (XZ) and a top view (XY) share one sheet axis: X."""
    front = _row(1, [0.0, 0.0, 0.0, 60.0, 0.0, 40.0], (1, 0, 0), (0, 0, 1))
    top = _row(2, [0.0, 0.0, 0.0, 60.0, 25.0, 0.0], (1, 0, 0), (0, 1, 0))
    pairs = view_alignment([front, top])
    assert all(pair["aligned"] for pair in pairs), pairs
    assert [item["axis"] for item in pairs[0]["shared"]] == [0]
    assert pairs[0]["shared"][0]["ratio"] == pytest.approx(1.0)


def test_a_zero_width_range_is_not_compared():
    """A flattened view has Y 5..5 - the edge of a plane, not a range. It used to
    be compared as if it were a direction (report N1)."""
    flat = _row(1, [0.0, 5.0, 0.0, 10.0, 5.0, 0.0], (1, 0, 0), (0, 1, 0))
    other = _row(2, [0.0, 5.0, 0.0, 10.0, 5.0, 0.0], (1, 0, 0), (0, 1, 0))
    pair = view_alignment([flat, other])[0]
    assert [item["axis"] for item in pair["shared"]] == [0], (
        "X is a real range; Y is 5..5 and must not be offered as agreement")


def test_a_rotated_frame_shares_nothing_a_range_can_be_compared_on():
    tilt = 0.8660254037844387     # 30 degrees off an axis
    left = _row(1, [0, 0, 0, 10, 10, 10], (tilt, tilt, 0), (0, 0, 1))
    right = _row(2, [0, 0, 0, 10, 10, 10], (1, 0, 0), (0, 1, 0))
    pairs = view_alignment([left, right])
    assert pairs[0]["shared"] == []


def test_partly_overlapping_views_are_reported_as_such_not_as_aligned():
    left = _row(1, [0.0, 0.0, 0.0, 60.0, 40.0, 0.0], (1, 0, 0), (0, 1, 0))
    shifted = _row(2, [30.0, 0.0, 0.0, 90.0, 40.0, 0.0], (1, 0, 0), (0, 1, 0))
    pair = view_alignment([left, shifted])[0]
    assert pair["aligned"] is False
    assert pair["reason"] == "shared direction, partly overlapping"
    assert pair["shared"][0]["ratio"] == pytest.approx(0.5)


# ── per-view placement and align ───────────────────────────────────────────


def test_an_anchor_value_is_the_low_middle_or_high_end_of_a_range():
    bounds = [10.0, 20.0, 30.0, 50.0]     # x 10..30, y 20..50
    assert _anchor_value(bounds, 0, "min") == 10.0
    assert _anchor_value(bounds, 0, "center") == 20.0
    assert _anchor_value(bounds, 0, "max") == 30.0
    assert _anchor_value(bounds, 1, "center") == 35.0


def _import(arguments):
    return BlueprintTools(connection=None).execute("catia_import_drawing", arguments)


def test_placement_and_align_together_are_refused(tmp_path):
    path = tmp_path / "missing.svg"
    with pytest.raises(CodedError) as error:
        _import({"file_path": str(path), "views": [
            {"region": [0, 0, 10, 10]},
            {"region": [0, 0, 10, 10],
             "placement": {"origin_2d": [0, 0]},
             "align": {"view": 1}}]})
    assert error.value.code == "BAD_ARGUMENT"
    assert "one move" in str(error.value).lower() or "both" in str(error.value)


def test_align_must_point_at_an_earlier_view(tmp_path):
    path = tmp_path / "missing.svg"
    with pytest.raises(CodedError) as error:
        _import({"file_path": str(path), "views": [
            {"region": [0, 0, 10, 10], "align": {"view": 2}},
            {"region": [0, 0, 10, 10]}]})
    assert error.value.code == "BAD_ARGUMENT"
    assert "EARLIER" in str(error.value)


def test_align_needs_a_view_number(tmp_path):
    path = tmp_path / "missing.svg"
    with pytest.raises(CodedError) as error:
        _import({"file_path": str(path), "views": [
            {"region": [0, 0, 10, 10]},
            {"region": [0, 0, 10, 10], "align": {"axis": "x"}}]})
    assert error.value.code == "BAD_ARGUMENT"
    assert "integer 'view'" in str(error.value)


def test_align_axis_and_anchor_are_validated(tmp_path):
    path = tmp_path / "missing.svg"
    for bad in ({"view": 1, "axis": "z"}, {"view": 1, "anchor": "left"}):
        with pytest.raises(CodedError) as error:
            _import({"file_path": str(path), "views": [
                {"region": [0, 0, 10, 10]}, {"region": [0, 0, 10, 10], "align": bad}]})
        assert error.value.code == "BAD_ARGUMENT"


def test_placement_needs_two_numbers(tmp_path):
    path = tmp_path / "missing.svg"
    with pytest.raises(CodedError) as error:
        _import({"file_path": str(path), "views": [
            {"region": [0, 0, 10, 10], "placement": {"origin_2d": [1]}}]})
    assert error.value.code == "BAD_ARGUMENT"
    assert "origin_2d" in str(error.value)


def test_the_view_schema_offers_placement_and_align():
    tools = BlueprintTools(connection=None)
    schema = [d for d in tools.get_tool_definitions()
              if d["name"] == "catia_import_drawing"][0]["inputSchema"]["properties"]
    entry = schema["views"]["items"]["properties"]
    assert entry["placement"]["properties"]["origin_2d"]["maxItems"] == 2
    assert entry["align"]["properties"]["anchor"]["enum"] == ["min", "center", "max"]


# ── which body a measurement means ─────────────────────────────────────────


class _Shapes:
    def __init__(self, count: int) -> None:
        self.Count = count


class _Body:
    def __init__(self, name: str, shapes: int) -> None:
        self.Name = name
        self.Shapes = _Shapes(shapes)


class _Bodies:
    def __init__(self, items: list) -> None:
        self._items = items
        self.Count = len(items)

    def Item(self, index: int):
        return self._items[index - 1]


class _Part:
    def __init__(self, main, others=()) -> None:
        self.MainBody = main
        self.Bodies = _Bodies([main, *others])
        self.Name = "P"

    def FindObjectByName(self, name: str):
        for body in self.Bodies._items:
            if body.Name == name:
                return body
        return None


def test_the_main_body_is_measured_when_it_has_a_solid():
    tools = MeasurementTools(connection=None)
    part = _Part(_Body("零件几何体", 1))
    body, scope = tools._measurable_body(part)
    assert body.Name == "零件几何体"
    assert scope == "main_body"


def test_the_only_body_with_a_solid_is_measured_when_the_main_body_is_empty():
    """Report C3: get_inertia read 2577 mm3 while get_bounding_box said
    'no solid shape yet' for the same part - the solid was in a second body."""
    tools = MeasurementTools(connection=None)
    part = _Part(_Body("零件几何体", 0), [_Body("AirPods1_Body", 3)])
    body, scope = tools._measurable_body(part)
    assert body.Name == "AirPods1_Body"
    assert "AirPods1_Body" in scope
    assert "main body is empty" in scope


def test_two_bodies_with_solids_are_a_refusal_that_lists_them():
    tools = MeasurementTools(connection=None)
    part = _Part(_Body("零件几何体", 0), [_Body("A", 1), _Body("B", 2)])
    with pytest.raises(CodedError) as error:
        tools._measurable_body(part)
    assert error.value.code == "AMBIGUOUS_BODY"
    assert "'A'" in str(error.value) and "'B'" in str(error.value)
    assert error.value.data["bodies"][0]["name"] == "A"


def test_a_named_body_that_is_not_there_lists_the_ones_that_are():
    tools = MeasurementTools(connection=None)
    part = _Part(_Body("零件几何体", 1), [_Body("AirPods1_Body", 3)])
    with pytest.raises(CodedError) as error:
        tools._measurable_body(part, "Nope")
    assert error.value.code == "BODY_NOT_FOUND"
    assert "AirPods1_Body" in str(error.value)


def test_a_named_body_is_used_even_when_the_main_body_has_a_solid():
    tools = MeasurementTools(connection=None)
    part = _Part(_Body("零件几何体", 1), [_Body("Second", 2)])
    body, scope = tools._measurable_body(part, "Second")
    assert body.Name == "Second"
    assert "Second" in scope


# ── the loft closing point ─────────────────────────────────────────────────


def test_closing_points_must_be_a_list_of_names_or_nulls():
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    part = _Part(_Body("零件几何体", 1))
    with pytest.raises(CodedError) as error:
        tools._closing_points(part, "CloseA", ["A", "B"])
    assert error.value.code == "BAD_ARGUMENT"
    with pytest.raises(CodedError) as error:
        tools._closing_points(part, ["A", "B", "C"], ["A", "B"])
    assert "2 section" in str(error.value)


def test_a_closing_point_that_does_not_exist_is_named_with_its_section():
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    part = _Part(_Body("零件几何体", 1))
    with pytest.raises(CodedError) as error:
        tools._closing_points(part, [None, "Ghost"], ["SecA", "SecB"])
    assert error.value.code == "CLOSING_POINT_NOT_FOUND"
    assert "'SecB'" in str(error.value)


# ── what a failed name refusal leaves behind ───────────────────────────────


def test_the_name_refusal_says_whether_the_sketch_was_deleted():
    """Report N4: 0.3.135 answered "Nothing was kept" while 'TopSection_new' sat
    in the tree for the rest of the session."""
    from catia_mcp.tools.sketcher import SketcherTools

    source = __import__("inspect").getsource(SketcherTools._create_sketch)
    assert "_discard_sketch" in source
    assert "Nothing was kept" not in source
    assert "modified=not removed" in source


# ── the one shape signal a sweep owns ──────────────────────────────────────


def test_station_growth_finds_the_segment_where_the_section_outgrows_its_travel():
    """Report C1: the reply reported a 1.4 %-consistent volume for a solid that
    had folded back on itself. The stations themselves carry the warning."""
    from catia_mcp.tools.gsd import GSDTools

    stations = [(0.0, 2.4, 2.1), (0.55, 2.5, 2.2), (0.66, 3.8, 3.3),
                (0.72, 5.6, 4.9)]
    rate, start, end = GSDTools._station_growth(stations, 42.109)
    assert start == 0.66 and end == 0.72
    # (5.6 - 3.8) / ((0.72 - 0.66) * 42.109) = 0.712
    assert rate == pytest.approx(0.712, abs=0.01)


def test_station_growth_is_quiet_on_a_gentle_sweep():
    from catia_mcp.tools.gsd import GSDTools

    stations = [(0.0, 2.0, 2.0), (0.5, 2.1, 2.1), (1.0, 2.2, 2.2)]
    rate, _start, _end = GSDTools._station_growth(stations, 50.0)
    assert rate < 0.5


def test_station_growth_needs_a_length_and_two_stations():
    from catia_mcp.tools.gsd import GSDTools

    one = [(0.0, 2.0, 2.0)]
    assert GSDTools._station_growth(one, 50.0) is None
    assert GSDTools._station_growth([(0.0, 1.0, 1.0), (1.0, 2.0, 2.0)], None) is None


def test_a_boolean_remembers_its_own_type():
    """Report C6: a boolean the caller named came out `type: unknown` while the
    pad above it said `pad` - the NLS prefix table cannot read a chosen name."""
    from catia_mcp.tools.part_design import PartDesignTools

    class _Doc:
        Name = "P.CATPart"

    class _Conn:
        active_document = _Doc()

    tools = PartDesignTools(connection=_Conn())
    tools._remember_boolean(type("F", (), {"Name": "Unified"})(), "add")
    assert tools._remembered_boolean("Unified") == "boolean"
    assert tools._remembered_boolean("SomethingElse") is None
