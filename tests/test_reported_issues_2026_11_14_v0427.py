# -*- coding: utf-8 -*-
"""0.3.125 - the two P0s of the 2026-11-14 report: a sketch that can be read and driven.

The report's own measurements are the reference:

  * `catia_sketch_get_geometry` said "coordinates are NOT returned", and that was
    read as "a sketch cannot be checked". Measured on B30 2026-11-14: 圆.1.Radius
    = 6.0, 椭圆.1.MajorRadius = 9.0, MinorRadius = 4.5. The limit is about Point2D,
    not about curves.
  * 7 elliptical sections, a user parameter StemLength, and set_parameter answering
    "changed: 23.8 -> 30" with the volume stuck at 2621.886 mm3 - because nothing
    in the sketch was a dimension. Adding one puts
    '<part>\\零件几何体\\草图.1\\半径.1\\半径' into catia_get_parameters, and
    driving it 6 -> 11 moved a pad 1131.0 -> 3801.3 mm3 = pi*121*10.
  * A parameter that is an OUTPUT of a relation is computed: set_parameter died on
    a bare `方法 Value 失败` with no code. Relation exposes NbOutParameters /
    GetOutParameter, so the relation that writes it can be named.
  * A solid loft renamed by the caller listed as type "unknown", so anything
    branching on the type skipped it.

Everything here is offline; the fakes answer only what these functions ask for.
"""

from __future__ import annotations

import pytest

from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.sketcher import SketcherTools


# ── fakes ────────────────────────────────────────────────────────────────────


class _Dimension:
    def __init__(self, value: float) -> None:
        self.Value = value


class _Constraint:
    def __init__(self, name: str, value: float | None) -> None:
        self.Name = name
        if value is not None:
            self.Dimension = _Dimension(value)


class _Constraints:
    """Just enough of CATIA's Constraints collection: Count/Item/Add/Remove."""

    def __init__(self, values: list[tuple[str, float | None]] | None = None) -> None:
        self.items = [_Constraint(name, value)
                      for name, value in (values or [])]
        self.added: list[int] = []
        self.refuse: Exception | None = None

    @property
    def Count(self) -> int:
        return len(self.items)

    def Item(self, index: int) -> _Constraint:
        return self.items[index - 1]

    def AddMonoEltCst(self, kind: int, element: object) -> _Constraint:
        if self.refuse is not None:
            raise self.refuse
        self.added.append(kind)
        constraint = _Constraint(f"半径.{len(self.items) + 1}", 6.0)
        self.items.append(constraint)
        return constraint

    def Remove(self, constraint: _Constraint) -> None:
        self.items.remove(constraint)


class _Element:
    def __init__(self, name: str, **dimensions: float) -> None:
        self.Name = name
        for key, value in dimensions.items():
            setattr(self, key, value)


class _Geometry:
    def __init__(self, elements: list[_Element]) -> None:
        self.items = elements

    @property
    def Count(self) -> int:
        return len(self.items)

    def Item(self, index: int) -> _Element:
        return self.items[index - 1]


class _Parameter:
    def __init__(self, name: str) -> None:
        self.Name = name


class _Parameters:
    def __init__(self, names: list[str]) -> None:
        self.items = [_Parameter(name) for name in names]

    @property
    def Count(self) -> int:
        return len(self.items)

    def Item(self, index: int) -> _Parameter:
        return self.items[index - 1]


class _Part:
    def __init__(self, names: list[str] | None = None) -> None:
        self.Name = "Demo"
        self.Parameters = _Parameters(names or [])


class _Sketch:
    def __init__(self, values: list[tuple[str, float | None]] | None = None,
                 elements: list[_Element] | None = None) -> None:
        self.Name = "草图.1"
        self.Constraints = _Constraints(values)
        self.GeometricElements = _Geometry(elements or [])


def _tools() -> SketcherTools:
    """A SketcherTools with an open sketch and no CATIA behind it."""
    tools = SketcherTools(connection=None)
    sketch = _Sketch()
    tools._active_sketch = sketch
    tools._sketch_part = _Part()
    return tools


# ── P0-2: the dimensions that ARE readable ───────────────────────────────────


def test_a_circle_and_an_ellipse_report_their_dimensions():
    sketch = _Sketch(elements=[
        _Element("圆.1", Radius=6.0),
        _Element("椭圆.1", MajorRadius=9.0, MinorRadius=4.5),
        _Element("直线.1"),                      # a line has no radius at all
    ])
    values = SketcherTools._curve_dimensions(sketch)
    assert values["圆.1"] == {"radius_mm": 6.0}
    assert values["椭圆.1"] == {"major_radius_mm": 9.0, "minor_radius_mm": 4.5}
    assert "直线.1" not in values, "a line's endpoints are still unreadable"


