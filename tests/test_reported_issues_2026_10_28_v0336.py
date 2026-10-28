"""0.3.36 - the two acceptance reports of 2026-10-28 against 0.3.35.

Report 1 (`八条全关且暖态再快8%...`): eight self-declared claims verified, then
six new items - the `after_cold` assertion pointing the wrong way, `after_cold`
being a MISSING KEY on the cold call, `components_doc_ms` labelled "that one
read" when most of it is name matching, the COM_ERROR paragraph still appended to
customised failures, `catia_type` always null, no cost account on
catia_get_inertia, and the observation that the cold/warm ratio is per part.

Report 2 (`重名时按名取文档静默返回另一份模型`): P1 - `document=` returned the
OTHER model when two open documents share a name, and echoed the name back; P2 -
an empty assembly's `skipped[0].component` was the ACTIVE WINDOW's name; P3 - a
read-only tool still got a `recovery` block when it failed validation.
"""

import json
import inspect
from types import SimpleNamespace

import pytest


# ─────────────── 1. a name that means two documents is refused, not guessed
class _Doc:
    def __init__(self, name, full=None):
        self.Name = name
        self.FullName = full if full is not None else name


def _conn(pairs, active):
    """A connection whose Documents collection holds `pairs` (name, full path)."""
    from catia_mcp.connection import CATIAConnection

    docs = [_Doc(n, f) for n, f in pairs]

    class _Coll:
        Count = len(docs)

        def Item(self, i):
            return docs[i - 1]

    conn = CATIAConnection()
    conn.app = SimpleNamespace(Documents=_Coll(),
                               ActiveDocument=_Doc(active, active),
                               Caption="CATIA V5")
    conn.test_documents = docs
    return conn


DUP = [("DUP35.CATPart", "E:\\bench\\DUP35.CATPart"),
       ("DUP35.CATPart", "DUP35.CATPart")]


def test_a_bare_name_that_matches_two_documents_resolves_to_neither():
    conn = _conn(DUP, "DUP35.CATPart")
    doc, matches = conn.resolve_document("DUP35.CATPart")
    assert doc is None, "picking one of two identical names IS the reported bug"
    assert len(matches) == 2


def test_the_saved_copy_can_be_named_by_path():
    """The escape hatch the error offers has to actually work."""
    conn = _conn(DUP, "DUP35.CATPart")
    doc, _ = conn.resolve_document("E:/bench/DUP35.CATPart")
    assert doc is not None and doc.FullName == "E:\\bench\\DUP35.CATPart"


def test_two_unsaved_copies_cannot_be_told_apart_by_any_argument():
    conn = _conn([("X.CATPart", "X.CATPart"), ("X.CATPart", "X.CATPart")],
                 "X.CATPart")
    doc, matches = conn.resolve_document("X.CATPart")
    assert doc is None and len(matches) == 2
    # An unsaved document reports its name as its FullName, so "has a path" is
    # not the same question as "was ever saved".
    assert all(not row["saved"] for row in matches)


def test_one_document_still_resolves_without_any_noise():
    conn = _conn([("A.CATPart", "E:\\a\\A.CATPart"),
                  ("B.CATProduct", "E:\\a\\B.CATProduct")], "A.CATPart")
    doc, matches = conn.resolve_document("A")
    assert doc is not None and doc.Name == "A.CATPart"
    assert len(matches) == 1


def test_the_measurement_route_refuses_an_ambiguous_name():
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools.conn = _conn(DUP, "DUP35.CATPart")
    with pytest.raises(Exception) as caught:
        tools._resolve_open_document("DUP35.CATPart")
    error = caught.value
    assert error.code == "DOCUMENT_NAME_AMBIGUOUS"
    assert error.modified is False
    assert "2 open documents" in str(error)
    assert "'E:\\bench\\DUP35.CATPart'" in str(error)
    # The candidates travel as DATA, so a caller does not parse the message.
    assert [row["document"] for row in error.data["candidates"]] == [
        "DUP35.CATPart", "DUP35.CATPart"]


