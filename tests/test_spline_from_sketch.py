# -*- coding: utf-8 -*-
"""catia_gsd_spline(element=...) - the outline of a sketch, back as a curve.

A loft section is ONE curve; a polygon outline is four. The only way through is
a curve through the same corners, and a sketch cannot hand its coordinates back
(a CATIA Point2D exposes no readable X/Y through automation on this build). So
the server remembers what it drew, and converts those 2D corners to global XYZ
through the plane the sketch was created on - mapping measured on B30
2026-10-25, see ``_to_global``.
"""

from __future__ import annotations

import inspect

import pytest


class _Conn:
    def __init__(self, outlines=None):
        self.sketch_outlines = outlines or {}


def _gsd(outlines=None):
    from catia_mcp.tools.gsd import GSDTools

    return GSDTools(_Conn(outlines))


def _record(placement, elements):
    return {"placement": placement, "elements": elements}


# --------------------------------------------------------------------------
# Placement: which sketches can be placed in 3D at all.
# --------------------------------------------------------------------------

def test_datum_plane_placements_are_known():
    from catia_mcp.tools.sketcher import SketcherTools

    assert SketcherTools._placement_of("xy", 50.0) == {"base": "xy", "distance": 50.0}
    assert SketcherTools._placement_of("zx", None) == {"base": "zx", "distance": 0.0}
    assert SketcherTools._placement_of(
        {"type": "offset", "base": "yz", "distance": 12.5}, None
    ) == {"base": "yz", "distance": 12.5}


def test_a_sketch_on_a_face_has_no_known_frame():
    from catia_mcp.tools.sketcher import SketcherTools

    assert SketcherTools._placement_of({"type": "face", "index": 3}, None) is None
    # An element that is not a datum plane is not a frame either.
    assert SketcherTools._placement_of({"type": "offset", "base": "MyPlane"}, 0) is None


def test_placement_is_recorded_when_the_sketch_is_created():
    from catia_mcp.tools.sketcher import SketcherTools

    source = inspect.getsource(SketcherTools._create_sketch)
    assert "_outline_record" in source
    assert "_placement_of" in source


# --------------------------------------------------------------------------
# The mapping itself - each number was measured, not assumed.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("base,u,v,expected", [
    ("xy", 10.0, 6.0, [10.0, 6.0, 25.0]),   # H = X, V = Y, normal = Z
    ("yz", 10.0, 6.0, [25.0, 10.0, 6.0]),   # H = Y, V = Z, normal = X
    ("zx", 10.0, 6.0, [6.0, 25.0, 10.0]),   # H = Z, V = X, normal = Y
])
def test_sketch_coordinates_land_on_the_right_global_axes(base, u, v, expected):
    from catia_mcp.tools.gsd import GSDTools

    placement = {"base": base, "distance": 25.0}
    assert GSDTools._to_global(placement, u, v) == expected


def test_a_negative_offset_is_carried_through():
    from catia_mcp.tools.gsd import GSDTools

    assert GSDTools._to_global({"base": "xy", "distance": -7.5}, 1, 2) == [1, 2, -7.5]


# --------------------------------------------------------------------------
# Ordering: corners have to come back as a loop, not as a bag of segments.
# --------------------------------------------------------------------------

def test_four_lines_come_back_in_loop_order():
    gsd = _gsd()
    elements = []
    for x1, y1, x2, y2 in ((-10, -6, 10, -6), (10, -6, 10, 6),
                           (10, 6, -10, 6), (-10, 6, -10, -6)):
        elements.append({"kind": "line", "x1": x1, "y1": y1, "x2": x2, "y2": y2})
    loop = gsd._chain_lines(elements, "草图.1")
    assert loop == [[-10, -6], [10, -6], [10, 6], [-10, 6]]


def test_a_segment_drawn_backwards_is_reversed_not_dropped():
    gsd = _gsd()
    elements = [
        {"kind": "line", "x1": 0, "y1": 0, "x2": 10, "y2": 0},
        {"kind": "line", "x1": 10, "y1": 10, "x2": 10, "y2": 0},   # drawn backwards
        {"kind": "line", "x1": 10, "y1": 10, "x2": 0, "y2": 10},
        {"kind": "line", "x1": 0, "y1": 0, "x2": 0, "y2": 10},     # closes the loop
    ]
    loop = gsd._chain_lines(elements, "草图.1")
    assert loop == [[0, 0], [10, 0], [10, 10], [0, 10]]


def test_an_open_chain_is_refused_with_the_point_it_stops_at():
    gsd = _gsd()
    elements = [
        {"kind": "line", "x1": 0, "y1": 0, "x2": 10, "y2": 0},
        {"kind": "line", "x1": 30, "y1": 30, "x2": 40, "y2": 40},
    ]
    with pytest.raises(RuntimeError) as error:
        gsd._chain_lines(elements, "草图.1")
    assert "do not form one chain" in str(error.value)
    assert "10, 0" in str(error.value)


