# -*- coding: utf-8 -*-
"""Regression tests for the 2026-10-26 (last) batch: three reports on 0.3.22.

The three reports agree that every headline item landed, and disagree with
nothing - what is left is a handful of places where the REPLY was thinner than
the data behind it:

* ``run_plan{stop_on_error: false}`` marked every failed step in ``data.steps``
  but the top-level ``message`` named only the last one, so a 5-step plan with
  two failures read as one failure in any log that keeps only ``message``.
* ``list_components{names_only: true}`` still put ``convention: "matrix"`` on
  every entry, next to a rotation that was deliberately not read.
* ``measure_topology``'s ``by_reason`` produced ``construction_borderfvertex``
  for CATIA's ``Selection_BorderFVertex`` - CATIA's letters, our missing
  separators, read by the reporter as a typo.
* ``catia_new_part(name=…, file_path=…)`` did not say that ``expect.document``
  matches the FILE name, and two round trips went into discovering it.
* ``close_document(closed: false)`` did not say where the focus went, although
  "which document will the next tool act on" stopped being answerable from the
  reply.
"""

from __future__ import annotations

import json
import inspect
from types import SimpleNamespace
from typing import Any

import pytest


# --------------------------------------------------------------------------
# 1. A plan that kept going names every failed step.
# --------------------------------------------------------------------------

def _plan_with(failures: dict[int, str], total: int, stop_on_error: bool):
    """Run a ``total``-step plan whose steps in ``failures`` return ok=False."""
    from catia_mcp.tools.planner import PlannerTools

    seen: list[int] = []

    def make(index: int):
        def run(tool, args):
            seen.append(index)
            if index in failures:
                return {"ok": False, "code": "PAD_FAILED",
                        "message": failures[index]}
            return {"ok": True, "code": "OK", "message": f"step {index} built"}
        return run

    router: dict[str, Any] = {}
    steps = []
    for index in range(1, total + 1):
        name = f"catia_step{index}"
        router[name] = SimpleNamespace(execute=make(index))
        steps.append({"tool": name})
    tools = PlannerTools(connection=None)
    tools.bind(router)
    return tools._run_plan({"steps": steps, "stop_on_error": stop_on_error,
                            "measure": False}), seen


def test_a_plan_that_kept_going_counts_every_failed_step():
    result, seen = _plan_with({4: "pad would not build", 5: "pad would not build"},
                              total=5, stop_on_error=False)
    assert result["ok"] is False
    assert result["code"] == "STEP_FAILED"
    assert result["message"] == ("2 of 5 steps failed: step 4 (catia_step4), "
                                 "step 5 (catia_step5)")
    # ... and the runs really did continue after the first failure.
    assert seen == [1, 2, 3, 4, 5]
    assert [s["ok"] for s in result["data"]["steps"]] == [True, True, True,
                                                          False, False]


def test_three_failures_are_all_named():
    result, _ = _plan_with({2: "x", 4: "y", 5: "z"}, total=5,
                           stop_on_error=False)
    assert result["message"].startswith("3 of 5 steps failed: ")
    for step in ("step 2 (catia_step2)", "step 4 (catia_step4)",
                 "step 5 (catia_step5)"):
        assert step in result["message"]


def test_a_single_failure_uses_the_same_count_form():
    """One grammar, not two: a script that parses this message should not have to
    branch on how many steps failed (reported 2026-10-27 - 2-of-15 read as
    "2 of 15 steps failed", 1-of-8 as "Step 6 (…) failed")."""
    result, _ = _plan_with({3: "nope"}, total=4, stop_on_error=False)
    assert result["message"] == "1 of 4 steps failed: step 3 (catia_step3)"


def test_a_stopping_plan_names_the_step_that_stopped_it():
    """The stopping form carries the total and what never ran: "Step 2 failed"
    alone cannot tell a 2-step plan from a 60-step one (asked for 2026-10-27)."""
    result, seen = _plan_with({2: "nope", 4: "never reached"}, total=5,
                              stop_on_error=True)
    assert result["message"] == (
        "1 of 5 planned steps failed at step 2 (catia_step2); "
        "3 step(s) were not run")
    assert seen == [1, 2]


