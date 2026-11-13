# -*- coding: utf-8 -*-
"""0.3.123 - the two P0s of the 2026-11-13 report, and two messages that lied.

Everything here is offline; the numbers in the docstrings were measured on B30
2026-11-13/14 (scripts/b30_v0424_probe_delete_and_view.py, plus the reporter's own
part for the fillet case).

  1. set_view's read-back compared COMPONENTS. CATIA normalises what it stores, so
     `isometric` (-1,-1,-1) read back as (-0.5774,-0.5774,-0.5774) and the reply
     announced "the Put was ignored" - for a camera that had taken it. Every
     diagonal named view and every non-unit `direction=` was misreported.
  2. A batch delete updated the part after EACH name, and read that in-between
     failure as a verdict on the model: "8 elements fail on their own ... delete
     those too". Six healthy features would have gone.
  3. `catia_fillet`'s refusal advice never mentioned the actual cause when the
     profile was drawn as segments: redraw it as ONE closed spline.
  4. `catia_pocket(direction='both')` said "TWO FEATURES" unconditionally and left
     `depth` ambiguous; measured both ways on B30 the same day.
"""

from __future__ import annotations

import pytest

from catia_mcp.tools.export import ExportTools
from catia_mcp.tools.part_design import PartDesignTools


# ── stubs ────────────────────────────────────────────────────────────────────


class _Viewpoint:
    def __init__(self):
        self.ProjectionMode = 0

    def PutSightDirection(self, _vector):
        pass

    def PutUpDirection(self, _vector):
        pass


class _Viewer:
    def __init__(self):
        self.Viewpoint3D = _Viewpoint()
        self.reframed = 0

    def Reframe(self):
        self.reframed += 1


class _SystemService:
    def __init__(self, values):
        self.values = values

    def Evaluate(self, _code, _flag, _func, _params):
        return list(self.values)


def _export(readback):
    tools = ExportTools(connection=None)
    viewer = _Viewer()
    conn = type("C", (), {
        "ensure_connected": staticmethod(lambda: None),
        "active_window": type("W", (), {"ActiveViewer": viewer})(),
        "app": type("A", (), {"SystemService": _SystemService(readback)})(),
    })()
    tools.conn = conn
    return tools, viewer


# ── 1. the read-back compares DIRECTIONS, not numbers ───────────────────────

#: What CATIA actually stores for the named isometric view: (1/sqrt3, ...) and the
#: up vector squared to the sight and normalised. Measured on B30 2026-11-13.
_ISO_READBACK = [-0.5774, -0.5774, -0.5774, -0.4082, -0.4082, 0.8165]


def test_isometric_reads_back_normalised_and_is_not_an_ignored_put():
    """The 0.3.122 report's reproduction A, verbatim.

    (-1,-1,-1) is not a unit vector, CATIA stores unit vectors, and the old
    component-wise test at 1e-6 therefore failed on a camera that had accepted
    the Put - a correct picture labelled as a fake one.
    """
    tools, _ = _export(_ISO_READBACK)
    reply = tools.execute("catia_set_view", {"view": "isometric"})
    assert "the camera took them" in reply
    assert "NOT the direction asked for" not in reply
    assert "sight is 0.00 deg" in reply


def test_a_non_unit_direction_is_not_an_ignored_put_either():
    """Reproduction B: [0.45, 1, -0.18] -> [0.4049, 0.8999, -0.162], 0.00 deg."""
    tools, _ = _export([0.4049, 0.8999, -0.162, 0.0665, 0.1477, 0.9868])
    reply = tools.execute("catia_set_view",
                          {"direction": [0.45, 1.0, -0.18], "up": [0, 0, 1]})
    assert "the camera took them" in reply
    assert "0.00 deg" in reply


def test_the_up_vector_is_squared_to_the_sight_before_it_is_compared():
    """CATIA Gram-Schmidts the up vector; asking for Z with a tilted sight is not
    a different view, it is the same view in CATIA's normal form."""
    tools, _ = _export([0.4049, 0.8999, -0.162, 0.0665, 0.1477, 0.9868])
    reply = tools.execute("catia_set_view",
                          {"direction": [0.45, 1.0, -0.18], "up": [0, 0, 1]})
    assert "squares the up vector to the sight" in reply
    assert "[0.0, 0.0, 1.0]" in reply, "the request is echoed as it was given"
    assert "the camera took them" in reply


def test_a_camera_that_really_looks_somewhere_else_is_still_reported():
    """The guard must not become a rubber stamp: a 90 degree difference is a
    different view and has to say so."""
    tools, _ = _export([0.0, 0.0, -1.0, 0.0, 1.0, 0.0])
    reply = tools.execute("catia_set_view", {"view": "front"})
    assert "NOT the direction asked for" in reply
    assert "90.00 deg" in reply
    assert "not a normalisation difference" in reply


def test_the_angle_thresholds_are_the_documented_ones():
    """0.5 deg for the sight, 1 deg for the up vector: half a degree off is a
    rounding of the normalisation, fifteen is a different camera."""
    tools, _ = _export([0.0, 0.99999, 0.0044, 0.0, 0.0044, 0.99999])  # ~0.25 deg
    assert "the camera took them" in tools.execute("catia_set_view",
                                                   {"view": "front"})
    tools, _ = _export([0.0, 0.9962, 0.0872, 0.0, -0.0872, 0.9962])  # 5 deg
    assert "NOT the direction asked for" in tools.execute("catia_set_view",
                                                          {"view": "front"})


