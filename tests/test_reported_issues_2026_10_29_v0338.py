"""0.3.38 - the two acceptance reports of 2026-10-29 against 0.3.37.

Report 1 (`写错文档名在激活工具上报成COM_ERROR并附错误诊断`): P1 - one wrong
document name answers two codes; ``catia_activate_document`` was the odd one out,
answering ``COM_ERROR`` plus the "CATIA reports most failures as a bare 'method
failed', check whether in_work_object is a geometrical set" paragraph, about a call
that never touched a body. P3 - ``catia_list_documents`` glued two notes together
with a space, so a document that is both a component and a duplicate read
"... with document=) 2 open documents carry this exact name ...", and the first
half's advice ("measure it by name with document=") is the thing the second half
forbids. §5.1 - the assembly inertia route answered none of the cold/warm
vocabulary. §5.2 - the same ambiguity was worded differently in two tools, and the
planner's copy dropped the "never saved - no path to name it by" fact.

Report 2 (`七条全关且冷暖双降但冷热账缺19%而解析器解释不了这次恢复`): §6.1
``measure_ms + tensor_ms`` was 18-19 % short of ``parts_ms`` on three samples with
no field naming the remainder; §6.2 the cold/warm note's range was falsified one
session later (all seven 12-cell parts read 1.93x-3.70x against a 1.81x ceiling);
§6.3 an empty product got the cost block from ``get_inertia`` but not from
``get_bounding_box``; §6.4 the two-note glue (same as report 1 P3); §6.5
``elapsed_note`` still carried four unversioned multipliers; §6.6 the 303.3 ms in
``poses_route_note`` was attributed to "the acceptance run". §3.1 asked whether the
narrowed path is really cheaper - it was not, because it re-read the whole
component list (poses included) to answer a structural question.

Its §8.8 asks the author NOT to write this release's warm number into CHANGELOG as
a baseline: both directions moved with the session, and the per-call resolver can
account for ~10 ms of a 5 900 ms change.
"""

import json
import re
from types import SimpleNamespace

import pytest


# ─────────────── 1. one wrong name, one code (report 1, P1)
class _Doc:
    def __init__(self, name, full=None):
        self.Name = name
        self.FullName = full if full is not None else name
        self._active = False

    def Activate(self):
        self._active = True


def _document_tools(docs):
    from catia_mcp.tools.document import DocumentTools

    class _Coll:
        Count = len(docs)

        def Item(self, i):
            return docs[i - 1]

    tools = DocumentTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None,
                                 documents=_Coll(),
                                 active_document=None,
                                 component_usage=lambda: {})
    tools._component_usage = lambda: {}
    return tools


def test_a_wrong_document_name_is_a_lookup_failure_on_the_document_tool():
    """The tool whose whole job is document names was the one that said COM_ERROR."""
    from catia_mcp.results import CodedError

    tools = _document_tools([_Doc("REG38.CATPart")])
    with pytest.raises(CodedError) as caught:
        tools._activate_document("NOPE38.CATPart")
    assert caught.value.code == "DOCUMENT_NOT_FOUND"
    assert caught.value.modified is False
    # The actions are about the ARGUMENT, so they survive on a read-only tool.
    assert caught.value.next_actions
    assert any("catia_list_documents" in action
               for action in caught.value.next_actions)
    assert "No open document named 'NOPE38.CATPart'" in str(caught.value)


def test_the_phrase_table_also_knows_the_word_named():
    """Belt and braces: a raise site that forgets the code still answers one."""
    from catia_mcp.results import failure_code

    code = failure_code("No open document named 'X.CATPart'. Open: a, b.",
                        ValueError("x"), default="COM_ERROR")
    assert code == "DOCUMENT_NOT_FOUND"


