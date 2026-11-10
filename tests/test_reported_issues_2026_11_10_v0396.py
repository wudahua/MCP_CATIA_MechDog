# -*- coding: utf-8 -*-
"""0.3.96: the thick-surface message measured the wrong quantity.

A 2026-11-10 report pasted three thickness attempts side by side and noticed the
message contradicts the rest of the package:

    0.4 / 0      -> "NOT below the 0.400 mm offset"
    0.2 / 0.2    -> "NOT below the 0.400 mm offset"   (total)
    0.1 / 0.1    -> "NOT below the 0.200 mm offset"   (total)

while everything else this package says (including the tool's own description) is
that the limit applies to EACH SIDE's offset, not the total wall. So the one case
where the caller had already done the right thing - split the wall in half - was
measured against the sum, and the radius looked like the blocker when it was not.

The same sentence also ended with "look at a seam or a self-intersection", which is
not a step anyone can run. It now names the tool that finds a seam.
"""
from __future__ import annotations

import types

import pytest


def _tools(monkeypatch, stl_path):
    from catia_mcp.tools.gsd import GSDTools
    from catia_mcp.tools.measurement import MeasurementTools

    monkeypatch.setattr(MeasurementTools, "_mesh_export",
                        lambda self, part, limit=None, visit=None: visit(stl_path))
    tools = GSDTools(connection=None)
    tools.conn = types.SimpleNamespace(
        get_active_part=lambda: types.SimpleNamespace(Name="P"))
    return tools


@pytest.fixture()
def cylinder_stl(tmp_path):
    from tests.test_reported_issues_2026_11_04_v0358 import _cylinder
    return _cylinder(tmp_path / "cyl.stl")


def test_a_split_offset_is_measured_one_side_at_a_time(monkeypatch, cylinder_stl):
    """The reported case: 0.1/0.1 must not be compared against 0.200."""
    tools = _tools(monkeypatch, cylinder_stl)
    message = tools._offset_blocker(object(), 0.2, 0.1)

    assert "0.100 mm single-side offset" in message
    assert "(of 0.200 mm total)" in message
    assert "curvature does not explain" in message


def test_the_sentence_ends_with_something_that_can_be_run(monkeypatch,
                                                          cylinder_stl):
    """"Look at a seam" was not executable; catia_check_surface is."""
    tools = _tools(monkeypatch, cylinder_stl)
    message = tools._offset_blocker(object(), 0.2, 0.1)

    assert "NEXT STEP THAT RUNS" in message
    assert "catia_check_surface" in message
    assert "catia_gsd_boundary(edge=N)" in message


def test_an_unsplit_offset_still_reports_the_total_as_the_offset(monkeypatch,
                                                                cylinder_stl):
    """With no `side`, the total IS the per-side offset - do not invent a split."""
    tools = _tools(monkeypatch, cylinder_stl)
    message = tools._offset_blocker(object(), 0.4)

    assert "0.400 mm single-side offset" in message
    assert "total" not in message
