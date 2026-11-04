# -*- coding: utf-8 -*-
"""0.3.63 - the knowledge module, ported only after measuring it.

Upstream's five Knowledgeware tools were dropped when this fork was cut. They
came back here, but not as a copy: every attribute name in that code was
exercised on B30 first, and four things did not behave as written.

**A design table could be created and never applied.** Upstream calls
``ConfigurationName``, ``Configurations`` and ``NumberOfConfigurations``. The
DesignTable interface (52 members, read from the type library) has none of them:

    Activate, Deactivate, Activated, Configuration (get AND put),
    ConfigurationsNb, ColumnsNb, Synchronize, AddAssociation, RemoveAssociation

So the property it wanted is ``Configuration``, it is writable, and writing
``2`` on a two-row sheet does move the part (measured: LEN 20 -> 30, REAL
3.5 -> 4.5). ``catia_apply_design_table`` is the sixth tool here because without
it a table is a file association a human has to activate by hand.

**A length cell with no unit is read in METRES.** A sheet row reading ``30`` set
a 20 mm parameter to 30000 mm; ``30mm`` and ``0.03m`` both gave 30. A product
family built from a plain Excel column would come out 1000x too large with
nothing in the reply saying why. Both the create and apply replies carry that.

**An associated parameter can no longer be written directly.** After the table
claims it, ``ValuateFromString`` fails with a bare COM error; the reply now
names the table path and points at ``catia_apply_design_table``.

**``bool("false")`` is True.** The boolean parameter type parsed its value with
``bool()``, so a false arriving as text became true. ``_as_bool`` refuses text it
does not understand rather than guessing.

What upstream got right and is kept: a relation's ``Value`` IS its expression
(CATIA rewrites it to full parameter paths - measured), ``AddAssociation(
parameter, column)`` takes them in that order, and a length has to go through
``ValuateFromString`` rather than a bare ``.Value`` assignment.
"""

from __future__ import annotations

import pytest

from catia_mcp.tools.knowledge import KnowledgeTools, _as_bool


# ───────────── stubs

class _Parameter:
    def __init__(self, name: str, value=None) -> None:
        self.Name = name
        self.Value = value


class _Parameters:
    def __init__(self, entries: dict[str, object] | None = None) -> None:
        self.entries = dict(entries or {})

    @property
    def Count(self) -> int:
        return len(self.entries)

    def Item(self, key):
        if isinstance(key, int):
            return list(self.entries.values())[key - 1]
        if key not in self.entries:
            raise KeyError(key)
        return self.entries[key]


class _Table:
    def __init__(self, name: str, rows: int = 2) -> None:
        self.Name = name
        self.ConfigurationsNb = rows
        self.Configuration = 0


class _Relations:
    def __init__(self, entries: dict[str, object] | None = None) -> None:
        self.entries = dict(entries or {})

    @property
    def Count(self) -> int:
        return len(self.entries)

    def Item(self, key):
        if isinstance(key, int):
            return list(self.entries.values())[key - 1]
        if key not in self.entries:
            raise KeyError(key)
        return self.entries[key]

    def Remove(self, name: str) -> None:
        self.entries.pop(name)


class _Part:
    def __init__(self, parameters: _Parameters, relations: _Relations) -> None:
        self.Parameters = parameters
        self.Relations = relations
        self.updates = 0

    def Update(self) -> None:
        self.updates += 1


class _Conn:
    def __init__(self, part: _Part) -> None:
        self.part = part

    def ensure_connected(self) -> None:
        return None

    def get_active_part(self):
        return self.part


def _tools(parameters=None, relations=None) -> tuple[KnowledgeTools, _Part]:
    part = _Part(_Parameters(parameters), _Relations(relations))
    return KnowledgeTools(connection=_Conn(part)), part


# ───────────── the boolean trap

@pytest.mark.parametrize("value,expected", [
    ("false", False), ("False", False), ("FALSE", False), ("0", False),
    ("no", False), ("off", False), (False, False), (0, False),
    ("true", True), ("True", True), ("1", True), ("yes", True),
    ("on", True), (True, True), (1, True),
])
def test_a_boolean_survives_the_round_trip_as_text(value, expected):
    """bool("false") is True, which inverted every false that arrived as a string."""
    assert _as_bool(value) is expected


@pytest.mark.parametrize("value", ["nope", "", "maybe", 2.5 and "2.5"])
def test_text_that_is_not_a_boolean_is_refused_not_guessed(value):
    with pytest.raises(ValueError) as caught:
        _as_bool(value)
    assert "not a boolean" in str(caught.value)


