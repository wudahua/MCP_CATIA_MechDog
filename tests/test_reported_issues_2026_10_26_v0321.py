# -*- coding: utf-8 -*-
"""Regression tests for the 2026-10-26 batch (three reports against 0.3.20).

Both P0s came from the same habit: answering a question that was never measured.

* ``catia_pad`` diagnosed the direction by building a SECOND pad from a sketch
  the failed pad had already consumed. That probe answered "nothing either way"
  for a direction that works, and removing it took the caller's input sketch with
  it - so the retry the message discouraged could not even run.
* ``catia_run_plan`` dropped an assertion whose measurement had failed, which
  turned `expect: {"document": …, "volume_mm3": 1.0}` into ``PLAN_OK``.

The third report is about a label: a part that an open assembly references cannot
be closed, which is ordinary CATIA behaviour, and it was reported as
"CLOSE NOT CONFIRMED".

Measured on B30 the same day: inserting the frame plane's datum points INTO the
body (instead of a geometrical set) makes a fillet on the resulting pad's edge
apply - ``[applied Edge.1@R1]`` - where the set-based tree refused it outright.
"""

from __future__ import annotations

import inspect
import json

import pytest


# --------------------------------------------------------------------------
# 1. pad: flip the same node, never build a second feature, never eat the sketch.
# --------------------------------------------------------------------------

def test_pad_flips_the_same_node_instead_of_building_a_second_pad():
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._pad)
    assert "_pad_other_direction" not in source
    assert "pad.DirectionOrientation = 0 if direction == \"reverse\" else 1" in source
    assert "auto-flipped to reach material" in source


def test_pad_failure_says_whether_the_sketch_survived():
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._pad)
    assert "_sketch_present" in source
    assert "is still in the tree" in source
    assert "did not survive" in source
    # Both directions are measured now, so the claim is a fact, not an assumption.
    assert "Both directions were MEASURED" in source
    assert "not the problem" not in source


def test_sketch_present_finds_a_sketch_in_any_body():
    from catia_mcp.tools.part_design import PartDesignTools

    class _Sketch:
        Name = "草图.3"

    class _Sketches:
        Count = 1

        def Item(self, index):
            return _Sketch()

    class _Body:
        Sketches = _Sketches()

    class _Bodies:
        Count = 2

        def Item(self, index):
            return _Body()

    class _Part:
        Bodies = _Bodies()

    body = _Body()
    body.Parent = _Part()
    tools = PartDesignTools(connection=None)
    assert tools._sketch_present(body, _Sketch()) is True
    missing = type("S", (), {"Name": "草图.9"})()
    assert tools._sketch_present(body, missing) is False


# --------------------------------------------------------------------------
# 2. An assertion that cannot be evaluated is a FAILED assertion.
# --------------------------------------------------------------------------

def _planner():
    from catia_mcp.tools.planner import PlannerTools

    return PlannerTools(connection=None)


def test_an_unmeasurable_volume_is_a_failure_not_an_empty_list():
    checks = _planner()._check({"volume_mm3": 1.0}, {"inertia_error": "boom"})
    assert len(checks) == 1
    assert checks[0]["pass"] is False
    assert checks[0]["actual"] is None
    assert "boom" in checks[0]["unavailable"]


def test_the_failure_line_says_which_document_and_why():
    line = _planner()._assertion_line({
        "name": "volume_mm3", "expected": 1.0, "actual": None,
        "document": "M113_asm", "unavailable": "exposes no Part",
    })
    assert "M113_asm" in line
    assert "could not be measured" in line
    assert "exposes no Part" in line


def test_an_assembly_is_measured_by_activation_not_by_document():
    """get_inertia(document=…) needs a Part; an assembly only aggregates."""
    from catia_mcp.tools.planner import PlannerTools

    source = inspect.getsource(PlannerTools._measure_part)
    assert "_measure_active_product" in source
    assert "_is_product_document" in source

    tools = _planner()
    tools.bind({})
    seen = {}

    class _Doc:
        Type = "ProductDocument"

    tools._named_document = lambda document: _Doc()

    def fake(document):
        seen["document"] = document
        return {"document": document, "volume_mm3": 42.0}

    tools._measure_active_product = fake
    data = tools._measure_part("M113_asm")
    assert seen["document"] == "M113_asm"
    assert data["volume_mm3"] == 42.0


