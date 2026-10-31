"""0.3.42 - report 2026-10-30: a circular pattern that placed copies but never turned them.

The report's measurement: an 8-copy pattern around X of a 400 mm bar gives
Y +-500 and Z +-305. Eight copies of one body at 45 degrees must be 8-fold
symmetric, so Z should have been +-500; +-305 is exactly what
`p -> p + (R*c - c)` produces, i.e. every copy translated to its angle and left
parallel to the seed. The volume was 8x the seed either way, which is why the
tool's own documentation ("the only trustworthy signal that the instances were
actually built") could not catch it.

Reproduced on B30 through the tool, and the cause is the last argument of
AddNewCircPattern - CATIA's "radius aligned" flag, which is what applies the
angle to the INSTANCE BODY rather than only to its position:

    radius_aligned=false   Y +-500   Z +-305
    radius_aligned=true    Y +-500   Z +-500

The default is now true; the flag is exposed for the one case where
translation-only is what you want (a ring of identical bosses).
"""

import json

import pytest


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


def _tools():
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
    return tools, part


def _call(tools, **extra):
    arguments = {"axis": "x", "center": [0.0, 0.0, 0.0], "count": 8}
    arguments.update(extra)
    reply = tools.execute("catia_circ_pattern", arguments)
    return reply, tools.conn.get_active_part().ShapeFactory.calls[-1]


def test_the_last_argument_of_the_pattern_call_rotates_the_copies():
    tools, part = _tools()
    reply, call = _call(tools)
    # (seed, radial, angular, radial step, angular step, radial pos, angular pos,
    #  centre, axis, reversed, rotation angle, radius aligned)
    assert call[0] == "SEED"
    assert call[1] == 1 and call[2] == 8
    assert call[4] == pytest.approx(45.0)
    assert call[-1] is True, "radius_aligned must default to True: it is the flag " \
                            "that makes a copy turn with the pattern"
    assert "rotated with the pattern" in reply


def test_translation_only_is_available_and_says_so():
    tools, _ = _tools()
    reply, call = _call(tools, radius_aligned=False)
    assert call[-1] is False
    assert "NOT rotated" in reply
    assert "radius_aligned=true" in reply


def test_the_schema_advertises_the_flag_with_true_as_default():
    from catia_mcp.tools.part_design import PartDesignTools

    schema = [d for d in PartDesignTools(connection=None).get_tool_definitions()
              if d["name"] == "catia_circ_pattern"][0]
    prop = schema["inputSchema"]["properties"]["radius_aligned"]
    assert prop["type"] == "boolean"
    assert prop["default"] is True
    # The description must stop claiming the volume proves the orientation.
    assert "nothing about their orientation" in schema["description"]
