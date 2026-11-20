"""The 2026-11-19 (0.3.151) report: six items, each answered by a number.

Nothing here talks to CATIA. What each test pins is the thing the report asked
for, plus the reason it was asked for, so a later change cannot quietly undo it:

  P0  the constraint tools SAY they are unavailable, before the call, and the
      ceiling entry carries the evidence (sketch control, workbench id).
  P1  transform_13 is the column-major 13-tuple; a transposed feed is visible
      in the read-back as an angle.
  P2  a vertex sweep that stops at the cap is reported as TRUNCATED, not as a
      lower bound - it can miss a whole side.
  P3  the first key of a box reply says what the numbers are.
  P4  save_as_copy can rewrite the copy's part number.
  P5  strict_arc refuses the silent centre move.
"""
from __future__ import annotations

import json
import math

import pytest

from catia_mcp.tools.assembly import AssemblyTools
from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.measurement import MeasurementTools
from catia_mcp.tools.sketcher import SketcherTools
from catia_mcp.results import CodedError


def _rotate_z(degrees: float) -> list[list[float]]:
    t = math.radians(degrees)
    return [[math.cos(t), -math.sin(t), 0.0],
            [math.sin(t), math.cos(t), 0.0],
            [0.0, 0.0, 1.0]]


def _as_13(matrix: list[list[float]], translation=(1.0, 2.0, 3.0),
           scale: float = 1.0) -> list[float]:
    """A column-major 13-tuple, the way SolidWorks writes one."""
    return ([matrix[r][0] for r in range(3)]
            + [matrix[r][1] for r in range(3)]
            + [matrix[r][2] for r in range(3)]
            + list(translation) + [scale])


# ══ P1: transform_13 ═══════════════════════════════════════════════════════
def test_transform_13_is_read_as_columns_not_rows() -> None:
    """CATIA's nine numbers are "where local x, y, z point" - the same columns."""
    tools = AssemblyTools(connection=None)
    matrix = _rotate_z(90.0)
    flat, translation = tools._from_transform_13(_as_13(matrix), "mm")
    assert flat == pytest.approx([matrix[0][0], matrix[1][0], matrix[2][0],
                                  matrix[0][1], matrix[1][1], matrix[2][1],
                                  matrix[0][2], matrix[1][2], matrix[2][2]])
    assert translation == pytest.approx([1.0, 2.0, 3.0])


def test_a_row_major_reading_would_be_the_transpose_of_the_truth() -> None:
    """The whole point of P1: reading the same numbers as rows is the transpose.

    Measured on B30 2026-11-19: transposing in the conversion put a 90 deg turn
    180 deg out, and only the read-back of where local X landed showed it.
    """
    tools = AssemblyTools(connection=None)
    matrix = _rotate_z(20.3)
    flat, _ = tools._from_transform_13(_as_13(matrix), "mm")
    # The nine numbers fed to CATIA are matrix's COLUMNS, so a reader that
    # treated them as rows would place matrixᵀ - the transposed assembly.
    for row in range(3):
        for column in range(3):
            assert flat[row * 3 + column] == pytest.approx(matrix[column][row])


def test_transform_13_translation_unit_is_applied_to_the_translation_only() -> None:
    tools = AssemblyTools(connection=None)
    flat_mm, translation_mm = tools._from_transform_13(
        _as_13(_rotate_z(0.0), (0.0, 0.0, 0.1)), "m")
    assert translation_mm == pytest.approx([0.0, 0.0, 100.0])
    assert flat_mm == pytest.approx([1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0])


def test_transform_13_refuses_a_scale_and_a_wrong_length() -> None:
    tools = AssemblyTools(connection=None)
    with pytest.raises(Exception) as scaled:
        tools._from_transform_13(_as_13(_rotate_z(0.0), scale=2.0), "mm")
    assert "scale" in str(scaled.value)
    with pytest.raises(Exception) as short:
        tools._from_transform_13([1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0], "mm")
    assert "thirteen" in str(short.value)
    with pytest.raises(Exception) as unit:
        tools._from_transform_13(_as_13(_rotate_z(0.0)), "inch")
    assert "translation_unit" in str(unit.value)


def test_the_schema_declares_transform_13_and_its_unit() -> None:
    schema = next(d for d in AssemblyTools(connection=None).get_tool_definitions()
                  if d["name"] == "catia_set_component_transform")
    properties = schema["inputSchema"]["properties"]
    assert properties["transform_13"]["minItems"] == 13
    assert properties["transform_13"]["maxItems"] == 13
    assert properties["translation_unit"]["enum"] == ["mm", "cm", "m"]
    assert "COLUMN" in properties["transform_13"]["description"]


