# -*- coding: utf-8 -*-
"""Regression tests for the two items the 2026-11-06 reports left open (0.3.73).

P2-4: `matches_request` was `deviation < 1e-6`, so 13 of 25 correct placements
were reported as not matching. The deviation came from unit vectors rounded to 4
decimals - 4.10e-05 by construction - and the reply put `ok: true` beside
`matches_request: false` with no threshold to compare against.

P2-6: `catia_set_visibility` called `get_active_part()` first, so on a Product it
answered PRECONDITION_FAILED "exposes no 'Part'" - the report's 11-part cleanup
took 33 calls. It now walks the component tree and touches each part through that
part's OWN Selection, so the active document does not move.
"""

from __future__ import annotations

import json
import math
from types import SimpleNamespace

import pytest

from catia_mcp.results import ArgumentRejected, PreconditionRejected


# ── P2-4: the placement tolerance ──────────────────────────────────────────

def _assembly_tools():
    from catia_mcp.tools.assembly import AssemblyTools

    tools = AssemblyTools(connection=None)
    tools.conn = SimpleNamespace(refresh_display=lambda: None,
                                 ensure_connected=lambda: None)
    return tools


def _tool_schema(name: str):
    from catia_mcp.tools.assembly import AssemblyTools

    for tool in AssemblyTools(None).get_tool_definitions():
        if tool["name"] == name:
            return tool
    raise AssertionError(f"{name} is gone")


def test_the_tolerance_is_declared_and_defaults_to_one_micron():
    tool = _tool_schema("catia_set_component_transform")
    tolerance = tool["inputSchema"]["properties"]["tolerance"]
    assert tolerance["default"] == 0.001
    assert tolerance["exclusiveMinimum"] == 0
    assert "matches_request" in tolerance["description"]
    # The report's case: 4-decimal vectors cannot do better than 1e-5..1e-4.
    assert "4 decimal" in tolerance["description"]


@pytest.mark.parametrize("value, expected", [
    (None, 0.001), (0.01, 0.01), (1e-6, 1e-6), ("0.002", 0.002),
])
def test_the_tolerance_is_read_and_defaulted(value, expected):
    assert _assembly_tools()._placement_tolerance(value) == expected


def test_a_zero_or_negative_tolerance_is_refused():
    tools = _assembly_tools()
    for bad in (0, -1, "wide"):
        with pytest.raises(ArgumentRejected):
            tools._placement_tolerance(bad)


def _orthonormalise(values: list[float]) -> list[float]:
    """What CATIA stores: the rotation it is given, re-orthonormalised.

    Measured on B30 2026-11-07: nine values rounded to 4 decimals go in, and the
    read-back is a proper rotation within 1e-9 - so `det R == +1` still holds and
    the only deviation is the rounding itself (1.9e-05 for 30 degrees). A model
    that skipped this step would report `right_handed: false` and a "the matrix
    mirrors the part" warning, which is NOT what CATIA does.
    """
    rows = [list(values[0:3]), list(values[3:6]), list(values[6:9])]

    def cross(a, b):
        return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
                a[0] * b[1] - a[1] * b[0]]

    def unit(v):
        length = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / length for x in v]

    x_axis = unit(rows[0])
    z_axis = unit(cross(rows[0], rows[1]))
    y_axis = cross(z_axis, x_axis)
    return x_axis + y_axis + z_axis + list(values[9:12])


class _Position:
    """Holds what CATIA was given; the read-back is the normalised version."""

    def __init__(self) -> None:
        self.values: list[float] = []

    def SetComponents(self, values) -> None:
        self.values = [float(v) for v in values]


def _placement_tools(noise: float = 0.0):
    tools = _assembly_tools()
    component = SimpleNamespace(Name="P10.1", Position=_Position())
    tools.conn.get_active_product = lambda: SimpleNamespace(Name="ASM")
    tools._resolve_component = lambda product, name: component
    tools._position_components = lambda position: _orthonormalise(position.values)
    return tools


#: What B30 actually answered for a 30-degree row set rounded to 4 decimals
#: (2026-11-07). The report's 25 instances were 4.10e-05; both are 1e-5-scale.
_ROUNDED_ROWS = [[0.866, -0.5, 0.0], [0.5, 0.866, 0.0], [0.0, 0.0, 1.0]]
_ROUNDED_DEVIATION = 1.9052629e-05


def test_a_four_decimal_placement_matches_by_default():
    """1.9e-05 was the deviation; `< 1e-6` called that a failure."""
    tools = _placement_tools()
    reply = json.loads(tools._set_component_transform({
        "component_name": "P10.1",
        "rotation_rows": _ROUNDED_ROWS,
        "translation": [200.0, 0.0, 0.0],
    }))
    verified = reply["verified"]
    assert verified["max_deviation"] == pytest.approx(_ROUNDED_DEVIATION, rel=0.01)
    assert verified["tolerance"] == 0.001
    assert verified["matches_request"] is True
    assert "warning" not in reply, (
        "a deviation below the published tolerance is not a warning")