def test_the_constraint_path_is_read_from_the_parameters_list():
    """The suffix is CATIA's localised name; building it from a guess is wrong."""
    part = _Part(["Demo\\零件几何体\\草图.1\\半径.1\\半径",
                  "Demo\\零件几何体\\草图.1\\半径.1\\模式",
                  "Demo\\零件几何体\\草图.1\\半径.1\\活动",
                  "Demo\\零件几何体\\凸台.1\\第一限制\\长度"])
    assert SketcherTools._constraint_parameter_path(part, "半径.1") == (
        "Demo\\零件几何体\\草图.1\\半径.1\\半径")
    assert SketcherTools._constraint_parameter_path(part, "半径.9") is None
    assert SketcherTools._constraint_parameter_path(None, "半径.1") is None


# ── P0-3: a drawn curve becomes drivable ─────────────────────────────────────


def test_a_circle_gets_one_radius_constraint_and_names_its_parameter():
    tools = _tools()
    tools._sketch_part = _Part(["Demo\\零件几何体\\草图.1\\半径.1\\半径"])
    note = tools._drive_curve(object(), [(SketcherTools._CST_DIM_RADIUS, "radius")])
    assert tools._active_sketch.Constraints.added == [SketcherTools._CST_DIM_RADIUS]
    assert "DRIVEN" in note
    assert "半径.1" in note and "半径.1\\半径" in note
    assert "catia_set_constraint_value" in note


def test_an_ellipse_gets_two_semi_axes():
    tools = _tools()
    note = tools._drive_curve(object(), [
        (SketcherTools._CST_DIM_SEMI_MAJOR, "semi-major"),
        (SketcherTools._CST_DIM_SEMI_MINOR, "semi-minor")])
    assert tools._active_sketch.Constraints.added == [
        SketcherTools._CST_DIM_SEMI_MAJOR, SketcherTools._CST_DIM_SEMI_MINOR]
    assert "semi-major" in note and "semi-minor" in note


def test_a_constraint_that_cannot_be_added_is_reported_not_raised():
    """The curve is already drawn; the loss is a parameter, not the geometry."""
    tools = _tools()
    tools._active_sketch.Constraints.refuse = RuntimeError("方法 AddMonoEltCst 失败")
    note = tools._drive_curve(object(), [(SketcherTools._CST_DIM_RADIUS, "radius")])
    assert "no dimensional constraint could be added" in note
    assert "catia_sketch_constraint" in note


def test_the_two_measured_type_numbers_are_not_the_same():
    """7 is the radius / semi-major; 19 is the semi-minor - measured, not assumed.

    Type 19 was the ONLY number that answered 4.5 on an ellipse whose major radius
    is 9.0, which is how a 'semi-minor' constraint is told apart from a second
    copy of the semi-major.
    """
    assert SketcherTools._CST_DIM_RADIUS == 7
    assert SketcherTools._CST_DIM_SEMI_MAJOR == 7
    assert SketcherTools._CST_DIM_SEMI_MINOR == 19
    assert PartDesignTools is not None, "imported, not used - keeps the two files linked"
    assert SketcherTools._CST_RADIUS == 14, (
        "the PRE-EXISTING 14 is a two-element distance; the new dimensional "
        "constants are _CST_DIM_* precisely so they cannot shadow it")


# ── P0-2: changing one value ─────────────────────────────────────────────────


def _with_constraints(values):
    tools = _tools()
    sketch = _Sketch(values, elements=[_Element("椭圆.1", MajorRadius=9.0)])
    tools._active_sketch = sketch
    tools._locate_sketch = lambda name=None: (sketch, "test")
    tools.conn = type("C", (), {"get_active_part": lambda self: _Part(),
                                "refresh_display": lambda self: None})()
    return tools


def test_setting_a_constraint_value_reports_what_moved():
    tools = _with_constraints([("半径.1", 9.0)])
    reply = tools._set_constraint_value(None, "半径.1", 12.0)
    assert "9 -> 12 mm" in reply
    assert "no curve dimension in this sketch changed" in reply, (
        "the fake element does not move, so the reply must say so rather than "
        "claim a change")


def test_an_unknown_constraint_lists_the_ones_that_exist():
    tools = _with_constraints([("半径.1", 9.0), ("半径.2", 4.5)])
    with pytest.raises(Exception) as caught:
        tools._set_constraint_value(None, "半径.7", 12.0)
    assert getattr(caught.value, "code", "") == "CONSTRAINT_NOT_FOUND"
    assert "半径.1" in str(caught.value) and "半径.2" in str(caught.value)


