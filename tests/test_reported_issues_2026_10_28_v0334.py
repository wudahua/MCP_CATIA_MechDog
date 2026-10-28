# -*- coding: utf-8 -*-
"""Regression tests for the 2026-10-28 batch: two acceptance reports on 0.3.33.

Report 1 (B32) closed everything it had asked for and found one shape problem:

* ``catia_list_features`` answered the bare STRING "No features in the active
  body" when a part had sketches but no feature - the one moment its own
  description says to call it - so ``resp["sketches"]`` raised instead of
  returning the sketch; ``catia_list_components`` did the same on an empty
  product, and the report called it "a pattern, not a typo".

Report 2 (B32) found two regressions and five smaller items:

* P0: a bare call collapsed ``NO_MATERIAL`` and ``SKETCH_NOT_FOUND`` into
  ``COM_ERROR`` while the same failure inside ``catia_run_plan`` kept its code;
* P1: the ``recovery`` block contradicted the message ("rolled back, volume
  stayed 96 750.0" next to ``rolled_back: null``), and appeared even for a
  lookup that wrote nothing;
* P1: warm ``parts[]`` 12.6-17.3 % slower than 0.3.31, and the docstring quoted
  one afternoon's absolute seconds as if they were a contract;
* P2: ``components_doc_ms`` was 90.5 % of the overhead block;
* P3: ``measurements`` silently empty in a stopping plan, ``in_work_object``
  left on a datum plane after a pocket rollback, ``shell`` without a volume.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest


# ─────────────── 1. one failure, one code, on both paths
def test_the_bare_call_and_the_plan_agree_on_the_code():
    """Same failure, two shapes: NO_MATERIAL on one, COM_ERROR on the other."""
    from catia_mcp.results import failure_code
    from catia_mcp.server import CATIAMCPServer
    from catia_mcp.tools.planner import _failure_code

    error = RuntimeError(
        "CATIA built no material for this pad in EITHER direction, so it was "
        "rolled back. Volume stayed 96750.0 mm3.")
    # The plan's classifier and the server's share one table now.
    assert _failure_code(str(error), error) == "NO_MATERIAL"
    # ...and the server's default (COM_ERROR) only applies when nothing matches.
    assert failure_code(str(error), error, default="COM_ERROR") == "NO_MATERIAL"
    assert failure_code("方法失败", RuntimeError("方法失败"),
                        default="COM_ERROR") == "COM_ERROR"
    assert failure_code("No sketch named '草图.99'. Available: 草图.1",
                        RuntimeError("x")) == "SKETCH_NOT_FOUND"
    assert failure_code("anything", AttributeError("no such method")) == \
        "UNSUPPORTED_CAPABILITY"
    assert CATIAMCPServer is not None


def test_a_coded_error_carries_its_code_into_the_envelope():
    from catia_mcp.results import CodedError, failure_code

    error = CodedError("no sketch named 'X'", code="SKETCH_NOT_FOUND")
    assert failure_code("anything at all", error) == "SKETCH_NOT_FOUND"


# ─────────────── 2. recovery only where it is true
def test_a_read_only_tool_does_not_claim_the_model_may_be_dirty():
    from catia_mcp.results import is_read_only

    for name in ("catia_get_bounding_box", "catia_list_features",
                 "catia_measure_topology", "catia_diagnose",
                 "catia_sketch_get_geometry", "catia_gsd_list_elements",
                 "catia_export"):
        assert is_read_only(name), name
    for name in ("catia_pad", "catia_pocket", "catia_hole", "catia_fillet",
                 "catia_delete_feature", "catia_save_document"):
        assert not is_read_only(name), name


def test_a_read_only_failure_has_no_recovery_block():
    from catia_mcp.results import NO_RECOVERY, operation_result

    payload = operation_result(tool="catia_get_bounding_box", ok=False,
                               code="DOCUMENT_NOT_FOUND", message="no such doc",
                               data={"failure_stage": "execute"},
                               modified=False, recovery=NO_RECOVERY)
    assert "recovery" not in payload
    assert payload["effects"]["modified"] is False


def test_a_pad_that_rolled_back_says_so_in_the_envelope():
    from catia_mcp.results import operation_result

    payload = operation_result(
        tool="catia_pad", ok=False, code="NO_MATERIAL",
        message="rolled back, volume stayed 96750.0",
        data={}, modified=None, rolled_back=True,
        next_actions=["Change the sketch's offset or profile, then retry"])
    recovery = payload["recovery"]
    assert recovery["rolled_back"] is True
    assert payload["effects"]["modified"] is False
    # The generic "fix the argument and retry" is wrong for this failure.
    assert recovery["next_actions"] == ["Change the sketch's offset or profile, "
                                        "then retry"]


def test_the_pad_raises_a_rejection_that_carries_the_rollback():
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._pad)
    assert "raise FeatureRejected(" in source
    assert "next_actions=[" in source
    assert "requested=requested_name" in source


def test_the_sketch_that_could_not_be_found_carries_its_code():
    from catia_mcp.tools.part_design import PartDesignTools
    from catia_mcp.tools.sketcher import SketcherTools

    for owner in (PartDesignTools._get_last_sketch, SketcherTools._locate_sketch):
        source = inspect.getsource(owner)
        assert 'code="SKETCH_NOT_FOUND"' in source, owner


# ─────────────── 3. a listing keeps its shape when it is empty
class _Sketch:
    def __init__(self, name):
        self.Name = name


class _Sketches:
    def __init__(self, names=()):
        self._items = [_Sketch(n) for n in names]
        self.Count = len(self._items)

    def Item(self, key):
        if isinstance(key, int):
            return self._items[key - 1]
        raise KeyError(key)


class _Shapes:
    def __init__(self, names=()):
        self._items = [SimpleNamespace(Name=n, Type=0) for n in names]
        self.Count = len(self._items)

    def Item(self, key):
        return self._items[key - 1]


def _part_tools(shapes=(), sketches=("草图.1",)):
    from catia_mcp.tools.part_design import PartDesignTools

    body = SimpleNamespace(Name="零件几何体", Shapes=_Shapes(shapes),
                           Sketches=_Sketches(sketches))
    part = SimpleNamespace(Bodies=SimpleNamespace(Count=1),
                           HybridBodies=SimpleNamespace(Count=0))
    conn = SimpleNamespace(ensure_connected=lambda: None,
                           get_active_part=lambda: part,
                           get_active_part_body=lambda: body,
                           sketch_aliases_of=lambda name: [])
    tools = PartDesignTools(connection=None)
    tools.conn = conn
    return tools


def test_a_body_with_only_sketches_still_answers_with_an_object():
    """The report's P1: a bare string here breaks every resp["sketches"]."""
    payload = json.loads(_part_tools(shapes=(), sketches=("草图.1",))
                         ._list_features())
    assert payload["count"] == 0
    assert payload["features"] == []
    assert payload["sketch_count"] == 1
    assert payload["sketches"][0]["name"] == "草图.1"
    assert "no Part Design feature yet" in payload["note"]


