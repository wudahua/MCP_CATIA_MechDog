"""0.3.92: the branch behind the reported envelope, and the count that looks
like a broken model.

The 2026-11-10 report found the last P0 residue: one folder where `new_part`
answered `SAVE_FAILED`, `data.call="Document.Save"`, `attempts=3`, and
`data.save_route` holding nothing but `enter` - while four structurally similar
folders saved through the staged route. Its reading ("a legacy, undocumented,
uninstrumented fallback") is close but not what the code does: the bare Save has
ONE entry condition, and the envelope is reproducible only when two reads of the
same fact disagree - the target IS this document's FullName, and the document
still reports no path. Either way the answer was a call that cannot work. It is
now unreachable for a pathless document, and the decision is a step of the route.
"""
from __future__ import annotations

import json
import types

import pytest

from catia_mcp.results import CodedError

TARGET = "F:/WokrBuddy Catia Space/饮料瓶瓶底曲面建模/verify_0391_cn.CATPart"


def _tools(monkeypatch, *, full_name="", has_path=False):
    """DocumentTools whose part is created by `documents.Add`."""
    from catia_mcp.tools import document as document_module

    doc = types.SimpleNamespace(Name="verify_0391_cn.CATPart", FullName=full_name,
                                Close=lambda: None,
                                Product=types.SimpleNamespace(PartNumber=None))
    tools = document_module.DocumentTools(connection=None)
    tools.conn = types.SimpleNamespace(
        active_document=doc,
        ensure_connected=lambda: None,
        refresh_display=lambda: None,
        documents=types.SimpleNamespace(Add=lambda kind: doc, Count=0),
        part_of=lambda d: types.SimpleNamespace(Name="Part1"),
        component_usage=lambda: {},
        last_sketch=None, last_part_name=None, active_body=None)

    monkeypatch.setattr(tools, "_document_at_path", lambda path: None)
    monkeypatch.setattr(tools, "_existing_file_note", lambda path: None)
    monkeypatch.setattr(tools, "_document_has_path", lambda d: has_path)
    return document_module, tools, doc


def _after_add_only(doc):
    """`_document_at_path` answers None for `_new_part`'s own guard, then finds
    the document once `documents.Add` has created it - the sequence a real
    session goes through."""
    asked: list[str] = []

    def at_path(path):
        asked.append(str(path))
        return None if len(asked) == 1 else doc

    return at_path


# ── the reported envelope must not be reachable ─────────────────────────


def test_a_pathless_document_never_reaches_a_bare_save(monkeypatch):
    """The reported state: the target reads as this document's own file, and the
    document still has no path. 0.3.91 answered with `doc.Save()` - three times -
    and a route that recorded only `enter`."""
    document_module, tools, doc = _tools(monkeypatch, full_name=TARGET,
                                         has_path=False)
    tools._document_at_path = _after_add_only(doc)
    bare: list[str] = []
    staged: list[str] = []
    monkeypatch.setattr(document_module.dialogs, "save",
                        lambda d, tries=3: bare.append("Save") or "x")
    monkeypatch.setattr(document_module.dialogs, "save_as",
                        lambda d, path, *a, **k: staged.append(str(path)) or str(path))

    reply = tools._new_part(None, TARGET)

    assert bare == []                                  # never a bare Save
    assert len(staged) == 2                            # temp file, then the target
    assert "through an ASCII staging file" in reply
    decision = [s for s in tools._save_trace if s["step"] == "route_decision"][0]
    assert decision["branch"].startswith("staged first save")
    assert "NO path" in decision["branch"]
    assert decision["has_path"] is False
    assert decision["same_file"] is True


def test_a_same_file_document_that_reports_a_path_still_takes_the_bare_save(
        monkeypatch):
    """The guard is on the PATH, not on the identity: a document that really is
    the target's file saves with Save, as before."""
    document_module, tools, doc = _tools(monkeypatch, full_name=TARGET,
                                         has_path=True)
    tools._document_at_path = _after_add_only(doc)
    bare: list[str] = []
    monkeypatch.setattr(document_module.dialogs, "save",
                        lambda d, tries=3: bare.append("Save") or d.Name)

    reply = tools._new_part(None, TARGET)

    assert bare == ["Save"]
    assert reply.startswith("Created new Part document")
    assert "Document 'verify_0391_cn.CATPart' saved" in reply


