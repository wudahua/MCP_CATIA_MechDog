"""0.3.37 - the two acceptance reports of 2026-10-28 against 0.3.36.

Report 1 (`重名提示把每份文档算成自己的孪生导致假告警`): new P1 - the duplicate
name WARNING compared document handles with ``is``, which pywin32 never satisfies,
so every document was its own twin: a brand-new part was told "another document is
already called X (1 of them)" and activating a unique name was told "2 open
documents carry that name". P2 - ``DOCUMENT_NAME_AMBIGUOUS`` lost both of its
``next_actions`` whenever it came from a read-only tool, because the recovery block
is suppressed there. P3 - ``overhead_breakdown`` grew a fourth SUB-bucket, so the
documented ``sum(breakdown.values())`` over-counted.

Report 2 (`八条全关且重名文档P1落地但暖态涨16%冷态只涨3%`): §6.1 ``cold_warm_note``
still quoted one session's 1.30x / 15.10x as if they were constants; §6.2 the
``catia_get_inertia`` PART route had no cost fields at all; §6.3 ``tensor_reason``
called an independent part "a component of an open assembly"; §6.4 the post-cold
note pointed at a "previous warm call quoted above" that a first warm call does not
have; §6.5 the 50x difference between the two aggregators' ``walk_other_ms`` needed
a sentence.

Its §3 asked the author to decide, not guess, whether the +16.4 % warm regression
was code. It is not: the per-cell sweep is byte-identical between 0.3.35 and 0.3.36,
and a same-session A/B on B30 measures the sweep itself at 38-40 ms on both. What
DID get slower is the per-CALL name resolver 0.3.36 introduced (28.5 ms of
``FullName`` reads on 8 open documents), and that is what this release fixes back
down - see ``_document_rows(with_paths=False)`` and the last test here.
"""

import json
import inspect
from types import SimpleNamespace

import pytest


# ─────────────── 1. a document is not its own twin (report 1, P1)
class _LiveDoc:
    """One document handle, the way pywin32 hands them out.

    ``Documents.Item(i)`` builds a NEW Python wrapper on every access, so
    ``Item(1) is Item(1)`` is False while ``Item(1) == Item(1)`` is True - the
    measurement both reports quote. Equality has to come from the document the
    wrapper stands for, not from the wrapper.
    """

    def __init__(self, name, full, uid, session):
        self.Name = name
        self.FullName = full
        self._uid = uid
        self._session = session

    def Activate(self):
        self._session["active"] = self._uid

    def __eq__(self, other):
        return isinstance(other, _LiveDoc) and other._uid == self._uid

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(("doc", self._uid))


def _live_conn(docs, active=1):
    """A connection whose Documents collection mints a fresh wrapper each read."""
    from catia_mcp.connection import CATIAConnection

    session = {"active": active}

    def make(index):
        name, full = docs[index - 1]
        return _LiveDoc(name, full, index, session)

    class _Coll:
        Count = len(docs)

        def Item(self, i):
            return make(i)

    class _App:
        # `is_connected` probes `Caption`; without it the connection decides it is
        # NOT connected and reaches for the live CATIA (which is a real machine
        # here - the first version of this test measured the real session).
        Caption = "CATIA V5"
        Documents = _Coll()

        @property
        def ActiveDocument(self):
            return None if session["active"] is None else make(session["active"])

        def RefreshDisplay(self):
            return None

    conn = CATIAConnection()
    conn.app = _App()
    conn.refresh_display = lambda: None
    return conn, session


PAIR = [("DUP37.CATPart", "E:\\bench\\DUP37.CATPart"),
        ("DUP37.CATPart", "DUP37.CATPart")]


def test_a_document_is_not_its_own_twin():
    """The regression: `is not doc` is true for the document itself."""
    from catia_mcp.tools.document import DocumentTools

    conn, _ = _live_conn([("SOLO37.CATPart", "SOLO37.CATPart")], active=1)
    tools = DocumentTools(conn)
    assert tools._twin_documents(conn.documents.Item(1)) == []


def test_the_other_copy_is_still_found():
    from catia_mcp.tools.document import DocumentTools

    conn, _ = _live_conn(PAIR, active=1)
    tools = DocumentTools(conn)
    twins = tools._twin_documents(conn.documents.Item(1))
    assert len(twins) == 1
    assert twins[0]["doc"] == conn.documents.Item(2)
    assert twins[0]["doc"] != conn.documents.Item(1)


def test_one_comparison_is_shared_by_the_listing_and_the_warning():
    """`connection` marks the active row with the same test, and it was right."""
    conn, _ = _live_conn(PAIR, active=2)
    rows = conn._document_rows()
    assert [row["active"] for row in rows] == [False, True]


