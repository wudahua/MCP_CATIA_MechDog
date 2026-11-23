"""0.3.165: the three findings of the 2026-11-23 (0.3.164) report.

The report opened its part, measured it, and got a truthful answer about the
wrong solid. Three separate mechanisms did that, and each one is pinned here:

* `catia_move_body` built the moved solid in a NEW body and left the session on
  the SOURCE, so the next `catia_get_inertia` answered the PRE-move geometry
  (P1-N1: centroid x = 10 after a +50 mm move) and `catia_list_bodies` totalled
  both solids (6000 + 6000 = 12000);
* a plan's rollback listed four frame elements as "left" and, in the same reply,
  the delete said no such names existed - because the frame scaffolding lives in
  `body.HybridShapes` and the delete lookup never entered it (P2-N2);
* `catia_reorder_feature` pointed at `catia_move_body` for a wrong POSITION
  without its cost, which on a 15-feature part is a dead solid (P2-N3).
"""

from __future__ import annotations

import pytest

from catia_mcp.results import ArgumentRejected, FeatureRejected


# ── a body that holds GSD elements and sketches of its own ───────────────


class _Collection:
    def __init__(self, items: list | None = None) -> None:
        self._items = list(items or [])
        self.Count = len(self._items)

    def Item(self, index: int):
        return self._items[index - 1]

    def insert(self, index: int, item) -> None:  # noqa: ANN001 - test helper
        self._items.insert(index, item)
        self.Count = len(self._items)


class _Node:
    def __init__(self, name: str, show: int = 0) -> None:
        self.Name = name
        self.show = show


class _Body:
    def __init__(self, name: str = "零件几何体", shapes=(), sketches=(),
                 hybrids=(), show: int = 0) -> None:
        self.Name = name
        self.show = show
        self.Shapes = _Collection(shapes)
        self.Sketches = _Collection(sketches)
        self.HybridShapes = _Collection(hybrids)
        self.HybridSketches = _Collection()

    @property
    def Count(self) -> int:  # noqa: D401 - the fake's collection-of-bodies cost
        return len(self.Shapes._items)


class _Bodies:
    def __init__(self, items: list[_Body]) -> None:
        self._items = items
        self.Count = len(items)

    def Item(self, index: int):
        return self._items[index - 1]


class _Selection:
    def __init__(self) -> None:
        self.selected: list = []
        self.deleted: list = []

    def Clear(self) -> None:
        self.selected = []

    def Add(self, node) -> None:  # noqa: ANN001
        self.selected.append(node)

    def Delete(self) -> None:
        self.deleted.extend(self.selected)

    @property
    def VisProperties(self):
        selection = self

        class _Visibility:
            def GetShow(self_inner):  # noqa: N805 - duck typed
                # (0, 1) = hidden, (0, 0) = shown - the build's own convention,
                # which is why `show` on the fake node is "1 means hidden".
                node = selection.selected[-1] if selection.selected else None
                return (0, getattr(node, "show", 0))

        return _Visibility()


class _Part:
    def __init__(self, bodies: list[_Body]) -> None:
        self.Bodies = _Bodies(bodies)
        self.MainBody = bodies[0]
        self.HybridBodies = _Collection()
        self.updated = 0
        self.InWorkObject = bodies[0]

    def Update(self) -> None:
        self.updated += 1

    def CreateReferenceFromObject(self, node):  # noqa: N802 - CATIA name
        return f"ref:{getattr(node, 'Name', node)}"


class _Conn:
    def __init__(self, part: _Part) -> None:
        self.part = part
        self.hso = _Selection()
        self.active_body = None
        self.active_document = None

    def ensure_connected(self) -> None:
        pass

    def get_active_part(self) -> _Part:
        return self.part

    def get_active_part_body(self) -> _Body:
        return self.active_body if self.active_body is not None else self.part.MainBody

    def refresh_display(self) -> None:
        pass


def _tools(bodies: list[_Body] | None = None):
    from catia_mcp.tools.part_design import PartDesignTools

    part = _Part(bodies or [_Body()])
    tools = PartDesignTools(connection=None)
    tools.conn = _Conn(part)
    return tools, part


# ── P1-N1: the session follows the moved solid ──────────────────────────


def test_the_move_switches_the_active_body_and_reads_it_back():
    tools, part = _tools([_Body("零件几何体"), _Body("零件几何体_MOVED")])
    moved = part.Bodies.Item(2)
    note = tools._activate_body_after_move(part, moved)
    assert tools.conn.active_body is moved
    assert part.InWorkObject is moved
    assert "The ACTIVE body is now '零件几何体_MOVED'" in note
    assert "PRE-move centre of gravity" in note, (
        "the sentence has to say what the old behaviour cost, or the next "
        "reader re-introduces it")