def test_a_bare_save_is_on_the_route_when_it_runs(monkeypatch):
    """`attempts: 3` used to travel alone, with a route that had one step."""
    document_module, tools, doc = _tools(monkeypatch, full_name=TARGET,
                                         has_path=True)
    tools._document_at_path = _after_add_only(doc)

    def fails(d, tries=3):
        raise CodedError("Save failed after 3 attempts: x (this call was a bare "
                         "doc.Save())", code="SAVE_FAILED", modified=False,
                         data={"call": "Document.Save", "document": "x",
                               "attempts": 3})

    monkeypatch.setattr(document_module.dialogs, "save", fails)

    with pytest.raises(CodedError) as caught:
        tools._new_part(None, TARGET)

    route = caught.value.data["save_route"]
    assert [step["step"] for step in route] == ["enter", "route_decision",
                                                "bare_save"]
    assert route[1]["branch"].startswith("bare Save")
    assert route[2]["ok"] is False
    assert route[2]["attempts"] == 3


# ── the decision is a step of its own ──────────────────────────────────


def test_the_route_decision_names_the_branch_and_the_facts(monkeypatch):
    """The reporter's first suggestion, and the reason it is cheap: the branch
    and the four facts behind it, in the envelope, so the next report settles it
    by reading."""
    document_module, tools, doc = _tools(monkeypatch, full_name="", has_path=False)
    monkeypatch.setattr(document_module.dialogs, "save_as",
                        lambda d, path, *a, **k: str(path))

    tools._new_part(None, TARGET)

    decision = [s for s in tools._save_trace if s["step"] == "route_decision"][0]
    assert decision["branch"] == "staged first save: this document has never been saved"
    assert decision["has_path"] is False
    assert decision["same_file"] is False
    assert decision["holder"] is None
    assert decision["file_exists"] is False


def test_a_document_with_a_path_takes_the_direct_saveas(monkeypatch):
    document_module, tools, doc = _tools(monkeypatch, full_name=TARGET,
                                         has_path=True)
    saved: list[str] = []
    monkeypatch.setattr(document_module.dialogs, "save_as",
                        lambda d, path, *a, **k: saved.append(str(path)) or str(path))

    reply = tools._new_part(None, TARGET)

    assert saved == [TARGET]
    decision = [s for s in tools._save_trace if s["step"] == "route_decision"][0]
    assert decision["branch"].startswith("direct SaveAs")
    assert "Document saved as" in reply


def test_a_file_held_by_another_document_is_refused_before_anything_is_created(
        monkeypatch):
    """`_new_part` refuses earlier and without creating a document - the refusal
    that keeps a pathless twin out of the session (2026-11-01)."""
    document_module, tools, doc = _tools(monkeypatch, full_name="", has_path=False)
    holder = types.SimpleNamespace(Name="other.CATPart", FullName=TARGET)
    tools._document_at_path = lambda path: holder

    with pytest.raises(CodedError) as caught:
        tools._new_part(None, TARGET)

    assert caught.value.code == "DOCUMENT_ALREADY_OPEN"
    assert "is already open as 'other.CATPart'" in str(caught.value)


def test_a_file_held_by_another_document_is_refused_by_save_document_too(
        monkeypatch):
    """The second line of defence - what `catia_save_document` hits, since it has
    no earlier guard of its own."""
    from catia_mcp.results import ArgumentRejected

    document_module, tools, doc = _tools(monkeypatch, full_name="", has_path=False)
    holder = types.SimpleNamespace(Name="other.CATPart", FullName=TARGET)
    monkeypatch.setattr(tools, "_document_at_path", lambda path: holder)

    with pytest.raises(ArgumentRejected) as caught:
        tools._save_document(TARGET)

    assert "is the file of 'other.CATPart'" in str(caught.value)


# ── "no file" is not a name ────────────────────────────────────────────


