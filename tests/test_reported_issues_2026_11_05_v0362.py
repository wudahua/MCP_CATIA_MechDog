# -*- coding: utf-8 -*-
"""0.3.62 - two settings this project asserted and never measured.

**Threading.** ``catia_hole`` wrote ``hole.ThreadingMode = 1  # catThreaded``
from 0.3.0 through 0.3.61. The value came from the comment, not from CATIA. Read
out of the Hole interface's own type library on B30 (2026-11-05):

    ENUM CatHoleThreadingMode
      catThreadedHoleThreading = 0
      catSmoothHoleThreading   = 1

so every hole asked for as threaded was built smooth and reported as created.
Nothing caught it because 1 is also what a hole in a fresh part already carries:
the write was a no-op on a property that was never read back.

Measured the same day, a second trap: a hole INHERITS the mode of the hole before
it in the same part. Three holes built in a row read 0 / 0 / 0 after the first
was threaded, and the two plain ones had never been touched. So ``threaded:
false`` has to be written too - "leave it alone" keeps the previous bore's
thread.

**Constraint indices.** ``GeometricElements`` item 1 is the sketch's ABSOLUTE
AXIS, not the first thing the caller drew:

    1: '绝对轴'   2: '直线.1'   3: '直线.2'

``catia_sketch_constraint`` passed the index straight to
``CreateReferenceFromObject``, so the natural ``geometry_index_1: 1`` - "my first
line" - produced CATIA's bare ``方法 AddMonoEltCst 失败``, which names neither
the axis nor the index. The delete path had refused index 1 for releases; the
constraint path had not.

**Not adopted.** The same upstream commit claims SPA Measurable scalars read 0
under late-bound dispatch and need re-wrapping in win32com.client.Dispatch.
Measured on a 20 mm cube whose values are exact (8000 mm3, 2400 mm2): raw and
re-wrapped read identically, so the re-dispatch is not here. It also says a fix
constraint needs ReferenceType = 1; a fresh fix reads 0 and there is no
CatConstraintReferenceType enum in that library to say what 0 means, so nothing
is written there either. And CatConstraintMode already reads 0 (driving) on a
new constraint, so forcing it would be a write that changes nothing.
"""

from __future__ import annotations

import json

import pytest

from catia_mcp.results import ArgumentOutOfRange
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.sketcher import SketcherTools


# ───────────── stubs: a hole, a part, a sketch, and the order things happened

class _Hole:
    def __init__(self, mode=1, refuse_write=False, name="孔.1") -> None:
        self._mode = mode
        self._refuse = refuse_write
        self.Name = name

    @property
    def ThreadingMode(self):
        if self._mode is None:
            raise AttributeError("ThreadingMode")
        return self._mode

    @ThreadingMode.setter
    def ThreadingMode(self, value):
        if self._refuse:
            raise RuntimeError("CATIA says no")
        self._mode = value


class _Part:
    """Records the order of writes and updates, which is half the fix."""

    def __init__(self, fail_update=False) -> None:
        self.events: list[tuple] = []
        self._fail = fail_update

    def Update(self) -> None:
        self.events.append(("update",))
        if self._fail:
            raise RuntimeError("update failed")


class _SpyHole(_Hole):
    def __init__(self, part: _Part, mode=1, refuse_write=False) -> None:
        super().__init__(mode=mode, refuse_write=refuse_write)
        self._part = part

    @_Hole.ThreadingMode.setter
    def ThreadingMode(self, value):
        self._part.events.append(("mode", value))
        _Hole.ThreadingMode.fset(self, value)


# ───────────── the threading mode

def test_a_threaded_hole_is_written_as_zero_not_one():
    """0 is catThreadedHoleThreading; 1 is what every smooth hole already has."""
    part = _Part()
    hole = _SpyHole(part, mode=1)
    note = PartDesignTools(connection=None)._apply_threading(part, hole, True)
    assert part.events[0] == ("mode", 0), (
        "writing 1 here is what made every threaded hole smooth for 61 releases")
    assert hole.ThreadingMode == 0
    assert "THREADED" in note
    assert "catThreadedHoleThreading" in note, (
        "the reply has to name the enum, or the next reader re-guesses the number")


def test_the_mode_is_written_before_the_part_is_updated():
    """Threading a hole that has not materialized breaks the rebuild."""
    part = _Part()
    hole = _SpyHole(part, mode=1)
    PartDesignTools(connection=None)._apply_threading(part, hole, True)
    assert part.events == [("mode", 0), ("update",)], (
        "set the mode, then update - not the other way round")


def test_a_hole_inherits_the_previous_bores_thread_so_false_is_written_too():
    """Three holes read 0/0/0 after one threaded hole, with no write on the rest."""
    part = _Part()
    hole = _SpyHole(part, mode=0)          # inherited from the hole before it
    note = PartDesignTools(connection=None)._apply_threading(part, hole, False)
    assert part.events == [("mode", 1), ("update",)], (
        "doing nothing leaves the previous bore's thread on this one")
    assert hole.ThreadingMode == 1
    assert "THREADED" not in note


def test_a_smooth_hole_that_is_already_smooth_is_not_touched():
    """No second update for the common case: writing 1 over 1 buys nothing."""
    part = _Part()
    hole = _SpyHole(part, mode=1)
    note = PartDesignTools(connection=None)._apply_threading(part, hole, False)
    assert part.events == []
    assert note == ""