def test_a_body_with_nothing_in_it_also_answers_with_an_object():
    payload = json.loads(_part_tools(shapes=(), sketches=())._list_features())
    assert payload["sketches"] == [] and payload["sketch_count"] == 0
    assert payload["count"] == 0


def test_an_empty_assembly_listing_is_an_object_too():
    from catia_mcp.tools.assembly import AssemblyTools

    class _Empty:
        Count = 0

        def Item(self, _index):
            raise IndexError

    product = SimpleNamespace(Products=_Empty())
    tools = AssemblyTools(connection=None)
    tools.conn = SimpleNamespace(
        ensure_connected=lambda: None,
        get_active_product=lambda: product,
        product_of=lambda doc: product,
        active_document=SimpleNamespace(Name="ASM.CATProduct",
                                        Type="ProductDocument"))
    payload = json.loads(tools._list_components())
    assert payload["components"] == [] and payload["count"] == 0
    assert "holds no component" in payload["note"]


def test_an_empty_constraint_listing_is_an_object_too():
    from catia_mcp.tools.assembly import AssemblyTools

    product = SimpleNamespace(Connections=lambda name: SimpleNamespace(Count=0))
    tools = AssemblyTools(connection=None)
    tools.conn = SimpleNamespace(get_active_product=lambda: product)
    payload = json.loads(tools._list_constraints())
    assert payload == {"constraints": [], "count": 0,
                       "note": "the active assembly holds no constraint - an "
                               "empty list, not a failure"}


