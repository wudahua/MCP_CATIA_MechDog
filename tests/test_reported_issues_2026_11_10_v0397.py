# -*- coding: utf-8 -*-
"""0.3.97: the five leftovers from the two 2026-11-10 reports on 0.3.94.

Neither report's headline was this list - those were 0.3.95 and 0.3.96. What is
left over is the class of defect both reports kept circling: **a reply that says
something the measurement in front of it does not support.**

* ``catia_sketch_arc``'s schema still described the endpoint form only, so a
  caller reading it would conclude ``direction`` does not apply to the angle
  form - which stopped being true in 0.3.95 (§4.1 + §4.6 of the first report).
* ``catia_gsd_split``'s next_actions still led with "measure a few points of the
  cutter" while its own message body had just measured that combination as
  unsupported (§4.2 of the second report). Advice that contradicts the
  measurement is what cost that report three controlled experiments.
* ``catia_gsd_project`` answered a parallel plane with a bare COM error (§4.5),
  and ``catia_gsd_offset_surface`` could not say which way it went (§4.4).
* ``catia_diagnose`` listed DECLARED members and left the caller to discover
  which of them never update (§3.4, priority P3 of the second report).
"""
from __future__ import annotations

import inspect
import types

import pytest

from catia_mcp.results import CodedError, PreconditionRejected
from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS, DiagnosticsTools
from catia_mcp.tools.gsd import _SPLIT_FAILURE_ACTIONS, GSDTools
from catia_mcp.tools.sketcher import SketcherTools


def _definition(tool_name: str) -> dict:
    tools = SketcherTools(connection=None)
    return next(d for d in tools.get_tool_definitions() if d["name"] == tool_name)


# ── the arc schema says what the code does (both forms) ────────────────


def test_arc_description_names_the_angle_form_rule_and_the_code() -> None:
    description = _definition("catia_sketch_arc")["description"]
    # Before 0.3.97 the description stopped at the endpoint form, so the
    # short-way rule and DEGENERATE_ARC were only discoverable by running it.
    assert "BOTH FORMS HONOUR" in description
    assert "DEGENERATE_ARC" in description
    assert "SHORT way round" in description


def test_arc_direction_is_not_described_as_endpoint_only() -> None:
    properties = _definition("catia_sketch_arc")["inputSchema"]["properties"]
    direction = properties["direction"]["description"]
    assert "BOTH forms" in direction or "both forms" in direction
    # The old wording sent angle-form callers away from the parameter.
    assert "With two endpoints this only matters" not in direction
    major = properties["major"]["description"]
    assert "two angles" in major


# ── the split's advice follows the split's own measurement ─────────────


def test_split_actions_lead_with_the_measured_unsupported_case() -> None:
    first = _SPLIT_FAILURE_ACTIONS[0]
    assert "catia_gsd_project" in first and "CURVED" in first
    # The instruction that cost the report three experiments must not lead.
    assert not first.startswith("Measure a few points")


def test_split_measures_points_only_for_a_plane_patch() -> None:
    point_advice = [a for a in _SPLIT_FAILURE_ACTIONS if "measure a few points" in a
                    or "measuring points" in a]
    assert len(point_advice) == 1
    assert "PLANE patch" in point_advice[0]
    assert _SPLIT_FAILURE_ACTIONS.index(point_advice[0]) == len(_SPLIT_FAILURE_ACTIONS) - 1


def test_split_failure_uses_the_shared_action_list() -> None:
    source = inspect.getsource(GSDTools._split)
    assert "_SPLIT_FAILURE_ACTIONS" in source


# ── a projection onto a parallel plane is named, not left as COM ───────


class _Conn:
    active_body = None
    active_document = types.SimpleNamespace(Name="P.CATPart")

    def ensure_connected(self) -> None:
        return None


def _gsd() -> GSDTools:
    tools = GSDTools(connection=_Conn())
    tools._ref = lambda name: f"ref:{name}"  # type: ignore[method-assign]
    tools._factory = lambda: types.SimpleNamespace(  # type: ignore[method-assign]
        AddNewProject=lambda element, support: "PROJECTION")
    return tools


