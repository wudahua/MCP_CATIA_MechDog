"""0.3.150: the 2026-11-19 report's parameter and honesty items.

Each cell here pins a claim that a B30 measurement made, and says which
measurement. The session side is
``scripts/b30_v0466_acceptance_limits_and_hints.py``; these run without CATIA.
"""
from __future__ import annotations

import json

import pytest

from catia_mcp.results import ArgumentRejected
from catia_mcp.tools.part_design import PartDesignTools


# ── P1-1: pad, two extents on ONE feature ────────────────────────────────
def test_limit1_and_limit2_are_accepted_without_height():
    two_sided, first, second, height, direction, symmetric = \
        PartDesignTools._pad_extents({"limit1": 5.0, "limit2": 3.0})
    assert two_sided is True
    assert (first, second, height) == (5.0, 3.0, None)
    assert direction == "normal" and symmetric is False


def test_measured_behaviour_of_reverse_is_the_pair_flipped_not_swapped():
    """B30 2026-11-19: limit1=4, limit2=1 + DirectionOrientation=1 -> z -4..+1.

    The first version of this helper swapped the two magnitudes AND flipped, and
    the acceptance measured z -1..+4 - the request the caller did not make.
    """
    _two, first, second, _height, direction, _sym = \
        PartDesignTools._pad_extents({"limit1": 4.0, "limit2": 1.0,
                                      "direction": "reverse"})
    assert (first, second) == (4.0, 1.0), "the magnitudes keep their sides"
    assert direction == "reverse", "the orientation is what moves them"


def test_limit2_alone_becomes_a_one_sided_pad_the_other_way():
    _two, first, second, _height, direction, _sym = \
        PartDesignTools._pad_extents({"limit2": 7.0})
    assert (first, second) == (7.0, 0.0)
    assert direction == "reverse"


def test_the_two_forms_cannot_be_mixed():
    with pytest.raises(ArgumentRejected) as caught:
        PartDesignTools._pad_extents({"height": 5.0, "limit1": 4.0})
    assert "pass one or the other" in str(caught.value)
    assert caught.value.modified is False


def test_a_zero_extent_pad_is_refused_before_catia():
    with pytest.raises(ArgumentRejected) as caught:
        PartDesignTools._pad_extents({"limit1": 0.0, "limit2": 0.0})
    assert "no extent" in str(caught.value)


def test_a_negative_extent_is_refused_and_points_at_direction():
    with pytest.raises(ArgumentRejected) as caught:
        PartDesignTools._pad_extents({"limit1": -4.0})
    assert "can be negative" in str(caught.value)
    assert "direction" in str(caught.value)


def test_the_schema_requires_one_of_the_two_forms():
    definitions = {tool["name"]: tool
                   for tool in PartDesignTools(None).get_tool_definitions()}
    schema = definitions["catia_pad"]["inputSchema"]
    assert schema["required"] == []
    assert {"required": ["height"]} in schema["anyOf"]
    assert {"required": ["limit1"]} in schema["anyOf"]


# ── P1-2: shaft, two angles ──────────────────────────────────────────────
def test_angle1_and_angle2_are_both_swept():
    assert PartDesignTools._revolution_angles(
        {"angle1": 33.5, "angle2": 33.5}) == (33.5, 33.5)


def test_angle_alone_stays_the_one_sided_form():
    assert PartDesignTools._revolution_angles({"angle": 60}) == (60.0, 0.0)
    assert PartDesignTools._revolution_angles({}) == (360.0, 0.0)


def test_angle2_alone_is_refused_because_it_has_no_direction():
    with pytest.raises(ArgumentRejected) as caught:
        PartDesignTools._revolution_angles({"angle2": 30})
    assert "angle1" in str(caught.value)


def test_a_conflicting_one_sided_angle_is_refused():
    with pytest.raises(ArgumentRejected) as caught:
        PartDesignTools._revolution_angles({"angle": 90, "angle1": 30})
    assert "one form or the other" in str(caught.value)


def test_the_schema_default_of_360_does_not_count_as_a_conflict():
    """An MCP client that fills defaults in must not make angle1 unusable."""
    assert PartDesignTools._revolution_angles(
        {"angle": 360, "angle1": 30, "angle2": 30}) == (30.0, 30.0)


# ── P2-3: the delete preview ─────────────────────────────────────────────
class _Node:
    def __init__(self, name):
        self.Name = name


class _Profile:
    def __init__(self, name):
        self.Name = name


