# -*- coding: utf-8 -*-
"""Regression tests for the assembly mesh box and its per-part cache (0.3.72).

Three claims from the 2026-11-06 report (P1-3, P3-10), and what this release
does about each:

  * "method='mesh' is refused for a Product" - it has not been since 0.3.45; the
    SCHEMA still said so, and that is what the report quotes. The text is fixed,
    and a test now holds it to the behaviour.
  * "`complete: true` beside `bounds: lower bound` is a contradiction" - both
    were true, because they answer different questions. `exact` and `bounds_code`
    answer the second one, and `complete_note` says which is which.
  * "45 s per call, cache the part-level boxes" - the cache existed but was
    consulted AFTER `part.Update()` and `_edited_in_context`, so a hit still paid
    the 4.2 s of window switching it was supposed to avoid. It is now checked
    before any COM call, gated on `Document.Saved`.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

BOX_STL = ("solid box\n"
           "facet normal 0 0 1\n outer loop\n"
           "  vertex 0 0 0\n  vertex 2 0 0\n  vertex 0 3 0\n"
           " endloop\nendfacet\n"
           "facet normal 0 0 1\n outer loop\n"
           "  vertex 2 0 0\n  vertex 2 3 0\n  vertex 0 3 4\n"
           " endloop\nendfacet\n"
           "endsolid box\n")


@pytest.fixture(autouse=True)
def _clean_cache():
    from catia_mcp.tools import measurement

    measurement._MESH_CACHE.clear()
    yield
    measurement._MESH_CACHE.clear()


def _tools():
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(
        active_document=SimpleNamespace(Name="ASM.CATProduct",
                                        Activate=lambda: None),
        app=SimpleNamespace(DisplayFileAlerts=True,
                            ActiveDocument=SimpleNamespace(Name="ASM.CATProduct")),
        documents=SimpleNamespace(Count=0),
        part_of=lambda doc: SimpleNamespace(Update=lambda: None),
    )
    return tools


# ── the schema vs the behaviour ────────────────────────────────────────────

def _document_param():
    from catia_mcp.tools.measurement import MeasurementTools

    for tool in MeasurementTools(None).get_tool_definitions():
        if tool["name"] == "catia_get_bounding_box":
            return tool["inputSchema"]["properties"]["document"]
    raise AssertionError("catia_get_bounding_box is gone")


def test_the_schema_no_longer_says_mesh_is_refused_for_a_product():
    """The report quoted this sentence, and it had been false since 0.3.45."""
    description = _document_param()["description"]
    assert "is refused" not in description, (
        "the schema must not say method='mesh' is refused for a Product: the "
        "refusal was removed in 0.3.45 and a 2026-11-06 report read the text and "
        "concluded there was no way to get a precise assembly envelope")
    assert "method='mesh' works on one" in description


def test_the_method_description_says_what_a_hit_costs():
    from catia_mcp.tools.measurement import MeasurementTools

    for tool in MeasurementTools(None).get_tool_definitions():
        if tool["name"] != "catia_get_bounding_box":
            continue
        method = tool["inputSchema"]["properties"]["method"]["description"]
        assert "cached" in method
        assert "unsaved edits" in method
        assert "'exact'" in method, (
            "'complete' is coverage; the description has to point at the field "
            "that answers accuracy")
        return
    raise AssertionError("catia_get_bounding_box is gone")


# ── exact / bounds_code on the assembly reply ──────────────────────────────

def _rows(count=2, stems=None):
    rows = []
    for index in range(count):
        stem = (stems[index] if stems else "PA")
        rows.append({
            "part": SimpleNamespace(
                Parent=SimpleNamespace(FullName=f"C:/p/{stem}.CATPart"),
                MainBody=SimpleNamespace(Name="PartBody")),
            "matrix": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
            "translation": [index * 100.0, 0.0, 0.0],
            "name": f"{stem}.{index + 1}", "part_number": stem,
            "index": index + 1, "problems": [],
        })
    return rows


def _box(tools, method="vertices", entry=None):
    tools._components_doc = lambda doc, only=None: _rows()
    tools._part_box_entry = entry or (
        lambda part, body, include_curved, force_mesh=False: {
            "points": [[0, 0, 0], [10, 10, 10]], "used": 2,
            "source": "vertices", "mesh_cache": None, "copy_note": ""})
    return json.loads(tools._assembly_box(SimpleNamespace(Name="A.CATProduct"),
                                          method))


def test_a_vertex_swept_assembly_says_it_is_not_exact():
    """`complete: true` used to be the only word for 'I covered everything'."""
    reply = _box(_tools())
    assert reply["complete"] is True
    assert reply["exact"] is False
    assert reply["bounds_code"] == "vertex_sweep"
    assert "LOWER bound" in reply["exact_note"]
    assert "method='mesh'" in reply["exact_note"]
    assert "ACCURATE" in reply["complete_note"]


def test_an_all_mesh_assembly_is_exact_by_the_chord_tolerance():
    reply = _box(_tools(), "mesh", entry=(
        lambda part, body, include_curved, force_mesh=False: {
            "points": [0.0, 0.0, 0.0, 10.0, 10.0, 10.0], "used": 2,
            "source": "mesh", "mesh_reason": "mesh_asked_for",
            "mesh_vertices": 2, "truncated": False,
            "mesh_cache": "kept", "copy_note": ""}))
    assert reply["exact"] is True
    assert reply["bounds_code"] == "mesh_chord"
    assert "exact_note" not in reply
    # The per-part flag is a property of the part, and it is published there.
    assert reply["parts"][0]["mesh_cache"] == "kept"
    assert "stored under its file" in reply["parts"][0]["mesh_cache_note"]


def test_a_mixed_assembly_is_neither():
    tools = _tools()
    tools._components_doc = lambda doc, only=None: _rows(stems=["PA", "PB"])
    sources = iter(["vertices", "mesh"])

    def entry(part, body, include_curved, force_mesh=False):
        source = next(sources)
        points = ([[0, 0, 0], [10, 10, 10]] if source == "vertices"
                  else [0.0, 0.0, 0.0, 10.0, 10.0, 10.0])
        return {"points": points, "used": 2, "source": source,
                "mesh_reason": "mesh_asked_for", "mesh_vertices": 2,
                "truncated": False, "mesh_cache": "reused", "copy_note": ""}

    tools._part_box_entry = entry
    reply = json.loads(tools._assembly_box(SimpleNamespace(Name="A.CATProduct"),
                                           "mesh"))
    assert reply["exact"] is False
    assert reply["bounds_code"] == "mixed"


def test_a_skipped_instance_makes_the_reply_not_exact():
    tools = _tools()
    tools._components_doc = lambda doc, only=None: _rows()
    tools._part_box_entry = lambda part, body, include_curved, force_mesh=False: {
        "error": "local box: nope"}
    reply = json.loads(tools._assembly_box(SimpleNamespace(Name="A.CATProduct"),
                                           "vertices"))
    assert reply["complete"] is False
    assert reply["exact"] is False
    assert reply["bounds_code"] == "complete_failed"


def test_the_empty_reply_has_the_same_two_keys():
    """The branch that proved it for the regime fields must not reopen it."""
    tools = _tools()
    tools._components_doc = lambda doc, only=None: []
    reply = json.loads(tools._assembly_box(SimpleNamespace(Name="E.CATProduct"),
                                           "mesh"))
    assert reply["exact"] is False
    assert reply["bounds_code"] == "empty"
    assert "complete_note" in reply


# ── the cache, before any COM call ─────────────────────────────────────────

def _part(path, saved):
    doc = SimpleNamespace(FullName=str(path), Name=os.path.basename(str(path)),
                          Saved=saved)
    calls = {"update": 0, "export": 0, "activate": 0}

    def update():
        calls["update"] += 1

    part = SimpleNamespace(Parent=doc, Update=update)
    return part, doc, calls


def _seed(path, lo, hi, seen=6):
    from catia_mcp.tools import measurement as M

    identity = M._file_identity(str(path))
    assert identity is not None
    cloud = [lo[0], lo[1], lo[2], hi[0], hi[1], hi[2]]
    M._mesh_cache_put(identity, (list(lo), list(hi), cloud, len(cloud) // 3),
                      want_cloud=True)
    return identity


def test_a_clean_part_is_answered_without_touching_catia(tmp_path):
    """The 4.2 s this saves: Activate 2.4 s + restore 1.8 s around a 10 ms export."""
    from catia_mcp.tools.measurement import MeasurementTools

    source = tmp_path / "CLEAN.CATPart"
    source.write_bytes(b"CATPart v5")
    _seed(source, [0.0, 0.0, 0.0], [2.0, 3.0, 4.0])
    part, doc, calls = _part(source, saved=True)
    tools = _tools()
    tools._export_stl = lambda doc, path: calls.__setitem__("export",
                                                            calls["export"] + 1)
    tools._edited_in_context = lambda doc: calls.__setitem__(
        "activate", calls["activate"] + 1) or False

    lo, hi, cloud, seen, note = tools._mesh_export(part, 200_000)
    assert lo == [0.0, 0.0, 0.0] and hi == [2.0, 3.0, 4.0]
    assert tools._mesh_last_cache == "reused"
    assert calls == {"update": 0, "export": 0, "activate": 0}, (
        "a cache hit must not update, activate or export")
    assert note == ""


def test_a_part_with_unsaved_edits_is_not_answered_from_the_cache(tmp_path):
    """The one mistake the gate exists to prevent: reporting the file's numbers."""
    source = tmp_path / "DIRTY.CATPart"
    source.write_bytes(b"CATPart v5")
    _seed(source, [0.0, 0.0, 0.0], [2.0, 3.0, 4.0])
    part, doc, calls = _part(source, saved=False)
    tools = _tools()

    def export(doc, path):
        calls["export"] += 1
        with open(path, "w", encoding="ascii") as handle:
            handle.write(BOX_STL)

    tools._export_stl = export
    tools._edited_in_context = lambda doc: False

    lo, hi, _cloud, _seen, note = tools._mesh_export(part, 200_000)
    assert calls["update"] == 1, "a dirty part is still updated before export"
    assert calls["export"] == 1, "and exported, not answered from the cache"
    assert tools._mesh_last_cache == "unsaved"
    # The seeded entry said 2/3/4; the export's own geometry is the truth here.
    assert lo == [0.0, 0.0, 0.0] and hi == [2.0, 3.0, 4.0]


