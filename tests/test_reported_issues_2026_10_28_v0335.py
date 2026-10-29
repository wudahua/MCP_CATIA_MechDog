# -*- coding: utf-8 -*-
"""Regression tests for the 2026-10-28 (second) batch: two reports on 0.3.34.

Report 1 (B32) had one behavioural gap and one naming complaint:

* P1: ``poses_route`` vanished entirely from a narrowed reply
  (``component='H34P.6'``) - the same field that says "which reader ran"
  answered KeyError on the one path that deliberately reads a single pose. That
  is the pattern 0.3.32 had already fixed once, for ``sweep_route: mesh``;
* P2: ``recovery.rollback_supported`` was false in every reply while sitting
  next to ``rolled_back: true``, so a caller branching on it read "this failure
  could not be rolled back" about a failure that had just been rolled back.

Report 2 (B32, M113) closed all eight of its items and left one stale note plus
five small ones:

* the ``overhead_note`` still said "90.5 % / five seconds" although
  ``components_doc_ms`` had dropped to 911.8 ms;
* ``elapsed_note`` said warm calls "vary by a few percent" while the call right
  after a cold pass measured 23.6 % above the third one;
* ``SKETCH_NOT_FOUND`` still got the default "inspect the model" advice, with
  ``effects.modified: null``, for a call that never touched the model;
* ``get_bounding_box(document='<not open>')`` was still ``COM_ERROR``;
* ``catia_shell``'s feature listed as type ``"unknown"``;
* ``error_type`` changed from ``RuntimeError`` to ``CodedError``.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest

_IDENTITY = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]

_ENTRY = {"points": [[0.0, 0.0, 0.0], [10.0, 6.0, 4.0]], "used": 8,
          "source": "vertices", "mesh_vertices": None, "copy_note": "",
          "cells": 60, "nls_chars": 2388, "points_route": "batched",
          "sweep_route": "one_call"}


class _Doc:
    Type = "ProductDocument"

    def __init__(self, name):
        self.Name = name


class _Part:
    def __init__(self, name):
        self.Name = name
        self.MainBody = SimpleNamespace(Name="PartBody",
                                        Shapes=SimpleNamespace(Count=1))


class _Child:
    def __init__(self, name, parent):
        self.Name = name
        self.PartNumber = name.split(".")[0]
        self.Position = object()
        self.ReferenceProduct = SimpleNamespace(Parent=parent)


class _Products:
    def __init__(self, children):
        self._children = children
        self.Count = len(children)

    def Item(self, index):
        return self._children[index - 1]


class _App:
    """Just enough SystemService to answer both scripts this path uses."""

    class SystemService:
        @staticmethod
        def Evaluate(_script, *rest):
            name = rest[1]
            if name == "position_components":
                return list(_IDENTITY)
            if name == "catia_mcp_poses":
                count = int(rest[2][1])
                return _IDENTITY * count + [1.0] * count
            raise RuntimeError(f"unexpected script {name}")


def _assembly_tools(names=("P1.1", "P1.2")):
    from catia_mcp.tools import measurement as M

    part = _Part("P1")
    parent = SimpleNamespace(Name="ASM.CATProduct", Part=part, Product=None)
    product = SimpleNamespace(Products=_Products([_Child(n, parent) for n in names]))
    tools = M.MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(
        ensure_connected=lambda: None,
        product_of=lambda doc: product,
        part_of=lambda doc: part,
        active_document=_Doc("ASM.CATProduct"),
        app=_App())
    tools._part_box_entry = lambda part, body, include_curved: dict(_ENTRY)
    tools._part_identity = lambda part: part.Name
    M.reset_session_books()
    return tools


# ─────────────── 1. the third honest value for the pose reader
def test_a_narrowed_reply_still_says_which_pose_reader_ran():
    """The report's P1: reply["poses_route"] raised KeyError on this path."""
    from catia_mcp.tools import measurement as M

    tools = _assembly_tools()
    reply = json.loads(tools._assembly_box(_Doc("ASM.CATProduct"), "vertices",
                                           only="P1.1"))
    assert reply["poses_route"] == "single"
    assert "narrowed" in reply["poses_route_note"]
    M.reset_session_books()