def test_the_planner_refuses_it_with_the_same_code():
    from catia_mcp.tools.planner import PlannerTools

    planner = PlannerTools(connection=None)
    planner.conn = _conn(DUP, "DUP35.CATPart")
    with pytest.raises(Exception) as caught:
        planner._topology_target("DUP35.CATPart")
    assert caught.value.code == "DOCUMENT_NAME_AMBIGUOUS"
    assert "Nothing was counted" in str(caught.value)


def test_the_error_envelope_carries_the_candidates():
    """`failure()` publishes CodedError.data, so the fix reaches the wire."""
    from catia_mcp.server import CATIAMCPServer

    class _Module:
        @staticmethod
        def execute(_name, _args):
            from catia_mcp.results import CodedError

            raise CodedError("ambiguous", code="DOCUMENT_NAME_AMBIGUOUS",
                             modified=False,
                             data={"candidates": [{"document": "A.CATPart"}]})

    server = CATIAMCPServer.__new__(CATIAMCPServer)
    server._tool_router = {"catia_get_inertia": _Module}
    server._schemas = {"catia_get_inertia": {"type": "object", "properties": {}}}
    server.connection = SimpleNamespace(ensure_connected=lambda: None,
                                       active_document=None)
    payload = server._execute_tool("catia_get_inertia", {}).structuredContent
    assert payload["data"]["candidates"] == [{"document": "A.CATPart"}]
    assert payload["data"]["failure_stage"] == "execute"


def test_a_new_part_says_the_name_is_already_taken():
    """The tool that creates the duplicate is where the caller can still act."""
    from catia_mcp.tools.document import DocumentTools

    class _Part:
        Name = "Part1"

    tools = DocumentTools(connection=None)
    tools.conn = _conn([("DUP35.CATPart", "E:\\bench\\DUP35.CATPart")],
                       "DUP35.CATPart")
    note = tools._name_collision_note(_Doc("DUP35.CATPart", "DUP35.CATPart"))
    assert "Another open document is already called 'DUP35.CATPart'" in note
    assert "DOCUMENT_NAME_AMBIGUOUS" in note
    # A name that is not taken says nothing at all.
    assert tools._name_collision_note(_Doc("FRESH.CATPart")) == ""


def test_activating_one_of_two_says_it_picked_the_first():
    from catia_mcp.tools.document import DocumentTools

    tools = DocumentTools(connection=None)
    tools.conn = _conn(DUP, "DUP35.CATPart")
    saved, unsaved = tools.conn.test_documents
    assert tools._twin_documents(saved) == [
        row for row in tools.conn._document_rows() if row["doc"] is unsaved]
    assert tools._twin_documents(_Doc("ONLY_ONE.CATPart")) == []


def test_the_listing_flags_a_shared_name_and_stays_a_list():
    from catia_mcp.tools.document import DocumentTools

    tools = DocumentTools(connection=None)

    class _Documents:
        Count = 2

        def Item(self, i):
            return [_Doc("DUP35.CATPart", "E:\\bench\\DUP35.CATPart"),
                    _Doc("DUP35.CATPart", "DUP35.CATPart")][i - 1]

    tools.conn = SimpleNamespace(
        ensure_connected=lambda: None, documents=_Documents(),
        part_of=lambda doc: (_ for _ in ()).throw(RuntimeError("not a part")),
        product_of=lambda doc: (_ for _ in ()).throw(RuntimeError("not a product")))
    tools._component_usage = lambda: {}
    rows = json.loads(tools._list_documents())
    assert [row["name_shared_by"] for row in rows] == [2, 2]
    assert "DOCUMENT_NAME_AMBIGUOUS" in rows[0]["note"]