def test_a_clean_export_is_stored_for_the_next_call(tmp_path):
    source = tmp_path / "STORE.CATPart"
    source.write_bytes(b"CATPart v5")
    part, doc, calls = _part(source, saved=True)
    tools = _tools()

    def export(doc, path):
        calls["export"] += 1
        with open(path, "w", encoding="ascii") as handle:
            handle.write(BOX_STL)

    tools._export_stl = export
    tools._edited_in_context = lambda doc: False

    tools._mesh_export(part, 200_000)
    assert tools._mesh_last_cache == "kept"
    assert calls["update"] == 0, (
        "a clean document has nothing pending, and Update() would clear the "
        "Saved flag the next call needs")

    # Second call: no export at all.
    again = _tools()
    again._export_stl = lambda doc, path: calls.__setitem__("export",
                                                            calls["export"] + 1)
    lo, hi, _cloud, _seen, _note = again._mesh_export(part, 200_000)
    assert calls["export"] == 1
    assert again._mesh_last_cache == "reused"


def test_doc_saved_none_when_the_build_does_not_answer():
    from catia_mcp.tools.measurement import _doc_is_saved

    class _Blind:
        @property
        def Saved(self):
            raise RuntimeError("no such property")

    assert _doc_is_saved(_Blind()) is None
    assert _doc_is_saved(SimpleNamespace(Saved=True)) is True
    assert _doc_is_saved(SimpleNamespace(Saved=False)) is False


