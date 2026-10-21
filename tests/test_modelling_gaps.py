"""The four modelling gaps closed for the cylinder spec - no CATIA needed.

Each fix was verified on a live V5-6R2020 B30 session first (see FORK.md); these
tests lock the shape of the API surface and the axis resolution logic so a later
refactor cannot quietly take the capability away again.
"""

import pytest

from catia_mcp.server import CATIAMCPServer
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.sketcher import SketcherTools


class FakeAxis:
    """Stand-in for the sketch's absolute-axis element."""

    HorizontalReference = "ref:H"
    VerticalReference = "ref:V"


class FakeElements:
    """Item(1) is the axis; Item(<name>) fails, as it does on a Chinese build."""

    def __init__(self):
        self.Count = 1
        self._axis = FakeAxis()

    def Item(self, key):
        if key == 1:
            return self._axis
        raise ValueError(f"no element named {key!r}")


class FakeSketch:
    Name = "草图.1"

    def __init__(self):
        self.GeometricElements = FakeElements()


def tools() -> PartDesignTools:
    # Only the pure helpers are exercised here; nothing reaches COM.
    return PartDesignTools(connection=None)


def schema(server: CATIAMCPServer, name: str) -> dict:
    return server._schemas[name]


# --------------------------------------------------------------------------
# The API surface must offer the capability, not just implement it.
# --------------------------------------------------------------------------


def test_create_sketch_accepts_an_offset():
    props = schema(CATIAMCPServer(), "catia_create_sketch")["properties"]
    assert "offset" in props
    assert props["offset"]["type"] == "number"
    # The plane name itself stays a plain enum inside oneOf, so every existing
    # call (plane='xy') keeps validating.
    planes = props["plane"]["oneOf"]
    assert {"type": "string", "enum": ["xy", "yz", "zx"]} in planes


def test_create_sketch_accepts_a_face_and_an_offset_object():
    props = schema(CATIAMCPServer(), "catia_create_sketch")["properties"]
    objects = [p for p in props["plane"]["oneOf"] if p["type"] == "object"]
    assert len(objects) == 1
    assert objects[0]["properties"]["type"]["enum"] == ["offset", "face"]
    assert "index" in objects[0]["properties"]
    assert "distance" in objects[0]["properties"]


def test_sketch_line_can_be_construction():
    props = schema(CATIAMCPServer(), "catia_sketch_line")["properties"]
    assert props["construction"]["type"] == "boolean"
    assert props["construction"]["default"] is False


@pytest.mark.parametrize("tool", ["catia_shaft", "catia_groove"])
def test_revolution_tools_take_an_axis(tool):
    props = schema(CATIAMCPServer(), tool)["properties"]
    assert props["axis"]["enum"] == ["horizontal", "vertical"]
    assert props["axis"]["default"] == "horizontal"


@pytest.mark.parametrize("tool", ["catia_pocket", "catia_hole"])
def test_cut_tools_take_through_all_and_do_not_require_depth(tool):
    definition = schema(CATIAMCPServer(), tool)
    assert definition["properties"]["through_all"]["type"] == "boolean"
    # depth stays available but must not be mandatory: through_all replaces it
    assert "depth" not in definition.get("required", [])


# --------------------------------------------------------------------------
# Axis resolution: index 1, never the localized name.
# --------------------------------------------------------------------------


def test_absolute_axis_is_looked_up_by_index():
    # A Chinese build names it '绝对轴' and Item('AbsoluteAxis') raises E_FAIL,
    # so a name lookup would break every revolution on those installs.
    assert PartDesignTools._sketch_absolute_axis(FakeSketch()) is not None


def test_absolute_axis_failure_is_actionable():
    class Empty:
        Name = "s"
        GeometricElements = type("E", (), {"Count": 0, "Item": lambda self, k: (_ for _ in ()).throw(RuntimeError("no"))})()

    with pytest.raises(RuntimeError, match="no absolute axis"):
        PartDesignTools._sketch_absolute_axis(Empty())


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("horizontal", "ref:H"), ("vertical", "ref:V"), ("HORIZONTAL", "ref:H")],
)
def test_revolve_axis_maps_to_the_sketch_axis(mode, expected):
    assert tools()._revolve_axis_reference(None, FakeSketch(), mode) == expected


def test_revolve_axis_rejects_unknown_modes():
    with pytest.raises(ValueError, match="horizontal"):
        tools()._revolve_axis_reference(None, FakeSketch(), "diagonal")


# --------------------------------------------------------------------------
# Removing a broken feature must never raise on top of the real error.
# --------------------------------------------------------------------------


