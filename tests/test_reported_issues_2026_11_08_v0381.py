# -*- coding: utf-8 -*-
"""0.3.81: the two reports that came in on 0.3.80's release day.

The first is a modelling report (seven findings, M1-M7); the second is a
source-level reading of the same release plus three smaller defects. Between
them, four things were wrong in a way a caller could not see:

M3  a sketch holding a SPLINE was lost for good when a failed pad/pocket took it,
    and the same reply said it could be rebuilt and that it could not.
M6  the drawing record did not follow the rebuilt sketch into ``_drawn``, so the
    loft's point-count guard silently stopped comparing rebuilt sections.
M1  a geometric set took a duplicate name silently, while catia_set_visibility
    hid BOTH sets and gsd_spline drew into one of them.
M2  ``parent`` existed on the writing side and nothing on the reading side, so a
    nested set could not be verified.
P0  catia_replace_component asserted "the new instance references the file at
    that path" without ever reading it - and on B30 a byte copy of an OPEN
    document resolves back to the loaded one (measured 2026-11-08).
//n two user-visible messages contained the literal ``//n`` for 15 versions.
"""
from __future__ import annotations

import ast
import pathlib
import re
from types import SimpleNamespace

import pytest

from catia_mcp.colours import UNCOLOURED, UNCOLOURED_INSTANCE, is_default
from catia_mcp.results import CodedError

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "catia_mcp"


# ── a mechanical guard, because reading the message is not a test ────────
def test_no_message_in_the_package_contains_a_literal_escape():
    """``//n`` reached users twice and crossed 15 versions and 1332 tests.

    The class is machine-detectable, so it is checked instead of remembered -
    the same reasoning as ``scan_bare_raises``. ``\\n`` written INSIDE a string
    literal is fine; what this looks for is the two-character sequence a caller
    would SEE, i.e. a backslash pair that no encoder turned into a newline.
    """
    offenders: list[str] = []
    pattern = re.compile(r"//[nrt]|\\\\[nrt]")
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            for match in pattern.finditer(node.value):
                offenders.append(f"{path.name}: ...{node.value[max(0, match.start() - 30):match.end() + 10]!r}")
    assert offenders == [], "literal escapes reach the caller:\n" + "\n".join(offenders)


def test_the_two_reported_messages_are_fixed():
    from catia_mcp.tools.assembly import AssemblyTools

    source = pathlib.Path(AssemblyTools.__module__.replace(".", "/") + ".py")
    text = (pathlib.Path(__file__).resolve().parent.parent / source).read_text(
        encoding="utf-8")
    assert "at all://n" not in text
    assert "to know://n" not in text


# ── geometric sets: the name, and the tree the reader could not see ──────
class _HB:
    """One geometrical set node."""

    def __init__(self, name="", parent=None):
        self.Name = name
        self.HybridBodies = _HBCollection(self)
        self.HybridShapes = _Collection([])
        self.HybridSketches = _Collection([])
        self.parent = parent


class _Collection:
    def __init__(self, items):
        self.items = list(items)
        self.Count = len(self.items)

    def Item(self, index):
        if isinstance(index, int):
            return self.items[index - 1]
        for item in self.items:
            if str(getattr(item, "Name", "")) == str(index):
                return item
        raise KeyError(index)


class _HBCollection(_Collection):
    """``HybridBodies``: Add() makes a child of the owner, as CATIA does."""

    def __init__(self, owner=None, items=None):
        super().__init__(items or [])
        self.owner = owner

    def Add(self):
        child = _HB("", parent=self.owner)
        self.items.append(child)
        self.Count = len(self.items)
        return child


def _part():
    part = SimpleNamespace(HybridBodies=_HBCollection(None),
                           Bodies=_Collection([]),
                           Update=lambda: None, Name="P")
    return part


def _gsd(part, doc_name="P.CATPart"):
    from catia_mcp.tools.gsd import GSDTools

    conn = SimpleNamespace(
        active_document=SimpleNamespace(Name=doc_name),
        ensure_connected=lambda: None, refresh_display=lambda: None,
        get_active_part=lambda: part,
    )
    tools = GSDTools(conn)
    return tools