def test_a_transposed_feed_is_visible_in_the_read_back() -> None:
    """90 deg about Z is 180 deg away from its own transpose; identity is 0."""
    assert AssemblyTools._transposed_reading_deg([0.0, 1.0, 0.0,
                                                  -1.0, 0.0, 0.0,
                                                  0.0, 0.0, 1.0]) == pytest.approx(180.0)
    assert AssemblyTools._transposed_reading_deg([1.0, 0.0, 0.0,
                                                  0.0, 1.0, 0.0,
                                                  0.0, 0.0, 1.0]) == pytest.approx(0.0)


def test_the_read_back_calls_out_an_instance_that_cannot_prove_the_convention() -> None:
    report = AssemblyTools._matrix_report([1.0, 0.0, 0.0,
                                           0.0, 1.0, 0.0,
                                           0.0, 0.0, 1.0])
    assert report["if_read_transposed"]["differs_by_deg"] == pytest.approx(0.0)
    assert "cannot tell you which convention" in report["if_read_transposed"]["verdict"]
    turned = AssemblyTools._matrix_report([0.0, 1.0, 0.0,
                                           -1.0, 0.0, 0.0,
                                           0.0, 0.0, 1.0])
    assert turned["if_read_transposed"]["differs_by_deg"] == pytest.approx(180.0)
    assert "transposed feed" in turned["if_read_transposed"]["verdict"]


# ══ P0: the tools say it before the call ═══════════════════════════════════
def test_every_mate_tool_says_it_is_unavailable_before_the_call() -> None:
    definitions = {d["name"]: d for d in
                   AssemblyTools(connection=None).get_tool_definitions()}
    for name in ("catia_fix_constraint", "catia_coincidence_constraint",
                 "catia_offset_constraint", "catia_angle_constraint"):
        text = definitions[name]["description"]
        assert "UNAVAILABLE_ON_THIS_BUILD" in text, name
        assert "force" in text, name
        assert "catia_set_component_transform" in text, name


def test_the_ceiling_entry_carries_the_evidence_not_just_the_verdict() -> None:
    entry = next(e for e in _CAPABILITY_CEILINGS
                 if e["member"].startswith("CATIAConstraints"))
    assert entry["status"] == "UNSUPPORTED_ON_B30"
    assert set(entry) == {"member", "status", "detail"}
    for said in ("Selection.Item(1).Reference", "SKETCH", "GetWorkbenchId",
                 "unknown command", "14 constraint type codes"):
        assert said in entry["detail"], said


def test_the_new_product_note_names_the_workbench_reading_and_the_sketch_control() -> None:
    note = DocumentTools._CONSTRAINT_CEILING
    assert "NOT AVAILABLE" in note
    for said in ("GetWorkbenchId", "SKETCH", "ReferenceProduct", "ApplyWorkMode",
                 "StartWorkbench", "GenerateConstraints",
                 "catia_set_component_transform", "NOT a parameterised assembly"):
        assert said in note, said


# ══ P2/P3: a truncated sweep is not a lower bound ══════════════════════════
def _box(points, **kwargs):
    return json.loads(MeasurementTools._bbox_json(
        points, "note", **kwargs))


def test_the_first_key_of_a_box_reply_is_what_the_numbers_are() -> None:
    reply = _box([[0.0, 0.0, 0.0], [1.0, 2.0, 3.0]],
                 summary="LOWER BOUND: a vertex sweep cannot see a curved extremum")
    assert list(reply)[0] == "summary"
    assert reply["summary"].startswith("LOWER BOUND")
    assert reply["max"] == {"x": 1.0, "y": 2.0, "z": 3.0}


def test_a_reply_without_a_summary_still_starts_with_the_numbers() -> None:
    reply = _box([[0.0, 0.0, 0.0], [1.0, 2.0, 3.0]])
    assert list(reply)[0] == "min"