def test_a_fresh_name_gets_no_warning_at_all():
    from catia_mcp.tools.document import DocumentTools

    conn, _ = _live_conn([("SOLO37.CATPart", "SOLO37.CATPart")], active=1)
    tools = DocumentTools(conn)
    assert tools._name_collision_note(conn.documents.Item(1)) == ""


def test_the_collision_note_counts_the_other_document_once():
    from catia_mcp.tools.document import DocumentTools

    conn, _ = _live_conn(PAIR, active=1)
    tools = DocumentTools(conn)
    note = tools._name_collision_note(conn.documents.Item(2))
    assert "already called 'DUP37.CATPart' (1 of them)" in note


def test_activating_a_unique_name_does_not_claim_two_documents():
    from catia_mcp.tools.document import DocumentTools

    conn, session = _live_conn([("SOLO37.CATPart", "SOLO37.CATPart")], active=None)
    tools = DocumentTools(conn)
    reply = tools._activate_document("SOLO37.CATPart")
    assert reply == "Activated document: 'SOLO37.CATPart'"
    assert "carry that name" not in reply
    assert session["active"] == 1


def test_activating_one_of_a_pair_says_two():
    from catia_mcp.tools.document import DocumentTools

    conn, _ = _live_conn(PAIR, active=1)
    tools = DocumentTools(conn)
    reply = tools._activate_document("DUP37.CATPart")
    assert "2 open documents carry that name" in reply


# ─────────────── 2. a read-only failure keeps its next actions (report 1, P2)
def _envelope(**kwargs):
    from catia_mcp.results import operation_result

    return operation_result(tool="catia_get_inertia", ok=False,
                            code="DOCUMENT_NAME_AMBIGUOUS", message="ambiguous",
                            modified=False, **kwargs)


def test_a_read_only_failure_publishes_next_actions_in_data():
    from catia_mcp.results import NO_RECOVERY

    payload = _envelope(data={"candidates": [{"document": "A.CATPart"}]},
                        recovery=NO_RECOVERY,
                        next_actions=["Pass the full path of the one you mean"])
    # The rule from 0.3.34 stands: no model repair advice for a tool that cannot
    # write. The ARGUMENT advice has to survive it, though (report 1, P2).
    assert "recovery" not in payload
    assert payload["data"]["next_actions"] == [
        "Pass the full path of the one you mean"]
    assert "does not modify the model" in payload["data"]["next_actions_note"]
    assert payload["data"]["candidates"] == [{"document": "A.CATPart"}]


def test_a_writing_tool_still_puts_them_in_the_recovery_block():
    payload = _envelope(data={}, recovery=None,
                        next_actions=["Pass the full path of the one you mean"])
    assert payload["recovery"]["next_actions"] == [
        "Pass the full path of the one you mean"]
    assert "next_actions" not in payload["data"]


def test_nothing_is_invented_when_the_raise_site_said_nothing():
    from catia_mcp.results import NO_RECOVERY

    payload = _envelope(data={}, recovery=NO_RECOVERY, next_actions=None)
    assert "recovery" not in payload
    assert "next_actions" not in payload["data"]


# ─────────────── 3. summing the cost block is safe again (report 1, P3)
def _measurement_tools():
    from catia_mcp.tools import measurement as M

    class _Part:
        def __init__(self, name):
            self.Name = name
            self.MainBody = SimpleNamespace(Name="PartBody",
                                            Shapes=SimpleNamespace(Count=1))

    class _PartDoc:
        def __init__(self, name):
            self.Name = name

    tools = M.MeasurementTools(connection=None)
    part = _Part("P37")
    rows = [{"index": 1, "name": "P37.1", "part_number": "P37",
             "matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
             "translation": [0.0, 0.0, 0.0], "part": part,
             "nested_doc": None, "kind": "part", "problems": []}]
    tools.conn = SimpleNamespace(ensure_connected=lambda: None, active_document=None)

    def _entry(part_, body, curved):
        return {"points": [[0.0, 0.0, 0.0], [10.0, 6.0, 4.0]], "used": 8,
                "source": "vertices", "mesh_vertices": None, "copy_note": "",
                "cells": 60, "nls_chars": 2388, "points_route": "batched",
                "sweep_route": "one_call"}

    def _components(doc, only=None):
        tools._poses_route = "single" if only else "one_call"
        return rows

    tools._components_doc = _components
    tools._part_box_entry = _entry
    tools._part_identity = lambda part_: part_.Name
    return tools