# --------------------------------------------------------------------------
# 2. names_only does not name a matrix it did not read.
# --------------------------------------------------------------------------

class _Placement:
    """A Position whose every read is counted - names_only must not touch it."""

    def __init__(self):
        self.reads = 0

    def __call__(self):  # pragma: no cover - defensive
        self.reads += 1
        return [1.0] * 12


class _Comp:
    def __init__(self, name, part_number, placement):
        self.Name = name
        self.PartNumber = part_number
        self._placement = placement

    @property
    def Position(self):
        self._placement.reads += 1
        return [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 10.0, 20.0, 30.0]

    @property
    def ReferenceProduct(self):
        return SimpleNamespace(Parent=object())


class _Products:
    def __init__(self, comps):
        self._comps = comps
        self.Count = len(comps)

    def Item(self, index):
        return self._comps[index - 1]


def _assembly_tools(*comps):
    from catia_mcp.tools.assembly import AssemblyTools

    tools = AssemblyTools(connection=None)
    tools.conn = SimpleNamespace(
        ensure_connected=lambda: None,
        get_active_product=lambda: SimpleNamespace(Products=_Products(list(comps))),
        app=None,
    )
    return tools


def test_names_only_omits_the_rotation_convention():
    placement = _Placement()
    tools = _assembly_tools(_Comp("P03_prop.1", "P03_prop", placement),
                            _Comp("P03_prop.2", "P03_prop", placement))
    payload = json.loads(tools._list_components(names_only=True))

    assert "convention" not in payload
    assert all("convention" not in entry for entry in payload["components"])
    assert "no position, no rotation" in payload["read"]
    # The claim is only worth anything if the placement was really not read.
    assert placement.reads == 0
    assert payload["count"] == 2
    assert [e["occurrence"] for e in payload["components"]] == [1, 2]


def test_the_full_listing_still_labels_the_convention():
    placement = _Placement()
    tools = _assembly_tools(_Comp("P03_prop.1", "P03_prop", placement))
    tools._position_components = lambda position: [1.0, 0.0, 0.0,
                                                   0.0, 1.0, 0.0,
                                                   0.0, 0.0, 1.0,
                                                   10.0, 20.0, 30.0]
    payload = json.loads(tools._list_components())

    assert payload["convention"] == "matrix"
    assert payload["components"][0]["convention"] == "matrix"
    assert payload["components"][0]["position"] == {"x": 10.0, "y": 20.0,
                                                    "z": 30.0}
    assert placement.reads == 1


def test_names_only_is_documented_as_dropping_the_convention():
    from catia_mcp.tools.assembly import AssemblyTools

    schema = [d for d in AssemblyTools(connection=None).get_tool_definitions()
              if d["name"] == "catia_list_components"][0]["inputSchema"]
    assert "'convention'" in schema["properties"]["names_only"]["description"]


# --------------------------------------------------------------------------
# 3. by_reason keeps CATIA's letters and adds the separators back.
# --------------------------------------------------------------------------

def test_a_border_free_vertex_reason_is_separated_not_run_together():
    from catia_mcp.topo_search import construction_reason

    reason = construction_reason(
        "Selection_BorderFVertex:(Vertex:(Brp:(GSMPlane.1;0);None:();Cf12:()))")
    assert reason == "construction_border_f_vertex"
    # The string a reporter read as a typo.
    assert reason != "construction_borderfvertex"


def test_the_named_reasons_did_not_move():
    """Callers diff by_reason; only the fall-through name may change."""
    from catia_mcp.topo_search import construction_reason

    assert construction_reason("Selection_BorderREdge:(Edge:(Brp:())") \
        == "datum_plane_border_edge"
    assert construction_reason("Selection_WireREdge:(Wire:(Brp:())") \
        == "sketch_wire_edge"
    assert construction_reason("Selection_BorderRSur:(Surface:(Brp:())") \
        == "datum_plane_border_face"
    assert construction_reason("Selection_WireFVertex:(Vertex:(Wire:()))") \
        == "sketch_wire_vertex"
    assert construction_reason("Selection_BorderRVertex:(Vertex:(Brp:())") \
        == "datum_plane_corner"