# ─────────────── 4. an empty product is not "every component failed"
def test_an_empty_product_says_it_holds_no_component():
    from catia_mcp.tools import measurement as M

    tools = M.MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None,
                                 active_document=None)
    tools._components_doc = lambda doc, only=None: []
    tools._active_name = lambda: "EMPTY.CATProduct"
    reply = json.loads(tools._assembly_box(
        SimpleNamespace(Name="EMPTY.CATProduct"), "vertices"))
    assert reply["complete"] is False
    assert reply["skipped"] == [{"component": "EMPTY.CATProduct",
                                 "kind": "unknown",
                                 "error": "the assembly has no components"}]
    assert "holds no component" in reply["method"]
    assert "each one's reason is in 'skipped'" not in reply["method"]


# ─────────────── 5. the cost block reconciles to the digit
def _assembly_tools(entries=None, walk_ms=0.0):
    from catia_mcp.tools import measurement as M

    entries = entries or {"P31A": {
        "points": [[0.0, 0.0, 0.0], [10.0, 6.0, 4.0]], "used": 8,
        "source": "vertices", "mesh_vertices": None, "copy_note": "",
        "cells": 60, "nls_chars": 2388, "points_route": "batched",
        "sweep_route": "one_call"}}

    class _Part:
        def __init__(self, name):
            self.Name = name
            self.MainBody = SimpleNamespace(Name="PartBody",
                                            Shapes=SimpleNamespace(Count=1))

    tools = M.MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None,
                                 active_document=None)
    parts = {name: _Part(name) for name in entries}
    tools._components_doc = lambda doc, only=None: [{
        "index": 1, "name": f"{name}.1", "part_number": name,
        "matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        "translation": [0.0, 0.0, 0.0], "part": part, "nested_doc": None,
        "kind": "part", "problems": []} for name, part in parts.items()]
    tools._part_box_entry = lambda part, body, include_curved: dict(
        entries[part.Name])
    tools._part_identity = lambda part: part.Name
    M._MEASURED_PARTS.clear()
    return tools


def test_the_three_cost_buckets_add_up_to_overhead_exactly():
    tools = _assembly_tools()
    reply = json.loads(tools._assembly_box(
        SimpleNamespace(Name="ASM.CATProduct"), "vertices"))
    breakdown = reply["overhead_breakdown"]
    assert round(sum(breakdown.values()), 1) == reply["overhead_ms"]
    assert reply["parts_ms"] + reply["overhead_ms"] == reply["elapsed_ms"]
    from catia_mcp.tools import measurement as M
    M._MEASURED_PARTS.clear()


def test_a_negative_remainder_comes_off_the_largest_bucket():
    """Rounding must never report a negative 'report_ms'."""
    from catia_mcp.tools import measurement as M

    source = inspect.getsource(M.MeasurementTools._assembly_box)
    assert "if report_ms < 0:" in source
    assert "walk_other = round(walk_other + report_ms, 1)" in source


def test_the_cold_note_quotes_this_call_and_a_range_of_versions():
    tools = _assembly_tools()
    reply = json.loads(tools._assembly_box(
        SimpleNamespace(Name="ASM.CATProduct"), "vertices"))
    note = reply["elapsed_note"]
    assert "parts_ms" in note and "overhead_ms" in note
    assert "4.65x" in note and "7.6x" in note and "7.24x" in note
    from catia_mcp.tools import measurement as M
    M._MEASURED_PARTS.clear()


# ─────────────── 6. the 12 numbers per instance, read once
def test_batch_positions_reads_the_status_bytes():
    from catia_mcp.tools.assembly import batch_positions

    flat = [0.0] * 26
    for i in range(12):
        flat[i] = float(i)          # component 1: 0..11
    flat[24] = 1.0                  # component 1 read
    flat[25] = 0.0                  # component 2 refused

    class _App:
        class SystemService:
            @staticmethod
            def Evaluate(*args, **kwargs):
                return list(flat)

    poses = batch_positions(_App(), object(), 2)
    assert poses[0] == [float(i) for i in range(12)]
    assert poses[1] is None


def test_batch_positions_returns_none_when_the_script_is_refused():
    from catia_mcp.tools.assembly import batch_positions

    class _App:
        class SystemService:
            @staticmethod
            def Evaluate(*args, **kwargs):
                raise RuntimeError("no CATScript here")

    assert batch_positions(_App(), object(), 3) is None
    assert batch_positions(object(), object(), 0) == []


