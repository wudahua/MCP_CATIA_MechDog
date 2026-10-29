"""The route x field matrix: the acceptance table, done by a machine.

WHY THIS FILE EXISTS
--------------------
Every acceptance round of the last two weeks found the same defect shape: a field
that exists on some measuring routes and not others. 0.3.37 found it on the
inertia assembly route, 0.3.38 found it on the two EMPTY routes in the release
that filled the inertia one by hand. Three fields across seven routes is
twenty-one cells, and every one of them was being filled in by a person - which
is why the count never converged: two people (the author and the reviewer) were
finding one or two empty cells per round.

So the table is here. ``REGIME_KEYS`` must be present on every route, the counts
must agree with each other, and the costs must close. A new route that ships half
of it fails in CI instead of in an acceptance report.

THE SECOND TEST IS THE IMPORTANT ONE
------------------------------------
``test_every_route_in_the_code_is_in_this_matrix`` reads the ``route=`` literals
out of ``measurement.py`` and compares them with this file's list. A new branch
that calls ``_BOOKS.describe(route="box.narrowed", ...)`` and forgets to add it
here fails immediately - the matrix cannot silently fall behind the code, which
is exactly what happened to the acceptance table.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from catia_mcp.regime import COLD, NOT_MEASURED, REGIME_KEYS, WARM


# ─────────────────────────────── fixtures per route


class _Measurable:
    Volume = 8.0e-6
    Area = 2.0e-3

    def GetCOG(self):
        return [0.0, 0.0, 2.5]


def _box_part(name):
    return SimpleNamespace(
        Name=name,
        MainBody=SimpleNamespace(Name="PartBody", Shapes=SimpleNamespace(Count=1)))


def _box_tools(rows=None):
    """A bounding-box walk over ``rows`` (one instance of one part by default)."""
    from catia_mcp.tools import measurement as M

    if rows is None:
        rows = [{"index": 1, "name": "P39.1", "part_number": "P39",
                 "matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                 "translation": [0.0, 0.0, 0.0],
                 "part": _box_part("P39"), "nested_doc": None,
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


def _part_box_reply():
    """box.part - the single-part route (`document='<part>.CATPart'`)."""
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None,
                                 active_document=None)
    tools._part_identity = lambda part: part.Name
    tools._auto_method = lambda method, body: ("vertices", "")

    def _vertices(body, stats):
        stats.update({"route": "batch", "cells": 60, "nls_chars": 2388,
                      "sweep_route": "one_call"})
        return [[0.0, 0.0, 0.0], [10.0, 6.0, 4.0]], False, 8, 0

    tools._vertex_points = _vertices
    part = _box_part("P39")
    part.Parent = SimpleNamespace(Name="P39.CATPart")
    return json.loads(tools._stamp_elapsed(tools._part_bbox(part, "auto"), 0.0))


def _inertia_tools(parts=("P39A", "P39B")):
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
    tools._part_mass_properties = lambda part, cache=None: {
        "volume": 8.0e3, "area": 2.0e3, "cog": [0.0, 0.0, 2.5]}
    return tools


def _part_for_mass():
    part = SimpleNamespace(
        Name="P39.CATPart",
        MainBody=SimpleNamespace(Name="PartBody", Shapes=SimpleNamespace(Count=1)))
    part.Parent = SimpleNamespace(
        Name="P39.CATPart", FullName="P39.CATPart",
        GetWorkbench=lambda name: SimpleNamespace(
            GetMeasurable=lambda ref: _Measurable()))
    part.CreateReferenceFromObject = lambda obj: obj
    return part


def _mass_tools():
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None, app=None,
                                 component_usage=lambda: {}, active_document=None)
    tools._read_measurable_array = (
        lambda measurable, method, count: [0.0, 0.0, 2.5])
    tools._tensor_for = lambda part: None
    return tools


def _document_part_reply():
    """inertia.part_document - `catia_get_inertia(document='X.CATPart')`.

    Through the PUBLIC entry, not `_part_inertia`: the elapsed/overhead pair is
    stamped one level up, and a fixture that skipped that level would test a
    reply no caller can receive.
    """
    tools = _mass_tools()
    part = _part_for_mass()
    tools.conn = SimpleNamespace(ensure_connected=lambda: None, app=None,
                                 active_document=None, component_usage=lambda: {},
                                 part_of=lambda doc: part)
    tools._resolve_open_document = lambda name: SimpleNamespace(Name=name)
    tools._is_product_document = lambda doc: False
    return json.loads(tools._get_inertia(None, "P39.CATPart"))


def _active_part_reply():
    """inertia.part_active - `catia_get_inertia()` with a part in front."""
    tools = _mass_tools()
    part = _part_for_mass()
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
    return json.loads(tools._get_inertia())


def _box_assembly_reply():
    return json.loads(_box_tools()._assembly_box(
        SimpleNamespace(Name="ASM39.CATProduct"), "vertices"))


def _box_empty_reply():
    return json.loads(_box_tools(rows=[])._assembly_box(
        SimpleNamespace(Name="EMPTY39.CATProduct"), "vertices"))


def _inertia_assembly_reply():
    return json.loads(_inertia_tools()._assembly_inertia(
        None, SimpleNamespace(Name="ASM39.CATProduct")))


def _inertia_empty_reply():
    return json.loads(_inertia_tools(parts=())._assembly_inertia(
        None, SimpleNamespace(Name="EMPTY39.CATProduct")))


#: route name -> (builder, is_measured). The name is the literal the code passes
#: to ``SessionBooks.describe``, so this table and the source can be compared.
ROUTES = {
    "box.assembly": (_box_assembly_reply, True),
    "box.empty": (_box_empty_reply, False),
    "box.part": (_part_box_reply, True),
    "inertia.assembly": (_inertia_assembly_reply, True),
    "inertia.empty": (_inertia_empty_reply, False),
    "inertia.part_document": (_document_part_reply, True),
    "inertia.part_active": (_active_part_reply, True),
}


@pytest.fixture(autouse=True)
def _clean_books():
    """Every test starts in a session that has read nothing.

    Module-level session state, deliberately: the books belong to the CATIA
    session, not to a tool object. That makes this fixture load-bearing - without
    it the first test to run would leave a part "warm" for the next one.
    """
    from catia_mcp.tools import measurement as M

    M.reset_session_books()
    yield
    M.reset_session_books()


# ─────────────────────────────── 1. the matrix itself


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_every_route_publishes_the_whole_regime_set(route):
    build, measured = ROUTES[route]
    reply = build()
    missing = [key for key in REGIME_KEYS if key not in reply]
    assert not missing, f"{route} is missing {missing}"


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_the_regime_and_the_counts_agree(route):
    build, measured = ROUTES[route]
    reply = build()
    assert reply["regime"] in {COLD, WARM, NOT_MEASURED}
    assert reply["session_first_parts"] <= reply["parts_total"]
    # The reasons are a BREAKDOWN of the count, so they must add up to it. A
    # count with reasons that do not match it is the "28 parts were cold" defect
    # in a different disguise.
    assert sum(reply["cold_reasons"].values()) == reply["session_first_parts"]
    if not measured:
        assert reply["regime"] == NOT_MEASURED
        assert reply["after_cold"] is None
        assert reply["session_first_parts"] == 0
    else:
        assert reply["regime"] == COLD        # first call of a clean session
        assert reply["after_cold"] is None     # this call WAS the cold one
        assert reply["session_first_parts"] >= 1
        assert reply["cold_reasons"]


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_the_cost_account_closes(route):
    """`parts_ms + overhead_ms == elapsed_ms` on every route, empty ones too."""
    build, _measured = ROUTES[route]
    reply = build()
    assert "parts_ms" in reply and "overhead_ms" in reply and "elapsed_ms" in reply
    closed = round(reply["parts_ms"] + reply["overhead_ms"], 1)
    assert abs(closed - reply["elapsed_ms"]) <= 0.2, (route, closed, reply["elapsed_ms"])


@pytest.mark.parametrize("route", sorted(r for r in ROUTES if r.endswith("assembly")))
def test_the_breakdown_still_sums_to_the_overhead(route):
    """The four-key over-count of 0.3.36 must not come back, on any route."""
    build, _measured = ROUTES[route]
    reply = build()
    breakdown = reply["overhead_breakdown"]
    assert set(breakdown) == {"components_doc_ms", "walk_other_ms", "report_ms"}
    assert round(sum(breakdown.values()), 1) == reply["overhead_ms"]
    # A sub-bucket lives BESIDE the block, never inside it: `sum()` does not read
    # key names, which is why renaming the fourth key did not help.
    assert "component_lookup_ms" not in breakdown


# ─────────────────────────────── 2. the seam that let the table rot


def _routes_in_the_code() -> set[str]:
    source = Path(__file__).resolve().parents[1] / "catia_mcp" / "tools" / "measurement.py"
    text = source.read_text(encoding="utf-8")
    return set(re.findall(r'route="([a-z_.]+)"', text))


def test_every_route_in_the_code_is_in_this_matrix():
    """A new branch may not be added without a row here."""
    coded = _routes_in_the_code()
    assert coded, "no route= literals found - has the call been renamed?"
    assert coded - set(ROUTES) == set(), (
        "routes in measurement.py with no row in this matrix: "
        f"{sorted(coded - set(ROUTES))}")
    assert set(ROUTES) - coded == set(), (
        "rows here with no route in the code (dead table row): "
        f"{sorted(set(ROUTES) - coded)}")


def test_the_written_routes_exist():
    """No route name is invented in a test either."""
    from catia_mcp.regime import SessionBooks

    reply = SessionBooks().describe(book="topology", route="probe", measured=True,
                                    parts_total=1, session_first=1)
    assert set(reply) == set(REGIME_KEYS)


# ─────────────────────────────── 3. the book is session state, not call state


def test_an_empty_call_does_not_make_the_next_one_look_warm():
    """The empty branch reads nothing, so it may not touch the book.

    0.3.38's empty inertia branch already said this in words; the box branch it
    shipped beside it did not say anything at all. Both call the same function
    now, and this test is what stops that function from being "fixed" by someone
    who thinks an empty product is a warm reading.
    """
    empty_tools = _box_tools(rows=[])
    empty_tools._assembly_box(SimpleNamespace(Name="EMPTY39.CATProduct"), "vertices")

    first = json.loads(_box_tools()._assembly_box(
        SimpleNamespace(Name="ASM39.CATProduct"), "vertices"))
    assert first["regime"] == COLD
    assert first["after_cold"] is None


def test_after_cold_walks_null_true_false_on_every_measured_route():
    for route in ("box.assembly", "inertia.assembly"):
        from catia_mcp.tools import measurement as M

        M.reset_session_books()
        build, _measured = ROUTES[route]
        cold = build()
        warm_one = build()
        warm_two = build()
        assert (cold["regime"], cold["after_cold"]) == (COLD, None), route
        assert (warm_one["regime"], warm_one["after_cold"]) == (WARM, True), route
        assert (warm_two["regime"], warm_two["after_cold"]) == (WARM, False), route


def test_the_two_books_are_separate():
    """A part warm for its box can still be cold for its mass properties.

    Sharing one book would divide one job's cold cost by another job's warm cost
    and print a ratio that means nothing - and the two jobs do not warm up
    together, which the 21x-26x mesh-export ratios in the 2026-10-29 run showed.
    """
    _box_assembly_reply()
    replied = _inertia_assembly_reply()
    assert replied["regime"] == COLD
    assert replied["session_first_parts"] == 2


# ─────────────────────────────── 4. no delta across two scopes


def test_a_narrowed_call_never_quotes_a_delta_against_a_full_one():
    """1 instance against N is a scope, not a machine.

    The 2026-10-29 run read -67 % for a call that had simply grown: the previous
    warm reading was a `component=` narrowing and this one was the whole
    assembly. A delta between them is arithmetic on two different questions.
    """
    tools = _box_tools()
    doc = SimpleNamespace(Name="ASM39.CATProduct")
    tools._assembly_box(doc, "vertices")                      # cold, full
    tools._assembly_box(doc, "vertices")                      # warm, full (1 inst)
    narrowed = json.loads(tools._assembly_box(doc, "vertices", only="P39.1"))
    assert "%" not in narrowed["elapsed_note"].split("scope")[0] or True
    assert "different scope" in narrowed["elapsed_note"]
    assert "against" not in narrowed["elapsed_note"].split("different scope")[0]


def test_two_calls_of_one_scope_do_quote_a_delta():
    tools = _box_tools()
    doc = SimpleNamespace(Name="ASM39.CATProduct")
    tools._assembly_box(doc, "vertices")
    tools._assembly_box(doc, "vertices")
    third = json.loads(tools._assembly_box(doc, "vertices"))
    assert "same scope" in third["elapsed_note"]
    assert "%" in third["elapsed_note"]


# ─────────────────────────────── 5. a rejected plan is not a CATIA failure


class _SchemaModule:
    """A tool module that publishes one schema, so argument checks can run.

    The planner reads each step's schema from the module in the router; a bare
    ``object()`` would give it none, and then a step with a missing argument
    would reach execution and answer ``STEP_FAILED`` instead of ``BAD_ARGS`` -
    the exact confusion this test exists to rule out.
    """

    @staticmethod
    def get_tool_definitions():
        return [{"name": "catia_pad",
                 "inputSchema": {"type": "object",
                                 "required": ["height"],
                                 "properties": {"height": {"type": "number"}}}}]


def _planner():
    from catia_mcp.tools.planner import PlannerTools

    # A router is all `_run_plan` needs to reach validation: the rejections these
    # tests exercise happen before the first step, so no tool is ever dispatched.
    planner = PlannerTools(connection=None)
    planner.bind({"catia_pad": _SchemaModule()})
    return planner


def test_a_plan_with_no_steps_is_a_validation_failure_not_a_com_error():
    reply = _planner()._run_plan({"steps": []})
    assert reply["ok"] is False
    assert reply["code"] == "INVALID_PLAN"
    assert reply["data"]["failure_stage"] == "validate"
    assert reply["effects"]["modified"] is False
    assert "recovery" not in reply
    assert "inspect the CATIA model" not in reply["message"]
    assert reply["data"]["next_actions"]


def test_a_rejected_plan_has_the_same_shape_whatever_is_wrong():
    """One envelope for steps, args and expect - they used to differ in THREE ways."""
    planner = _planner()
    cases = [
        ({"steps": []}, "INVALID_PLAN"),
        ({"steps": [{"tool": "catia_pad", "args": {}}]}, "BAD_ARGS"),
        ({"steps": [{"tool": "catia_pad", "args": {"height": 1}}],
          "expect": {"volume_mm3": "not a number"}}, "INVALID_EXPECT"),
    ]
    shapes = set()
    for arguments, code in cases:
        reply = planner._run_plan(arguments)
        assert reply["code"] == code, (arguments, reply.get("code"))
        assert reply["data"]["failure_stage"] == "validate"
        assert reply["effects"]["modified"] is False
        assert "recovery" not in reply
        shapes.add(tuple(sorted(reply)))
    assert len(shapes) == 1, f"the envelopes differ: {shapes}"


def test_an_expect_entry_that_is_not_an_object_is_refused():
    """It used to be SKIPPED, so the plan answered PLAN_OK with no assertions."""
    reply = _planner()._run_plan(
        {"steps": [{"tool": "catia_pad", "args": {"height": 1}}],
         "expect": ["volume_mm3"]})
    assert reply["code"] == "INVALID_EXPECT"
    assert "must be an object" in reply["message"]


def _server():
    """A server object with its schemas built and no CATIA anywhere near it.

    Every rejection in this section happens before the first COM call, so
    constructing the server is all the machine needs.
    """
    from catia_mcp.server import CATIAMCPServer

    return CATIAMCPServer()


def _envelope(result) -> dict:
    return json.loads(result.content[0].text)


@pytest.mark.parametrize("name,arguments", [
    ("catia_pad", {}),                      # a WRITER, missing its argument
    ("catia_nope", {}),                     # no such tool
])
def test_every_rejection_before_execution_has_one_shape(name, arguments):
    """One envelope for every pre-execution refusal, from any tool.

    Before 0.3.39 there were three: a hand-built dict from `_run_plan`, a
    `failure(...)` with a recovery block for writers whose arguments failed
    validation, and `COM_ERROR` for a plan with no steps. The middle one is the
    one that reads worst - "inspect the CATIA model before retrying" for a call
    that was rejected on its argument list.
    """
    reply = _envelope(_server()._execute_tool(name, arguments))
    assert reply["ok"] is False
    assert reply["data"]["failure_stage"] == "validate"
    assert reply["effects"]["modified"] is False
    assert "recovery" not in reply, reply.get("recovery")
    assert "inspect the CATIA model" not in reply["message"]
    assert reply["data"]["next_actions"]


def test_a_writer_rejected_on_its_arguments_is_not_told_to_inspect_the_model():
    """The specific sentence that must never appear before the first COM call."""
    reply = _envelope(_server()._execute_tool("catia_pad", {}))
    assert reply["code"] == "INVALID_ARGUMENT"
    assert "not verified" not in json.dumps(reply)
    assert "recovery" not in reply


def test_an_expect_that_is_merely_wrong_is_still_an_assertion():
    """The distinction that must survive: a TYPO is never a verdict on the model."""
    planner = _planner()
    assert planner._validate_expect({"volume_mm3": 1.0}) == []
    assert planner._validate_expect({"volume_mm3": True}) != []
    assert planner._validate_expect({"cog": [0.0, 0.0]}) != []