def test_a_move_that_cannot_switch_says_what_to_call_instead():
    """Switching can fail; then the reply must not imply the reading is current."""
    tools, part = _tools([_Body("零件几何体"), _Body("零件几何体_MOVED")])
    moved = part.Bodies.Item(2)

    def refuse(_body):  # noqa: ANN001
        raise RuntimeError("InWorkObject refused")

    class _Stuck(_Conn):
        def get_active_part_body(self):
            return self.part.MainBody

    tools.conn = _Stuck(part)
    note = tools._activate_body_after_move(part, moved)
    assert "STILL" in note
    assert "catia_set_active_body(name='零件几何体_MOVED')" in note


# ── P1-N1: a hidden body's volume is not the part's volume ───────────────


def test_list_bodies_separates_visible_from_hidden_volume():
    source = _Body("零件几何体", shapes=[_Node("凸台.1")], show=1)      # hidden
    moved = _Body("零件几何体_MOVED", shapes=[_Node("封闭曲面.1")], show=0)
    tools, part = _tools([source, moved])

    class _Spa:
        """6e-6 m3 = 6000 mm3 per body, whichever one is asked for."""

        def GetMeasurable(self, reference):  # noqa: N802 - CATIA name
            return type("_M", (), {"Volume": 6e-6})()

    tools._topo_measure = lambda _part: _Spa()                            # type: ignore
    import json
    payload = json.loads(tools._list_bodies())
    assert payload["total_volume_mm3"] == 12000.0
    assert payload["visible_volume_mm3"] == 6000.0
    assert payload["hidden_volume_mm3"] == 6000.0
    assert [entry["visible"] for entry in payload["bodies"]] == [False, True]
    assert "HIDDEN" in payload["volume_note"]


# ── P2-N2: the frame scaffolding is in the body's own HybridShapes ───────


def test_an_element_in_a_body_hybrid_shapes_is_found_and_deleted():
    """The lookup that never entered body.HybridShapes - the report's "no such
    name" about four elements sitting in the tree."""
    frame = _Node("T5_Empty_FRAME")
    point = _Node("T5_Empty_FRAME.O")
    body = _Body(hybrids=[point, frame])
    tools, part = _tools([body])
    found, kind = tools._find_in_body_hybrids(body, "T5_Empty_FRAME")
    assert found is frame and kind == "GSD element"
    found, kind = tools._find_in_body_hybrids(body, "T5_Empty_FRAME.O")
    assert found is point
    # a name that is not there is (None, "") and not an exception
    assert tools._find_in_body_hybrids(body, "nope") == (None, "")


def test_a_sketch_inside_a_body_hybrid_sketches_is_a_sketch():
    """Kind matters: the open-sketch guard only applies to ``kind == "sketch"``."""
    sketch = _Node("S_in_set")
    body = _Body()
    body.HybridSketches = _Collection([sketch])
    tools, _part = _tools([body])
    found, kind = tools._find_in_body_hybrids(body, "S_in_set")
    assert found is sketch and kind == "sketch"


# ── P2-N2: the plane goes before its own points ─────────────────────────


def test_a_frame_plane_is_ordered_before_its_own_points():
    tools, _part = _tools()
    tools._profile_map = lambda: ({}, [])                                # type: ignore
    ordered = tools._batch_order(["T5_FRAME.O", "T5_FRAME.U",
                                  "T5_FRAME.V", "T5_FRAME"])
    assert ordered[0] == "T5_FRAME", ordered
    # ... and the plane does NOT block itself: its own name has no dot, so
    # "T5_FRAME".rsplit(".",1)[0] is "T5_FRAME" - the first version read that as
    # "blocked by itself" and fell back to the caller's order.
    assert len(ordered) == 4


def test_a_frame_plane_goes_after_the_sketch_it_supports_in_the_same_batch():
    """The sketch has to be gone first, or the rebuild has no support."""
    tools, _part = _tools()
    tools._profile_map = lambda: ({}, [])                                # type: ignore
    ordered = tools._batch_order(["T5_FRAME", "T5", "T5_FRAME.O"])
    assert ordered == ["T5", "T5_FRAME", "T5_FRAME.O"], ordered


