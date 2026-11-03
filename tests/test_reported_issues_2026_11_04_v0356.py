# -*- coding: utf-8 -*-
"""0.3.56 - the surface fillet and the spine, which 0.3.53 declared unusable.

0.3.53 wrote two things into README:

    AddNewFilletBiTangent(...) updates for one orientation pair out of four ...
    so what it builds is unverified - the UI's Edge Fillet and this automation
    object are not the same thing.
    AddNewSpine() takes no parameters at all and the typelib names no way to
    give it sections, so it cannot be driven.

Both sentences were measured on B30 2026-11-03 and both turned out wrong, in the
same way: a declared call was tried once, in one arrangement, and its failure was
read as a property of the object.

THE FILLET. The unverified part is now verified. With the walls hidden so the STL
carried the fillet alone (scripts/b30_v0356_fillet_geom.py, _fillet_scope.py):

    90 deg wedge, r=5, 40 mm edge   every vertex 5.0000 mm from the rolling-ball
                                    axis (min = max), area 314.159 mm2 = (pi/2)rL
    60 deg / 120 deg, r=5           radius min = max = 5.0000, areas 418.879 and
                                    209.440 mm2 = (pi - theta) r L
    floor z=0 + wall x=0, r=5       radius min = max = 5.0000, area 471.239 exact

The working pair was (-1, +1) every time, including for a corner built in a
different direction - so it is the call, not the build order. But the same call
between a disc and a cylinder standing on it built TWO surfaces, (+1,+1) 573.4
mm2 and (+1,-1) 391.3 mm2, which is why the tool refuses when more than one
orientation survives instead of picking one.

THE SPINE. HybridShapeSpine declares 40 members, AddSection among them. The
0.3.53 probe listed dir(spine) filtered for "Add"/"ection", which for a
late-bound COM object returns almost nothing. Measured: sections go in, the count
moves, UpdateObject SUCCEEDS with two or more (one section fails), and the curve
is the spine - normal to the sections, not through their centres. Three parallel
rings 40 mm apart gave 40.0000 mm and did not bend for a 30 mm sideways offset; a
45 deg tilted second ring turned it into a 37.9224 mm curve leaving that axis by
7.05 mm at z=10.

What is asserted here is the behaviour that keeps those findings from rotting: the
search, the refusals, the cleanup. The geometry itself cannot be asserted without
CATIA, so it is recorded in the docstrings and measured by the scripts above.
"""

from __future__ import annotations

import pytest


# ───────────── a connection stub that records what the search did

class _Selection:
    """The selection object CATIA deletes through."""

    def __init__(self):
        self.added = []
        self.deleted = 0
        self.cleared = 0

    def Clear(self):                       # noqa: N802 - COM spelling
        self.cleared += 1

    def Add(self, item):                   # noqa: N802
        self.added.append(item)

    def Delete(self):                      # noqa: N802
        self.deleted += 1


class _Geoset:
    def __init__(self):
        self.appended = []
        self.Name = "GSD_Set"

    def AppendHybridShape(self, shape):    # noqa: N802
        self.appended.append(shape)


def _tools(behaviour):
    """GSDTools wired to fakes; ``behaviour(o1, o2)`` decides each attempt.

    ``behaviour`` returns "ok" or an exception to raise.
    """
    from catia_mcp.tools.gsd import GSDTools

    selection = _Selection()
    geoset = _Geoset()
    attempts = []
    completions = []

    class _Shape:
        def __init__(self, o1, o2):
            self.o1 = o1
            self.o2 = o2
            self.Name = ""

    class _Factory:
        def AddNewFilletBiTangent(self, ref1, ref2, radius, o1, o2,  # noqa: N802
                                  trim, relim):
            attempts.append((o1, o2, trim, relim))
            return _Shape(o1, o2)

    class _Part:
        def UpdateObject(self, shape):     # noqa: N802
            outcome = behaviour(shape.o1, shape.o2)
            if outcome != "ok":
                raise outcome

    tools = GSDTools(connection=None)
    tools.conn = type("C", (), {
        "ensure_connected": staticmethod(lambda: None),
        "get_active_part": staticmethod(lambda: _Part()),
        "hso": selection,
    })()
    tools._ref = lambda name: f"ref:{name}"
    tools._factory = lambda: _Factory()
    tools._get_geoset = lambda: geoset

    def _completed(shape, name, message, target):
        completions.append((shape, name, message))
        if name:
            shape.Name = name
        return message

    tools._completed = _completed

    return tools, {"selection": selection, "geoset": geoset,
                   "attempts": attempts, "completed": completions}