def test_remove_feature_swallows_a_broken_selection():
    class BrokenSelection:
        def Clear(self):
            raise RuntimeError("no selection")

    broken = PartDesignTools(connection=type("C", (), {"hso": BrokenSelection()})())
    broken._remove_feature(None, object())  # must not raise


# --------------------------------------------------------------------------
# Sketch support resolution: a main plane, an offset, or a solid face.
# --------------------------------------------------------------------------


class FakeOrigin:
    PlaneXY = "plane:xy"
    PlaneYZ = "plane:yz"
    PlaneZX = "plane:zx"


class FakeDatum:
    Name = ""


class FakeHybridShapeFactory:
    def AddNewPlaneOffset(self, ref, distance, opposite):
        plane = FakeDatum()
        plane.Name = f"datum:{ref}:{distance:g}:{opposite}"
        return plane


class FakePart:
    OriginElements = FakeOrigin()
    HybridShapeFactory = FakeHybridShapeFactory()

    def CreateReferenceFromObject(self, obj):
        return f"ref:{obj}"

    def UpdateObject(self, obj):
        pass


class FakeBody:
    class Shapes:
        Count = 1

    def __init__(self):
        self.inserted = []

    def InsertHybridShape(self, shape):
        self.inserted.append(shape)


class FakeCell:
    def __init__(self, index):
        self.Reference = f"ref:face{index}"
        self.Name = f"Face:(Brp:(Pad.1;{index}))"


class FakeSelection:
    """Mimics HSO: Search('Topology.Face,sel') fills the selection."""

    def __init__(self, count=2):
        self._items = []
        self.Count = 0
        self._count = count
        self.queries = []

    def Clear(self):
        self._items = []
        self.Count = 0

    def Add(self, obj):
        pass

    def Search(self, query):
        self.queries.append(query)
        self._items = [FakeCell(i) for i in range(1, self._count + 1)]
        self.Count = self._count

    def Item(self, index):
        return self._items[index - 1]


def sketcher(count=2):
    conn = type("C", (), {"hso": FakeSelection(count)})()
    return SketcherTools(connection=conn)


def test_plane_name_still_resolves_to_the_main_plane():
    ref, where = sketcher()._resolve_support(FakePart(), FakeBody(), "xy", None)
    assert ref == "ref:plane:xy"
    assert where == "XY (front)"


def test_offset_object_inserts_a_datum_plane_into_the_body():
    body = FakeBody()
    ref, where = sketcher()._resolve_support(
        FakePart(), body, {"type": "offset", "base": "xy", "distance": 40}, None
    )
    # Into the body, not a geometrical set: AddNewPad rejects a sketch whose
    # support lives outside the body.
    assert len(body.inserted) == 1
    assert body.inserted[0].Name == "Datum_xy_40"
    assert where == "XY (front) offset 40 mm"
    assert ref.startswith("ref:")


def test_face_object_resolves_through_the_topology_search():
    ref, where = sketcher()._resolve_support(
        FakePart(), FakeBody(), {"type": "face", "index": 2}, None
    )
    assert ref == "ref:face2"
    # The reply has to warn that a face has its own coordinate frame: guessing
    # wrong does not raise, the cut simply removes nothing.
    assert where.startswith("Face.2 of the solid")
    assert "face's own frame" in where


def test_face_index_out_of_range_says_how_many_exist():
    with pytest.raises(RuntimeError, match="exposes 2 face"):
        sketcher()._resolve_support(
            FakePart(), FakeBody(), {"type": "face", "index": 5}, None
        )


@pytest.mark.parametrize("index", [None, 1.5, "2", True])
def test_face_index_must_be_an_integer(index):
    with pytest.raises(ValueError, match="integer 'index'"):
        sketcher()._resolve_support(
            FakePart(), FakeBody(), {"type": "face", "index": index}, None
        )


def test_unknown_plane_type_is_rejected():
    with pytest.raises(ValueError, match="Unknown plane type"):
        sketcher()._resolve_support(FakePart(), FakeBody(), {"type": "tangent"}, None)


def test_unknown_offset_base_is_rejected():
    with pytest.raises(ValueError, match="Unknown offset base"):
        sketcher()._resolve_support(
            FakePart(), FakeBody(), {"type": "offset", "base": "ab", "distance": 1}, None
        )


def test_non_numeric_offset_distance_is_rejected():
    with pytest.raises(ValueError, match="numeric 'distance'"):
        sketcher()._resolve_support(
            FakePart(), FakeBody(), {"type": "offset", "distance": "40"}, None
        )


def test_plane_must_be_a_string_or_an_object():
    with pytest.raises(ValueError, match="type' of"):
        sketcher()._resolve_support(FakePart(), FakeBody(), 3, None)