def test_an_unfiltered_reply_says_one_call():
    from catia_mcp.tools import measurement as M

    tools = _assembly_tools()
    reply = json.loads(tools._assembly_box(_Doc("ASM.CATProduct"), "vertices"))
    assert reply["poses_route"] == "one_call"
    assert "ONE CATIA script call" in reply["poses_route_note"]
    M.reset_session_books()


def test_the_fallback_route_has_its_own_sentence():
    from catia_mcp.tools import measurement as M

    assert "round trip per instance" in M._pose_note("per_component")
    assert "narrowed" in M._pose_note("single")
    assert "ONE CATIA script call" in M._pose_note("one_call")
    # Three routes, three different sentences - a caller must never have to
    # infer the route from a missing key.
    assert len({M._pose_note("single"), M._pose_note("one_call"),
                M._pose_note("per_component")}) == 3


def test_the_narrowed_route_is_set_where_the_decision_is_made():
    from catia_mcp.tools import measurement as M

    source = inspect.getsource(M.MeasurementTools._components_doc)
    assert 'self._poses_route = "single"' in source
    # Captured before the narrowed branch re-reads the list for a nested twin.
    box = inspect.getsource(M.MeasurementTools._assembly_box)
    assert "poses_route = self._poses_route" in box


# ─────────────── 2. the constant that read as the opposite
def test_recovery_has_no_constant_called_rollback_supported():
    from catia_mcp.results import operation_result

    block = operation_result(tool="t", ok=False, code="NO_MATERIAL", message="m",
                             data={}, modified=None, rolled_back=True)["recovery"]
    assert "rollback_supported" not in block
    assert block["can_request_rollback"] is False
    assert block["rolled_back"] is True
    assert "no tool in this server takes a rollback argument" in block["rollback_note"]
    # ...and the note is only there when there is a rollback story to tell.
    quiet = operation_result(tool="t", ok=False, code="X", message="m", data={},
                             modified=None)["recovery"]
    assert "rollback_note" not in quiet


# ─────────────── 3. the two notes that still described a vanished numbers
def test_the_overhead_note_no_longer_claims_five_seconds():
    from catia_mcp.tools import measurement as M

    tools = _assembly_tools()
    reply = json.loads(tools._assembly_box(_Doc("ASM.CATProduct"), "vertices"))
    note = reply["overhead_note"]
    assert "five seconds" not in note
    assert "0.3.33 and earlier" in note          # the number is history now
    assert "poses_route" in note                 # ...and this call says which
    M.reset_session_books()


def test_the_warm_note_admits_the_first_call_after_a_cold_one():
    """Both 0.3.33 and 0.3.34 measured +23.6 % there, note said "a few percent"."""
    from catia_mcp.tools import measurement as M

    tools = _assembly_tools()
    cold = json.loads(tools._assembly_box(_Doc("ASM.CATProduct"), "vertices"))
    # Two instances, one PART: cold_parts counts parts, not instances.
    # 0.3.36: the cold reply carries after_cold: null instead of omitting the key,
    # so reply["after_cold"] answers on all three regimes (reported §5.2).
    assert cold["session_first_parts"] == 1 and cold["after_cold"] is None
    first_warm = json.loads(tools._assembly_box(_Doc("ASM.CATProduct"), "vertices"))
    assert first_warm["session_first_parts"] == 0
    assert first_warm["after_cold"] is True
    assert "FIRST call after a cold pass" in first_warm["elapsed_note"]
    second_warm = json.loads(tools._assembly_box(_Doc("ASM.CATProduct"), "vertices"))
    assert second_warm["after_cold"] is False
    assert "FIRST call after a cold pass" not in second_warm["elapsed_note"]
    M.reset_session_books()


