"""0.3.162 - catia_move_body, the one call the 2026-11-23 report asked for.

The report (P1-4) rebuilt a 15-feature part twice because its first pad was 5 mm
out, and wrote: "with only catia_move_body(dx, dy, dz), one call would have fixed
it". V5 has no solid transform, so the tool translates the body's BOUNDARY and
closes it back into a solid - and refuses the result unless the volume, the area
and the centre of gravity all say it was a rigid motion.

These tests pin the parts that a "ok:true" would hide:
  * the refusal is a refusal (zero vector, no solid, not rigid);
  * the COG check is live - same volume and area with a COG that did not move is
    still MOVE_NOT_RIGID, because that is a body that grew in place;
  * the moved solid lands in a NEW body (a CloseSurface in the body it closes is
    circular and fails to update - measured), and the source is hidden, not gone.
"""

from __future__ import annotations

import pytest

from catia_mcp.results import CodedError, PreconditionRejected
from catia_mcp.tools.part_design import PartDesignTools


# ───────────── a part small enough to record what it is asked to do

class _Shape:
    def __init__(self, name: str = "shape",
                 vector: tuple[float, float, float] | None = None) -> None:
        self.Name = name
        self.vector = vector


class _Body:
    def __init__(self, name: str, volume: float = 90000.0,
                 area: float = 13450.0, cog: tuple[float, float, float] = (12.5, 22.5, 40.0)) -> None:
        self.Name = name
        self.volume_mm3 = volume
        self.area_mm2 = area
        self.cog = list(cog)
        self.shapes: list[_Shape] = [_Shape("凸台.1")]
        self.hidden_with: int | None = None

    @property
    def Shapes(self) -> list[_Shape]:
        return self.shapes


class _Collection:
    def __init__(self) -> None:
        self.items: list[object] = []

    @property
    def Count(self) -> int:
        return len(self.items)

    def Item(self, key):
        if isinstance(key, int):
            return self.items[key - 1]
        for item in self.items:
            if getattr(item, "Name", None) == key:
                return item
        raise KeyError(key)

    def Add(self):
        raise NotImplementedError


class _Bodies(_Collection):
    def Add(self):
        body = _Body(f"Body.{len(self.items) + 1}")
        self.items.append(body)
        return body


class _Geoset(_Shape):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.appended: list[object] = []

    def AppendHybridShape(self, shape: object) -> None:
        self.appended.append(shape)


class _Geosets(_Collection):
    def Add(self):
        geoset = _Geoset(f"GeometricalSet.{len(self.items) + 1}")
        self.items.append(geoset)
        return geoset


class _HybridFactory:
    def __init__(self, part: "_Part") -> None:
        self.part = part
        self.calls: list[tuple] = []

    def AddNewDirectionByCoord(self, x: float, y: float, z: float) -> _Shape:
        self.calls.append(("direction", x, y, z))
        return _Shape(f"Direction.{len(self.calls)}", (x, y, z))

    def AddNewTranslate(self, element: object, direction: object,
                        distance: float) -> _Shape:
        self.calls.append(("translate", element, direction, distance))
        shape = _Shape(f"平移.{len(self.calls)}")
        self.part.translate = shape
        vector = getattr(direction, "vector", (1.0, 0.0, 0.0))
        self.part.pending_delta = tuple(component * distance for component in vector)
        return shape


class _SolidFactory:
    def __init__(self, part: "_Part") -> None:
        self.part = part
        self.closed_from: list[object] = []
        self.in_work_at_close: str | None = None

    def AddNewCloseSurface(self, surface: object) -> _Shape:
        self.closed_from.append(surface)
        self.in_work_at_close = str(getattr(self.part.InWorkObject, "Name", ""))
        shape = _Shape("封闭曲面.1")
        # What the closed surface produces: the source's numbers, translated -
        # unless the test says otherwise (a move that changed the shape).
        source = self.part.bodies.items[0]
        target = self.part.InWorkObject
        target.volume_mm3 = self.part.moved_volume_override or source.volume_mm3
        target.area_mm2 = self.part.moved_area_override or source.area_mm2
        delta = self.part.pending_delta or (0.0, 0.0, 0.0)
        target.cog = list(self.part.moved_cog_override or
                          [source.cog[i] + delta[i] for i in range(3)])
        return shape


class _Measurable:
    def __init__(self, body: _Body) -> None:
        self.body = body

    @property
    def Volume(self) -> float:
        return self.body.volume_mm3 / 1e9

    @property
    def Area(self) -> float:
        return self.body.area_mm2 / 1e6

    @property
    def cog(self) -> list[float]:
        return list(self.body.cog)


