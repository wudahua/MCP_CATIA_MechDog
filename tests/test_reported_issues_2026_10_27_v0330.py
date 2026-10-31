# -*- coding: utf-8 -*-
"""Regression tests for the 2026-10-27 evening batch: two reports on 0.3.29.

The serious one is a lie inside a failure message. `catia_pad` answered "Your
sketch '草图.7' is still in the tree - only the pad node was removed" while CATIA
had deleted the sketch together with the feature it was consumed by (measured on
B30: 5 of 5 rollbacks lost it, 3 of 5 claimed otherwise). The check that was
supposed to catch this had been answering True for every input: it walked
``body.Parent.Bodies``, a Body has no Parent in the V5 model, and the fallback
returned "cannot tell, do not accuse" - as True.

The rest are the same shape one step out: `component=` narrowing called a partial
box complete, the bounding-box calibration was quoted from cold-cache numbers only,
`nls_chars` was published with a causal claim its own data refutes, and a failed
plan reported no final measurement even when every later step ran.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest


# ─────────────────────────── 1. the sketch really goes with the rollback
def test_sketch_present_reads_the_part_and_admits_when_it_cannot_tell():
    from catia_mcp.tools.part_design import PartDesignTools

    class _Sketch:
        Name = "草图.3"

    class _Sketches:
        Count = 1

        def Item(self, index):
            return _Sketch()

    class _Bodies:
        Count = 1

        def Item(self, index):
            return SimpleNamespace(Sketches=_Sketches())

    tools = PartDesignTools(connection=None)
    assert tools._sketch_present(SimpleNamespace(Bodies=_Bodies()), _Sketch()) is True
    assert tools._sketch_present(SimpleNamespace(Bodies=_Bodies()),
                                 SimpleNamespace(Name="草图.9")) is False
    # No Part.Bodies at all: say "unknown", never "it is fine".
    assert tools._sketch_present(object(), _Sketch()) is None


def test_the_rollback_sentence_follows_what_is_actually_in_the_tree():
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    tools.conn = SimpleNamespace(active_document=SimpleNamespace(Name="P.CATPart"),
                                 sketch_outlines={})
    s = inspect.getsource(PartDesignTools._sketch_after_rollback)
    assert "is still in the tree" in s
    assert "removed the sketch" in s
    assert "could not be read back" in s

    assert "is still in the tree" in tools._sketch_after_rollback("草图.3", True)
    gone = tools._sketch_after_rollback("草图.3", False)
    assert "removed the sketch" in gone
    unknown = tools._sketch_after_rollback("草图.3", None)
    assert "could not be read back" in unknown


def test_the_lost_sketch_comes_back_with_its_profile():
    """A rollback that takes the sketch has to leave something to rebuild it."""
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    tools.conn = SimpleNamespace(
        active_document=SimpleNamespace(Name="P.CATPart"),
        sketch_outlines={"P.CATPart::草图.7": {
            "placement": {"base": "xy", "distance": 20.0},
            "elements": [{"kind": "rectangle", "x1": 30.0, "y1": 15.0,
                          "x2": 35.0, "y2": 25.0}],
        }})
    said = tools._sketch_after_rollback("草图.7", False)
    assert "It held:" in said
    assert "rectangle (30, 15) .. (35, 25)" in said
    assert "plane 'xy'" in said and "offset 20" in said
    assert "rebuilds it" in said


def test_pocket_and_hole_say_it_too():
    from catia_mcp.tools.part_design import PartDesignTools

    for name in ("_pocket", "_hole"):
        source = inspect.getsource(getattr(PartDesignTools, name))
        assert "_sketch_present" in source, name
        assert "_recover_lost_sketch" in source, name


# ──────────────────── 2. component= narrowing is not "complete"
class _Doc:
    def __init__(self, name: str):
        self.Name = name
        self.Type = "ProductDocument"


class _Part:
    def __init__(self, name: str):
        self.Name = name
        self.MainBody = SimpleNamespace(Name="PartBody",
                                        Shapes=SimpleNamespace(Count=1))


def _row(name, part=None, kind="part", nested=None):
    return {"index": 1, "name": name, "part_number": name.split(".")[0],
            "matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            "translation": [0.0, 0.0, 0.0], "part": part,
            "nested_doc": nested, "kind": kind, "problems": []}


def _narrowed_tools():
    from catia_mcp.tools.measurement import MeasurementTools

    plate = _Part("P26A")
    sub = _Doc("ASM27B.CATProduct")
    tools = MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None,
                                 active_document=None)
    rows = {
        "ASM27D.CATProduct": [
            _row("P26A.1", part=plate),
            _row("ASM27B.1", kind="sub_assembly", nested=sub),
        ],
        # The same part, installed inside the sub-assembly.
        "ASM27B.CATProduct": [_row("P26A.1", part=plate)],
    }
    tools._components_doc = lambda doc, only=None: (
        [r for r in rows.get(str(doc.Name), [])
         if only is None or r["name"] == only or r["part_number"] == only])
    tools._part_box_entry = lambda part, body, include_curved: {
        "points": [[-50.0, -30.0, 0.0], [50.0, 30.0, 10.0]],
        "used": 2, "source": "vertices", "mesh_vertices": None,
        "copy_note": "", "cells": 12, "nls_chars": 1200}
    tools._part_identity = lambda part: part.Name
    return tools


def test_a_narrowed_box_says_that_the_same_part_is_also_nested():
    tools = _narrowed_tools()
    reply = json.loads(tools._assembly_box(_Doc("ASM27D.CATProduct"), "vertices",
                                           "P26A.1"))
    # The top-level instance is measured correctly...
    assert reply["min"] == {"x": -50.0, "y": -30.0, "z": 0.0}
    # ...but it is NOT the whole story, and the reply must not pretend it is.
    assert reply["complete"] is False
    assert reply["scope"] == "top_level_only"
    assert reply["also_nested"] == [{"path": "ASM27B.1/P26A.1", "depth": 2}]
    assert reply["instances"] == {"total": 2, "measured": 1, "skipped": 1}
    assert reply["skipped"][0]["path"] == "ASM27B.1/P26A.1"
    assert reply["skipped"][0]["depth"] == 2
    assert "narrows to TOP-LEVEL" in reply["skipped"][0]["error"]
    assert "PARTIAL" not in reply["incomplete_because"]
    assert "NOT in this box" in reply["incomplete_because"]
    # The three statements of one reply must not contradict each other.
    assert "narrowed to the TOP-LEVEL" in reply["note_scope"]
    assert "EXPANDED" not in reply["note_scope"]


def test_a_narrowed_box_with_no_nested_twin_stays_complete():
    tools = _narrowed_tools()
    reply = json.loads(tools._assembly_box(_Doc("ASM27D.CATProduct"), "vertices",
                                           "ASM27B.1"))
    assert reply["complete"] is True
    assert "also_nested" not in reply
    assert "none exist" in reply["note_scope"]


def test_the_unnarrowed_reply_keeps_saying_sub_assemblies_are_expanded():
    tools = _narrowed_tools()
    reply = json.loads(tools._assembly_box(_Doc("ASM27D.CATProduct"), "vertices"))
    assert "EXPANDED" in reply["note_scope"]
    assert "scope" not in reply
    assert reply["complete"] is True
    assert reply["assembly_depth_max"] == 2


# ─────────────────── 3. cold and warm are different measurements
def test_the_box_says_which_regime_it_ran_in():
    from catia_mcp.tools import measurement as M

    M.reset_session_books()
    tools = _narrowed_tools()
    first = json.loads(tools._assembly_box(_Doc("ASM27D.CATProduct"), "vertices"))
    # 0.3.39 renamed `cold_parts` to `session_first_parts`: the old name asserted
    # a cause ("these parts were cold") that the count never measured - 26 of 28
    # parts in the 2026-10-29 run were no slower on their second reading.
    assert first["regime"] == "cold"
    assert first["session_first_parts"] == 1
    assert first["parts_total"] == 1
    assert first["part_boxes_total"] == 1
    assert "session_first_parts" in first["elapsed_note"]
    assert first["parts"][0]["first_measure"] is True
    assert first["parts"][0]["cold_reason"] == "topology"

    second = json.loads(tools._assembly_box(_Doc("ASM27D.CATProduct"), "vertices"))
    assert second["regime"] == "warm"
    assert second["session_first_parts"] == 0
    assert "warm number" in second["elapsed_note"]
    assert second["parts"][0]["first_measure"] is False
    M.reset_session_books()


def test_the_description_quotes_both_regimes_not_only_the_cold_one():
    from catia_mcp.tools.measurement import MeasurementTools

    description = [d for d in MeasurementTools(connection=None)
                   .get_tool_definitions()
                   if d["name"] == "catia_get_bounding_box"][0]["description"]
    # 0.3.34: a RANGE instead of the number one afternoon produced. The report
    # that closed this one measured 3.8-4.8 ms/cell on its own machine, so a
    # single figure here had already stopped describing the product.
    assert "3-5 ms per topology cell" in description
    assert "9.5 ms" in description  # the 0.3.31 figure, kept as the comparison
    assert "16-90 ms per cell" in description
    assert "RANGES, not constants" in description
    # 0.3.43: this line used to assert `cold_parts` - a field renamed to
    # session_first_parts in 0.3.39. The test therefore PINNED the ghost name in
    # place: 0.3.39 changed the field and the description, then this assertion
    # quietly required the description to keep naming a key that no longer
    # exists. That is why the ghost survived three versions (reported
    # 2026-10-31). A test that quotes a field name has to quote the live one.
    assert "first_measure" in description and "session_first_parts" in description


# ─────────────────── 4. nls_chars is a size, not a cost
def test_nls_chars_note_no_longer_claims_to_explain_the_time():
    from catia_mcp.tools import measurement as M

    # 0.3.31 moved the sentence onto one module constant so both routes publish
    # the same words - the single-part reply used to carry the number alone.
    source = M._NLS_NOTE
    assert "not a cost predictor" in source or "not how long" in source
    # The exact sentence that was withdrawn (it survives only inside the comment
    # that records WHY it was withdrawn).
    assert "that is the filter's whole per-cell cost" not in source
    # The counter-evidence travels with the field.
    assert "5.49x" in source and "0.34" in source


# ─────────────────── 5. a step error a script can branch on
def test_step_errors_have_a_code_and_no_class_name_in_front():
    from catia_mcp.tools.planner import PlannerTools, _failure_code

    assert _failure_code("No sketch named '草图.9' in the active body. "
                         "Available: 草图.1.") == "SKETCH_NOT_FOUND"
    assert _failure_code("CATIA built no material for this pad in EITHER "
                         "direction, so it was rolled back.") == "NO_MATERIAL"
    assert _failure_code("The pocket removed no material in either direction") \
        == "NO_MATERIAL"
    assert _failure_code("UNSUPPORTED_CAPABILITY: no such API") \
        == "UNSUPPORTED_CAPABILITY"
    assert _failure_code("something else entirely") == "STEP_EXCEPTION"

    router = {"catia_boom": SimpleNamespace(
        execute=lambda tool, args: (_ for _ in ()).throw(RuntimeError("boom"))),
        "catia_ok": SimpleNamespace(
            execute=lambda tool, args: "fine")}
    tools = PlannerTools(connection=None)
    tools.bind(router)
    tools._measure_part = lambda: {}
    result = tools._run_plan({"steps": [{"tool": "catia_boom"}, {"tool": "catia_ok"}],
                              "stop_on_error": False, "measure": False})
    step = result["data"]["steps"][0]
    assert step["error"] == "boom"                  # no "RuntimeError: " prefix
    assert step["error_type"] == "RuntimeError"     # ...but the class is kept
    assert step["code"] == "STEP_EXCEPTION"


# ─────────────────── 6. list_features shows the sketches
def test_list_features_includes_sketches_without_folding_them_into_count():
    from catia_mcp.tools.part_design import PartDesignTools

    class _Sketches:
        Count = 2

        def Item(self, index):
            return SimpleNamespace(Name=f"草图.{index}")

    class _Shapes:
        Count = 1

        def Item(self, index):
            return SimpleNamespace(Name="凸台.1", Type="Pad")

    body = SimpleNamespace(Name="PartBody", Shapes=_Shapes(),
                           Sketches=_Sketches())
    part = SimpleNamespace(Name="P", Bodies=SimpleNamespace(Count=1),
                           HybridBodies=SimpleNamespace(Count=0))
    tools = PartDesignTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None,
                                 get_active_part=lambda: part,
                                 get_active_part_body=lambda: body)
    reply = json.loads(tools._list_features())
    assert reply["count"] == 1
    assert reply["sketch_count"] == 2
    assert [s["name"] for s in reply["sketches"]] == ["草图.1", "草图.2"]
    assert "not counted in 'count'" in reply["sketches_note"]


# ─────────────────── 7. inertia publishes its cost on every route
def test_part_inertia_now_reports_elapsed_ms_like_the_assembly_route():
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None, app=None)
    tools._resolve_open_document = lambda name: SimpleNamespace(Name=name,
                                                                Type="PartDocument")
    tools._is_product_document = lambda doc: False
    tools.conn.part_of = lambda doc: object()
    tools._part_inertia = lambda part, density, label: json.dumps(
        {"document": label, "volume_mm3": 1000.0})
    reply = json.loads(tools._get_inertia(None, "P.CATPart"))
    assert reply["volume_mm3"] == 1000.0
    assert isinstance(reply["elapsed_ms"], float)