def _fake_vertex_route(monkeypatch, points, truncated, total,
                       refused: str | None = None):
    tools = MeasurementTools.__new__(MeasurementTools)
    tools._mesh_last_cache = None

    def vertex_points(self, body, stats=None, limit=200, allow_truncated=False):
        if refused:
            stats["refused_vertices"] = refused
            stats["cells"] = total
            return [], False, total, 0
        return points, truncated, total, 0

    monkeypatch.setattr(MeasurementTools, "_vertex_points", vertex_points)
    monkeypatch.setattr(MeasurementTools, "_mesh_corners",
                        lambda self, part: ([[0.0, 0.0, 0.0],
                                             [1.0, 46.1033, 0.0]], 2316, ""))
    monkeypatch.setattr(MeasurementTools, "_visible_hybrid_sets",
                        lambda self, part: (0, 0))
    monkeypatch.setattr(MeasurementTools, "_auto_method",
                        lambda self, method, body: ("vertices", ""))
    monkeypatch.setattr(MeasurementTools, "_part_identity",
                        lambda self, part: None)
    monkeypatch.setattr(MeasurementTools, "_measurable_body",
                        lambda self, part, name=None: (_Shapes(), "the main body"))
    return tools


class _Shapes:
    class Shapes:  # noqa: N801 - stands in for body.Shapes
        Count = 1


class _Part:
    class Parent:
        Name = "Heavy.CATPart"

    Name = "Heavy"


def test_a_truncated_sweep_is_labelled_when_the_caller_asks_for_it(
        monkeypatch) -> None:
    """allow_truncated=true is the ONLY way to get a sample's box now."""
    tools = _fake_vertex_route(monkeypatch,
                               [[0.0, 0.0, 0.0], [1.0, 3.1941, 0.0]],
                               True, 582)
    reply = json.loads(tools._part_bbox_of_body(_Part(), "vertices", _Shapes(),
                                               allow_truncated=True))
    assert reply["bounds_code"] == "vertex_sweep_truncated"
    assert reply["vertices_missed"] == 580
    assert reply["exact_envelope"] is False
    assert reply["summary"].startswith("NOT THE ENVELOPE")
    assert "581 unread" in reply["summary"] or "580 unread" in reply["summary"]
    assert "STOPPED AT THE 2-vertex cap" in reply["method"]


def test_too_many_vertices_means_the_mesh_route_not_a_sample(
        monkeypatch) -> None:
    """The 2026-11-20 report's 200 s: the default must not pay it."""
    tools = _fake_vertex_route(monkeypatch, [], False, 582,
                               refused="582 vertices, cap 200")
    reply = json.loads(tools._part_bbox_of_body(_Part(), "vertices", _Shapes()))
    assert reply["points_route"] == "mesh"
    assert reply["max"]["y"] == pytest.approx(46.1033)
    assert "582 vertices, cap 200" in reply["method"]
    assert "42.9" in reply["method"]      # the size of the miss it avoided
    assert "allow_truncated=true" in reply["method"]
    assert reply["bounds_code"] == "mesh_chord"


def test_an_uncapped_sweep_is_a_lower_bound_and_says_so(monkeypatch) -> None:
    tools = _fake_vertex_route(monkeypatch,
                               [[0.0, 0.0, 0.0], [1.0, 2.0, 0.0]], False, 2)
    reply = json.loads(tools._part_bbox_of_body(_Part(), "vertices", _Shapes()))
    assert reply["bounds_code"] == "vertex_sweep"
    assert reply["vertices_missed"] == 0
    assert reply["summary"].startswith("LOWER BOUND")


def test_the_mesh_summary_says_lower_bound_not_exact(monkeypatch) -> None:
    tools = MeasurementTools.__new__(MeasurementTools)
    tools._mesh_last_cache = None
    monkeypatch.setattr(MeasurementTools, "_mesh_corners",
                        lambda self, part: ([[0.0, 0.0, 0.0], [1.0, 2.0, 3.0]],
                                            2316, ""))
    monkeypatch.setattr(MeasurementTools, "_part_identity",
                        lambda self, part: None)
    monkeypatch.setattr(MeasurementTools, "_visible_hybrid_sets",
                        lambda self, part: (0, 0))
    reply = json.loads(tools._part_bbox_of_body(_Part(), "mesh", _Shapes()))
    assert reply["exact_envelope"] is False
    assert reply["summary"].startswith("LOWER BOUND")
    assert "TIGHTER route" in reply["summary"]


def test_the_vertices_documentation_names_the_cap_and_the_mesh_price() -> None:
    schema = next(d for d in MeasurementTools(connection=None).get_tool_definitions()
                  if d["name"] == "catia_get_bounding_box")
    text = schema["inputSchema"]["properties"]["method"]["description"]
    assert "STOPS AT 200" in text
    assert "42.9 mm short" in text
    assert "181 ms" in text


