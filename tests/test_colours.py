# -*- coding: utf-8 -*-
"""0.3.79: colour in an assembly, and the three signatures that lie.

Every assertion here comes from a measurement on V5-6R2020 B30, 2026-11-08:

* ``SetRealColor(R, G, B)`` is refused with 无效的参数数目; the build accepts a
  fourth value. The stub below therefore RAISES on three arguments, so a tool
  that got this wrong fails here instead of on the user's machine.
* ``GetRealColor()`` returns four values with the RGB in the LAST THREE: after
  ``SetRealColor(220, 30, 30, 0)`` it answers ``(0, 220, 30, 30)``. The stub
  reproduces that shape, so ``values[0:3]`` reads black and fails.
* An untouched part reads ``(0, 210, 210, 255)``, i.e. RGB (210, 210, 255).
* ``Selection.Add(part)`` - the Part node - is accepted and reads (0, 0, 0), which
  is why the tools colour a BODY and never the Part object.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from catia_mcp.colours import UNCOLOURED, parse_colour
from catia_mcp.results import ArgumentRejected, PreconditionRejected
from catia_mcp.tools.appearance import AppearanceTools


# ── the stub CATIA ────────────────────────────────────────────────────────
class _Collection:
    def __init__(self, items):
        self.items = list(items)
        self.Count = len(self.items)

    def Item(self, key):
        if isinstance(key, int):
            return self.items[key - 1]
        for item in self.items:
            if str(getattr(item, "Name", "")) == str(key):
                return item
        raise KeyError(key)


class _Vis:
    """VisProperties as this build behaves, not as the reference documents it."""

    def __init__(self, selection):
        self.selection = selection

    def SetRealColor(self, *args):
        self.selection.calls.append(len(args))
        if len(args) != 4:
            # Exactly what B30 answers for three arguments.
            raise TypeError("无效的参数数目。")
        rgb = tuple(int(v) for v in args[:3])
        self.selection.colours[self.selection.current] = rgb
        # Measured on B30 2026-11-08: EVEN flags store the colour and leave the
        # view alone; odd ones apply it. GetRealColor answers either way, which
        # is why a wrong flag looks like success until you look at the picture.
        if int(args[3]) % 2:
            self.selection.displayed[self.selection.current] = rgb

    def GetRealColor(self):
        rgb = self.selection.colours.get(self.selection.current, UNCOLOURED)
        return (0, rgb[0], rgb[1], rgb[2])   # four values, RGB last

    def SetShow(self, value):
        self.selection.shown[self.selection.current] = value


class _Selection:
    def __init__(self):
        self.colours = {}
        self.displayed = {}
        self.shown = {}
        self.calls = []
        self.current = None

    def Clear(self):
        self.current = None

    def Add(self, node):
        self.current = id(node)

    @property
    def VisProperties(self):
        return _Vis(self)


def _node(name, **extra):
    node = SimpleNamespace(Name=name)
    for key, value in extra.items():
        setattr(node, key, value)
    return node


class _Body(_Collection):
    def __init__(self, name, shapes=(), sketches=(), hybrids=(), sets=()):
        super().__init__([])
        self.Name = name
        self.Shapes = _Collection(shapes)
        self.Sketches = _Collection(sketches)
        self.HybridShapes = _Collection(hybrids)
        self.HybridBodies = _Collection(sets)


def _part_doc(*bodies, sets=()):
    body_collection = _Collection(bodies)
    part = SimpleNamespace(Bodies=body_collection, HybridBodies=_Collection(sets))
    doc = SimpleNamespace(Name="P04.CATPart", Selection=_Selection())

    def part_of(document):
        return part

    def product_of(document):
        raise RuntimeError("not a product")

    conn = SimpleNamespace(
        active_document=doc, part_of=part_of, product_of=product_of,
        ensure_connected=lambda: None, refresh_display=lambda: None,
        app=SimpleNamespace(ActiveDocument=doc),
    )
    return conn, part, doc


def _product_doc(*components, name="ASM.CATProduct"):
    products = _Collection(components)
    product = SimpleNamespace(Products=products, Name="ASM")
    doc = SimpleNamespace(Name=name, Selection=_Selection())

    def part_of(document):
        raise RuntimeError("not a part")

    def product_of(document):
        return product

    conn = SimpleNamespace(
        active_document=doc, part_of=part_of, product_of=product_of,
        ensure_connected=lambda: None, refresh_display=lambda: None,
        app=SimpleNamespace(ActiveDocument=doc),
    )
    return conn, product, doc


def _component(name, part_number):
    return SimpleNamespace(Name=name, PartNumber=part_number)


def _displayed_of(doc, node):
    """What the viewer would show - empty means the write did nothing visible."""
    return doc.Selection.displayed.get(id(node))


def _colour_of(doc, node):
    """Read a stub node back through the same 4-value shape the tools use."""
    doc.Selection.Clear()
    doc.Selection.Add(node)
    values = doc.Selection.VisProperties.GetRealColor()
    doc.Selection.Clear()
    return (values[-3], values[-2], values[-1])


# ── colour parsing ───────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("#E4572E", (228, 87, 46)),
    ("e4572e", (228, 87, 46)),
    ("#abc", (170, 187, 204)),
    ("red", (220, 30, 30)),
    ("  Grey  ", (128, 128, 128)),
    ([1, 2, 3], (1, 2, 3)),
    ("1, 2, 3", (1, 2, 3)),
    ((255, 0, 128), (255, 0, 128)),
])
def test_every_accepted_colour_form(text, expected):
    assert parse_colour(text) == expected


@pytest.mark.parametrize("value", [
    "#GGGGGG", "0xE4572E", "rgb(1,2,3)", [1, 2], [1, 2, 3, 4], "1,2",
    [-1, 0, 0], [0, 0, 300], None, 42, "chartreuse",
])
def test_a_bad_colour_is_refused_before_anything_is_written(value):
    with pytest.raises(ArgumentRejected) as caught:
        parse_colour(value)
    assert caught.value.code == "ARGUMENT_REJECTED"


def test_the_refusal_lists_the_forms_that_work():
    """A caller who typed 0xRRGGBB should not have to guess again."""
    with pytest.raises(ArgumentRejected) as caught:
        parse_colour("0xE4572E")
    text = str(caught.value)
    assert "#RRGGBB" in text and "colour name" in text


# ── the measured interface shape ─────────────────────────────────────────
def test_the_rgb_is_read_from_the_last_three_of_four_values():
    """Measured: SetRealColor(220,30,30,0) reads back (0,220,30,30).

    A `[0:3]` slice reports (0,220,30) - i.e. black-ish - for every colour, which
    is how a read-back check can "pass" while showing nothing.
    """
    conn, part, doc = _part_doc(_Body("PartBody"))
    tools = AppearanceTools(conn)
    body = part.Bodies.Item(1)

    doc.Selection.Clear()
    doc.Selection.Add(body)
    doc.Selection.colours[doc.Selection.current] = (220, 30, 30)
    assert doc.Selection.VisProperties.GetRealColor() == (0, 220, 30, 30)
    assert tools._read_one(doc.Selection, body) == (220, 30, 30)


def test_set_real_color_is_called_with_four_arguments():
    """Three is the documented call and it is refused on this build."""
    conn, part, doc = _part_doc(_Body("PartBody"))
    tools = AppearanceTools(conn)
    reply = tools.execute("catia_set_color", {"color": "#C82828"})

    assert doc.Selection.calls == [4], doc.Selection.calls
    assert "#C82828" in reply


def test_the_colour_is_APPLIED_and_not_only_stored():
    """The fourth argument is a status flag, not a dummy.

    Measured on B30 2026-11-08: flag 0 stores the colour and leaves the picture
    exactly as it was; 1 applies it. The read-back answers in both cases, so a
    tool that passes 0 reports success on a gray assembly - which is precisely
    what this feature's first version did.
    """
    from catia_mcp.colours import APPLY

    assert APPLY % 2 == 1, "an even flag is silently ignored by the viewer"

    conn, part, doc = _part_doc(_Body("PartBody"))
    body = part.Bodies.Item(1)
    AppearanceTools(conn).execute("catia_set_color", {"color": [1, 2, 3]})

    assert _colour_of(doc, body) == (1, 2, 3)          # stored
    assert _displayed_of(doc, body) == (1, 2, 3)       # and visible


def test_a_zero_flag_write_would_look_successful_but_show_nothing():
    """Keeps the failure mode itself on record, so the test above means something."""
    conn, part, doc = _part_doc(_Body("PartBody"))
    body = part.Bodies.Item(1)
    doc.Selection.Clear()
    doc.Selection.Add(body)
    doc.Selection.VisProperties.SetRealColor(200, 0, 0, 0)

    assert _colour_of(doc, body) == (200, 0, 0)        # reads back fine
    assert _displayed_of(doc, body) is None            # and shows nothing


# ── part level ───────────────────────────────────────────────────────────
def test_no_target_colours_every_body_of_the_part():
    conn, part, doc = _part_doc(_Body("PartBody"), _Body("Housing"))
    tools = AppearanceTools(conn)
    reply = tools.execute("catia_set_color", {"color": [10, 20, 30]})

    assert _colour_of(doc, part.Bodies.Item(1)) == (10, 20, 30)
    assert _colour_of(doc, part.Bodies.Item(2)) == (10, 20, 30)
    assert "2 body(ies)" in reply
    assert "CATPart" in reply and "catia_save_document" in reply


def test_a_named_feature_is_coloured_alone():
    pad = _node("凸台.1")
    body = _Body("PartBody", shapes=[pad])
    conn, part, doc = _part_doc(body)
    tools = AppearanceTools(conn)
    reply = tools.execute("catia_set_color",
                          {"target": "凸台.1", "color": "#16A085"})

    assert _colour_of(doc, pad) == (0x16, 0xA0, 0x85)
    assert _colour_of(doc, body) == UNCOLOURED   # the body was not touched
    assert "feature" in reply


def test_every_node_with_the_same_name_is_coloured():
    """P04 of the 2026-11-06 build holds four 'Datum_frame' (0.3.74's lesson)."""
    planes = [_node("Datum_frame") for _ in range(4)]
    body = _Body("PartBody", hybrids=planes)
    conn, part, doc = _part_doc(body)
    tools = AppearanceTools(conn)
    reply = tools.execute("catia_set_color",
                          {"target": "Datum_frame", "color": "blue"})

    assert [_colour_of(doc, plane) for plane in planes] == [(30, 90, 220)] * 4
    assert "3 other node(s) carrying that name" in reply


def test_a_geometrical_set_colours_what_is_inside_it():
    """A set's colour does not reach its contents - each element has its own."""
    inner_surface = _node("Surface.1")
    inner_set = _node("INNER", HybridShapes=_Collection([inner_surface]),
                      HybridSketches=_Collection([]),
                      HybridBodies=_Collection([]))
    outer = _node("OUTER", HybridShapes=_Collection([]),
                  HybridSketches=_Collection([]),
                  HybridBodies=_Collection([inner_set]))
    conn, part, doc = _part_doc(_Body("PartBody"), sets=[outer])
    tools = AppearanceTools(conn)
    reply = tools.execute("catia_set_color",
                          {"target": "OUTER", "color": [5, 6, 7]})

    assert _colour_of(doc, outer) == (5, 6, 7)
    assert _colour_of(doc, inner_set) == (5, 6, 7)
    assert _colour_of(doc, inner_surface) == (5, 6, 7)
    assert "element(s) inside it" in reply


def test_an_unknown_part_node_names_the_lists_that_show_it():
    conn, part, doc = _part_doc(_Body("PartBody"))
    tools = AppearanceTools(conn)
    with pytest.raises(ArgumentRejected) as caught:
        tools.execute("catia_set_color", {"target": "nope", "color": "red"})
    assert "catia_list_features" in str(caught.value)


def test_a_part_with_no_body_is_refused_not_silently_ok():
    conn, part, doc = _part_doc()
    tools = AppearanceTools(conn)
    with pytest.raises(PreconditionRejected) as caught:
        tools.execute("catia_set_color", {"color": "red"})
    assert caught.value.stage == "validate"


def test_all_instances_is_ignored_in_a_part_and_says_so():
    conn, part, doc = _part_doc(_Body("PartBody"))
    tools = AppearanceTools(conn)
    reply = tools.execute("catia_set_color",
                          {"color": "red", "all_instances": True})
    assert "ignored" in reply


# ── assembly level ───────────────────────────────────────────────────────
def test_an_instance_colour_leaves_the_other_instance_alone():
    first = _component("P01_motor.1", "P01_motor")
    second = _component("P01_motor.2", "P01_motor")
    conn, product, doc = _product_doc(first, second)
    tools = AppearanceTools(conn)
    reply = tools.execute("catia_set_color",
                          {"target": "P01_motor.1", "color": "#E4572E"})

    assert _colour_of(doc, first) == (228, 87, 46)
    assert _colour_of(doc, second) == UNCOLOURED
    assert "INSTANCE colour" in reply and "CATProduct" in reply
    assert "P01_motor.2" in reply          # the sibling it did not touch


def test_all_instances_colours_every_instance_of_that_part():
    first = _component("P01_motor.1", "P01_motor")
    second = _component("P01_motor.2", "P01_motor")
    other = _component("P02_shaft.1", "P02_shaft")
    conn, product, doc = _product_doc(first, second, other)
    tools = AppearanceTools(conn)
    reply = tools.execute("catia_set_color",
                          {"target": "P01_motor.1", "color": "green",
                           "all_instances": True})

    assert _colour_of(doc, first) == (60, 160, 60)
    assert _colour_of(doc, second) == (60, 160, 60)
    assert _colour_of(doc, other) == UNCOLOURED
    assert "every instance of" in reply


def test_set_color_without_a_target_in_a_product_lists_the_components():
    conn, product, doc = _product_doc(_component("A.1", "A"))
    tools = AppearanceTools(conn)
    with pytest.raises(ArgumentRejected) as caught:
        tools.execute("catia_set_color", {"color": "red"})
    text = str(caught.value)
    assert "A.1" in text and "catia_colorize_assembly" in text


def test_colorize_gives_one_colour_per_part_and_shares_it_with_its_instances():
    a1 = _component("A.1", "A")
    a2 = _component("A.2", "A")
    b1 = _component("B.1", "B")
    conn, product, doc = _product_doc(a1, a2, b1)
    tools = AppearanceTools(conn)
    reply = tools.execute("catia_colorize_assembly", {})

    first, second = _colour_of(doc, a1), _colour_of(doc, b1)
    assert _colour_of(doc, a2) == first        # same part, same colour
    assert first != second
    assert "2 colour(s) over 3 instance(s)" in reply
    assert _displayed_of(doc, a1) == first, "coloured in the property, not on screen"
    assert "one per part" in reply
    assert "read back exactly" in reply


def test_per_instance_gives_every_instance_its_own_colour():
    a1 = _component("A.1", "A")
    a2 = _component("A.2", "A")
    conn, product, doc = _product_doc(a1, a2)
    tools = AppearanceTools(conn)
    reply = tools.execute("catia_colorize_assembly", {"per_instance": True})

    assert _colour_of(doc, a1) != _colour_of(doc, a2)
    assert "one per INSTANCE" in reply


def test_the_palette_never_wraps_around():
    """Two parts of one colour is the one outcome this tool must not produce."""
    components = [_component(f"P{index:02d}.1", f"P{index:02d}") for index in range(1, 16)]
    conn, product, doc = _product_doc(*components)
    tools = AppearanceTools(conn)
    reply = tools.execute("catia_colorize_assembly",
                          {"palette": ["#FF0000", "#00FF00"]})

    colours = [_colour_of(doc, component) for component in components]
    assert len(set(colours)) == len(colours), colours
    assert "13 generated" in reply


def test_a_custom_palette_is_used_in_tree_order():
    a1 = _component("A.1", "A")
    b1 = _component("B.1", "B")
    conn, product, doc = _product_doc(a1, b1)
    tools = AppearanceTools(conn)
    tools.execute("catia_colorize_assembly",
                  {"palette": ["#010203", "#040506"]})

    assert _colour_of(doc, a1) == (1, 2, 3)
    assert _colour_of(doc, b1) == (4, 5, 6)


def test_part_numbers_filter_narrows_the_work():
    a1 = _component("A.1", "A")
    b1 = _component("B.1", "B")
    conn, product, doc = _product_doc(a1, b1)
    tools = AppearanceTools(conn)
    tools.execute("catia_colorize_assembly", {"part_numbers": ["B"]})

    assert _colour_of(doc, a1) == UNCOLOURED
    assert _colour_of(doc, b1) != UNCOLOURED


def test_a_filter_that_matches_nothing_lists_what_is_there():
    conn, product, doc = _product_doc(_component("A.1", "A"))
    tools = AppearanceTools(conn)
    with pytest.raises(ArgumentRejected) as caught:
        tools.execute("catia_colorize_assembly", {"part_numbers": ["ZZ"]})
    assert "A.1" in str(caught.value)


def test_colorize_refuses_a_part_document_and_an_empty_assembly():
    conn, part, doc = _part_doc(_Body("PartBody"))
    with pytest.raises(PreconditionRejected) as caught:
        AppearanceTools(conn).execute("catia_colorize_assembly", {})
    assert "catia_set_color" in str(caught.value)

    conn, product, doc = _product_doc()
    with pytest.raises(PreconditionRejected) as caught:
        AppearanceTools(conn).execute("catia_colorize_assembly", {})
    assert "catia_add_component" in str(caught.value)


def test_a_colour_that_does_not_take_is_reported():
    """The read-back is the point: a silent no-op is what 0.3.73 was about."""
    a1 = _component("A.1", "A")

    class _Stubborn(_Selection):
        """SetRealColor returns without raising and changes nothing - the shape
        of a write that "succeeds" on a target that is not a colour carrier."""

        class _Ignoring(_Vis):
            def SetRealColor(self, *args):
                self.selection.calls.append(len(args))

        @property
        def VisProperties(self):
            return self._Ignoring(self)

    conn, product, doc = _product_doc(a1)
    doc.Selection = _Stubborn()
    reply = AppearanceTools(conn).execute("catia_colorize_assembly", {})
    assert "did NOT take" in reply
    assert "A.1 ->" in reply


def test_an_existing_colour_is_reported_as_overwritten():
    a1 = _component("A.1", "A")
    conn, product, doc = _product_doc(a1)
    doc.Selection.Clear()
    doc.Selection.Add(a1)
    doc.Selection.colours[doc.Selection.current] = (1, 2, 3)
    doc.Selection.Clear()

    reply = AppearanceTools(conn).execute("catia_colorize_assembly", {})
    assert "overwritten" in reply


# ── reading back ─────────────────────────────────────────────────────────
def test_get_color_flags_the_uncoloured_default_as_evidence_not_proof():
    body = _Body("PartBody")
    conn, part, doc = _part_doc(body)
    reply = AppearanceTools(conn).execute("catia_get_color", {})

    assert "#D2D2FF" in reply          # (210, 210, 255)
    assert "evidence, not proof" in reply
    # 0.3.81 renamed this from "uncoloured default" because the sentence now also
    # covers the other half of the fact: a body explicitly painted this exact
    # value reads back identically (measured on B30 2026-11-08).
    assert "uncoloured appearance" in reply


def test_get_color_reads_one_component_by_part_and_occurrence():
    a1 = _component("A.1", "A")
    a2 = _component("A.2", "A")
    conn, product, doc = _product_doc(a1, a2)
    doc.Selection.Clear()
    doc.Selection.Add(a2)
    doc.Selection.colours[doc.Selection.current] = (9, 8, 7)
    doc.Selection.Clear()

    reply = AppearanceTools(conn).execute(
        "catia_get_color", {"target": {"part_number": "A", "occurrence": 2}})
    assert "#090807" in reply and "A.2" in reply


def test_get_color_lists_every_component():
    conn, product, doc = _product_doc(_component("A.1", "A"), _component("B.1", "B"))
    reply = AppearanceTools(conn).execute("catia_get_color", {})
    assert "2 component(s)" in reply and "A.1" in reply and "B.1" in reply


# ── the listing field ────────────────────────────────────────────────────
def test_list_components_can_report_the_colour_but_does_not_by_default():
    import inspect

    from catia_mcp.tools.assembly import AssemblyTools

    schema = [d for d in AssemblyTools(connection=None).get_tool_definitions()
              if d["name"] == "catia_list_components"][0]
    assert schema["inputSchema"]["properties"]["include_color"]["default"] is False
    source = inspect.getsource(AssemblyTools._list_components)
    assert "if include_color:" in source
    # One place for the four-value shape, shared with the colour tools.
    assert "from catia_mcp.colours import" not in source
    assert "read_rgb" in source


def test_the_colour_module_and_the_tools_agree_on_the_default():
    """Two copies of (210, 210, 255) would drift; there is one."""
    from catia_mcp import colours

    assert colours.UNCOLOURED == (210, 210, 255)
    assert colours.hex_of(colours.UNCOLOURED) == "#D2D2FF"


def test_the_server_registers_the_three_colour_tools():
    from catia_mcp.server import CATIAMCPServer

    server = CATIAMCPServer()
    registered = set(server._tool_router)
    for name in ("catia_colorize_assembly", "catia_set_color", "catia_get_color"):
        assert name in registered
    # ... and the schema is valid JSON Schema, like every other tool.
    json.dumps(server._schemas["catia_set_color"])
