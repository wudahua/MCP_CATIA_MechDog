# -*- coding: utf-8 -*-
"""0.3.122 - many splines in one call, and what it refuses to average away.

Asked for by the 0.3.120 acceptance report (P2-3): a 17-section handle dome was
17 calls and 380 hand-fed coordinates, a 13-section bend the same. Measured on
B30 2026-11-13 (scripts/b30_v0423_probe_splines.py), one fresh document per case:

    A  three 18-point rings in one call -> all three in the tree, then a solid
       loft through them measures 27945.3 mm3
    B  entry 2 with ONE point          -> refused at validation, nothing built
    C  two entries named X1            -> refused at validation, nothing built
    E  entry 2 with a repeated point    -> CATIA refuses that one at update;
                                          entry 1 stays and is reported by name
    D  a closed ring repeating its first point -> 19 sent, 18 on the curve, and
                                          the reply says one was dropped

Everything here is offline; the stubs answer only what ``_splines`` asks for.
"""

from __future__ import annotations

import pytest

from catia_mcp.tools.gsd import GSDTools


# ── stubs ────────────────────────────────────────────────────────────────────


class _Part:
    def __init__(self, names=()):
        self.names = set(names)
        self.lookups = []

    def FindObjectByName(self, name):
        self.lookups.append(name)
        return object() if name in self.names else None


class _Conn:
    def __init__(self, part=None):
        self.part = part or _Part()
        self.connected = 0

    def ensure_connected(self):
        self.connected += 1

    def get_active_part(self):
        return self.part


def _tools(names=(), third_raises_on=None):
    tools = GSDTools(connection=None)
    tools.conn = _Conn(_Part(names))
    calls = []
    created = []

    def fake_spline(arguments):
        calls.append(dict(arguments))
        name = arguments.get("name")
        if third_raises_on is not None and len(calls) == third_raises_on:
            raise RuntimeError("CATIA rejected the feature (update failed)")
        created.append(name)
        return f"3D spline created. Feature: '{name}'"

    tools._spline = fake_spline
    return tools, calls


def _entry(name, count=3, **extra):
    points = [[float(i), 0.0, 0.0] for i in range(count)]
    entry = {"name": name, "points": points}
    entry.update(extra)
    return entry


# ── 1. every malformed entry is refused BEFORE anything is built ────────────


@pytest.mark.parametrize("arguments,code", [
    ({}, "ENTRIES_REQUIRED"),
    ({"entries": []}, "ENTRIES_REQUIRED"),
    ({"entries": "S01"}, "ENTRIES_REQUIRED"),
    ({"entries": ["S01"]}, "BAD_ENTRY"),
    ({"entries": [{"name": "A"}]}, "BAD_ENTRY"),
    ({"entries": [{"name": "A", "points": [[0, 0, 0]], "element": "草图.1"}]},
     "BAD_ENTRY"),
    ({"entries": [{"name": "A", "points": [[0, 0, 0]], "close": True}]},
     "BAD_ENTRY"),
    ({"entries": [{"name": "A", "points": [[0, 0, 0]], "closed": True}]},
     "BAD_ENTRY"),
    ({"entries": [{"name": "A", "points": [[0, 0, 0], [1, 0, 0]]},
                  {"name": "A", "points": [[0, 0, 5], [1, 0, 5]]}]},
     "NAME_REPEATED"),
])
def test_a_malformed_entry_is_refused_before_catia_is_touched(arguments, code):
    """No connection at all in this test: `ensure_connected` is never called on
    these paths, which is the proof that the check runs before the build."""
    tools, calls = _tools()
    with pytest.raises(Exception) as caught:
        tools.execute("catia_gsd_splines", arguments)
    assert getattr(caught.value, "code", "") == code
    assert calls == [], "no curve may be built from a list that did not validate"
    assert tools.conn.connected == 0, "CATIA was not even asked"


def test_a_misspelled_key_names_itself_and_the_legal_ones():
    tools, calls = _tools()
    with pytest.raises(Exception) as caught:
        tools.execute("catia_gsd_splines",
                      {"entries": [{"points": [[0, 0, 0], [1, 0, 0]],
                                    "closedd": True}]})
    message = str(caught.value)
    assert "closedd" in message
    assert "closed" in message, "the message has to list what IS accepted"
    assert calls == []


