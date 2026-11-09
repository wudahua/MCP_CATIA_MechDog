# -*- coding: utf-8 -*-
"""0.3.82: the 2026-11-08 bottle-base report, in tests.

Three of its items were reproduced and fixed here (the spline endpoint pairing,
the missing sketch-copy step, and the missing plane normal to a curve), one was
reproduced and answered with a refusal path (a GSD pattern does not exist on this
build), and one - the Chinese FOLDER refusing a first SaveAs - did not reproduce
on B30 in nine attempts. What is asserted here is the CODE behaviour; the
measurements behind it are in ``scripts/b30_v0382_*.py``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from catia_mcp.results import ArgumentOutOfRange, CodedError, PreconditionRejected


# ── close_sketch: the spline's ends are in the pairing ──────────────────────

class _FakeFactory2D:
    """Only the calls the drawing helpers make, and they record nothing."""

    def CreatePoint(self, x, y):
        return SimpleNamespace(x=x, y=y, Construction=False)

    def CreateLine(self, x1, y1, x2, y2):
        return SimpleNamespace(Construction=False)

    def CreateSpline(self, points):
        return SimpleNamespace(Name="样条.1")

    def CreateCircle(self, *args):
        return SimpleNamespace(Name="圆.1")

    def CreateClosedCircle(self, cx, cy, radius):
        return SimpleNamespace(Name="圆.1")


def _sketcher_drawing():
    """A SketcherTools with an open sketch and a factory that draws nothing.

    The point of driving the real drawing helpers is that the RECORDERS are what
    broke: the arc's two calls sat after a ``return`` (so an arc never entered
    the record at all) and the spline never made them. Calling _note_ends
    directly in a test would pass on the broken code.
    """
    from catia_mcp.tools.sketcher import SketcherTools

    tools = SketcherTools(connection=None)
    tools.conn = SimpleNamespace(sketch_outlines={},
                                 refresh_display=lambda: None,
                                 ensure_connected=lambda: None)
    tools._active_sketch = SimpleNamespace(Name="草图.1")
    tools._active_factory = _FakeFactory2D()
    tools._profile_ends = []
    tools._profile_closed_curves = 0
    tools._drawn = {}
    return tools


def test_a_spline_end_is_paired_with_the_line_that_touches_it():
    """The report's P1-1: one 6-point spline + four lines, ends identical.

    0.3.81 recorded endpoints for LINES only - the arc's recorder sat after a
    ``return`` and the spline never called it - so the spline's two ends were
    invisible and the pairing reported a 29.39 mm "gap" between an end and the
    point it coincided with.
    """
    tools = _sketcher_drawing()
    tools._draw_spline([[0, 0], [10, 2], [20, 8], [25, 20], [20, 32], [10, 38]])
    for (x1, y1), (x2, y2) in (((10, 38), (0, 38)), ((0, 38), (0, 20)),
                               ((0, 20), (0, 10)), ((0, 10), (0, 0))):
        tools._draw_line(x1, y1, x2, y2)

    report = tools._profile_breaks()

    assert report["closed"] is True, report
    assert report["endpoints"] == 10        # five open curves, two ends each
    assert report["endpoints_loose"] == 0
    assert report["breaks"] == []


def test_a_circle_only_sketch_is_closed_not_unknown():
    """Nothing to pair is not "nothing recorded": a circle has no free end."""
    tools = _sketcher_drawing()
    tools._draw_circle(10, 10, 5)

    report = tools._profile_breaks()

    assert report["closed"] is True
    assert report["endpoints"] == 0
    assert "closed curve" in report["basis"]


def test_an_arc_records_its_ends_exactly_once():
    """The recorder used to sit after a ``return`` and never ran - and the first
    version of that fix ran it twice, which would have doubled every arc."""
    tools = _sketcher_drawing()
    tools._draw_arc(0, 0, 10, start_angle=0, end_angle=180)

    assert len(tools._profile_ends) == 2, tools._profile_ends
    recorded = [item for item in tools._drawn.get(tools._sketch_key("草图.1"), [])
                if item.get("kind") == "arc"]
    assert len(recorded) == 1, recorded


# ── sketch copy ────────────────────────────────────────────────────────────

def test_copy_refuses_a_sketch_it_has_no_record_for():
    """A reopened sketch cannot be read back, so copying it would invent it."""
    from catia_mcp.tools.sketcher import SketcherTools

    tools = SketcherTools(connection=None)
    tools.conn = SimpleNamespace(sketch_outlines={},
                                 ensure_connected=lambda: None)
    tools._drawn = {}

    with pytest.raises(CodedError) as caught:
        tools._copy_sketch({"source": "草图.9", "plane": "xy"})

    assert caught.value.code == "SKETCH_NOT_RECORDED"
    assert "drawn by these tools in THIS session" in str(caught.value)


def test_copy_replays_every_element_and_records_the_copy_again(monkeypatch):
    """The copy has to be copyable itself - that is what the record is for."""
    from catia_mcp.tools.sketcher import SketcherTools

    tools = SketcherTools(connection=None)
    tools.conn = SimpleNamespace(sketch_outlines={},
                                 ensure_connected=lambda: None)
    tools._drawn = {
        tools._sketch_key("A"): [
            {"kind": "line", "x1": 0, "y1": 0, "x2": 10, "y2": 0},
            {"kind": "circle", "cx": 5, "cy": 5, "radius": 2},
        ]
    }

    replayed: list[dict] = []
    noted: list[dict] = []

    def fake_create(plane=None, offset=None):
        tools._active_sketch = SimpleNamespace(Name="草图.7")
        tools._active_factory = object()
        return "created"

    monkeypatch.setattr(tools, "_create_sketch", fake_create)
    monkeypatch.setattr(tools, "_close_sketch", lambda structured=False: "closed")
    monkeypatch.setattr(SketcherTools, "_replay_element",
                        staticmethod(lambda factory, element: replayed.append(element)))
    monkeypatch.setattr(tools, "_note_element", lambda **kw: noted.append(kw))

    reply = tools._copy_sketch({"source": "A", "plane": "xy", "offset": [5, 5]})

    assert len(replayed) == 2
    assert replayed[0] == {"kind": "line", "x1": 5, "y1": 5, "x2": 15, "y2": 5}
    assert replayed[1]["cx"] == 10 and replayed[1]["cy"] == 10
    # Recorded again, so the copy can be copied.
    assert len(noted) == 2
    assert "copied from 'A'" in reply or "copy of 'A'" in reply
    # The limit is stated, not discovered later.
    assert "not the ones CATIA solved to" in reply


# ── plane normal to a curve ────────────────────────────────────────────────

def test_plane_normal_calls_the_three_argument_point_signature(monkeypatch):
    """Two arguments answer '无效的参数数目': the typelib names three.

    AddNewPointOnCurveFromPercent(iCrv, iLong, iOrientation) - measured on B30
    2026-11-08, after a two-argument call had been reported as if the plane call
    were the one refusing.
    """
    from catia_mcp.tools.gsd import GSDTools

    calls: list[tuple] = []

    class Factory:
        def AddNewPointOnCurveFromPercent(self, *args):
            calls.append(("point", args))
            return SimpleNamespace(Name="P")

        def AddNewPlaneNormal(self, curve, point):
            calls.append(("plane", (curve, point)))
            return SimpleNamespace(Name="NRM")

    tools = GSDTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None,
                                 get_active_part=lambda: SimpleNamespace(
                                     UpdateObject=lambda target: None,
                                     CreateReferenceFromObject=lambda obj: obj))
    monkeypatch.setattr(tools, "_ref", lambda name: f"ref:{name}")
    monkeypatch.setattr(tools, "_factory", lambda: Factory())
    monkeypatch.setattr(tools, "_get_geoset", lambda: SimpleNamespace(
        AppendHybridShape=lambda shape: None))
    monkeypatch.setattr(tools, "_free_name", lambda part, wanted: (wanted, True))
    monkeypatch.setattr(tools, "_on_curve_note", lambda part, ref, p: "")
    monkeypatch.setattr(tools, "_finish",
                        lambda shape, name, message: message)

    reply = tools._plane_normal_to_curve({"curve": "AXIS", "position": 0.5})

    assert calls[0] == ("point", ("ref:AXIS", 0.5, 0))
    assert calls[1][0] == "plane"
    assert "normal to 'AXIS'" in reply


def test_plane_normal_rejects_a_position_outside_the_curve():
    from catia_mcp.tools.gsd import GSDTools

    tools = GSDTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None)

    with pytest.raises(ArgumentOutOfRange) as caught:
        tools._plane_normal_to_curve({"curve": "AXIS", "position": 1.5})

    assert caught.value.code == "POSITION_OUT_OF_RANGE"
    assert "FRACTION" in str(caught.value)


# ── GSD circular pattern ───────────────────────────────────────────────────

def _pattern_tools(monkeypatch, join_raises: bool = False):
    from catia_mcp.tools.gsd import GSDTools

    rotations: list[float] = []
    joins: list[tuple] = []

    class Rotate:
        def __init__(self, angle):
            self.angle = angle
            self.Name = ""

    class Join:
        Name = ""

        def AddElement(self, ref):
            joins.append(("add", ref))

    class Factory:
        def AddNewRotate(self, ref, axis, angle):
            rotations.append(angle)
            return Rotate(angle)

        def AddNewJoin(self, first, second):
            joins.append(("new", first, second))
            return Join()

    tools = GSDTools(connection=None)
    tools.conn = SimpleNamespace(
        ensure_connected=lambda: None,
        get_active_part=lambda: SimpleNamespace(
            UpdateObject=lambda target: None))
    monkeypatch.setattr(tools, "_ref", lambda name: f"ref:{name}")
    monkeypatch.setattr(tools, "_factory", lambda: Factory())
    monkeypatch.setattr(tools, "_get_geoset", lambda: SimpleNamespace(
        AppendHybridShape=lambda shape: None))
    monkeypatch.setattr(tools, "_axis_ref", lambda spec, owner: ("axis-ref", ""))
    monkeypatch.setattr(tools, "_free_name",
                        lambda part, wanted: (wanted, True))
    if join_raises:
        def explode(shape, name, message):
            raise RuntimeError("update failed")
        monkeypatch.setattr(tools, "_finish", explode)
    else:
        monkeypatch.setattr(tools, "_finish",
                            lambda shape, name, message: message)
    return tools, rotations, joins


def test_pattern_rotates_by_the_derived_step(monkeypatch):
    tools, rotations, joins = _pattern_tools(monkeypatch)

    reply = tools._circ_pattern({"element": "SECTOR", "count": 5,
                                 "total_angle": 360, "axis": "z"})

    assert rotations == [72.0, 144.0, 216.0, 288.0]   # count-1 copies
    assert joins[0][0] == "new"
    # The reply says what the tree will look like: no single pattern node.
    assert "no single pattern node" in reply


def test_pattern_without_join_still_returns_the_rotations(monkeypatch):
    tools, rotations, joins = _pattern_tools(monkeypatch)

    reply = tools._circ_pattern({"element": "SECTOR", "count": 3, "join": False})

    assert rotations == [120.0, 240.0]
    assert joins == []           # no join attempted at all
    assert "NOT joined" in reply


def test_a_refused_join_keeps_the_rotations_and_names_the_way_out(monkeypatch):
    """Measured on B30: three coplanar patches share ONE axis line, so the join
    cannot work - one edge, three faces. The refusal has to say so."""
    tools, rotations, _joins = _pattern_tools(monkeypatch, join_raises=True)

    with pytest.raises(PreconditionRejected) as caught:
        tools._circ_pattern({"element": "PATCH", "count": 3, "axis": "z"})

    assert caught.value.code == "PATTERN_JOIN_FAILED"
    assert rotations == [120.0, 240.0]
    assert "join=false" in str(caught.value)
    assert "still in the tree" in str(caught.value)


def test_pattern_refuses_count_and_angle_outside_the_useful_range():
    from catia_mcp.tools.gsd import GSDTools

    tools = GSDTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None,
                                 get_active_part=lambda: None)

    with pytest.raises(ArgumentOutOfRange) as caught:
        tools._circ_pattern({"element": "S", "count": 1})
    assert caught.value.code == "PATTERN_COUNT_OUT_OF_RANGE"

    with pytest.raises(ArgumentOutOfRange) as caught:
        tools._circ_pattern({"element": "S", "count": 3, "total_angle": 0})
    assert caught.value.code == "PATTERN_ANGLE_OUT_OF_RANGE"


# ── the save fallback ──────────────────────────────────────────────────────

def _tools_for_saving(monkeypatch):
    from catia_mcp.tools import document as document_module

    tools = document_module.DocumentTools(connection=None)
    tools.conn = SimpleNamespace(
        active_document=SimpleNamespace(Name="P.CATPart", FullName=""),
        ensure_connected=lambda: None, refresh_display=lambda: None,
        documents=SimpleNamespace(Count=0),
        component_usage=lambda: {})
    monkeypatch.setattr(tools, "_document_at_path", lambda path: None)
    monkeypatch.setattr(tools, "_existing_file_note", lambda path: None)
    monkeypatch.setattr(tools, "_document_has_path", lambda doc: False)
    return document_module, tools


def test_a_refused_first_saveas_falls_back_to_an_ascii_staging_file(monkeypatch):
    """An ASCII folder whose first SaveAs is refused still gets the staging try.

    0.3.88 moved the staged route EARLIER for a NON-ASCII folder (see the next
    test); on an ASCII folder it is still the fallback, so it costs an extra
    SaveAs only on a path that has already failed.
    """
    import os

    document_module, tools = _tools_for_saving(monkeypatch)
    saved: list[str] = []

    def fake_save_as(doc, path, *args, **kwargs):
        saved.append(str(path))
        if len(saved) == 1:
            raise RuntimeError("(0, 'CATIAPartDocument', '\u65b9\u6cd5 Save \u5931\u8d25')")
        return str(path)

    monkeypatch.setattr(document_module.dialogs, "save_as", fake_save_as)

    target = "F:/ascii_project/part.CATPart"
    reply = tools._save_document(target)

    assert len(saved) == 3                 # refused target, staging, target again
    assert saved[0] == target              # the caller's path, refused
    assert os.path.basename(saved[1]).isascii()      # staging file
    assert "mechdog" in saved[1]
    assert saved[2] == target              # then the real path
    assert "moved here from an ASCII staging file" in reply


def test_a_non_ascii_folder_is_staged_FIRST_not_after_a_failure(monkeypatch):
    """0.3.88: the reporter's machine refuses the first SaveAs into its Chinese
    folder on every version, and accepts the same save from a document that
    already has a path. So the staged route runs first there, and the caller no
    longer waits through six refused attempts for the route measured working.
    """
    import os

    document_module, tools = _tools_for_saving(monkeypatch)
    saved: list[str] = []

    def fake_save_as(doc, path, *args, **kwargs):
        saved.append(str(path))
        if not os.path.basename(str(path)).isascii() and len(saved) == 1:
            raise RuntimeError("(0, 'CATIAPartDocument', '\u65b9\u6cd5 Save \u5931\u8d25')")
        return str(path)

    monkeypatch.setattr(document_module.dialogs, "save_as", fake_save_as)

    target = "F:/\u4e2d\u6587\u9879\u76ee/part.CATPart"
    reply = tools._save_document(target)

    # Staging ran FIRST, then the target - the target was never tried directly,
    # so a caller on that machine pays one extra SaveAs instead of six refusals.
    assert len(saved) == 2
    assert os.path.basename(saved[0]).isascii()
    assert saved[1] == target
    assert "through an ASCII staging file" in reply


def test_a_failed_fallback_reports_the_original_error(monkeypatch):
    """Two failures must not hide the first one behind the staging attempt."""
    from catia_mcp.tools import document as document_module

    def always_fails(doc, path, *args, **kwargs):
        raise RuntimeError(f"refused: {path}")

    monkeypatch.setattr(document_module.dialogs, "save_as", always_fails)

    tools = document_module.DocumentTools(connection=None)
    tools.conn = SimpleNamespace(
        active_document=SimpleNamespace(Name="P.CATPart", FullName=""),
        ensure_connected=lambda: None, refresh_display=lambda: None,
        documents=SimpleNamespace(Count=0),
        component_usage=lambda: {})
    monkeypatch.setattr(tools, "_document_at_path", lambda path: None)
    monkeypatch.setattr(tools, "_existing_file_note", lambda path: None)
    monkeypatch.setattr(tools, "_document_has_path", lambda doc: False)

    with pytest.raises(RuntimeError) as caught:
        tools._save_document(r"F:\ascii\part.CATPart")

    # The FIRST failure, naming the caller's path - not the staging copy.
    assert r"F:\ascii\part.CATPart" in str(caught.value)


def test_a_refusal_from_this_server_is_not_dressed_up_as_a_save_failure(monkeypatch):
    """Re-running the audit found this: "that file exists" came back as
    SAVE_FAILED with a paragraph about non-ASCII folders - and the staging
    fallback then removed the existing file, which overwrite=false had just
    promised not to touch."""
    from catia_mcp.tools import document as document_module

    saved: list[str] = []

    def never_called(doc, path, *args, **kwargs):
        saved.append(str(path))
        return str(path)

    monkeypatch.setattr(document_module.dialogs, "save_as", never_called)

    tools = document_module.DocumentTools(connection=None)
    tools.conn = SimpleNamespace(
        active_document=SimpleNamespace(Name="P.CATPart", FullName=""),
        ensure_connected=lambda: None, refresh_display=lambda: None,
        documents=SimpleNamespace(Count=0),
        component_usage=lambda: {})
    monkeypatch.setattr(tools, "_document_at_path", lambda path: None)
    monkeypatch.setattr(tools, "_existing_file_note", lambda path: "39.6 KB")
    monkeypatch.setattr(tools, "_document_has_path", lambda doc: False)

    with pytest.raises(CodedError) as caught:
        tools._save_document(r"F://probe//taken.CATPart")

    # The refusal keeps its own code and its own advice.
    assert caught.value.code == "ARGUMENT_REJECTED"
    assert "overwrite=true" in str(caught.value)
    # And nothing was written: no staging SaveAs, and the file is untouched.
    assert saved == []
