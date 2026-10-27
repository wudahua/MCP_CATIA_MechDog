# -*- coding: utf-8 -*-
"""Regression tests for the mesh-box cache (2026-10-26).

The copy route reads the SAVED file, so its answer is a function of that file.
Reusing it is not an approximation - it is the same number without copying,
opening, updating, exporting and closing a document per part, which measured
12-17 s per curved part on B30 (5 parts = 74.2 s of a 94.4 s assembly box).

The tests that matter are the invalidation ones: a file that changed must be
re-measured, and an entry that has no cloud must never be talked into producing
one from its two corners - that was the diagonal bug of 0.3.23.
"""

from __future__ import annotations

import inspect
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


class _FakeOpened:
    def __init__(self, path, counter):
        self.path = path
        self.counter = counter
        self.closed = False

    def ExportData(self, target, fmt):
        self.counter["exports"] += 1
        with open(target, "w", encoding="ascii") as handle:
            handle.write(BOX_STL)

    def Close(self):
        self.closed = True


class _FakeDocs:
    def __init__(self, counter):
        self.counter = counter
        self.Count = 0

    def Open(self, path):
        self.counter["opens"] += 1
        return _FakeOpened(path, self.counter)


def _tools(counter):
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(
        app=SimpleNamespace(DisplayFileAlerts=True),
        documents=_FakeDocs(counter),
        part_of=lambda doc: SimpleNamespace(Update=lambda: None),
    )
    return tools


@pytest.fixture(autouse=True)
def _clean_cache():
    from catia_mcp.tools import measurement

    measurement._MESH_CACHE.clear()
    yield
    measurement._MESH_CACHE.clear()


def _saved_part(tmp_path, name="PART.CATPart", body=b"CATPart v5"):
    path = tmp_path / name
    path.write_bytes(body)
    return path


def test_the_same_unchanged_file_is_measured_once(tmp_path):
    from catia_mcp.tools.measurement import MeasurementTools

    source = _saved_part(tmp_path)
    counter = {"opens": 0, "exports": 0}
    tools = _tools(counter)
    doc = SimpleNamespace(FullName=str(source), Name=os.path.basename(str(source)))
    out = tmp_path / "part.stl"

    first = tools._mesh_from_copy(doc, str(out))
    assert counter["opens"] == 1 and counter["exports"] == 1
    assert tools._mesh_last_cache == "miss"

    second = tools._mesh_from_copy(doc, str(out))
    assert counter["opens"] == 1, "the second call must not open a copy again"
    assert counter["exports"] == 1
    assert tools._mesh_last_cache == "hit"
    # ... and the numbers are identical, not merely close.
    assert first == second
    assert first[0] == [0.0, 0.0, 0.0] and first[1] == [2.0, 3.0, 4.0]


def test_a_file_that_changed_is_measured_again(tmp_path):
    source = _saved_part(tmp_path)
    counter = {"opens": 0, "exports": 0}
    tools = _tools(counter)
    doc = SimpleNamespace(FullName=str(source), Name="PART.CATPart")
    out = tmp_path / "part.stl"

    tools._mesh_from_copy(doc, str(out))
    assert counter["opens"] == 1

    # Different size AND mtime: a re-saved part.
    source.write_bytes(b"CATPart v5 with more geometry")
    tools._mesh_from_copy(doc, str(out))
    assert counter["opens"] == 2


def test_touching_the_file_without_changing_size_still_invalidates(tmp_path):
    source = _saved_part(tmp_path)
    counter = {"opens": 0, "exports": 0}
    tools = _tools(counter)
    doc = SimpleNamespace(FullName=str(source), Name="PART.CATPart")
    out = tmp_path / "part.stl"

    tools._mesh_from_copy(doc, str(out))
    future = os.stat(source).st_mtime_ns + 5_000_000_000
    os.utime(source, ns=(future, future))
    tools._mesh_from_copy(doc, str(out))
    assert counter["opens"] == 2


def test_a_cloud_request_is_not_satisfied_by_the_two_corners(tmp_path):
    from catia_mcp.tools import measurement as M

    source = _saved_part(tmp_path)
    identity = M._file_identity(str(source))
    assert identity is not None
    # An extent-only entry (what a part-level box stores).
    M._mesh_cache_put(identity, ([0.0, 0.0, 0.0], [2.0, 3.0, 4.0], None, 6),
                      want_cloud=False)
    assert M._mesh_cache_get(identity, want_cloud=False) is not None
    # A world box needs the cloud; two corners would be the 0.3.23 diagonal bug.
    assert M._mesh_cache_get(identity, want_cloud=True) is None


