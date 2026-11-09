"""0.3.90: a save that says which step it took, a record that follows a delete.

Two reports on 0.3.89.

The first kept meeting `SAVE_FAILED` with `data.call = "Document.Save"` - a bare
Save - for `new_part(file_path=...)`, on ONE folder, while every neighbouring
folder took the staged route. `_save_document` enters its bare-Save branch when
"the target file is this document's own file", and that test compared **names**
(`holder.Name == doc.Name`) while CATIA freely gives two open documents the same
name. Identity is the file, so `_same_file` compares FullName - and a document
with no path can never be "the same file", which is exactly the case where a bare
Save cannot work. The route is now also published step by step in
`data.save_route`, so the next report is settled by reading rather than by
re-deriving.

The second report saw `sketch_copy` make a copy whose outline was exactly twice
the source's. Measured here 2026-11-09: two lines, delete the second, and the
session's drawing record still held BOTH - so the copy replayed a line the sketch
no longer had. Each recorded entry now carries the live element indices it
produced, and a delete drops it (or, when one entry covers several elements, the
whole record, with a sentence saying so).
"""
from __future__ import annotations

import types

import pytest

from catia_mcp.results import CodedError


# ── identity of a save target: the file, not the name ───────────────────


def _doc(name: str, full: str | None = None):
    if full is None:
        def boom():
            raise RuntimeError("never saved")
        return types.SimpleNamespace(Name=name, FullName=boom)
    return types.SimpleNamespace(Name=name, FullName=full)


def test_two_documents_with_one_name_are_not_the_same_file():
    """CATIA allows duplicate names; a name therefore cannot mean identity."""
    from catia_mcp.tools.document import DocumentTools

    held = _doc("verify_cn.CATPart", r"F:\work\verify_cn.CATPart")
    other = _doc("verify_cn.CATPart", r"F:\elsewhere\verify_cn.CATPart")
    assert DocumentTools._same_file(held, other) is False


def test_the_same_file_reads_as_the_same_file():
    from catia_mcp.tools.document import DocumentTools

    assert DocumentTools._same_file(
        _doc("p.CATPart", r"F:\work\p.CATPart"),
        _doc("p.CATPart", "F:/work/p.CATPart")) is True


def test_a_document_with_no_path_is_never_saving_onto_its_own_file():
    """A bare Save cannot give it a path - so this branch must not be taken."""
    from catia_mcp.tools.document import DocumentTools

    assert DocumentTools._same_file(
        _doc("verify_cn.CATPart", r"F:\work\verify_cn.CATPart"),
        _doc("verify_cn.CATPart")) is False


def test_a_failed_save_publishes_the_route_it_took(monkeypatch):
    """data.save_route: "the staged branch never ran" vs "its step 2 failed"."""
    from catia_mcp.tools import document as document_module

    tools = document_module.DocumentTools(connection=None)
    tools.conn = types.SimpleNamespace(
        active_document=types.SimpleNamespace(Name="P.CATPart", FullName=""),
        ensure_connected=lambda: None, refresh_display=lambda: None,
        documents=types.SimpleNamespace(Count=0),
        component_usage=lambda: {})
    monkeypatch.setattr(tools, "_document_at_path", lambda path: None)
    monkeypatch.setattr(tools, "_existing_file_note", lambda path: None)
    monkeypatch.setattr(tools, "_document_has_path", lambda doc: False)

    def refuse(doc, path, *args, **kwargs):
        raise CodedError("SaveAs failed after 6 attempts", code="SAVE_FAILED",
                         modified=False, data={"call": "Document.SaveAs"})

    monkeypatch.setattr(document_module.dialogs, "save_as", refuse)

    with pytest.raises(CodedError) as caught:
        tools._save_document(r"F:\work\p.CATPart")

    trace = (caught.value.data or {}).get("save_route")
    assert trace, "a failed save has to say which steps it took"
    steps = [row["step"] for row in trace]
    assert steps[0] == "enter"
    assert "staging" in steps and "save_as" in steps
    assert trace[0]["has_path"] is False
    assert trace[0]["folder_ascii"] is True
    failed = [row for row in trace if row.get("ok") is False]
    assert failed and "SaveAs failed" in failed[-1]["error"]


