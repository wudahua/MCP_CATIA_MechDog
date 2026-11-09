"""0.3.87: the three items left open by the 2026-11-09 reports.

  * ``catia_gsd_fill`` reported "Fill surface created" for an open boundary while
    the ``Edge.N`` route was refused for not being closed - two routes, two
    answers, and the silent one left a zero-area face that only showed up when a
    later thickening produced non-manifold edges
  * a sketch on a plane built by ``catia_gsd_plane_angle`` had no frame, so its
    2D coordinates could never be turned back into global XYZ - the frame is now
    reconstructed from the plane's construction and CHECKED by measurement
  * ``catia_sketch_copy`` did not say where the copy landed, so the reporter had
    to derive 2*r*sin(A/2) by hand to prove it
"""
from __future__ import annotations

import inspect
import types

import pytest

from catia_mcp.sketch_frames import (BASES, frame_of, plane_angle_frame,
                                     point_world, rotate)


# ── the one frame table ──────────────────────────────────────────────────


def test_the_frame_table_has_exactly_one_home():
    """Three callers used to carry their own copy of this table."""
    from catia_mcp.tools import part_design

    assert part_design._SKETCH_BASES is BASES


def test_a_rotated_plane_keeps_the_axis_direction_and_moves_the_other():
    """zx rotated 72 degrees about z: H stays Z, V becomes the radial at 72."""
    u, v = plane_angle_frame("zx", (0.0, 0.0, 1.0), 72.0)
    assert u == pytest.approx((0.0, 0.0, 1.0))
    assert v == pytest.approx((0.309016994, 0.951056516, 0.0), abs=1e-8)


def test_the_reporters_own_numbers_come_out_of_the_reconstruction():
    """They measured 7.0534 mm for a copy 72 degrees round at r=6 (theory 7.053).

    The point (h=9.5, v=6) on zx is (6, 0, 9.5); 72 degrees round it is
    (6cos72, 6sin72, 9.5), and the chord between them is what they measured.
    """
    u, v = plane_angle_frame("zx", (0.0, 0.0, 1.0), 72.0)
    rotated = {"base": "frame", "origin": [0.0, 0.0, 0.0],
               "u": list(u), "v": list(v)}
    source = point_world({"base": "zx", "distance": 0.0}, 9.5, 6.0)
    copy = point_world(rotated, 9.5, 6.0)
    assert source == pytest.approx([6.0, 0.0, 9.5], abs=1e-9)
    assert copy == pytest.approx([6 * 0.309016994, 6 * 0.951056516, 9.5],
                                 abs=1e-6)
    chord = sum((a - b) ** 2 for a, b in zip(copy, source)) ** 0.5
    assert chord == pytest.approx(6 * 2 * 0.587785252, abs=1e-6)  # 2r sin(36)


def test_rotating_about_the_planes_own_normal_moves_nothing():
    """The trap that cost a session in 0.3.84: a plane rotated about its normal."""
    assert rotate((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), 0.0) == pytest.approx(
        (1.0, 0.0, 0.0))


def test_an_unknown_base_gives_no_frame_rather_than_a_guess():
    assert plane_angle_frame("my plane", (0.0, 0.0, 1.0), 30.0) is None
    assert frame_of({"base": "angled-unknown"}) is None


# ── the frame a sketch on a rotated plane records ────────────────────────


class _Document:
    Name = "Part1.CATPart"


class _Connection:
    def __init__(self) -> None:
        self.active_document = _Document()
        self.sketch_outlines: dict = {}

    def ensure_connected(self):
        pass


def test_a_sketch_on_a_rotated_plane_records_a_derived_frame():
    from catia_mcp.tools.sketcher import SketcherTools

    conn = _Connection()
    conn.datum_plane_angles = {"P72": {"base": "zx",
                                       "axis": [0.0, 0.0, 1.0],
                                       "angle": 72.0, "axis_name": "z"}}
    tools = SketcherTools(conn)
    placement = tools._angled_placement("P72")
    assert placement is not None
    assert placement["base"] == "frame"
    assert "rotated 72 degrees about z" in placement["derived"]
    assert placement["u"] == pytest.approx([0.0, 0.0, 1.0])
    assert placement["v"] == pytest.approx([0.309016994, 0.951056516, 0.0],
                                           abs=1e-8)


def test_a_plane_from_disk_has_no_frame_and_says_nothing_about_one():
    from catia_mcp.tools.sketcher import SketcherTools

    tools = SketcherTools(_Connection())
    assert tools._angled_placement("P72") is None
    assert tools._angled_placement({"type": "offset", "base": "xy"}) is None


def test_a_rename_moves_the_record_instead_of_splitting_it():
    """The copy is renamed after its placement is recorded and before its
    elements are - so the frame sat under '草图.2' while the geometry sat under
    'COPY'. One sketch, two records, and GSD answered "no recorded frame"."""
    from catia_mcp.tools.sketcher import SketcherTools

    conn = _Connection()
    tools = SketcherTools(conn)
    old, new = "草图.2", "COPY"
    conn.sketch_outlines[tools._sketch_key(old)] = {
        "placement": {"base": "frame", "origin": [0, 0, 0],
                      "u": [0, 0, 1], "v": [1, 0, 0]},
        "elements": [],
    }
    conn.sketch_outlines[tools._sketch_key(new)] = {
        "placement": None, "elements": [{"kind": "line"}],
    }
    tools._rekey_records(old, new)
    assert tools._sketch_key(old) not in conn.sketch_outlines
    record = conn.sketch_outlines[tools._sketch_key(new)]
    assert record["placement"]["base"] == "frame"
    assert record["elements"] == [{"kind": "line"}]