# ─────────────── 2. after_cold is three values and the note goes both ways
def _assembly_tools(entries=None, walk_ms=0.0, part_ms=0.0):
    from catia_mcp.tools import measurement as M

    entries = entries or {"P31A": {
        "points": [[0.0, 0.0, 0.0], [10.0, 6.0, 4.0]], "used": 8,
        "source": "vertices", "mesh_vertices": None, "copy_note": "",
        "cells": 60, "nls_chars": 2388, "points_route": "batched",
        "sweep_route": "one_call"}}

    class _Part:
        def __init__(self, name):
            self.Name = name
            self.MainBody = SimpleNamespace(Name="PartBody",
                                            Shapes=SimpleNamespace(Count=1))

    class _PartDoc:
        def __init__(self, name):
            self.Name = name

    tools = M.MeasurementTools(connection=None)
    rows = []
    for part_name in entries:
        part = _Part(part_name)
        rows.append({"index": 1, "name": part_name + ".1", "part_number": part_name,
                     "matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                     "translation": [0.0, 0.0, 0.0], "part": part,
                     "nested_doc": None, "kind": "part", "problems": []})
    tools.conn = SimpleNamespace(ensure_connected=lambda: None,
                                 active_document=None)

    def _entry(part, body, curved):
        if part_ms:
            import time as _t
            _t.sleep(part_ms / 1000.0)
        return dict(entries[part.Name])

    def _components(doc, only=None):
        # The real reader sets the route; the stub has to as well, or every cost
        # field around it is untested.
        tools._poses_route = "one_call"
        return rows

    tools._components_doc = _components
    tools._part_box_entry = _entry
    tools._part_identity = lambda part: part.Name

    def _slow_walk(*_args, **_kwargs):
        if walk_ms:
            import time as _t
            _t.sleep(walk_ms / 1000.0)

    original = tools._box_walk

    def _walk(rows_, matrix, t, depth, prefix, ctx):
        _slow_walk()
        return original(rows_, matrix, t, depth, prefix, ctx)

    tools._box_walk = _walk
    return tools


def _doc(name):
    return SimpleNamespace(Name=name)


def test_after_cold_is_null_true_false_and_always_present():
    from catia_mcp.tools import measurement as M

    M._MEASURED_PARTS.clear()
    tools = _assembly_tools()
    cold = json.loads(tools._assembly_box(_doc("ASM.CATProduct"), "vertices"))
    assert cold["cold_parts"] == 1
    assert "after_cold" in cold and cold["after_cold"] is None
    first = json.loads(tools._assembly_box(_doc("ASM.CATProduct"), "vertices"))
    assert first["after_cold"] is True
    second = json.loads(tools._assembly_box(_doc("ASM.CATProduct"), "vertices"))
    assert second["after_cold"] is False
    M._MEASURED_PARTS.clear()


def test_the_post_cold_note_does_not_claim_a_direction():
    """0.3.34 measured +27 %, 0.3.35 measured -1 %: it is not a rule."""
    from catia_mcp.tools import measurement as M

    M._MEASURED_PARTS.clear()
    tools = _assembly_tools()
    json.loads(tools._assembly_box(_doc("ASM.CATProduct"), "vertices"))
    note = json.loads(
        tools._assembly_box(_doc("ASM.CATProduct"), "vertices"))["elapsed_note"]
    assert "upper bound" in note          # the phrase is named...
    assert "NOT as an upper bound" in note  # ...in order to reject it
    assert "+27.4 %" in note and "-0.8 %" in note
    assert "181.8 s" not in note          # no unlabelled constant survives
    M._MEASURED_PARTS.clear()


def test_a_repeat_warm_call_quotes_this_sessions_difference():
    from catia_mcp.tools import measurement as M

    M._MEASURED_PARTS.clear()
    tools = _assembly_tools()
    json.loads(tools._assembly_box(_doc("ASM.CATProduct"), "vertices"))
    json.loads(tools._assembly_box(_doc("ASM.CATProduct"), "vertices"))
    note = json.loads(
        tools._assembly_box(_doc("ASM.CATProduct"), "vertices"))["elapsed_note"]
    assert "previous warm call on this document in this session" in note
    M._MEASURED_PARTS.clear()