def test_project_still_reports_success_plainly() -> None:
    """0.3.100: "created" is no longer the whole reply - it carries the measured
    length of what landed, because a projection that keeps 6 % of the element is
    still "created" and every downstream tool then measures the fragment."""
    tools = _gsd()
    tools._finish = lambda shape, name, message: message  # type: ignore[method-assign]
    tools._curve_length_mm = lambda reference: 63.96  # type: ignore[method-assign]
    tools._source_length_mm = lambda name: (63.96, "outline", True)  # type: ignore[method-assign]
    tools._ref = lambda name, *a, **k: None  # type: ignore[method-assign]
    tools._factory = lambda: types.SimpleNamespace(  # type: ignore[method-assign]
        AddNewProject=lambda a, b: types.SimpleNamespace())
    tools.conn = types.SimpleNamespace(
        ensure_connected=lambda: None,
        get_active_part=lambda: types.SimpleNamespace(
            CreateReferenceFromObject=lambda obj: obj))
    reply = tools._project({"element": "草图.8", "support": "yz"})
    assert reply.startswith("Projection of '草图.8' onto 'yz' created.")
    assert "63.960 mm long" in reply
    assert "WARNING" not in reply      # the whole thing landed


def test_project_warns_when_only_part_of_the_element_landed() -> None:
    """The 2026-11-10 bottle case: 63.96 mm outline -> 4.000 mm curve, and the
    reply said only "created". Measured on B30."""
    tools = _gsd()
    tools._finish = lambda shape, name, message: message  # type: ignore[method-assign]
    tools._curve_length_mm = lambda reference: 4.0  # type: ignore[method-assign]
    tools._source_length_mm = lambda name: (63.96, "outline", True)  # type: ignore[method-assign]
    tools._ref = lambda name, *a, **k: None  # type: ignore[method-assign]
    tools._factory = lambda: types.SimpleNamespace(  # type: ignore[method-assign]
        AddNewProject=lambda a, b: types.SimpleNamespace())
    tools.conn = types.SimpleNamespace(
        ensure_connected=lambda: None,
        get_active_part=lambda: types.SimpleNamespace(
            CreateReferenceFromObject=lambda obj: obj))
    reply = tools._project({"element": "草图.45", "support": "Face.13"})
    assert reply.startswith(" WARNING")
    assert "4.000 mm" in reply and "63.960 mm" in reply
    assert "6.3 %" in reply
    assert "MINIMUM" in reply


def test_project_failure_names_both_degenerate_cases() -> None:
    """The first draft blamed only the parallel case; one audit run disproved it.

    The same rectangle failed onto 'yz' and 'zx' as well (measured 2026-11-10 on
    B30), so a message that said "not parallel" was a half-rule - exactly the
    class of reply these reports keep paying for.
    """
    tools = _gsd()

    def refuse(*_args, **_kwargs):
        raise RuntimeError("CATIA rejected the feature (update failed)")

    tools._finish = refuse  # type: ignore[method-assign]
    tools._cutter_gap = lambda a, b: 40.0  # type: ignore[method-assign]
    with pytest.raises(PreconditionRejected) as caught:
        tools._project({"element": "草图.8", "support": "xy"})
    error = caught.value
    assert error.code == "PROJECT_FAILED"
    message = str(error)
    lowered = message.lower()
    # The measurement, both degenerate cases, and the support that did work.
    assert "40.0000 mm" in message
    assert "parallel" in lowered and "perpendicular" in lowered
    assert "45 degrees" in message
    assert "yz" in message and "zx" in message
    assert "Nothing this call created was left behind" in message
    assert any("NEITHER parallel NOR perpendicular" in action
               for action in error.next_actions)


def test_project_failure_without_a_distance_does_not_invent_one() -> None:
    tools = _gsd()

    def refuse(*_args, **_kwargs):
        raise RuntimeError("CATIA rejected the feature (update failed)")

    tools._finish = refuse  # type: ignore[method-assign]
    tools._cutter_gap = lambda a, b: None  # type: ignore[method-assign]
    with pytest.raises(PreconditionRejected) as caught:
        tools._project({"element": "草图.8", "support": "xy"})
    assert "could not be measured" in str(caught.value)


def test_project_is_not_a_bare_com_error_any_more() -> None:
    source = inspect.getsource(GSDTools._project)
    assert "PROJECT_FAILED" in source
    # 0.3.94's whole body was three lines and no handler at all.
    assert "except Exception" in source