# ───────────── 1. the fillet searches, and keeps the one CATIA accepted

def test_all_four_orientations_are_tried_and_trim_stays_zero():
    """Only one pair survives on a real corner, and it is not predictable."""
    tools, log = _tools(lambda o1, o2: "ok" if (o1, o2) == (-1, 1) else
                        RuntimeError("方法 UpdateObject 失败"))
    reply = tools.execute("catia_gsd_fillet_surface",
                          {"surface1": "A", "surface2": "B", "radius": 5.0})
    assert log["attempts"] == [(1, 1, 0, 0), (-1, 1, 0, 0),
                               (1, -1, 0, 0), (-1, -1, 0, 0)]
    assert "o1=-1, o2=+1" in reply
    assert "not trimmed" in reply.lower() or "NOT trimmed" in reply
    assert len(log["completed"]) == 1


def test_every_losing_attempt_is_deleted_immediately():
    """A failed fillet left in the tree makes every later part.Update() fail."""
    tools, log = _tools(lambda o1, o2: RuntimeError("失败"))
    with pytest.raises(RuntimeError):
        tools.execute("catia_gsd_fillet_surface",
                      {"surface1": "A", "surface2": "B", "radius": 5.0})
    assert log["selection"].deleted == 4
    assert log["geoset"].appended and len(log["geoset"].appended) == 4


def test_zero_survivors_names_the_arrangement_that_fails():
    """Two disjoint parallel cylinders failed all nine radius/orientation pairs."""
    tools, _log = _tools(lambda o1, o2: RuntimeError("失败"))
    with pytest.raises(RuntimeError) as caught:
        tools.execute("catia_gsd_fillet_surface",
                      {"surface1": "C1", "surface2": "C2", "radius": 5.0})
    text = str(caught.value)
    assert "all four orientations" in text
    assert "CORNER" in text
    assert "catia_fillet" in text


def test_two_surviving_orientations_are_refused_not_picked():
    """Disc + standing cylinder: 573.4 mm2 and 391.3 mm2 are different surfaces."""
    from catia_mcp.results import ArgumentRejected

    tools, log = _tools(lambda o1, o2: "ok" if o1 == 1 else RuntimeError("失败"))
    with pytest.raises(ArgumentRejected) as caught:
        tools.execute("catia_gsd_fillet_surface",
                      {"surface1": "DISC", "surface2": "BOSS", "radius": 5.0})
    text = str(caught.value)
    assert "2 orientations" in text
    assert "573.4" in text and "391.3" in text
    # Two built and two failed; all four are removed, so the part is unchanged.
    assert log["selection"].deleted == 4, "every attempt has to be removed"
    assert getattr(caught.value, "stage", None) == "validate"


def test_radius_must_be_positive():
    from catia_mcp.results import ArgumentOutOfRange

    tools, log = _tools(lambda o1, o2: "ok")
    with pytest.raises(ArgumentOutOfRange):
        tools.execute("catia_gsd_fillet_surface",
                      {"surface1": "A", "surface2": "B", "radius": 0})
    assert log["attempts"] == []


# ───────────── 2. the spine is driven by AddSection, and counts what it kept

def _spine_tools(keep_sections=None, keep_guides=None):
    from catia_mcp.tools.gsd import GSDTools

    calls = {"sections": [], "guides": [], "completed": [], "dropped": []}

    class _Spine:
        def __init__(self):
            self.Name = ""
            self._sections = 0
            self._guides = 0

        def AddSection(self, ref):         # noqa: N802
            calls["sections"].append(ref)
            self._sections += 1

        def AddGuide(self, ref):           # noqa: N802
            calls["guides"].append(ref)
            self._guides += 1

        def GetNumberOfSections(self):     # noqa: N802
            return self._sections if keep_sections is None else keep_sections

        def GetNumberOfGuides(self):       # noqa: N802
            return self._guides if keep_guides is None else keep_guides

    spine = _Spine()

    class _Factory:
        def AddNewSpine(self):             # noqa: N802
            return spine

    class _Geoset:
        Name = "GSD_Set"

        def AppendHybridShape(self, shape):  # noqa: N802
            pass

    tools = GSDTools(connection=None)
    tools.conn = type("C", (), {
        "ensure_connected": staticmethod(lambda: None),
        "get_active_part": staticmethod(lambda: object()),
    })()
    tools._ref = lambda name: f"ref:{name}"
    tools._factory = lambda: _Factory()
    tools._get_geoset = lambda: _Geoset()

    def completed(shape, name, message, target):
        calls["completed"].append(message)
        return message

    tools._completed = completed
    tools._drop = lambda shape: calls["dropped"].append(shape)
    return tools, calls, spine