def test_each_part_carries_its_own_cold_warm_ratio():
    from catia_mcp.tools import measurement as M

    M._MEASURED_PARTS.clear()
    tools = _assembly_tools(part_ms=3.0)
    json.loads(tools._assembly_box(_doc("ASM.CATProduct"), "vertices"))
    warm = json.loads(tools._assembly_box(_doc("ASM.CATProduct"), "vertices"))
    entry = warm["parts"][0]
    assert entry["cold_ms"] > 0
    assert entry["cold_warm_ratio"] > 0
    assert "weighted sum" in entry["cold_warm_note"]
    M._MEASURED_PARTS.clear()


# ─────────────── 3. an empty assembly: no phantom instance, no active-window name
def test_an_empty_assembly_names_itself_not_the_active_window():
    from catia_mcp.tools import measurement as M

    tools = M.MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None,
                                 active_document=None)
    tools._components_doc = lambda doc, only=None: []
    tools._active_name = lambda: "SOMETHING_IN_FRONT.CATPart"
    reply = json.loads(tools._assembly_box(_doc("EMPTY35.CATProduct"), "vertices"))
    assert reply["empty_assembly"] is True
    assert reply["skipped"] == []
    assert reply["instances"] == {"total": 0, "measured": 0, "skipped": 0}
    assert "SOMETHING_IN_FRONT" not in json.dumps(reply)
    assert "'EMPTY35.CATProduct' holds no component" in reply["incomplete_because"]


def test_the_two_routes_agree_on_how_many_instances_an_empty_product_has():
    from catia_mcp.tools import measurement as M

    tools = M.MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None,
                                 active_document=None)
    tools._components_doc = lambda doc, only=None: []
    box = json.loads(tools._assembly_box(_doc("EMPTY35.CATProduct"), "vertices"))
    inertia = json.loads(tools._assembly_inertia(_doc("EMPTY35.CATProduct")))
    assert box["instances"] == inertia["instances"]
    assert box["skipped"] == inertia["skipped"] == []
    assert box["empty_assembly"] is inertia["empty_assembly"] is True


def test_instances_skipped_still_equals_len_skipped_when_a_sub_assembly_is_empty():
    from catia_mcp.tools import measurement as M

    tools = M.MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None,
                                 active_document=None)

    class _Sub:
        Name = "SUB.CATProduct"

    rows = [{"index": 1, "name": "SUB.1", "part_number": "SUB",
             "matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
             "translation": [0.0, 0.0, 0.0], "part": None,
             "nested_doc": _Sub(), "kind": "sub_assembly", "problems": []}]

    def _components(doc, only=None):
        return [] if str(doc.Name) == "SUB.CATProduct" else rows

    tools._components_doc = _components
    reply = json.loads(tools._assembly_box(_doc("TOP.CATProduct"), "vertices"))
    assert reply["empty_assembly"] is False
    assert reply["instances"]["skipped"] == len(reply["skipped"]) == 1
    assert "holds no components" in reply["skipped"][0]["error"]


# ─────────────── 4. the narrowed read's cost is labelled for what it is
def test_the_narrowed_note_does_not_call_the_whole_cost_one_pose():
    from catia_mcp.tools.measurement import _pose_note

    note = _pose_note("single")
    assert "NOT that one read" in note
    assert "component_lookup_ms" in note
    assert "303.3 ms" in note