# ── the offset says which way it went, in numbers ──────────────────────


def _offset_note(gap):
    tools = _gsd()
    tools._cutter_gap = lambda a, b: gap  # type: ignore[method-assign]
    part = types.SimpleNamespace(
        CreateReferenceFromObject=lambda shape: "ref:offset")
    return tools._offset_side_note(part, "ref:skin", "OFFSET_SHAPE", reverse=False)


def test_offset_note_reports_the_distance_and_the_flag() -> None:
    note = _offset_note(0.4)
    assert "0.4000 mm apart" in note
    assert "reverse=false" in note


def test_offset_note_admits_the_side_is_not_readable() -> None:
    """The centroid idea was tried on B30 and rejected: it measures the solid.

    A measurable taken on a hybrid surface resolves to the enclosing body (a
    100x100 face extract reported the whole block's area), and both offset
    directions measure 0.4000 mm from the original. Shipping "the centroid moved
    -0.4 mm" would have been a measurement of the wrong object - the very defect
    these reports keep paying for.
    """
    note = _offset_note(0.4)
    assert "cannot be read from here" in note
    assert "resolves to the enclosing SOLID" in note
    # It has to leave the caller with something to run.
    assert "catia_boolean(remove)" in note
    assert "reverse=true" in note
    # And it must not pretend to know inside/outside.
    assert "inside" not in note.lower() and "outside" not in note.lower()


def test_offset_note_says_unknown_when_the_distance_fails() -> None:
    note = _offset_note(None)
    assert "could not be measured" in note
    assert "cannot be read from here" in note


def test_offset_no_longer_claims_a_centroid_measurement() -> None:
    source = inspect.getsource(GSDTools)
    assert "_surface_centroid" not in source
    assert "GetCOG" not in inspect.getsource(GSDTools._offset_side_note)


def test_offset_surface_appends_the_note_and_keeps_the_message() -> None:
    source = inspect.getsource(GSDTools._offset_surface)
    assert "_offset_side_note" in source
    assert "return reply + self._offset_side_note" in source


# ── diagnose publishes measured ceilings, not just declarations ───────


def test_capability_ceilings_use_the_stated_vocabulary() -> None:
    allowed = {"UNSUPPORTED_ON_B30", "NOT_IN_TYPELIB",
               "DECLARED_NOT_MEASURED", "AVAILABLE_WITH_LIMIT",
               "MEASURED_UNEXPLAINED"}
    assert _CAPABILITY_CEILINGS
    for entry in _CAPABILITY_CEILINGS:
        assert set(entry) == {"member", "status", "detail"}
        assert entry["status"] in allowed
        assert entry["member"] and entry["detail"]


def test_measured_ceilings_carry_a_date_and_the_unmeasured_do_not_claim_one() -> None:
    for entry in _CAPABILITY_CEILINGS:
        if entry["status"] == "UNSUPPORTED_ON_B30":
            assert "2026-11-10" in entry["detail"], entry["member"]
        if entry["status"] == "DECLARED_NOT_MEASURED":
            assert "unknown" in entry["detail"].lower()


def test_diagnose_publishes_the_block() -> None:
    source = inspect.getsource(DiagnosticsTools._diagnose)
    assert 'report["capability_ceilings"]' in source


def test_split_ceiling_keeps_the_local_cut_warning() -> None:
    entry = next(e for e in _CAPABILITY_CEILINGS if e["member"] == "AddNewSplit")
    assert "NOT a local cut" in entry["detail"]


def test_no_capability_ceiling_is_a_bare_promise() -> None:
    """Every entry has to point somewhere a caller can go."""
    for entry in _CAPABILITY_CEILINGS:
        detail = entry["detail"].lower()
        assert any(word in detail for word in
                   ("catia_", "no tool", "not wrapped", "unknown")), entry["member"]


def test_error_codes_are_distinct_from_the_generic_com_error() -> None:
    # A code that equals the default would be indistinguishable from the bare
    # COM failure these fixes exist to replace.
    assert CodedError.code == "STEP_EXCEPTION"
    assert PreconditionRejected.code != "COM_ERROR"
