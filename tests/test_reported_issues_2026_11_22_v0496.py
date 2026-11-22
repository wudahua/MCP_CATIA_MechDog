"""The 2026-11-22 reports (both of them), as tests.

Each test names the item it pins:

* P8  a refusal that DECLARES ``UNSUPPORTED_CAPABILITY`` keeps its own message;
      the generic envelope may only explain a failure nobody explained.
* P7  the assembly-constraint probe reports how many documents it left open,
      read from the session instead of promised in a sentence.
* P9  repeated mesh warnings collapse into one line, and a part number shared by
      two documents is counted rather than repeated.
* P1-21 a two-distance bevel reads its MODE back, reports the volume removed
      against the ideal L*a*b/2, and can be flipped with swap_distances.
* P2-23 a close that is not confirmed says what was CHECKED, and the deep walk
      finds a part nested two levels down.
* P2-20 the vertex count now has a rule that separates "fewer places" from
      "the same place counted twice".
"""

from __future__ import annotations

import json
import types

import pytest

from catia_mcp.results import CodedError


# ── P8: the envelope must not talk over a site that knows the answer ────────
def _server_with(tool_name, execute):
    from catia_mcp.server import CATIAMCPServer

    server = CATIAMCPServer()
    server.connection.ensure_connected = lambda: None

    class _Module:
        pass

    module = _Module()
    module.execute = staticmethod(execute)
    server._tool_router = {tool_name: module}
    return server


def _call(server, tool_name, arguments=None):
    import asyncio

    return asyncio.run(server.call_tool(tool_name, arguments or {}))


def test_a_declared_unsupported_capability_keeps_its_own_message():
    def execute(name, arguments):
        raise CodedError(
            "Fix constraint: NOT AVAILABLE ON THIS BUILD - refused here, so "
            "nothing was changed. WHICH LAYER REFUSES IS NOT ESTABLISHED: "
            "nothing here can name a licence or a configuration as the cause.",
            code="UNSUPPORTED_CAPABILITY", stage="validate", modified=False)

    server = _server_with("catia_fix_constraint", execute)
    result = _call(server, "catia_fix_constraint", {"component_name": "P05.1"})
    message = result.structuredContent["message"]
    assert result.structuredContent["code"] == "UNSUPPORTED_CAPABILITY"
    assert "WHICH LAYER REFUSES IS NOT ESTABLISHED" in message
    # The 2026-11-22 report (P8) got both sentences at once: the measured body
    # from the site, and "a stale pywin32 gen_py cache ... a missing workbench
    # licence can also lock methods" from the envelope.
    assert "gen_py" not in message
    assert "workbench licence" not in message
    assert result.structuredContent["data"]["failure_stage"] == "validate"
    assert result.structuredContent["effects"]["modified"] is False


def test_an_undeclared_missing_member_still_gets_the_guidance():
    def execute(name, arguments):
        raise AttributeError("Missing AddNewPad")

    server = _server_with("catia_new_part", execute)
    result = _call(server, "catia_new_part")
    message = result.structuredContent["message"]
    assert result.structuredContent["code"] == "UNSUPPORTED_CAPABILITY"
    # A bare AttributeError is the one case where nobody has said what happened,
    # so the envelope has to: the two possibilities, and where to look.
    assert "catia_diagnose" in message and "absent" in message
    assert "gen_py" in message


# ── P7: the probe counts what it left open ─────────────────────────────────
def _probe_with(documents):
    from catia_mcp.tools.diagnostics import DiagnosticsTools

    tools = DiagnosticsTools(connection=None)
    conn = types.SimpleNamespace()

    class _Docs:
        Count = len(documents)

        @staticmethod
        def Item(index):
            return types.SimpleNamespace(Name=documents[index - 1])

    conn.documents = _Docs()
    conn.app = types.SimpleNamespace(
        Documents=types.SimpleNamespace(Add=lambda kind: (_ for _ in ()).throw(
            RuntimeError("no CATIA in this test"))),
        GetWorkbenchId=lambda: "Assembly")
    tools.conn = conn
    return tools


