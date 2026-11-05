"""What porting drafting cost, pinned so it is not undone.

Everything here is offline: the failures being guarded were measured on B30
2026-11-05 and each test names the measurement it protects. The theme is the
same as 0.3.62's: a number or an index that looked right and was not.

  1. Face.N used to be searched from the LAST FEATURE with the English query
     "Topology.Face,sel". On a Chinese install that query raises and the same
     selection answers 拓扑.面,sel - so face-driven projection could not work at
     all. It now goes through topology_cells, like catia_list_faces.
  2. A view's GeometricElements item 1 is the ABSOLUTE AXIS, exactly as in a
     sketch. Index 1 reached Dimensions.Add and CATIA answered a bare
     方法 Add 失败.
  3. A distance between a horizontal and a vertical line is refused BY CATIA.
     The tool used to forward that verbatim.
  4. catia_drawing_add_view demanded part_name whenever two parts were open,
     including when the caller had just built one of them.
  5. First-angle and third-angle layouts put a "right" view on opposite sides.
"""

from __future__ import annotations

import pytest

from catia_mcp.tools.drawing import DraftingTools


# ── stubs ────────────────────────────────────────────────────────────────────


class _Doc:
    def __init__(self, name: str) -> None:
        self.Name = name


class _Docs:
    def __init__(self, *names: str) -> None:
        self._docs = [_Doc(name) for name in names]

    @property
    def Count(self) -> int:
        return len(self._docs)

    def Item(self, index: int):
        return self._docs[index - 1]


class _Conn:
    def __init__(self, *names: str) -> None:
        self.documents = _Docs(*names)
        self.last_part_name: str | None = None
        self.app = None

    def ensure_connected(self) -> None:
        return None

    def refresh_display(self) -> None:
        return None


class _Element:
    def __init__(self, name: str) -> None:
        self.Name = name


class _Elements:
    def __init__(self, count: int) -> None:
        self.Count = count

    def Item(self, index: int):
        return _Element("绝对轴" if index == 1 else f"直线.{index - 1}")


class _Dimensions:
    def __init__(self, fail: bool = False) -> None:
        self.Count = 0
        self.fail = fail
        self.calls: list[tuple] = []

    def Add(self, *args):
        self.calls.append(args)
        if self.fail:
            raise RuntimeError(
                "(-2147352567, '发生意外。', (0, 'CATIADrawingDimensions', "
                "'方法 Add 失败', None, 0, -2147467259), None)")
        self.Count += 1

        class _Dim:
            Name = "Dimension 1"

        return _Dim()


class _View:
    def __init__(self, name: str = "Manual View", *, elements: int = 3,
                 fail_add: bool = False) -> None:
        self.Name = name
        self.GeometricElements = _Elements(elements)
        self.Dimensions = _Dimensions(fail=fail_add)
        self.activated = False

    def Activate(self) -> None:
        self.activated = True


# ── 1. the part this session built is the part meant -----------------─────────


def test_two_open_parts_resolve_to_the_one_this_session_built():
    conn = _Conn("Part1.CATPart", "Part2.CATPart")
    conn.last_part_name = "Part2.CATPart"
    tools = DraftingTools(conn)
    found = tools._find_part_document(None)
    assert str(found.Name) == "Part2.CATPart", (
        "requiring part_name here made the caller read back a document name "
        "CATIA chose - measured on B30, where 'Pass part_name' was the answer "
        "to a caller that had just built the part")


def test_an_explicit_part_name_still_wins_over_the_remembered_one():
    conn = _Conn("Part1.CATPart", "Part2.CATPart")
    conn.last_part_name = "Part2.CATPart"
    tools = DraftingTools(conn)
    assert str(tools._find_part_document("Part1.CATPart").Name) == "Part1.CATPart"


def test_ambiguity_is_still_refused_when_nothing_is_remembered():
    tools = DraftingTools(_Conn("Part1.CATPart", "Part2.CATPart"))
    with pytest.raises(RuntimeError) as caught:
        tools._find_part_document(None)
    message = str(caught.value)
    assert "part_name" in message and "Part1.CATPart" in message


def test_one_open_part_needs_no_arguments():
    tools = DraftingTools(_Conn("Only.CATPart"))
    assert str(tools._find_part_document(None).Name) == "Only.CATPart"


# ── 2 and 3. the absolute axis, and a dimension the geometry cannot carry ────


def test_element_index_one_is_the_absolute_axis_not_the_first_line():
    tools = DraftingTools(_Conn())
    view = _View()
    tools._find_view = lambda name: view  # type: ignore[assignment]

    with pytest.raises(RuntimeError) as caught:
        tools._add_dimension({"type": "length", "element_index_1": 1})
    message = str(caught.value)
    assert "ABSOLUTE AXIS" in message
    assert "index 2" in message, (
        "the reply has to say which index WOULD have worked, or the caller is "
        "left with the same bare failure CATIA gave")
    assert view.Dimensions.calls == [], (
        "the call must be refused before it reaches CATIA - the point is that "
        "CATIA's own answer names neither the axis nor the index")