# --------------------------------------------------------------------------
# 3. close_document: a referenced part is normal, not an exception.
# --------------------------------------------------------------------------

class _Doc:
    def __init__(self, name):
        self.Name = name


def _document_tools(names, uses):
    from catia_mcp.tools.document import DocumentTools

    tools = DocumentTools(connection=None)

    class _Conn:
        active_document = _Doc(names[0])

    tools.conn = _Conn()
    tools._document_names = lambda: list(names)
    tools._component_usage = lambda: uses
    return tools


def test_closing_a_referenced_part_is_reported_as_a_state_not_a_failure():
    tools = _document_tools(["P03_Piston.CATPart", "P00_Assembly.CATProduct"],
                            {"P03_Piston.CATPart": ["P00_Assembly.CATProduct"]})

    class _OpenDoc(_Doc):
        def Close(self):
            return None

    tools.conn.active_document = _OpenDoc("P03_Piston.CATPart")
    reply = tools._close_document()
    assert "STILL OPEN (closed: false)" in reply
    assert "not a failure" in reply
    assert "P00_Assembly.CATProduct" in reply
    assert not reply.startswith("CLOSE NOT CONFIRMED")


def test_a_close_that_fails_with_no_referrer_is_still_an_error():
    tools = _document_tools(["Mystery.CATPart"], {})

    class _OpenDoc(_Doc):
        def Close(self):
            return None

    tools.conn.active_document = _OpenDoc("Mystery.CATPart")
    with pytest.raises(RuntimeError) as error:
        tools._close_document()
    assert "CLOSE NOT CONFIRMED" in str(error.value)
    assert "NO open assembly references it" in str(error.value)


# --------------------------------------------------------------------------
# 4. activate_document accepts the spelling every other tool uses.
# --------------------------------------------------------------------------

def test_activate_document_takes_document_and_document_name():
    from catia_mcp.tools.document import DocumentTools

    schema = [d for d in DocumentTools(connection=None).get_tool_definitions()
              if d["name"] == "catia_activate_document"][0]["inputSchema"]
    assert set(schema["properties"]) >= {"name", "document", "document_name"}
    assert "required" not in schema
    branches = {tuple(sorted(branch["required"])) for branch in schema["anyOf"]}
    assert branches == {("name",), ("document",), ("document_name",)}


# --------------------------------------------------------------------------
# 5. Anchors: report every bad one, and let the caller choose what to do.
# --------------------------------------------------------------------------

def test_every_bad_anchor_is_listed_at_once():
    from catia_mcp.tools.part_design import PartDesignTools

    text = PartDesignTools._anchor_trouble_text(
        [{"anchor": [999.0, 999.0, 999.0], "nearest_edge": "Edge.3",
          "nearest_length_mm": 40.0, "distance_mm": 1690.0423},
         {"anchor": [888.0, 0.0, 0.0], "nearest_edge": "Edge.8",
          "nearest_length_mm": 20.0, "distance_mm": 848.0}],
        [{"anchor": [0.0, 0.0, 10.0], "candidates": ["Edge.1", "Edge.9"],
          "distance_mm": 0.0}],
        0.5,
    )
    assert "2 anchor(s) beyond anchor_tol=0.5" in text
    assert "Edge.3" in text and "Edge.8" in text
    assert "1 ambiguous anchor(s)" in text
    assert "Edge.1, Edge.9" in text


def test_on_anchor_miss_has_both_modes_in_the_schema():
    from catia_mcp.tools.part_design import PartDesignTools

    for name in ("catia_fillet", "catia_chamfer"):
        schema = [d for d in PartDesignTools(connection=None).get_tool_definitions()
                  if d["name"] == name][0]["inputSchema"]
        assert schema["properties"]["on_anchor_miss"]["enum"] == ["fail", "skip"]
        assert schema["properties"]["on_anchor_miss"]["default"] == "fail"


# --------------------------------------------------------------------------
# 6. Fillet refusals name the cause that can be read from the fingerprint.
# --------------------------------------------------------------------------

def test_a_refusal_beside_an_existing_fillet_says_so():
    from catia_mcp.tools.part_design import PartDesignTools

    text = PartDesignTools._rejection_reason(
        ["Edge.34 (L45.0000|Pad.1;2|Fillet.1;2)"])
    assert "existing fillet or chamfer" in text
    assert "ONE call" in text