def test_the_probe_reports_zero_documents_left_open():
    tools = _probe_with(["Mine.CATPart"])
    payload = tools._probe_assembly_constraints()
    data = payload["data"]
    assert data["documents_left_open"] == 0
    assert data["documents_added"] == []
    assert "as the caller left it" in data["documents_left_open_note"]
    # The sentence the description stopped making, and the field that replaced
    # it (reported 2026-11-22, P7: the description deleted the claim while the
    # runtime kept it).
    assert "WITHOUT saving" not in data["documents_used"]


# ── P9: one line per statement ────────────────────────────────────────────
def test_repeated_warnings_collapse_into_one_line():
    from catia_mcp.tools.measurement import _group_same_warnings

    note = "this part's document differs from its file in memory, so these " \
           "numbers are the FILE's; save the part and measure again"
    warnings = [f"P01_frame: {note}", f"P02_crankdisk: {note}",
                f"P03_crankarm: {note}", "a short note"]
    grouped = _group_same_warnings(warnings)
    assert len(grouped) == 2
    assert grouped[0].startswith("3 parts (P01_frame, P02_crankdisk, P03_crankarm)")
    assert note in grouped[0]
    assert grouped[1] == "a short note"


def test_two_documents_with_one_part_number_are_counted_not_repeated():
    from catia_mcp.tools.measurement import _group_same_warnings

    note = "x" * 60
    grouped = _group_same_warnings([f"P05_legrod: {note}",
                                    f"P05_legrod: {note}"])
    assert grouped[0].startswith("2 parts (P05_legrod x2)")
    assert grouped[0].count("P05_legrod") == 1


def test_different_statements_never_merge():
    from catia_mcp.tools.measurement import _group_same_warnings

    grouped = _group_same_warnings(["A: " + "x" * 60, "B: " + "y" * 60])
    assert len(grouped) == 2
    assert grouped[0].startswith("A: ") and grouped[1].startswith("B: ")


def test_the_mesh_note_says_what_saved_false_can_mean():
    import inspect

    from catia_mcp.tools import measurement

    source = inspect.getsource(measurement.MeasurementTools._mesh_export)
    # Measured on B30 2026-11-22: a part loaded through its assembly answers
    # Saved=true, so a false flag is a real difference - and a colour counts.
    assert "Saved=false" in source
    assert "cannot move this box" in source
    assert "UNSAVED edits, which are not in these numbers" not in source


# ── P1-21: the bevel's read-back ──────────────────────────────────────────
class _Feature:
    """A Chamfer whose Mode reads and whose lengths do not (as on B30)."""

    Name = "倒角.1"
    Mode = 0

    def __getattr__(self, item):
        if item in ("Length", "SecondLength"):
            raise Exception(f"Item.{item}")
        raise AttributeError(item)


def test_the_read_back_reports_the_mode_and_admits_the_lengths():
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    text = tools._chamfer_readback(_Feature(), 25.0, 20.0, 45.0,
                                   1_000_000.0, 975_000.0,
                                   ["Edge.1 (L100.0000|Sketch.1;4|Pad.1;2)"])
    assert "Mode=0" in text and "catLengthLength" in text
    assert "not readable on this build" in text
    assert "values that were SENT" in text
    assert "Removed 25000.0 mm3" in text
    assert "25000.0 mm3" in text.split("Removed")[-1]
    assert "swap_distances=true" in text


def test_a_length_angle_bevel_has_no_swap_promise():
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    text = tools._chamfer_readback(_Feature(), 25.0, None, 20.0, None, None,
                                   ["Edge.1 (L100.0000)"])
    assert "25 mm at 20 deg" in text
    assert "swap_distances" not in text
    assert "Removed" not in text            # no volume was measured


