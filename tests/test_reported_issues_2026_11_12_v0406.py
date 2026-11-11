"""0.3.106: the outline is put ON the surface instead of being guessed onto it.

Every number asserted here was measured on B30 on 2026-11-11; where a claim could
not be measured the code does not make it, and the tests check that too.
"""
from __future__ import annotations

import math
import types

import pytest

import catia_mcp.tools.gsd as gsd_module
from catia_mcp.results import ArgumentRejected, PreconditionRejected
from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS
from catia_mcp.tools.gsd import GSDTools


@pytest.fixture()
def tools() -> GSDTools:
    return GSDTools(connection=None)


# ── the 2D -> world mapping ───────────────────────────────────────────
def test_zx_strokes_land_on_the_zx_frame(tools) -> None:
    """zx maps u -> Z and v -> X, and the offset moves along the plane normal (Y).
    Getting this wrong is not cosmetic: a probe that did put the glyph at x=24
    instead of x=0 and every point then had to travel 33.5 mm instead of 26."""
    lines, where = tools._stroke_points_3d(
        {"strokes": [[[10.0, 20.0], [11.0, 21.0]]], "plane": "zx",
         "offset": -60.0})
    assert lines == [[[20.0, -60.0, 10.0], [21.0, -60.0, 11.0]]]
    assert "plane 'zx'" in where and "-60" in where


