# -*- coding: utf-8 -*-
"""0.3.60 - a surface that could not be moved, and three smaller traps.

None of these came from an acceptance report. They came from BUILDING the
five-petal bottle base (2026-11-04), in the order they cost time:

1. A five-lobe base is one lobe plus four rotations of 72 degrees, and GSD had
   no way to rotate anything - `catia_circ_pattern` is a Part Design feature and
   wants a solid, `catia_gsd_symmetry` only reflects. So the lobe was rebuilt
   five times, once per position, and the positions could not be moved later.
   `AddNewRotate(element, axis, angle)` had been in the typelib all along.
   Measured on B30: a cylinder whose axis sits at (20, 0) rotated +90 about z
   lands at (0, 20) - counter-clockwise, right-hand rule - and the original
   stays. `AddNewTranslate(element, direction, distance)`: a cylinder spanning
   z 0..20 moved 30 along +z spans 30..50 and no longer meets z=0.

2. The two members of that family take DIFFERENT argument kinds. AddNewRotate
   wants a REFERENCE to an axis line; AddNewTranslate wants the Direction
   OBJECT, and a Reference there raises "type mismatch, argument 2". Both were
   found by trial, so both are pinned here: a future tidy-up that makes them
   consistent-in-code without measuring will break one of them.

3. `catia_gsd_revolve`'s angle1/angle2 were documented as "first/second angle
   limit", i.e. a start and an end. They are the typelib's iOffsetDebut /
   iOffsetFin: a sweep from the profile plane toward each side, so the total is
   angle1 + angle2. (72, 72) is a 144 degree sector, not a zero-width one, and
   finding that out cost a whole rebuild.

4. A closed spline whose last point repeats the first is REFUSED by CATIA - the
   whole spline, with a generic invalid-parameter error that reads like bad
   coordinates. Closing the ring by hand is the intuitive thing to do.

5. A mesh export of a part with no geometry at all hung for five minutes with
   no output and had to be killed, so it is refused before the call now. The
   first draft of that guard also claimed a curve-only part would raise a modal
   dialog; measured the same day, a part holding one spline exported an STL
   fine, and the claim was corrected rather than kept.
"""

from __future__ import annotations

import types

import pytest

from catia_mcp.tools.export import ExportTools
from catia_mcp.tools.gsd import GSDTools, _normalise_arguments
from catia_mcp.tools.measurement import _uniform_thickness


# ───────────── a part small enough to record what it is asked to do

class _Shape:
    def __init__(self, name: str = "shape") -> None:
        self.Name = name


class _Geoset:
    def __init__(self) -> None:
        self.appended: list[object] = []

    def AppendHybridShape(self, shape: object) -> None:
        self.appended.append(shape)


class _Part:
    def __init__(self) -> None:
        self.updated: list[object] = []

    def UpdateObject(self, shape: object) -> None:
        self.updated.append(shape)

    def CreateReferenceFromObject(self, shape: object) -> tuple[str, object]:
        return ("reference", shape)


class _Factory:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def AddNewDirectionByCoord(self, x: float, y: float, z: float) -> _Shape:
        self.calls.append(("direction_coord", x, y, z))
        return _Shape("direction")

    def AddNewDirection(self, reference: object) -> _Shape:
        self.calls.append(("direction_from_line", reference))
        return _Shape("direction")

    def AddNewLinePtPt(self, start: object, end: object) -> _Shape:
        self.calls.append(("line", start, end))
        return _Shape("axis_line")

    def AddNewRotate(self, element: object, axis: object, angle: float) -> _Shape:
        self.calls.append(("rotate", element, axis, angle))
        return _Shape("rotated")

    def AddNewTranslate(self, element: object, direction: object,
                        distance: float) -> _Shape:
        self.calls.append(("translate", element, direction, distance))
        return _Shape("translated")


class _Conn:
    def __init__(self, part: _Part) -> None:
        self._part = part

    def ensure_connected(self) -> None:
        return None

    def get_active_part(self) -> _Part:
        return self._part


def _tools() -> tuple[GSDTools, _Factory, _Geoset, _Part]:
    part = _Part()
    tools = GSDTools(connection=_Conn(part))
    factory = _Factory()
    geoset = _Geoset()
    tools._factory = lambda: factory                    # type: ignore[method-assign]
    tools._get_geoset = lambda: geoset                  # type: ignore[method-assign]
    tools._free_name = lambda part, wanted: (wanted, True)  # type: ignore[method-assign]
    tools._ref = lambda name: ("reference", name)       # type: ignore[method-assign]
    tools._point_ref = lambda spec: ("point", tuple(spec))  # type: ignore[method-assign]
    tools._finish = lambda shape, name, message: message    # type: ignore[method-assign]
    return tools, factory, geoset, part


