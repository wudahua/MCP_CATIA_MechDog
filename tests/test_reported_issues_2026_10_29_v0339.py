"""0.3.40 - the 2026-10-29 acceptance report against 0.3.39 (Theo Jansen build).

Three of its nine findings are code, and each one is asserted here.

§1 P1 - ``catia_set_component_transform(placements=[...], rotation_convention=
'basis')`` dropped the convention: the batch branch returned BEFORE the
argument was read, every entry fell back to ``matrix``, and a basis table went
in transposed. The reply said ``convention=matrix`` truthfully and uselessly.
32 of 80 instances were placed wrong; the 48 mirrored ones hid it because their
rotation is symmetric. The single-call path was always correct, so the fix is
inheritance - not the transpose the report suggested, which would have broken
the path that works.

§4 ``catia_delete_feature`` searched every body's Shapes and Sketches and
nothing else, so a GSD element ("bridging.1", "loft.1") answered "not found",
stayed visible, and was measured by the STL envelope.

§5/§8 ``closed=true`` closes by repeating the first point (documented), and the
report found the consequence: a degenerate sliver after project + split +
thick_surface. The geometry is not changed here; the reply now names the risk.
"""

import json

import pytest


# ─────────────── harness: one component, placements we can read back
class _Pos:
    def __init__(self):
        self.values = [0.0] * 12

    def SetComponents(self, values):
        self.values = [float(v) for v in values]


class _Comp:
    def __init__(self, name):
        self.Name = name
        self.Position = _Pos()


class _Products:
    def __init__(self, items):
        self._items = items
        self.Count = len(items)

    def Item(self, index):
        return self._items[index - 1]


def _assembly(components=("F Linkage.1",)):
    from catia_mcp.tools.assembly import AssemblyTools

    comps = [_Comp(name) for name in components]
    product = type("P", (), {"Products": _Products(comps), "Name": "ASM"})()
    tools = AssemblyTools(connection=None)
    tools.conn = type("C", (), {
        "get_active_product": staticmethod(lambda: product),
        "ensure_connected": staticmethod(lambda: None),
        "refresh_display": staticmethod(lambda: None),
        "app": None,
    })()
    tools._position_components = lambda position: list(position.values)
    return tools, comps


BASIS = [[1.0, 0.0, 0.0],
         [0.0, -0.1152, 0.9933],
         [0.0, -0.9933, -0.1152]]
MATRIX_OF_BASIS = [1.0, 0.0, 0.0,
                   0.0, -0.1152, -0.9933,
                   0.0, 0.9933, -0.1152]
TRANSLATION = [520.0, -266.355, -771.191]


# ─────────────── 1. the batch inherits the convention
def test_batch_inherits_the_top_level_basis_convention():
    tools, comps = _assembly()
    out = json.loads(tools._set_component_transform({
        "placements": [{"component_name": "F Linkage.1",
                        "rotation_rows": BASIS, "translation": TRANSLATION}],
        "rotation_convention": "basis",
    }))
    assert out["placed"] == 1
    assert out["rotation_convention"] == {
        "value": "basis", "from": "top level",
        "inherited_by": 1, "overridden_by": 0,
    }
    # Nine numbers are three basis vectors in order: this is the value the
    # single-call path has always produced for 'basis'.
    assert [round(v, 4) for v in comps[0].Position.values[:9]] == [1.0, 0.0, 0.0,
                                                                   0.0, -0.1152, 0.9933,
                                                                   0.0, -0.9933, -0.1152]
    assert comps[0].Position.values[:9] != pytest.approx(MATRIX_OF_BASIS)


def test_the_same_basis_rows_without_the_convention_are_the_transpose():
    """The failure the report measured: same input, no convention -> transposed."""
    tools, comps = _assembly()
    out = json.loads(tools._set_component_transform({
        "placements": [{"component_name": "F Linkage.1",
                        "rotation_rows": BASIS, "translation": TRANSLATION}],
    }))
    assert out["placed"] == 1
    assert "rotation_convention" not in out
    assert comps[0].Position.values[:9] == pytest.approx(MATRIX_OF_BASIS)


def test_an_entry_may_override_the_inherited_convention():
    tools, comps = _assembly(("A.1", "B.1"))
    out = json.loads(tools._set_component_transform({
        "placements": [
            {"component_name": "A.1", "rotation_rows": BASIS},
            {"component_name": "B.1", "rotation_rows": BASIS,
             "rotation_convention": "matrix"},
        ],
        "rotation_convention": "basis",
    }))
    assert out["rotation_convention"]["inherited_by"] == 1
    assert out["rotation_convention"]["overridden_by"] == 1
    assert comps[0].Position.values[:9] == pytest.approx(
        [1.0, 0.0, 0.0, 0.0, -0.1152, 0.9933, 0.0, -0.9933, -0.1152])
    assert comps[1].Position.values[:9] == pytest.approx(MATRIX_OF_BASIS)


