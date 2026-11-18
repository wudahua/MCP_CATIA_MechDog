"""2026-11-17 report, item 4: a frame sketch's scaffolding outlives it.

The report created 20+ frame sketches and the tree filled up with hidden datum
planes and three construction points each. The measurement behind this change
(B30 2026-11-18, probe v0454) is that NOTHING CAN BE ASKED WHO OWNS WHAT:

* ``Sketch.Plane`` and ``Sketch.Support`` do not exist ('Add.Plane');
* ``Sketch.AbsoluteAxis`` answers no GetAxisData / Origin;
* the plane from ``AddNewPlane3Points`` has no FirstPoint / SecondPoint /
  ThirdPoint ('Item.FirstPoint');
* the three points come out of ``AddNewPointCoord`` unnamed, as 点.1..点.3;
* deleting the plane first and the points afterwards works and the update takes;
  the sketch is still in the tree after both.

So the fix is a NAME - '<sketch>_FRAME' and '<sketch>_FRAME.O/.U/.V' - written
by ``catia_create_sketch`` once it knows the sketch's name, and read by
``catia_delete_feature(cleanup_frame_datum=true)``. What is pinned here, all
without CATIA: the name is written and read back, the delete order is plane
first, a name that only LOOKS like scaffolding is left alone, and a sketch from
before this change is reported instead of guessed at.
"""
from __future__ import annotations

import pytest

from catia_mcp import sketch_frames
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.sketcher import SketcherTools


class _Node:
    def __init__(self, name: str):
        self.Name = name

    def __repr__(self) -> str:
        return f"<{self.Name}>"


class _Collection:
    def __init__(self, items=()):
        self._items = list(items)

    @property
    def Count(self) -> int:
        return len(self._items)

    def Item(self, index: int):
        return self._items[index - 1]

    def remove(self, node) -> None:
        self._items.remove(node)

    def names(self) -> list[str]:
        return [str(node.Name) for node in self._items]


class _Body:
    def __init__(self, hybrid=()):
        self.HybridShapes = _Collection(hybrid)
        self.Shapes = _Collection()
        self.Sketches = _Collection()


class _Bodies:
    def __init__(self, bodies):
        self._items = list(bodies)

    @property
    def Count(self) -> int:
        return len(self._items)

    def Item(self, index: int):
        return self._items[index - 1]


class _Selection:
    """Records every Delete() as one batch of names, in order."""

    def __init__(self):
        self.current: list = []
        self.batches: list[list[str]] = []

    def Clear(self) -> None:
        self.current = []

    def Add(self, node) -> None:
        self.current.append(node)

    def Delete(self) -> None:
        self.batches.append([str(node.Name) for node in self.current])
        self.current = []


class _Part:
    def __init__(self, body: _Body):
        self.Bodies = _Bodies([body])
        self.updated = 0

    def Update(self) -> None:
        self.updated += 1

    def UpdateObject(self, _node) -> None:
        pass


class _Connection:
    def __init__(self, part: _Part, body: _Body, selection: _Selection):
        self._part = part
        self._body = body
        self._selection = selection

    def get_active_part(self):
        return self._part

    def get_active_part_body(self):
        return self._body

    @property
    def hso(self):
        return self._selection


def _tools(nodes: list[str]):
    body = _Body([_Node(name) for name in nodes])
    part = _Part(body)
    selection = _Selection()
    return (PartDesignTools(_Connection(part, body, selection)), part, body,
            selection)


# ── 1. the scaffolding is found by name and removed plane first ───────────
def test_the_datum_goes_before_its_points():
    tools, part, body, selection = _tools(
        ["草图.6_FRAME", "草图.6_FRAME.O", "草图.6_FRAME.U", "草图.6_FRAME.V",
         "草图.7_FRAME", "草图.7_FRAME.O", "草图.7_FRAME.U", "草图.7_FRAME.V"])
    text = tools._delete_frame_scaffold("草图.6")
    assert "removed the frame scaffolding" in text
    assert selection.batches[0] == ["草图.6_FRAME"], (
        "the plane is the points' definition - deleting the points first leaves "
        "an update that cannot succeed")
    assert sorted(selection.batches[1]) == ["草图.6_FRAME.O", "草图.6_FRAME.U",
                                            "草图.6_FRAME.V"]
    # the neighbouring frame is untouched: four elements, not eight
    assert sum(len(batch) for batch in selection.batches) == 4
    assert "草图.7_FRAME" in body.HybridShapes.names()
    assert part.updated == 1


def test_a_numbered_datum_from_a_retry_is_still_this_sketchs():
    # _free_feature_name hands out '<base>.2' when the name is taken, and the
    # points then hang off '.2'.
    tools, _part, _body, selection = _tools(
        ["草图.6_FRAME.2", "草图.6_FRAME.2.O", "草图.6_FRAME.2.U"])
    text = tools._delete_frame_scaffold("草图.6")
    assert "removed the frame scaffolding" in text
    assert selection.batches[0] == ["草图.6_FRAME.2"]
    assert sorted(selection.batches[1]) == ["草图.6_FRAME.2.O", "草图.6_FRAME.2.U"]


