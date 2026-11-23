"""0.3.163 - catia_reorder_feature: the plan, then the measured refusal.

The 2026-11-23 report asked for a way to move a feature that ended up in the
wrong place in a 15-feature history. V5 has no such member - so the tool's job is
to be honest in two ways at once:

  * it does the work of understanding the call (names validated, current
    position, target position, the order that WOULD have resulted), because
    "not supported" without the plan is a shrug; and
  * it refuses in 0 ms with modified: false and the measurements behind it,
    instead of asking CATIA for something that cannot work.

The tests pin the parts a "just say no" would get wrong: an unknown name is a
NOT-FOUND (with the list), a target in another body is its own answer, asking
for the order that already exists is a success and not a refusal, and force: true
must report what the interface declares NOW - including the branch where an
ordering member has appeared, which would make the verdict false.
"""

from __future__ import annotations

import types

import pytest

from catia_mcp.results import CodedError, PreconditionRejected
from catia_mcp.tools.part_design import PartDesignTools


# ───────────── a tiny COM that advertises whatever members a test wants

class _Info:
    def __init__(self, names: list[str]) -> None:
        self.names = names

    def GetTypeAttr(self):
        return types.SimpleNamespace(cFuncs=len(self.names))

    def GetFuncDesc(self, index: int):
        return types.SimpleNamespace(memid=index)

    def GetNames(self, memid: int):
        return [self.names[memid]]


class _Ole:
    def __init__(self, names: list[str]) -> None:
        self.info = _Info(names)

    def GetTypeInfo(self):
        return self.info


class _Shape:
    def __init__(self, name: str) -> None:
        self.Name = name
        self._oleobj_ = _Ole(["Name", "Sketch", "FirstLimit"])


class _Shapes:
    def __init__(self, names: list[str]) -> None:
        self.items = [_Shape(name) for name in names]
        self._oleobj_ = _Ole(["Item", "Count", "_NewEnum", "GetBoundary", "Parent"])

    @property
    def Count(self) -> int:
        return len(self.items)

    def Item(self, index: int) -> _Shape:
        return self.items[index - 1]


class _Body:
    def __init__(self, name: str, shapes: list[str]) -> None:
        self.Name = name
        self.Shapes = _Shapes(shapes)
        self._oleobj_ = _Ole(["Name", "Shapes", "InsertHybridShape"])


class _Bodies:
    def __init__(self, bodies: list[_Body]) -> None:
        self.items = bodies

    @property
    def Count(self) -> int:
        return len(self.items)

    def Item(self, index) -> _Body:
        return self.items[index - 1]


class _Part:
    def __init__(self, bodies: dict[str, list[str]] | None = None) -> None:
        pairs = bodies or {"零件几何体": ["凸台.1", "凹槽.1", "凸台.2"]}
        self.Name = "REORDER_TEST"
        self.Bodies = _Bodies([_Body(name, shapes)
                               for name, shapes in pairs.items()])
        self.updated: list[object] = []
        self._oleobj_ = _Ole(["Bodies", "Name", "UpdateObject"])

    def UpdateObject(self, shape: object) -> None:
        self.updated.append(shape)


class _Selection:
    def __init__(self) -> None:
        self._oleobj_ = _Ole(["Add", "Clear", "Copy", "Paste", "Delete",
                              "VisProperties"])


class _Conn:
    def __init__(self, part: _Part) -> None:
        self.part = part
        self._hso = _Selection()

    def ensure_connected(self) -> None:
        return None

    def get_active_part(self) -> _Part:
        return self.part

    @property
    def hso(self) -> _Selection:
        return self._hso


def _tools(bodies: dict[str, list[str]] | None = None
           ) -> tuple[PartDesignTools, _Part]:
    part = _Part(bodies)
    return PartDesignTools(connection=_Conn(part)), part


# ───────────── 1. it is offered, with the report's arguments

def test_reorder_is_offered_and_takes_a_place_to_go():
    schemas = {tool["name"]: tool for tool in PartDesignTools(connection=None)
               .get_tool_definitions()}
    assert "catia_reorder_feature" in schemas
    schema = schemas["catia_reorder_feature"]["inputSchema"]
    assert schema["required"] == ["feature"]
    for key in ("before", "after", "index", "last", "force", "document"):
        assert key in schema["properties"], f"{key} is missing"


# ───────────── 2. the name is checked against the tree, not assumed

def test_an_unknown_feature_lists_what_the_body_holds():
    tools, part = _tools()
    with pytest.raises(CodedError) as caught:
        tools._reorder_feature({"feature": "倒圆角.61", "index": 1})
    assert caught.value.code == "FEATURE_NOT_FOUND"
    assert caught.value.modified is False
    assert "凸台.1, 凹槽.1, 凸台.2" in str(caught.value), (
        "the list is the point: feature names drift, so 'not found' has to say "
        "what IS there")
    assert part.updated == []


def test_a_target_in_another_body_is_its_own_answer():
    tools, _part = _tools({"零件几何体": ["凸台.1"], "辅助": ["凸台.9"]})
    with pytest.raises(PreconditionRejected) as caught:
        tools._reorder_feature({"feature": "凸台.1", "after": "凸台.9"})
    assert caught.value.code == "TARGET_IN_OTHER_BODY"
    assert "辅助" in str(caught.value)


# ───────────── 3. the arguments are validated before the verdict

