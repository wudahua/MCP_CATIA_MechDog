"""0.3.88: the three follow-ups from the 2026-11-09 reports on 0.3.87.

  * the save failures now NAME the COM call that ran (bare `doc.Save()` vs
    `doc.SaveAs(...)`), because a report had two true sentences that looked
    contradictory and no way to tell which call it hit
  * a never-saved document going into a NON-ASCII folder is staged FIRST - the
    route the reporter measures working - instead of six refused attempts
  * `catia_create_sketch` reports H/V on a rotated plane instead of "unknown for
    this support", and a sketch that IS one spline hands its coordinates over
    with the refusal instead of closing the only readback path
"""
from __future__ import annotations

import types

import pytest

from catia_mcp.results import CodedError


# ── the save messages name their call ───────────────────────────────────


def test_a_bare_save_failure_says_it_was_a_bare_save():
    from catia_mcp import dialogs

    doc = types.SimpleNamespace(Name="P.CATPart", Save=lambda: (_ for _ in ()).throw(
        RuntimeError("(0, 'CATIAPartDocument', '方法 Save 失败')")))
    with pytest.raises(CodedError) as caught:
        dialogs.save(doc, tries=1)
    text = str(caught.value)
    assert text.startswith("Save failed after 1 attempts: P.CATPart")
    assert "this call was a bare doc.Save()" in text
    assert caught.value.data["call"] == "Document.Save"


def test_a_saveas_failure_says_it_was_a_saveas(tmp_path):
    """The path is a real folder under tmp_path, not `F:/ascii`.

    It was `F:/ascii/x.CATPart` until 0.3.91, and a 2026-11-09 report showed what
    that costs: on a machine with no F: drive, `os.makedirs('F:\\ascii')` raised
    PermissionError BEFORE the fake SaveAs ever ran, so the expected CodedError
    never appeared and this test failed - a green suite that depended on the
    author's drive letters.
    """
    from catia_mcp import dialogs

    doc = types.SimpleNamespace(
        Name="P.CATPart",
        SaveAs=lambda path: (_ for _ in ()).throw(RuntimeError("E_FAIL")))
    target = str(tmp_path / "ascii" / "x.CATPart")
    with pytest.raises(CodedError) as caught:
        dialogs.save_as(doc, target, tries=1, wait=0)
    text = str(caught.value)
    assert text.startswith("SaveAs failed after 1 attempts")
    assert "this call was doc.SaveAs(" in text
    assert caught.value.data["call"] == "Document.SaveAs"


def test_the_save_advice_no_longer_sends_the_caller_back_to_the_same_call():
    """It used to say "rebuild through catia_new_part(file_path=...)", which is
    the call that had just failed - the reporter pointed that out."""
    from catia_mcp import dialogs

    doc = types.SimpleNamespace(Name="P.CATPart", Save=lambda: (_ for _ in ()).throw(
        RuntimeError("E_FAIL")))
    with pytest.raises(CodedError) as caught:
        dialogs.save(doc, tries=1)
    advice = " ".join(caught.value.next_actions)
    assert "rebuild through catia_new_part" not in advice
    assert "NO retry of this call can work" in advice
    assert "catia_save_document(file_path='<the non-ASCII folder>" in advice


# ── the staged route first, for a Chinese folder ────────────────────────


def test_only_a_non_ascii_folder_takes_the_early_staging_route():
    from catia_mcp.tools.document import DocumentTools

    assert DocumentTools._path_is_ascii(r"F:\wb_pet\part.CATPart")
    assert not DocumentTools._path_is_ascii(r"F:\中文项目\part.CATPart")


# ── H/V on a rotated plane ─────────────────────────────────────────────


def test_a_rotated_plane_reports_its_axes_instead_of_unknown():
    from catia_mcp.tools.sketcher import SketcherTools

    placement = {"base": "frame", "origin": [0.0, 0.0, 0.0],
                 "u": [0.0, 0.0, 1.0],
                 "v": [0.309016994, 0.951056516, 0.0],
                 "derived": "'P72' = the zx plane rotated 72 degrees about z"}
    note = SketcherTools._axis_note("P72", placement)
    assert "H = (0, 0, 1)" in note
    assert "V = (0.309017, 0.951057, 0)" in note
    assert "normal = (-0.951057, 0.309017, 0)" in note
    assert "unknown for this support" not in note
    assert "rotated 72 degrees about z" in note


def test_a_named_plane_with_no_frame_still_says_unknown():
    from catia_mcp.tools.sketcher import SketcherTools

    assert "unknown for this support" in SketcherTools._axis_note("Plane_from_disk",
                                                                 None)


# ── a one-spline sketch still gives its coordinates ────────────────────


def _gsd_with_spline():
    from catia_mcp.tools.gsd import GSDTools

    conn = types.SimpleNamespace(
        active_document=types.SimpleNamespace(Name="Part1.CATPart"),
        sketch_outlines={
            "Part1.CATPart::草图.4": {
                "placement": {"base": "frame", "origin": [0.0, 0.0, 0.0],
                              "u": [0.0, 0.0, 1.0],
                              "v": [0.309016994, 0.951056516, 0.0],
                              "derived": "'P36' = the zx plane rotated 36 degrees "
                                         "about z"},
                "elements": [{"kind": "spline", "points": 4,
                              "pts": [[0.0, 0.0], [2.0, 3.0], [6.0, 4.0],
                                      [9.0, 0.0]],
                              "closed": False}],
            },
        },
        ensure_connected=lambda: None,
    )
    tools = GSDTools(conn)
    tools._sketch_exists = lambda name: True            # type: ignore[method-assign]
    tools._sketch_alias = lambda name: None             # type: ignore[method-assign]
    tools._verify_frame = lambda *args: ""              # type: ignore[method-assign]
    return tools


def test_a_one_spline_sketch_refuses_but_publishes_its_points():
    tools = _gsd_with_spline()
    with pytest.raises(Exception) as caught:
        tools._outline_points("草图.4", False)
    text = str(caught.value)
    assert "already holds a spline" in text
    assert "Nothing was created" in text
    # (h, v) = (2, 3) on zx-rotated-36 -> 3*(cos36, sin36) + 2*Z
    assert "(0.9271, 2.8532, 2)" in text
    assert "in the frame of 'P36'" in text


def test_the_points_come_from_the_record_not_from_a_guess():
    tools = _gsd_with_spline()
    recorded = {"elements": [{"kind": "spline",
                              "pts": [[0.0, 0.0], [0.0, 10.0]]}]}
    placement = {"base": "frame", "origin": [0.0, 0.0, 0.0],
                 "u": [0.0, 0.0, 1.0], "v": [1.0, 0.0, 0.0]}
    points = tools._recorded_spline_points(recorded, placement)
    # H is Z here and V is X, so (h=0, v=10) is 10 along X - the mapping the
    # record is read through, not a coordinate the code invented.
    assert points == [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]]


def test_a_sketch_with_no_spline_record_keeps_the_plain_refusal():
    tools = _gsd_with_spline()
    assert tools._recorded_spline_points(
        {"elements": [{"kind": "line", "x1": 0, "y1": 0, "x2": 1, "y2": 1}]},
        {"base": "xy"}) == []