def test_the_lookup_time_is_published_beside_the_breakdown_not_inside_it():
    from catia_mcp.tools import measurement as M

    M.reset_session_books()
    tools = _measurement_tools()
    inner = tools._components_doc

    def _narrowed(doc, only=None):
        rows = inner(doc, only)
        tools._poses_route, tools._lookup_ms = "single", 250.0
        return rows

    tools._components_doc = _narrowed
    reply = json.loads(tools._assembly_box(SimpleNamespace(Name="ASM.CATProduct"),
                                           "vertices", only="P37.1"))
    breakdown = reply["overhead_breakdown"]
    assert reply["component_lookup_ms"] == 250.0
    assert set(breakdown) == {"components_doc_ms", "walk_other_ms", "report_ms"}
    # The contract 0.3.34 published, and the one a caller wrote code against:
    # the block SUMS to overhead_ms. A fourth key broke it silently (P3).
    assert round(sum(breakdown.values()), 1) == reply["overhead_ms"]
    assert "summing this dict is safe" in reply["overhead_note"]
    assert "SIBLING" in reply["overhead_note"]
    M.reset_session_books()


# ─────────────── 4. the cold/warm note quotes no multiplier (report 2, §6.1)
def test_the_cold_ratio_note_quotes_no_multiplier():
    """0.3.38: the range this test used to require was falsified one session later.

    The rule this release adopted, and the reason the assertions reversed: a range
    is published only if it holds in three independent sessions. Here it held for
    one - the 0.3.36 readings ("1.15x-1.81x on 12-34-cell parts") were followed by
    1.93x-3.70x on every 12-cell part and 18.84x on P01 (reported 2026-10-29,
    §6.2). So the note publishes the mechanism and points at the two fields that
    carry THIS call's reading, and any \"N.NNx\" in it is a regression.
    """
    import re

    from catia_mcp.tools.measurement import _COLD_RATIO_NOTE

    assert not re.search(r"\d+\.\d+x", _COLD_RATIO_NOTE), _COLD_RATIO_NOTE
    assert "cold_ms" in _COLD_RATIO_NOTE and "cold_warm_ratio" in _COLD_RATIO_NOTE
    assert "session" in _COLD_RATIO_NOTE


# ─────────────── 5. the part route has a cost account (report 2, §6.2)
class _Measurable:
    Volume = 8.0e-6
    Area = 2.0e-3

    class _Cog:
        def GetCOG(self):  # pragma: no cover - only exists to be probed
            return [0.0, 0.0, 0.0]

    def GetCOG(self):
        return [0.0, 0.0, 2.5]


class _PartForCost:
    def __init__(self):
        self.Name = "P37.CATPart"
        self.MainBody = SimpleNamespace(Name="PartBody",
                                        Shapes=SimpleNamespace(Count=1))
        measurable = _Measurable()
        self.Parent = SimpleNamespace(
            Name="P37.CATPart",
            FullName="P37.CATPart",
            GetWorkbench=lambda name: SimpleNamespace(
                GetMeasurable=lambda ref: measurable))
        self.CreateReferenceFromObject = lambda obj: obj


def test_the_part_route_publishes_the_same_two_buckets():
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None, app=None,
                                 component_usage=lambda: {})
    tools._read_measurable_array = (
        lambda measurable, method, count: [0.0, 0.0, 2.5])
    tools._tensor_for = lambda part: None
    reply = json.loads(tools._part_inertia(_PartForCost(), 2700.0, "P37.CATPart"))
    assert "parts_ms" in reply and "measure_ms" in reply and "tensor_ms" in reply
    assert "overhead_note" in reply
    assert reply["volume_mm3"] == 8000.0
    # Stamped at the exit, from the elapsed the caller sees.
    stamped = json.loads(tools._stamp_elapsed(json.dumps(reply), 0.0))
    assert round(stamped["parts_ms"] + stamped["overhead_ms"],
                 1) == stamped["elapsed_ms"]


def test_the_tensor_step_is_timed_because_a_failed_one_still_costs():
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None, app=None,
                                 component_usage=lambda: {})
    tools._read_measurable_array = (
        lambda measurable, method, count: [0.0, 0.0, 2.5])
    slept = {"how": 0}

    def _slow(part):
        import time
        time.sleep(0.02)
        slept["how"] += 1
        tools._tensor_why = "activation_refused"
        return None

    tools._tensor_for = _slow
    reply = json.loads(tools._part_inertia(_PartForCost(), None, "P37.CATPart"))
    assert slept["how"] == 1
    assert reply["tensor_ms"] >= 15.0
    assert "borrowing the activation" in reply["overhead_note"]


# ─────────────── 6. "nested in an assembly" is a claim to check (report 2, §6.3)
def _refusal(holders, why="activation_refused"):
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(component_usage=lambda: holders)
    tools._tensor_why = why
    return tools._tensor_refusal(SimpleNamespace(Name="P37.CATPart"), "P37.CATPart")


