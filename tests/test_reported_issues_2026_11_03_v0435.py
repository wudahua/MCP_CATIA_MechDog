"""0.3.133 - the sheet-metal parameters, and the wall that still is not there.

The 2026-11-03 report asked for eight sheet-metal tools and got none. Re-measured
on B30 2026-11-15, two of the eight are reachable and six are not:

    reachable      read/write thickness, bend radius, K factor, relief slots
                   (`part.Parameters` - and `Value` there IS writable)
                   a new document that already carries the set
    not reachable  wall, flange, bend, unfold, flat pattern - `SheetMetalFactory`
                   has 23 members and no `AddNew*` at all

What these tests hold is the half that does not need CATIA: the parameter lookup
(which is a NAME match, because the leaves are localised), the refusal paths, and
the one ordering rule that already went wrong in this release - the reply must
quote the value read back AFTER the writes, not the one seen before them.
"""

from __future__ import annotations

import json

import pytest

from catia_mcp import sheetmetal
from catia_mcp.results import ArgumentRejected, PreconditionRejected
from catia_mcp.tools.sheetmetal import SheetMetalTools

DOC = "Demo"
CONTAINER = DOC + "\\钣金参数.1"
RULE = CONTAINER + "\\DIN 一般公式\\Activity"


# ── fakes: only what the tool actually reads ─────────────────────────────────


class _Param:
    """A parameter. ``locked_by`` reproduces the DIN rule on the K factor."""

    def __init__(self, name: str, value, locked_by: "_Param | None" = None) -> None:
        self.Name = name
        self._value = value
        self.locked_by = locked_by
        self.writes = 0

    @property
    def Value(self):  # noqa: N802 - the COM name
        return self._value

    @Value.setter
    def Value(self, value):  # noqa: N802
        if self.locked_by is not None and bool(self.locked_by.Value):
            raise RuntimeError("(-2147352567, '发生意外。', (0, 'CATIARealParam', "
                               "'方法 Value 失败', None, 0, -2147467259), None)")
        self._value = value
        self.writes += 1


class _Collection:
    def __init__(self, items: list) -> None:
        self._items = items

    @property
    def Count(self):  # noqa: N802
        return len(self._items)

    def Item(self, index: int):  # noqa: N802
        return self._items[index - 1]


class _SheetMetalParameters:
    def __init__(self, part: "_Part") -> None:
        self._part = part

    def GetThickness(self):  # noqa: N802
        return self._part.thickness().Value


class _Factory:
    def __init__(self, part: "_Part") -> None:
        self._part = part
        self.calls = 0

    def CreateSheetMetalParameters(self):  # noqa: N802
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("(-2147352567, '发生意外。', (0, "
                               "'CATIASheetMetalFactory', "
                               "'方法 CreateSheetMetalParameters 失败'...))")
        self._part.add_set()
        return _SheetMetalParameters(self._part)


class _Part:
    def __init__(self) -> None:
        self.Name = DOC
        self.Parameters = _Collection([])
        self.SheetMetalFactory = _Factory(self)
        self.SheetMetalParameters = _SheetMetalParameters(self)
        self.updates = 0
        self.readable = False

    # the set appears only after the factory ran, exactly as on B30
    def add_set(self) -> None:
        activity = _Param(RULE, True)
        relief_rule = _Param(CONTAINER + "\\CornerReliefRadiusFormula\\Activity", True)
        k_factor = _Param(CONTAINER + "\\K 因子", 0.400515, locked_by=activity)
        self.Parameters = _Collection([
            _Param(CONTAINER + "\\线宽", 2.0),
            _Param(CONTAINER + "\\弯曲半径", 4.0),
            k_factor,
            _Param(CONTAINER + "\\拐角止裂槽半径长度", 1.0),
            _Param(CONTAINER + "\\拐角止裂槽轴长度", 2.0),
            _Param(CONTAINER + "\\Minimum Bend Radius", 0.0),
            activity,
            relief_rule,
            _Param(DOC + "\\零件编号", "Demo"),
        ])
        self.readable = True

    def thickness(self) -> _Param:
        return self.find("线宽")

    def find(self, leaf: str) -> _Param:
        for index in range(1, self.Parameters.Count + 1):
            parameter = self.Parameters.Item(index)
            if parameter.Name.endswith("\\" + leaf):
                return parameter
        raise KeyError(leaf)

    def Update(self):  # noqa: N802
        self.updates += 1


class _Document:
    def __init__(self, name: str = "Demo.CATPart") -> None:
        self.Name = name