def test_a_duplicate_geoset_is_renamed_not_duplicated():
    """The 2026-11-08 report built OUTER, INNER inside it, then OUTER again."""
    part = _part()
    tools = _gsd(part)
    tools.execute("catia_gsd_create_geoset", {"name": "OUTER"})
    tools.execute("catia_gsd_create_geoset", {"name": "INNER", "parent": "OUTER"})
    reply = tools.execute("catia_gsd_create_geoset", {"name": "OUTER"})

    assert "'OUTER#2'" in reply, reply
    assert "already exists" in reply
    names = [str(item[0].Name) for item in tools._geoset_index(part)]
    assert names.count("OUTER") == 1
    assert "OUTER#2" in names


def test_a_nested_set_is_not_created_in_an_arbitrary_branch():
    """Two sets with one name: the old loop took the first and said nothing."""
    part = _part()
    tools = _gsd(part)
    tools.execute("catia_gsd_create_geoset", {"name": "OUTER"})
    duplicate = part.HybridBodies.Add()
    duplicate.Name = "OUTER"

    with pytest.raises(CodedError) as caught:
        tools.execute("catia_gsd_create_geoset",
                      {"name": "CHILD", "parent": "OUTER"})
    assert caught.value.code == "GEOSET_AMBIGUOUS"
    assert caught.value.modified is False
    assert "#1" in str(caught.value) and "#2" in str(caught.value)
    assert len(caught.value.data["matches"]) == 2


def test_set_active_geoset_refuses_an_ambiguous_name():
    part = _part()
    tools = _gsd(part)
    tools.execute("catia_gsd_create_geoset", {"name": "OUTER"})
    duplicate = part.HybridBodies.Add()
    duplicate.Name = "OUTER"

    with pytest.raises(CodedError) as caught:
        tools.execute("catia_gsd_set_active_geoset", {"name": "OUTER"})
    assert caught.value.code == "GEOSET_AMBIGUOUS"


def test_list_elements_publishes_parent_and_depth():
    """0.3.78 added a write side for nesting and no read side."""
    import json

    part = _part()
    tools = _gsd(part)
    tools.execute("catia_gsd_create_geoset", {"name": "OUTER"})
    tools.execute("catia_gsd_create_geoset", {"name": "INNER", "parent": "OUTER"})

    listing = json.loads(tools.execute("catia_gsd_list_elements", {}))
    by_name = {entry["name"]: entry for entry in listing["geometrical_sets"]}
    assert by_name["OUTER"]["parent"] is None
    assert by_name["OUTER"]["depth"] == 0
    assert by_name["INNER"]["parent"] == "OUTER"
    assert by_name["INNER"]["depth"] == 1
    assert by_name["OUTER"]["index"] == 1 and by_name["INNER"]["index"] == 2


# ── the spline that a rollback used to destroy ──────────────────────────
class _Factory:
    """Factory2D stub: records what was drawn, rejects nothing."""

    def __init__(self):
        self.splines: list[list[tuple[float, float]]] = []
        self.lines = []

    def CreatePoint(self, x, y):
        return SimpleNamespace(X=x, Y=y)

    def CreateSpline(self, points):
        self.splines.append([(p.X, p.Y) for p in points])
        return SimpleNamespace(Name="Spline")

    def CreateLine(self, *args):
        self.lines.append(args)


class _Sketches:
    def __init__(self, name="草图.1"):
        self.created: list[SimpleNamespace] = []
        self._next = name
        self.factory = _Factory()

    def Add(self, support):
        sketch = SimpleNamespace(Name=self._next, Closed=False,
                                 OpenEdition=lambda: self.factory,
                                 CloseEdition=lambda: None)
        self.created.append(sketch)
        return sketch


def _sketcher(doc_name="P.CATPart", sketch_name="草图.1"):
    from catia_mcp.tools.sketcher import SketcherTools

    sketches = _Sketches(sketch_name)
    body = SimpleNamespace(Sketches=sketches, HybridShapes=_Collection([]))
    part = SimpleNamespace(Name="P", Update=lambda: None,
                           HybridBodies=_HBCollection(None),
                           Bodies=_Collection([]))
    conn = SimpleNamespace(
        active_document=SimpleNamespace(Name=doc_name),
        ensure_connected=lambda: None, refresh_display=lambda: None,
        get_active_part=lambda: part, get_active_part_body=lambda: body,
        sketch_outlines={}, last_sketch=None,
    )
    tools = SketcherTools(conn)
    return tools, conn, part, body, sketches