def test_same_file_refuses_empty_none_and_relative_names():
    """`str(None)` is "None", and a relative FullName resolves against THIS
    server's working directory. Both turn "no file" into "the same file"."""
    from catia_mcp.tools.document import DocumentTools

    class _Doc:
        def __init__(self, full):
            self.FullName = full

    assert DocumentTools._same_file(_Doc(None), _Doc(None)) is False
    assert DocumentTools._same_file(_Doc(""), _Doc("")) is False
    assert DocumentTools._same_file(_Doc("a.CATPart"), _Doc("a.CATPart")) is False
    assert DocumentTools._same_file(_Doc(r"C:\p\a.CATPart"),
                                    _Doc(r"C:\p\a.CATPart")) is True
    assert DocumentTools._same_file(_Doc(r"C:\p\a.CATPart"),
                                    _Doc(r"C:\q\a.CATPart")) is False


def test_document_at_path_ignores_documents_without_an_absolute_file():
    from catia_mcp.tools.document import DocumentTools

    class _Doc:
        def __init__(self, full):
            self.FullName = full

    class _Docs:
        def __init__(self, items):
            self._items = items
            self.Count = len(items)

        def Item(self, index):
            return self._items[index - 1]

    tools = DocumentTools(connection=None)
    tools.conn = types.SimpleNamespace(
        documents=_Docs([_Doc(""), _Doc(None), _Doc("Part1.CATPart")]))
    assert tools._document_at_path(r"C:\p\x.CATPart") is None

    found = _Doc(r"C:\p\x.CATPart")
    tools.conn.documents = _Docs([_Doc(""), found])
    assert tools._document_at_path(r"C:\p\x.CATPart") is found


# ── the count that looks like a broken model ───────────────────────────


def _three_faces_on_one_edge(tmp_path):
    """Three triangles sharing (0,0,0)-(10,0,0): the non-manifold defect itself."""
    path = tmp_path / "nonmanifold.stl"
    faces = [
        ((0, 0, 1), [(0, 0, 0), (10, 0, 0), (0, 10, 0)]),
        ((0, -1, 0), [(0, 0, 0), (10, 0, 0), (0, 0, 10)]),
        ((0, -1, 0), [(10, 0, 0), (0, 0, 0), (0, 0, -10)]),
    ]
    with open(path, "w", encoding="ascii") as handle:
        handle.write("solid test\n")
        for normal, verts in faces:
            handle.write(f"facet normal {normal[0]} {normal[1]} {normal[2]}\n")
            handle.write("  outer loop\n")
            for vertex in verts:
                handle.write(f"    vertex {vertex[0]} {vertex[1]} {vertex[2]}\n")
            handle.write("  endloop\nendfacet\n")
        handle.write("endsolid test\n")
    return str(path)


def _measure(tmp_path, visible):
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)

    class _Part:
        Parent = type("P", (), {"Name": "BOTTLE.CATPart"})()

    tools.conn = type("C", (), {
        "ensure_connected": staticmethod(lambda: None),
        "get_active_part": staticmethod(lambda: _Part()),
    })()
    tools._mesh_export = (lambda part, limit, visit:
                          visit(_three_faces_on_one_edge(tmp_path)))
    tools._visible_hybrid_sets = lambda part: visible
    return json.loads(tools._check_surface(25.0, 600000, None))


def test_the_visibility_hint_leads_the_report(tmp_path):
    """1373 reads as "my model is broken"; it was the visible GSD set on top of
    the solid. The explanation was in non_manifold_note, after the number."""
    payload = _measure(tmp_path, (1, 1))          # one set, visible

    assert payload["non_manifold_edges"] > 0
    assert list(payload)[0] == "headline"         # first key in the JSON
    assert "catia_set_visibility" in payload["headline"]
    assert "non-manifold" in payload["headline"]


def test_no_hint_when_nothing_extra_is_visible(tmp_path):
    """With every set hidden the count is about the solid itself - then the
    first thing to read is not an instruction to hide something."""
    payload = _measure(tmp_path, (0, 1))

    assert payload["non_manifold_edges"] > 0
    assert "headline" not in payload
