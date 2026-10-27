# -*- coding: utf-8 -*-
"""Regression tests for the 2026-10-27 batch: two reports on 0.3.26.

The one that matters is the parameter contract: `component=` was accepted and
IGNORED whenever the active document was a Part, so a loop over instances returned
N identical, plausible, wrong numbers - no error, no warning, not even the name in
the reply. The tool refuses `method='mesh'` in Product mode with the words "a wrong
number that looks plausible is worse than an error"; this was the same sin with a
quieter face.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest


class _Doc:
    def __init__(self, name, kind="Part"):
        self.Name = name
        self.FullName = name
        self.Type = kind
        self.Part = object() if kind == "Part" else None


def _box_tools(open_docs, holds, active="P26A.CATPart"):
    """A session where `holds` maps assembly name -> component names it contains."""
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    docs = open_docs

    class _Documents:
        Count = len(docs)

        @staticmethod
        def Item(index):
            return docs[index - 1]

    tools.conn = SimpleNamespace(
        ensure_connected=lambda: None,
        documents=_Documents(),
        active_document=_Doc(active),
    )

    def components_doc(doc, only=None):
        names = holds.get(str(doc.Name), [])
        if only is None:
            return [{"index": i + 1, "name": name, "part_number": name,
                     "part": object(), "matrix": None, "translation": None,
                     "problems": []} for i, name in enumerate(names)]
        return [{"index": i + 1, "name": name, "part_number": name,
                 "part": object(), "matrix": None, "translation": None,
                 "problems": []}
                for i, name in enumerate(names) if name == only]

    tools._components_doc = components_doc
    tools._document_kind = lambda: "part"

    def assembly_box(doc, method, only, include_curved):
        return json.dumps({"document": str(doc.Name), "component": only,
                           "dimensions": {"length_x": 1.0}, "complete": True})

    tools._assembly_box = assembly_box
    return tools


# --------------------------------------------------------------------------
# 1. component= must never be silently ignored.
# --------------------------------------------------------------------------

def test_a_component_is_looked_up_in_the_open_assembly_when_a_part_is_active():
    tools = _box_tools(
        [_Doc("P26A.CATPart"), _Doc("ASM26.CATProduct", "Product")],
        holds={"ASM26.CATProduct": ["P26A.1", "P26B.1", "P26B.2"]})
    reply = json.loads(tools._get_bounding_box(component="P26B.1"))
    assert reply["document"] == "ASM26.CATProduct"
    assert reply["component"] == "P26B.1"
    # The caller has to be told the measurement did not happen where they pointed.
    assert "ASM26.CATProduct" in reply["measured_in"]
    assert "active document is not an assembly" in reply["measured_in"]


def test_two_assemblies_holding_the_component_are_named_not_guessed():
    tools = _box_tools(
        [_Doc("P26A.CATPart"), _Doc("ASM26.CATProduct", "Product"),
         _Doc("ASM27.CATProduct", "Product")],
        holds={"ASM26.CATProduct": ["P26B.1"], "ASM27.CATProduct": ["P26B.1"]})
    with pytest.raises(RuntimeError) as error:
        tools._get_bounding_box(component="P26B.1")
    text = str(error.value)
    assert "more than one open assembly" in text
    assert "ASM26.CATProduct" in text and "ASM27.CATProduct" in text
    assert "document='<assembly>.CATProduct'" in text


def test_an_unknown_component_says_it_was_not_measured():
    tools = _box_tools([_Doc("P26A.CATPart")], holds={})
    with pytest.raises(RuntimeError) as error:
        tools._get_bounding_box(component="P26B.9")
    text = str(error.value)
    assert "was NOT measured" in text
    assert "P26A.CATPart" in text          # names the document it would have used
    assert "catia_activate_document" in text


def test_mesh_plus_component_still_refuses():
    """The refusal that already existed must not slip through the new route."""
    tools = _box_tools(
        [_Doc("P26A.CATPart"), _Doc("ASM26.CATProduct", "Product")],
        holds={"ASM26.CATProduct": ["P26B.1"]})
    with pytest.raises(RuntimeError) as error:
        tools._get_bounding_box(component="P26B.1", method="mesh")
    assert "method='mesh' is not available for an assembly" in str(error.value)


# --------------------------------------------------------------------------
# 2. elapsed_ms on the single-part replies too.
# --------------------------------------------------------------------------

def test_stamp_elapsed_adds_the_field_and_keeps_the_rest():
    import time

    from catia_mcp.tools.measurement import MeasurementTools

    started = time.perf_counter() - 0.05
    stamped = json.loads(MeasurementTools._stamp_elapsed(
        json.dumps({"min": {"x": 0.0}, "points_route": "mesh"}), started))
    assert stamped["points_route"] == "mesh"
    assert stamped["elapsed_ms"] >= 50.0
    assert stamped["min"] == {"x": 0.0}


def test_stamp_elapsed_does_not_overwrite_a_measured_value():
    import time

    from catia_mcp.tools.measurement import MeasurementTools

    stamped = json.loads(MeasurementTools._stamp_elapsed(
        json.dumps({"elapsed_ms": 7.5}), time.perf_counter()))
    assert stamped["elapsed_ms"] == 7.5


def test_every_single_part_return_is_stamped():
    from catia_mcp.tools.measurement import MeasurementTools

    source = __import__("inspect").getsource(MeasurementTools._get_bounding_box)
    # document= path, active-part vertices, active-part mesh
    assert source.count("_stamp_elapsed(reply, started)") == 3


def test_the_mesh_route_names_itself():
    from catia_mcp.tools.measurement import MeasurementTools

    for name in ("_part_bbox", "_get_bounding_box"):
        source = __import__("inspect").getsource(getattr(MeasurementTools, name))
        assert '"points_route": "mesh"' in source


# --------------------------------------------------------------------------
# 3. parts[] carries points_route whichever route the part took.
# --------------------------------------------------------------------------

def _rows(part_name, count):
    class _Part:
        def __init__(self, name):
            self.Name = name
            self.MainBody = object()

    return [{"index": i + 1, "name": f"{part_name} I{i:03d}",
             "part_number": part_name, "part": _Part(part_name),
             "matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
             "translation": [0.0, 0.0, 0.0], "problems": []}
            for i in range(count)]


def test_parts_publish_points_route_for_both_routes():
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools._components_doc = lambda doc, only=None: _rows("dish", 2) + _rows("plate", 1)
    tools._part_identity = lambda part: part.Name
    tools._to_world = lambda matrix, translation, point: list(point)

    def local_box(part, body, stats=None):
        if part.Name == "dish":
            return [], 0
        if stats is not None:
            stats["cells"] = 12
            stats["points_route"] = "batch"
        return [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]], 8

    tools._local_box = local_box
    tools._mesh_cloud = lambda part: ([0.0, 0.0, 0.0, 2.0, 2.0, 2.0], 6, "")

    payload = json.loads(tools._assembly_box(SimpleNamespace(Name="A.CATProduct"),
                                             "vertices"))
    routes = {p["part"]: p.get("points_route") for p in payload["parts"]}
    assert routes == {"dish": "mesh", "plate": "batched"}
    for detail in payload["parts"]:
        assert "points_route" in detail


# --------------------------------------------------------------------------
# 4. run_plan: one grammar for failures, and skipped assertions say so.
# --------------------------------------------------------------------------

def _plan(failing: dict[int, str], total: int, *, stop_on_error=False,
          expect=None):
    from catia_mcp.tools.planner import PlannerTools

    router: dict[str, Any] = {}
    steps = []
    for index in range(1, total + 1):
        name = f"catia_step{index}"

        def run(tool, args, _index=index):
            if _index in failing:
                return {"ok": False, "code": "STEP_BROKE",
                        "message": failing[_index]}
            return {"ok": True, "code": "OK", "message": f"step {_index} built"}

        router[name] = SimpleNamespace(execute=run)
        steps.append({"tool": name})

    tools = PlannerTools(connection=None)
    tools.bind(router)
    tools._measure_part = lambda: {"volume_mm3": 1.0}
    arguments: dict[str, Any] = {"steps": steps,
                                 "stop_on_error": stop_on_error,
                                 "measure": True}
    if expect:
        arguments["expect"] = expect
    return tools._run_plan(arguments), tools


def test_a_single_failure_uses_the_count_form_too():
    result, _ = _plan({6: "nope"}, 8)
    assert result["message"].startswith("1 of 8 steps failed: ")
    assert "step 6 (catia_step6)" in result["message"]


def test_a_skipped_expect_is_not_silent():
    result, _ = _plan({3: "nope"}, 5, expect=[{"document": "X.CATPart",
                                              "volume_mm3": 1.0}])
    assert result["ok"] is False
    data = result["data"]
    assert data["measurements"] == {}
    assert data["assertions"] == []
    assert data["assertions_status"] == "skipped"
    assert "1 step(s) failed" in data["assertions_skipped_because"]
    assert "assertions not evaluated" in result["message"]


def test_a_plan_without_expect_does_not_claim_assertions_were_skipped():
    result, _ = _plan({2: "nope"}, 4)
    assert "assertions_status" not in result["data"]