def test_a_name_that_only_looks_like_scaffolding_is_left_alone():
    # 'Datum_frame.R' is neither the plane nor one of the three points. A name
    # this call does not understand is not a licence to delete it.
    tools, _part, body, selection = _tools(
        ["草图.6_FRAME", "草图.6_FRAME.O", "草图.6_FRAME.R"])
    text = tools._delete_frame_scaffold("草图.6")
    assert selection.batches[0] == ["草图.6_FRAME"]
    assert selection.batches[1] == ["草图.6_FRAME.O"]
    assert "草图.6_FRAME.R" in body.HybridShapes.names()
    assert "Left alone" in text and "草图.6_FRAME.R" in text


# ── 2. absent scaffolding is reported, never guessed ──────────────────────
def test_a_sketch_from_before_the_naming_is_reported_with_its_candidates():
    tools, _part, _body, selection = _tools(
        ["Datum_frame", "点.1", "点.2", "点.3"])
    text = tools._delete_frame_scaffold("草图.6")
    assert "could not be identified" in text
    assert "before 0.3.146" in text
    assert "Datum_frame" in text, "the caller has to see what is actually there"
    assert selection.batches == [], "guessing which datum to delete would break "
    assert "catia_delete_feature(names=[...])" in text


def test_a_batch_over_plain_sketches_stays_quiet():
    tools, _part, _body, _selection = _tools(["Datum_frame"])
    assert tools._delete_frame_scaffold("草图.6", quiet=True) == ""


# ── 3. the name is written by create_sketch, and read back ────────────────
def test_the_scaffolding_takes_the_sketch_name():
    plane = _Node("Datum_frame")
    points = [_Node("点.1"), _Node("点.2"), _Node("点.3")]
    body = _Body([plane] + points)
    part = _Part(body)
    sketcher = SketcherTools(_Connection(part, body, _Selection()))
    sketcher._sketch_part = part
    sketcher._last_frame_scaffold = {"plane": plane, "points": points}
    final = sketcher._rename_frame_scaffold(_Node("草图.6"))
    assert final == "草图.6_FRAME"
    assert str(plane.Name) == "草图.6_FRAME"
    assert [str(point.Name) for point in points] == [
        "草图.6_FRAME.O", "草图.6_FRAME.U", "草图.6_FRAME.V"]
    # the record is consumed: a second sketch must not rename these again
    assert sketcher._last_frame_scaffold is None


def test_the_reply_quotes_the_name_catia_kept_not_the_one_we_asked_for():
    class _Unyielding(_Node):
        """Keeps its own name - CATIA can accept the assignment and ignore it."""

        def __init__(self, name: str = "Datum_frame"):
            self.__dict__["_locked"] = False
            super().__init__(name)

        def __setattr__(self, key, value):
            if key == "Name" and self.__dict__.get("_locked"):
                raise RuntimeError("Property 'Name' can not be set")
            super().__setattr__(key, value)

    stubborn = _Unyielding()
    stubborn._locked = True
    body = _Body([stubborn])
    part = _Part(body)
    sketcher = SketcherTools(_Connection(part, body, _Selection()))
    sketcher._sketch_part = part
    sketcher._last_frame_scaffold = {"plane": stubborn, "points": []}
    assert sketcher._rename_frame_scaffold(_Node("草图.6")) == "Datum_frame", (
        "the read-back value is what the reply and the later delete must use")


def test_delete_feature_offers_the_parameter_and_says_why():
    definitions = {tool["name"]: tool
                   for tool in PartDesignTools(None).get_tool_definitions()}
    schema = definitions["catia_delete_feature"]["inputSchema"]["properties"]
    assert "cleanup_frame_datum" in schema
    # 0.3.147: three-state, because "always" and "never" are both wrong - see
    # tests below, which pin the case the default has to stop at.
    assert schema["cleanup_frame_datum"].get("default") == "auto"
    assert schema["cleanup_frame_datum"].get("enum") == [True, False, "auto"]
    text = definitions["catia_delete_feature"]["description"]
    assert "cleanup_frame_datum defaults to" in text
    assert "NOT its children" in text


def _scaffold_tools(remaining: list[str]):
    body = _Body([_Node("FRAME_A_FRAME"), _Node("FRAME_A_FRAME.O"),
                  _Node("FRAME_A_FRAME.U"), _Node("FRAME_A_FRAME.V")])
    body.Sketches = _Collection([_Node(name) for name in remaining])
    part = _Part(body)
    selection = _Selection()
    return PartDesignTools(_Connection(part, body, selection)), body, selection


def test_auto_cleans_up_when_it_was_the_last_sketch():
    tools, _body, selection = _scaffold_tools([])
    text = tools._cleanup_after_sketch("FRAME_A", "auto")
    assert "removed the frame scaffolding" in text
    assert selection.batches[0] == ["FRAME_A_FRAME"], "plane first, then points"