# ── the drawing record follows a delete ────────────────────────────────


class _Geometry:
    def __init__(self, count: int) -> None:
        self.Count = count


class _Sketch:
    def __init__(self, name: str, count: int) -> None:
        self.Name = name
        self.GeometricElements = _Geometry(count)


def _sketcher(count: int = 1):
    from catia_mcp.tools.sketcher import SketcherTools

    tools = SketcherTools(connection=None)
    tools.conn = types.SimpleNamespace(
        active_document=types.SimpleNamespace(Name="P.CATPart"),
        sketch_outlines={},
        ensure_connected=lambda: None,
    )
    tools._active_sketch = _Sketch("草图.1", count)
    tools._last_geom_count = count
    return tools


def test_each_recorded_shape_knows_which_live_elements_it_made():
    tools = _sketcher(1)
    tools._active_sketch.GeometricElements.Count = 2
    tools._note_element("line", x1=0, y1=0, x2=20, y2=0)
    tools._active_sketch.GeometricElements.Count = 3
    tools._note_element("line", x1=20, y1=0, x2=20, y2=10)

    drawn = tools._drawn[tools._sketch_key("草图.1")]
    assert [entry["_span"] for entry in drawn] == [[2, 2], [3, 3]]


def test_deleting_a_shape_drops_it_from_the_record_too():
    """Without this a copy reproduces geometry the sketch no longer has."""
    tools = _sketcher(1)
    tools._active_sketch.GeometricElements.Count = 2
    tools._note_element("line", x1=0, y1=0, x2=20, y2=0)
    tools._active_sketch.GeometricElements.Count = 3
    tools._note_element("line", x1=20, y1=0, x2=20, y2=10)

    note = tools._forget_deleted_element("草图.1", 3)

    drawn = tools._drawn[tools._sketch_key("草图.1")]
    assert [entry["x2"] for entry in drawn] == [20]      # only the first line
    assert drawn[0]["_span"] == [2, 2]
    assert "dropped that line" in note
    # The outline record GSD reads has to lose it as well.
    outlines = tools.conn.sketch_outlines[tools._sketch_key("草图.1")]
    assert [entry["x2"] for entry in outlines["elements"]] == [20]


def test_a_delete_that_cannot_be_kept_exact_drops_the_record_and_says_so():
    """A rectangle is ONE entry over FOUR elements - one side cannot be replayed."""
    tools = _sketcher(1)
    tools._active_sketch.GeometricElements.Count = 5
    tools._note_element("rectangle", x1=0, y1=0, x2=10, y2=6)

    note = tools._forget_deleted_element("草图.1", 3)

    assert tools._drawn[tools._sketch_key("草图.1")] == []
    assert "record was dropped" in note
    assert "catia_sketch_copy will refuse" in note


def test_a_later_draw_after_a_delete_still_gets_a_span():
    """The counter has to be re-based, or every span afterwards is wrong."""
    tools = _sketcher(1)
    tools._active_sketch.GeometricElements.Count = 2
    tools._note_element("line", x1=0, y1=0, x2=20, y2=0)
    # delete element 2, then draw another line: it becomes element 2 again
    tools._forget_deleted_element("草图.1", 2)
    tools._active_sketch.GeometricElements.Count = 2
    tools._last_geom_count = 1
    tools._note_element("line", x1=0, y1=5, x2=10, y2=5)
    drawn = tools._drawn[tools._sketch_key("草图.1")]
    assert drawn[-1]["_span"] == [2, 2]


# ── a loft that refuses says what was measured about the guides ─────────