# ───────────── 1. the transform tools exist and do the thing

def test_both_transform_tools_are_registered():
    schemas = {tool["name"]: tool for tool in GSDTools(connection=None)
               .get_tool_definitions()}
    for name in ("catia_gsd_rotate", "catia_gsd_translate"):
        assert name in schemas, f"{name} is not offered"
    rotate = schemas["catia_gsd_rotate"]["inputSchema"]
    assert rotate["required"] == ["element", "axis", "angle"]
    translate = schemas["catia_gsd_translate"]["inputSchema"]
    assert translate["required"] == ["element", "direction", "distance"]


def test_rotate_passes_the_angle_and_an_axis_reference():
    tools, factory, _geoset, _part = _tools()
    message = tools._rotate({"element": "LOBE", "axis": "z", "angle": 72})
    rotate = [call for call in factory.calls if call[0] == "rotate"]
    assert rotate, "AddNewRotate was never called"
    _name, element, axis, angle = rotate[0]
    assert element == ("reference", "LOBE")
    assert axis[0] == "reference", (
        "AddNewRotate needs a REFERENCE to the axis line; anything else raises")
    assert getattr(axis[1], "Name", None) == "LOBE_axis_z", (
        "the reference has to be to the line that was just built, named after "
        "the element it rotates so a five-fold pattern stays readable")
    assert angle == 72.0
    assert "72" in message


def test_an_origin_axis_is_built_as_a_line_not_a_direction():
    """A direction cannot say WHERE the axis is, and a rotation needs that."""
    tools, factory, geoset, _part = _tools()
    tools._axis_ref("z", "LOBE")
    lines = [call for call in factory.calls if call[0] == "line"]
    assert len(lines) == 1, "the z axis should be one line from the origin"
    _tag, start, end = lines[0]
    assert start == ("point", (0.0, 0.0, 0.0))
    assert end == ("point", (0.0, 0.0, 1.0))
    assert geoset.appended, "the axis line has to live in the geometrical set"


def test_a_named_axis_is_used_instead_of_building_one():
    tools, factory, _geoset, _part = _tools()
    reference, note = tools._axis_ref("MY_AXIS", "LOBE")
    assert reference == ("reference", "MY_AXIS")
    assert note == ""
    assert not [call for call in factory.calls if call[0] == "line"]


def test_translate_hands_over_the_direction_object_not_its_reference():
    """The measured failure: a Reference here raises 'type mismatch, argument 2'."""
    tools, factory, _geoset, _part = _tools()
    tools._translate({"element": "LOBE", "direction": [0, 0, 1], "distance": 30})
    translated = [call for call in factory.calls if call[0] == "translate"]
    assert translated, "AddNewTranslate was never called"
    _tag, element, direction, distance = translated[0]
    assert element == ("reference", "LOBE")
    assert isinstance(direction, _Shape), (
        "the direction must be the Direction OBJECT itself, not a Reference to it")
    assert distance == 30.0


def test_a_zero_direction_is_refused_before_catia_sees_it():
    tools, _factory, _geoset, _part = _tools()
    with pytest.raises(ValueError, match="zero vector"):
        tools._direction_obj([0, 0, 0], "LOBE")
    with pytest.raises(ValueError, match="exactly 3"):
        tools._direction_obj([0, 1], "LOBE")


# ───────────── 2. revolve's angles are offsets, and the reply says so

def test_revolve_reports_the_sum_because_that_is_the_sweep():
    tools, factory, _geoset, _part = _tools()
    tools._factory = lambda: types.SimpleNamespace(  # type: ignore[method-assign]
        AddNewRevol=lambda profile, a1, a2, axis: (factory.calls.append(
            ("revol", a1, a2)), _Shape("revol"))[1])
    message = tools._revolve({"profile": "P", "axis": "A",
                              "angle1": 72, "angle2": 72})
    assert ("revol", 72, 72) in factory.calls
    assert "144" in message, "the reply must name the sweep, not just angle1"
    assert "sum" in message


def test_the_revolve_description_says_offsets_and_names_the_typelib():
    schema = [tool for tool in GSDTools(connection=None).get_tool_definitions()
              if tool["name"] == "catia_gsd_revolve"][0]
    text = schema["description"]
    assert "iOffsetDebut" in text and "iOffsetFin" in text, (
        "the wording that caused a whole rebuild has to be traceable to the "
        "typelib names, not just paraphrased")
    assert "NOT A START ANGLE" in text.upper()


# ───────────── 3. a closed spline drops a repeated first point