class _DummyApp:
    """Stands in for the CATIA Application - only read_absolute_axes sees it."""


def _stub_axes(monkeypatch, systems: dict[str, tuple], tools=None):
    """Make read_absolute_axes answer from a table instead of CATIA."""
    if tools is not None:
        # The route needs conn.app before it can be stubbed; the fake connection
        # has none, which is exactly the "unknown, so do not delete" path.
        tools.conn.app = _DummyApp()

    def fake(_app, sketches):
        answer = []
        for sketch in sketches:
            system = systems.get(str(sketch.Name))
            if system is None:
                return None
            answer.append(system)
        return answer

    monkeypatch.setattr("catia_mcp.sketch_frames.read_absolute_axes", fake)


# 1000 mm up, u = +X, v = +Y - the array B30 answers for a frame at (0, 0, 1000).
ON_1000 = ([0.0, 0.0, 1000.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0])
ON_ZERO = ([0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0])
PLANE_1000 = sketch_frames.plane_signature(ON_1000)


def test_auto_stops_when_another_sketch_sits_on_the_same_plane(monkeypatch):
    # MEASURED on B30 2026-11-18 (probe v0457): a second sketch built ON the
    # datum by name survives the delete, then reads 需要修改绝对轴定义 and the part
    # update fails. A sketch that stands on the datum lies on its plane, and
    # GetAbsoluteAxisData is how this build answers where a sketch sits.
    tools, body, selection = _scaffold_tools(["ON_FRAME"])
    _stub_axes(monkeypatch, {"ON_FRAME": ON_1000}, tools)
    text = tools._cleanup_after_sketch("FRAME_A", "auto", plane=PLANE_1000)
    assert "left alone" in text
    assert "ON_FRAME" in text, "the reply has to name what it is protecting"
    assert "SAME plane" in text
    assert selection.batches == []
    assert "FRAME_A_FRAME" in body.HybridShapes.names()


def test_auto_cleans_up_when_the_other_sketches_are_on_their_own_planes(monkeypatch):
    # THE REPORT'S OWN PROJECT (2026-11-18, P2): P02/P05/P06 hold 5-8 frame
    # sketches, each with its own _FRAME, so "are there other sketches" was
    # always true and auto never fired. "Are they on MY plane" is the question
    # that has an answer.
    tools, body, selection = _scaffold_tools(["SK_1", "SK_2", "SK_3"])
    _stub_axes(monkeypatch, {"SK_1": ON_ZERO, "SK_2": ON_ZERO,
                             "SK_3": ON_ZERO}, tools)
    text = tools._cleanup_after_sketch("FRAME_A", "auto", plane=PLANE_1000)
    assert "removed the frame scaffolding" in text
    assert selection.batches[0] == ["FRAME_A_FRAME"]


def test_auto_refuses_when_where_the_other_sketches_sit_cannot_be_read():
    # The fake connection has no .app, so the axis route cannot run. Unknown is
    # not "none of them": nothing is deleted.
    tools, body, selection = _scaffold_tools(["ON_FRAME"])
    text = tools._cleanup_after_sketch("FRAME_A", "auto", plane=PLANE_1000)
    assert "NOT removed" in text
    assert "could not be decided" in text
    assert selection.batches == []
    assert "FRAME_A_FRAME" in body.HybridShapes.names()


def test_auto_does_nothing_when_there_is_no_scaffolding_to_remove():
    tools, _body, selection = _scaffold_tools(["ON_FRAME"])
    body = _Body([_Node("UNRELATED")])
    tools = PartDesignTools(_Connection(_Part(body), body, _Selection()))
    assert tools._cleanup_after_sketch("FRAME_A", "auto",
                                       plane=PLANE_1000) == ""


def test_true_forces_the_cleanup_and_names_the_risk():
    tools, _body, selection = _scaffold_tools(["ON_FRAME"])
    text = tools._cleanup_after_sketch("FRAME_A", True)
    assert "removed the frame scaffolding" in text
    assert "Forced cleanup" in text and "shared support" in text
    assert selection.batches[0] == ["FRAME_A_FRAME"]


def test_false_leaves_everything_alone():
    tools, body, selection = _scaffold_tools([])
    assert tools._cleanup_after_sketch("FRAME_A", False) == ""
    assert selection.batches == []
    assert "FRAME_A_FRAME" in body.HybridShapes.names()


def test_the_frame_reply_names_the_delete_route():
    definitions = {tool["name"]: tool
                   for tool in SketcherTools(None).get_tool_definitions()}
    import inspect
    frame = inspect.getsource(SketcherTools._frame_plane)
    assert "cleanup_frame_datum=true" in frame
    # The note is written before the sketch exists, so it quotes the provisional
    # name; _create_sketch renames the four elements and rewrites the sentence.
    assert "PROVISIONAL" in frame
    create = inspect.getsource(SketcherTools._create_sketch)
    assert "_rename_frame_scaffold" in create
    assert "where = where.replace(provisional, final)" in create
    assert "catia_create_sketch" in definitions