def test_the_ideal_volume_is_the_triangle_and_needs_a_length():
    from catia_mcp.tools.part_design import PartDesignTools

    two = PartDesignTools._chamfer_ideal(25.0, 20.0, 45.0,
                                         ["Edge.1 (L100.0000)"])
    assert two == (25_000.0, [100.0])
    angle = PartDesignTools._chamfer_ideal(25.0, None, 20.0,
                                           ["Edge.1 (L100.0000)"])
    assert angle is not None and abs(angle[0] - 11_374.0) < 20.0
    # A label without a length is not guessed at: a wrong "expected" number
    # reads as a defect in CATIA.
    assert PartDesignTools._chamfer_ideal(25.0, 20.0, 45.0, ["Edge.1"]) is None


def test_swap_distances_exchanges_the_two_values_before_building():
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    tools.conn = types.SimpleNamespace(
        ensure_connected=lambda: None,
        get_active_part=lambda: types.SimpleNamespace(ShapeFactory=object()))
    tools._prepare_body = lambda part: object()
    seen = {}

    def _one(length, angle, edge_name, length2=None):
        seen.update(length=length, angle=angle, edge=edge_name, length2=length2)
        return "built"

    tools._chamfer_one = _one
    tools._chamfer({"length": 25, "length2": 20, "swap_distances": True,
                    "edge_name": "Edge.1"})
    assert seen == {"length": 20.0, "angle": 45, "edge": "Edge.1",
                    "length2": 25.0}


def test_swap_distances_without_a_second_distance_is_refused():
    from catia_mcp.tools.part_design import PartDesignTools
    from catia_mcp.results import ArgumentRejected

    tools = PartDesignTools(connection=None)
    with pytest.raises(ArgumentRejected) as refused:
        tools._chamfer({"length": 25, "swap_distances": True,
                        "edge_name": "Edge.1"})
    assert "needs length2" in str(refused.value)
    assert "Nothing was created" in str(refused.value)


# ── P2-23: the counts behind the close refusal ────────────────────────────
def test_the_deep_walk_finds_a_part_two_levels_down():
    from catia_mcp.connection import CATIAConnection

    part_doc = types.SimpleNamespace(Name="Held.CATPart")

    class _Products:
        def __init__(self, children):
            self.Count = len(children)
            self._children = children

        def Item(self, index):
            return self._children[index - 1]

    inner = types.SimpleNamespace(
        Name="Inner.1",
        ReferenceProduct=types.SimpleNamespace(
            Parent=types.SimpleNamespace(Name="Inner.CATProduct")),
        Products=_Products([types.SimpleNamespace(
            Name="Held.1",
            ReferenceProduct=types.SimpleNamespace(Parent=part_doc))]))

    class _Conn(CATIAConnection):
        """`documents` is a property on the real class, so it is overridden."""

        @property
        def documents(self):
            return types.SimpleNamespace(
                Count=1,
                Item=lambda index: types.SimpleNamespace(Name="Outer.CATProduct"))

        def product_of(self, doc):
            return types.SimpleNamespace(Products=_Products([inner]))

    conn = object.__new__(_Conn)

    shallow = conn.component_usage()
    deep = conn.component_usage(deep=True)
    # The shallow walk answers the wrong question about a part two levels down:
    # it names the sub-assembly, never the part. That is what made the close
    # reply assert "no assembly references it" (reported 2026-11-22, P2-23).
    assert "Held.CATPart" not in shallow
    assert deep["Held.CATPart"] == ["Outer.CATProduct"]


# ── P2-20: fewer places, or one place counted twice? ──────────────────────
def test_the_vertex_list_says_how_many_distinct_positions_it_holds():
    from catia_mcp.tools.planner import PlannerTools

    source = __import__("inspect").getsource(PlannerTools._vertex_point_lists)
    assert "distinct_vertex_positions" in source
    assert "vertices_sharing_a_position" in source
    # The count is a property of the POINTS, so it has to be built from the
    # returned list rather than from the cell count.
    assert "for point in points:" in source
