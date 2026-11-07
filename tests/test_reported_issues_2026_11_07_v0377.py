"""0.3.77: the 2026-11-07 report (frigate built from a text description).

Eleven items. What was measurable was measured first, on B30, before any code
changed (`scripts/b30_v0377_probe.py`, `..._probe2.py`):

  A  A plane reference in AddNewRectPattern drives the plane's OWN horizontal
     axis, not its normal: dir1_axis='x' landed on +Y, 'y' on +Z, 'z' on +X -
     one axis past what the table assumed. That is the root cause of P3, and it
     had been carried as an "unreliable, read the bounding box" WARNING since
     2026-11-02. The default pair (no axis arguments) drives Y then X, and the
     reply had promised "dir1 (X), dir2 (Y)" for as long as it existed.
  B  `through_all` + direction='both' DID cut on a frame sketch here (4000 mm3
     from a 40x40x40 block), so P4 is not reproduced - the message now names the
     workaround the reporter measured instead of blaming the profile.
  C  A loft over four separate sketch lines is refused; the same four points as
     a closed spline build. P1's limitation is real, and the four alternatives
     tried (join of the 2D elements, the Sketch object, an Extract) all failed,
     so the error now names the offending section instead of hinting.

The rest are deterministic: P5 (the recovery path was handed a placement whose
key is `base`, while `_resolve_support` reads `type`), P6 (direction='normal' is
the default side, not a pin), P8 (one level of set descent is not enough), P9
(the empty part is now closed again), P10 (the diagnosis names the cause).
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from catia_mcp.tools.part_design import PartDesignTools


# ── P3: the direction table is the measurement ─────────────────────────────

def test_the_axis_table_matches_what_was_measured():
    """PlaneXY drives X, PlaneYZ drives Y, PlaneZX drives Z.

    Measured on B30 2026-11-07 by patterning a 10x10x5 pad 3x1 at 100 mm and
    reading the body box: with the old table ({"x": "yz", ...}) dir1_axis='x'
    grew Y by exactly 200 mm - the reported defect.
    """
    source = inspect.getsource(PartDesignTools._rect_pattern)
    assert 'by_axis = {"x": "xy", "y": "yz", "z": "zx"}' in source
    # The old warning is gone; the comment still records what the old table
    # said, which is the only place that mistake should survive.
    # The warning the caller used to get is gone (the comment that explains why
    # may still quote it; the reply may not).
    assert "WARNING: the axis each direction actually takes" not in source
    assert "one axis past the one the old table assumed" in source


def test_the_default_pair_no_longer_promises_the_wrong_axes():
    """No arguments gives dir1 = Y, dir2 = X - measured, not assumed."""
    source = inspect.getsource(PartDesignTools._rect_pattern)
    assert "PlaneYZ -> dir1 (Y), PlaneZX -> dir2 (X)" in source
    assert "PlaneYZ -> dir1 (X), PlaneZX -> dir2 (Y)" not in source


def test_a_left_handed_pair_is_still_swapped_not_refused():
    """The handedness rule is untouched by the direction fix."""
    source = inspect.getsource(PartDesignTools._rect_pattern)
    assert 'right_handed = (first, second) in (("x", "y"), ("y", "z"), ("z", "x"))' in source


# ── P5: the recovery path accepts the plane its creator accepts ────────────

def test_a_frame_sketch_can_be_rebuilt():
    """`base: "frame"` in the record vs `type: "frame"` in the resolver.

    The record stores the frame in the form the GSD side replays, and it was
    forwarded as-is to a function that reads `type`, so rebuilding a frame
    sketch answered "Unknown plane type None" about a sketch whose creation had
    just been accepted (reported 2026-11-07, P5).
    """
    from catia_mcp.tools.sketcher import SketcherTools

    seen: dict = {}

    def fake_resolve(part, body, plane, offset):
        seen["plane"] = plane
        raise RuntimeError("stop here")

    tools = SketcherTools(connection=None)
    tools.conn = SimpleNamespace(
        active_document=SimpleNamespace(Name="probe.CATPart"),
        sketch_outlines={
            "probe.CATPart::草图.45": {
                "placement": {"base": "frame", "origin": [0, -6900, 0],
                              "u": [1, 0, 0], "v": [0, 0, 1]},
                "elements": [{"kind": "rectangle", "x1": 0, "y1": 0,
                              "x2": 10, "y2": 10}],
            },
        },
    )
    tools._resolve_support = fake_resolve  # type: ignore[method-assign]

    name, why = tools.rebuild_lost_sketch(SimpleNamespace(), SimpleNamespace(),
                                          "草图.45")

    assert name is None
    assert seen["plane"]["type"] == "frame"
    assert seen["plane"]["origin"] == [0, -6900, 0]
    assert seen["plane"]["u"] == [1, 0, 0]
    assert seen["plane"]["v"] == [0, 0, 1]
    # It reached the resolver: no "Unknown plane type None" any more.
    assert "Unknown plane type" not in why
    assert "stop here" in why


# ── P6: 'normal' is the default side, not a pin ────────────────────────────

def test_normal_direction_may_flip_and_reverse_stays_literal():
    source = inspect.getsource(PartDesignTools._pocket)
    assert "if took is False and not symmetric and not reverse:" in source
    assert "reverse means reverse" in source
    # And a reverse that removes nothing names the way back.
    assert "Retry once with direction='normal'" in source


# ── P8: a set inside a set is descended too ───────────────────────────────

def _collection(items):
    return SimpleNamespace(Count=len(items),
                           Item=lambda index: items[index - 1])


def test_set_descent_reaches_a_nested_geometrical_set():
    """One level was not enough: the reply said hidden, the box still saw it."""
    inner_element = SimpleNamespace(Name="ConeBase")
    inner = SimpleNamespace(Name="Inner", HybridShapes=_collection([inner_element]),
                            HybridSketches=_collection([]),
                            HybridBodies=_collection([]))
    outer = SimpleNamespace(Name="GSD_Set",
                            HybridShapes=_collection([]),
                            HybridSketches=_collection([]),
                            HybridBodies=_collection([inner]))

    applied = []
    count = PartDesignTools._hide_set_contents(outer, applied.append, depth=0)

    assert applied == [inner, inner_element]
    assert count == 2


def test_set_descent_is_bounded():
    """A tree that points at itself must not loop for ever."""
    node = SimpleNamespace(Name="Loop", HybridShapes=_collection([]),
                           HybridSketches=_collection([]), HybridBodies=None)
    node.HybridBodies = SimpleNamespace(Count=1, Item=lambda index: node)
    applied = []
    PartDesignTools._hide_set_contents(node, applied.append, depth=0)
    assert len(applied) <= 5


# ── P9: the unsaved document is closed again ──────────────────────────────

def _document_tools(close_raises: bool = False, stays: bool = False):
    from catia_mcp.tools.document import DocumentTools

    closed = []

    class Doc:
        Name = "part.CATPart"

        def Close(self):
            if close_raises:
                raise RuntimeError("a dialog refused it")
            closed.append(True)

    documents = SimpleNamespace(Count=0 if not stays else 1,
                                Item=lambda index: SimpleNamespace(Name="part.CATPart"))
    tools = DocumentTools(connection=None)
    tools.conn = SimpleNamespace(documents=documents)
    return tools, Doc(), closed


def test_an_empty_part_that_cannot_be_saved_is_closed_again():
    tools, doc, closed = _document_tools()
    assert tools._discard_unsaved_document(doc) is True
    assert closed == [True]


def test_a_close_that_raised_is_not_reported_as_closed():
    tools, doc, closed = _document_tools(close_raises=True)
    assert tools._discard_unsaved_document(doc) is False
    assert closed == []


def test_a_document_still_in_the_list_is_not_reported_as_closed():
    tools, doc, closed = _document_tools(stays=True)
    assert tools._discard_unsaved_document(doc) is False
    assert closed == [True]


def test_new_part_records_the_discard_in_the_envelope():
    from catia_mcp.tools.document import DocumentTools

    source = inspect.getsource(DocumentTools._new_part)
    assert "modified=not discarded" in source
    assert "closed again, so no unsaved document" in source


# ── P2 / P10: the sections are described, and a mismatch is named ──────────

def _loft_tools(records: dict):
    tools = PartDesignTools(connection=None)
    tools.conn = SimpleNamespace(
        active_document=SimpleNamespace(Name="probe.CATPart"),
        sketch_outlines=records,
    )
    return tools


def test_section_facts_come_from_the_drawing_record():
    tools = _loft_tools({
        "probe.CATPart::草图.1": {
            "placement": {"base": "xy", "distance": 0.0},
            "elements": [{"kind": "spline", "points": 59, "chord": 100.0}],
        },
    })
    facts = tools._section_facts("草图.1")

    assert facts == {"elements": 1, "curves": 1, "kinds": {"spline": 1},
                     "points": 59}
    assert "one closed curve" in PartDesignTools._describe_facts([("草图.1", facts)])
    assert "59 points" in PartDesignTools._describe_facts([("草图.1", facts)])


def test_a_rectangle_is_four_curves_not_one_element():
    """The record holds ONE drawing call; the sketch holds FOUR curves.

    Counting record entries called a 4-line rectangle "one element", so the
    multi-curve branch never fired on the exact section the reporter used
    (2026-11-07, P1) - and the generic hint came back instead of the cause.
    """
    tools = _loft_tools({
        "probe.CATPart::草图.4": {
            "placement": {"base": "xy", "distance": 0.0},
            "elements": [{"kind": "rectangle", "x1": 0, "y1": 0,
                          "x2": 20, "y2": 10}],
        },
    })
    facts = tools._section_facts("草图.4")

    assert facts["elements"] == 1
    assert facts["curves"] == 4
    assert "4 curves (1 rectangle(s))" in PartDesignTools._describe_facts(
        [("草图.4", facts)])


def test_a_closed_spline_stays_one_curve():
    tools = _loft_tools({
        "probe.CATPart::草图.1": {
            "placement": {"base": "xy", "distance": 0.0},
            "elements": [{"kind": "spline", "points": 180, "chord": 10.0}],
        },
    })
    facts = tools._section_facts("草图.1")
    assert facts["curves"] == 1
    assert "one closed curve" in PartDesignTools._describe_facts([("草图.1", facts)])


def test_an_unknown_section_is_unknown_not_single_curve():
    """No record is "not known" - never a clean bill of health."""
    tools = _loft_tools({})
    assert tools._section_facts("GSD_Spline.1") is None
    described = PartDesignTools._describe_facts([("GSD_Spline.1", None)])
    assert "no drawing record" in described


def test_a_point_count_mismatch_is_a_warning_on_a_successful_loft():
    warning = PartDesignTools._coupling_warning({
        "草图.1": {"elements": 1, "kinds": {"spline": 1}, "points": 59},
        "草图.2": {"elements": 1, "kinds": {"spline": 1}, "points": 60},
    })
    assert warning.startswith(" WARNING:")
    assert "'草图.1' 59" in warning and "'草图.2' 60" in warning
    # The measured consequence, not a guess about one.
    assert "+49.8%" in warning and "ok:true" in warning
    assert "same segment counts" in warning


def test_matching_sections_raise_no_warning():
    assert PartDesignTools._coupling_warning({
        "草图.1": {"elements": 1, "kinds": {"spline": 1}, "points": 180},
        "草图.2": {"elements": 1, "kinds": {"spline": 1}, "points": 180},
    }) == ""


def test_the_loft_error_names_the_multi_element_section():
    source = inspect.getsource(PartDesignTools._solid_loft)
    assert "separate curve(s)" in source
    assert "recorded as multi-element" in source
    assert "rectangle section is refused" in source


# ── P4: the through_all workaround is named ───────────────────────────────

def test_a_through_all_pocket_that_cuts_nothing_names_the_depth_workaround():
    source = inspect.getsource(PartDesignTools._pocket)
    assert "catia_pocket(depth=20000, direction='both')" in source
    assert "through_all extent can come back empty" in source


def test_the_pocket_scope_is_documented():
    schema = [tool for tool in PartDesignTools(None).get_tool_definitions()
              if tool["name"] == "catia_pocket"][0]
    description = schema["description"]
    assert "SCOPE" in description
    assert "catia_new_body" in description  # how to isolate a cut