def test_the_lookup_is_timed_and_published_as_a_sub_bucket():
    from catia_mcp.tools import measurement as M

    class _Child:
        def __init__(self, name):
            self.Name = name
            self.PartNumber = name.split(".")[0]

    class _Products:
        Count = 3

        def Item(self, i):
            return _Child(f"P0{i}.1")

    tools = M.MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(
        product_of=lambda doc: SimpleNamespace(Products=_Products()),
        app=SimpleNamespace())
    tools._is_product_document = lambda doc: False
    tools._part_of_child = lambda child: None
    rows = tools._components_doc(_doc("ASM.CATProduct"), "P01.1")
    assert len(rows) == 1
    assert tools._poses_route == "single"
    assert tools._lookup_ms is not None and tools._lookup_ms >= 0.0


def test_a_narrowed_reply_publishes_the_lookup_inside_components_doc():
    from catia_mcp.tools import measurement as M

    tools = _assembly_tools()
    inner = tools._components_doc

    def _narrowed(doc, only=None):
        rows = inner(doc, only)
        # What the real reader does on the narrowed path: one pose, and a name
        # scan it now reports separately.
        tools._poses_route, tools._lookup_ms = "single", 250.0
        return rows

    tools._components_doc = _narrowed
    reply = json.loads(tools._assembly_box(_doc("ASM.CATProduct"), "vertices",
                                           only="P31A.1"))
    assert reply["poses_route"] == "single"
    breakdown = reply["overhead_breakdown"]
    assert breakdown["component_lookup_ms"] == 250.0
    # A SUB-bucket: the three terms still add up to overhead_ms exactly.
    assert round(breakdown["components_doc_ms"] + breakdown["walk_other_ms"]
                 + breakdown["report_ms"], 1) == reply["overhead_ms"]


# ─────────────── 5. a customised failure stops appending the COM_ERROR sermon
def _server_raising(error):
    from catia_mcp.server import CATIAMCPServer

    class _Module:
        @staticmethod
        def execute(_name, _args):
            raise error

    server = CATIAMCPServer.__new__(CATIAMCPServer)
    server._tool_router = {"catia_pad": _Module}
    server._schemas = {"catia_pad": {"type": "object", "properties": {}}}
    server.connection = SimpleNamespace(ensure_connected=lambda: None,
                                       active_document=None)
    return server


def test_a_coded_refusal_does_not_also_say_check_the_in_work_object():
    from catia_mcp.results import CodedError

    server = _server_raising(CodedError("No sketch named '草图.99'.", code="SKETCH_NOT_FOUND"))
    message = server._execute_tool("catia_pad", {}).structuredContent["message"]
    assert "Session state:" in message          # the reading stays
    assert "reader as a bare 'method failed'" not in message
    assert "bad sketch, bad edge" not in message


def test_a_bare_com_failure_still_gets_the_state_check():
    server = _server_raising(RuntimeError("方法 Add 失败"))
    message = server._execute_tool("catia_pad", {}).structuredContent["message"]
    assert "Session state:" in message
    assert "bad sketch, bad edge" in message


def test_a_document_that_is_not_open_does_not_send_you_to_a_body():
    from catia_mcp.results import CodedError

    server = _server_raising(CodedError("No open document matches 'NOPE'.",
                                        code="DOCUMENT_NOT_FOUND", modified=False))
    payload = server._execute_tool("catia_pad", {}).structuredContent
    assert payload["code"] == "DOCUMENT_NOT_FOUND"
    assert "in_work_object" not in payload["message"]


# ─────────────── 6. a read-only tool has no recovery block on ANY failure
def test_validation_failure_of_a_read_only_tool_has_no_recovery():
    from catia_mcp.server import CATIAMCPServer

    server = CATIAMCPServer.__new__(CATIAMCPServer)
    server._tool_router = {"catia_list_components": SimpleNamespace()}
    server._schemas = {"catia_list_components": {
        "type": "object",
        "properties": {"names_only": {"type": "boolean"}},
        "additionalProperties": False}}
    server.connection = SimpleNamespace(ensure_connected=lambda: None,
                                       active_document=None)
    payload = server._execute_tool("catia_list_components",
                                   {"names_only": "yes please"}).structuredContent
    assert payload["code"] == "INVALID_ARGUMENT"
    assert "recovery" not in payload
    assert payload["effects"]["modified"] is False


