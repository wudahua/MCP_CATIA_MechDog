# -*- coding: utf-8 -*-
"""Regression tests for the 2026-10-25 v0.3.17 batch (three reports).

The big one: this server told users CATIA had no solid loft, because
``catia_diagnose`` probed a hand-written method list and ``AddNewLoft`` was not
in it. A user enumerated the type library, found it, and built a smooth propeller
with it. Measured here on B30 before writing any of this: two circles (r8 at z=0,
r16 at z=40) give a 18 765.8 mm3 SOLID through ``ShapeFactory.AddNewLoft()``, and
a 2.30 mm lens section that refuses every thick_surface offset lofts to 720.5 mm3.

So: the tool exists now, the diagnose reads the real method table, and the two
pieces of user pain that came with it - a silent file overwrite and a fillet
fingerprint collision - are handled.
"""

from __future__ import annotations

import inspect
import os

import pytest


# --------------------------------------------------------------------------
# 1. catia_solid_loft exists, and says what it is.
# --------------------------------------------------------------------------

def _tool(name: str):
    from catia_mcp.tools.part_design import PartDesignTools

    return [d for d in PartDesignTools(connection=None).get_tool_definitions()
            if d["name"] == name][0]


def test_solid_loft_is_registered_with_sections():
    doc = _tool("catia_solid_loft")
    props = doc["inputSchema"]["properties"]
    assert "sections" in props
    assert doc["inputSchema"]["required"] == ["sections"]
    assert props["sections"]["minItems"] == 2


def test_solid_loft_description_carries_the_measurement_and_the_correction():
    text = _tool("catia_solid_loft")["description"]
    assert "18765.8 mm3" in text          # the reproducible number
    assert "one curve per section" in text.lower()
    # and it owns up to what this server used to claim
    assert "no solid loft existed" in text


def test_solid_loft_refuses_a_single_section():
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)

    class _Any:
        def ensure_connected(self):
            pass

    tools.conn = _Any()
    with pytest.raises(ValueError):
        tools._solid_loft({"sections": ["only_one"]})


def test_section_reference_accepts_the_three_forms():
    """sketch in a body, hybrid element in a geoset, or Edge.N/Face.N."""
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._section_reference)
    assert "Sketches.Item" in source
    assert "HybridShapes" in source
    assert "topology_reference" in source


def test_an_unknown_section_names_the_listings_that_help():
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._section_reference)
    assert "catia_list_sketches" in source
    assert "catia_gsd_list_elements" in source


def test_a_failed_solid_loft_rolls_back_and_says_so():
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._solid_loft)
    assert "FeatureRejected" in source
    assert "rolled_back" in source
    assert "catia_sketch_spline" in source      # the "one curve" hint


# --------------------------------------------------------------------------
# 2. diagnose reads the object's own method table.
# --------------------------------------------------------------------------

class _FuncDesc:
    def __init__(self, memid):
        self.memid = memid


class _TypeInfo:
    def __init__(self, names):
        self._names = names

    def GetTypeAttr(self):
        return type("A", (), {"cFuncs": len(self._names)})()

    def GetFuncDesc(self, index):
        return _FuncDesc(index)

    def GetNames(self, memid):
        return [self._names[memid]]


class _ComObject:
    def __init__(self, names, broken=False):
        self._info = None if broken else _TypeInfo(names)

    @property
    def _oleobj_(self):
        class _Ole:
            def __init__(self, info):
                self.info = info

            def GetTypeInfo(self):
                if self.info is None:
                    raise RuntimeError("no type library")
                return self.info

        return _Ole(self._info)


def test_typeinfo_returns_every_addnew_method_sorted():
    from catia_mcp.tools.diagnostics import DiagnosticsTools

    obj = _ComObject(["AddNewLoft", "AddNewPad", "Remove", "AddNewBlend"])
    assert DiagnosticsTools._typeinfo_methods(obj) == [
        "AddNewBlend", "AddNewLoft", "AddNewPad"]


def test_typeinfo_without_a_typеlibrary_is_none_not_empty():
    from catia_mcp.tools.diagnostics import DiagnosticsTools

    assert DiagnosticsTools._typeinfo_methods(_ComObject([], broken=True)) is None


def test_the_probe_reports_the_method_table_and_says_which_list_is_which():
    from catia_mcp.tools.diagnostics import DiagnosticsTools

    probe = inspect.getsource(DiagnosticsTools._probe_methods)
    assert "typeinfo_addnew" in probe
    notes = inspect.getsource(DiagnosticsTools._notes)
    assert "NOT the capability list" in notes


# --------------------------------------------------------------------------
# 3. Save As will not silently replace a file.
# --------------------------------------------------------------------------