def test_the_second_slot_is_guarded_too():
    tools = DraftingTools(_Conn())
    view = _View()
    tools._find_view = lambda name: view  # type: ignore[assignment]
    with pytest.raises(RuntimeError) as caught:
        tools._add_dimension({"type": "distance", "element_index_1": 2,
                              "element_index_2": 1})
    assert "geometry_index" not in str(caught.value)  # it is a view, not a sketch
    assert "ABSOLUTE AXIS" in str(caught.value)


def test_an_index_past_the_end_says_how_many_there_are():
    tools = DraftingTools(_Conn())
    view = _View(elements=3)
    tools._find_view = lambda name: view  # type: ignore[assignment]
    with pytest.raises(RuntimeError) as caught:
        tools._add_dimension({"type": "length", "element_index_1": 9})
    message = str(caught.value)
    assert "3 element(s)" in message
    assert "catia_drawing_list_view_geometry" in message


def test_a_refused_dimension_explains_which_pairings_work():
    tools = DraftingTools(_Conn())
    view = _View(fail_add=True)
    tools._find_view = lambda name: view  # type: ignore[assignment]

    with pytest.raises(RuntimeError) as caught:
        tools._add_dimension({"type": "distance", "element_index_1": 2,
                              "element_index_2": 3})
    message = str(caught.value)
    assert "parallel" in message
    assert "generative view cannot be dimensioned" in message
    assert "方法 Add 失败" in message, (
        "the underlying CATIA text is kept, but only after the explanation - "
        "it is the only diagnostic when the pairing is unusual")


def test_a_working_dimension_still_returns_its_name():
    tools = DraftingTools(_Conn())
    view = _View()
    tools._find_view = lambda name: view  # type: ignore[assignment]
    reply = tools._add_dimension({"type": "distance", "element_index_1": 2,
                                  "element_index_2": 3})
    assert "Dimension 1" in reply
    assert view.activated, "Dimensions.Add needs the view active (measured)"


# ── 4. face resolution must not use the localized-blind English search ───────


class _PlaneMeasurable:
    def __init__(self, plane, error=None) -> None:
        self._plane = plane
        self._error = error
        self.Area = 314.159e-6

    def GetPlane(self):  # never called directly; read_measurable_array is patched
        raise AssertionError("GetPlane must go through read_measurable_array")


class _Spa:
    def __init__(self, measurable) -> None:
        self._measurable = measurable

    def GetMeasurable(self, reference):
        return self._measurable


class _Selection:
    """Fails loudly if anything searches it: face resolution must not."""

    def Search(self, query):
        raise AssertionError(f"Selection.Search({query!r}) was called")

    def Add(self, _obj):
        raise AssertionError("Selection.Add was called")

    def Clear(self):
        return None


class _Body:
    Name = "零件几何体"
    Shapes = type("S", (), {"Count": 1})()


class _Bodies:
    Count = 1

    def Item(self, _index):
        return _Body()


class _Part:
    MainBody = _Body()
    Bodies = _Bodies()


class _PartDoc:
    Part = _Part()
    Selection = _Selection()

    def __init__(self, spa) -> None:
        self._spa = spa

    def GetWorkbench(self, _name):
        return self._spa

    def Activate(self):
        return None


@pytest.fixture()
def face_env(monkeypatch):
    import catia_mcp.topo_search as topo
    import catia_mcp.tools.drawing as drawing

    calls = {"cells": 0}

    class _Cell:
        Reference = "ref"

    def fake_cells(conn, body, kind, stats=None):
        calls["cells"] += 1
        assert kind == "Face"
        return [_Cell(), _Cell()]

    monkeypatch.setattr(topo, "topology_cells", fake_cells)
    return drawing, calls


