# -*- coding: utf-8 -*-
"""Regression tests for the 2026-11-07 report (0.3.74).

70-1: ``catia_open_document`` answered "already open - activated it" for a
document that has no window of its own, while ``catia_activate_document``
refused the SAME document with ACTIVATION_FAILED. Two tools, one state,
opposite answers - and the caller trusted the wrong one for four calls.

70-2: ``catia_list_documents`` typed every CATDrawing "Other", the label it also
uses for a .CATfct catalogue.

Also here: the two items the 2026-11-06 report's own re-check left open.

  * a frame sketch leaves its datum plane and three construction points VISIBLE
    (a white diamond over the solid) and ``catia_set_visibility`` cannot even
    find them by name, because they live in the body's HybridShapes and neither
    the name walk nor the kind walk looked there;
  * the SaveAs message said "the folder counts, not only the file name" - it
    does not: measured on B30 2026-11-07 in all four combinations, only the
    FILE NAME matters (scripts/b30_v0374_paths.py).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from catia_mcp.names import unescaped_name_note
from catia_mcp.results import CodedError, PreconditionRejected


# ── 70-1: "already open" must not claim an activation it did not get ────────

def _collection(items):
    def item(key):
        if isinstance(key, int):
            return items[key - 1]
        for candidate in items:
            if str(getattr(candidate, "Name", "")) == key:
                return candidate
        raise KeyError(key)

    return SimpleNamespace(Count=len(items), Item=item)


def _open_tools(path, doc, active_name, windows=("Drawing1",)):
    from catia_mcp.tools.document import DocumentTools

    tools = DocumentTools(connection=None)
    windows_coll = SimpleNamespace(
        Count=len(windows),
        Item=lambda index: SimpleNamespace(Caption=windows[index - 1]))
    tools.conn = SimpleNamespace(
        ensure_connected=lambda: None,
        documents=SimpleNamespace(Count=1, Item=lambda index: doc,
                                  Open=lambda native: doc),
        last_sketch="stale", active_body="stale",
        refresh_display=lambda: None,
        app=SimpleNamespace(ActiveDocument=SimpleNamespace(Name=active_name),
                            Windows=windows_coll),
    )
    return tools


class _Doc:
    """A document object that accepts Activate() and changes nothing."""

    def __init__(self, name, full_name, switch_to=None):
        self.Name = name
        self.FullName = full_name
        self.activated = 0
        self._switch_to = switch_to

    def Activate(self):
        self.activated += 1


def _open_doc_tools(tmp_path, name, active_name, windows=("Drawing1",)):
    path = tmp_path / name
    path.write_text("x", encoding="ascii")
    doc = _Doc(name, str(path))
    return _open_tools(str(path), doc, active_name, windows), doc


def test_already_open_without_a_window_is_activation_failed(tmp_path):
    """The report's three steps: activate Drawing1, open a component, list."""
    tools, doc = _open_doc_tools(tmp_path, "P08_post.CATPart", "Drawing1.CATDrawing")
    with pytest.raises(CodedError) as caught:
        tools._open_document(doc.FullName)
    error = caught.value
    assert "already open - activated it" not in str(error)
    assert error.code == "ACTIVATION_FAILED"
    assert error.stage == "execute"
    assert error.modified is False
    assert "did not switch" in str(error)
    assert "no window of its own" in str(error)
    # The route that cannot work while the product is open is not offered.
    assert "close what holds it" in str(error) or "catia_close_document" in str(error)
    assert "open it with catia_open_document" not in str(error)
    # It did try: Activate() was called once, it just does not switch.
    assert doc.activated == 1


def test_already_open_and_activated_says_so(tmp_path):
    tools, doc = _open_doc_tools(tmp_path, "P08_post.CATPart", "P08_post.CATPart")
    reply = tools._open_document(doc.FullName)
    assert "already open" in reply
    assert "activated it" not in reply
    assert "now the active document" in reply
    assert "reload" in reply