def test_a_closed_spline_drops_the_point_that_repeats_the_first():
    tools, _factory, _geoset, _part = _tools()
    ring = [[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0], [0, 0, 0]]
    kept, note = tools._drop_closing_point(ring)
    assert len(kept) == 4
    assert kept[-1] == [0, 10, 0]
    assert "5 points in, 4 on the curve" in note
    assert "refuse" in note, "the reply should say why it was dropped"


def test_points_that_merely_come_close_are_kept():
    """0.001 mm apart is a real point on a fine section, not a repeat."""
    tools, _factory, _geoset, _part = _tools()
    ring = [[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0], [0.001, 0, 0]]
    kept, note = tools._drop_closing_point(ring)
    assert len(kept) == 5 and note == ""


def test_named_points_are_left_alone():
    """Two element names cannot be compared without measuring both."""
    tools, _factory, _geoset, _part = _tools()
    ring = ["P1", "P2", "P3", "P1"]
    kept, note = tools._drop_closing_point(ring)
    assert kept == ring and note == ""


# ───────────── 4. one concept, three argument names

def test_the_plane_argument_answers_to_its_other_names():
    cases = [
        ("catia_gsd_circle", {"support": "xy"}, "support_plane"),
        ("catia_gsd_project", {"support_plane": "xy"}, "support"),
        ("catia_gsd_plane_offset", {"support": "xy"}, "base_plane"),
        ("catia_gsd_intersection", {"cutter": "PL"}, "element2"),
    ]
    for tool, arguments, expected in cases:
        assert expected in _normalise_arguments(tool, arguments), (
            f"{tool} should accept {list(arguments)} and read '{expected}'")


def test_an_explicit_argument_is_never_overwritten_by_an_alias():
    renamed = _normalise_arguments("catia_gsd_circle",
                                   {"support": "yz", "support_plane": "xy"})
    assert renamed["support_plane"] == "xy", "the explicit key must win"


def test_the_intersection_alias_prevents_a_wrong_answer():
    """A KeyError inside try/except reads as 'they do not intersect'."""
    assert _normalise_arguments(
        "catia_gsd_intersection", {"element1": "A", "cutter": "B"}
    ) == {"element1": "A", "element2": "B"}


# ───────────── 5. the wall thickness that used to be computed by hand

def test_uniform_thickness_matches_the_bottle_base_measurement():
    """volume 10099.5 mm3, area 67392.8 mm2, a 0.3 mm wall: 2V/A = 0.2997."""
    assert _uniform_thickness(10099.5, 67392.8) == 0.2997


def test_uniform_thickness_is_absent_rather_than_zero():
    assert _uniform_thickness(None, 67392.8) is None
    assert _uniform_thickness(10099.5, None) is None
    assert _uniform_thickness(0, 67392.8) is None


# ───────────── 6. a mesh export must not raise a modal dialog

class _Collection:
    def __init__(self, items: list) -> None:
        self._items = items
        self.Count = len(items)

    def Item(self, index: int):
        return self._items[index - 1]


def _document(bodies: list, geosets: list):
    return types.SimpleNamespace(
        Part=types.SimpleNamespace(Bodies=_Collection(bodies),
                                   HybridBodies=_Collection(geosets)))


def _body(shapes: int):
    return types.SimpleNamespace(Shapes=types.SimpleNamespace(Count=shapes))


def _geoset(shapes: int):
    return types.SimpleNamespace(HybridShapes=types.SimpleNamespace(Count=shapes))


def test_a_mesh_export_of_an_empty_part_is_refused():
    tools = ExportTools(connection=None)
    with pytest.raises(ValueError, match="Nothing to export"):
        tools._assert_has_mesh_geometry(_document([], []), "stl")


def test_a_part_with_a_solid_is_allowed_through():
    tools = ExportTools(connection=None)
    tools._assert_has_mesh_geometry(_document([_body(1)], []), "stl")


def test_a_part_with_gsd_elements_is_allowed_through():
    tools = ExportTools(connection=None)
    tools._assert_has_mesh_geometry(_document([], [_geoset(3)]), "stl")


def test_the_refusal_says_what_it_cannot_catch():
    """The first draft claimed a curve-only part would raise a dialog.

    Measured on B30 2026-11-04: a part holding one spline exported an STL fine.
    So the guard stops an empty export and must say so, instead of promising a
    protection it does not have.
    """
    tools = ExportTools(connection=None)
    with pytest.raises(ValueError) as caught:
        tools._assert_has_mesh_geometry(_document([], []), "stl")
    message = str(caught.value)
    assert "Nothing to export" in message
    assert "not stopped here" in message, (
        "the message has to admit that a curve-only part gets through - a guard "
        "that overstates its coverage is worse than no guard")


def test_a_product_is_not_this_guard_s_business():
    tools = ExportTools(connection=None)
    tools._assert_has_mesh_geometry(types.SimpleNamespace(), "stl")