class _Conn:
    def __init__(self, part: _Part) -> None:
        self.part = part
        self.connected = 0

    def ensure_connected(self):
        self.connected += 1
        return "connected"

    @property
    def active_document(self):
        return _Document()

    def get_active_part(self):
        return self.part


class _StrictConn:
    """Fails loudly if the tool reaches CATIA before the argument is checked."""

    def ensure_connected(self):
        raise AssertionError("ensure_connected ran before the argument check")

    @property
    def active_document(self):
        raise AssertionError("the document was read before the argument check")

    def get_active_part(self):
        raise AssertionError("the part was read before the argument check")


class _Documents:
    def __init__(self, part: _Part) -> None:
        self.part = part
        self.calls: list[tuple[str, dict]] = []

    def execute(self, tool: str, arguments: dict) -> str:
        # catia_new_part does NOT add the sheet-metal set - the factory has to,
        # and that is the whole difference between catia_new_part and this tool.
        self.calls.append((tool, dict(arguments)))
        return f"Created {arguments.get('name')}"


def _tools(part: _Part | None = None, documents=None):
    part = part or _Part()
    conn = _Conn(part)
    return SheetMetalTools(conn, documents), part, conn


# ── the lookup: a name match, because the leaves are localised ───────────────


def test_the_container_is_the_one_that_holds_the_most_recognised_leaves():
    names = [
        "Other\\Thickness",              # a stray elsewhere: one match only
        CONTAINER + "\\线宽",
        CONTAINER + "\\弯曲半径",
        CONTAINER + "\\K 因子",
        "Doc\\零件编号",
    ]
    located = sheetmetal.locate(names)
    assert located["container"] == CONTAINER
    assert located["found"]["thickness"] == CONTAINER + "\\线宽"
    assert located["found"]["bend_radius"] == CONTAINER + "\\弯曲半径"
    assert "relief_radius" in located["missing"]


def test_a_chinese_leaf_and_its_english_spelling_are_the_same_property():
    """'K 因子' on this build, 'KFactor' on another - one property, not two."""
    assert sheetmetal.property_of("K 因子") == "k_factor"
    assert sheetmetal.property_of("KFactor") == "k_factor"
    assert sheetmetal.property_of("k-factor") == "k_factor"
    assert sheetmetal.property_of("弯曲半径") == "bend_radius"
    assert sheetmetal.property_of("Bend Radius") == "bend_radius"
    # NOT a prefix match: the minimum is a different number, not a spelling.
    assert sheetmetal.property_of("Minimum Bend Radius") == "minimum_bend_radius"


def test_no_match_is_reported_as_nothing_rather_than_an_empty_success():
    located = sheetmetal.locate(["Doc\\零件编号", "Doc\\术语"])
    assert located["container"] is None
    assert located["found"] == {}
    assert set(located["missing"]) == set(sheetmetal.PROPERTIES)


# ── arguments are refused before a session is opened ────────────────────────


@pytest.mark.parametrize("arguments", [{"thickness": -1}, {"k_factor": 5},
                                       {"bend_radius": "thick"},
                                       {"thickness": True}])
def test_a_bad_value_is_refused_without_touching_catia(arguments):
    tools = SheetMetalTools.__new__(SheetMetalTools)
    tools.conn = _StrictConn()
    tools._documents = None
    with pytest.raises(ArgumentRejected):
        tools.execute("catia_sheetmetal_parameters", arguments)


def test_a_new_sheetmetal_with_a_bad_thickness_creates_nothing():
    part = _Part()
    documents = _Documents(part)
    tools = SheetMetalTools(_StrictConn(), documents)
    with pytest.raises(ArgumentRejected):
        tools.execute("catia_new_sheetmetal", {"name": "X", "thickness": 0})
    assert documents.calls == []


# ── the refusals that read as states, not typos ─────────────────────────────


def test_a_part_without_the_set_says_which_switch_creates_it():
    tools, _part, _conn = _tools()
    with pytest.raises(PreconditionRejected) as error:
        tools.execute("catia_sheetmetal_parameters", {})
    assert error.value.code == "NO_SHEETMETAL_PARAMETERS"
    assert "create=true" in str(error.value)
    assert "catia_new_sheetmetal" in str(error.value)


def test_create_is_needed_once_and_a_second_ask_is_a_no_op():
    tools, part, _conn = _tools()
    first = json.loads(tools.execute("catia_sheetmetal_parameters", {"create": True}))
    second = json.loads(tools.execute("catia_sheetmetal_parameters", {"create": True}))
    assert first["created"] is True and second["created"] is False
    assert part.SheetMetalFactory.calls == 1, "a second set must not be created"