# ── fill: two routes, one answer ────────────────────────────────────────


class _Fill:
    def AddBound(self, _reference):
        pass


class _Factory:
    def AddNewFill(self):
        return _Fill()


def _fill_tools(area: float | None, update_error: bool = False):
    from catia_mcp.tools.gsd import GSDTools

    tools = GSDTools(_Connection())
    dropped: list = []
    tools._factory = lambda: _Factory()          # type: ignore[method-assign]
    tools._ref = lambda name: f"ref({name})"     # type: ignore[method-assign]
    tools._get_geoset = lambda: types.SimpleNamespace(  # type: ignore
        AppendHybridShape=lambda _shape: None)

    def finish(_shape, _name, message):
        if update_error:
            raise RuntimeError("CATIA rejected the feature (update failed)")
        return message

    tools._finish = finish                       # type: ignore[method-assign]
    tools._shape_area_mm2 = lambda _shape: area   # type: ignore[method-assign]
    tools._drop_element = dropped.append         # type: ignore[method-assign]
    return tools, dropped


def test_a_fill_publishes_the_area_that_says_it_closed_a_region():
    tools, _dropped = _fill_tools(area=100.0)
    reply = tools.execute("catia_gsd_fill", {"boundaries": ["S0", "S1"]})
    assert "100.0000 mm2" in reply
    assert "the boundary really closed a region" in reply


def test_a_degenerate_fill_is_taken_back_out_and_measured():
    """The silent case: an open boundary builds a face with no area."""
    tools, dropped = _fill_tools(area=0.0)
    with pytest.raises(Exception) as caught:
        tools.execute("catia_gsd_fill", {"boundaries": ["OPEN_EDGE"]})
    error = caught.value
    assert getattr(error, "code", "") == "FILL_DEGENERATE"
    message = str(error)
    assert "0.000000 mm2" in message
    assert "catia_gsd_boundary(edge=N)" in message
    assert "25.0000 mm" in message          # the measured single side
    assert dropped, "the degenerate face has to be deleted again"


def test_an_unmeasurable_area_is_not_treated_as_zero():
    tools, dropped = _fill_tools(area=None)
    reply = tools.execute("catia_gsd_fill", {"boundaries": ["S0"]})
    assert "Fill surface created" in reply
    assert not dropped


def test_both_routes_answer_with_the_same_code_when_catia_refuses():
    tools, _dropped = _fill_tools(area=100.0, update_error=True)
    with pytest.raises(Exception) as caught:
        # A named curve, not Edge.N: that route goes through the topology search
        # first, and this stub has no selection to search.
        tools.execute("catia_gsd_fill", {"boundaries": ["BORDER"]})
    assert getattr(caught.value, "code", "") == "FILL_NOT_CLOSED"
    assert "CLOSED boundaries" in str(caught.value)


# ── the cap failure now names the loft fix ──────────────────────────────


def test_the_cap_refusal_tells_a_loft_caller_what_to_change():
    from catia_mcp.tools.gsd import GSDTools

    text = inspect.getsource(GSDTools._cap_centre)
    assert "IF 'element' IS A LOFT" in text
    assert "one of the loft's guides" in text
    assert "0.0000 mm, measured 2026-11-09" in text


# ── the probe has to be a corner that can fail ──────────────────────────


def test_the_frame_check_probes_the_farthest_corner_not_the_origin():
    from catia_mcp.tools.gsd import GSDTools

    far = GSDTools._far_corner([[0.0, 0.0, 0.0], [1.8541, 5.7063, 0.0]])
    assert far == [1.8541, 5.7063, 0.0], (
        "the first corner is usually the sketch origin, which lies on any "
        "sketch through it - a probe there answers 0.0000 mm for a frame that "
        "is wrong by 90 degrees")


def test_a_reconstructed_frame_says_so_and_an_ordinary_one_does_not():
    from catia_mcp.tools.gsd import GSDTools

    derived = GSDTools._placement_note(
        {"base": "frame", "u": [0, 0, 1], "v": [1, 0, 0],
         "derived": "'P72' = the zx plane rotated 72 degrees about z"})
    assert "the frame of 'P72'" in derived
    plain = GSDTools._placement_note({"base": "xy", "distance": 25.0})
    assert plain == " in xy plane order, z=25"


# ── the visibility note the reporter asked for ──────────────────────────


def test_set_visibility_mentions_that_one_element_can_be_shown_alone():
    from catia_mcp.tools.part_design import PartDesignTools

    schema = next(entry for entry in PartDesignTools(connection=None)
                  .get_tool_definitions()
                  if entry["name"] == "catia_set_visibility")
    text = schema["description"] + str(schema["inputSchema"])
    assert "加厚曲面.1" in text
    assert "brought the solid back" in text
