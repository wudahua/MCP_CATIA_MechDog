"""0.3.164: building a feature at a chosen place in the history.

The 2026-11-23 report (P1-4) wanted one feature moved out of fifteen and got
`catia_reorder_feature`'s measured "no" instead. But an INSERTION point exists -
0.3.163 measured that CATIA places a raw feature after whatever `InWorkObject`
points at - and this server was the thing in front of it: `_prepare_body` puts the
in-work object back on the body before every factory call, so every feature was
appended.

These tests pin the four claims the reply makes, because each one can be false:

* "Placed at position 2 of 3" is a READING of the tree, not a promise - a feature
  can add exactly the right material and still land at the end;
* an insertion rebuilds the whole part, and `UpdateObject` alone does NOT show its
  effect (measured: 87738.0533 -> 85476.1066 only after `part.Update()`);
* a rollback after a failed insertion has to say whether the PART is whole again,
  which is a different question from whether the feature came out;
* `insert_after` naming something that is not a feature of the body is refused
  before the factory runs.
"""

from __future__ import annotations

import pytest

from catia_mcp.results import ArgumentRejected, CodedError, FeatureRejected


class _Collection:
    def __init__(self, items: list) -> None:
        self._items = list(items)
        self.Count = len(self._items)

    def Item(self, index: int):
        return self._items[index - 1]


class _Shape:
    def __init__(self, name: str) -> None:
        self.Name = name
        self.DirectionOrientation = 0

    @property
    def FirstLimit(self):
        return type("_Limit", (), {"LimitMode": 0, "Dimension": _Dim()})()

    @property
    def SecondLimit(self):
        return type("_Limit", (), {"LimitMode": 0, "Dimension": _Dim()})()


class _Dim:
    Value = 0.0


class _Bodies:
    def __init__(self, items: list) -> None:
        self._items = items
        self.Count = len(items)

    def Item(self, index: int):
        return self._items[index - 1]


class _Body:
    def __init__(self, name: str, shapes: list[_Shape]) -> None:
        self.Name = name
        self.Shapes = _Collection(shapes)


class _Part:
    """A part whose history the fake factory can insert INTO or append TO."""

    def __init__(self, body: _Body, mode: str = "insert") -> None:
        self.MainBody = body
        self.Bodies = _Bodies([body])
        self.mode = mode
        self.InWorkObject = body
        self.updates: list[str] = []
        self.fail_update: Exception | None = None
        self.ShapeFactory = _Factory(self)

    def Update(self) -> None:
        self.updates.append("part")
        if self.fail_update is not None:
            raise self.fail_update

    def UpdateObject(self, feature) -> None:  # noqa: ANN001 - duck typed
        self.updates.append(f"object:{feature.Name}")


class _Factory:
    """Adds a shape either after the in-work object (CATIA) or at the end."""

    def __init__(self, part: _Part) -> None:
        self.part = part
        self.counter = 0

    def AddNewPad(self, sketch, height):  # noqa: ANN001, N802 - CATIA name
        return self._add(sketch, f"凸台.{len(self.part.MainBody.Shapes._items) + 1}")

    def AddNewPocket(self, sketch, depth):  # noqa: ANN001, N802 - CATIA name
        return self._add(sketch, f"凹槽.{len(self.part.MainBody.Shapes._items) + 1}")

    def _add(self, sketch, stem: str):  # noqa: ANN001
        name = stem
        shape = _Shape(name)
        items = self.part.MainBody.Shapes._items
        anchor = self.part.InWorkObject
        if (self.part.mode == "insert" and anchor in items
                and items.index(anchor) < len(items) - 1):
            items.insert(items.index(anchor) + 1, shape)
        else:
            items.append(shape)
        self.part.MainBody.Shapes.Count = len(items)
        return shape


class _Sketch:
    def __init__(self, name: str, volume: float) -> None:
        self.Name = name
        self.volume = volume


def _tools(shapes=("凸台.1", "凹槽.1"), mode="insert"):
    from catia_mcp.tools.part_design import PartDesignTools

    body = _Body("零件几何体", [_Shape(name) for name in shapes])
    part = _Part(body, mode=mode)
    tools = PartDesignTools(connection=None)
    tools.conn = _Conn(part)
    return tools, part, body


