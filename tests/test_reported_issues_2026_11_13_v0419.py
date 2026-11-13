"""0.3.117: an open curve kills a pad/pocket, and an engrave's helpers cannot go.

Two items from the same reports, and both are cases where the tool was about to
learn something the hard way.

1  MULTI-REGION CUT (2026-11-13, P0). The report's sketch held one closed region
   plus the two open splines that divide it, and the pocket answered NO_MATERIAL.
   Measured on B30 2026-11-13, four documents, one variable each
   (scripts/b30_v0419_probe_multiregion.py): a circle alone cut 603.2 mm3; the
   same circle with an open spline THROUGH it, an open spline INSIDE it, or an
   open LINE through it removed 0 mm3 in all three. So the open curve is the
   cause - it need not divide anything, and it need not be a spline. pad/pocket
   now refuse such a sketch before CATIA is asked.

2  ENGRAVE CLEANUP (2026-11-12, P1-4). The report asked for cleanup=true or a
   prefix purge, reasoning that the ~19 elements a stroke leaves are "only
   referenced by that boolean, so deleting them cannot break anything else". That
   is backwards: four cells, deleting the tube, the plane, the point or the circle
   each left a part that never updates again. Hiding them is safe and is now the
   default.

The pure arithmetic is pinned here; the COM behaviour lives in the two probes.
"""
from __future__ import annotations

import inspect
import types

import pytest

from catia_mcp.geometry2d import open_ends, segments_of
from catia_mcp.results import ArgumentRejected, PreconditionRejected
from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.part_design import PartDesignTools


# ── the rule itself: which endpoints are free ──────────────────────────────

def test_a_closed_triangle_has_no_free_end():
    elements = [{"kind": "line", "x1": 0, "y1": 0, "x2": 10, "y2": 0},
                {"kind": "line", "x1": 10, "y1": 0, "x2": 0, "y2": 10},
                {"kind": "line", "x1": 0, "y1": 10, "x2": 0, "y2": 0}]

    assert open_ends(segments_of(elements)) == []


def test_a_circle_is_closed_by_itself_and_not_called_open():
    """Its two ends are one point: counting that as free called every circle open."""
    circle = [{"kind": "circle", "cx": 30, "cy": 20, "radius": 8}]

    assert open_ends(segments_of(circle)) == []


def test_a_rectangle_is_four_lines_that_close():
    assert open_ends(segments_of(
        [{"kind": "rectangle", "x1": 0, "y1": 0, "x2": 60, "y2": 40}])) == []


def test_a_circle_plus_a_spline_inside_it_has_exactly_two_free_ends():
    """The report's geometry, minus the division: still unusable as a profile."""
    elements = [{"kind": "circle", "cx": 30, "cy": 20, "radius": 8},
                {"kind": "spline", "closed": False,
                 "pts": [[27, 17], [30, 20], [33, 23]]}]

    ends = open_ends(segments_of(elements))

    assert ends == [(27.0, 17.0), (33.0, 23.0)]


# ── the refusal, from the drawing record ───────────────────────────────────

class _Sketch:
    def __init__(self, name: str) -> None:
        self.Name = name


class _Conn:
    def __init__(self, record: dict) -> None:
        self.active_document = types.SimpleNamespace(Name="P.CATPart")
        self.sketch_outlines = record


def _tools(elements: list[dict] | None) -> PartDesignTools:
    record = ({'P.CATPart::草图.2': {"placement": None, "elements": elements}}
              if elements is not None else {})
    return PartDesignTools(connection=_Conn(record))


def test_an_open_curve_in_the_record_is_refused_before_catia():
    tools = _tools([{"kind": "circle", "cx": 30, "cy": 20, "radius": 8},
                    {"kind": "spline", "closed": False,
                     "pts": [[27, 17], [30, 20], [33, 23]]}])

    with pytest.raises(PreconditionRejected) as caught:
        tools._refuse_open_curves(_Sketch("草图.2"), allow=False)

    assert caught.value.code == "SKETCH_HAS_OPEN_CURVES"
    message = str(caught.value)
    # The measured comparison, not a rule invented from the report's wording.
    assert "603.2" in message and "0 mm3" in message
    # Where the break is, so the caller can move it.
    assert "(27.0000, 17.0000)" in message
    assert caught.value.modified is False
    # And the two ways out, one of which is "you may be right, go anyway".
    assert any("own sketch" in action for action in caught.value.next_actions)
    assert any("allow_open_curves" in action
               for action in caught.value.next_actions)


def test_a_closed_sketch_is_not_refused():
    tools = _tools([{"kind": "circle", "cx": 30, "cy": 20, "radius": 8}])

    tools._refuse_open_curves(_Sketch("草图.2"), allow=False)  # no raise