class _SPA:
    def __init__(self, part: "_Part") -> None:
        self.part = part

    def GetMeasurable(self, reference) -> _Measurable:
        return _Measurable(reference[1])


class _Document:
    def __init__(self, part: "_Part") -> None:
        self.part = part

    def GetWorkbench(self, name: str) -> _SPA:
        assert name == "SPAWorkbench"
        return _SPA(self.part)


class _Part:
    def __init__(self) -> None:
        self.Name = "MOVE_TEST"
        self.bodies = _Bodies()
        self.main_body = self.bodies.Add()
        self.main_body.Name = "零件几何体"
        self.geosets = _Geosets()
        self.InWorkObject = self.main_body
        self.updated: list[object] = []
        self.created_references: list[object] = []
        self.translate: _Shape | None = None
        self.pending_delta: tuple[float, float, float] | None = None
        self.moved_volume_override: float | None = None
        self.moved_area_override: float | None = None
        self.moved_cog_override: list[float] | None = None
        self.hybrid_factory = _HybridFactory(self)
        self.solid_factory = _SolidFactory(self)

    @property
    def MainBody(self) -> _Body:
        return self.main_body

    @property
    def Bodies(self) -> _Bodies:
        return self.bodies

    @property
    def HybridBodies(self) -> _Geosets:
        return self.geosets

    @property
    def HybridShapeFactory(self) -> _HybridFactory:
        return self.hybrid_factory

    @property
    def ShapeFactory(self) -> _SolidFactory:
        return self.solid_factory

    @property
    def Parent(self) -> _Document:
        return _Document(self)

    def CreateReferenceFromObject(self, shape: object) -> tuple[str, object]:
        self.created_references.append(shape)
        return ("reference", shape)

    def UpdateObject(self, shape: object) -> None:
        self.updated.append(shape)


class _VisProperties:
    def __init__(self, selection: "_Selection") -> None:
        self.selection = selection

    def SetShow(self, value: int) -> None:
        for node in self.selection.nodes:
            if isinstance(node, _Body):
                node.hidden_with = value

    def GetShow(self):
        hidden = any(getattr(node, "hidden_with", 0) == 1
                     for node in self.selection.nodes)
        return (0, 1) if hidden else (0, 0)


class _Selection:
    def __init__(self) -> None:
        self.nodes: list[object] = []
        self.VisProperties = _VisProperties(self)

    def Clear(self) -> None:
        self.nodes = []

    def Add(self, node: object) -> None:
        self.nodes.append(node)


class _SystemService:
    @staticmethod
    def Evaluate(code: str, mode: int, name: str, arguments: list) -> list[float]:
        return list(arguments[0].cog)


class _App:
    SystemService = _SystemService()


class _Conn:
    def __init__(self, part: _Part) -> None:
        self.part = part
        self.selection = _Selection()
        self.app = _App()
        self.refreshed = 0
        self.active_body = None

    def ensure_connected(self) -> None:
        return None

    def get_active_part(self) -> _Part:
        return self.part

    def get_active_part_body(self):
        return self.active_body

    @property
    def hso(self) -> _Selection:
        return self.selection

    def refresh_display(self) -> None:
        self.refreshed += 1


def _tools() -> tuple[PartDesignTools, _Part, _Conn]:
    part = _Part()
    conn = _Conn(part)
    tools = PartDesignTools(connection=conn)
    return tools, part, conn


# ───────────── 1. it is offered, and the shape of the call is the report's

def test_move_body_is_offered_with_the_arguments_the_report_asked_for():
    schemas = {tool["name"]: tool for tool in PartDesignTools(connection=None)
               .get_tool_definitions()}
    assert "catia_move_body" in schemas
    properties = schemas["catia_move_body"]["inputSchema"]["properties"]
    for key in ("dx", "dy", "dz", "name", "new_body_name", "hide_source",
                "document"):
        assert key in properties, f"{key} is missing"
    assert "required" not in schemas["catia_move_body"]["inputSchema"]


# ───────────── 2. the cheap refusals happen before CATIA is asked

def test_a_zero_vector_is_refused_without_touching_the_part():
    tools = PartDesignTools(connection=None)

    class _Never:
        def ensure_connected(self) -> None:
            return None

        def get_active_part(self):
            raise AssertionError("the part must not be touched for a zero vector")

    tools.conn = _Never()                                   # type: ignore[assignment]
    with pytest.raises(CodedError) as caught:
        tools._move_body({"dx": 0, "dy": 0, "dz": 0})
    assert caught.value.code == "BAD_ARGUMENT"