# ── 2. a batch deletes names first and updates ONCE ────────────────────────


class _Part:
    def __init__(self, update_fails=False):
        self.update_calls = 0
        self.update_fails = update_fails
        self.HybridBodies = type("S", (), {"Count": 0})()

    def Update(self):
        self.update_calls += 1
        if self.update_fails:
            raise RuntimeError("方法 Update 失败")


def _design(update_fails=False):
    part = _Part(update_fails)
    tools = PartDesignTools(connection=None)
    tools.conn = type("C", (), {
        "ensure_connected": staticmethod(lambda: None),
        "get_active_part": staticmethod(lambda: part),
        "refresh_display": staticmethod(lambda: None),
    })()
    return tools, part


def test_a_batch_updates_the_part_once_not_once_per_name(monkeypatch):
    """The mechanism of the 2026-11-13 P0-1, pinned."""
    tools, part = _design()
    seen = []

    def one(args, forced, defer_update=False):
        seen.append(defer_update)
        return f"Deleted feature '{forced}'."

    monkeypatch.setattr(tools, "_delete_one", one)
    monkeypatch.setattr("catia_mcp.tools.part_design.failing_elements",
                        lambda _part: [])
    reply = tools.execute("catia_delete_feature", {"names": ["a", "b"]})
    assert seen == [True, True], "no per-name update"
    assert part.update_calls == 1, "ONE update for the whole batch"
    assert "ONE update at the end" in reply


def test_a_terminal_failure_never_says_delete_those_too(monkeypatch):
    """The sentence that cost a reporter six features, replaced.

    It was: "8 elements fail on their own ... delete those too (catia_delete_feature)".
    It is now a TERMINAL state with the destructive route last and its cost named.
    """
    tools, part = _design(update_fails=True)
    monkeypatch.setattr(tools, "_delete_one",
                        lambda args, forced, defer_update=False: "Deleted.")
    monkeypatch.setattr("catia_mcp.tools.part_design.failing_elements",
                        lambda _part: ["凹槽.3", "移除.1"])
    reply = tools.execute("catia_delete_feature", {"names": ["凹槽.1"]})
    assert "TERMINAL" in reply
    assert "delete those too" not in reply
    assert "DESTRUCTIVE" in reply
    assert "凹槽.3" in reply and "移除.1" in reply
    assert part.update_calls == 1


def test_one_unreadable_element_does_not_say_elements_fail(monkeypatch):
    """Singular/plural comes from describe(), which the reply must not re-declare."""
    tools, _part = _design(update_fails=True)
    monkeypatch.setattr(tools, "_delete_one",
                        lambda args, forced, defer_update=False: "Deleted.")
    monkeypatch.setattr("catia_mcp.tools.part_design.failing_elements",
                        lambda _part: ["封闭曲面.1"])
    reply = tools.execute("catia_delete_feature", {"names": ["Neck_T"]})
    assert "'封闭曲面.1' is the element that fails on its own IN THIS STATE" in reply
    assert "fail on their own IN THIS STATE" not in reply


# ── 3. the fillet hint names the SEGMENTED OUTLINE ─────────────────────────


def test_a_rejected_rim_from_one_segmented_sketch_gets_the_redraw_route():
    """The report's §3: 12/12 refused on a 12-segment hole, 1/1 after the redraw.

    The old advice ("draw the radius in the sketch, or cut with catia_gsd_*") sent
    the reporter into a 15-section, 1440-coordinate flare body.
    """
    labels = [f"'Edge.{i}' (L9.7100|Sketch.5;1)" for i in range(120, 132)]
    hint = PartDesignTools._segmented_outline_hint(labels)
    assert "12 of these 12 edge(s)" in hint
    assert "'Sketch.5'" in hint
    assert "catia_sketch_spline(closed=true)" in hint
    assert "1/1 at R1.8" in hint, "the measured before/after has to be in the message"


def test_edges_from_different_profiles_get_no_such_hint():
    labels = [f"'Edge.{i}' (L9.7100|Sketch.{i};1)" for i in range(1, 13)]
    assert PartDesignTools._segmented_outline_hint(labels) == ""


def test_a_couple_of_rejected_edges_is_not_a_segmented_profile():
    """Three is the floor: two edges can share a sketch for innocent reasons."""
    labels = ["'Edge.1' (L9.71|Sketch.5;1)", "'Edge.2' (L9.71|Sketch.5;1)"]
    assert PartDesignTools._segmented_outline_hint(labels) == ""


# ── 4. an edge with one bounding face says so ──────────────────────────────


def test_an_edge_listing_marks_a_missing_second_face():
    """Report P2-2: Edge.64 came back bounded by 'Sketch.5;1' alone, and that
    missing face is exactly the information a fillet refusal needs."""
    assert PartDesignTools._bounded_by_flags(["Sketch.5;1"]) == {
        "bounded_by_incomplete": True}
    assert PartDesignTools._bounded_by_flags(["Pad.1;2", "Sketch.5;1"]) == {}
    assert PartDesignTools._bounded_by_flags([]) == {
        "bounded_by_incomplete": True}