def test_face_plane_uses_topology_cells_and_never_searches(face_env, monkeypatch):
    drawing, calls = face_env
    plane = [0.0, 0.0, 10.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    monkeypatch.setattr(drawing, "read_measurable_array",
                        lambda app, m, method, count: plane)

    tools = DraftingTools(_Conn())
    doc = _PartDoc(_Spa(_PlaneMeasurable(plane)))
    assert tools._face_plane(doc, None, "Face.2") == plane
    assert calls["cells"] == 1, (
        "the English query 'Topology.Face,sel' raises 方法 Search 失败 on a "
        "Chinese install (B30 2026-11-05); the fork already resolves Face.N "
        "through topology_cells, which knows 拓扑.面")


def test_a_face_past_the_end_lists_how_many_the_body_has(face_env):
    drawing, _ = face_env
    tools = DraftingTools(_Conn())
    with pytest.raises(RuntimeError) as caught:
        tools._face_plane(_PartDoc(_Spa(_PlaneMeasurable([0] * 9))), None, "Face.9")
    message = str(caught.value)
    assert "out of range" in message and "2 face(s)" in message


def test_a_non_planar_face_is_named_as_such_not_as_a_script_crash(face_env, monkeypatch):
    drawing, _ = face_env

    def boom(app, measurable, method, count):
        raise RuntimeError("(-2147352567, '发生意外。', (0, None, "
                           "'执行脚本(Evaluate, MemoryMacro, catia_mcp_out)')")

    monkeypatch.setattr(drawing, "read_measurable_array", boom)
    tools = DraftingTools(_Conn())
    with pytest.raises(RuntimeError) as caught:
        tools._face_plane(_PartDoc(_Spa(_PlaneMeasurable(None))), None, "Face.1")
    message = str(caught.value)
    assert "has no plane" in message
    assert "cylindrical" in message
    assert "314.2" in message, (
        "the area proves the face is fine, which is what separates 'not planar' "
        "from 'this face is broken'")


def test_a_badly_named_face_is_refused_before_any_work(face_env):
    drawing, calls = face_env
    tools = DraftingTools(_Conn())
    with pytest.raises(ValueError):
        tools._face_plane(_PartDoc(_Spa(_PlaneMeasurable([0] * 9))), None, "Face")
    assert calls["cells"] == 0


# ── 5. first angle mirrors the layout ────────────────────────────────────────


class _GenBehaviour:
    def __init__(self) -> None:
        self.Document = "PART"

    def Update(self) -> None:
        return None

    def DefineProjectionView(self, parent, code) -> None:
        self.code = code


class _Links:
    def CopyLinksTo(self, other) -> None:
        return None


class _SheetView:
    def __init__(self, name: str) -> None:
        self.Name = name
        self.GenerativeBehavior = _GenBehaviour()
        self.GenerativeLinks = _Links()
        self.x = 0.0
        self.y = 0.0
        self.Scale = 1.0

    def IsGenerative(self) -> bool:
        return True


class _ViewList:
    def __init__(self) -> None:
        self.items = [_SheetView("Main View"), _SheetView("Background View")]
        self.active = None

    @property
    def Count(self) -> int:
        return len(self.items)

    def Item(self, index: int):
        return self.items[index - 1]

    def Add(self, name: str):
        view = _SheetView(name)
        self.items.append(view)
        return view

    def Remove(self, name: str) -> None:
        self.items = [v for v in self.items if v.Name != name]

    @property
    def ActiveView(self):
        return self.active or self.items[0]


class _Sheet:
    def __init__(self, projection_method: int) -> None:
        self.ProjectionMethod = projection_method
        self.Views = _ViewList()
        self.Name = "图纸.1"


class _Drawing:
    def __init__(self, projection_method: int) -> None:
        self._sheet = _Sheet(projection_method)
        self.Name = "Drawing1.CATDrawing"

        class _Sheets:
            ActiveSheet = self._sheet

        self.Sheets = _Sheets()

    def Activate(self) -> None:
        return None


def _project(direction: str, method: int) -> float:
    drawing = _Drawing(method)
    tools = DraftingTools(_Conn())
    tools._find_active_drawing = lambda: drawing  # type: ignore[assignment]
    parent = _SheetView("Front View")
    parent.x, parent.y = 300.0, 150.0
    drawing.Sheets.ActiveSheet.Views.items.append(parent)
    tools._find_view = lambda name: parent  # type: ignore[assignment]
    tools.execute("catia_drawing_projection_view",
                  {"direction": direction, "parent_view": "Front View", "gap": 100})
    added = drawing.Sheets.ActiveSheet.Views.items[-1]
    assert added is not parent
    return added.x


def test_first_angle_puts_the_right_view_on_the_left():
    assert _project("right", 0) == pytest.approx(200.0), (
        "CatSheetProjectionMethod 0 is first angle, where the view seen from "
        "the right is drawn on the left (measured on B30: a fresh sheet reads 0)")


def test_third_angle_puts_the_right_view_on_the_right():
    assert _project("right", 1) == pytest.approx(400.0)


# ── the module itself ───────────────────────────────────────────────────────


def test_all_eleven_tools_are_declared_once():
    tools = DraftingTools(_Conn())
    names = [d["name"] for d in tools.get_tool_definitions()]
    assert len(names) == len(set(names)) == 11
    assert "catia_drawing_generate_dimensions" in names


def test_the_face_parameter_says_its_index_is_session_scoped():
    tools = DraftingTools(_Conn())
    schema = {d["name"]: d for d in tools.get_tool_definitions()}["catia_drawing_add_view"]
    description = schema["inputSchema"]["properties"]["face"]["description"]
    assert "SESSION-SCOPED" in description, (
        "the same plate gave the hole's cylinder at Face.1 in one build and at "
        "Face.7 in another, so an index carried across sessions picks the wrong "
        "geometry - silently")


def test_the_empty_dimension_note_names_a_constraint_type_that_exists():
    tools = DraftingTools(_Conn())
    source = tools._generate_dimensions.__code__.co_consts
    joined = " ".join(str(const) for const in source if isinstance(const, str))
    assert "'length' type" in joined or "no 'length' type" in joined, (
        "the note used to tell the caller to use a 'length' constraint, which "
        "catia_sketch_constraint does not have: a length is 'distance' with one "
        "element")