def test_a_tighter_tolerance_still_reports_the_same_deviation():
    tools = _placement_tools()
    reply = json.loads(tools._set_component_transform({
        "component_name": "P10.1",
        "position_components": [row for rows in _ROUNDED_ROWS for row in rows]
                              + [0.0, 0.0, 0.0],
        "tolerance": 1e-6,
    }))
    verified = reply["verified"]
    assert verified["matches_request"] is False
    assert verified["max_deviation"] == pytest.approx(_ROUNDED_DEVIATION, rel=0.01)
    assert "1e-06 mm" in reply["warning"]


def test_the_batch_hands_its_tolerance_down_to_every_entry():
    """A batch that judged entries against a different threshold would be worse
    than no threshold: 25 rows, one verdict, no way to know which rule."""
    from catia_mcp.tools.assembly import AssemblyTools

    source = AssemblyTools._apply_placements.__doc__ or ""
    assert source  # the method exists

    tools = _placement_tools()
    tools.conn.get_active_product = lambda: SimpleNamespace(Name="ASM")
    seen = []

    def fake(entry):
        seen.append(entry.get("tolerance"))
        return {"component": entry["component_name"],
                "verified": {"max_deviation": 4.1e-05,
                             "matches_request": True,
                             "tolerance": entry.get("tolerance")}}

    tools._set_component_transform = fake
    reply = json.loads(tools._apply_placements(
        [{"component_name": "A"}, {"component_name": "B"}],
        tolerance=0.002))
    assert seen == [0.002, 0.002]
    assert reply["tolerance"] == 0.002
    assert "tolerance" in reply["tolerance_note"]


# ── P2-6: visibility on a Product ──────────────────────────────────────────

class _Selection:
    def __init__(self) -> None:
        self.current = None
        self.shown: dict[int, int] = {}
        self.cleared = 0

    def Clear(self) -> None:
        self.current = None
        self.cleared += 1

    def Add(self, node) -> None:
        self.current = node

    @property
    def VisProperties(self):
        return self

    def SetShow(self, value: int) -> None:
        self.shown[id(self.current)] = value


def _collection(items):
    """Index by number OR by name, like a CATIA collection does."""

    def item(key):
        if isinstance(key, int):
            return items[key - 1]
        for candidate in items:
            if str(getattr(candidate, "Name", "")) == key:
                return candidate
        raise KeyError(key)

    return SimpleNamespace(Count=len(items), Item=item)


def _sketch(name):
    return SimpleNamespace(Name=name)


def _part(name, sketches=(), bodies=(), planes=True):
    body = SimpleNamespace(Name="零件几何体",
                           Sketches=_collection(list(sketches)),
                           Shapes=_collection([]))
    part = SimpleNamespace(
        Bodies=_collection([body, *bodies]),
        HybridBodies=_collection([]),
        OriginElements=SimpleNamespace(PlaneXY=_sketch("PlaneXY"),
                                       PlaneYZ=_sketch("PlaneYZ"),
                                       PlaneZX=_sketch("PlaneZX"))
        if planes else SimpleNamespace(),
    )
    return part


def _doc(name, kind="Part", selection=None):
    return SimpleNamespace(Name=name, Selection=selection or _Selection(),
                           Type=kind)


def _product_tree(*children):
    """A product whose `Products` yields the given (child, doc) pairs."""
    items = []
    for doc, nested in children:
        items.append(SimpleNamespace(
            ReferenceProduct=SimpleNamespace(Parent=doc),
            Products=nested.Products if nested is not None else _collection([])))
    return SimpleNamespace(Products=_collection(items))


def _visibility_tools(active_doc, parts_by_doc, products_by_doc):
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)

    def part_of(doc):
        if doc.Name in parts_by_doc:
            return parts_by_doc[doc.Name]
        raise PreconditionRejected("not a part")

    def product_of(doc):
        if doc.Name in products_by_doc:
            return products_by_doc[doc.Name]
        raise PreconditionRejected("not a product")

    tools.conn = SimpleNamespace(active_document=active_doc,
                                 part_of=part_of, product_of=product_of,
                                 ensure_connected=lambda: None,
                                 refresh_display=lambda: None)
    return tools


