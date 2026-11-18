"""2026-11-18 report on 0.3.148: what a datum is, and who consumes a sketch.

Two findings, both about a criterion whose INPUT was not the thing it judged:

* **P2** - ``cleanup_frame_datum="auto"`` refused whenever another sketch was on
  the same PLANE. A plane is infinite, a datum is one axis system: two frames on
  z = 0 with datum points 50 mm apart are two datums, and the reporter measured
  that the cleanup is safe there (forced cleanup + ``Part.Update`` answered
  "Part updated successfully"). Measured the same day here (probe v0463): those
  two sketches report origins (0,0,0) and (50,0,0) with the SAME H and V, while a
  sketch built ON ``<other>_FRAME`` by name reports the identical triple. So the
  axis system separates exactly the two cases that matter.
* **P1** - deleting a sketch a pad consumes was diagnosed as a dead reference.
  The real cause is one sentence long and was already known to this tool
  elsewhere: CATIA does not let a consumed sketch go on its own. Measured both
  ways on B30 2026-11-18 (probe v0462): it can refuse the delete, and it can
  accept it and leave the pad failing every later update. ``Pad.Sketch`` answers
  the profile name (``凸台.1.Sketch.Name`` -> 'SK_A'), so the check is a read, not
  a guess.

Pinned here without CATIA; the session side is ``scripts/b30_v0464_acceptance*``.
"""
from __future__ import annotations

import pytest

from catia_mcp import nurbs, sketch_frames
from catia_mcp.results import ArgumentRejected
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.sketcher import _limited_by

# The two axis systems the report's own repro produces on B30, plus the one a
# sketch built on the datum reports back.
ON_ZERO = ([0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0])
COPLANAR_APART = ([50.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0])
TURNED_AT_SAME_POINT = ([0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0])


# ── P2: a datum is the whole axis system ──────────────────────────────────
def test_a_sketch_built_on_the_frame_reports_the_identical_axis_system():
    # probe v0462: ON_FRAME vs its owner, origin gap 0.000000 mm. This is the
    # direction that must not be got wrong - it is the case that breaks.
    assert sketch_frames.same_datum(ON_ZERO, ON_ZERO) is True


def test_a_coplanar_datum_fifty_millimetres_away_is_a_different_datum():
    assert sketch_frames.same_plane(
        sketch_frames.plane_signature(ON_ZERO),
        sketch_frames.plane_signature(COPLANAR_APART)) is True, (
        "the old criterion saw these two as the same and refused")
    assert sketch_frames.same_datum(ON_ZERO, COPLANAR_APART) is False
    assert sketch_frames.origin_gap(ON_ZERO, COPLANAR_APART) == pytest.approx(50.0)


def test_the_directions_are_compared_too_not_only_the_point():
    assert sketch_frames.same_datum(ON_ZERO, TURNED_AT_SAME_POINT) is False


def test_floating_point_noise_at_the_same_point_is_the_same_datum():
    nudged = ([1e-9, -2e-9, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0])
    assert sketch_frames.same_datum(ON_ZERO, nudged) is True


def test_an_unreadable_axis_system_is_not_a_different_datum():
    assert sketch_frames.same_datum(None, ON_ZERO) is None
    assert sketch_frames.same_datum(ON_ZERO, None) is None
    # a degenerate direction is unknown, not "different": the caller must refuse
    assert sketch_frames.same_datum(
        ON_ZERO, ([0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 1.0, 0.0])) is None


# ── P1: a consumed sketch is refused before anything is attempted ─────────
class _Node:
    def __init__(self, name: str):
        self.Name = name

    def __repr__(self) -> str:
        return f"<{self.Name}>"


class _Placeholder:
    """What a late-bound COM object answers for a member it does not have.

    Measured on B30 2026-11-18 (probe v0462): ``getattr(sketch, 'Reference')``
    returns something instead of raising, and using it fails with
    ``'NoneType' object has no attribute 'Name'``. A consumer scan that trusted
    ``getattr`` would read every non-sketch feature as "no profile".
    """

    @property
    def Name(self):
        raise AttributeError("'NoneType' object has no attribute 'Name'")