# ─────────────── 4. refusals that wrote nothing say so
def test_a_sketch_name_that_is_not_there_says_it_created_nothing():
    from catia_mcp.results import CodedError
    from catia_mcp.tools.part_design import PartDesignTools

    class _Sketches:
        Count = 1

        def Item(self, key):
            if isinstance(key, int):
                return SimpleNamespace(Name="草图.1")
            raise KeyError(key)          # a name that is not in the body

    body = SimpleNamespace(Name="零件几何体", Sketches=_Sketches(),
                           Shapes=SimpleNamespace(Count=0))
    tools = PartDesignTools(connection=None)
    tools.conn = SimpleNamespace(
        ensure_connected=lambda: None,
        get_active_part_body=lambda: body,
        get_active_part=lambda: SimpleNamespace(Bodies=SimpleNamespace(Count=1)),
        sketch_alias=lambda name: None,
        sketch_aliases_of=lambda name: [])
    with pytest.raises(CodedError) as caught:
        tools._get_last_sketch("草图.99")
    error = caught.value
    assert error.code == "SKETCH_NOT_FOUND"
    assert error.modified is False
    assert "catia_list_features" in error.next_actions[0]


def test_a_document_that_is_not_open_is_not_a_com_error():
    from catia_mcp.results import CodedError, failure_code
    from catia_mcp.tools import measurement as M

    # The phrase table alone knows it too, for any raise site that does not
    # carry a code.
    assert failure_code("No open document matches 'X.CATProduct', and no "
                        "component of an open assembly references a part") == \
        "DOCUMENT_NOT_FOUND"
    source = inspect.getsource(M.MeasurementTools._resolve_open_document)
    assert 'code="DOCUMENT_NOT_FOUND"' in source
    assert "modified=False" in source
    assert CodedError is not None


def test_the_sketcher_lookup_carries_the_same_facts():
    from catia_mcp.results import CodedError
    from catia_mcp.tools.sketcher import SketcherTools

    class _Sketches:
        Count = 1

        def Item(self, key):
            if isinstance(key, int):
                return SimpleNamespace(Name="草图.1")
            raise KeyError(key)

    body = SimpleNamespace(Sketches=_Sketches())
    tools = SketcherTools(connection=None)
    tools.conn = SimpleNamespace(get_active_part_body=lambda: body,
                                 sketch_alias=lambda name: None,
                                 last_sketch=None)
    tools._active_sketch = None
    with pytest.raises(CodedError) as caught:
        tools._locate_sketch("草图.99")
    assert caught.value.code == "SKETCH_NOT_FOUND"
    assert caught.value.modified is False


def test_the_envelope_of_a_sketch_refusal_is_modified_false():
    """catia_pad with a bad name: code, modified false, no "inspect the model"."""
    from catia_mcp.server import CATIAMCPServer

    class _Module:
        @staticmethod
        def execute(_name, _args):
            from catia_mcp.results import CodedError

            raise CodedError("No sketch named '草图.99' in the active body. "
                             "Available: 草图.1.",
                             code="SKETCH_NOT_FOUND", modified=False,
                             next_actions=["Pick a name from 'Available'"])

    server = CATIAMCPServer.__new__(CATIAMCPServer)
    server._tool_router = {"catia_pad": _Module}
    server._schemas = {"catia_pad": {"type": "object", "properties": {}}}
    server.connection = SimpleNamespace(ensure_connected=lambda: None,
                                       active_document=None)
    result = server._execute_tool("catia_pad", {})
    payload = result.structuredContent
    assert payload["code"] == "SKETCH_NOT_FOUND"
    assert payload["effects"]["modified"] is False
    assert payload["recovery"]["next_actions"] == ["Pick a name from 'Available'"]
    assert "Inspect the CATIA model" not in json.dumps(payload["recovery"])


# ─────────────── 5. the shell's feature type
def test_a_shell_is_a_shell_in_the_listing():
    from catia_mcp.tools.part_design import PartDesignTools

    assert PartDesignTools._feature_kind("盒体.1") == "shell"
    assert PartDesignTools._feature_kind("抽壳.2") == "shell"
    assert PartDesignTools._feature_kind("Shell.3") == "shell"
    assert PartDesignTools._feature_kind("凸台.1") == "pad"