def test_a_refusal_without_a_fillet_does_not_blame_a_gsd_surface():
    from catia_mcp.tools.part_design import PartDesignTools

    text = PartDesignTools._rejection_reason(
        ["Edge.15 (L45.0000|Pad.1;2|Sketch.1;1)"])
    assert "topology" in text
    assert "CloseSurface" not in text
    assert "gsd_close_surface" not in text


# --------------------------------------------------------------------------
# 7. Frame sketches live in the body - points included.
# --------------------------------------------------------------------------

def test_the_frame_plane_puts_its_points_in_the_body():
    from catia_mcp.tools.sketcher import SketcherTools

    source = inspect.getsource(SketcherTools._frame_plane)
    assert "body.InsertHybridShape(point)" in source
    # A geometrical set poisons every feature built on the sketch (measured).
    assert "HybridBodies.Add()" not in source
    assert "InsertHybridShape(plane_feature)" in source


# --------------------------------------------------------------------------
# 8. Overlapping closed loops are named at close time.
# --------------------------------------------------------------------------

def _sketches():
    from catia_mcp.tools.sketcher import SketcherTools

    tools = SketcherTools(connection=None)
    tools._active_sketch = type("S", (), {"Name": "Sketch.1"})()
    return tools


def test_two_overlapping_rectangles_are_reported():
    tools = _sketches()
    tools._note_element("rectangle", x1=0, y1=0, x2=30, y2=20)
    tools._note_element("rectangle", x1=5, y1=5, x2=25, y2=15)
    warning = tools._loop_warning("Sketch.1")
    assert "2 closed loops" in warning
    assert "overlap" in warning
    assert "600.0 mm2" in warning and "200.0 mm2" in warning


def test_two_separate_rectangles_are_a_legitimate_profile():
    tools = _sketches()
    tools._note_element("rectangle", x1=0, y1=0, x2=10, y2=10)
    tools._note_element("rectangle", x1=50, y1=0, x2=60, y2=10)
    assert tools._loop_warning("Sketch.1") == ""


# --------------------------------------------------------------------------
# 9. shell: a purely outward wall is expressible, and described as such.
# --------------------------------------------------------------------------

def test_shell_accepts_zero_thickness_when_the_wall_is_outward():
    from catia_mcp.tools.part_design import PartDesignTools

    schema = [d for d in PartDesignTools(connection=None).get_tool_definitions()
              if d["name"] == "catia_shell"][0]["inputSchema"]
    assert schema["properties"]["thickness"]["minimum"] == 0
    assert "OUTWARD" in schema["properties"]["thickness"]["description"]


def test_the_shell_reply_names_the_side_the_wall_grew_on():
    from catia_mcp.tools.part_design import PartDesignTools

    assert "OUTWARD" in PartDesignTools._shell_wall_text(0, 5)
    assert "0.0 mm wall" not in PartDesignTools._shell_wall_text(0, 5)
    assert "inward + 2.5 mm outward" in PartDesignTools._shell_wall_text(2.5, 2.5)
    assert "inward wall" in PartDesignTools._shell_wall_text(5, 0)


# --------------------------------------------------------------------------
# 10. tensor_reason carries a one-word code beside the sentence.
# --------------------------------------------------------------------------

def test_tensor_reason_has_a_code():
    from catia_mcp.tools import measurement

    source = inspect.getsource(measurement.MeasurementTools._part_inertia)
    assert 'payload["tensor_reason_code"] = "no_own_window"' in source


# --------------------------------------------------------------------------
# 11. A plan step that uses an alias still validates.
# --------------------------------------------------------------------------

def test_bad_args_understands_anyof_required_lists():
    from catia_mcp.tools.document import DocumentTools

    from catia_mcp.tools.planner import PlannerTools

    planner = PlannerTools(connection=None)
    planner.bind({"catia_activate_document": DocumentTools(connection=None)})
    result = planner._run_plan({"steps": [{"tool": "catia_activate_document",
                                          "args": {}}]})
    assert result["code"] == "BAD_ARGS"
    detail = result["data"]["details"][0]
    assert detail["missing"] == ["name"]
    assert detail["schema"]["required"] == ["name"]
    assert detail["schema"]["alternatives"] == [["name"], ["document"],
                                                ["document_name"]]