# ══ P4: the copy's part number ═════════════════════════════════════════════
def test_save_as_copy_declares_part_number() -> None:
    schema = next(d for d in DocumentTools(connection=None).get_tool_definitions()
                  if d["name"] == "catia_save_as_copy")
    assert "part_number" in schema["inputSchema"]["properties"]
    assert "part_number" in schema["inputSchema"]["properties"]["part_number"]["description"]


def test_save_as_copy_forwards_the_part_number(tmp_path, monkeypatch) -> None:
    calls = {}
    source = tmp_path / "Orig.CATPart"
    source.write_bytes(b"not really a catpart")

    class _Doc:
        FullName = str(source)

        def Activate(self):  # noqa: D401 - stands in for Document.Activate
            calls["activated"] = True

    class _Conn:
        app = None
        documents = None

        def __init__(self):
            self.active_document = _Doc()

    tools = DocumentTools(_Conn())
    monkeypatch.setattr(DocumentTools, "_rewrite_part_number",
                        lambda self, path, number, reactivate: calls.setdefault(
                            "rewrite", (path, number)) and "rewritten")
    monkeypatch.setattr("catia_mcp.tools.document.dialogs.save",
                        lambda doc: None)
    reply = tools._save_as_copy(str(tmp_path / "Copy.CATPart"), True, "P06_mir")
    assert calls["rewrite"][1] == "P06_mir"
    assert reply.startswith("Copied")
    assert (tmp_path / "Copy.CATPart").exists()


def test_rewrite_part_number_reports_a_failed_rewrite_with_the_file_intact() -> None:
    class _Broken:
        def Open(self, path):
            raise RuntimeError("CATIA refused to open it")

    class _Conn:
        documents = _Broken()

    tools = DocumentTools(_Conn())
    with pytest.raises(RuntimeError) as failed:
        tools._rewrite_part_number("C:/tmp/Copy.CATPart", "P06_mir", None)
    text = str(failed.value)
    assert "ORIGINAL part number" in text
    assert "P06_mir" in text


# ══ P5: strict_arc ══════════════════════════════════════════════════════
WEB = {"type": "arc", "center": [15.0, 0.0], "radius": 72.367,
       "p1": [-55.222, 17.5], "p2": [-55.222, -17.5], "direction": "ccw"}


def test_strict_arc_refuses_the_centre_move_and_draws_nothing() -> None:
    tools = SketcherTools(connection=None)
    with pytest.raises(CodedError) as refused:
        tools._plan_segment(1, dict(WEB), strict_arc=True)
    error = refused.value
    assert error.code == "ARC_ENDPOINT_OFF_CIRCLE"
    assert "strict_arc" in str(error)
    assert "0.002" in str(error)  # the deviation, to 1e-6 mm
    # ... and nothing was planned, so nothing can be drawn.
    assert getattr(error, "modified", None) in (None, False)


def test_without_strict_mode_the_same_arc_still_keeps_its_radius() -> None:
    tools = SketcherTools(connection=None)
    plan = tools._plan_segment(1, dict(WEB))
    assert plan["radius"] == pytest.approx(72.367)
    assert any("KEPT" in note for note in plan["adjust"])


def test_an_arc_already_on_its_circle_passes_strict_mode() -> None:
    tools = SketcherTools(connection=None)
    on_circle = _on(45.0)
    segment = {"type": "arc", "center": [0.0, 0.0], "radius": 10.0,
               "p1": on_circle(0.0), "p2": on_circle(90.0), "direction": "ccw"}
    plan = tools._plan_segment(1, segment, strict_arc=True)
    assert plan["radius"] == pytest.approx(10.0)
    assert not plan["adjust"]


def _on(angle: float):
    def point(degrees: float) -> list[float]:
        radians = math.radians(degrees)
        return [round(10.0 * math.cos(radians), 12),
                round(10.0 * math.sin(radians), 12)]
    return point


def test_the_profile_schema_declares_strict_arc() -> None:
    schema = next(d for d in SketcherTools(connection=None).get_tool_definitions()
                  if d["name"] == "catia_sketch_profile")
    strict = schema["inputSchema"]["properties"]["strict_arc"]
    assert strict["default"] is False
    assert "ARC_ENDPOINT_OFF_CIRCLE" in strict["description"]
