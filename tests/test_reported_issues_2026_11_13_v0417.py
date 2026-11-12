"""0.3.115: two visibility facts, and a pair of chamfers that would not fail.

The 2026-11-12 report (P0-2) tried three ways to get a clean picture and got an
empty viewport from two of them. Measured on B30 2026-11-13, with pictures for
every frame:

    solid loft built                        on screen (53 978 B)
    GSD_Set hidden                          STILL on screen, unchanged
    GSD_Set shown again                     same picture
    kind='construction' hidden               EMPTY: 61 008 B, solid gone
    kind='construction' shown again          back, 53 978 B - the same bytes

So the trap is `kind='construction'` (a loft's section curves ARE construction, and
CATIA stops displaying a feature whose input is hidden), not hiding a set, and not
`clean=true`. These tests pin the two replies that have to say so - they read the
source, because the behaviour itself needs CATIA and lives in the acceptance script
scripts/b30_v0417_visibility_fix.py.

The paired-chamfer claim (2026-11-13, P0) is pinned the other way round: six cells
on B30 all succeeded, so what ships is a ceiling entry that says so.
"""
from __future__ import annotations

import inspect

from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS
from catia_mcp.tools.part_design import PartDesignTools


def test_hiding_construction_warns_that_the_solid_can_go_with_it():
    source = inspect.getsource(PartDesignTools._set_visibility_kind)

    assert "can take the SOLID off the screen" in source
    # And the way back, which was measured, not assumed.
    assert "with show=true to get the picture back" in source
    # Only on the hiding side: showing cannot hide anything.
    assert 'vanish = "" if show else' in source


def test_showing_a_body_also_shows_the_features_in_it():
    """The report's own sequence: body show=true answered "is now shown" and did
    nothing, because the BODY had never been hidden - its feature had."""
    source = inspect.getsource(PartDesignTools._set_visibility)

    assert 'if show and where == "body":' in source
    assert "target.Shapes" in source
    # The count has to reach the reply, otherwise the sentence is unchanged.
    assert "children += 1" in source


def test_hiding_a_set_still_says_it_leaves_the_body_alone_and_names_the_real_trap():
    source = inspect.getsource(PartDesignTools._set_visibility)

    assert "leaves a solid built in the body alone" in source
    # The measured exception, in the same breath as the promise.
    assert "What is NOT safe is kind='construction'" in source


def _ceiling(member_startswith: str) -> dict:
    for entry in _CAPABILITY_CEILINGS:
        if entry["member"].startswith(member_startswith):
            return entry
    raise AssertionError(f"no ceiling entry starting '{member_startswith}'")


def test_the_visibility_ceiling_carries_both_numbers():
    entry = _ceiling("hiding construction elements")

    assert entry["status"] == "AVAILABLE_WITH_LIMIT"
    detail = entry["detail"]
    # The empty frame and the frame with the solid, so the claim can be re-checked.
    assert "61 008" in detail and "53 978" in detail
    # And the two things that did NOT do it, because the report blamed them.
    assert "does not do this" in detail and "clean=true" in detail


def test_the_paired_chamfer_ceiling_says_not_reproduced_and_what_is_unknown():
    entry = _ceiling("two mirrored chamfers")

    assert entry["status"] == "SEEN_ONCE_NOT_REPRODUCED"
    detail = entry["detail"]
    assert "six cells" in detail
    assert "UNKNOWN" in detail
    # The numbers a re-run would have to hit.
    assert "12 000 -> 11 980 -> 11 960" in detail
