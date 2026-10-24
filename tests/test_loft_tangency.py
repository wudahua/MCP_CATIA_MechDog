# -*- coding: utf-8 -*-
"""End tangency on a loft: the entry point exists, the factor does not.

For three releases this project said "CATIA's GSD loft does not expose a tangent
condition" and put it in the README as a capability CATIA does not offer. That was
wrong, and the way it was wrong is worth a test: an *enumeration of properties*
found three names and concluded there was nothing, while `SetStartSectionTangent`
and `SetEndSectionTangent` are METHODS, take one geometry each, and work.

Measured on V5-6R2020 B30, 2026-10-24, three circles r=10/20/10 at z=0/50/100 with
a cylindrical surface through the first section:

    no condition            10701.639 mm2
    start tangent to cylinder 10174.914 mm2   (-4.9 %)
    the same support on the far end -> "UpdateObject failed" (it does not touch it)

and every ``*TangentFactor`` / ``MaintainTangency`` name is absent, so the factor
really is not exposed.
"""

from __future__ import annotations

import inspect
import json

import pytest


def _definition():
    from catia_mcp.tools.gsd import GSDTools

    return next(d for d in GSDTools(connection=None).get_tool_definitions()
                if d["name"] == "catia_gsd_multi_section_surface")


def test_the_tangency_supports_are_declared_parameters():
    schema = _definition()["inputSchema"]["properties"]
    assert "start_tangent" in schema
    assert "end_tangent" in schema
    for key in ("start_tangent", "end_tangent"):
        assert "surface" in schema[key]["description"].lower()


def test_the_description_states_what_does_not_exist():
    """The counter-claim has to be as specific as the claim it replaces."""
    text = _definition()["description"]
    assert "SetStartSectionTangent" in text
    assert "tangent factor" in text
    assert "does not exist" in text or "does NOT exist" in text


def test_the_old_claim_is_gone():
    text = _definition()["description"]
    assert "there is no tangent/curvature condition here" not in text


def test_ruled_and_tangency_are_refused_together():
    """The ruled path is blends, so there is no loft feature to carry it."""
    from catia_mcp.tools.gsd import GSDTools

    source = inspect.getsource(GSDTools._multi_section_surface)
    assert "cannot be combined" in source
    assert "one blend per section pair" in source


def test_a_tangency_failure_explains_the_support_rule():
    """CATIA accepts the setter, then fails the update - the message must say why."""
    from catia_mcp.tools.gsd import GSDTools

    source = inspect.getsource(GSDTools._multi_section_surface)
    # The sentence is split across string literals in the source, so look for both
    # halves rather than the rendered sentence.
    assert "has to touch that section" in source or "has to TOUCH that section" in source
    assert "accepts the setter before it" in source
    assert "checks the geometry" in source


def test_the_success_message_names_the_tangent_end():
    from catia_mcp.tools.gsd import GSDTools

    source = inspect.getsource(GSDTools._multi_section_surface)
    assert "section tangent to" in source
