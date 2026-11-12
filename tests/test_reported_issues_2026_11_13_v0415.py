"""0.3.114: the seam check must not depend on the ORDER the segments were given.

The 2026-11-13 report (problem 6) is a wrong-warning story, and wrong warnings are
expensive: the caller had already checked the volume by hand (284.8 mm3, exact),
was told "8 of 8 junction(s) are wider than 0.05 mm, widest 33.4548 mm", and only
the next call - catia_close_sketch answering "closed" - kept it from rebuilding a
profile that was never broken. Its cause: the check compared segment i's end with
segment i+1's start, so any profile quoted in its source data's row order (a spec
table, a STEP dump, a SolidWorks feature row) looked broken.

Two things are pinned here:
  1  a connected loop given out of order is reported as closed
  2  a real gap still is reported, and the sentence names the nearest end

And one from the same report's problem 4: a pad/pocket whose UpdateObject raised
used to answer ok:true with "UNVERIFIED", which hid the root cause ten steps up
the plan. That reply is now a refusal.
"""
from __future__ import annotations

import pytest

from catia_mcp.results import FeatureRejected
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.sketcher import SketcherTools


class _Recorder(SketcherTools):
    """A SketcherTools that records what it would draw instead of drawing it."""

    def __init__(self) -> None:
        super().__init__(connection=None)
        self._active_sketch = object()
        self.calls: list[tuple] = []

    def _draw_line(self, x1, y1, x2, y2, construction=False):   # noqa: D401
        self.calls.append(("line", x1, y1, x2, y2))
        return "Line created"

    def _draw_arc(self, cx, cy, radius, start_angle=None, end_angle=None,
                  p1=None, p2=None, direction="ccw", major=False):
        self.calls.append(("arc", cx, cy, radius, start_angle, end_angle,
                           direction))
        return "Arc created"


TOP = {"type": "line", "p1": [0, 20], "p2": [60, 20]}
RIGHT = {"type": "arc", "center": [60, 0], "radius": 20,
         "start_angle": 90, "end_angle": -90, "direction": "cw"}
BOTTOM = {"type": "line", "p1": [60, -20], "p2": [0, -20]}
LEFT = {"type": "arc", "center": [0, 0], "radius": 20,
        "start_angle": -90, "end_angle": 90, "direction": "cw"}


def test_a_connected_loop_given_out_of_order_is_not_reported_as_broken():
    """The report's case, in four segments: same geometry, rows in another order.

    In the order below every neighbour pair is broken (the top line's end is
    63.2 mm from the left arc's start), and the loop is still closed - which is
    exactly what the old neighbour-pair check could not see.
    """
    tools = _Recorder()

    reply = tools._draw_profile([RIGHT, TOP, LEFT, BOTTOM])

    assert "NOT CLOSED" not in reply
    assert "WARNING" not in reply
    assert "Every segment end meets another one within 0.0000 mm" in reply
    # And it says why the old sentence appeared, so the caller is not left
    # wondering whether the order matters.
    assert "do not follow each other in the order given" in reply
    assert len(tools.calls) == 4


def test_the_order_note_is_silent_when_the_segments_do_follow_each_other():
    tools = _Recorder()

    reply = tools._draw_profile([TOP, RIGHT, BOTTOM, LEFT])

    assert "Every segment end meets another one within 0.0000 mm" in reply
    assert "do not follow each other" not in reply


def test_a_real_gap_still_reads_as_not_closed_and_names_the_nearest_end():
    tools = _Recorder()
    gapped = [TOP, RIGHT,
              {"type": "line", "p1": [60.06, -20], "p2": [0, -20]}, LEFT]

    reply = tools._draw_profile(gapped)

    assert "NOT CLOSED" in reply
    assert "0.0600 mm" in reply
    assert "its nearest end is segment" in reply
    # Both coordinates, so the caller can see which one to move.
    assert "(60.0000, -20.0000)" in reply and "(60.0600, -20.0000)" in reply
    # And the rule the message rests on: order is not what made it fail.
    assert "does NOT matter" in reply


def test_an_arc_whose_ends_are_swapped_is_still_one_endpoint():
    """A loop that closes through the arc's OTHER end is closed, not 2 breaks."""
    tools = _Recorder()
    # The same stadium, with the two arcs' directions reversed: each arc now
    # starts where it used to end.
    flipped = [
        {"type": "line", "p1": [0, 20], "p2": [60, 20]},
        {"type": "arc", "center": [60, 0], "radius": 20,
         "start_angle": -90, "end_angle": 90, "direction": "cw"},
        {"type": "line", "p1": [60, -20], "p2": [0, -20]},
        {"type": "arc", "center": [0, 0], "radius": 20,
         "start_angle": 90, "end_angle": -90, "direction": "cw"},
    ]

    reply = tools._draw_profile(flipped)

    assert "NOT CLOSED" not in reply


def test_a_pad_that_catia_refused_to_update_is_not_an_ok_reply():
    tools = PartDesignTools(connection=None)

    with pytest.raises(FeatureRejected) as caught:
        tools._unverified("pad", "凸台.1", "20.000 mm (reverse)",
                          "the measurable's Volume failed", RuntimeError("boom"),
                          [])

    assert caught.value.code == "UPDATE_FAILED"
    assert "凸台.1" in str(caught.value)
    assert "Do not build on this reply" in str(caught.value)
    # The next step has to be the diagnostic, not the next feature.
    assert any("catia_update_part" in action for action in caught.value.next_actions)


def test_an_unmeasurable_volume_without_an_update_error_stays_unverified():
    """The other half: no volume is not proof, so it must not become a refusal."""
    tools = PartDesignTools(connection=None)

    reply = tools._unverified("pad", "凸台.1", "20.000 mm (normal)",
                              "the measurable's Volume failed", None, [])

    assert reply.startswith("UNVERIFIED:")
    assert "凸台.1" in reply