def test_the_snake_helper_handles_a_run_of_capitals():
    from catia_mcp.topo_search import _snake_token

    assert _snake_token("BorderFVertex") == "border_f_vertex"
    assert _snake_token("BorderREdge") == "border_r_edge"
    assert _snake_token("Wire") == "wire"


# --------------------------------------------------------------------------
# 2b. The mesh fallback is labelled as one, once per part.
# --------------------------------------------------------------------------

class _BoxPart:
    def __init__(self, name):
        self.MainBody = object()
        self.Name = name


def _assembly_box_tools(rows, curved):
    """An assembly where the parts in ``curved`` have no vertex at an extremum."""
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools._components_doc = lambda doc, only=None: rows
    tools._to_world = lambda matrix, translation, point: list(point)
    tools._part_identity = lambda part: part.Name

    def local_box(part, body, stats=None):
        if part.Name in curved:
            return [], 0
        return [[0.0, 0.0, 0.0], [1.0, 2.0, 3.0]], 8

    tools._local_box = local_box
    # 0.3.24: the assembly route takes the vertex CLOUD, not two corners, so that
    # a rotated instance gets the true box of its rotated part.
    tools._mesh_cloud = lambda part: ([0.0, 0.0, 0.0, 4.0, 5.0, 6.0],
                                      4_620, "measured from a COPY")
    return tools


def _row(name, index, part=None):
    return {
        "index": index,
        "name": f"{name} I{index:03d}",
        "part_number": name,
        "part": part or _BoxPart(name),
        "matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        "translation": [0.0, 0.0, 0.0],
        "problems": [],
    }


def test_a_mesh_bounded_part_reports_mesh_vertices_not_a_bogus_count():
    rows = [_row("curved plate", 1), _row("curved plate", 2), _row("block", 3)]
    tools = _assembly_box_tools(rows, curved={"curved plate"})
    payload = json.loads(tools._assembly_box(SimpleNamespace(Name="ASM.CATProduct"),
                                             "vertices"))

    assert payload["complete"] is True
    assert payload["include_curved"] is True
    assert payload["part_boxes"] == 2
    # Part-level list + instance count (0.3.24): `warnings` is per part, and two
    # fields counting different things read as a defect.
    assert payload["mesh_fallback"] == ["curved plate"]
    assert payload["mesh_fallback_instances"] == 2
    assert payload["bounds"].startswith("mixed: 2 instance(s)")

    mesh = [e for e in payload["components"] if e["source"] == "mesh_fallback"]
    assert len(mesh) == 2
    for entry in mesh:
        # 0.3.25: the counts belong to the PART and live in `parts`, once.
        assert "vertices_used" not in entry

    parts = {p["part"]: p for p in payload["parts"]}
    assert parts["curved plate"]["mesh_vertices"] == 4_620
    assert parts["curved plate"]["instances"] == 2
    assert parts["curved plate"]["source_note"].startswith(
        "no vertex lies at this part's extremum")

    straight = [e for e in payload["components"] if e["source"] == "vertices"]
    assert "mesh_vertices" not in straight[0]
    assert parts["block"]["vertices_used"] == 8


def test_the_copy_warning_is_repeated_once_per_part_not_once_per_instance():
    rows = [_row("curved plate", 1), _row("curved plate", 2),
            _row("curved plate", 3)]
    tools = _assembly_box_tools(rows, curved={"curved plate"})
    payload = json.loads(tools._assembly_box(SimpleNamespace(Name="ASM.CATProduct"),
                                             "vertices"))
    assert len(payload["warnings"]) == 1
    assert payload["warnings"][0].startswith("curved plate: ")
    # The note is written to be appended to a sentence; pasted after a label its
    # leading "; " read as a typo ("drone fin capp: ; measured from a copy").
    assert "; ;" not in payload["warnings"][0]
    assert not payload["warnings"][0].split(": ", 1)[1].startswith(";")