# ─────────────── 2. two notes, two sentences (report 1 P3 / report 2 §6.4)
def _part_that_is_also_a_component(shared):
    from catia_mcp.tools.document import DocumentTools

    docs = [_Doc("LAND38.CATPart", "E:\\bench\\LAND38.CATPart")]
    if shared:
        docs.append(_Doc("LAND38.CATPart", "LAND38.CATPart"))

    class _Coll:
        Count = len(docs)

        def Item(self, i):
            return docs[i - 1]

    tools = DocumentTools(connection=None)
    tools.conn = SimpleNamespace(
        ensure_connected=lambda: None, documents=_Coll(),
        active_document=None,
        part_of=lambda doc: SimpleNamespace(Name=doc.Name),
        product_of=lambda doc: (_ for _ in ()).throw(RuntimeError("not a product")))
    tools._component_usage = lambda: {"LAND38.CATPart": ["C37PROD.CATProduct"]}
    return json.loads(tools._list_documents())


def test_the_two_notes_read_as_two_sentences():
    entry = _part_that_is_also_a_component(shared=True)[0]
    note = entry["note"]
    # "... document=)" followed directly by a digit read like an argument.
    assert "document=) 2" not in note
    assert ". This exact name is carried by 2 open documents" in note
    assert note.endswith(".")


def test_the_component_advice_yields_to_the_shared_name_advice():
    """Both notes cannot be right: 'use the name' and 'the name is refused'."""
    shared = _part_that_is_also_a_component(shared=True)[0]
    assert "measure it by name with document=" not in shared["note"]
    assert shared["name_shared_by"] == 2
    # Alone, the same document keeps the advice - it is not banned, it is beaten.
    alone = _part_that_is_also_a_component(shared=False)[0]
    assert "measure it by name with document=" in alone["note"]


def test_the_two_refusals_describe_a_candidate_the_same_way():
    """The planner's copy dropped the one fact the advice depends on."""
    from catia_mcp.connection import CATIAConnection

    rows = [{"document": "DUP38.CATPart",
             "path": "E:\\bench\\DUP38.CATPart", "active": False, "saved": True},
            {"document": "DUP38.CATPart", "path": None,
             "active": True, "saved": False}]
    listing = CATIAConnection.format_matches(rows)
    assert "'DUP38.CATPart' at 'E:\\bench\\DUP38.CATPart'" in listing
    assert "never saved - no path to name it by" in listing
    # A copy with no path must not be shown as "at 'None'" or "at 'DUP38.CATPart'".
    assert "at 'None'" not in listing

    from catia_mcp.tools import planner as P

    source = P.__file__
    with open(source, encoding="utf-8") as handle:
        assert "format_matches" in handle.read()


# ─────────────── 3. parts_ms names its own remainder (report 2, §6.1)
class _Measurable:
    Volume = 8.0e-6
    Area = 2.0e-3

    def GetCOG(self):
        return [0.0, 0.0, 2.5]


def _cost_tools(tensor_ms=0.02):
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None, app=None,
                                 component_usage=lambda: {},
                                 active_document=None)
    tools._read_measurable_array = (
        lambda measurable, method, count: [0.0, 0.0, 2.5])

    def _slow_tensor(part):
        import time
        time.sleep(tensor_ms)
        return None

    tools._tensor_for = _slow_tensor
    return tools


def _part_for_cost():
    part = SimpleNamespace(
        Name="P38.CATPart",
        MainBody=SimpleNamespace(Name="PartBody", Shapes=SimpleNamespace(Count=1)))
    part.Parent = SimpleNamespace(
        Name="P38.CATPart", FullName="P38.CATPart",
        GetWorkbench=lambda name: SimpleNamespace(
            GetMeasurable=lambda ref: _Measurable()))
    part.CreateReferenceFromObject = lambda obj: obj
    return part


def test_the_documented_part_route_names_the_remainder_that_used_to_vanish():
    tools = _cost_tools()
    reply = json.loads(tools._part_inertia(_part_for_cost(), None, "P38.CATPart"))
    assert "parts_other_ms" in reply
    closed = (reply["measure_ms"] + reply["tensor_ms"] + reply["parts_other_ms"])
    # The three terms add up to parts_ms to 0.1 ms, which is the promise the
    # note now makes in words.
    assert abs(round(closed, 1) - reply["parts_ms"]) <= 0.1
    assert "parts_other_ms" in reply["overhead_note"]
    assert "three terms" in reply["overhead_note"]