class _Shape(_Node):
    def __init__(self, name, profile=None):
        super().__init__(name)
        self._profile = profile

    @property
    def Sketch(self):
        if self._profile is None:
            raise AttributeError("no Sketch on this feature")
        return _Profile(self._profile)


class _Collection:
    def __init__(self, items=()):
        self._items = list(items)

    @property
    def Count(self):
        return len(self._items)

    def Item(self, index):
        return self._items[index - 1]


class _Body:
    def __init__(self, shapes=(), sketches=()):
        self.Shapes = _Collection(shapes)
        self.Sketches = _Collection(sketches)
        self.HybridShapes = _Collection()
        self.Name = "零件几何体"


class _Part:
    def __init__(self, body):
        self.Bodies = _Collection([body])
        self.MainBody = body


class _Conn:
    def __init__(self, part, body):
        self._part, self._body = part, body
        self.app = None

    def get_active_part(self):
        return self._part

    def get_active_part_body(self):
        return self._body

    def ensure_connected(self):
        pass


def _tools(shapes=(), sketches=()):
    body = _Body(list(shapes), list(sketches))
    return PartDesignTools(_Conn(_Part(body), body)), body


def test_dry_run_names_the_sketch_a_feature_would_take():
    tools, body = _tools([_Shape("凸台.1", "SK_A")], [_Node("SK_A")])
    reply = tools.execute("catia_delete_feature",
                          {"feature_name": "凸台.1", "dry_run": True})
    assert "DRY RUN" in reply and "nothing was deleted" in reply
    assert "SK_D" not in reply
    assert "'SK_A'" in reply and "ALSO remove" in reply
    assert body.Shapes.Count == 1 and body.Sketches.Count == 1, (
        "a preview that deletes is not a preview")


def test_dry_run_says_a_consumed_sketch_would_be_refused():
    tools, body = _tools([_Shape("凸台.1", "SK_A")], [_Node("SK_A")])
    reply = tools.execute("catia_delete_feature",
                          {"feature_name": "SK_A", "dry_run": True})
    assert "REFUSED" in reply and "SKETCH_CONSUMED_BY" in reply
    assert body.Sketches.Count == 1


def test_dry_run_lists_later_features_as_candidates_only():
    tools, _body = _tools([_Shape("凸台.1", "SK_A"), _Shape("凹槽.1", None)],
                          [_Node("SK_A")])
    reply = tools.execute("catia_delete_feature",
                          {"feature_name": "凸台.1", "dry_run": True})
    assert "凹槽.1" in reply
    assert "CANDIDATES, not casualties" in reply, (
        "this tool has no dependency graph - saying 'will fail' would be a guess")


def test_dry_run_needs_a_name():
    tools, _body = _tools([_Shape("凸台.1", "SK_A")])
    with pytest.raises(ArgumentRejected) as caught:
        tools.execute("catia_delete_feature", {"dry_run": True})
    assert "feature_name" in str(caught.value)


# ── P2-5: frame scaffolding is counted, not invisible ────────────────────
def test_frame_scaffolding_is_grouped_by_owning_sketch():
    # Both sketches still exist, so both owners read sketch_present: true - the
    # key 0.3.165 added (2026-11-23 report, P2-N2: the count alone let a reader
    # believe the sketch was still there when only its frame was).
    body = _Body(sketches=[_Node("A"), _Node("B")])
    body.HybridShapes = _Collection([
        _Node("A_FRAME"), _Node("A_FRAME.O"), _Node("A_FRAME.U"),
        _Node("A_FRAME.V"), _Node("B_FRAME"), _Node("B_FRAME.O"),
        _Node("Datum_frame.1"), _Node("Body")])
    tools = PartDesignTools(None)
    inventory = tools._frame_scaffold_inventory(body)
    assert inventory["element_count"] == 7
    assert inventory["sketches"] == [
        {"sketch": "A", "elements": 4, "sketch_present": True},
        {"sketch": "B", "elements": 2, "sketch_present": True}]
    assert inventory["legacy"] == 1, (
        "a pre-0.3.146 datum cannot be tied to a sketch and is counted apart")


# ── P0-1: the constraint ceiling is stated up front ──────────────────────
def test_the_assembly_note_is_one_claim_with_evidence():
    from catia_mcp.tools.document import DocumentTools

    note = DocumentTools._CONSTRAINT_CEILING
    assert "NOT AVAILABLE" in note
    for route in ("ReferenceProduct", "ApplyWorkMode", "StartWorkbench",
                  "GenerateConstraints"):
        assert route in note, f"{route} was one of the routes tried"
    assert "catia_set_component_transform" in note