def test_a_body_with_no_solid_is_refused_and_named():
    """A body of sketches only has no measurable, which is not "volume 0"."""
    tools, part, _conn = _tools()

    class _Broken(_SPA):
        def GetMeasurable(self, reference):
            raise ValueError("the body holds no solid")

    class _NoSolidPart(_Part):
        @property
        def Parent(self):
            class _Doc(_Document):
                def GetWorkbench(self, name):
                    return _Broken(self.part)
            return _Doc(self)

    part.__class__ = _NoSolidPart
    with pytest.raises(PreconditionRejected) as caught:
        tools._move_body({"dx": 5})
    assert caught.value.code == "BODY_HAS_NO_SOLID"
    assert "no solid" in str(caught.value)


# ───────────── 3. the move itself

def test_the_boundary_is_translated_by_the_normalised_vector_and_the_distance():
    tools, part, _conn = _tools()
    tools._move_body({"dx": 3, "dy": 4, "dz": 0})
    direction = [call for call in part.hybrid_factory.calls
                 if call[0] == "direction"]
    translate = [call for call in part.hybrid_factory.calls
                 if call[0] == "translate"]
    assert direction and translate, "the translate was never built"
    _kind, x, y, z = direction[0]
    assert (x, y, z) == pytest.approx((0.6, 0.8, 0.0)), (
        "AddNewTranslate takes a direction and a distance, so the vector has to "
        "be normalised - passing 3,4,0 with distance 5 would move it 25 mm")
    _kind, _element, _direction, distance = translate[0]
    assert distance == pytest.approx(5.0)


def test_the_closed_solid_goes_into_a_new_body_not_the_one_it_closes():
    tools, part, _conn = _tools()
    tools._move_body({"dx": 5})
    assert part.solid_factory.in_work_at_close == "零件几何体_MOVED", (
        "a CloseSurface whose surface is the body it lands in is circular and "
        "fails to update - measured on B30")
    assert len(part.bodies.items) == 2


def test_the_reply_prints_the_volume_the_area_and_the_cog_displacement():
    tools, part, _conn = _tools()
    reply = tools._move_body({"dx": 5})
    assert "90000.0000 -> 90000.0000" in reply
    assert "13450.0000 -> 13450.0000" in reply
    assert "centre of gravity moved (5.0000, 0.0000, 0.0000)" in reply
    assert "DEAD SOLID" in reply, (
        "the caller has to be told the result carries no feature history")
    assert "零件几何体" in reply and "零件几何体_MOVED" in reply


def test_the_source_is_hidden_and_a_hide_source_false_leaves_it_alone():
    tools, part, _conn = _tools()
    tools._move_body({"dx": 5})
    assert part.main_body.hidden_with == 1, "SetShow(1) is the hide on this build"

    tools, part, _conn = _tools()
    reply = tools._move_body({"dx": 5, "hide_source": False})
    assert part.main_body.hidden_with is None
    assert "still VISIBLE" in reply


# ───────────── 4. the three checks that make it a move and not a rebuild

def test_a_move_that_changes_the_volume_is_not_reported_as_a_move():
    tools, part, _conn = _tools()
    part.moved_volume_override = 90100.0
    with pytest.raises(CodedError) as caught:
        tools._move_body({"dx": 5})
    assert caught.value.code == "MOVE_NOT_RIGID"
    assert caught.value.modified is True, (
        "the moved body exists, so claiming nothing changed would be a lie")
    assert "90100.0000" in str(caught.value)


def test_a_move_that_leaves_the_cog_where_it_was_is_refused():
    """Same volume, same area, no displacement: the shape grew in place."""
    tools, part, _conn = _tools()
    part.moved_cog_override = [12.5, 22.5, 40.0]
    with pytest.raises(CodedError) as caught:
        tools._move_body({"dx": 5})
    assert caught.value.code == "MOVE_NOT_RIGID"
    assert "centre of gravity moved (0.0000, 0.0000, 0.0000)" in str(caught.value)


def test_a_move_that_changes_the_area_is_refused():
    tools, part, _conn = _tools()
    part.moved_area_override = 14000.0
    with pytest.raises(CodedError) as caught:
        tools._move_body({"dx": 5})
    assert caught.value.code == "MOVE_NOT_RIGID"
    assert "area 13450.0000 -> 14000.0000" in str(caught.value)