def test_the_active_part_route_closes_the_same_way():
    """The route a caller reaches without naming anything: same three terms."""
    tools = _cost_tools()
    part = _part_for_cost()
    body = part.MainBody
    tools.conn = SimpleNamespace(
        ensure_connected=lambda: None, app=None, active_document=None,
        component_usage=lambda: {},
        get_active_part=lambda: part, get_active_part_body=lambda: body)
    tools._document_kind = lambda: "part"
    tools._spa_workbench = lambda: SimpleNamespace(
        GetMeasurable=lambda ref: _Measurable())
    tools._evaluate_floats = lambda script, name, args: (
        [0.0155, 1.0e-6, 0.0, 0.0, 0.0, 2.6e-6, 0.0, 0.0, 0.0, 2.6e-6])
    reply = json.loads(tools._get_inertia())
    assert "parts_other_ms" in reply
    closed = (reply["measure_ms"] + reply["tensor_ms"] + reply["parts_other_ms"])
    assert abs(round(closed, 1) - reply["parts_ms"]) <= 0.1
    assert round(reply["parts_ms"] + reply["overhead_ms"], 1) == reply["elapsed_ms"]


# ─────────────── 4. no multiplier without three sessions (report 2, §6.2/§6.5)
def _box_part(name):
    """A part good enough for the box walk: it asks for MainBody, then a box."""
    return SimpleNamespace(
        Name=name,
        MainBody=SimpleNamespace(Name="PartBody", Shapes=SimpleNamespace(Count=1)))


def _assembly_tools(part_boxes_given=None):
    from catia_mcp.tools import measurement as M

    rows = part_boxes_given if part_boxes_given is not None else [
        {"index": 1, "name": "P38.1", "part_number": "P38",
         "matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
         "translation": [0.0, 0.0, 0.0],
         "part": _box_part("P38"), "nested_doc": None,
         "kind": "part", "problems": []}]

    tools = M.MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None,
                                 active_document=None)

    def _components(doc, only=None):
        tools._poses_route = "one_call" if only is None else "single"
        return [dict(row) for row in rows]

    tools._components_doc = _components
    tools._part_box_entry = lambda part, body, curved: {
        "points": [[0.0, 0.0, 0.0], [10.0, 6.0, 4.0]], "used": 8,
        "source": "vertices", "mesh_vertices": None, "copy_note": "",
        "cells": 60, "nls_chars": 2388, "points_route": "batched",
        "sweep_route": "one_call"}
    tools._part_identity = lambda part: part.Name
    return tools


def test_the_cold_note_quotes_this_call_not_four_old_multipliers():
    from catia_mcp.tools import measurement as M

    M.reset_session_books()
    tools = _assembly_tools()
    reply = json.loads(tools._assembly_box(SimpleNamespace(Name="ASM38.CATProduct"),
                                           "vertices"))
    note = reply["elapsed_note"]
    assert reply["session_first_parts"] == 1
    # The four numbers this note used to carry: 4.65x / 7.6x / 7.24x / 9.11x.
    assert not re.search(r"\d+\.\d+x", note), note
    assert "parts[].cold_warm_ratio" in note
    assert "parts_ms 0.0" in note or "parts_ms" in note


def test_the_empty_assembly_answers_with_the_same_cost_block():
    from catia_mcp.tools import measurement as M

    M.reset_session_books()
    tools = _assembly_tools(part_boxes_given=[])
    reply = json.loads(tools._assembly_box(SimpleNamespace(Name="EMPTY38.CATProduct"),
                                           "vertices"))
    assert reply["empty_assembly"] is True
    assert reply["parts_ms"] == 0.0
    assert reply["overhead_ms"] == reply["elapsed_ms"]
    breakdown = reply["overhead_breakdown"]
    assert set(breakdown) == {"components_doc_ms", "walk_other_ms", "report_ms"}
    assert round(sum(breakdown.values()), 1) == reply["overhead_ms"]
    assert reply["poses_route"] == "one_call"


def test_the_pose_note_owns_its_number():
    from catia_mcp.tools.measurement import _pose_note

    text = _pose_note("single")
    assert "303.3 ms" not in text or "B30" in text
    assert "acceptance run" not in text.split("303.3")[0][-80:]


