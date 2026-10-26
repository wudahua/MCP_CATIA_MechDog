"""Planner assertions, plan validation and matrix reporting - no CATIA needed.

These cover the parts of the lite fork that decide whether a build is correct,
because those are the parts that must not silently drift. The COM-facing code
is exercised by hand against a live session (see FORK.md).
"""

import json
from types import SimpleNamespace

import pytest

from catia_mcp.dialogs import _normalize
from catia_mcp.tools.assembly import AssemblyTools
from catia_mcp.tools.planner import PlannerTools


def planner() -> PlannerTools:
    # The connection is only touched by the COM-facing helpers, not by these.
    return PlannerTools(connection=None)


# ---- assertions ------------------------------------------------------------


def test_volume_within_tolerance_passes():
    checks = planner()._check(
        {"volume_mm3": 1450450, "volume_tolerance_pct": 1.0},
        {"volume_mm3": 1450500.0},
    )
    assert checks[0]["pass"] is True
    assert abs(checks[0]["delta_pct"]) < 0.01


def test_volume_outside_tolerance_fails():
    checks = planner()._check({"volume_mm3": 1000, "volume_tolerance_pct": 0.1},
                             {"volume_mm3": 1010.0})
    assert checks[0]["pass"] is False


def test_topology_counts_are_exact_not_tolerant():
    """A volume judge lets a part grow an isolated boss; counts must not."""
    checks = planner()._check({"faces": 26}, {"faces": 25})
    assert checks[0]["pass"] is False


def test_cog_distance_uses_millimetres():
    checks = planner()._check({"cog": [0, 0, 178.941], "cog_tolerance_mm": 0.5},
                             {"cog": [0.1, -0.1, 178.9]})
    assert checks[0]["pass"] is True
    assert checks[0]["distance_mm"] == pytest.approx(0.1479, abs=1e-3)

    tight = planner()._check({"cog": [0, 0, 178.941], "cog_tolerance_mm": 0.01},
                             {"cog": [0.1, -0.1, 178.9]})
    assert tight[0]["pass"] is False


def test_missing_measurement_does_not_invent_a_pass():
    """It must not pass - and since 2026-10-26 it must not vanish either.

    Dropping the assertion entirely is how `expect: {"document": …, "volume_mm3":
    1.0}` came back as PLAN_OK: the measurement had failed, so no check was built,
    so nothing contradicted the caller's expectation.
    """
    checks = planner()._check({"volume_mm3": 1000}, {})
    assert len(checks) == 1
    assert checks[0]["pass"] is False
    assert checks[0]["actual"] is None
    assert "unavailable" in checks[0]


def test_an_unmeasurable_area_is_a_failure_not_a_skip():
    checks = planner()._check({"area_mm2": 100.0},
                             {"area_mm2": "n/a (Product document)"})
    assert checks[0]["pass"] is False
    assert "Product" in checks[0]["unavailable"]


def test_a_cog_with_a_missing_component_is_a_failure():
    checks = planner()._check({"cog": [0, 0, 1]}, {"cog": [0, None, 1]})
    assert checks[0]["pass"] is False


# ---- plan validation -------------------------------------------------------


def test_plan_rejects_unknown_tool_and_nesting():
    tools = planner()
    tools.bind({"catia_pad": object()})
    problems = tools._validate_steps(
        [
            {"tool": "catia_padd"},
            {"tool": "catia_run_plan"},
            {"tool": "catia_pad"},
            {"args": {}},
        ]
    )
    assert any("unknown tool" in p for p in problems)
    assert any("cannot be nested" in p for p in problems)
    assert any("missing 'tool'" in p for p in problems)


def test_dry_run_touches_nothing():
    tools = planner()
    tools.bind({"catia_pad": object()})
    result = tools._run_plan({"steps": [{"tool": "catia_pad"}], "dry_run": True})
    assert result["ok"] is True
    assert result["code"] == "PLAN_VALID"


def test_invalid_plan_returns_structured_failure():
    tools = planner()
    tools.bind({})
    result = tools._run_plan({"steps": [{"tool": "catia_nope"}]})
    assert result["ok"] is False
    assert result["code"] == "INVALID_PLAN"


def test_step_failure_stops_the_plan():
    calls = []

    def boom(tool, args):
        raise RuntimeError("CATIA said no")

    def ok(tool, args):
        calls.append(tool)
        return "fine"

    tools = planner()
    tools.bind({"catia_pad": SimpleNamespace(execute=boom),
                "catia_get_inertia": SimpleNamespace(execute=ok)})
    result = tools._run_plan(
        {
            "steps": [{"tool": "catia_pad"}, {"tool": "catia_get_inertia"}],
            "stop_on_error": True,
            "measure": False,
        }
    )
    assert result["ok"] is False
    assert result["code"] == "STEP_FAILED"
    assert calls == []  # the second step never ran
    assert result["data"]["executed"] == 1


# ---- matrix reporting ------------------------------------------------------