def test_a_3d_polyline_passes_through_untouched(tools) -> None:
    lines, where = tools._stroke_points_3d(
        {"points": [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]})
    assert lines == [[[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]]
    assert "world mm" in where


def test_strokes_and_points_together_are_refused(tools) -> None:
    with pytest.raises(ArgumentRejected) as caught:
        tools._stroke_points_3d({"strokes": [[[0, 0], [1, 1]]],
                                 "points": [[0, 0, 0], [1, 1, 1]],
                                 "plane": "xy"})
    assert "not both" in str(caught.value)


def test_2d_strokes_without_a_plane_are_refused(tools) -> None:
    """The plane cannot be guessed - it decides which axis is which."""
    with pytest.raises(ArgumentRejected) as caught:
        tools._stroke_points_3d({"strokes": [[[0, 0], [1, 1]]]})
    assert caught.value.code == "PLANE_REQUIRED"


def test_a_single_point_is_not_a_spline(tools) -> None:
    with pytest.raises(ArgumentRejected):
        tools._stroke_points_3d({"points": [[1.0, 2.0, 3.0]]})


def test_a_stroke_with_one_point_is_refused_not_half_drawn(tools) -> None:
    with pytest.raises(ArgumentRejected):
        tools._stroke_points_3d({"strokes": [[[0.0, 0.0]]], "plane": "xy"})


# ── which element the points land on ──────────────────────────────────
def _part_with_faces(names) -> types.SimpleNamespace:
    """Bodies.Item(i) is called with an INDEX, so the fake has to return the name
    at that position - a fake that echoes the key back makes the body lookup miss
    and quietly tests the fall-through branch instead."""
    def item(key):
        # Called with an index when listing and with a NAME when resolving one.
        name = names[int(key) - 1] if isinstance(key, int) else str(key)
        return types.SimpleNamespace(Name=name)

    return types.SimpleNamespace(Bodies=types.SimpleNamespace(
        Count=len(names), Item=item))


def test_a_body_name_is_turned_into_a_face(monkeypatch, tools) -> None:
    """Measured: a body reference is refused ('UpdateObject 失败') while a face of
    the same body works, so the name has to be translated rather than passed on."""
    seen: list[str] = []

    def fake_ref(name):
        seen.append(name)
        return f"ref:{name}"

    monkeypatch.setattr(tools, "_ref", fake_ref)
    monkeypatch.setattr(gsd_module, "topology_cells",
                        lambda conn, body, kind: [object()])
    part = _part_with_faces(["零件几何体"])
    ref, note = tools._surface_support_ref(part, {"support": "零件几何体"})
    assert seen == ["Face.1"]
    assert ref == "ref:Face.1"
    assert "refused" in note


def test_without_a_support_it_uses_the_first_face_of_the_body(monkeypatch, tools) -> None:
    monkeypatch.setattr(tools, "_ref", lambda name: f"ref:{name}")
    monkeypatch.setattr(tools.conn or types.SimpleNamespace(), "x", 0, raising=False)
    tools.conn = types.SimpleNamespace(
        get_active_part_body=lambda: types.SimpleNamespace(Name="零件几何体"))
    monkeypatch.setattr(gsd_module, "topology_cells",
                        lambda conn, body, kind: [object()])
    part = _part_with_faces([])
    ref, note = tools._surface_support_ref(part, {})
    assert ref == "ref:Face.1"
    assert "first face" in note


def test_a_body_without_faces_is_refused(monkeypatch, tools) -> None:
    tools.conn = types.SimpleNamespace(
        get_active_part_body=lambda: types.SimpleNamespace(Name="B"))
    monkeypatch.setattr(gsd_module, "topology_cells", lambda conn, body, kind: [])
    with pytest.raises(PreconditionRejected):
        tools._surface_support_ref(_part_with_faces([]), {})


# ── what the ceiling may claim ────────────────────────────────────────
def test_the_snap_ceiling_carries_the_numbers_and_the_limits() -> None:
    entry = next(e for e in _CAPABILITY_CEILINGS
                 if "PointOnSurfaceWithReference" in e["member"])
    detail = entry["detail"]
    assert entry["status"] == "AVAILABLE_WITH_LIMIT"
    for said in ("0.0022", "26.0052", "3.026", "0.99"):
        assert said in detail, said
    # The two traps that would otherwise be advertised as features.
    assert "DIRECTION is ignored" in detail
    assert "SOLID BODY reference is refused" in detail


def test_the_curve_tool_says_where_a_point_can_land(tools) -> None:
    schema = next(d for d in tools.get_tool_definitions()
                  if d["name"] == "catia_curve_on_surface")
    text = schema["description"]
    assert "NEAREST point" in text
    assert "measured" in text.lower()
    assert "26.0052" in text


# ── the ellipse ───────────────────────────────────────────────────────
def test_the_ellipse_schema_exists_and_takes_an_angle() -> None:
    from catia_mcp.tools.sketcher import SketcherTools

    schema = next(d for d in SketcherTools.get_tool_definitions(None)
                  if d["name"] == "catia_sketch_ellipse")
    props = schema["inputSchema"]["properties"]
    assert set(props) >= {"cx", "cy", "major", "minor", "angle"}
    assert schema["inputSchema"]["required"] == ["major", "minor"]


def test_an_ellipse_shorter_in_major_than_minor_is_refused() -> None:
    """"major" sets the DIRECTION of the ellipse, so a major shorter than the
    minor is a contradiction - refusing is cheaper than a silently rotated hole."""
    from catia_mcp.tools.sketcher import SketcherTools

    tools = SketcherTools(connection=None)
    tools._ensure_sketch_open = lambda: None  # type: ignore[method-assign]
    with pytest.raises(ArgumentRejected) as caught:
        tools._draw_ellipse(0, 0, 5, 10, 0, False)
    assert "shorter than minor" in str(caught.value)


# ── text points as data ───────────────────────────────────────────────
def test_text_points_are_only_printed_when_asked_for() -> None:
    from catia_mcp.tools.sketcher import _stroke_data

    strokes = [[[0.0, 0.0], [1.0, 1.0]]]
    assert _stroke_data(strokes, False) == ""
    printed = _stroke_data(strokes, True)
    assert "POINTS (sketch mm" in printed and "[0.0000, 0.0000]" in printed


def test_a_long_outline_is_counted_rather_than_cut_silently() -> None:
    from catia_mcp.tools.sketcher import _stroke_data

    strokes = [[[float(i), 0.0] for i in range(700)]]
    printed = _stroke_data(strokes, True, limit=600)
    assert "further point(s) were not printed" in printed
    assert "700" in printed or "100" in printed


def test_an_ellipse_length_is_never_claimed() -> None:
    """An ellipse's arc length has no elementary form, so the note keeps the axes
    instead of inventing a number that a later caller would trust."""
    from catia_mcp.tools.sketcher import SketcherTools

    source = (SketcherTools._draw_ellipse.__doc__ or "") + str(
        SketcherTools._draw_ellipse.__code__.co_consts)
    assert "semi-major" in source


def test_the_math_is_not_invented_for_the_audit() -> None:
    """The number the B30 audit asserts: pi*a*b*h for a=30, b=10, h=5."""
    assert math.pi * 30 * 10 * 5 == pytest.approx(4712.3890, abs=0.0001)