def test_an_unreadable_mode_is_treated_as_must_write():
    part = _Part()
    hole = _SpyHole(part, mode=None)
    PartDesignTools(connection=None)._apply_threading(part, hole, True)
    assert part.events[0] == ("mode", 0)


def test_a_refused_thread_write_is_an_error_not_a_plain_bore():
    """The caller asked for a thread; a smooth bore reported as success is the bug."""
    part = _Part()
    hole = _SpyHole(part, mode=1, refuse_write=True)
    with pytest.raises(RuntimeError) as caught:
        PartDesignTools(connection=None)._apply_threading(part, hole, True)
    message = str(caught.value)
    assert "SMOOTH" in message
    assert "catia_delete_feature" in message, (
        "the feature is already in the tree; say how to get rid of it")


def test_a_refused_reset_on_a_plain_hole_warns_instead_of_raising():
    part = _Part()
    hole = _SpyHole(part, mode=0, refuse_write=True)
    note = PartDesignTools(connection=None)._apply_threading(part, hole, False)
    assert "WARNING" in note and "thread" in note


def test_a_failed_update_after_threading_is_an_error():
    part = _Part(fail_update=True)
    hole = _SpyHole(part, mode=1)
    with pytest.raises(RuntimeError) as caught:
        PartDesignTools(connection=None)._apply_threading(part, hole, True)
    assert "thread" in str(caught.value).lower()


# ───────────── the absolute axis

def test_the_absolute_axis_is_recognised_by_name_and_by_position():
    tools = SketcherTools(connection=None)
    assert tools._is_absolute_axis(_Element("绝对轴"), 1) is True
    assert tools._is_absolute_axis(_Element("AbsoluteAxis"), 2) is True
    assert tools._is_absolute_axis(_Element("直线.1"), 1) is True, (
        "item 1 is the axis even in a build whose name we do not match")
    assert tools._is_absolute_axis(_Element("直线.1"), 2) is False


class _Element:
    def __init__(self, name: str) -> None:
        self.Name = name


class _Geometry:
    def __init__(self, names: list[str]) -> None:
        self._items = [_Element(name) for name in names]
        self.Count = len(names)

    def Item(self, index: int):
        return self._items[index - 1]


class _Constraint:
    Name = "固定的.1"


class _Constraints:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def AddMonoEltCst(self, code, reference):
        self.calls.append(("mono", code, reference))
        return _Constraint()

    def AddBiEltCst(self, code, first, second):
        self.calls.append(("bi", code, first, second))
        return _Constraint()


class _Sketch:
    def __init__(self, names: list[str], label: str = "草图.1") -> None:
        self.Name = label
        self.GeometricElements = _Geometry(names)
        self.Constraints = _Constraints()


class _RefPart:
    def CreateReferenceFromObject(self, obj):
        return ("ref", obj.Name)


class _SketcherConn:
    def __init__(self) -> None:
        self.part = _RefPart()

    def get_active_part(self):
        return self.part


def _sketcher(names: list[str]) -> tuple[SketcherTools, _Sketch]:
    tools = SketcherTools(connection=_SketcherConn())
    sketch = _Sketch(names)
    tools._active_sketch = sketch
    tools._sketch_part = _SketcherConn().part
    return tools, sketch


def test_constraining_index_one_explains_itself():
    tools, _sketch_ = _sketcher(["绝对轴", "直线.1", "直线.2"])
    with pytest.raises(RuntimeError) as caught:
        tools._add_constraint({"type": "fix", "geometry_index_1": 1})
    message = str(caught.value)
    assert "ABSOLUTE AXIS" in message
    assert "index 2" in message, "say where the first drawn element actually is"
    assert "AddMonoEltCst" not in message, (
        "quoting CATIA's bare failure is what left the caller with nothing")


def test_the_second_index_is_the_first_line_and_it_works():
    tools, sketch = _sketcher(["绝对轴", "直线.1", "直线.2"])
    reply = tools._add_constraint({"type": "fix", "geometry_index_1": 2})
    assert "固定的.1" in reply
    assert sketch.Constraints.calls == [("mono", 0, ("ref", "直线.1"))]


def test_the_axis_is_refused_in_the_second_slot_as_well():
    tools, _sketch_ = _sketcher(["绝对轴", "直线.1", "直线.2"])
    with pytest.raises(RuntimeError) as caught:
        tools._add_constraint({"type": "distance", "geometry_index_1": 2,
                               "geometry_index_2": 1, "value": 25})
    assert "geometry_index_2" in str(caught.value), (
        "the message has to name the argument that was wrong, not the first one")


def test_an_index_past_the_end_says_how_many_there_are():
    tools, _sketch_ = _sketcher(["绝对轴", "直线.1"])
    with pytest.raises(ArgumentOutOfRange) as caught:
        tools._add_constraint({"type": "fix", "geometry_index_1": 9})
    assert "2 element" in str(caught.value)


def test_the_geometry_listing_marks_the_axis():
    tools, _sketch_ = _sketcher(["绝对轴", "直线.1", "直线.2"])
    tools.conn = _SketcherConn()
    tools.conn.active_document = type("Doc", (), {"Name": "Part1.CATPart"})()
    tools._locate_sketch = lambda name=None: (tools._active_sketch, "the open sketch")
    payload = json.loads(tools._get_geometry())
    assert payload["elements"][0]["absolute_axis"] is True
    assert payload["elements"][1]["absolute_axis"] is False
    assert "absolute_axis" in payload["note"]