def test_identity_is_right_handed():
    report = AssemblyTools._matrix_report([1, 0, 0, 0, 1, 0, 0, 0, 1])
    assert report["checks"]["right_handed"] is True
    assert report["checks"]["orthogonal"] is True
    assert report["basis_columns"]["local_x_maps_to"] == [1.0, 0.0, 0.0]
    assert report["rotation_rows"] == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def test_transposed_matrix_is_still_a_rotation():
    """The trap: a transpose keeps det = +1, so det alone never catches it."""
    c, s = 0.0, 1.0
    rows_major = [c, -s, 0, s, c, 0, 0, 0, 1]  # Rz(90) read as rows
    columns = [c, s, 0, -s, c, 0, 0, 0, 1]  # same rotation read as columns
    for values in (rows_major, columns):
        report = AssemblyTools._matrix_report(values)
        assert report["checks"]["det"] == pytest.approx(1.0)
        assert report["checks"]["right_handed"] is True
    # ...but they describe different placements, which is why both are reported.
    assert AssemblyTools._matrix_report(rows_major)["rotation_rows"] != (
        AssemblyTools._matrix_report(columns)["rotation_rows"]
    )


def test_mirrored_matrix_is_flagged():
    report = AssemblyTools._matrix_report([-1, 0, 0, 0, 1, 0, 0, 0, 1])
    assert report["checks"]["det"] == pytest.approx(-1.0)
    assert report["checks"]["right_handed"] is False


# ---- rotation_rows conventions (0.3.4) -------------------------------------
# Two 3x3 spellings of the same placement are in circulation. A spec that says
# "P_asm = Rᵀ·P_local + T, R's rows are the local axes in global" lists one form;
# the maths form P_asm = A·P_local + T is the other. They are transposes of each
# other and both keep det = +1, so picking the wrong one yields a placement that
# passes every sanity check and is still wrong. Field case: a quadcopter landing
# stand meant to rise 189 mm (local Z → global +Y) came out lying on its side,
# and the four motors tilted 60° instead of pointing down.

SPEC_ROWS = [[0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]  # local X→+Z, Y→+X, Z→+Y


def test_matrix_convention_is_unchanged():
    """Default stays the pre-0.3.4 behaviour: row-major A, transposed on the way in."""
    assert AssemblyTools._rotation_components(SPEC_ROWS) == [
        0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0,
    ]
    assert AssemblyTools._rotation_components(SPEC_ROWS, "matrix") == [
        0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0,
    ]


def test_basis_convention_writes_the_rows_verbatim():
    assert AssemblyTools._rotation_components(SPEC_ROWS, "basis") == [
        0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0,
    ]


def test_basis_convention_round_trips_through_the_report():
    flat = AssemblyTools._rotation_components(SPEC_ROWS, "basis")
    report = AssemblyTools._matrix_report(flat)
    assert report["basis_rows"] == SPEC_ROWS
    # local Z is the stand's 189 mm axis and must end up pointing at global +Y
    assert report["basis_columns"]["local_z_maps_to"] == [0.0, 1.0, 0.0]
    assert report["basis_columns"]["local_x_maps_to"] == [0.0, 0.0, 1.0]


def test_matrix_convention_round_trips_through_the_report():
    flat = AssemblyTools._rotation_components(SPEC_ROWS, "matrix")
    report = AssemblyTools._matrix_report(flat)
    assert report["rotation_rows"] == SPEC_ROWS
    assert report["matrix_a"] == SPEC_ROWS
    # the same placement seen as basis vectors is the transpose
    assert report["basis_rows"] == [[0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [1.0, 0.0, 0.0]]


def test_wireframe_geometry_is_ignored_in_counts():
    """Sketch lines come back as Selection_WireREdge; solid ones as Selection_REdge."""

    class Item:
        def __init__(self, name):
            self.Value = SimpleNamespace(Name=name)

    tools = planner()
    tools.conn = SimpleNamespace(_dynamic=lambda value: value)
    assert tools._is_wireframe(Item("Selection_WireREdge:(Wire:...)")) is True
    assert tools._is_wireframe(Item("Selection_REdge:(Edge:...)")) is False
    assert tools._is_wireframe(Item("Selection_WireFVertex:(Vertex:...)")) is True
    assert tools._is_wireframe(Item("Selection_FVertex:(Vertex:...)")) is False


# ---- dialogs ---------------------------------------------------------------


def test_paths_are_normalised_for_catia():
    """CATIA rejects forward slashes in SaveAs with E_FAIL."""
    import os

    assert "\\" in _normalize("C:/temp/a.CATPart") or os.sep == "/"
    assert not _normalize("C:/temp/a.CATPart").endswith("/")


def test_planner_tools_are_registered_in_the_server():
    from catia_mcp.server import CATIAMCPServer

    server = CATIAMCPServer()
    names = set(server._tool_router)
    assert {"catia_run_plan", "catia_measure_topology",
            "catia_get_component_transform", "catia_set_component_transform"} <= names
    # The dropped modules must not linger in the router.
    assert not {"catia_drawing_add_view", "catia_create_parameter"} & names
    assert len(json.dumps(list(names))) > 0


def test_plan_rejects_keys_it_would_silently_ignore():
    """A typo must not turn verification off.

    `assertions` instead of `expect`, or `volume` instead of `volume_mm3`, used
    to validate and then be ignored - the plan reported PLAN_OK with an empty
    assertion list, which is the worst possible answer from a tool whose point
    is verification.
    """
    from jsonschema import ValidationError, validate

    from catia_mcp.server import CATIAMCPServer

    schema = CATIAMCPServer()._schemas["catia_run_plan"]
    for plan in (
        {"steps": [{"tool": "catia_new_part"}], "assertions": {"volume_mm3": 1}},
        {"steps": [{"tool": "catia_new_part"}], "expect": {"volume": 1}},
        {"steps": [{"tool": "catia_new_part", "arg": {}}]},
    ):
        try:
            validate(plan, schema)
        except ValidationError:
            continue
        raise AssertionError(f"schema accepted a key it ignores: {plan}")