def test_a_part_in_an_assembly_is_reported_as_nested_and_the_assembly_is_named():
    code, reason = _refusal({"P37.CATPart": ["ASM37.CATProduct"]})
    assert code == "nested_in_assembly"
    assert "ASM37.CATProduct" in reason


def test_a_part_in_no_assembly_is_not_accused_of_being_nested():
    code, reason = _refusal({})
    assert code == "not_activated"
    assert "in no open assembly" in reason
    assert "catia_activate_document" in reason


def test_a_part_that_could_not_be_measured_says_so_instead():
    code, reason = _refusal({}, why="unreadable")
    assert code == "unreadable"
    assert "catia_measure_topology" in reason


# ─────────────── 7. the post-cold note only cites a previous warm call it has
def test_the_post_cold_note_does_not_cite_a_missing_sibling_field():
    from catia_mcp.tools import measurement as M

    M.reset_session_books()
    tools = _measurement_tools()
    json.loads(tools._assembly_box(SimpleNamespace(Name="ASM.CATProduct"), "vertices"))
    note = json.loads(
        tools._assembly_box(SimpleNamespace(Name="ASM.CATProduct"),
                            "vertices"))["elapsed_note"]
    assert "FIRST call after a cold pass" in note
    # This call IS the first warm one: there is no previous warm delta in the
    # reply, so pointing at "quoted above" pointed at another session (report 2,
    # §6.4).
    assert "quoted above" not in note
    assert "no earlier warm call on this document in this session yet" in note
    # ...and it must not claim a delta it cannot have: with no previous warm
    # reading of this scope there is nothing to subtract, and inventing "0.0 %"
    # is how a first reading turns into a "trend".
    assert "against" not in note
    M.reset_session_books()


# ─────────────── 8. the two aggregators' walk_other_ms are different jobs
def test_the_inertia_cost_note_explains_its_small_walk_block():
    from catia_mcp.tools.measurement import MeasurementTools

    note = inspect.getsource(MeasurementTools._assembly_inertia)
    assert "one centroid per instance" in note
    assert "463-486 ms" in note


# ─────────────── 9. the resolver does not read what it does not need
class _CountingDoc:
    def __init__(self, name, full):
        self._name = name
        self._full = full
        self.reads = []

    @property
    def Name(self):
        self.reads.append("name")
        return self._name

    @property
    def FullName(self):
        self.reads.append("full")
        return self._full


def test_a_unique_name_costs_names_only():
    from catia_mcp.connection import CATIAConnection

    docs = [_CountingDoc("A.CATPart", "E:\\a\\A.CATPart"),
            _CountingDoc("B.CATProduct", "E:\\a\\B.CATProduct")]

    class _Coll:
        Count = len(docs)

        def Item(self, i):
            return docs[i - 1]

    conn = CATIAConnection()
    conn.app = SimpleNamespace(Documents=_Coll(), ActiveDocument=docs[0],
                           Caption="CATIA V5")
    doc, matches = conn.resolve_document("A.CATPart")
    assert doc is not None and len(matches) == 1
    # FullName is the expensive read (report 1 P3 / A/B of 2026-10-28): a name
    # that resolves uniquely must not pay for it.
    assert all("full" not in d.reads for d in docs)


def test_a_path_key_still_matches_the_saved_copy():
    from catia_mcp.connection import CATIAConnection

    docs = [_CountingDoc("A.CATPart", "E:\\a\\A.CATPart"),
            _CountingDoc("A.CATPart", "A.CATPart")]

    class _Coll:
        Count = len(docs)

        def Item(self, i):
            return docs[i - 1]

    conn = CATIAConnection()
    conn.app = SimpleNamespace(Documents=_Coll(), ActiveDocument=docs[0],
                           Caption="CATIA V5")
    doc, matches = conn.resolve_document("E:/a/A.CATPart")
    assert doc is not None and doc.FullName == "E:\\a\\A.CATPart"
    # The escape hatch the error offers: a path names ONE copy - which is exactly
    # why passing it is the fix for an ambiguous bare name.
    assert len(matches) == 1


def test_two_copies_still_resolve_to_neither():
    from catia_mcp.connection import CATIAConnection

    docs = [_CountingDoc("A.CATPart", "E:\\a\\A.CATPart"),
            _CountingDoc("A.CATPart", "A.CATPart"),
            _CountingDoc("B.CATProduct", "E:\\a\\B.CATProduct")]

    class _Coll:
        Count = len(docs)

        def Item(self, i):
            return docs[i - 1]

    conn = CATIAConnection()
    conn.app = SimpleNamespace(Documents=_Coll(), ActiveDocument=docs[0],
                           Caption="CATIA V5")
    doc, matches = conn.resolve_document("A.CATPart")
    assert doc is None
    assert [row["saved"] for row in matches] == [True, False]
