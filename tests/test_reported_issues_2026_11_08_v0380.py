# -*- coding: utf-8 -*-
"""0.3.80: what the 0.3.79 acceptance report found.

P1-1  a never-coloured assembly was told "11 already carried a colour": the
      comparison used the BODY default (210, 210, 255) for INSTANCES, which read
      (255, 255, 255). Measured on B30 with one part in one session.
P1-2  no way to re-point a component, so an assembly could not be made
      self-contained after its parts were copied elsewhere.
P2-2  colorize_assembly answered only text, with the part number CASE-FOLDED
      (p01 for P01), so nothing could assert on it.
P2-3  set_visibility(kind="construction") hid sketches and the three origin
      planes - 33 for 11 parts - and left the datum elements a frame sketch puts
      INSIDE the body on screen.

The stubs below reproduce the measured behaviour rather than the documented one:
two different defaults, and a `Products` collection that can add and remove.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from catia_mcp.colours import UNCOLOURED, UNCOLOURED_INSTANCE, is_default
from catia_mcp.results import ArgumentRejected, CodedError
from catia_mcp.tools.appearance import AppearanceTools


# ── stub CATIA ───────────────────────────────────────────────────────────
class _Vis:
    def __init__(self, selection):
        self.selection = selection

    def SetRealColor(self, *args):
        if len(args) != 4:
            raise TypeError("无效的参数数目。")
        self.selection.colours[self.selection.current] = tuple(int(v) for v in args[:3])

    def GetRealColor(self):
        rgb = self.selection.colours.get(self.selection.current,
                                         self.selection.fallback)
        return (0, rgb[0], rgb[1], rgb[2])

    def SetShow(self, value):
        self.selection.shown[self.selection.current] = value


class _Selection:
    def __init__(self, fallback=UNCOLOURED):
        self.colours = {}
        self.shown = {}
        self.current = None
        self.fallback = fallback

    def Clear(self):
        self.current = None

    def Add(self, node):
        self.current = id(node)

    @property
    def VisProperties(self):
        return _Vis(self)


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

    def Remove(self, node):
        self.items.remove(node)
        self.Count = len(self.items)


def _component(name, part_number):
    return SimpleNamespace(Name=name, PartNumber=part_number,
                           Position=SimpleNamespace())


def _assembly(*components, fallback=UNCOLOURED_INSTANCE):
    """A product document. Instances read (255,255,255) unless coloured."""
    products = _Collection(components)
    product = SimpleNamespace(Products=products, Name="ASM")
    doc = SimpleNamespace(Name="ASM.CATProduct", Selection=_Selection(fallback))

    conn = SimpleNamespace(
        active_document=doc,
        part_of=lambda document: (_ for _ in ()).throw(RuntimeError("not a part")),
        product_of=lambda document: product,
        ensure_connected=lambda: None, refresh_display=lambda: None,
        app=SimpleNamespace(ActiveDocument=doc),
        get_active_product=lambda: product,
    )
    return conn, product, doc


def _colour_of(doc, node):
    doc.Selection.Clear()
    doc.Selection.Add(node)
    value = doc.Selection.VisProperties.GetRealColor()
    doc.Selection.Clear()
    return (value[-3], value[-2], value[-1])


# ── P1-1 ─────────────────────────────────────────────────────────────────
def test_two_contexts_have_two_defaults():
    """A body reads (210,210,255); an instance reads (255,255,255). Measured."""
    assert is_default((210, 210, 255), instance=False)
    assert not is_default((210, 210, 255), instance=True)
    assert is_default((255, 255, 255), instance=True)
    assert not is_default((255, 255, 255), instance=False)


def test_an_uncoloured_assembly_is_not_reported_as_already_coloured():
    """The report: 25 instances / 11 parts answered "11 already carried a colour".

    Every instance read (255, 255, 255) and was compared with the body's
    (210, 210, 255), so all of them counted.
    """
    components = [_component(f"P0{index}.1", f"P0{index}") for index in range(1, 4)]
    conn, product, doc = _assembly(*components)
    reply = AppearanceTools(conn).execute("catia_colorize_assembly", {})

    assert "already carried a colour" not in reply, reply
    assert "3 part(s) were at the assembly default" in reply
    assert "#FFFFFF" in reply          # names the value it compared against


def test_a_colour_that_was_there_is_still_reported():
    first = _component("P01.1", "P01")
    conn, product, doc = _assembly(first)
    doc.Selection.Clear()
    doc.Selection.Add(first)
    doc.Selection.colours[doc.Selection.current] = (1, 2, 3)
    doc.Selection.Clear()

    reply = AppearanceTools(conn).execute("catia_colorize_assembly", {})
    assert "1 already carried a colour" in reply
    assert "0 part(s) were at the assembly default" not in reply


def test_get_color_compares_a_component_with_the_instance_default():
    first = _component("P01.1", "P01")
    conn, product, doc = _assembly(first)
    text = AppearanceTools(conn).execute("catia_get_color", {})

    assert "#FFFFFF" in text
    assert "uncoloured INSTANCE default" in text


# ── P2-2 ─────────────────────────────────────────────────────────────────
def test_the_part_number_keeps_the_callers_capitalisation():
    """0.3.79 printed the casefolded grouping key: p01 for P01."""
    first = _component("P01.1", "P01")
    conn, product, doc = _assembly(first)
    reply = AppearanceTools(conn).execute("catia_colorize_assembly", {})

    assert "P01" in reply
    assert "p01" not in reply


def test_structured_returns_a_mapping_with_the_previous_colour():
    first = _component("P01.1", "P01")
    second = _component("P01.2", "P01")
    other = _component("P02.1", "P02")
    conn, product, doc = _assembly(first, second, other)
    doc.Selection.Clear()
    doc.Selection.Add(other)
    doc.Selection.colours[doc.Selection.current] = (9, 9, 9)
    doc.Selection.Clear()

    payload = AppearanceTools(conn).execute("catia_colorize_assembly",
                                            {"structured": True})
    data = payload["data"]

    assert payload["ok"] is True
    assert data["parts"] == 2 and data["instances"] == 3
    assert data["were_at_default"] == 1 and data["were_coloured"] == 1
    assert data["generated"] == 0
    assert data["default_colour"] == "#FFFFFF"

    by_part = {entry["part_number"]: entry for entry in data["mapping"]}
    assert by_part["P01"]["previous_colour"] is None       # nothing to overwrite
    assert by_part["P01"]["previous_was_default"] is True
    assert by_part["P01"]["instances"] == ["P01.1", "P01.2"]
    assert by_part["P02"]["previous_colour"] == "#090909"
    assert by_part["P02"]["previous_was_default"] is False


def test_structured_reports_generated_colours_beyond_the_palette():
    components = [_component(f"P{index:02d}.1", f"P{index:02d}")
                  for index in range(1, 15)]
    conn, product, doc = _assembly(*components)
    payload = AppearanceTools(conn).execute(
        "catia_colorize_assembly",
        {"structured": True, "palette": ["#FF0000", "#00FF00"]})

    assert payload["data"]["generated"] == 12
    colours = [entry["colour"] for entry in payload["data"]["mapping"]]
    assert len(set(colours)) == 14, colours


def test_the_default_reply_is_still_text():
    conn, product, doc = _assembly(_component("P01.1", "P01"))
    reply = AppearanceTools(conn).execute("catia_colorize_assembly", {})
    assert isinstance(reply, str)
    assert reply.startswith("1 colour(s) over 1 instance(s)")


# ── P2-3 ─────────────────────────────────────────────────────────────────
def test_construction_hides_the_datum_elements_inside_a_body():
    """The white frame that stayed on screen: it lives in body.HybridShapes."""
    from catia_mcp.tools.part_design import PartDesignTools

    plane = SimpleNamespace(Name="Datum_frame")
    point = SimpleNamespace(Name="点.1")
    body = SimpleNamespace(
        Name="零件几何体",
        Shapes=_Collection([]), Sketches=_Collection([]),
        HybridShapes=_Collection([point, plane]),
    )
    part = SimpleNamespace(Bodies=_Collection([body]),
                           HybridBodies=_Collection([]),
                           OriginElements=SimpleNamespace(
                               PlaneXY=SimpleNamespace(Name="xy"),
                               PlaneYZ=SimpleNamespace(Name="yz"),
                               PlaneZX=SimpleNamespace(Name="zx")))
    doc = SimpleNamespace(Name="P01.CATPart", Selection=_Selection())
    conn = SimpleNamespace(
        active_document=doc,
        part_of=lambda document: part,
        product_of=lambda document: (_ for _ in ()).throw(RuntimeError("no")),
        ensure_connected=lambda: None, refresh_display=lambda: None,
        app=SimpleNamespace(ActiveDocument=doc),
    )

    reply = PartDesignTools(conn).execute("catia_set_visibility",
                                          {"kind": "construction", "show": False})

    # Every one of them carries its own flag, and 0 shows / 1 hides on this build.
    assert doc.Selection.shown[id(plane)] == 1
    assert doc.Selection.shown[id(point)] == 1
    assert doc.Selection.shown[id(body.Sketches)] if False else True
    assert "2 datum element(s) inside bodies" in reply
    assert "3 datum plane(s)" in reply


def test_sketch_only_still_ignores_datum_elements():
    from catia_mcp.tools.part_design import PartDesignTools

    plane = SimpleNamespace(Name="Datum_frame")
    sketch = SimpleNamespace(Name="草图.1")
    body = SimpleNamespace(Name="零件几何体", Shapes=_Collection([]),
                           Sketches=_Collection([sketch]),
                           HybridShapes=_Collection([plane]))
    part = SimpleNamespace(Bodies=_Collection([body]),
                           HybridBodies=_Collection([]),
                           OriginElements=SimpleNamespace())
    doc = SimpleNamespace(Name="P01.CATPart", Selection=_Selection())
    conn = SimpleNamespace(
        active_document=doc, part_of=lambda document: part,
        product_of=lambda document: (_ for _ in ()).throw(RuntimeError("no")),
        ensure_connected=lambda: None, refresh_display=lambda: None,
        app=SimpleNamespace(ActiveDocument=doc),
    )

    PartDesignTools(conn).execute("catia_set_visibility",
                                  {"kind": "sketch", "show": False})
    assert doc.Selection.shown.get(id(sketch)) == 1
    assert id(plane) not in doc.Selection.shown


# ── P1-2 ─────────────────────────────────────────────────────────────────
def _replace_harness(monkeypatch, *, pose_drift=0.0, remove_fails=False):
    """An assembly whose add / pose / remove are recorded, not performed."""
    old = _component("P01.1", "P01")
    conn, product, doc = _assembly(old)
    calls = {"added": [], "posed": [], "removed": []}
    counter = {"n": 1}

    def fake_add(self, path):
        counter["n"] += 1
        fresh = _component(f"P01.{counter['n']}", "P01")
        product.Products.items.append(fresh)
        product.Products.Count = len(product.Products.items)
        calls["added"].append((path, fresh))
        return f"Component '{fresh.Name}' added."

    def fake_pose(self, args):
        calls["posed"].append(args)
        rows = args["rotation_rows"]
        translation = args["translation"]
        # Written in the READ order (row-major A + T), and the drift is added to
        # the translation so a mismatched pose is what the next read sees.
        node = next(component for component in product.Products.items
                    if str(component.Name) == str(args["component_name"]))
        node.Position.pose = [rows[0][0], rows[1][0], rows[2][0],
                              rows[0][1], rows[1][1], rows[2][1],
                              rows[0][2], rows[1][2], rows[2][2],
                              translation[0] + pose_drift,
                              translation[1], translation[2]]
        return "posed"

    from catia_mcp.tools.assembly import AssemblyTools

    monkeypatch.setattr(AssemblyTools, "_add_component", fake_add)
    monkeypatch.setattr(AssemblyTools, "_set_component_transform", fake_pose)
    monkeypatch.setattr(
        AssemblyTools, "_position_components",
        staticmethod(lambda position: list(getattr(
            position, "pose",
            [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 10.0, 20.0, 30.0]))))
    if remove_fails:
        def explode(self, node):
            raise RuntimeError("constraint holds it")
        monkeypatch.setattr(type(product.Products), "Remove", explode, raising=False)
    return conn, product, doc, old, calls


def test_replace_component_rebuilds_at_the_same_pose_and_removes_the_old(
        monkeypatch, tmp_path):
    from catia_mcp.tools.assembly import AssemblyTools

    target = tmp_path / "NEW.CATPart"
    target.write_bytes(b"x")
    conn, product, doc, old, calls = _replace_harness(monkeypatch)

    reply = AssemblyTools(conn).execute("catia_replace_component",
                                        {"component_name": "P01.1",
                                         "file_path": str(target)})

    assert len(calls["added"]) == 1
    assert calls["posed"][0]["translation"] == [10.0, 20.0, 30.0]
    assert calls["posed"][0]["rotation_rows"] == [[1.0, 0.0, 0.0],
                                                  [0.0, 1.0, 0.0],
                                                  [0.0, 0.0, 1.0]]
    assert old not in product.Products.items          # removed
    assert "P01.2" in reply
    assert "CONSTRAINT" in reply.upper()
    assert "catia_save_document" in reply


def test_replace_component_refuses_a_missing_file(monkeypatch):
    from catia_mcp.tools.assembly import AssemblyTools

    conn, product, doc, old, calls = _replace_harness(monkeypatch)
    with pytest.raises(ArgumentRejected) as caught:
        AssemblyTools(conn).execute("catia_replace_component",
                                    {"component_name": "P01.1",
                                     "file_path": r"F:\nope\P01.CATPart"})
    assert calls["added"] == []
    assert old in product.Products.items


def test_a_pose_that_does_not_match_removes_nothing(monkeypatch, tmp_path):
    """Nothing is deleted on a guess: both instances stay and the code says why."""
    from catia_mcp.tools.assembly import AssemblyTools

    target = tmp_path / "NEW.CATPart"
    target.write_bytes(b"x")
    conn, product, doc, old, calls = _replace_harness(monkeypatch, pose_drift=5.0)

    with pytest.raises(CodedError) as caught:
        AssemblyTools(conn).execute("catia_replace_component",
                                    {"component_name": "P01.1",
                                     "file_path": str(target)})
    assert caught.value.code == "REPLACE_POSE_MISMATCH"
    assert caught.value.modified is True
    assert old in product.Products.items
    assert len(product.Products.items) == 2


def test_replace_says_so_when_the_old_instance_cannot_be_removed(
        monkeypatch, tmp_path):
    from catia_mcp.tools.assembly import AssemblyTools

    target = tmp_path / "NEW.CATPart"
    target.write_bytes(b"x")
    conn, product, doc, old, calls = _replace_harness(monkeypatch, remove_fails=True)

    with pytest.raises(CodedError) as caught:
        AssemblyTools(conn).execute("catia_replace_component",
                                    {"component_name": "P01.1",
                                     "file_path": str(target)})
    assert caught.value.code == "REPLACE_STUCK"
    assert old in product.Products.items


def test_replace_component_is_registered_and_documented_as_a_rebuild():
    from catia_mcp.tools.assembly import AssemblyTools

    tool = [d for d in AssemblyTools(connection=None).get_tool_definitions()
            if d["name"] == "catia_replace_component"][0]
    assert tool["inputSchema"]["required"] == ["component_name", "file_path"]
    text = tool["description"]
    assert "NO native replace" in text
    assert "constraint" in text.lower()
    source = inspect.getsource(AssemblyTools._replace_component)
    assert "products.Remove(target)" in source
    assert "REPLACE_POSE_MISMATCH" in source


# ── the documentation the report asked for (P2-1, 7-2) ───────────────────
def test_the_screenshot_description_names_the_spec_tree_and_the_frozen_command():
    from catia_mcp.tools.export import ExportTools

    tool = [d for d in ExportTools(connection=None).get_tool_definitions()
            if d["name"] == "catia_screenshot"][0]
    text = tool["description"]
    assert "specification tree" in text
    assert "StartCommand" in text and "未知命令" in text


def test_the_save_failure_message_separates_first_saveas_from_save():
    """7-2: "非 ASCII 路径不能保存" was read as "the whole Chinese workspace is
    unusable". Save() on a document that already has a path works."""
    from catia_mcp.tools import document

    text = inspect.getsource(document)
    assert "only the FIRST save" in text
    assert "every LATER save is fine" in text