# ── writing, and reading back what was written ──────────────────────────────


def test_a_write_quotes_the_value_read_back_after_it():
    """The defect this release shipped for a few minutes: the cross-check before it.

    `cross_check` was computed before the write loop, so a call that set the
    thickness to 1.5 reported `get_thickness_mm: 2.0` right beside `after: 1.5`.
    Both halves of the reply have to come from the same moment.
    """
    tools, _part, _conn = _tools()
    tools.execute("catia_sheetmetal_parameters", {"create": True})
    report = json.loads(tools.execute("catia_sheetmetal_parameters",
                                      {"thickness": 1.5}))
    entry = report["writes"][0]
    assert entry["before"] == 2.0 and entry["after"] == 1.5
    assert entry["written"] is True
    assert report["parameters"]["thickness"]["value"] == 1.5
    assert report["cross_check"] == {"get_thickness_mm": 1.5, "agrees": True}


def test_a_locked_k_factor_is_written_by_switching_off_the_rule_and_says_so():
    tools, part, _conn = _tools()
    tools.execute("catia_sheetmetal_parameters", {"create": True})
    report = json.loads(tools.execute("catia_sheetmetal_parameters",
                                      {"k_factor": 0.5}))
    entry = report["writes"][0]
    assert entry["written"] is True and entry["after"] == 0.5
    assert entry["unlocked"]["switched_off"] == ["DIN 一般公式"]
    assert part.find("DIN 一般公式\\Activity").Value is False
    assert any("DIN 一般公式" in note for note in report["notes"])


def test_a_bound_parameter_that_the_write_could_not_land_is_reported_not_hidden():
    """An assignment that does not raise and does not stick is still a failure."""
    tools, part, _conn = _tools()
    tools.execute("catia_sheetmetal_parameters", {"create": True})

    class _Stubborn(_Param):
        @_Param.Value.setter
        def Value(self, value):  # noqa: N802
            pass                       # accepted, ignored - the quiet kind

    names = [part.Parameters.Item(i) for i in range(1, part.Parameters.Count + 1)]
    for index, parameter in enumerate(names):
        if parameter.Name.endswith("\\弯曲半径"):
            names[index] = _Stubborn(parameter.Name, parameter.Value)
    part.Parameters = _Collection(names)

    report = json.loads(tools.execute("catia_sheetmetal_parameters",
                                      {"bend_radius": 1.905}))
    entry = report["writes"][0]
    assert entry["written"] is False
    assert "kept" in entry["error"] and "1.905" in entry["error"]


def test_a_value_with_no_known_spelling_is_reported_with_its_spellings():
    tools, part, _conn = _tools()
    tools.execute("catia_sheetmetal_parameters", {"create": True})
    names = [part.Parameters.Item(i) for i in range(1, part.Parameters.Count + 1)]
    part.Parameters = _Collection([p for p in names
                                   if not p.Name.endswith("\\拐角止裂槽轴长度")])
    report = json.loads(tools.execute("catia_sheetmetal_parameters",
                                      {"relief_axis": 1.5}))
    entry = report["writes"][0]
    assert entry["written"] is False
    assert "relief_axis" in entry["reason"]
    assert "拐角止裂槽轴长度" in entry["spellings"]


def test_a_read_only_call_changes_nothing_and_says_so():
    tools, part, _conn = _tools()
    tools.execute("catia_sheetmetal_parameters", {"create": True})
    before = [part.Parameters.Item(i).Value
              for i in range(1, part.Parameters.Count + 1)]
    report = json.loads(tools.execute("catia_sheetmetal_parameters", {}))
    after = [part.Parameters.Item(i).Value
             for i in range(1, part.Parameters.Count + 1)]
    assert before == after
    assert report["writes"] == []
    assert any("only READ" in note for note in report["notes"])


# ── a new document that carries the set ─────────────────────────────────────


def test_new_sheetmetal_goes_through_new_part_and_then_sets_the_thickness():
    part = _Part()
    documents = _Documents(part)
    tools = SheetMetalTools(_Conn(part), documents)
    report = json.loads(tools.execute("catia_new_sheetmetal",
                                      {"name": "Bracket", "file_path": "B.CATPart",
                                       "thickness": 1.2}))
    assert documents.calls == [("catia_new_part",
                                {"name": "Bracket", "file_path": "B.CATPart"})]
    assert report["created"] is True, "this call created the set"
    assert part.SheetMetalFactory.calls == 1, "and exactly one set"
    assert report["parameters"]["thickness"]["value"] == 1.2
    assert "Created Bracket" in report["new_part"]