def test_a_spline_records_where_its_points_were():
    """Without the coordinates there is nothing to replay - the M3 root cause."""
    tools, conn, part, body, sketches = _sketcher()
    tools._active_sketch = SimpleNamespace(Name="草图.1")
    tools._active_factory = _Factory()
    tools._draw_spline([[0, 0], [10, 0], [10, 10], [0, 10]], closed=True)

    record = conn.sketch_outlines["P.CATPart::草图.1"]["elements"][0]
    assert record["kind"] == "spline"
    assert record["pts"] == [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]
    assert record["closed"] is True
    assert record["points"] == 4          # the count the loft warning reads


def test_a_spline_is_replayed_from_its_record():
    tools, conn, part, body, sketches = _sketcher(sketch_name="草图.3")
    conn.sketch_outlines["P.CATPart::草图.2"] = {
        "placement": {"base": "xy", "distance": 0.0},
        "elements": [{"kind": "spline", "points": 4, "chord": 30.0,
                      "pts": [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]],
                      "closed": True}],
    }
    tools._resolve_support = lambda part, body, plane, offset: (object(), "xy")
    factory = sketches.factory

    new_name, problem = tools.rebuild_lost_sketch(part, body, "草图.2")
    assert problem == "", problem
    assert new_name == "草图.3"
    # A closed spline repeats its first point so the result is ONE curve.
    assert factory.splines == [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0),
                                (0.0, 10.0), (0.0, 0.0)]]


def test_a_spline_record_without_points_is_refused_with_a_reason():
    tools, conn, part, body, sketches = _sketcher()
    conn.sketch_outlines["P.CATPart::草图.2"] = {
        "placement": {"base": "xy", "distance": 0.0},
        "elements": [{"kind": "spline", "points": 4, "chord": 30.0}],
    }
    new_name, problem = tools.rebuild_lost_sketch(part, body, "草图.2")
    assert new_name is None
    assert "control points" in problem or "coordinates" in problem, problem


def test_the_rebuilt_record_also_reaches_the_drawn_store():
    """M6: _drawn is what catia_sketch_get_geometry reads, and it was not moved."""
    tools, conn, part, body, sketches = _sketcher(sketch_name="草图.3")
    conn.sketch_outlines["P.CATPart::草图.2"] = {
        "placement": {"base": "xy", "distance": 0.0},
        "elements": [{"kind": "rectangle", "x1": -10.0, "y1": -5.0,
                      "x2": 10.0, "y2": 5.0}],
    }
    tools._resolve_support = lambda part, body, plane, offset: (object(), "xy")

    tools.rebuild_lost_sketch(part, body, "草图.2")
    assert "P.CATPart::草图.3" in tools._drawn, list(tools._drawn)
    assert tools._drawn["P.CATPart::草图.3"][0]["kind"] == "rectangle"


def test_a_failed_rebuild_does_not_also_promise_a_rebuild():
    """M3: one message said it could be rebuilt AND that it could not."""
    from catia_mcp.tools.part_design import PartDesignTools

    class _Broken:
        def __init__(self, conn):
            pass

        def rebuild_lost_sketch(self, part, body, name):
            return None, "it held a spline, which cannot be redrawn from a record"

    import catia_mcp.tools.sketcher as sketcher_module

    original = sketcher_module.SketcherTools
    sketcher_module.SketcherTools = _Broken
    try:
        tools = PartDesignTools(connection=None)
        tools.conn = SimpleNamespace(
            active_document=SimpleNamespace(Name="P.CATPart"),
            sketch_outlines={"P.CATPart::草图.2": {
                "placement": {"base": "frame"},
                "elements": [{"kind": "spline", "points": 4}]}})
        text, can_retry = tools._recover_lost_sketch(object(), object(),
                                                     "草图.2", False)
    finally:
        sketcher_module.SketcherTools = original

    assert "could NOT be re-created" in text
    assert "rebuilds it" not in text, text
    assert "drawn again by hand" in text
    assert can_retry is False