def test_no_target_is_refused_as_a_bad_argument():
    tools, _part = _tools()
    with pytest.raises(CodedError) as caught:
        tools._reorder_feature({"feature": "凹槽.1"})
    assert caught.value.code == "BAD_ARGUMENT"
    assert "nothing was changed" in str(caught.value).lower()


def test_two_targets_are_refused_rather_than_guessed():
    tools, _part = _tools()
    with pytest.raises(CodedError) as caught:
        tools._reorder_feature({"feature": "凹槽.1", "index": 1, "last": True})
    assert caught.value.code == "BAD_ARGUMENT"
    assert "index" in str(caught.value) and "last" in str(caught.value)


def test_an_index_outside_the_body_is_out_of_range():
    tools, _part = _tools()
    with pytest.raises(CodedError) as caught:
        tools._reorder_feature({"feature": "凹槽.1", "index": 9})
    assert caught.value.code == "INDEX_OUT_OF_RANGE"
    assert "1..3" in str(caught.value)


# ───────────── 4. asking for the order that already exists is not a failure

def test_asking_for_the_current_order_is_a_success_not_a_refusal():
    tools, _part = _tools()
    reply = tools._reorder_feature({"feature": "凸台.1", "index": 1})
    assert "already at position 1 of 3" in reply
    assert "Nothing was changed" in reply


def test_moving_a_feature_before_its_own_successor_is_also_a_no_op():
    """'before 凹槽.1' for 凸台.1 is where it already is - not a move."""
    tools, _part = _tools()
    reply = tools._reorder_feature({"feature": "凸台.1", "before": "凹槽.1"})
    assert "already at position 1 of 3" in reply


# ───────────── 5. the refusal carries the plan

def test_the_refusal_reports_both_positions_and_the_resulting_order():
    tools, part = _tools()
    with pytest.raises(CodedError) as caught:
        tools._reorder_feature({"feature": "凸台.2", "index": 1})
    error = caught.value
    assert error.code == "UNSUPPORTED_CAPABILITY"
    assert error.modified is False, (
        "nothing was sent to CATIA, so claiming a modification would be a lie")
    message = str(error)
    assert "position 3 -> 1 of 3" in message
    assert "凸台.1, 凹槽.1, 凸台.2 -> 凸台.2, 凸台.1, 凹槽.1" in message
    assert "no Insert" in message, "the member list is the evidence"
    assert "APPENDED" in message, "paste is the obvious workaround; say why not"
    assert error.data["order"] == ["凸台.1", "凹槽.1", "凸台.2"]
    assert error.data["resulting_order"] == ["凸台.2", "凸台.1", "凹槽.1"]
    assert error.data["moved"] is False
    assert part.updated == [], "a refusal must not touch the model"


def test_the_refusal_offers_the_three_routes_that_do_work():
    tools, _part = _tools()
    with pytest.raises(CodedError) as caught:
        tools._reorder_feature({"feature": "凹槽.1", "last": True})
    actions = " ".join(caught.value.next_actions)
    assert "catia_delete_feature" in actions
    assert "catia_edit_pad" in actions and "catia_move_body" in actions


# ───────────── 6. force: what does the interface declare NOW?

def test_force_reports_no_ordering_member_and_says_the_verdict_stands():
    tools, part = _tools()
    reply = tools._reorder_feature({"feature": "凹槽.1", "index": 1,
                                    "force": True})
    assert "Body.Shapes: 5 member(s)" in reply
    assert "cannot be moved on this build" in reply
    assert part.updated == []


def test_insert_hybrid_shape_is_named_and_excluded_not_counted():
    """'insert' is in its name and it is not an ordering control.

    The first version of the hint list counted it, and the force report then
    said "an ordering member now exists" - the exact opposite of the measurement
    (InsertHybridShape refuses a solid feature, 0.3.162). A name that matches a
    keyword is not a capability.
    """
    tools, _part = _tools()
    reply = tools._reorder_feature({"feature": "凹槽.1", "index": 1,
                                    "force": True})
    assert "Body: 3 member(s), ordering: NONE (not InsertHybridShape:" in reply
    assert "refuses a solid" in reply
    assert "an existing feature cannot be moved on this build" in reply


def test_a_name_that_merely_CONTAINS_the_letters_is_not_an_ordering_member():
    """Remove/Remove2 contain "move", OrderedGeometricalSets contains "order".

    Both exist on the real interfaces (Selection and Body, measured on B30
    2026-11-23), and substring matching read them as capability - the force
    report then claimed "an ordering member now exists" about a build that has
    none. Only a whole CamelCase word counts.
    """
    tools, part = _tools()
    part.Bodies.Item(1).Shapes._oleobj_ = _Ole(
        ["Item", "Count", "Remove", "Remove2", "OrderedGeometricalSets"])
    reply = tools._reorder_feature({"feature": "凹槽.1", "index": 1,
                                    "force": True})
    assert "Body.Shapes: 5 member(s), ordering: NONE" in reply
    assert "cannot be moved on this build" in reply


def test_force_notices_when_an_ordering_member_has_appeared():
    """If a licence/release adds one, the verdict must stop being repeated."""
    tools, part = _tools()
    part.Bodies.Item(1).Shapes._oleobj_ = _Ole(
        ["Item", "Count", "_NewEnum", "GetBoundary", "MoveFeature"])
    reply = tools._reorder_feature({"feature": "凹槽.1", "index": 1,
                                    "force": True})
    assert "MoveFeature" in reply
    assert "may have changed" in reply, (
        "a member that exists but is not implemented must be reported as such, "
        "not silently treated as 'still unavailable'")
    assert "no code path" in reply
    assert part.updated == []