def test_a_loft_refusal_leads_with_the_measured_guide_gaps():
    """The report's five attempts all got "fold" first, which was never it."""
    from catia_mcp.tools.gsd import GSDTools

    factory = types.SimpleNamespace(
        AddNewLoft=lambda: types.SimpleNamespace(
            AddSectionToLoft=lambda *a: None,
            AddGuide=lambda *a: None,
        ))
    part = types.SimpleNamespace(
        Name="PART", InWorkObject=None,
        FindObjectByName=lambda name: None,
        CreateReferenceFromObject=lambda obj: ("ref", obj),
        UpdateObject=lambda shape: None,
    )
    tools = GSDTools(connection=None)
    tools.conn = types.SimpleNamespace(
        ensure_connected=lambda: None, get_active_part=lambda: part,
        active_document=types.SimpleNamespace(Name="P.CATPart"),
        refresh_display=lambda: None,
    )
    tools._factory = lambda: factory                      # type: ignore[method-assign]
    tools._ref = lambda name: ("ref", name)               # type: ignore[method-assign]
    tools._resolve_sections = lambda sections: [          # type: ignore[method-assign]
        (name, ("ref", name)) for name in sections]
    tools._refuse_point_sections = lambda *a: None        # type: ignore[method-assign]
    tools._section_kind = lambda *a: "spline"             # type: ignore[method-assign]
    # GuideA reaches every section; GuideB misses one by 2.5 mm.
    tools._cutter_gap = lambda a, b: (                       # type: ignore[method-assign]
        2.5 if b == ("ref", "GuideB") else 0.0)
    tools._finish = lambda *a, **k: (_ for _ in ()).throw(   # type: ignore[method-assign]
        RuntimeError("CATIA rejected the feature (update failed)"))

    with pytest.raises(RuntimeError) as caught:
        tools.execute("catia_gsd_multi_section_surface",
                      {"sections": ["S1", "S2"], "guides": ["GuideA", "GuideB"]})

    text = str(caught.value)
    assert "MEASURED BEFORE THIS MESSAGE" in text
    assert "'GuideB' 2.5000 mm" in text
    assert "does not reach every section" in text
    # And it has to come BEFORE the generic list, or it is just more prose.
    assert text.index("MEASURED BEFORE THIS MESSAGE") < text.index("FOLD")


def test_a_loft_refusal_that_finds_every_guide_touching_says_so():
    from catia_mcp.tools.gsd import GSDTools

    factory = types.SimpleNamespace(
        AddNewLoft=lambda: types.SimpleNamespace(
            AddSectionToLoft=lambda *a: None, AddGuide=lambda *a: None))
    part = types.SimpleNamespace(
        Name="PART", InWorkObject=None, FindObjectByName=lambda name: None,
        CreateReferenceFromObject=lambda obj: ("ref", obj),
        UpdateObject=lambda shape: None)
    tools = GSDTools(connection=None)
    tools.conn = types.SimpleNamespace(
        ensure_connected=lambda: None, get_active_part=lambda: part,
        active_document=types.SimpleNamespace(Name="P.CATPart"),
        refresh_display=lambda: None)
    tools._factory = lambda: factory                      # type: ignore[method-assign]
    tools._ref = lambda name: ("ref", name)               # type: ignore[method-assign]
    tools._resolve_sections = lambda sections: [          # type: ignore[method-assign]
        (name, ("ref", name)) for name in sections]
    tools._refuse_point_sections = lambda *a: None        # type: ignore[method-assign]
    tools._section_kind = lambda *a: "spline"             # type: ignore[method-assign]
    tools._cutter_gap = lambda a, b: 0.0                  # type: ignore[method-assign]
    tools._finish = lambda *a, **k: (_ for _ in ()).throw(   # type: ignore[method-assign]
        RuntimeError("CATIA rejected the feature (update failed)"))

    with pytest.raises(RuntimeError) as caught:
        tools.execute("catia_gsd_multi_section_surface",
                      {"sections": ["S1", "S2"], "guides": ["GuideA"]})

    text = str(caught.value)
    assert "Every guide REACHES every section" in text
    assert "NOT what this is" in text
    assert "control point" in text
