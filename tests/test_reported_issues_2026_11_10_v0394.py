# -*- coding: utf-8 -*-
"""0.3.94: the engraving chain, measured before it was advertised.

A 2026-11-10 report listed the operations it needed and could not get. Three of
them turned out to be answerable by measurement rather than by guessing:

  * `ShapeFactory.AddNewSplit(surface, side)` is declared AND works - a 100x100x10
    block split by a plane at Z=3 gave 30000 mm3 with one side value and 70000
    with the other. It needs the cutter to divide the body completely: a 40 mm
    wall on the same block fails at update with the bare "method Update failed".
  * `ShapeFactory.AddNewSewSurface(...)` is declared, creates a feature, and then
    fails at update in every one of the eight geometries tried. Not shipped;
    recorded in the README's known limits instead.
  * `Factory2D` has 37 members and none of them creates text, so "engrave a
    label" is a curve-projection problem, not a font one.

What this file pins down is the part that is code: the split tool's contract, the
pre-flight distance check that turns "NO_MATERIAL after the fact" into "refused
before anything is built", and `catia_sketch_copy(target_body=...)`.
"""
from __future__ import annotations

import types

import pytest

from catia_mcp.results import CodedError, FeatureRejected

from test_modelling_gaps import FakeSolidBody, _pd_with_bodies


# ── a part that can be split, measured and rolled back ─────────────────


class _Shapes:
    def __init__(self, items):
        self._items = list(items)
        self.Count = len(self._items)

    def Item(self, index):
        if isinstance(index, int):
            return self._items[index - 1]
        return next(i for i in self._items if i.Name == index)


class _Body:
    def __init__(self, name):
        self.Name = name
        self.Shapes = _Shapes([types.SimpleNamespace(Name="Pad.1")])


def _split_part(before=100000.0, after=70000.0, update_fails=False, gap=None,
                elements=("Wall", "Geom")):
    """A Part whose volume moves when a Split is created, and whose update may fail."""
    state = {"volume": before}
    log: dict[str, list] = {"split": [], "deleted": [], "added": [], "updates": 0}

    class Feature:
        Name = "Split.1"

    class Factory:
        def AddNewSplit(self, ref, side):
            log["split"].append((ref, side))
            if not update_fails:
                state["volume"] = after
            return Feature()

    body = _Body("PartBody")
    bodies = _Shapes([body])
    hybrid = types.SimpleNamespace(
        Name="Geom",
        HybridShapes=_Shapes([types.SimpleNamespace(Name=n) for n in elements]),
        HybridBodies=_Shapes([]),
    )

    class Measurable:
        @property
        def Volume(self):
            return state["volume"] / 1e9      # SPA answers in m3

        def GetMinimumDistance(self, other):
            if gap is None:
                raise RuntimeError("this measurable can not do distance")
            return gap

    class Spa:
        def GetMeasurable(self, ref):
            return Measurable()

    class Selection:
        def Clear(self):
            log["added"].clear()

        def Add(self, obj):
            log["added"].append(getattr(obj, "Name", obj))

        def Delete(self):
            log["deleted"].extend(log["added"])

    def update():
        log["updates"] += 1
        if update_fails:
            raise RuntimeError("(0, 'CATIAPart', '方法 Update 失败')")

    part = types.SimpleNamespace(
        Bodies=bodies,
        MainBody=body,
        HybridBodies=_Shapes([hybrid]),
        ShapeFactory=Factory(),
        OriginElements=types.SimpleNamespace(PlaneXY="PlaneXY", PlaneYZ="PlaneYZ",
                                             PlaneZX="PlaneZX"),
        Parent=types.SimpleNamespace(GetWorkbench=lambda name: Spa()),
        CreateReferenceFromObject=lambda obj: (
            obj if isinstance(obj, str) else "ref:" + str(obj.Name)),
        UpdateObject=lambda element: None,
        InWorkObject=body,
    )
    part.Update = update

    from catia_mcp.tools.part_design import PartDesignTools

    class Conn:
        active_body = None
        hso = Selection()
        part_in_error = None

        def ensure_connected(self):
            return None

        def get_active_part(self):
            return part

        def get_active_part_body(self):
            return self.active_body or body

        def refresh_display(self):
            return None

    return PartDesignTools(connection=Conn()), part, log


# ── the tool's contract ────────────────────────────────────────────────


def test_the_split_tool_exists_and_states_the_rule_it_cannot_check():
    from catia_mcp.tools.part_design import PartDesignTools

    definitions = {d["name"]: d for d in
                   PartDesignTools(connection=None).get_tool_definitions()}
    tool = definitions["catia_split_solid"]
    assert tool["inputSchema"]["required"] == ["cutter"]
    assert tool["inputSchema"]["properties"]["keep"]["enum"] == ["front", "back"]
    # The two facts a caller cannot discover by trying: the cutter must divide
    # the body, and which side 'front' is.
    assert "COMPLETELY" in tool["description"]
    assert "normal" in tool["description"]