def test_a_cloud_is_reused_when_it_was_stored(tmp_path):
    from catia_mcp.tools import measurement as M

    source = _saved_part(tmp_path)
    identity = M._file_identity(str(source))
    cloud = [0.0, 0.0, 0.0, 2.0, 3.0, 4.0]
    M._mesh_cache_put(identity, ([0.0, 0.0, 0.0], [2.0, 3.0, 4.0], cloud, 2),
                      want_cloud=True)
    hit = M._mesh_cache_get(identity, want_cloud=True)
    assert hit is not None and hit[2] == cloud


def test_a_truncated_cloud_is_not_cached(tmp_path):
    from catia_mcp.tools import measurement as M

    source = _saved_part(tmp_path)
    identity = M._file_identity(str(source))
    # 6 vertices were scanned but only 2 kept: the cloud cannot answer for the file.
    M._mesh_cache_put(identity, ([0.0, 0.0, 0.0], [2.0, 3.0, 4.0],
                                 [0.0, 0.0, 0.0, 2.0, 3.0, 4.0], 6),
                      want_cloud=True)
    assert M._mesh_cache_get(identity, want_cloud=True) is None
    assert M._mesh_cache_get(identity, want_cloud=False) is not None


def test_the_cache_is_bounded(tmp_path):
    from catia_mcp.tools import measurement as M

    for index in range(M._MESH_CACHE_LIMIT + 5):
        identity = (f"C:/fake/{index}.CATPart", index, index)
        M._mesh_cache_put(identity, ([0.0] * 3, [1.0] * 3, None, 1),
                          want_cloud=False)
    assert len(M._MESH_CACHE) == M._MESH_CACHE_LIMIT


def test_only_the_copy_route_is_cached():
    from catia_mcp.tools.measurement import MeasurementTools

    copy = inspect.getsource(MeasurementTools._mesh_from_copy)
    export = inspect.getsource(MeasurementTools._mesh_export)
    assert "_mesh_cache_get" in copy and "_mesh_cache_put" in copy
    # An in-place export may include unsaved edits - no file identity describes it.
    assert "_mesh_cache" not in export


def test_the_reply_says_whether_the_box_was_reused(tmp_path):
    from catia_mcp.tools.measurement import MeasurementTools

    source = _saved_part(tmp_path)
    counter = {"opens": 0, "exports": 0}
    tools = _tools(counter)
    doc = SimpleNamespace(FullName=str(source), Name="PART.CATPart")

    class _Part:
        Parent = doc

        class MainBody:
            pass

    part = _Part()
    tools._edited_in_context = lambda d: True

    lo, hi, _cloud, _seen, note = tools._mesh_export(part)
    assert "reused" not in note
    lo, hi, _cloud, _seen, note = tools._mesh_export(part)
    assert "reused" in note
    assert counter["opens"] == 1


# --------------------------------------------------------------------------
# 6. Opening a file CATIA already holds must not raise its "reopen?" dialog.
# --------------------------------------------------------------------------

class _OpenDoc:
    def __init__(self, name, full_name):
        self.Name = name
        self.FullName = full_name
        self.activated = 0

    def Activate(self):
        self.activated += 1


def test_opening_an_already_open_document_activates_it_instead(tmp_path):
    """The dialog this avoids has a Yes that RELOADS the document and discards
    unsaved edits - and with nobody to answer it, the call never returns."""
    from catia_mcp.tools.document import DocumentTools

    path = tmp_path / "ASM.CATProduct"
    path.write_text("x", encoding="ascii")
    doc = _OpenDoc("ASM.CATProduct", str(path))

    opened = {"count": 0}

    class _Docs:
        Count = 1

        @staticmethod
        def Item(index):
            return doc

        @staticmethod
        def Open(native):
            opened["count"] += 1
            return doc

    tools = DocumentTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None, documents=_Docs(),
                                 last_sketch="stale", active_body="stale")
    reply = tools._open_document(str(path))

    assert opened["count"] == 0, "must not call Documents.Open on an open file"
    assert doc.activated == 1
    assert "already open" in reply
    assert "reload" in reply
    assert tools.conn.last_sketch is None


def test_opening_a_file_that_is_not_open_still_opens_it(tmp_path):
    from catia_mcp.tools.document import DocumentTools

    path = tmp_path / "NEW.CATPart"
    path.write_text("x", encoding="ascii")
    made = _OpenDoc("NEW.CATPart", str(path))
    opened = {"count": 0}

    class _Docs:
        Count = 0

        @staticmethod
        def Item(index):
            raise IndexError

        @staticmethod
        def Open(native):
            opened["count"] += 1
            return made

    tools = DocumentTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None, documents=_Docs(),
                                 last_sketch=None, active_body=None)
    reply = tools._open_document(str(path))
    assert opened["count"] == 1
    assert reply.startswith("Opened document: 'NEW.CATPart'")
