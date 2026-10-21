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



# --------------------------------------------------------------------------
# "The last sketch" cannot be read back from body.Sketches.
# --------------------------------------------------------------------------


class FakeSketchRef:
    def __init__(self, name):
        self.Name = name


class OrderedSketches:
    """Mimics CATIA: Item(i) is tree order, which is neither creation nor
    alphabetical order. Observed on a cylinder cap: 草图.1, 草图.3, 草图.2."""

    def __init__(self, sk):
        self._items = sk
        self.Count = len(sk)

    def Item(self, key):
        if isinstance(key, int):
            return self._items[key - 1]
        for s in self._items:
            if s.Name == key:
                return s
        raise ValueError(key)


def test_last_sketch_prefers_creation_order_over_tree_order():
    newest = FakeSketchRef("草图.3")
    tree = [FakeSketchRef("草图.1"), FakeSketchRef("草图.3"), FakeSketchRef("草图.2")]
    conn = type("C", (), {
        "last_sketch": newest,
        "get_active_part": lambda self: None,
        "get_active_part_body": lambda self: type("B", (), {"Sketches": OrderedSketches(tree)})(),
    })()
    got = PartDesignTools(connection=conn)._get_last_sketch()
    # Item(Count) would hand back 草图.2 here, which is how the bolt-hole pocket
    # ended up cutting the boss off.
    assert got.Name == "草图.3"


def test_last_sketch_falls_back_when_the_handle_is_stale():
    class Dead:
        @property
        def Name(self):
            raise RuntimeError("closed")

    tree = [FakeSketchRef("草图.1")]
    conn = type("C", (), {
        "last_sketch": Dead(),
        "get_active_part": lambda self: None,
        "get_active_part_body": lambda self: type("B", (), {"Sketches": OrderedSketches(tree)})(),
    })()
    assert PartDesignTools(connection=conn)._get_last_sketch().Name == "草图.1"


def test_named_sketch_still_wins():
    tree = [FakeSketchRef("草图.1"), FakeSketchRef("草图.3")]
    conn = type("C", (), {
        "last_sketch": tree[0],
        "get_active_part": lambda self: None,
        "get_active_part_body": lambda self: type("B", (), {"Sketches": OrderedSketches(tree)})(),
    })()
    assert PartDesignTools(connection=conn)._get_last_sketch("草图.3").Name == "草图.3"


def test_list_edges_reports_lengths_so_a_fillet_can_pick_its_edge():
    """Without a length, the only handle is an index that shifts per feature.

    P08's R5 fillet sits on the 281.301 mm outline arc; the previous pass found
    it by length through custom COM, which the tool now has to offer itself.
    """
    props = CATIAMCPServer()._schemas["catia_list_edges"]
    assert props is not None
    # The schema stays an empty object - the geometry is in the reply, not the
    # arguments - so assert on the implementation instead.
    import inspect

    source = inspect.getsource(PartDesignTools._list_edges)
    assert "length_mm" in source
    assert "GetMeasurable" in source

    faces_source = inspect.getsource(PartDesignTools._list_faces)
    assert "area_mm2" in faces_source


def test_save_as_documents_its_own_ordering_constraint():
    """SaveAs after geometry fails, so the tool has to say how to keep a file."""
    from catia_mcp.tools.document import DocumentTools

    doc = [d for d in DocumentTools(connection=None).get_tool_definitions()
           if d["name"] == "catia_save_document"][0]
    text = doc["description"]
    assert "no geometry" in text
    assert "doc.Save()" in text


# --------------------------------------------------------------------------
# Renaming a component instance: the only readable name in the tree.
# --------------------------------------------------------------------------


class FakeInstance:
    def __init__(self, name, writable=True):
        self._name = name
        self._writable = writable

    @property
    def Name(self):
        return self._name

    @Name.setter
    def Name(self, value):
        if not self._writable:
            return                      # CATIA silently keeps the old name
        self._name = value


def _assembly_tools(instances):
    from catia_mcp.tools.assembly import AssemblyTools

    products = type("P", (), {
        "Count": len(instances),
        "Item": lambda self, i: instances[i - 1],
    })()
    product = type("Pr", (), {"Products": products})()
    conn = type("C", (), {
        "get_active_product": lambda self: product,
        "refresh_display": lambda self: None,
    })()
    return AssemblyTools(connection=conn)


def test_rename_sets_the_instance_name():
    instances = [FakeInstance("Part1.1"), FakeInstance("Part2.1")]
    out = _assembly_tools(instances)._rename_component(
        {"component_name": "2", "new_name": "P02_PistonRod"})
    assert instances[1].Name == "P02_PistonRod"
    assert "renamed" in out


def test_rename_reports_a_refused_rename_instead_of_claiming_success():
    # Part.Name is read-only on V5-6R2020; if a rename does not stick the reply
    # has to say so rather than echo the requested name back.
    instances = [FakeInstance("Part1.1", writable=False)]
    out = _assembly_tools(instances)._rename_component(
        {"component_name": "1", "new_name": "P01_CylinderBlock"})
    assert "not applied" in out
    assert "Part1.1" in out


def test_rename_tool_is_registered():
    from catia_mcp.tools.assembly import AssemblyTools

    names = [d["name"] for d in AssemblyTools(connection=None).get_tool_definitions()]
    assert "catia_rename_component" in names