def test_a_split_reports_both_volumes_and_the_side_it_kept():
    tools, part, log = _split_part(before=100000.0, after=70000.0)
    reply = tools._split_solid({"cutter": "Wall"})

    assert log["split"] == [("ref:Wall", 0)]      # 'front' -> iSplitSide 0
    assert "100000.0 -> 70000.0 mm3" in reply
    assert "70.0%" in reply                        # the share, so a wrong half shows
    assert "keep='back'" in reply                  # ...and how to flip it
    assert "still in its geometrical set" in reply


def test_keep_back_is_the_other_side_value():
    tools, part, log = _split_part()
    tools._split_solid({"cutter": "Wall", "keep": "back"})
    assert log["split"] == [("ref:Wall", 1)]


def test_the_origin_planes_answer_to_the_aliases_the_gsd_tools_use():
    tools, part, log = _split_part()
    reply = tools._split_solid({"cutter": "zx"})
    assert log["split"] == [("PlaneZX", 0)]
    assert "origin plane PlaneZX" in reply


def test_an_unknown_cutter_lists_what_the_sets_hold():
    tools, _, _ = _split_part()
    with pytest.raises(CodedError) as caught:
        tools._split_solid({"cutter": "Wlal"})
    assert caught.value.code == "CUTTER_NOT_FOUND"
    assert "Wall" in str(caught.value)


def test_a_bad_keep_value_is_refused_before_catia_is_asked():
    tools, _, log = _split_part()
    with pytest.raises(CodedError) as caught:
        tools._split_solid({"cutter": "Wall", "keep": "left"})
    assert caught.value.code == "BAD_ARGUMENT"
    assert log["split"] == []


# ── the three ways a split can be a lie ────────────────────────────────


def test_a_split_that_changes_nothing_is_taken_back_out():
    """A feature with nothing to cut is perfectly valid to CATIA - only the
    volume disagrees. Same rule as catia_boolean(remove) in 0.3.93."""
    tools, _, log = _split_part(before=100000.0, after=100000.0)
    with pytest.raises(FeatureRejected) as caught:
        tools._split_solid({"cutter": "Wall"})

    error = caught.value
    assert error.code == "SPLIT_NO_EFFECT"
    assert error.rolled_back is True
    assert log["deleted"] == ["Split.1"]
    assert "catia_measure_distance('Wall', 'PartBody')" in " ".join(error.next_actions)


def test_a_failed_update_names_the_rule_that_was_measured():
    tools, _, log = _split_part(update_fails=True)
    with pytest.raises(FeatureRejected) as caught:
        tools._split_solid({"cutter": "Wall"})

    error = caught.value
    assert error.code == "SPLIT_FAILED"
    assert error.rolled_back is True                 # the dead node was removed
    assert log["deleted"] == ["Split.1"]
    text = str(error)
    assert "does not divide 'PartBody' completely" in text
    assert "NOT made" in text
    assert "catia_boolean(remove)" in " ".join(error.next_actions)


def test_a_split_that_keeps_an_empty_half_is_refused():
    tools, _, log = _split_part(before=100000.0, after=0.0)
    with pytest.raises(FeatureRejected) as caught:
        tools._split_solid({"cutter": "Wall"})

    error = caught.value
    assert error.code == "SPLIT_KEPT_NOTHING"
    assert error.rolled_back is True
    assert log["deleted"] == ["Split.1"]
    assert "'back'" in error.next_actions[0]


# ── the cut is judged BEFORE it is built ───────────────────────────────


def _bool_part(gap=None, before=1000.0, after=900.0):
    tools, conn, part = _pd_with_bodies(["PartBody", "Tool"])
    log = {"created": 0}
    state = {"volume": before}

    class Feature:
        Name = "Remove.1"

    class Factory:
        def AddNewRemove(self, body):
            log["created"] += 1
            state["volume"] = after
            return Feature()

    class Measurable:
        @property
        def Volume(self):
            return state["volume"] / 1e9

        def GetMinimumDistance(self, other):
            if gap is None:
                raise RuntimeError("no distance on this build")
            return gap

    class Spa:
        def GetMeasurable(self, ref):
            return Measurable()

    part.ShapeFactory = Factory()
    part.CreateReferenceFromObject = lambda obj: obj
    part.Parent = types.SimpleNamespace(GetWorkbench=lambda name: Spa())
    part.Update = lambda: None
    part.UpdateObject = lambda feature: None
    return tools, part, log, state


def test_a_tool_body_that_never_reaches_the_target_is_refused_before_anything_is_built():
    """0.3.93 reported NO_MATERIAL after the fact. The distance is measurable
    first, and a positive gap PROVES the intersection is empty."""
    tools, part, log, state = _bool_part(gap=0.3)
    with pytest.raises(FeatureRejected) as caught:
        tools._boolean({"operation": "remove", "tool_body": "Tool",
                        "target_body": "PartBody"})

    error = caught.value
    assert error.code == "TOOL_DOES_NOT_TOUCH"
    assert error.rolled_back is False
    assert log["created"] == 0                       # no feature, no rollback needed
    assert "0.3000 mm away" in str(error)
    assert state["volume"] == 1000.0


