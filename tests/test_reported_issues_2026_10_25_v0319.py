# -*- coding: utf-8 -*-
"""Regression tests for the 2026-10-25 v0.3.18 batch (four reports).

Two things drove this batch:

* Save As had two refusals with different wording - the "an open document holds
  this file" branch reported neither size nor mtime, so a caller could not tell
  a finished part from a stale one, and could not tell which guard it had hit;
* a 24-instance assembly had to be built with 24 calls of ``add_component``,
  each in its own isolated process, producing peer components instead of
  instances of one reference. ``Products.AddComponent(ref)`` was measured to
  create a true instance (the new component's ReferenceProduct is the same
  product), so it has a tool now.
"""

from __future__ import annotations

import inspect

import pytest


# --------------------------------------------------------------------------
# 1. Both Save As refusals report what is on disk, and say which guard fired.
# --------------------------------------------------------------------------

class _Doc:
    def __init__(self, name):
        self.Name = name


def _document_tools(holder=None, usages=None, active="NEW.CATPart"):
    from catia_mcp.tools.document import DocumentTools

    tools = DocumentTools(connection=None)

    class _Conn:
        active_document = _Doc(active)

    tools.conn = _Conn()
    tools._document_at_path = lambda path: holder
    tools._component_usage = lambda: usages or {}
    return tools


def test_the_holder_refusal_reports_size_and_time_too(tmp_path):
    path = tmp_path / "P01.CATPart"
    path.write_bytes(b"x" * 4096)
    tools = _document_tools(holder=_Doc("P01.CATPart"),
                            usages={"P01.CATPart": ["P00.CATProduct"]})
    with pytest.raises(RuntimeError) as error:
        tools._save_document(str(path))
    message = str(error.value)
    assert "already exists (4.0 KB, last modified" in message
    assert "P00.CATProduct" in message


def test_the_holder_refusal_says_overwrite_will_not_help(tmp_path):
    path = tmp_path / "P01.CATPart"
    path.write_bytes(b"x" * 64)
    tools = _document_tools(holder=_Doc("P01.CATPart"))
    with pytest.raises(RuntimeError) as error:
        tools._save_document(str(path), overwrite=True)
    assert "overwrite=true does NOT help" in str(error.value)


def test_a_new_path_is_announced_as_new(monkeypatch, tmp_path):
    from catia_mcp import dialogs

    tools = _document_tools()
    path = tmp_path / "fresh.CATPart"
    monkeypatch.setattr(dialogs, "save_as", lambda doc, target: str(path))
    reply = tools._save_document(str(path))
    assert "new file - nothing was overwritten" in reply


def test_a_replaced_path_is_announced_as_replaced(monkeypatch, tmp_path):
    from catia_mcp import dialogs

    tools = _document_tools()
    path = tmp_path / "old.CATPart"
    path.write_bytes(b"x" * 2048)
    monkeypatch.setattr(dialogs, "save_as", lambda doc, target: str(path))
    reply = tools._save_document(str(path), overwrite=True)
    assert "replaced the existing file" in reply
    assert "KB" in reply


# --------------------------------------------------------------------------
# 2. A missing inertia tensor is a field, not a paragraph.
# --------------------------------------------------------------------------

def test_a_missing_tensor_is_reported_as_a_field():
    from catia_mcp.tools.measurement import MeasurementTools

    source = inspect.getsource(MeasurementTools._part_inertia)
    assert 'payload["tensor"] = None' in source
    assert 'payload["tensor_reason"]' in source
    assert "tensor_reason" in source


# --------------------------------------------------------------------------
# 3. A revolved solid's zero vertices is not a broken measurement.
# --------------------------------------------------------------------------

def test_zero_vertices_on_a_revolved_solid_is_explained():
    from catia_mcp.tools.planner import PlannerTools

    source = inspect.getsource(PlannerTools._count_topology)
    assert "vertices_note" in source
    assert "revolved" in source


# --------------------------------------------------------------------------
# 4. catia_copy_component.
# --------------------------------------------------------------------------

class _Component:
    def __init__(self, name, part_number):
        self.Name = name
        self.PartNumber = part_number
        self.ReferenceProduct = self


class _Products:
    def __init__(self, components):
        self._items = list(components)
        self.Count = len(self._items)
        self.added = []

    def Item(self, key):
        if isinstance(key, int):
            return self._items[key - 1]
        for item in self._items:
            if item.Name == key:
                return item
        raise KeyError(key)

    def AddComponent(self, reference):
        fresh = _Component(f"{reference.PartNumber}.{self.Count + 1}",
                           reference.PartNumber)
        self._items.append(fresh)
        self.Count = len(self._items)
        self.added.append(fresh)
        return fresh


def _product(components):
    return type("P", (), {"Products": _Products(components),
                          "Name": "ASM"})()