def test_without_include_curved_a_curved_part_is_reported_not_guessed():
    rows = [_row("curved plate", 1), _row("block", 2)]
    tools = _assembly_box_tools(rows, curved={"curved plate"})
    tools._mesh_cloud = lambda part: pytest.fail("mesh route must not be taken")
    payload = json.loads(tools._assembly_box(SimpleNamespace(Name="ASM.CATProduct"),
                                             "vertices", include_curved=False))
    assert payload["complete"] is False
    assert "mesh_fallback" not in payload
    assert "include_curved=true" in payload["skipped"][0]["error"]


# --------------------------------------------------------------------------
# 4. new_part says which name a plan asserts on.
# --------------------------------------------------------------------------

class _NewDoc:
    def __init__(self):
        self.Product = SimpleNamespace(PartNumber="")
        self.Name = "_v322_test.CATPart"


class _NewPart:
    Name = "Part1"


def _new_part_tools(path):
    from catia_mcp.tools.document import DocumentTools

    tools = DocumentTools(connection=None)
    doc = _NewDoc()
    tools.conn = SimpleNamespace(
        ensure_connected=lambda: None,
        documents=SimpleNamespace(Add=lambda kind: doc),
        last_sketch="previous",
        active_body="previous",
        part_of=lambda d: _NewPart(),
        refresh_display=lambda: None,
    )
    tools._save_document = lambda p: f"Saved '{p}'"
    return tools


def test_new_part_names_the_file_a_plan_must_assert_on():
    tools = _new_part_tools("D:/work/_v322_test.CATPart")
    reply = tools._new_part(name="V322test", file_path="D:/work/_v322_test.CATPart")

    assert "expect.document" in reply
    assert "'_v322_test.CATPart'" in reply
    # ... and names the part-number the caller would otherwise have tried.
    assert "'V322test'" in reply
    assert "must use the" in reply and "FILE name" in reply


def test_new_part_without_a_path_still_asks_to_save():
    tools = _new_part_tools(None)
    reply = tools._new_part(name="V322test")
    assert "catia_save_document" in reply
    assert "expect.document" not in reply


# --------------------------------------------------------------------------
# 5. close_document says where the focus went.
# --------------------------------------------------------------------------

def test_a_deferred_close_reports_the_new_active_document():
    from catia_mcp.tools.document import DocumentTools

    class _Doc:
        def __init__(self, name):
            self.Name = name

    class _Closable(_Doc):
        def Close(self):
            return None

    part = _Closable("P03_Piston.CATPart")
    assembly = _Doc("P00_Assembly.CATProduct")

    tools = DocumentTools(connection=None)
    tools.conn = SimpleNamespace(active_document=part)
    tools._document_names = lambda: ["P03_Piston.CATPart",
                                     "P00_Assembly.CATProduct"]
    tools._component_usage = lambda: {
        "P03_Piston.CATPart": ["P00_Assembly.CATProduct"]}

    # CATIA keeps the part and hands the focus to the assembly that holds it.
    original_close = part.Close

    def close_then_hand_over():
        tools.conn.active_document = assembly
        return original_close()

    part.Close = close_then_hand_over

    reply = tools._close_document()
    assert "closed: false" in reply
    assert "active_document_now: 'P00_Assembly.CATProduct'" in reply
    assert "moved focus" in reply


def test_the_focus_line_survives_having_no_active_document():
    from catia_mcp.tools.document import DocumentTools

    class _Doc:
        def __init__(self, name):
            self.Name = name

    class _Closable(_Doc):
        def Close(self):
            return None

    part = _Closable("P03_Piston.CATPart")
    tools = DocumentTools(connection=None)
    tools.conn = SimpleNamespace(active_document=part)
    tools._document_names = lambda: ["P03_Piston.CATPart"]
    tools._component_usage = lambda: {"P03_Piston.CATPart": ["ASM.CATProduct"]}

    def close_and_drop_focus():
        class _NoFocus:
            @property
            def active_document(self):
                raise RuntimeError("no active document")

        tools.conn = _NoFocus()
        return None

    part.Close = close_and_drop_focus

    reply = tools._close_document()
    assert "active_document_now: none" in reply


# --------------------------------------------------------------------------
# 6. An activation that did not happen says which cause it can rule out.
# --------------------------------------------------------------------------

class _ActDoc:
    def __init__(self, name):
        self.Name = name

    def Activate(self):
        """Accept the call and change nothing - exactly what CATIA does."""