def test_a_caller_that_needs_the_stl_file_is_not_answered_from_the_cache(tmp_path):
    """`catia_check_surface` reads the export, not the box - `visit` asks for it.

    A cache hit has no file to hand over, so serving one there would give that
    tool a path that does not exist and turn a working check into an error.
    """
    source = tmp_path / "VISIT.CATPart"
    source.write_bytes(b"CATPart v5")
    _seed(source, [0.0, 0.0, 0.0], [2.0, 3.0, 4.0])
    part, doc, calls = _part(source, saved=True)
    tools = _tools()

    def export(doc, path):
        calls["export"] += 1
        with open(path, "w", encoding="ascii") as handle:
            handle.write(BOX_STL)

    tools._export_stl = export
    tools._edited_in_context = lambda doc: False
    visited = []

    def visit(path):
        # `_check_surface` reads the STL inside this callback: the temp folder is
        # removed in `_mesh_export`'s finally, so "it existed when I was called"
        # is the property that matters.
        visited.append((path, os.path.getsize(path)))

    tools._mesh_export(part, 200_000, visit=visit)

    assert calls["export"] == 1, "the export must run when a file is asked for"
    assert visited and visited[0][1] >= 128
    assert tools._mesh_last_cache == "kept", (
        "the numbers are still worth keeping for the next box")