def test_every_section_is_added_and_counted():
    tools, calls, _spine = _spine_tools()
    reply = tools.execute("catia_gsd_spine",
                          {"sections": ["S1", "S2", "S3"], "name": "SP"})
    assert calls["sections"] == ["ref:S1", "ref:S2", "ref:S3"]
    assert "3 sections" in reply
    assert "NORMAL to the sections" in reply


def test_guides_are_added_and_counted():
    tools, calls, _spine = _spine_tools()
    tools.execute("catia_gsd_spine", {"sections": ["S1", "S2"], "guides": ["G"]})
    assert calls["guides"] == ["ref:G"]


def test_one_section_is_refused_before_catia_is_touched():
    """A spine with one section fails at UpdateObject - so never create it."""
    from catia_mcp.results import ArgumentRejected

    tools, calls, _spine = _spine_tools()
    with pytest.raises(ArgumentRejected) as caught:
        tools.execute("catia_gsd_spine", {"sections": ["S1"]})
    assert getattr(caught.value, "stage", None) == "validate"
    assert calls["sections"] == []
    assert calls["dropped"] == []


def test_a_dropped_section_leaves_nothing_behind():
    """AddSection can return and keep nothing; healing taught that lesson."""
    tools, calls, _spine = _spine_tools(keep_sections=1)
    with pytest.raises(RuntimeError) as caught:
        tools.execute("catia_gsd_spine", {"sections": ["S1", "S2"]})
    assert "kept 1 of the 2 sections" in str(caught.value)
    assert calls["dropped"], "the half-built spine must not stay in the tree"


def test_a_dropped_guide_is_reported():
    tools, calls, _spine = _spine_tools(keep_guides=0)
    with pytest.raises(RuntimeError):
        tools.execute("catia_gsd_spine", {"sections": ["S1", "S2"], "guides": ["G"]})
    assert calls["dropped"]


# ───────────── 3. both tools say in their schema what they were measured to do

def _described(name):
    from catia_mcp.tools.gsd import GSDTools

    described = [tool for tool in GSDTools(connection=None).get_tool_definitions()
                 if tool["name"] == name]
    assert len(described) == 1
    return described[0]


def test_the_fillet_schema_carries_the_measurement_and_the_refusal():
    tool = _described("catia_gsd_fillet_surface")
    text = tool["description"]
    assert "AddNewFilletBiTangent" in text
    assert "314.159" in text, "the measured band area is the evidence it is real"
    assert "rolling-ball" in text
    assert "MORE THAN ONE" in text.upper()
    assert "trim=1" in text
    assert tool["inputSchema"]["required"] == ["surface1", "surface2", "radius"]


def test_the_spine_schema_corrects_0353_and_names_the_calls():
    tool = _described("catia_gsd_spine")
    text = tool["description"]
    assert "AddSection" in text
    assert "40" in text, "the member count is what made 0.3.53 read it as absent"
    assert "37.9224" in text
    assert "CORRECTION" in text.upper()
    assert tool["inputSchema"]["properties"]["sections"]["minItems"] == 2


# ───────────── 4. README no longer claims what 0.3.53 could not measure

def test_readme_says_the_call_does_build_a_real_fillet():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "README.md").read_text(
        encoding="utf-8")
    known = text.split("## Known limits", 1)[1]
    assert "No surface-to-surface fillet" not in known, (
        "0.3.53's blanket 'no fillet' is now measured to be wrong: the (-1,+1) "
        "call builds a true rolling-ball fillet on a straight corner")
    assert "catia_gsd_fillet_surface" in known
    assert "catia_gsd_spine" in known


def test_readme_no_longer_says_the_spine_cannot_be_driven():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "README.md").read_text(
        encoding="utf-8")
    assert "the typelib names no way to give it sections" not in text, (
        "HybridShapeSpine declares AddSection; the 0.3.53 probe looked at "
        "dir(spine) and concluded the interface had no such member")