def test_a_top_level_convention_that_makes_no_sense_is_refused():
    tools, _ = _assembly()
    with pytest.raises(ValueError) as error:
        tools._set_component_transform({
            "placements": [{"component_name": "F Linkage.1", "rotation_rows": BASIS}],
            "rotation_convention": "row-major",
        })
    assert "rotation_convention must be" in str(error.value)


def test_top_level_arguments_the_batch_did_not_use_are_named():
    tools, _ = _assembly()
    out = json.loads(tools._set_component_transform({
        "placements": [{"component_name": "F Linkage.1", "rotation_rows": BASIS}],
        "rotation_convention": "basis",
        "rotation_rows": BASIS,
        "component_name": "stale",
    }))
    assert out["ignored_top_level_keys"] == ["component_name", "rotation_rows"]
    assert "conflicted with the batch" in out["note"]


def test_the_single_call_still_reads_basis_as_basis():
    tools, comps = _assembly()
    out = json.loads(tools._set_component_transform({
        "component_name": "F Linkage.1", "rotation_rows": BASIS,
        "translation": TRANSLATION, "rotation_convention": "basis",
        "verify": False,
    }))
    assert "convention=basis" in out["source"]
    assert comps[0].Position.values[:9] == pytest.approx(
        [1.0, 0.0, 0.0, 0.0, -0.1152, 0.9933, 0.0, -0.9933, -0.1152])


# ─────────────── 2. delete_feature reaches into geometrical sets
class _Named:
    def __init__(self, name):
        self.Name = name


class _Coll:
    def __init__(self, items=()):
        self._items = list(items)
        self.Count = len(self._items)

    def Item(self, index):
        return self._items[index - 1]


class _Set:
    """A geometrical set: HybridShapes, HybridSketches and nested sets."""

    def __init__(self, name, shapes=(), sketches=(), children=()):
        self.Name = name
        self.HybridShapes = _Coll(shapes)
        self.HybridSketches = _Coll(sketches)
        self.HybridBodies = _Coll(children)


class _Body:
    def __init__(self, name, shapes=(), sketches=()):
        self.Name = name
        self.Shapes = _Coll(shapes)
        self.Sketches = _Coll(sketches)


class _Selection:
    def __init__(self):
        self.added = []
        self.deleted = 0
        self.deleted_items = []

    def Clear(self):
        self.added = []

    def Add(self, item):
        self.added.append(item)

    def Delete(self):
        self.deleted += 1
        self.deleted_items = list(self.added)


class _Part:
    def __init__(self, bodies=(), sets=()):
        self.Bodies = _Coll(bodies)
        self.HybridBodies = _Coll(sets)
        self.updates = 0

    def Update(self):
        self.updates += 1


def _part_design(part):
    from catia_mcp.tools.part_design import PartDesignTools

    selection = _Selection()
    tools = PartDesignTools(connection=None)
    tools.conn = type("C", (), {
        "ensure_connected": staticmethod(lambda: None),
        "get_active_part": staticmethod(lambda: part),
        "get_active_part_body": staticmethod(
            lambda: part.Bodies.Item(1) if part.Bodies.Count else None),
        "refresh_display": staticmethod(lambda: None),
        "hso": selection,
    })()
    return tools, selection


def test_delete_feature_finds_a_gsd_element():
    part = _Part(bodies=[_Body("PartBody")],
                 sets=[_Set("GSD_Set", shapes=[_Named("桥接.1"), _Named("放样曲面.1")])])
    tools, selection = _part_design(part)
    message = tools._delete_feature({"feature_name": "桥接.1"})
    assert "Deleted GSD element '桥接.1'" in message
    assert selection.deleted == 1
    assert part.updates == 1


def test_delete_feature_walks_nested_sets():
    inner = _Set("PatternAxis", shapes=[_Named("截面线.1")])
    outer = _Set("GSD_Set", children=[inner])
    part = _Part(bodies=[_Body("PartBody")], sets=[outer])
    tools, selection = _part_design(part)
    message = tools._delete_feature({"feature_name": "截面线.1"})
    assert "Deleted GSD element '截面线.1'" in message
    assert selection.deleted_items[0].Name == "截面线.1"