# ── 2. the loop itself ──────────────────────────────────────────────────────


def test_three_rings_become_three_splines_and_are_checked_by_name():
    tools, calls = _tools(names={"S01", "S02", "S03"})
    reply = tools.execute("catia_gsd_splines", {"entries": [
        _entry("S01", closed=True), _entry("S02", closed=True),
        _entry("S03", closed=True)]})
    assert [call["name"] for call in calls] == ["S01", "S02", "S03"]
    assert all(call["closed"] for call in calls)
    assert "Built 3 spline(s) in one call" in reply
    assert "All 3 are in the tree by name after the update" in reply
    assert tools.conn.part.lookups == ["S01", "S02", "S03"]


def test_each_entry_keeps_its_own_closed_and_create_points():
    tools, calls = _tools(names={"A", "B"})
    tools.execute("catia_gsd_splines", {"entries": [
        _entry("A", closed=True, create_points=True),
        _entry("B", closed=False)]})
    assert calls[0]["closed"] is True and calls[0]["create_points"] is True
    assert calls[1]["closed"] is False and calls[1]["create_points"] is False


def test_a_tree_that_cannot_confirm_a_name_says_what_it_verified():
    """`FindObjectByName` returning None is a real answer and must be shown.

    The 0.3.115 lesson applies: a name that was assigned is not a name that is
    there. Here S02 is missing from a fake tree and the reply has to say so.
    """
    tools, _ = _tools(names={"S01", "S03"})
    reply = tools.execute("catia_gsd_splines", {"entries": [
        _entry("S01"), _entry("S02"), _entry("S03")]})
    assert "2 of 3 could be found" in reply
    assert "MISSING: 'S02'" in reply


def test_a_closed_ring_that_repeats_its_start_loses_the_repeat_and_says_so():
    ring = [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [10.0, 10.0, 0.0], [0.0, 10.0, 0.0],
            [0.0, 0.0, 0.0]]
    tools, calls = _tools(names={"R01"})
    reply = tools.execute("catia_gsd_splines",
                          {"entries": [{"name": "R01", "points": ring,
                                        "closed": True}]})
    assert len(calls[0]["points"]) == 4, "the repeated closing point was dropped"
    assert "1 closing point(s)" in reply
    assert "'R01' (4 point(s), closed)" in reply, "the reply counts what is ON the curve"


# ── 3. a refusal in the middle is not a success ─────────────────────────────


def test_a_mid_chain_refusal_reports_what_exists_and_does_not_roll_back():
    """Measured on B30 2026-11-13: a section whose points repeat is refused by
    CATIA at update, the earlier section stays, and deleting it would leave its
    points behind (2026-11-03), so the built one is kept and named."""
    tools, calls = _tools(names={"U01"}, third_raises_on=2)
    with pytest.raises(Exception) as caught:
        tools.execute("catia_gsd_splines",
                      {"entries": [_entry("U01"), _entry("U02"), _entry("U03")]})
    error = caught.value
    assert getattr(error, "code", "") == "SPLINES_PARTIAL"
    assert getattr(error, "modified", False) is True
    data = getattr(error, "data", {}) or {}
    assert data.get("created") == ["U01"]
    assert data.get("failed_entry") == 2
    assert data.get("failed_name") == "U02"
    message = str(error)
    assert "Built 1 of 3" in message
    assert "NOTHING WAS ROLLED BACK" in message
    assert len(calls) == 2, "the loop stops at the refusal, it does not skip it"


def test_the_batch_is_registered_with_one_required_list():
    definition = next(d for d in GSDTools(connection=None).get_tool_definitions()
                      if d["name"] == "catia_gsd_splines")
    assert definition["inputSchema"]["required"] == ["entries"]
    text = definition["description"]
    assert "catia_gsd_spline" in text, "the batch has to point at the single call"
    assert "PARTIAL FAILURE IS NOT A SUCCESS" in text