def test_ordinary_names_keep_their_order():
    tools, _part = _tools()
    tools._profile_map = lambda: ({}, [])                                # type: ignore
    names = ["倒圆角.1", "凹槽.2", "凸台.1"]
    assert tools._batch_order(names) == names


def test_a_frame_plane_and_its_points_are_deleted_in_two_passes():
    """One selection with both leaves the part unable to update (measured)."""
    plane = _Node("T5_FRAME")
    point = _Node("T5_FRAME.O")
    other = _Node("P_07")
    body = _Body(hybrids=[plane, point, other])
    tools, part = _tools([body])
    tools._map_geometrical_sets = (                                      # type: ignore
        lambda p, wanted: {name: [node] for name, node in
                           (("T5_FRAME", plane), ("T5_FRAME.O", point),
                            ("P_07", other)) if name in wanted})
    removed, kept = tools._delete_owned(["T5_FRAME", "T5_FRAME.O", "P_07"])
    assert set(removed) == {"T5_FRAME", "T5_FRAME.O", "P_07"}
    assert kept == []
    # P_07 first (its own pass), then the plane, then the point - and an update
    # happened between the plane and the point.
    order = [node.Name for node in tools.conn.hso.deleted]
    assert order.index("T5_FRAME") < order.index("T5_FRAME.O"), order
    assert part.updated >= 2, "the plane's pass has to rebuild before the points go"


def test_a_frame_plane_whose_sketch_is_alive_is_refused():
    frame = _Node("T5_FRAME")
    sketch = _Node("T5")
    body = _Body(sketches=[sketch], hybrids=[frame])
    tools, _part = _tools([body])
    with pytest.raises(ArgumentRejected) as caught:
        tools._delete_one({"feature_name": "T5_FRAME"}, "T5_FRAME")
    assert caught.value.code == "FRAME_DATUM_IN_USE"
    assert "T5" in str(caught.value)
    assert tools.conn.hso.deleted == [], "nothing was deleted"


def test_frame_role_knows_planes_from_points_and_leaves_other_names_alone():
    from catia_mcp.tools.part_design import PartDesignTools

    assert PartDesignTools._frame_role("T5_FRAME") == "plane"
    assert PartDesignTools._frame_role("T5_FRAME.2") == "plane"
    assert PartDesignTools._frame_role("T5_FRAME.O") == "point"
    assert PartDesignTools._frame_role("T5_FRAME.2.V") == "point"
    assert PartDesignTools._frame_role("EdgeFillet.1") is None
    assert PartDesignTools._frame_role("T5_FRAME_odd") is None


# ── P2-N2: the inventory says when the owner sketch is gone ─────────────


def test_frame_scaffolding_flags_an_owner_whose_sketch_is_gone():
    body = _Body(sketches=[_Node("A")],
                 hybrids=[_Node("A_FRAME"), _Node("B_FRAME"),
                          _Node("B_FRAME.O")])
    tools, _part = _tools([body])
    inventory = tools._frame_scaffold_inventory(body)
    assert inventory["sketches"] == [
        {"sketch": "A", "elements": 1, "sketch_present": True},
        {"sketch": "B", "elements": 2, "sketch_present": False}]
    assert inventory["orphans"] == ["B"]
    assert "GONE" in inventory["orphans_note"]
    assert "catia_delete_feature" in inventory["orphans_note"]


def test_an_unreadable_sketch_list_says_nothing_about_presence():
    body = _Body(hybrids=[_Node("A_FRAME")])

    class _Broken(_Body):
        @property
        def Sketches(self):  # noqa: N802 - the fake's member name
            raise RuntimeError("no sketch list")

        @Sketches.setter
        def Sketches(self, value):  # noqa: N802 - accepted and dropped
            pass

    broken = _Broken(hybrids=[_Node("A_FRAME")])
    tools, _part = _tools([body])
    inventory = tools._frame_scaffold_inventory(broken)
    assert "sketch_present" not in inventory["sketches"][0], (
        "an unreadable list must not turn into 'the sketch is gone'")
    assert "orphans" not in inventory


# ── P2-N3: the reorder advice is a branch, not a suggestion ─────────────


def test_the_reorder_advice_names_all_three_routes_and_the_cost():
    import inspect

    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools.get_tool_definitions)
    assert "DEAD SOLID" in source, (
        "pointing at catia_move_body for a wrong position without saying it "
        "produces a dead solid is what the report (P2-N3) caught")
    assert "insert_after" in source
    assert "a wrong DEPTH or size -> catia_edit_pad" in source