def test_delete_feature_can_remove_a_whole_geometrical_set():
    part = _Part(bodies=[_Body("PartBody")], sets=[_Set("GSD_Set")])
    tools, _ = _part_design(part)
    message = tools._delete_feature({"feature_name": "GSD_Set"})
    assert "Deleted geometrical set 'GSD_Set'" in message


def test_delete_feature_still_prefers_a_solid_feature_of_the_same_name():
    part = _Part(bodies=[_Body("PartBody", shapes=[_Named("凹槽.1")])],
                 sets=[_Set("GSD_Set", shapes=[_Named("凹槽.1")])])
    tools, selection = _part_design(part)
    tools._delete_feature({"feature_name": "凹槽.1"})
    assert not hasattr(selection.deleted_items[0], "HybridShapes")


def test_a_missing_name_says_where_it_looked():
    part = _Part(bodies=[_Body("PartBody")], sets=[_Set("GSD_Set")])
    tools, _ = _part_design(part)
    with pytest.raises(RuntimeError) as error:
        tools._delete_feature({"feature_name": "不存在.1"})
    assert "geometrical set" in str(error.value)


# ─────────────── 3. the closed-spline reply names the seam
class _SketchFactory:
    def CreatePoint(self, x, y):
        return (x, y)

    def CreateSpline(self, points):
        return type("S", (), {"Name": "样条.1"})()


def _sketcher():
    from catia_mcp.tools.sketcher import SketcherTools

    tools = SketcherTools(connection=None)
    tools._ensure_sketch_open = lambda: None
    tools._note_element = lambda *a, **k: None
    tools._active_factory = _SketchFactory()
    return tools


def test_a_closed_spline_says_it_is_not_a_periodic_one():
    reply = _sketcher()._draw_spline([[0, 0], [10, 0], [10, 5], [0, 5]], closed=True)
    assert "repeating the first point" in reply
    assert "NOT a periodic spline" in reply
    assert "zero-volume sliver" in reply


def test_an_open_spline_does_not_carry_the_warning():
    reply = _sketcher()._draw_spline([[0, 0], [10, 0], [10, 5]], closed=False)
    assert "periodic" not in reply


# ─────────────── 4. a two-section ruled loft appended its blend twice
class _Geoset:
    """Refuses what CATIA refuses: appending an element that is already in it."""

    def __init__(self):
        self.Name = "GSD_Set"
        self.appended = []

    def AppendHybridShape(self, shape):
        if shape in self.appended:
            raise RuntimeError("方法 AppendHybridShape 失败")
        self.appended.append(shape)


class _Blend:
    def __init__(self, number):
        self.Name = f"桥接.{number}"
        self.curves = {}

    def SetCurve(self, index, reference):
        self.curves[index] = reference


class _Join:
    def __init__(self, first, second):
        self.Name = "接合.1"
        self.elements = [first, second]

    def AddElement(self, reference):
        self.elements.append(reference)


class _Factory:
    def __init__(self):
        self.blends = 0

    def AddNewBlend(self):
        self.blends += 1
        return _Blend(self.blends)

    def AddNewJoin(self, first, second):
        return _Join(first, second)


class _GSDPart:
    def __init__(self):
        self.InWorkObject = None
        self.updated = []

    def UpdateObject(self, item):
        self.updated.append(item)

    def CreateReferenceFromObject(self, item):
        return item


def _gsd():
    from catia_mcp.tools.gsd import GSDTools

    part, geoset, factory = _GSDPart(), _Geoset(), _Factory()
    tools = GSDTools(connection=None)
    tools.conn = type("C", (), {
        "get_active_part": staticmethod(lambda: part),
        "ensure_connected": staticmethod(lambda: None),
        "refresh_display": staticmethod(lambda: None),
    })()
    tools._factory = lambda: factory
    tools._get_geoset = lambda: geoset
    tools._ref = lambda section: section
    tools._safe_update = lambda shape: None
    return tools, geoset, factory


def test_two_section_ruled_loft_appends_the_blend_once():
    tools, geoset, factory = _gsd()
    message = tools._ruled_loft(["section_a", "section_b"], "fan_blade")
    assert factory.blends == 1
    assert len(geoset.appended) == 1
    assert "between 2 sections" in message
    assert "fan_blade" in message


def test_three_section_ruled_loft_appends_blends_and_one_join():
    tools, geoset, factory = _gsd()
    tools._ruled_loft(["a", "b", "c"], "blade")
    assert factory.blends == 2
    assert len(geoset.appended) == 3      # two blends + the join
    assert isinstance(geoset.appended[-1], _Join)