def test_an_unknown_tool_has_no_recovery_either():
    from catia_mcp.server import CATIAMCPServer

    server = CATIAMCPServer.__new__(CATIAMCPServer)
    server._tool_router = {}
    server._schemas = {}
    server.connection = SimpleNamespace(ensure_connected=lambda: None,
                                       active_document=None)
    payload = server._execute_tool("catia_analyze_assembly", {}).structuredContent
    assert payload["code"] == "UNKNOWN_TOOL"
    assert "recovery" not in payload


# ─────────────── 7. the two small fields and the second aggregator's cost
def test_catia_type_is_the_hosts_own_word_and_never_null_for_a_known_feature():
    from catia_mcp.tools.part_design import PartDesignTools

    assert PartDesignTools._feature_type_name("凸台.1") == "凸台"
    assert PartDesignTools._feature_type_name("盒体.1") == "盒体"
    assert PartDesignTools._feature_type_name("Pocket.2") == "Pocket"
    assert PartDesignTools._feature_type_name("SomethingElse.1") is None
    source = inspect.getsource(PartDesignTools._list_features)
    assert '"catia_type": self._feature_type_name(shape.Name)' in source


def test_the_sketches_note_describes_a_key_that_may_be_absent():
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._list_features)
    assert "gains an 'aliases' array only when it has one" in source


def test_the_inertia_route_publishes_a_cost_account():
    from catia_mcp.tools import measurement as M

    tools = _assembly_tools()
    reply = json.loads(tools._assembly_inertia(_doc("ASM.CATProduct")))
    assert reply["poses_route"] in {"one_call", "per_component"}
    assert "parts_ms" in reply and "overhead_ms" in reply
    breakdown = reply["overhead_breakdown"]
    assert round(breakdown["components_doc_ms"] + breakdown["walk_other_ms"]
                 + breakdown["report_ms"], 1) == reply["overhead_ms"]
    assert round(reply["parts_ms"] + reply["overhead_ms"], 1) == reply["elapsed_ms"]


# ─────────────── 8. a named measurement gives the active document back
def test_measuring_a_named_part_restores_the_document_that_was_in_front():
    """Found while verifying report 2's P1 on a real machine, not reported.

    `_tensor_for` borrowed the activation (the tensor only exists for the active
    document) and restored it BY NAME. With two documents sharing a name it
    restored the wrong one - measured on B30: the active document went from the
    unsaved `v36_dup.CATPart` to the saved copy after a named `catia_get_inertia`,
    so the NEXT no-argument call measured the other model.
    """
    from catia_mcp.tools import measurement as M

    class _App:
        ActiveDocument = None

    class _Shapes:
        Count = 1

    class _Part:
        def __init__(self, doc):
            self.Parent = doc
            self.Name = "v36_dup"
            self.MainBody = SimpleNamespace(Shapes=_Shapes())

    app = _App()

    class _Doc:
        def __init__(self, name):
            self.Name = name
            self.activations = 0

        def Activate(self):
            self.activations += 1
            app.ActiveDocument = self

    front = _Doc("v36_dup.CATPart")          # what the caller had in front
    measured = _Doc("v36_dup.CATPart")       # same name, different document
    app.ActiveDocument = front

    tools = M.MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(app=app, active_document=front,
                                 documents=SimpleNamespace(Count=2,
                                                           Item=lambda i: [front, measured][i - 1]))
    tools._evaluate_floats = lambda *_a, **_k: [0.0] * 10
    assert tools._tensor_for(_Part(measured)) is not None
    assert measured.activations == 1
    assert front.activations == 1, "the document in front has to be put back"
    assert app.ActiveDocument is front, "restored by OBJECT, not by name"