def test_a_retry_is_not_suggested_after_the_sketch_is_gone():
    """The report's exact complaint about next_actions."""
    from catia_mcp.tools.part_design import PartDesignTools

    source = pathlib.Path(
        PartDesignTools.__module__.replace(".", "/") + ".py")
    text = (pathlib.Path(__file__).resolve().parent.parent / source).read_text(
        encoding="utf-8")
    assert "if not sketch_usable else" in text
    assert "Draw the sketch again before retrying" in text


# ── the reference a component really points at ───────────────────────────
class _RefProduct:
    def __init__(self, full_name):
        self.Parent = SimpleNamespace(FullName=full_name)


def _replace_stub(rgb=None, real_name=None):
    """An assembly whose inserted instance resolves to ``real_name``."""
    from catia_mcp.tools.assembly import AssemblyTools

    original = SimpleNamespace(Name="P05.1", PartNumber="P05",
                               Position=SimpleNamespace())
    holder = {}

    class _Products(_Collection):
        def AddComponent(self, path):
            new = SimpleNamespace(
                Name="P05.2", PartNumber="P05",
                Position=SimpleNamespace(),
                ReferenceProduct=_RefProduct(real_name or path))
            self.items.append(new)
            self.Count = len(self.items)
            holder["new"] = new
            return new

        def Remove(self, node):
            self.items.remove(node)
            self.Count = len(self.items)

    products = _Products([original])
    product = SimpleNamespace(Products=products, Name="ASM")
    holder["original"] = original
    doc = SimpleNamespace(Name="ASM.CATProduct",
                          Selection=SimpleNamespace(Clear=lambda: None,
                                                    Add=lambda node: None))
    conn = SimpleNamespace(
        active_document=doc,
        ensure_connected=lambda: None, refresh_display=lambda: None,
        get_active_product=lambda: product,
        product_of=lambda document: product,
    )
    tools = AssemblyTools(conn)
    tools._position_components = lambda position: [1.0, 0.0, 0.0,
                                                   0.0, 1.0, 0.0,
                                                   0.0, 0.0, 1.0,
                                                   0.0, 0.0, 0.0]
    tools._add_component = lambda path: products.AddComponent(path)
    tools._set_component_transform = lambda args: "posed"
    return tools, products, holder


def test_replace_refuses_when_the_instance_resolves_elsewhere(tmp_path):
    """Measured on B30: asked for B\\PZ81A.CATPart, resolved to A\\PZ81A.CATPart."""
    wanted = tmp_path / "B" / "P05.CATPart"
    wanted.parent.mkdir()
    wanted.write_text("x", encoding="utf-8")
    tools, products, holder = _replace_stub(real_name=str(
        tmp_path / "A" / "P05.CATPart"))

    with pytest.raises(CodedError) as caught:
        tools.execute("catia_replace_component",
                      {"component_name": "P05.1", "file_path": str(wanted)})
    assert caught.value.code == "REPLACE_REFERENCE_MISMATCH"
    assert caught.value.modified is False
    assert caught.value.data["same_file_name"] is True
    # The instance this call inserted is undone; the old one is untouched.
    assert len(products.items) == 1
    assert products.items[0] is holder["original"]
    assert products.items[0].Name == "P05.1"
    assert caught.value.data["requested_path"] == str(wanted)


def test_replace_reports_the_reference_it_read_back(tmp_path):
    target = tmp_path / "B" / "P05.CATPart"
    target.parent.mkdir()
    target.write_text("x", encoding="utf-8")
    tools, products, holder = _replace_stub(real_name=str(target))

    reply = tools.execute("catia_replace_component",
                          {"component_name": "P05.1", "file_path": str(target)})
    assert "read back, not assumed" in reply, reply
    assert str(target) in reply
    assert "not saved yet" in reply.lower()