def test_the_impossible_route_is_gone_from_both_tools():
    """Both texts sent the caller to catia_open_document, which cannot comply."""
    from catia_mcp.tools.document import DocumentTools

    refusal = DocumentTools._activation_refusal.__doc__ or ""
    assert refusal  # the envelope has its own docstring
    source = __import__("inspect").getsource(DocumentTools._activation_refusal)
    assert "open the part as its own document with catia_open_document" not in source
    assert "catia_close_document" in source

    listing = __import__("inspect").getsource(DocumentTools._list_documents)
    assert "refuses it, open it with" not in listing
    assert "cannot give it a window" in listing

    missing = __import__("inspect").getsource(DocumentTools._activate_document)
    assert "open it with catia_open_document" not in missing


# ── 70-2: a drawing is not "Other" ──────────────────────────────────────────

def _listing_tools(drawings=(), others=()):
    from catia_mcp.tools.document import DocumentTools

    docs = [SimpleNamespace(Name=name, FullName=name)
            for name in ("P01.CATPart", "ASM.CATProduct", *drawings, *others)]
    tools = DocumentTools(connection=None)

    def part_of(doc):
        if doc.Name.endswith(".CATPart"):
            return object()
        raise PreconditionRejected("not a part")

    def product_of(doc):
        if doc.Name.endswith(".CATProduct"):
            return object()
        raise PreconditionRejected("not a product")

    def drawing_of(doc):
        if doc.Name.endswith(".CATDrawing"):
            return doc
        raise PreconditionRejected("not a drawing")

    tools.conn = SimpleNamespace(
        ensure_connected=lambda: None,
        documents=_collection(docs),
        part_of=part_of, product_of=product_of, drawing_of=drawing_of,
        component_usage=lambda: {},
    )
    return tools


def test_a_drawing_is_listed_as_a_drawing():
    tools = _listing_tools(drawings=("Drawing1.CATDrawing",),
                           others=("MyCatalog.CATfct",))
    rows = {row["name"]: row["type"] for row in json.loads(tools._list_documents())}
    assert rows["P01.CATPart"] == "CATPart"
    assert rows["ASM.CATProduct"] == "CATProduct"
    assert rows["Drawing1.CATDrawing"] == "CATDrawing"
    # A .CATfct is what "Other" is for - and the two are no longer the same word.
    assert rows["MyCatalog.CATfct"] == "Other"


def test_drawing_of_refuses_a_document_that_has_no_sheets():
    from catia_mcp.connection import CATIAConnection

    conn = CATIAConnection()
    with pytest.raises(PreconditionRejected) as caught:
        conn.drawing_of(SimpleNamespace(Name="P01.CATPart", Part=object()))
    assert "Drawing" in str(caught.value)
    assert "Sheets" in str(caught.value)
    assert conn.drawing_of(SimpleNamespace(Name="D.CATDrawing",
                                           Sheets=object())) is not None


# ── the frame sketch's datum plane: named, found, hidden ────────────────────

class _Selection:
    def __init__(self) -> None:
        self.current = None
        self.shown: dict[int, int] = {}

    def Clear(self) -> None:
        self.current = None

    def Add(self, node) -> None:
        self.current = node

    @property
    def VisProperties(self):
        return self

    def SetShow(self, value: int) -> None:
        self.shown[id(self.current)] = value


def _frame_part(existing_hybrids=()):
    hybrids = list(existing_hybrids)
    points: list[Any] = []
    body = SimpleNamespace(Name="零件几何体", Sketches=_collection([]),
                           Shapes=_collection([]))

    def insert(feature):
        hybrids.append(feature)

    body.InsertHybridShape = insert

    class _Hybrids:
        """A live collection: Count is read every time, as COM's is."""

        Count = property(lambda self: len(hybrids))

        @staticmethod
        def Item(index):
            return hybrids[index - 1]

    body.HybridShapes = _Hybrids()

    class _Factory:
        @staticmethod
        def AddNewPointCoord(x, y, z):
            point = SimpleNamespace(Name=f"点.{len(hybrids) + 1}")
            points.append(point)
            return point

        @staticmethod
        def AddNewPlane3Points(first, second, third):
            return SimpleNamespace(Name="")

    part = SimpleNamespace(HybridShapeFactory=_Factory(),
                           InWorkObject=None,
                           UpdateObject=lambda feature: None,
                           CreateReferenceFromObject=lambda feature: feature)
    selection = _Selection()
    doc = SimpleNamespace(Name="P04.CATPart", Selection=selection)
    return part, body, doc, selection, hybrids