class _Feature(_Node):
    """A Part Design feature: ``.Sketch`` answers, for pad/pocket it does."""

    def __init__(self, name: str, profile: str | None):
        super().__init__(name)
        self._profile = profile

    @property
    def Sketch(self):
        if self._profile is None:
            return _Placeholder()
        return _Node(self._profile)


class _Bare(_Node):
    """A feature with no profile at all, like a fillet."""

    @property
    def Sketch(self):
        raise AttributeError("Feature has no Sketch")


class _Collection:
    def __init__(self, items=()):
        self._items = list(items)

    @property
    def Count(self) -> int:
        return len(self._items)

    def Item(self, index):
        if isinstance(index, str):
            for item in self._items:
                if str(item.Name) == index:
                    return item
            raise KeyError(index)
        return self._items[index - 1]

    def names(self) -> list[str]:
        return [str(item.Name) for item in self._items]


class _Body:
    def __init__(self, shapes=(), sketches=(), hybrid=()):
        self.Shapes = _Collection(shapes)
        self.Sketches = _Collection(sketches)
        self.HybridShapes = _Collection(hybrid)


class _Bodies:
    def __init__(self, bodies):
        self._items = list(bodies)

    @property
    def Count(self) -> int:
        return len(self._items)

    def Item(self, index):
        return self._items[index - 1]


class _Part:
    def __init__(self, body: _Body):
        self.Bodies = _Bodies([body])
        self.HybridBodies = _Collection()
        self.updated = 0

    def Update(self) -> None:
        self.updated += 1

    def UpdateObject(self, _node) -> None:
        pass


class _Selection:
    """Records every Delete() as one batch, and can cascade like CATIA does.

    ``cascade`` maps a feature name to the sketches it takes with it - measured
    on B30 2026-11-18: "Deleted feature '凸台.1'. CATIA also removed 1 sketch(es)
    that belonged to it: SK_P".
    """

    def __init__(self, body=None, cascade=None):
        self.batches: list[list[str]] = []
        self.current: list = []
        self.refused: set[str] = set()
        self._body = body
        self._cascade = dict(cascade or {})

    def Clear(self) -> None:
        self.current = []

    def Add(self, node) -> None:
        self.current.append(node)

    def Delete(self) -> None:
        names = [str(node.Name) for node in self.current]
        if any(name in self.refused for name in names):
            raise RuntimeError("(-2147352567, '发生意外。', (0, 'CATIASelection', "
                               "'方法 Delete 失败'))")
        self.batches.append(names)
        body = self._body
        if body is not None:
            taken = {sketch for name in names
                     for sketch in self._cascade.get(name, ())}
            if taken:
                body.Sketches = _Collection(
                    [item for item in body.Sketches._items
                     if str(item.Name) not in taken])
        self.current = []


class _Connection:
    def __init__(self, part: _Part, body: _Body, selection: _Selection):
        self._part, self._body, self._selection = part, body, selection
        self.app = None

    def get_active_part(self):
        return self._part

    def get_active_part_body(self):
        return self._body

    @property
    def hso(self):
        return self._selection

    def ensure_connected(self) -> None:
        pass

    def refresh_display(self) -> None:
        pass


def _tools(shapes=(), sketches=(), cascade=None):
    body = _Body(list(shapes), list(sketches))
    part = _Part(body)
    selection = _Selection(body, cascade)
    return PartDesignTools(_Connection(part, body, selection)), part, selection


def test_the_profile_of_a_pad_is_read_from_the_feature():
    tools, _part, _selection = _tools(
        [_Feature("凸台.1", "SK_A"), _Bare("倒圆角.1")])
    found, unasked = tools._sketch_consumers("SK_A")
    assert found == ["凸台.1"]
    assert unasked == ["倒圆角.1"], (
        "a feature that will not answer has to be reported, not skipped - "
        "otherwise 'no feature names it' is a claim this scan cannot make")


def test_a_placeholder_member_does_not_count_as_a_profile():
    tools, _part, _selection = _tools([_Feature("凸台.1", None)])
    found, unasked = tools._sketch_consumers("SK_A")
    assert found == [] and unasked == ["凸台.1"]