def test_the_script_carries_a_status_per_instance():
    from catia_mcp.tools.assembly import POSES_SCRIPT

    # Empty slots come back as None, so the status byte - not the numbers - has
    # to decide whether a pose was read.
    assert "out(12 * count + i - 1) = 0" in POSES_SCRIPT
    assert "If Err.Number = 0 Then" in POSES_SCRIPT
    assert "ReDim out(13 * count - 1)" in POSES_SCRIPT


def test_the_assembly_reports_how_the_poses_were_read():
    from catia_mcp.tools import measurement as M

    tools = M.MeasurementTools(connection=None)
    tools._poses_route = "one_call"
    source = inspect.getsource(M.MeasurementTools._assembly_box)
    assert 'payload["poses_route"] = self._poses_route' in source
    order = inspect.getsource(M._cost_first)
    assert '"poses_route"' in order and '"poses_route_note"' in order
    assert tools is not None


def test_the_pose_route_is_only_batched_without_a_filter():
    from catia_mcp.tools import measurement as M

    source = inspect.getsource(M.MeasurementTools._components_doc)
    # A narrowed call wants ONE pose; batching all of them was the 5.8 s entry
    # fee 0.3.26 removed.
    assert "if wanted is None:" in source
    assert "batch_positions" in source


# ─────────────── 7. the small items
def test_a_stopping_plan_says_the_measurement_was_not_taken():
    from catia_mcp.tools.planner import PlannerTools

    class _Module:
        @staticmethod
        def execute(name, arguments):
            raise RuntimeError("boom")

    tools = PlannerTools(connection=None)
    tools._router = {"catia_pad": _Module, "catia_step": _Module}
    tools.bind({"catia_pad": _Module, "catia_step": _Module})
    reply = tools._run_plan({
        "steps": [{"tool": "catia_pad", "args": {}}], "stop_on_error": True})
    data = reply["data"]
    assert data["measurements_status"] == "not_taken"
    assert "NOT measured" in data["measurements_note"]


def test_a_rolled_back_feature_puts_the_body_back_in_work():
    from catia_mcp.tools.part_design import PartDesignTools

    class _Sel:
        def Clear(self):
            pass

        def Add(self, _thing):
            pass

        def Delete(self):
            pass

    class _Part:
        InWorkObject = "Datum_xy_45"

    part = _Part()
    tools = PartDesignTools(connection=None)
    tools.conn = SimpleNamespace(hso=_Sel())
    body = SimpleNamespace(Name="零件几何体")
    assert tools._remove_feature(part, object(), body) is True
    assert part.InWorkObject is body


def test_shell_reports_the_volume_it_added():
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._shell)
    assert "self._volume_probe(part, body)" in source
    assert "Volume {before:.1f} -> {after:.1f} mm3" in source


def test_a_rebuild_in_the_callers_vocabulary_names_the_callers_name():
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._recover_lost_sketch)
    assert "resolves to" in source
    assert "requested" in source

    class _Sketcher:
        @staticmethod
        def rebuild_lost_sketch(part, body, name):
            return "草图.4", None

    tools = PartDesignTools(connection=None)
    tools.conn = SimpleNamespace()
    tools._sketch_rebuild_hint = lambda name, tail=None: tail or ""
    import catia_mcp.tools.sketcher as S
    original = S.SketcherTools
    try:
        S.SketcherTools = lambda conn: _Sketcher()
        text = tools._recover_lost_sketch(None, None, "草图.3", False,
                                         requested="草图.2")
    finally:
        S.SketcherTools = original
    assert text.startswith(" ('草图.2' resolves to '草图.3'")
    assert "re-created from its recorded definition as '草图.4'" in text


def test_the_description_does_not_quote_one_afternoons_seconds():
    from catia_mcp.tools.measurement import MeasurementTools

    description = [d for d in MeasurementTools(connection=None)
                   .get_tool_definitions()
                   if d["name"] == "catia_get_bounding_box"][0]["description"]
    assert "173 s (0.3.31)" in description and "183 s (0.3.33)" in description
    assert "RANGES, not constants" in description
    assert "173.4 s" not in description
