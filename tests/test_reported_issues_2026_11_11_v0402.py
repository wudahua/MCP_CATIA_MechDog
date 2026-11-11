# -*- coding: utf-8 -*-
"""0.3.102: the first projection in a document answers with a fragment.

Measured on B30 2026-11-11 with eight projections in a row in one document:

    a sketch, 8 times   2.200 / 32.111 / 32.111 ... 32.111 mm
    a copy,   8 times   2.316 / 2.316 ... 2.316 mm

Two-call experiments had disagreed three times before that, because the plateau
only shows up when the same source is projected over and over. So the tool now
projects twice and keeps the second, and the first is removed again.
"""
from __future__ import annotations

import types

import pytest

import catia_mcp.tools.part_design as part_design
from catia_mcp.results import PreconditionRejected
from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS
from catia_mcp.tools.gsd import GSDTools


class _Projection:
    def __init__(self, name: str) -> None:
        self.Name = name


class _Factory:
    def __init__(self) -> None:
        self.made: list[_Projection] = []

    def AddNewProject(self, element, support):
        made = _Projection(f"Projection.{len(self.made) + 1}")
        self.made.append(made)
        return made


class _Part:
    def __init__(self, probe_survives: bool = False) -> None:
        self.probe_survives = probe_survives

    def CreateReferenceFromObject(self, obj):
        return obj

    def FindObjectByName(self, name):
        return (types.SimpleNamespace(Name=name)
                if self.probe_survives and str(name).endswith(".1") else None)


class _Conn:
    def __init__(self, part) -> None:
        self._part = part

    def ensure_connected(self) -> None:
        return None

    def get_active_part(self):
        return self._part


def _tools(first=2.200, second=32.111, probe_survives=False,
           fail_second=False):
    part = _Part(probe_survives)
    tools = GSDTools(connection=_Conn(part))
    factory = _Factory()
    tools._factory = lambda: factory  # type: ignore[method-assign]
    tools._get_geoset = lambda: types.SimpleNamespace(  # type: ignore[method-assign]
        AppendHybridShape=lambda shape: None)
    tools._completed = lambda shape, name, message, geoset: message  # type: ignore[method-assign]
    tools._ref = lambda name, *a, **k: f"ref:{name}"  # type: ignore[method-assign]
    tools._source_length_mm = lambda name: (66.671, "measured on the sketch's 8 curves",
                                            True)  # type: ignore[method-assign]
    tools._drop_element = lambda shape: None  # type: ignore[method-assign]

    def finish(shape, name, message):
        if fail_second:
            raise RuntimeError("CATIA rejected the feature (update failed)")
        return message

    tools._finish = finish  # type: ignore[method-assign]
    tools._curve_length_mm = (  # type: ignore[method-assign]
        lambda ref: first if getattr(ref, "Name", "").endswith(".1") else second)
    return tools, factory


def test_a_projection_made_twice_keeps_the_second_pass() -> None:
    tools, factory = _tools()
    reply = tools._project({"element": "草图.1", "support": "Bottle"})
    assert len(factory.made) == 2
    assert "32.111 mm" in reply and "48.2 %" in reply
    assert "projected TWICE and kept the second" in reply
    assert "the first pass measured 2.200 mm" in reply


def test_the_two_passes_agreeing_is_said_instead_of_the_long_story() -> None:
    tools, _factory = _tools(first=17.783, second=17.783)
    reply = tools._project({"element": "草图.1", "support": "Wall"})
    assert "Both passes of this projection measured the same" in reply
    assert "projected TWICE" not in reply


def test_a_probe_that_cannot_be_removed_is_admitted() -> None:
    tools, _factory = _tools(probe_survives=True)
    reply = tools._project({"element": "草图.1", "support": "Bottle"})
    assert "could not be removed again" in reply


def test_a_failing_second_pass_still_takes_the_probe_out() -> None:
    """The probe must not survive a refusal - that is the 0.3.101 defect, one
    level up."""
    tools, factory = _tools(fail_second=True)
    dropped: list[str] = []
    tools._drop_element = lambda shape: dropped.append(  # type: ignore[method-assign]
        getattr(shape, "Name", "?"))
    with pytest.raises(PreconditionRejected):
        tools._project({"element": "草图.1", "support": "Bottle"})
    assert dropped == ["Projection.1"]


def test_the_two_pass_behaviour_is_published_in_the_ceilings() -> None:
    entry = next((e for e in _CAPABILITY_CEILINGS
                  if "first projection in a document" in e["member"]), None)
    assert entry is not None
    assert entry["status"] == "AVAILABLE_WITH_LIMIT"
    assert "32.111" in entry["detail"]
    assert "unknown" in entry["detail"].lower()


def test_the_low_share_warning_no_longer_sends_callers_to_translate() -> None:
    """0.3.100 told callers to move the element closer; measured, a translated
    copy lands 3.5 % where the sketch lands 48.2 %."""
    tools, _factory = _tools(first=2.200, second=2.200)
    reply = tools._project({"element": "草图.1", "support": "Bottle"})
    assert "Do NOT reach for catia_gsd_translate" in reply
    assert "Move the element closer" not in reply