def test_a_rectangle_records_four_corners():
    gsd = _gsd()
    loop = gsd._chain_outline(
        [{"kind": "rectangle", "x1": -3, "y1": -2, "x2": 3, "y2": 2}], "草图.1")
    assert loop == [[-3, -2], [3, -2], [3, 2], [-3, 2]]


# --------------------------------------------------------------------------
# Refusals: each one says what to do instead.
# --------------------------------------------------------------------------

def test_a_circle_sketch_points_at_gsd_circle():
    gsd = _gsd()
    with pytest.raises(RuntimeError) as error:
        gsd._chain_outline([{"kind": "circle", "cx": 0, "cy": 0, "radius": 5}], "草图.1")
    assert "already ONE curve" in str(error.value)


def test_a_sketch_this_server_never_drew_says_so():
    gsd = _gsd()
    with pytest.raises(RuntimeError) as error:
        gsd._outline_points("草图.9", True)
    assert "No outline recorded" in str(error.value)


def test_a_sketch_on_a_face_refuses_rather_than_guessing():
    gsd = _gsd({"草图.1": _record(None, [{"kind": "rectangle", "x1": 0, "y1": 0,
                                          "x2": 1, "y2": 1}])})
    with pytest.raises(RuntimeError) as error:
        gsd._outline_points("草图.1", True)
    assert "cannot be turned into global XYZ" in str(error.value)


def test_the_happy_path_returns_global_coordinates_and_says_where_they_came_from():
    gsd = _gsd({"草图.1": _record(
        {"base": "xy", "distance": 50.0},
        [{"kind": "line", "x1": -10, "y1": -6, "x2": 10, "y2": -6},
         {"kind": "line", "x1": 10, "y1": -6, "x2": 10, "y2": 6},
         {"kind": "line", "x1": 10, "y1": 6, "x2": -10, "y2": 6},
         {"kind": "line", "x1": -10, "y1": 6, "x2": -10, "y2": -6}])})
    coords, note = gsd._outline_points("草图.1", True)
    assert coords == [[-10.0, -6.0, 50.0], [10.0, -6.0, 50.0],
                      [10.0, 6.0, 50.0], [-10.0, 6.0, 50.0]]
    assert "草图.1" in note and "z=50" in note


def test_an_empty_sketch_is_refused():
    gsd = _gsd({"草图.1": _record({"base": "xy", "distance": 0.0}, [])})
    with pytest.raises(RuntimeError) as error:
        gsd._outline_points("草图.1", True)
    assert "holds nothing" in str(error.value)


# --------------------------------------------------------------------------
# The schema: 'points' is no longer required, and both cannot be passed.
# --------------------------------------------------------------------------

def test_spline_accepts_element_instead_of_points():
    from catia_mcp.tools.gsd import GSDTools

    doc = [d for d in GSDTools(connection=None).get_tool_definitions()
           if d["name"] == "catia_gsd_spline"][0]
    props = doc["inputSchema"]["properties"]
    assert "element" in props
    assert doc["inputSchema"].get("required", []) == []


def test_passing_both_points_and_element_is_a_clear_error(monkeypatch):
    from catia_mcp.tools.gsd import GSDTools

    class _Any:
        def ensure_connected(self):
            pass

    gsd = GSDTools(_Any())
    with pytest.raises(ValueError) as error:
        gsd._spline({"points": [[0, 0, 0], [1, 1, 1]], "element": "草图.1"})
    assert "not both" in str(error.value)


def test_neither_points_nor_element_is_a_clear_error():
    from catia_mcp.tools.gsd import GSDTools

    class _Any:
        def ensure_connected(self):
            pass

    gsd = GSDTools(_Any())
    with pytest.raises(ValueError) as error:
        gsd._spline({})
    assert "'element'" in str(error.value)


# --------------------------------------------------------------------------
# The two routes are documented where people look.
# --------------------------------------------------------------------------

def test_the_loft_description_warns_that_a_spline_bulges():
    from catia_mcp.tools.gsd import GSDTools

    doc = [d for d in GSDTools(connection=None).get_tool_definitions()
           if d["name"] == "catia_gsd_multi_section_surface"][0]
    text = doc["description"]
    assert "A SPLINE IS NOT THE POLYGON" in text
    assert "24.04 x 21.46" in text
    assert "catia_sketch_spline" in text


def test_the_loft_description_documents_the_extra_section_route():
    from catia_mcp.tools.gsd import GSDTools

    doc = [d for d in GSDTools(connection=None).get_tool_definitions()
           if d["name"] == "catia_gsd_multi_section_surface"][0]
    text = doc["description"]
    assert "BULGES PAST ITS OWN SECTIONS" in text
    assert "78.0 mm2" in text and "106.4 mm2" in text
    assert "within 5 %" in text
    assert "67 % short" in text