def test_list_components_can_report_the_reference():
    from catia_mcp.tools.assembly import AssemblyTools
    import json

    component = SimpleNamespace(
        Name="A.1", PartNumber="A", Position=SimpleNamespace(),
        ReferenceProduct=_RefProduct(r"D:\out\A.CATPart"))
    products = _Collection([component])
    product = SimpleNamespace(Products=products, Name="ASM")
    doc = SimpleNamespace(Name="ASM.CATProduct")
    conn = SimpleNamespace(
        active_document=doc, ensure_connected=lambda: None,
        refresh_display=lambda: None, get_active_product=lambda: product,
        product_of=lambda document: product,
        part_of=lambda document: (_ for _ in ()).throw(RuntimeError("not a part")),
    )
    tools = AssemblyTools(conn)
    tools._position_components = lambda position: [1.0, 0.0, 0.0,
                                                   0.0, 1.0, 0.0,
                                                   0.0, 0.0, 1.0,
                                                   0.0, 0.0, 0.0]
    payload = json.loads(tools.execute("catia_list_components",
                                       {"include_reference": True}))
    entry = payload["components"][0]
    assert entry["reference_path"] == r"D:\out\A.CATPart"
    assert "reference_error" not in entry


# ── closing the document the advice names ────────────────────────────────
def _docs_stub(*documents, active=0):
    from catia_mcp.tools.document import DocumentTools

    collection = _Collection(documents)

    def make_close(doc):
        def close():
            doc.closed = True
            if doc in collection.items:
                collection.items.remove(doc)
                collection.Count = len(collection.items)
        return close

    for doc in documents:
        doc.Close = make_close(doc)
    conn = SimpleNamespace(
        documents=collection,
        active_document=documents[active],
        ensure_connected=lambda: None, refresh_display=lambda: None,
        component_usage=lambda: {},
    )
    return DocumentTools(conn)


def _doc(name, full):
    """One document; _docs_stub wires Close() so it leaves the collection."""
    doc = SimpleNamespace(Name=name, FullName=full, Close=lambda: None)
    doc.closed = False
    doc.was_closed = lambda: doc.closed
    return doc


def test_close_document_closes_the_named_one_not_the_active_one():
    first = _doc("P05.CATPart", r"D:\a\P05.CATPart")
    second = _doc("P06.CATPart", r"D:\a\P06.CATPart")
    tools = _docs_stub(first, second, active=1)

    reply = tools.execute("catia_close_document",
                          {"document": r"D:\a\P05.CATPart"})
    assert "closed" in reply
    assert first.was_closed() is True
    assert second.was_closed() is False


def test_close_document_refuses_an_ambiguous_name():
    first = _doc("P05.CATPart", r"D:\a\P05.CATPart")
    second = _doc("P05.CATPart", r"D:\b\P05.CATPart")
    tools = _docs_stub(first, second)
    with pytest.raises(CodedError) as caught:
        tools.execute("catia_close_document", {"document": "P05.CATPart"})
    assert caught.value.code == "DOCUMENT_AMBIGUOUS"
    assert first.was_closed() is False and second.was_closed() is False


def test_close_document_says_which_documents_are_open_when_none_match():
    first = _doc("P05.CATPart", r"D:\a\P05.CATPart")
    tools = _docs_stub(first)
    with pytest.raises(CodedError) as caught:
        tools.execute("catia_close_document", {"document": "NOPE.CATPart"})
    assert caught.value.code == "DOCUMENT_NOT_FOUND"
    assert "P05.CATPart" in str(caught.value)


# ── the two colour labels ────────────────────────────────────────────────
def test_a_colour_reset_to_the_default_is_not_reported_as_an_existing_colour():
    """The 2026-11-08 report reset every part to #D2D2FF and read 11 again."""
    assert is_default((210, 210, 255), instance=True)
    assert is_default((210, 210, 255), instance=False)
    assert is_default(UNCOLOURED_INSTANCE, instance=True)
    assert not is_default(UNCOLOURED_INSTANCE, instance=False)


def test_the_count_label_follows_the_mode():
    """P3-3: 25 instances were reported as "25 part(s)" in one sentence."""
    from catia_mcp.tools.appearance import AppearanceTools

    source = pathlib.Path(
        AppearanceTools.__module__.replace(".", "/") + ".py")
    text = (pathlib.Path(__file__).resolve().parent.parent / source).read_text(
        encoding="utf-8")
    assert 'unit = "instance(s)" if per_instance else "part(s)"' in text
    assert "{at_default} part(s)" not in text