def test_a_product_answers_for_every_part_under_it():
    base_doc = _doc("P01.CATPart")
    arm_doc = _doc("P02.CATPart")
    asm_doc = _doc("ASM.CATProduct", kind="Product")
    product = _product_tree((base_doc, None), (arm_doc, None))
    tools = _visibility_tools(asm_doc,
                              {"P01.CATPart": _part("P01", [_sketch("草图.1")]),
                               "P02.CATPart": _part("P02", [_sketch("草图.1")])},
                              {"ASM.CATProduct": product})

    labels = [label for label, _part_, _sel in tools._visibility_parts()]
    assert labels == ["P01.CATPart", "P02.CATPart"]
    assert tools.conn.active_document is asm_doc, "the walk must not activate"


def test_the_same_part_twice_is_touched_once():
    """A 70-instance assembly repeats a handful of parts; each is one document."""
    doc = _doc("P01.CATPart")
    asm_doc = _doc("ASM.CATProduct", kind="Product")
    product = _product_tree((doc, None), (doc, None), (doc, None))
    tools = _visibility_tools(asm_doc, {"P01.CATPart": _part("P01")},
                              {"ASM.CATProduct": product})
    assert len(tools._visibility_parts()) == 1


def test_a_subassembly_is_walked_one_level_down():
    inner_doc = _doc("SUB.CATProduct", kind="Product")
    part_doc = _doc("P03.CATPart")
    asm_doc = _doc("ASM.CATProduct", kind="Product")
    inner = _product_tree((part_doc, None))
    outer = _product_tree((inner_doc, inner))
    tools = _visibility_tools(
        asm_doc, {"P03.CATPart": _part("P03")},
        {"ASM.CATProduct": outer, "SUB.CATProduct": inner})
    assert [label for label, _p, _s in tools._visibility_parts()] == ["P03.CATPart"]


def test_a_product_with_no_readable_part_is_a_precondition():
    asm_doc = _doc("ASM.CATProduct", kind="Product")
    tools = _visibility_tools(asm_doc, {},
                              {"ASM.CATProduct": _product_tree()})
    with pytest.raises(PreconditionRejected) as caught:
        tools._visibility_parts()
    assert "no part under it" in str(caught.value)


def test_hiding_a_kind_on_a_product_touches_each_part_selection():
    base_sel, arm_sel = _Selection(), _Selection()
    base_doc = _doc("P01.CATPart", selection=base_sel)
    arm_doc = _doc("P02.CATPart", selection=arm_sel)
    asm_doc = _doc("ASM.CATProduct", kind="Product")
    base_sketch, arm_sketch = _sketch("草图.1"), _sketch("草图.2")
    tools = _visibility_tools(
        asm_doc,
        {"P01.CATPart": _part("P01", [base_sketch]),
         "P02.CATPart": _part("P02", [arm_sketch])},
        {"ASM.CATProduct": _product_tree((base_doc, None), (arm_doc, None))})

    reply = tools._set_visibility_kind("sketch", False)
    assert "2 sketch(es)" in reply
    assert "in 2 of 2 part(s) of 'ASM.CATProduct'" in reply
    # 1 hides (0 shows) - the measured enum this used to get wrong.
    assert base_sel.shown[id(base_sketch)] == 1
    assert arm_sel.shown[id(arm_sketch)] == 1


def test_a_name_that_exists_in_one_part_says_one_of_how_many():
    base_sel = _Selection()
    base_doc = _doc("P01.CATPart", selection=base_sel)
    arm_doc = _doc("P02.CATPart", selection=_Selection())
    asm_doc = _doc("ASM.CATProduct", kind="Product")
    node = _sketch("只在这里")
    tools = _visibility_tools(
        asm_doc,
        {"P01.CATPart": _part("P01", [node]),
         "P02.CATPart": _part("P02", [_sketch("草图.1")])},
        {"ASM.CATProduct": _product_tree((base_doc, None), (arm_doc, None))})

    reply = tools._set_visibility("只在这里", False)
    assert "in 1 of 2 part(s)" in reply and "P01.CATPart" in reply
    assert base_sel.shown[id(node)] == 1


def test_a_name_in_no_part_is_refused_with_the_scope():
    asm_doc = _doc("ASM.CATProduct", kind="Product")
    tools = _visibility_tools(
        asm_doc,
        {"P01.CATPart": _part("P01", [_sketch("草图.1")]),
         "P02.CATPart": _part("P02", [_sketch("草图.1")])},
        {"ASM.CATProduct": _product_tree(
            (_doc("P01.CATPart"), None), (_doc("P02.CATPart"), None))})
    with pytest.raises(ArgumentRejected) as caught:
        tools._set_visibility("NO_SUCH_NODE", False)
    assert "in any of the 2 part(s)" in str(caught.value)


def test_a_single_part_keeps_the_old_wording():
    part_doc = _doc("P01.CATPart")
    sketch = _sketch("草图.1")
    tools = _visibility_tools(part_doc, {"P01.CATPart": _part("P01", [sketch])},
                              {})
    reply = tools._set_visibility("草图.1", False)
    assert reply == "'草图.1' (sketch) is now hidden."