# ───────────── applying a design table

def test_applying_a_row_sets_the_configuration_and_updates():
    tools, part = _tools(relations={"DT1": _Table("DT1", rows=3)})
    reply = tools.execute("catia_apply_design_table",
                          {"name": "DT1", "configuration": 2})
    assert part.Relations.Item("DT1").Configuration == 2
    assert part.updates == 1
    assert "2 of 3" in reply


def test_a_row_past_the_end_names_the_range():
    tools, _part = _tools(relations={"DT1": _Table("DT1", rows=2)})
    with pytest.raises(RuntimeError) as caught:
        tools.execute("catia_apply_design_table", {"name": "DT1", "configuration": 5})
    message = str(caught.value)
    assert "out of range" in message and "2" in message


def test_row_numbers_start_at_one():
    tools, _part = _tools(relations={"DT1": _Table("DT1", rows=2)})
    with pytest.raises(RuntimeError):
        tools.execute("catia_apply_design_table", {"name": "DT1", "configuration": 0})


def test_a_relation_that_is_not_a_table_is_reported_as_such():
    class _Formula:
        Name = "F1"
        Value = "1mm"

        @property
        def ConfigurationsNb(self):
            raise AttributeError("ConfigurationsNb")

    tools, _part = _tools(relations={"F1": _Formula()})
    with pytest.raises(RuntimeError) as caught:
        tools.execute("catia_apply_design_table", {"name": "F1", "configuration": 1})
    assert "not a design table" in str(caught.value)


def test_an_unknown_table_points_at_the_listing():
    tools, _part = _tools()
    with pytest.raises(RuntimeError) as caught:
        tools.execute("catia_apply_design_table", {"name": "DT9", "configuration": 1})
    assert "catia_list_relations" in str(caught.value)


# ───────────── the metres warning

def test_the_apply_reply_carries_the_unit_warning():
    tools, _part = _tools(relations={"DT1": _Table("DT1", rows=2)})
    reply = tools.execute("catia_apply_design_table",
                          {"name": "DT1", "configuration": 1})
    assert "1000x" in reply or "metres" in reply.lower(), (
        "a length column without a unit multiplies by 1000; the reply has to say so")


def test_the_create_tool_refuses_a_sheet_that_is_not_there():
    tools, _part = _tools()
    with pytest.raises(RuntimeError) as caught:
        tools.execute("catia_create_design_table",
                      {"name": "DT1", "file_path": r"C:\nope\family.txt",
                       "parameters": ["H"]})
    assert "No sheet" in str(caught.value)


def test_missing_parameters_are_listed_before_a_table_is_built():
    tools, _part = _tools(parameters={"H": _Parameter("Part1\\H", 20)})
    with pytest.raises(RuntimeError) as caught:
        tools.execute("catia_create_design_table",
                      {"name": "DT1", "file_path": __file__, "parameters": ["H", "W"]})
    assert "W" in str(caught.value)


# ───────────── the near-name hint

def test_a_wrongly_spelled_feature_path_offers_near_matches():
    tools, _part = _tools(parameters={
        "Part3\\零件几何体\\凸台.1\\第一限制\\长度": _Parameter(
            "Part3\\零件几何体\\凸台.1\\第一限制\\长度", 20),
        "Part3\\零件几何体\\凸台.1\\第二限制\\长度": _Parameter(
            "Part3\\零件几何体\\凸台.1\\第二限制\\长度", 0),
    })
    with pytest.raises(RuntimeError) as caught:
        tools.execute("catia_set_formula",
                      {"name": "F", "target_parameter": "凸台.1\\第一限制\\长度",
                       "expression": "20mm"})
    message = str(caught.value)
    assert "第一限制" in message and "第二限制" in message, (
        "the hint has to show the full localized names, which is what the caller cannot guess")
    assert "catia_get_parameters" in message


# ───────────── the module as a whole

def test_the_module_publishes_six_tools():
    definitions = KnowledgeTools(connection=None).get_tool_definitions()
    names = {definition["name"] for definition in definitions}
    assert names == {
        "catia_create_parameter", "catia_set_formula", "catia_list_relations",
        "catia_delete_relation", "catia_create_design_table",
        "catia_apply_design_table",
    }
    for definition in definitions:
        assert definition["description"].strip(), definition["name"]
