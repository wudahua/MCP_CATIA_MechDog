# -*- coding: utf-8 -*-
"""0.3.95: the copy that warned about a body it was already in.

0.3.94's own new feature was the loudest thing in the 2026-11-10 acceptance: the
copy landed in the right body, the pad built 50000 mm3 in that body, and **every
single call** answered "WARNING: the sketch reports its body as 'Sketches', so the
switch did not take; the copy is in the wrong body".

The cause is one line: `sketch.Parent` is not the Body. On B30 the chain is

    草图.1 -> Sketches -> 零件几何体 -> Bodies -> Part -> document

so comparing `sketch.Parent.Name` ("Sketches") with the body's name could never
match. The rest of this file covers the smaller things the same pair of reports
found, each one a case of the tool saying something the measurement does not
support.
"""
from __future__ import annotations

import types

import pytest

from catia_mcp.results import CodedError


# ── a sketch's owner is asked of the body, not of the sketch ───────────


class _Coll:
    def __init__(self, items=()):
        self._items = list(items)
        self.Count = len(self._items)

    def Item(self, key):
        if isinstance(key, int):
            return self._items[key - 1]
        return next(i for i in self._items if i.Name == key)

    def append(self, item):
        self._items.append(item)
        self.Count = len(self._items)


class _SketchObj:
    """A sketch double with identity equality.

    SimpleNamespace compares by value, so two sketches that share a name would
    compare equal - which is exactly the ambiguity this test is about. A CATIA
    dispatch pointer compares by COM identity.
    """

    def __init__(self, name):
        self.Name = name
        self.Parent = types.SimpleNamespace(Name="Sketches")


def _body(name, sketches=()):
    body = types.SimpleNamespace(Name=name)
    body.Sketches = _Coll(sketches)
    body.Shapes = _Coll()
    return body


def _part(*bodies):
    return types.SimpleNamespace(Bodies=_Coll(bodies), MainBody=bodies[0],
                                 InWorkObject=None)


def _sketcher(part):
    from catia_mcp.tools.sketcher import SketcherTools

    class Conn:
        active_body = None
        last_sketch = None
        active_document = types.SimpleNamespace(Name="P.CATPart")

        def ensure_connected(self):
            return None

        def get_active_part(self):
            return part

        def get_active_part_body(self):
            return self.active_body or part.MainBody

        def refresh_display(self):
            return None

    return SketcherTools(connection=Conn())


def test_the_owner_is_found_even_though_parent_is_the_collection():
    """The reported shape: Parent.Name is "Sketches" and must not be believed."""
    sketch = _SketchObj("CopyCheck")
    part = _part(_body("零件几何体"), _body("CopyTarget", [sketch]))

    tools = _sketcher(part)
    assert tools._body_holding(part, sketch) == "CopyTarget"


def test_a_duplicated_name_does_not_make_the_owner_ambiguous():
    """A part can hold two sketches called 草图.1 after a rollback, so identity
    is checked before the name."""
    other = _SketchObj("草图.1")
    wanted = _SketchObj("草图.1")
    part = _part(_body("A", [other]), _body("B", [wanted]))

    tools = _sketcher(part)
    assert tools._body_holding(part, wanted) == "B"


def test_an_unreadable_body_is_unknown_not_wrong():
    sketch = _SketchObj("S")
    broken = types.SimpleNamespace(Name="B")
    broken.Sketches = types.SimpleNamespace(
        Count=1, Item=lambda i: (_ for _ in ()).throw(RuntimeError("E_FAIL")))
    part = _part(_body("A"), broken)

    tools = _sketcher(part)
    assert tools._body_holding(part, sketch) is None


# ── many polylines in one call ─────────────────────────────────────────


class _Factory:
    def __init__(self):
        self.lines = []

    def CreateLine(self, x1, y1, x2, y2):
        self.lines.append((x1, y1, x2, y2))
        return types.SimpleNamespace(Construction=False)

    def CreateCircle(self, cx, cy, radius, start, end):
        self.lines.append((cx, cy, radius, start, end))


def _stroke_tools():
    tools = _sketcher(_part(_body("P")))
    factory = _Factory()
    tools._active_factory = factory
    tools._active_sketch = types.SimpleNamespace(Name="Sketch.1")
    tools._ensure_sketch_open = lambda: None
    tools._notes = []

    def note_ends(a, b):
        tools._notes.append(("ends", a, b))

    def note_element(kind, **params):
        tools._notes.append((kind, params))

    tools._note_ends = note_ends
    tools._note_element = note_element
    return tools, factory


def test_a_letter_outline_is_drawn_as_straight_segments_in_one_call():
    tools, factory = _stroke_tools()
    # A very angular "A": two diagonals and a crossbar.
    reply = tools._draw_strokes([[[0, 0], [5, 10], [10, 0]],
                                 [[2, 0], [8, 0]]])

    assert len(factory.lines) == 3          # 2 + 1 segments, not 4 points
    assert "2 stroke(s) drawn as 3 straight line(s)" in reply
    assert "nothing was smoothed" in reply