class _Conn:
    def __init__(self, part: _Part) -> None:
        self.part = part
        self.open_sketch = None

    def ensure_connected(self) -> None:
        pass

    def get_active_part(self) -> _Part:
        return self.part

    def get_active_part_body(self) -> _Body:
        return self.part.MainBody

    def refresh_display(self) -> None:
        pass


# ── the plan: what the tree says about the insertion point ───────────────


def test_insert_plan_reads_the_position_and_the_tail():
    tools, _part, body = _tools(("凸台.1", "凹槽.1", "凸台.2"))
    plan = tools._insert_plan(tools.conn.part, body, "凹槽.1")
    assert plan["name"] == "凹槽.1"
    assert plan["index"] == 1
    assert plan["downstream"] == ["凸台.2"], (
        "the tail is what has to be rebuilt, so it has to be named")


def test_no_insert_after_means_the_old_append_behaviour():
    tools, _part, body = _tools()
    assert tools._insert_plan(tools.conn.part, body, None) is None
    assert tools._insert_plan(tools.conn.part, body, "") is None


def test_a_name_that_is_not_a_feature_lists_the_body_in_build_order():
    tools, _part, body = _tools(("凸台.1", "凹槽.1", "凸台.2"))
    with pytest.raises(ArgumentRejected) as caught:
        tools._insert_plan(tools.conn.part, body, "凸台.9")
    assert caught.value.code == "FEATURE_NOT_FOUND"
    text = str(caught.value)
    assert "凸台.1 (1), 凹槽.1 (2), 凸台.2 (3)" in text
    assert caught.value.data["features"] == ["凸台.1", "凹槽.1", "凸台.2"]


def test_a_feature_of_another_body_is_named_where_it_was_found():
    """'Not found' about a name three bodies over is true and useless."""
    from catia_mcp.tools.part_design import PartDesignTools

    here = _Body("零件几何体", [_Shape("凸台.1")])
    there = _Body("几何体.2", [_Shape("凸台.2")])
    part = _Part(here)
    part.Bodies = _Bodies([here, there])
    tools = PartDesignTools(connection=None)
    tools.conn = _Conn(part)
    with pytest.raises(ArgumentRejected) as caught:
        tools._insert_plan(part, here, "凸台.2")
    assert "几何体.2" in str(caught.value)
    assert caught.value.data["found_in"] == ["几何体.2"]


# ── the update: an insertion cannot be judged by UpdateObject ────────────


def test_an_insertion_rebuilds_the_whole_part_and_an_append_does_not():
    tools, part, _body = _tools()
    plan = {"target": part.MainBody.Shapes.Item(1), "name": "凸台.1",
            "index": 0, "downstream": ["凹槽.1"]}
    tools._update_placed(part, _Shape("x"), plan)
    assert part.updates == ["part"], (
        "UpdateObject does not recompute the features after an inserted node - "
        "measured on B30: the volume only moved after a full Update")
    part.updates.clear()
    tools._update_placed(part, _Shape("y"), None)
    assert part.updates == ["object:y"]


# ── the verdict: the tree is the arbiter, not the volume ─────────────────


def test_placed_where_asked_is_read_back_from_the_tree():
    tools, part, body = _tools()
    new = _Shape("凹槽.2")
    body.Shapes._items.insert(1, new)          # right after 凸台.1
    body.Shapes.Count = len(body.Shapes._items)
    reply = tools._verify_insertion(
        part, body, [new],
        {"target": body.Shapes.Item(1), "name": "凸台.1", "index": 0,
         "downstream": ["凹槽.1"]})
    assert "Placed at position 2 of 3" in reply
    assert "REBUILT by a full part.Update()" in reply
    assert "凹槽.1" in reply, "the rebuilt tail has to be named"


def test_a_feature_that_landed_at_the_end_is_a_failure_even_though_it_built():
    """The volume cannot tell an insertion from an append; the tree can."""
    tools, part, body = _tools()
    new = _Shape("凹槽.2")
    body.Shapes._items.append(new)             # appended, not inserted
    body.Shapes.Count = len(body.Shapes._items)
    with pytest.raises(CodedError) as caught:
        tools._verify_insertion(
            part, body, [new],
            {"target": body.Shapes.Item(1), "name": "凸台.1", "index": 0,
             "downstream": ["凹槽.1"]})
    assert caught.value.code == "INSERT_POSITION_WRONG"
    assert caught.value.modified is True, (
        "the solid is real material the caller asked for - deleting it because a "
        "sentence would otherwise be false is the worse answer")
    assert "凸台.1, 凹槽.1, 凹槽.2" in str(caught.value)
    assert caught.value.data["wanted_position"] == 2


