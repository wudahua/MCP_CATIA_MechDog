# -*- coding: utf-8 -*-
"""0.3.48 - the two array findings from the 2026-10-31 report that were safe to fix.

Both were measured on CATIA B30 on 2026-11-02 before and after the change.

#7 circ_pattern had no way to ask for the other direction. A 4x4 pad seeded at
   (30, 0), two copies at 90 degrees about Z: the copy landed at (0, -30), i.e.
   CLOCKWISE, while the flag that controls it was hardcoded False. `reverse` is
   now an argument (default unchanged) and the reply says which way it went.

#6 user_pattern reported its `count` argument as the number of instances, and
   CATIA ignores it: a 1000 mm3 pocket with a 3-point sketch removed 3000 mm3 -
   three copies - with count=1, 3 and 5 alike. The reply now counts the sketch's
   locating points (minus its own absolute axis) and names the disagreement.

rect_pattern is NOT here: measured the same day, the direction a plane reference
produces depends on its partner (the same PlaneYZ drove Y beside PlaneZX and Z
beside PlaneXY), and line references are rejected outright, so that one needs a
runtime check rather than a patch.
"""

from __future__ import annotations

import json

import pytest


# ───────────────────────────── circ_pattern: the direction

class _Pattern:
    Name = "圆形阵列.1"


class _ShapeFactory:
    def __init__(self):
        self.calls = []

    def AddNewCircPattern(self, *args):
        self.calls.append(args)
        return _Pattern()


class _Part:
    def __init__(self):
        self.ShapeFactory = _ShapeFactory()
        self.InWorkObject = None

    def CreateReferenceFromName(self, name):
        return f"ref:{name}"

    def Update(self):
        pass


def _circ_tools():
    from catia_mcp.tools.part_design import PartDesignTools

    part = _Part()
    tools = PartDesignTools(connection=None)
    tools.conn = type("C", (), {
        "ensure_connected": staticmethod(lambda: None),
        "get_active_part": staticmethod(lambda: part),
        "refresh_display": staticmethod(lambda: None),
        "hso": None,
    })()
    tools._prepare_body = lambda p: "BODY"
    tools._pattern_seed = lambda body, name: "SEED"
    tools._pattern_axis_reference = lambda p, axis, center: "AXIS"
    tools._volume_probe = lambda p, body: (100.0, "")
    tools._body_volume_mm3 = lambda p, body: 800.0
    tools._try_update = lambda p, pattern: None
    return tools


def _circ_call(tools, **extra):
    arguments = {"axis": "z", "center": [0.0, 0.0, 0.0], "count": 2,
                 "angular_spacing": 90.0}
    arguments.update(extra)
    reply = tools.execute("catia_circ_pattern", arguments)
    return reply, tools.conn.get_active_part().ShapeFactory.calls[-1]


def test_the_default_direction_is_the_measured_one_and_says_so():
    tools = _circ_tools()
    reply, call = _circ_call(tools)
    # (seed, radial, angular, steps, positions, centre, axis, reversed, angle, aligned)
    assert call[9] is False, "the default must not change: it is what 0.3.42 shipped"
    assert "CLOCKWISE" in reply
    assert "reverse=true" in reply


def test_reverse_turns_the_copies_the_other_way():
    tools = _circ_tools()
    reply, call = _circ_call(tools, reverse=True)
    assert call[9] is True, "reverse must reach CATIA's reversed-axis flag"
    assert "the other way round" in reply


def test_the_reverse_argument_is_advertised_with_its_default():
    from catia_mcp.tools.part_design import PartDesignTools

    schema = [d for d in PartDesignTools(connection=None).get_tool_definitions()
              if d["name"] == "catia_circ_pattern"][0]
    prop = schema["inputSchema"]["properties"]["reverse"]
    assert prop["type"] == "boolean"
    assert prop["default"] is False
    assert "CLOCKWISE" in prop["description"]


def test_a_single_copy_does_not_talk_about_direction():
    """count=1 has no second copy to have a direction about."""
    tools = _circ_tools()
    reply, _ = _circ_call(tools, count=1, angular_spacing=0.0)
    assert "CLOCKWISE" not in reply


# ───────────────────────────── user_pattern: the count that is ignored

class _Point:
    def __init__(self, name):
        self.Name = name


class _Elements:
    def __init__(self, names):
        self.names = names
        self.Count = len(names)

    def Item(self, index):
        return _Point(self.names[index - 1])


class _Sketch:
    def __init__(self, names, name="草图.3"):
        self.Name = name
        self.GeometricElements = _Elements(names)


class _UserPattern:
    Name = "用户阵列.1"

    def AddFeatureToLocatePositions(self, sketch):
        self.sketch = sketch


class _UserFactory:
    def AddNewUserPattern(self, feature, count):
        self.count = count
        return _UserPattern()


class _UserPart:
    def __init__(self):
        self.ShapeFactory = _UserFactory()
        self.InWorkObject = None


def _user_tools(names=("绝对轴", "点.1", "点.2", "点.3")):
    from catia_mcp.tools.part_design import PartDesignTools

    part = _UserPart()
    sketch = _Sketch(list(names))
    tools = PartDesignTools(connection=None)
    tools.conn = type("C", (), {
        "ensure_connected": staticmethod(lambda: None),
        "get_active_part": staticmethod(lambda: part),
        "refresh_display": staticmethod(lambda: None),
        "hso": None,
    })()
    tools._prepare_body = lambda p: "BODY"
    tools._pattern_seed = lambda body, name: "SEED"
    tools._get_last_sketch = lambda name: sketch
    tools._body_volume_mm3 = lambda p, body: 59000.0
    tools._safe_update = lambda p, pattern: None
    return tools, part


def test_the_reply_counts_the_points_not_the_ignored_argument():
    tools, _ = _user_tools()
    reply = tools.execute("catia_user_pattern",
                          {"count": 5, "sketch_name": "草图.3"})
    # Three points and the sketch's own absolute axis make four elements: the
    # axis is not a locating point, and counting it would put the reply one out.
    assert "3 copies placed on the 3 point(s)" in reply
    assert "plus the original" in reply
    assert "CATIA ignores 'count'" in reply
    assert "not 5" in reply


def test_a_matching_count_gets_no_warning():
    tools, _ = _user_tools()
    reply = tools.execute("catia_user_pattern",
                          {"count": 3, "sketch_name": "草图.3"})
    assert "3 copies" in reply
    assert "ignores 'count'" not in reply


def test_an_unreadable_sketch_says_nothing_it_cannot_prove():
    class _NoElements:
        def __init__(self):
            self.Count = 2

        def Item(self, index):
            raise RuntimeError("cannot read")

    tools, _ = _user_tools()
    tools._get_last_sketch = lambda name: type("S", (), {
        "Name": "草图.3", "GeometricElements": _NoElements()})()
    reply = tools.execute("catia_user_pattern",
                          {"count": 4, "sketch_name": "草图.3"})
    assert "instances placed on the points" in reply
    assert "copies" not in reply.split("Volume")[0]
    assert "does not decide it" in reply