def _activate_tools(docs, active, captions):
    """A session where Activate() is accepted and ActiveDocument never moves."""
    from catia_mcp.tools.document import DocumentTools

    tools = DocumentTools(connection=None)
    handles = [_ActDoc(name) for name in docs]
    by_name = {d.Name: d for d in handles}

    class _Windows:
        Count = len(captions)

        @staticmethod
        def Item(index):
            return SimpleNamespace(Caption=captions[index - 1])

    class _NoWindows:
        def __init__(self, active):
            self.ActiveDocument = active

        @property
        def Windows(self):
            raise AttributeError("this release does not expose Windows")

    conn = SimpleNamespace(
        ensure_connected=lambda: None,
        documents=SimpleNamespace(Count=len(handles),
                                  Item=lambda i: handles[i - 1]),
        app=SimpleNamespace(ActiveDocument=by_name[active],
                            Windows=_Windows()),
        refresh_display=lambda: None,
    )
    tools.conn = conn
    return tools, conn, lambda: _NoWindows(by_name[active])


def test_a_part_with_a_window_is_not_told_it_has_none():
    tools, conn, _ = _activate_tools(
        ["P09_skin.CATPart", "ASM.CATProduct"], "ASM.CATProduct",
        ["ASM.CATProduct", "P09_skin.CATPart"])
    with pytest.raises(RuntimeError) as error:
        tools._activate_document("P09_skin.CATPart")
    text = str(error.value)
    assert "HAS a window of its own" in text
    assert "has no window of its own" not in text
    assert "no usable window list" not in text


def test_a_component_without_a_window_is_told_so():
    tools, conn, _ = _activate_tools(
        ["P09_skin.CATPart", "ASM.CATProduct"], "ASM.CATProduct",
        ["ASM.CATProduct"])
    with pytest.raises(RuntimeError) as error:
        tools._activate_document("P09_skin.CATPart")
    text = str(error.value)
    assert "has no window of its own" in text
    assert "HAS a window of its own" not in text


def test_an_unreadable_window_list_says_it_cannot_tell():
    tools, conn, no_windows = _activate_tools(
        ["P09_skin.CATPart", "ASM.CATProduct"], "ASM.CATProduct", [])
    conn.app = no_windows()
    assert tools._window_state("P09_skin.CATPart") is None
    with pytest.raises(RuntimeError) as error:
        tools._activate_document("P09_skin.CATPart")
    text = str(error.value)
    assert "no usable window list" in text
    assert "cannot be told apart" in text
    # Both routes stay in the message, since either cause is possible.
    assert "catia_get_inertia(document=" in text
    assert "catia_open_document" in text


def test_a_successful_activation_says_so():
    tools, conn, _ = _activate_tools(["P09_skin.CATPart"], "P09_skin.CATPart",
                                     ["P09_skin.CATPart"])
    assert tools._activate_document("P09_skin") == \
        "Activated document: 'P09_skin.CATPart'"


def test_the_caption_is_the_name_without_the_extension():
    """Measured on B30 2026-10-26: caption 'Part1' for 'Part1.CATPart'."""
    tools, conn, _ = _activate_tools(["Part1.CATPart"], "Part1.CATPart",
                                     ["Part1"])
    assert tools._window_state("Part1.CATPart") is True


def test_a_short_name_is_not_matched_inside_a_longer_caption():
    """'P1' must not be found in 'P10_prop' - that flips the whole diagnosis."""
    tools, conn, _ = _activate_tools(["P1.CATPart", "P10_prop.CATPart"],
                                     "P10_prop.CATPart", ["P10_prop"])
    assert tools._window_state("P1.CATPart") is False
    assert tools._window_state("P10_prop.CATPart") is True


# --------------------------------------------------------------------------
# 7. A missing tensor points at the route that does work.
# --------------------------------------------------------------------------

def test_the_tensor_reason_points_at_the_plan_route():
    from catia_mcp.tools.measurement import MeasurementTools

    source = inspect.getsource(MeasurementTools._part_inertia)
    # Since 0.3.24 the route is a structured field, not a seventh prose line.
    assert '"tensor_partial_route"' in source
    assert '"borrows_activation"' in source