def test_deleting_a_consumed_sketch_is_refused_with_the_consumers_name():
    tools, _part, selection = _tools(
        [_Feature("凸台.1", "SK_A")], [_Node("SK_A")])
    with pytest.raises(ArgumentRejected) as caught:
        tools.execute("catia_delete_feature", {"feature_name": "SK_A"})
    error = caught.value
    assert error.code == "SKETCH_CONSUMED_BY"
    assert "SK_A" in str(error) and "凸台.1" in str(error)
    assert "凸台.1" in " ".join(error.next_actions)
    assert error.data["consumed_by"] == ["凸台.1"], (
        "a gate should not have to parse the name out of the sentence")
    assert selection.batches == [], "nothing may be attempted first"
    assert error.modified is False


def test_the_batch_form_may_delete_the_feature_and_the_sketch_together():
    # The one sensible batch: the pad goes first and takes the sketch with it.
    # Refusing here would leave the caller with two calls and a state between.
    tools, _part, selection = _tools(
        [_Feature("凸台.1", "SK_A")], [_Node("SK_A")])
    text = tools.execute("catia_delete_feature",
                         {"names": ["凸台.1", "SK_A"]})
    assert "SKETCH_CONSUMED_BY" not in text
    assert selection.batches == [["凸台.1"], ["SK_A"]]


# ── P4: what limited the sampling ─────────────────────────────────────────
def test_a_capped_fit_says_the_budget_was_the_limit():
    sp16 = [(66.0, 347.067257), (64.475007, 391.287947),
            (100.837551, 405.962001), (101.0, 432.076285)]
    capped = nurbs.fit_samples(sp16, 3, tolerance=0.01, limit=32)
    assert capped["met"] is False and capped["capped"] is True
    assert _limited_by(capped) == "cap", (
        "0.01 mm was not met because the count stops at 32 per span - a gate "
        "has to be able to tell that from 'this curve cannot do it'")


def test_a_caller_chosen_count_is_what_limited_it():
    fit = {"met": False, "capped": False, "per_span": 4}
    assert _limited_by(fit) == "samples"


def test_a_met_tolerance_claims_no_limit_at_all():
    met = nurbs.fit_samples([(0.0, 0.0), (10.0, 0.0)], 1, tolerance=0.01, limit=32)
    assert met["met"] is True and met["capped"] is False
    assert _limited_by(met) is None


# ── P1, the batch: order and bookkeeping ──────────────────────────────────
def test_the_batch_deletes_the_feature_before_the_sketch_it_consumes():
    # A caller who writes names=["SK_A", "凸台.1"] wants both gone. Doing it in
    # that order deletes the profile out from under a live pad; the batch puts
    # the pad first whatever order the list arrived in.
    tools, _part, selection = _tools(
        [_Feature("凸台.1", "SK_A")], [_Node("SK_A")],
        cascade={"凸台.1": ["SK_A"]})
    reply = tools.execute("catia_delete_feature", {"names": ["SK_A", "凸台.1"]})
    assert selection.batches == [["凸台.1"]], (
        "the pad goes first and takes the sketch with it; a second delete of the "
        "sketch would be an attempt on something already gone")
    assert "SKETCH_CONSUMED_BY" not in reply


def test_a_sketch_taken_by_a_feature_is_counted_as_removed_not_failed():
    # B30 acceptance v0464, first run: the reply read "Deleted 1 element(s) for
    # 2 name(s) given. Failed (1): 'SK_Q' (No feature ... named 'SK_Q')" - a
    # complete success reported as a partial one.
    tools, _part, selection = _tools(
        [_Feature("凸台.1", "SK_A")], [_Node("SK_A")],
        cascade={"凸台.1": ["SK_A"]})
    reply = tools.execute("catia_delete_feature", {"names": ["凸台.1", "SK_A"]})
    assert "Failed" not in reply, reply
    assert "Deleted 2 element(s) for 2 name(s) given." in reply, reply
    assert "'SK_A' (taken with '凸台.1')" in reply, reply