def test_copy_component_repeats_one_reference():
    from catia_mcp.tools.assembly import AssemblyTools

    class _Conn:
        def refresh_display(self):
            pass

    tools = AssemblyTools(_Conn())
    product = _product([_Component("MOTOR.1", "MOTOR")])
    tools._require_active_product = lambda: product
    reply = tools._copy_component({"component_name": "MOTOR.1", "count": 3})
    assert "Added 3 instance(s)" in reply
    assert "they share one reference" in reply.lower()
    assert product.Products.Count == 4
    assert "MOTOR.4" in reply


def test_copy_component_reports_a_partial_failure():
    from catia_mcp.tools.assembly import AssemblyTools

    class _Conn:
        def refresh_display(self):
            pass

    tools = AssemblyTools(_Conn())
    product = _product([_Component("MOTOR.1", "MOTOR")])

    calls = {"n": 0}
    original = product.Products.AddComponent

    def flaky(reference):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("CATIA said no")
        return original(reference)

    product.Products.AddComponent = flaky
    tools._require_active_product = lambda: product
    with pytest.raises(RuntimeError) as error:
        tools._copy_component({"component_name": "MOTOR.1", "count": 3})
    message = str(error.value)
    assert "refused instance 2 of 3" in message
    assert "1 instance(s) were added before it" in message


def test_copy_component_is_registered_with_a_count():
    from catia_mcp.tools.assembly import AssemblyTools

    doc = [d for d in AssemblyTools(connection=None).get_tool_definitions()
           if d["name"] == "catia_copy_component"][0]
    props = doc["inputSchema"]["properties"]
    assert props["count"]["default"] == 1
    assert props["component_name"]["type"] == ["string", "object"]


def test_the_description_records_why_paste_is_not_used():
    from catia_mcp.tools.assembly import AssemblyTools

    doc = [d for d in AssemblyTools(connection=None).get_tool_definitions()
           if d["name"] == "catia_copy_component"][0]
    text = doc["description"]
    assert "ReferenceProduct" in text
    assert "Copy/Paste is NOT usable" in text


# --------------------------------------------------------------------------
# 5. Addressing one instance among many.
# --------------------------------------------------------------------------

def test_a_component_can_be_addressed_by_part_and_occurrence():
    from catia_mcp.tools.assembly import AssemblyTools

    product = _product([_Component("MOTOR.1", "MOTOR"),
                        _Component("MOTOR.2", "MOTOR"),
                        _Component("STAND.1", "STAND"),
                        _Component("MOTOR.3", "MOTOR")])
    assert AssemblyTools._resolve_component(
        product, {"part_number": "MOTOR", "occurrence": 2}).Name == "MOTOR.2"
    assert AssemblyTools._resolve_component(
        product, {"part_number": "MOTOR", "occurrence": 3}).Name == "MOTOR.3"


def test_an_occurrence_past_the_end_lists_the_real_ones():
    from catia_mcp.tools.assembly import AssemblyTools

    product = _product([_Component("MOTOR.1", "MOTOR"),
                        _Component("MOTOR.2", "MOTOR")])
    with pytest.raises(ValueError) as error:
        AssemblyTools._resolve_component(product,
                                         {"part_number": "MOTOR", "occurrence": 9})
    message = str(error.value)
    assert "has 2 instance(s)" in message
    assert "MOTOR.1" in message and "MOTOR.2" in message


def test_a_name_still_works_and_so_does_an_index():
    from catia_mcp.tools.assembly import AssemblyTools

    product = _product([_Component("A.1", "A"), _Component("B.1", "B")])
    assert AssemblyTools._resolve_component(product, "B.1").Name == "B.1"
    assert AssemblyTools._resolve_component(product, "2").Name == "B.1"


def test_the_transform_tool_accepts_the_object_form():
    from catia_mcp.tools.assembly import AssemblyTools

    doc = [d for d in AssemblyTools(connection=None).get_tool_definitions()
           if d["name"] == "catia_set_component_transform"][0]
    assert doc["inputSchema"]["properties"]["component_name"]["type"] == [
        "string", "object"]


def test_list_components_labels_the_rotation_convention():
    from catia_mcp.tools.assembly import AssemblyTools

    source = inspect.getsource(AssemblyTools._list_components)
    assert '"convention": "matrix"' in source
    assert '"occurrence"' in source
    doc = [d for d in AssemblyTools(connection=None).get_tool_definitions()
           if d["name"] == "catia_list_components"][0]
    assert "part_number" in doc["inputSchema"]["properties"]
    assert "TRANSPOSE" in doc["description"]


# --------------------------------------------------------------------------
# 6. Deleting "the last feature" has to be asked for.
# --------------------------------------------------------------------------

def test_delete_feature_without_a_target_is_refused():
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._delete_feature)
    assert "args.get(\"last\")" in source
    assert "LAST CREATED feature" in source


def test_delete_feature_schema_has_the_last_switch():
    from catia_mcp.tools.part_design import PartDesignTools

    doc = [d for d in PartDesignTools(connection=None).get_tool_definitions()
           if d["name"] == "catia_delete_feature"][0]
    assert doc["inputSchema"]["properties"]["last"]["default"] is False
    assert "last=true" in doc["description"]