def test_a_stroke_that_repeats_its_first_point_is_reported_closed():
    tools, factory = _stroke_tools()
    reply = tools._draw_strokes([[[0, 0], [10, 0], [10, 10], [0, 0]]])
    assert "1 of them closed" in reply
    assert len(factory.lines) == 3


def test_every_segment_is_recorded_so_the_copy_can_replay_it():
    """Recorded as "line", which is the kind the replay path already knows - a
    new record kind would have been an unhandled branch in catia_sketch_copy."""
    tools, factory = _stroke_tools()
    tools._draw_strokes([[[0, 0], [5, 10], [10, 0]]])

    kinds = [entry[0] for entry in tools._notes if entry[0] != "ends"]
    assert kinds == ["line", "line"]


def test_a_repeated_point_is_refused_before_anything_is_drawn():
    """A zero-length line is accepted by CATIA and breaks the profile three steps
    later, so the whole batch is refused up front - not half drawn."""
    tools, factory = _stroke_tools()
    with pytest.raises(CodedError) as caught:
        tools._draw_strokes([[[0, 0], [10, 0]], [[5, 5], [5, 5], [9, 9]]])
    assert caught.value.code == "DEGENERATE_SEGMENT"
    assert factory.lines == []


def test_a_stroke_with_one_point_is_refused():
    tools, factory = _stroke_tools()
    with pytest.raises(CodedError) as caught:
        tools._draw_strokes([[[0, 0]]])
    assert caught.value.code == "BAD_ARGUMENT"
    assert factory.lines == []


def test_construction_strokes_are_not_recorded_as_profile_geometry():
    tools, factory = _stroke_tools()
    tools._draw_strokes([[[0, 0], [10, 0]]], construction=True)
    assert factory.lines == [(0, 0, 10, 0)]
    assert [entry[0] for entry in tools._notes] == []


# ── the arc's own direction, not the one that was typed ────────────────


def _arc_tools():
    tools = _sketcher(_part(_body("P")))
    factory = _Factory()
    tools._active_factory = factory
    tools._active_sketch = types.SimpleNamespace(Name="Sketch.1")
    tools._ensure_sketch_open = lambda: None
    tools._note_ends = lambda a, b: None
    tools._note_element = lambda kind, **params: None
    return tools, factory


def test_descending_angles_are_described_as_clockwise():
    """Reported 2026-11-10: "from 90 to 0 - swept 90 counter-clockwise", which
    describes no arc at all. 90 -> 0 is clockwise."""
    tools, factory = _arc_tools()
    reply = tools._draw_arc(0, 0, 10, start_angle=90, end_angle=0)
    assert "from 90° clockwise to 0°" in reply
    assert "counter-clockwise" not in reply.split("(")[0]


def test_ascending_angles_are_described_as_counter_clockwise():
    tools, factory = _arc_tools()
    reply = tools._draw_arc(0, 0, 10, start_angle=0, end_angle=90)
    assert "from 0° counter-clockwise to 90°" in reply


def test_a_major_arc_asked_for_clockwise_says_clockwise():
    tools, factory = _arc_tools()
    reply = tools._draw_arc(0, 0, 10, start_angle=0, end_angle=90,
                            direction="cw", major=True)
    assert "from 0° clockwise to 90°" in reply


# ── list_bodies stops hiding a stale handle ────────────────────────────


def test_list_bodies_names_the_active_body_and_says_when_it_is_gone():
    from test_modelling_gaps import _pd_with_bodies

    tools, conn, part = _pd_with_bodies(["零件几何体", "FarTool"])
    conn.hso = types.SimpleNamespace(Clear=lambda: None, Add=lambda o: None,
                                     Count=0)
    # The state a boolean leaves behind: the handle sits on the body it consumed.
    conn.active_body = types.SimpleNamespace(Name="几何体.5")

    payload = __import__("json").loads(tools._list_bodies())
    assert payload["active_body"] == "几何体.5"
    assert payload["active_body_missing"] is True
    assert "NOT in this list" in payload["active_body_note"]
    assert all(entry["active"] is False for entry in payload["bodies"])


def test_list_bodies_is_quiet_when_the_active_body_exists():
    from test_modelling_gaps import _pd_with_bodies

    tools, conn, part = _pd_with_bodies(["零件几何体"], main="零件几何体")
    conn.hso = types.SimpleNamespace(Clear=lambda: None, Add=lambda o: None,
                                     Count=0)
    payload = __import__("json").loads(tools._list_bodies())
    assert payload["active_body"] == "零件几何体"
    assert "active_body_missing" not in payload