def _sketcher(part, doc):
    from catia_mcp.tools.sketcher import SketcherTools

    tools = SketcherTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None,
                                 active_document=doc,
                                 refresh_display=lambda: None)
    return tools


def _plane():
    return {"type": "frame", "origin": [10.0, 20.0, 30.0],
            "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0]}


def test_a_frame_plane_gets_a_name_of_its_own():
    """P04 of the 2026-11-06 build held FOUR features called 'Datum_frame'."""
    existing = [SimpleNamespace(Name="Datum_frame"),
                SimpleNamespace(Name="Datum_frame.2")]
    part, body, doc, _selection, _hybrids = _frame_part(existing)
    from catia_mcp.tools.sketcher import SketcherTools

    assert SketcherTools._free_feature_name(body, "Datum_frame") == "Datum_frame.3"
    assert SketcherTools._free_feature_name(body, "Datum_other") == "Datum_other"


def test_the_frame_plane_and_its_points_are_hidden():
    part, body, doc, selection, hybrids = _frame_part()
    tools = _sketcher(part, doc)
    reference, note = tools._frame_plane(part, body, _plane())

    assert reference is not None
    assert len(hybrids) == 4, "3 points + the plane"
    assert [str(feature.Name) for feature in hybrids][-1] == "Datum_frame"
    # 1 hides (0 shows) on B30 - the measured enum.
    assert [selection.shown.get(id(feature)) for feature in hybrids] == [1, 1, 1, 1]
    assert "hidden" in note
    assert "catia_set_visibility(name='Datum_frame'" in note


def test_a_second_frame_plane_is_named_and_hidden_too():
    part, body, doc, selection, hybrids = _frame_part()
    tools = _sketcher(part, doc)
    tools._frame_plane(part, body, _plane())
    _ref, note = tools._frame_plane(part, body, _plane())
    names = [str(feature.Name) for feature in hybrids]
    assert names[-1] == "Datum_frame.2"
    assert "catia_set_visibility(name='Datum_frame.2'" in note


def test_set_visibility_finds_a_hybrid_shape_inside_a_body():
    """The walk covered geometrical sets only, so it could not see them."""
    from catia_mcp.tools.part_design import PartDesignTools

    target = SimpleNamespace(Name="Datum_frame.2")
    body = SimpleNamespace(
        Name="零件几何体",
        Sketches=_collection([]),
        Shapes=_collection([]),
        HybridShapes=_collection([SimpleNamespace(Name="点.1"), target]))
    part = SimpleNamespace(Bodies=_collection([body]),
                           HybridBodies=_collection([]),
                           OriginElements=SimpleNamespace())
    selection = _Selection()
    doc = SimpleNamespace(Name="P04.CATPart", Part=part, Selection=selection)

    tools = PartDesignTools(connection=None)
    tools.conn = SimpleNamespace(
        ensure_connected=lambda: None,
        active_document=doc,
        part_of=lambda d: part,
        product_of=lambda d: (_ for _ in ()).throw(PreconditionRejected("no")),
        refresh_display=lambda: None,
    )
    reply = tools._set_visibility("Datum_frame.2", True)
    assert selection.shown[id(target)] == 0, "0 shows"
    assert "datum element" in reply
    assert "Datum_frame.2" in reply


def test_every_node_with_that_name_is_set():
    """Collection.Item(name) returns the FIRST match and says nothing.

    P04 of the 2026-11-06 build holds four features called 'Datum_frame' (this
    server created all four), so hiding "the datum plane" used to set one and
    report success while three stayed on screen (measured on B30 2026-11-07).
    """
    from catia_mcp.tools.part_design import PartDesignTools

    first = SimpleNamespace(Name="Datum_frame")
    second = SimpleNamespace(Name="Datum_frame")
    body = SimpleNamespace(
        Name="零件几何体", Sketches=_collection([]), Shapes=_collection([]),
        HybridShapes=_collection([first, second]))
    part = SimpleNamespace(Bodies=_collection([body]),
                           HybridBodies=_collection([]),
                           OriginElements=SimpleNamespace())
    selection = _Selection()
    doc = SimpleNamespace(Name="P04.CATPart", Part=part, Selection=selection)
    tools = PartDesignTools(connection=None)
    tools.conn = SimpleNamespace(
        ensure_connected=lambda: None, active_document=doc,
        part_of=lambda d: part,
        product_of=lambda d: (_ for _ in ()).throw(PreconditionRejected("no")),
        refresh_display=lambda: None)

    reply = tools._set_visibility("Datum_frame", False)
    assert selection.shown[id(first)] == 1 and selection.shown[id(second)] == 1
    assert "1 other tree node" in reply


# ── an escape that was never decoded ───────────────────────────────────────

def test_an_undecoded_escape_in_a_name_is_named():
    note = unescaped_name_note("\\u4e3b\\u89c6\\u56fe")
    assert "never decoded" in note
    assert "主视图" in note
    assert "2 literal" not in note  # three codes, not two


def test_an_ordinary_windows_path_is_not_an_escape():
    assert unescaped_name_note(r"F:\Users\me\P06.CATPart") == ""
    assert unescaped_name_note("主视图") == ""
    assert unescaped_name_note("") == ""


def test_the_view_tools_say_it_when_a_name_carries_an_escape():
    import inspect

    from catia_mcp.tools.drawing import DraftingTools

    for method in (DraftingTools._add_view, DraftingTools._projection_view,
                   DraftingTools._section_view, DraftingTools._detail_view):
        assert "unescaped_name_note" in inspect.getsource(method), method.__name__


# ── the save message must not blame the folder ─────────────────────────────

def test_the_save_failure_no_longer_blames_the_folder():
    """Measured on B30 2026-11-07, four combinations, one variable at a time.

    The report came back a second time with "evidence B" (a Chinese folder with
    an ASCII name makes catia_new_part fail). Three attempts through this tool
    with its exact path saved the file, so the message states what was measured
    and points at the CATIA error it already carries - rather than asserting a
    cause that could not be reproduced.
    """
    import inspect

    from catia_mcp import dialogs
    from catia_mcp.tools import document

    text = inspect.getsource(document)
    assert "the folder counts" not in text
    assert "the FILE NAME contains non-ASCII" in text
    # The escape hatch for the opposite observation: read the bracketed error.
    assert "if this name IS ASCII" in text
    dialog_text = inspect.getsource(dialogs)
    assert "a Chinese folder counts" not in dialog_text
    assert "non-ASCII FOLDER" in dialog_text
    # CATIA's own wording, which the report asked for and a screenshot confirmed.
    assert "上述文件名无效" in dialog_text


def test_new_product_fails_the_same_way_as_new_part():
    """The report closed with "catia_new_product 是否同样返回 ok:false 尚未测"."""
    import inspect

    from catia_mcp.tools.document import DocumentTools

    source = inspect.getsource(DocumentTools._new_product)
    assert 'code="SAVE_FAILED"' in source
    assert 'stage="execute"' in source
    # 0.3.77 changed the claim: the empty product is closed again, so the call
    # did not leave a modified document behind - `modified` now follows that.
    assert "modified=not discarded" in source
    assert "_discard_unsaved_document" in source
    # and it names the document= value the caller should use from then on
    assert "For every document= argument use" in source


def test_the_gap_advice_comes_before_the_coordinate_list():
    """A client cut the 1242-char reply and lost the actionable sentence."""
    from catia_mcp.tools.sketcher import SketcherTools

    tools = SketcherTools(connection=None)
    tools._profile_ends = [(0.0, 0.0), (0.0002, 0.0), (10.0, 0.0), (10.0, 10.0)]
    warning = tools._profile_warning()
    assert "least" not in warning  # sanity: not the old wording
    advice = warning.index("rounding artefact")
    gaps = warning.index("The gaps:")
    assert advice < gaps
    assert "smallest gap" in warning