# ─────────────── 5. a narrowed call reads the list once (report 2, §3.1)
def test_a_narrowed_call_does_not_reread_the_whole_component_list():
    """The nested-twin search used to pay a second full pose read per call."""
    from catia_mcp.tools import measurement as M

    M.reset_session_books()
    rows = [{"index": 1, "name": "P38.1", "part_number": "P38",
             "matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
             "translation": [0.0, 0.0, 0.0],
             "part": _box_part("P38"), "nested_doc": None,
             "kind": "part", "problems": []}]

    calls = {"rows": 0}

    tools = M.MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(
        ensure_connected=lambda: None, active_document=None,
        # One top-level child, and it is a SUB-ASSEMBLY: without the product tree
        # the old code re-read every component of the assembly to find it.
        product_of=lambda doc: SimpleNamespace(
            Products=SimpleNamespace(
                Count=1,
                Item=lambda i: SimpleNamespace(
                    Name="SUB38.1", Products=SimpleNamespace(Count=1),
                    ReferenceProduct=SimpleNamespace(Parent=None)))))
    tools._components_doc = lambda doc, only=None: (
        calls.__setitem__("rows", calls["rows"] + 1)
        or [dict(row) for row in rows])
    tools._part_box_entry = lambda part, body, curved: {
        "points": [[0.0, 0.0, 0.0], [10.0, 6.0, 4.0]], "used": 8,
        "source": "vertices", "mesh_vertices": None, "copy_note": "",
        "cells": 60, "nls_chars": 2388, "points_route": "batched",
        "sweep_route": "one_call"}
    tools._part_identity = lambda part: part.Name
    reply = json.loads(tools._assembly_box(
        SimpleNamespace(Name="ASM38.CATProduct"), "vertices", only="P38.1"))
    assert reply["instances"]["total"] >= 1
    assert calls["rows"] == 1, calls


# ─────────────── 6. the inertia route answers cold and warm (report 1, §5.1)
def _inertia_tools(parts=("P38A", "P38B")):
    from catia_mcp.tools.measurement import MeasurementTools

    rows = []
    for index, name in enumerate(parts, start=1):
        rows.append({"index": index, "name": f"{name}.1", "part_number": name,
                     "matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                     "translation": [0.0, 0.0, 0.0],
                     "part": SimpleNamespace(Name=name), "nested_doc": None,
                     "kind": "part", "problems": []})

    tools = MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None,
                                 active_document=None)

    def _components(doc):
        tools._poses_route = "one_call"
        return rows

    tools._components_doc = _components
    tools._part_identity = lambda part: part.Name

    def _props(part, cache=None):
        import time
        time.sleep(0.005)
        return {"volume": 1.0e6, "area": 2.0e4, "cog": [0.0, 0.0, 0.0]}

    tools._part_mass_properties = _props
    return tools


def test_the_assembly_inertia_route_says_which_regime_it_ran_in():
    tools = _inertia_tools()
    cold = json.loads(tools._assembly_inertia(
        None, SimpleNamespace(Name="ASM38.CATProduct")))
    assert cold["after_cold"] is None
    assert cold["session_first_parts"] == 2
    assert cold["parts_total"] == 2
    assert all(entry["first_measure"] is True for entry in cold["parts"])
    # Which job the first reading was: the note counts it by name, so a caller
    # can tell "2 parts paid for mass properties" from "2 parts paid for a
    # topology sweep" - the distinction 0.3.38 collapsed into one count.
    assert "mass-properties" in cold["elapsed_note"]
    assert cold["cold_reasons"] == {"mass_properties": 2}
    assert round(cold["parts_ms"] + cold["overhead_ms"], 1) == cold["elapsed_ms"]

    warm = json.loads(tools._assembly_inertia(
        None, SimpleNamespace(Name="ASM38.CATProduct")))
    assert warm["after_cold"] is True
    assert warm["session_first_parts"] == 0
    assert all(entry["first_measure"] is False for entry in warm["parts"])
    assert "cold_warm_ratio" in warm["parts"][0]
    assert "cold_ms" in warm["parts"][0]
    assert round(warm["parts_ms"] + warm["overhead_ms"], 1) == warm["elapsed_ms"]