def test_existing_file_note_reports_size_and_time(tmp_path):
    from catia_mcp.tools.document import DocumentTools

    path = tmp_path / "part.CATPart"
    path.write_bytes(b"x" * 2048)
    note = DocumentTools._existing_file_note(str(path))
    assert note is not None
    assert "KB" in note and "last modified" in note


def test_existing_file_note_is_none_when_the_file_is_not_there(tmp_path):
    from catia_mcp.tools.document import DocumentTools

    assert DocumentTools._existing_file_note(str(tmp_path / "nope.CATPart")) is None


def _document_tools(active_name="NEW.CATPart"):
    from catia_mcp.tools.document import DocumentTools

    tools = DocumentTools(connection=None)

    class _Doc:
        Name = active_name

    class _Conn:
        active_document = _Doc()

    tools.conn = _Conn()
    tools._document_at_path = lambda path: None       # nothing holds the file
    return tools


def test_save_as_refuses_to_replace_an_existing_file(tmp_path):
    tools = _document_tools()
    path = tmp_path / "existing.CATPart"
    path.write_bytes(b"x" * 1024)
    with pytest.raises(RuntimeError) as error:
        tools._save_document(str(path))
    message = str(error.value)
    assert "already exists" in message
    assert "overwrite=true" in message
    assert os.path.getsize(path) == 1024        # untouched


def test_save_as_overwrites_when_told_to(monkeypatch, tmp_path):
    from catia_mcp import dialogs

    tools = _document_tools()
    path = tmp_path / "existing.CATPart"
    path.write_bytes(b"x" * 10)
    monkeypatch.setattr(dialogs, "save_as", lambda doc, target: str(path))
    reply = tools._save_document(str(path), overwrite=True)
    assert "replaced the existing file" in reply


def test_the_schema_documents_the_overwrite_flag():
    from catia_mcp.tools.document import DocumentTools

    doc = [d for d in DocumentTools(connection=None).get_tool_definitions()
           if d["name"] == "catia_save_document"][0]
    props = doc["inputSchema"]["properties"]
    assert "overwrite" in props
    assert props["overwrite"]["default"] is False
    assert "OVERWRITE IS REFUSED BY DEFAULT" in doc["description"]


# --------------------------------------------------------------------------
# 4. Fingerprint collisions and per-edge radii.
# --------------------------------------------------------------------------

def _table_entry(name, faces, length, fingerprint):
    return {"name": name, "bounded_by": faces, "length": length,
            "fingerprint": fingerprint, "cell": None}


def test_an_identical_fingerprint_collision_says_how_to_pass_the_index():
    from catia_mcp.tools.part_design import PartDesignTools

    fingerprint = "L2.6256|Sketch.10;3|Pad.10;2"
    table = [_table_entry("Edge.302", ["Sketch.10;3", "Pad.10;2"], 2.6256, fingerprint),
             _table_entry("Edge.303", ["Sketch.10;3", "Pad.10;2"], 2.6256, fingerprint)]
    with pytest.raises(RuntimeError) as error:
        PartDesignTools._match_edge(table, ["Sketch.10;3", "Pad.10;2"], 2.6256,
                                    fingerprint)
    message = str(error.value)
    assert "FINGERPRINTS ARE IDENTICAL" in message
    assert '"index": N' in message
    assert "fillet boundary" in message


def test_a_different_length_collision_keeps_the_old_advice():
    from catia_mcp.tools.part_design import PartDesignTools

    table = [_table_entry("Edge.1", ["A;1", "B;2"], 10.0, "L10.0|A;1|B;2"),
             _table_entry("Edge.2", ["A;1", "B;2"], 12.0, "L12.0|A;1|B;2")]
    with pytest.raises(RuntimeError) as error:
        PartDesignTools._match_edge(table, ["A;1", "B;2"], None, "A;1|B;2")
    assert "Add length_mm" in str(error.value)


def test_index_plus_faces_cross_checks_the_faces():
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._resolve_edge_specs)
    assert "is not the edge you fingerprinted" in source
    assert "the tree changed under them" in source


def test_the_batch_reply_lists_the_radius_applied_to_each_edge():
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._apply_fillets)
    assert "[applied " in source
    assert "@R{" in source


# --------------------------------------------------------------------------
# 5. The two small things from the third report.
# --------------------------------------------------------------------------

def test_pytest_prints_the_skip_summary():
    import io

    text = io.open("pyproject.toml", encoding="utf-8").read()
    assert "[tool.pytest.ini_options]" in text
    assert 'addopts = "-ra"' in text


def test_close_sketch_documents_the_scope_of_its_echo():
    from catia_mcp.tools.sketcher import SketcherTools

    doc = [d for d in SketcherTools(connection=None).get_tool_definitions()
           if d["name"] == "catia_close_sketch"][0]
    text = doc["description"]
    assert "SCOPE OF THE ECHO" in text
    assert "not an empty sketch" in text