def test_allow_open_curves_bypasses_the_refusal():
    """A refusal that can only ever say no is worse than CATIA's own answer."""
    tools = _tools([{"kind": "line", "x1": 0, "y1": 0, "x2": 30, "y2": 30}])

    tools._refuse_open_curves(_Sketch("草图.2"), allow=True)  # no raise


def test_an_unrecorded_sketch_is_not_refused():
    """No record is not a clean bill of health - but it is not a refusal either."""
    tools = _tools(None)

    tools._refuse_open_curves(_Sketch("草图.2"), allow=False)  # no raise


def test_pad_and_pocket_both_check_before_the_factory():
    for method in (PartDesignTools._pad, PartDesignTools._pocket):
        source = inspect.getsource(method)
        assert "_refuse_open_curves(sketch" in source
        # Before AddNewPad / AddNewPocket, or CATIA has already been asked.
        check_at = source.index("_refuse_open_curves")
        assert check_at < source.index("AddNew")


# ── engrave helpers: hide, never delete ────────────────────────────────────

class _HideRecorder(GSDTools):
    """A GSDTools that records the visibility calls instead of making them."""

    def __init__(self) -> None:
        super().__init__(connection=None)
        self.calls: list[tuple[str, bool]] = []

    def _set_helper_visibility(self, name: str, show: bool) -> None:
        self.calls.append((name, show))


def test_helpers_default_to_hide_and_say_why_they_cannot_be_deleted():
    tools = _HideRecorder()

    text = tools._engrave_helpers(["Trace_at0.5", "EG0_N", "EG0_P", "EG0_T"],
                                  "hide")
    assert tools.calls == [("Trace_at0.5", False), ("EG0_N", False),
                           ("EG0_P", False), ("EG0_T", False)]

    assert "4 helper element(s)" in text and "now hidden" in text
    assert "'EG0_T'" in text
    # The measured reason, with the number of cells.
    assert "four separate" in text and "update fails for good" in text
    assert "helpers='show'" in text


def test_show_says_shown_and_keep_touches_nothing():
    tools = _HideRecorder()

    assert "now shown" in tools._engrave_helpers(["A"], "show")
    assert tools.calls == [("A", True)]
    tools.calls.clear()
    assert tools._engrave_helpers(["A"], "keep") == ""
    assert tools.calls == []


def test_a_helper_whose_visibility_fails_is_named_as_still_there():
    class _Broken(_HideRecorder):
        def _set_helper_visibility(self, name: str, show: bool) -> None:
            raise RuntimeError("no such element")

    text = _Broken()._engrave_helpers(["EG0_N"], "hide")

    assert "could not be set" in text
    assert "still in the tree" in text


def test_an_unknown_helpers_value_is_refused_before_catia():
    tools = GSDTools(connection=None)

    with pytest.raises(ArgumentRejected) as caught:
        tools._surface_engrave({"curve": "Trace", "depth": 0.4,
                                "helpers": "hide_all"})

    assert caught.value.code == "BAD_ARGUMENT"
    assert "Nothing was created" in str(caught.value)


def test_the_engrave_schema_carries_the_three_modes():
    definitions = {item["name"]: item
                   for item in GSDTools(connection=None).get_tool_definitions()}
    schema = definitions["catia_surface_engrave"]["inputSchema"]

    assert schema["properties"]["helpers"]["enum"] == ["hide", "show", "keep"]
    assert schema["properties"]["helpers"]["default"] == "hide"
    assert "NONE of the three deletes them" in \
        schema["properties"]["helpers"]["description"]


# ── both facts are in the ceilings, with their numbers ─────────────────────

def _ceiling(member_startswith: str) -> dict:
    for entry in _CAPABILITY_CEILINGS:
        if entry["member"].startswith(member_startswith):
            return entry
    raise AssertionError(f"no ceiling entry starting '{member_startswith}'")


def test_the_open_curve_ceiling_carries_all_four_cells():
    detail = _ceiling("a pad or pocket from a sketch that also holds")["detail"]

    assert "603.2" in detail
    assert "INSIDE" in detail and "LINE through it" in detail
    assert "SKETCH_HAS_OPEN_CURVES" in detail


def test_the_engrave_ceiling_says_hide_not_delete():
    entry = _ceiling("the elements one catia_surface_engrave stroke leaves behind")

    assert entry["status"] == "AVAILABLE_WITH_LIMIT"
    detail = entry["detail"]
    assert "HIDDEN, not DELETED" in detail
    assert "23987.4336290779" in detail
    assert "backwards" in detail