def test_a_pair_of_features_is_checked_together():
    """A mid-plane cut inserts TWO features; both have to be in place."""
    tools, part, body = _tools(("凸台.1", "凸台.2"))
    one, two = _Shape("凹槽.1"), _Shape("凹槽.2")
    items = body.Shapes._items
    items.insert(1, one)
    items.insert(1, two)                       # [凸台.1, 凹槽.2, 凹槽.1, 凸台.2]
    body.Shapes.Count = len(items)
    reply = tools._verify_insertion(
        part, body, [two, one],
        {"target": items[0], "name": "凸台.1", "index": 0,
         "downstream": ["凹槽.1", "凸台.2"]})
    assert "Placed at positions 2-3 of 4" in reply


# ── the rollback: "the feature came out" is not "the part is whole" ──────


def test_a_failed_insertion_says_whether_the_part_updates_again():
    tools, part, body = _tools()
    tools._volume_probe = lambda p, b: (100.0, None)      # type: ignore[assignment]
    tools._sketch_present = lambda part, sketch: True     # type: ignore[assignment]
    tools._recover_lost_sketch = (                               # type: ignore[assignment]
        lambda *a, **k: ("", True))
    tools._remove_feature = lambda p, f, b=None: True     # type: ignore[assignment]
    with pytest.raises(FeatureRejected) as caught:
        tools._insert_rejected(
            part, body, _Shape("凹槽.2"), _Sketch("H", 1.0),
            {"target": body.Shapes.Item(1), "name": "凸台.1", "index": 0,
             "downstream": ["凹槽.1"]},
            RuntimeError("update failed"), 100.0, 100.0)
    assert caught.value.code == "INSERTION_REJECTED"
    assert caught.value.rolled_back is True
    assert caught.value.modified is False
    assert "the part updates again" in str(caught.value)
    assert caught.value.data["downstream"] == ["凹槽.1"]


def test_a_failed_insertion_that_leaves_the_part_broken_is_not_a_clean_refusal():
    """rolled_back=true with a part that no longer rebuilds is the lie to avoid."""
    tools, part, body = _tools()
    tools._volume_probe = lambda p, b: (100.0, None)      # type: ignore[assignment]
    tools._sketch_present = lambda part, sketch: True     # type: ignore[assignment]
    tools._recover_lost_sketch = (                               # type: ignore[assignment]
        lambda *a, **k: ("", True))
    tools._remove_feature = lambda p, f, b=None: True     # type: ignore[assignment]
    part.fail_update = RuntimeError("凹槽.1 will not rebuild")
    monkey = pytest.MonkeyPatch()
    monkey.setattr("catia_mcp.tools.part_design.failing_elements",
                   lambda p: ["凹槽.1"])
    try:
        with pytest.raises(FeatureRejected) as caught:
            tools._insert_rejected(
                part, body, _Shape("凹槽.2"), _Sketch("H", 1.0),
                {"target": body.Shapes.Item(1), "name": "凸台.1", "index": 0,
                 "downstream": ["凹槽.1"]},
                RuntimeError("update failed"), 100.0, 100.0)
    finally:
        monkey.undo()
    assert caught.value.rolled_back is False
    assert caught.value.modified is True
    assert "THE PART IS STILL IN ERROR" in str(caught.value)
    assert "'凹槽.1'" in str(caught.value)
    assert caught.value.data["still_failing"] == ["凹槽.1"]


# ── the schemas: the argument has to be offered, and explained ───────────


def test_the_three_feature_tools_offer_insert_after():
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    by_name = {tool["name"]: tool for tool in tools.get_tool_definitions()}
    for name in ("catia_pad", "catia_pocket", "catia_hole"):
        schema = by_name[name]["inputSchema"]["properties"]
        assert "insert_after" in schema, name
        assert "part.Update()" in schema["insert_after"]["description"], name