def test_a_non_dimensional_constraint_says_why_it_cannot_be_set():
    tools = _with_constraints([("固定的.1", None)])
    with pytest.raises(Exception) as caught:
        tools._set_constraint_value(None, "固定的.1", 5.0)
    assert getattr(caught.value, "code", "") == "NOT_A_DIMENSION"
    assert "no numeric dimension" in str(caught.value)


# ── P1-2: a parameter a formula drives ───────────────────────────────────────


class _Relation:
    def __init__(self, name: str, outputs: list[str]) -> None:
        self.Name = name
        self.NbOutParameters = len(outputs)
        self._outputs = outputs

    def GetOutParameter(self, index: int) -> _Parameter:
        return _Parameter(self._outputs[index - 1])


class _Relations:
    def __init__(self, relations: list[_Relation]) -> None:
        self.items = relations

    @property
    def Count(self) -> int:
        return len(self.items)

    def Item(self, index: int) -> _Relation:
        return self.items[index - 1]


def test_the_relation_that_writes_a_parameter_is_found_by_its_outputs():
    """Not by matching the expression text - 'DriveStem = 23.8mm' does not name it."""
    from catia_mcp.tools.measurement import MeasurementTools

    part = _Part()
    part.Relations = _Relations([
        _Relation("DriveStem", ["Demo\\StemLength"]),
        _Relation("Other", ["Demo\\Something"]),
    ])
    assert MeasurementTools._driving_relations(
        part, "Demo\\StemLength") == ["DriveStem"]
    assert MeasurementTools._driving_relations(part, "Demo\\NotDriven") == []


# ── P2-4: a renamed loft still knows it is a loft ────────────────────────────


class _Rich:
    """A feature as part.FindObjectByName returns it: the full interface."""

    def __init__(self, members: list[str]) -> None:
        self._members = members

    class _Info:
        def __init__(self, members: list[str]) -> None:
            self._members = members

        def GetTypeAttr(self):
            return type("Attr", (), {"cFuncs": len(self._members)})()

        def GetFuncDesc(self, index: int):
            return type("Desc", (), {"memid": index})()

        def GetNames(self, memid: int):
            return (self._members[memid],)

    def GetTypeInfo(self) -> _Info:
        return self._Info(self._members)

    _oleobj_ = None


def _rich(members: list[str]) -> _Rich:
    rich = _Rich(members)
    rich._oleobj_ = rich                     # same two methods, one object
    return rich


class _LookupPart:
    def __init__(self, mapping: dict[str, _Rich]) -> None:
        self._mapping = mapping

    def FindObjectByName(self, name: str) -> _Rich | None:
        return self._mapping.get(name)


def test_a_renamed_loft_is_still_a_loft():
    """The 2026-11-14 report's own case: a solid loft named 'AirPods_Body'."""
    tools = PartDesignTools(connection=None)
    part = _LookupPart({
        "AirPods_Body": _rich(["AddSectionToLoft", "GetSectionFromLoft",
                               "Name", "Sections"]),
    })
    shape = type("S", (), {"Name": "AirPods_Body"})()
    assert tools._feature_kind_resolved(part, shape) == "loft"


def test_a_name_the_prefix_table_knows_needs_no_lookup():
    tools = PartDesignTools(connection=None)
    part = _LookupPart({})                   # nothing there at all
    shape = type("S", (), {"Name": "凸台.3"})()
    assert tools._feature_kind_resolved(part, shape) == "pad"


def test_an_unknown_feature_stays_unknown_rather_than_guessing():
    tools = PartDesignTools(connection=None)
    part = _LookupPart({})
    shape = type("S", (), {"Name": "SomethingElse"})()
    assert tools._feature_kind_resolved(part, shape) == "unknown"


# ── the tool surface ─────────────────────────────────────────────────────────


def test_the_drawing_tools_advertise_driven_and_the_semi_axis_meaning():
    definitions = {item["name"]: item
                   for item in SketcherTools(connection=None).get_tool_definitions()}
    assert "catia_set_constraint_value" in definitions
    circle = definitions["catia_sketch_circle"]["inputSchema"]["properties"]
    ellipse = definitions["catia_sketch_ellipse"]["inputSchema"]["properties"]
    assert circle["driven"]["default"] is True
    assert ellipse["driven"]["default"] is True
    assert "HALF the long axis" in ellipse["major"]["description"]
    assert "Must not exceed major" in ellipse["minor"]["description"]