def test_an_intersect_whose_bodies_are_apart_is_refused_too():
    tools, part, log, _ = _bool_part(gap=2.5)
    with pytest.raises(FeatureRejected) as caught:
        tools._boolean({"operation": "intersect", "tool_body": "Tool",
                        "target_body": "PartBody"})
    assert caught.value.code == "TOOL_DOES_NOT_TOUCH"
    assert "EMPTY body" in str(caught.value)
    assert log["created"] == 0


def test_touching_bodies_are_not_refused():
    """Distance 0 is not "overlapping" - two bodies can touch at an edge and
    share no volume - so the check must stay one-sided or it blocks real cuts."""
    tools, part, log, _ = _bool_part(gap=0.0)
    reply = tools._boolean({"operation": "remove", "tool_body": "Tool",
                            "target_body": "PartBody"})
    assert log["created"] == 1
    assert "Boolean 'remove'" in reply


def test_an_unmeasurable_gap_does_not_block_the_cut():
    tools, part, log, _ = _bool_part(gap=None)
    reply = tools._boolean({"operation": "remove", "tool_body": "Tool",
                            "target_body": "PartBody"})
    assert log["created"] == 1
    assert "Boolean 'remove'" in reply


def test_a_union_is_never_judged_by_distance():
    """Two disjoint bodies can legitimately be joined, so 'add' must not even
    look at the gap."""
    tools, part, log, _ = _bool_part(gap=50.0)
    part.ShapeFactory.AddNewAdd = part.ShapeFactory.AddNewRemove
    reply = tools._boolean({"operation": "add", "tool_body": "Tool",
                            "target_body": "PartBody"})
    assert log["created"] == 1
    assert "Boolean 'add'" in reply


# ── the copy knows which body it belongs in ────────────────────────────


def _copy_tools(parent_name="Label"):
    from catia_mcp.tools.sketcher import SketcherTools

    bodies = [_Body("PartBody"), _Body("Label")]
    bodies[0].Shapes = _Shapes([])

    class Conn:
        active_body = None
        last_sketch = None
        active_document = types.SimpleNamespace(Name="P.CATPart")

        def ensure_connected(self):
            return None

        def get_active_part(self):
            return part

        def get_active_part_body(self):
            return self.active_body or bodies[0]

        def refresh_display(self):
            return None

    part = types.SimpleNamespace(Bodies=_Shapes(bodies), InWorkObject=None,
                                 MainBody=bodies[0])

    tools = SketcherTools(connection=Conn())
    tools._drawn = {tools._sketch_key("Src"): [{"kind": "line", "x1": 0, "y1": 0,
                                                "x2": 10, "y2": 0}]}
    tools._active_sketch = None
    tools._active_factory = object()
    tools._shift_element = lambda element, dx, dy: element
    tools._replay_element = lambda factory, element: None
    tools._note_element = lambda **params: None
    tools._close_sketch = lambda: "Sketch closed."
    tools._copy_placement_note = lambda target: ""

    def create(plane=None):
        tools._active_sketch = types.SimpleNamespace(
            Name="Section.2", Parent=types.SimpleNamespace(Name=parent_name))

    tools._create_sketch = create
    return tools, part, Conn


def test_a_copy_can_be_put_in_another_body_and_says_which():
    tools, part, connector = _copy_tools(parent_name="Label")
    reply = tools._copy_sketch({"source": "Src", "plane": "xy",
                                "target_body": "Label"})
    assert "in body 'Label'" in reply
    assert tools.conn.active_body is part.Bodies.Item("Label")
    assert str(part.InWorkObject.Name) == "Label"


def test_a_copy_that_landed_in_the_wrong_body_is_not_reported_as_fine():
    """InWorkObject reports no error while CATIA keeps pointing somewhere else
    (measured 2026-10-26), so the parent is read back."""
    tools, part, _ = _copy_tools(parent_name="PartBody")
    reply = tools._copy_sketch({"source": "Src", "plane": "xy",
                                "target_body": "Label"})
    assert "WARNING" in reply
    assert "wrong body" in reply


def test_an_unknown_target_body_lists_the_bodies():
    tools, part, _ = _copy_tools()
    with pytest.raises(CodedError) as caught:
        tools._copy_sketch({"source": "Src", "plane": "xy",
                            "target_body": "Lable"})
    assert caught.value.code == "BODY_NOT_FOUND"
    assert "Label" in str(caught.value)


def test_the_copy_tool_declares_the_new_argument():
    from catia_mcp.tools.sketcher import SketcherTools

    definitions = {d["name"]: d for d in
                   SketcherTools(connection=None).get_tool_definitions()}
    props = definitions["catia_sketch_copy"]["inputSchema"]["properties"]
    assert "target_body" in props
