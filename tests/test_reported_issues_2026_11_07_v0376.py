"""0.3.76: the gap list as data, because the sentence is what gets truncated.

The 2026-11-06 report (P1-2, third bullet) asked for the full loose-endpoint
list in a structured field rather than inside the sentence: on a six-arc outline
the reply was cut to ``... (1242 chars)``, so the coordinates the caller needed
to fix the profile were exactly the part that disappeared. The sentence itself
was already fixed in 0.3.71 (summary and advice first, coordinates last), which
only made the cut cheaper - it did not make the list reachable.

``catia_close_sketch`` now takes ``structured=true`` (default false). Nothing
that parses the text reply changes, which is why this is opt-in rather than a
new default.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from catia_mcp.tools.sketcher import SketcherTools


def _tools(ends: list[tuple[float, float]] | None = None,
           drawn: list[dict] | None = None) -> SketcherTools:
    owner = SimpleNamespace(names=[])

    def update_object(item):
        owner.names.append(getattr(item, "Name", "?"))

    owner.UpdateObject = update_object
    conn = SimpleNamespace(
        get_active_part=lambda: owner,
        refresh_display=lambda: None,
        active_document=SimpleNamespace(Name="probe.CATPart"),
        last_sketch=None,
        active_body=None,
    )
    tools = SketcherTools(conn)
    tools._active_sketch = SimpleNamespace(
        Name="草图.1", CloseEdition=lambda: None)
    tools._sketch_part = owner
    if ends is not None:
        tools._profile_ends = list(ends)
    if drawn:
        key = tools._sketch_key("草图.1")
        tools._drawn[key] = list(drawn)
    return tools


def _open_square(gap: float) -> list[tuple[float, float]]:
    """A 40x20 rectangle whose fourth corner is `gap` mm short of closing."""
    return [
        (0.0, 0.0), (40.0, 0.0),          # bottom
        (40.0, 0.0), (40.0, 20.0),        # right
        (40.0, 20.0), (0.0, 20.0),        # top
        (0.0, 20.0), (0.0, gap),          # left, stopping `gap` short of origin
    ]


# ── the data the sentence could not carry ─────────────────────────────────

def test_the_full_gap_list_is_in_the_data_not_only_in_the_sentence():
    tools = _tools(_open_square(0.0002))
    breaks = tools._profile_breaks()

    assert breaks["closed"] is False
    assert breaks["endpoints"] == 8
    assert breaks["endpoints_loose"] == 2
    assert len(breaks["breaks"]) == 1
    item = breaks["breaks"][0]
    # The pairing is by index order among the loose points, exactly as the
    # sentence computed it: (0, 0) was left loose first, then (0, 0.0002).
    assert item["from"] == [0.0, 0.0]
    assert item["to"] == [0.0, 0.0002]
    assert item["gap_mm"] == pytest.approx(0.0002, abs=5e-7)
    assert breaks["smallest_gap_mm"] == pytest.approx(0.0002, abs=5e-7)
    assert breaks["within_tolerance"] == 1
    assert breaks["tolerance_mm"] == 0.05
    assert "cannot be read back" in breaks["measured_from"]


def test_every_break_is_listed_and_counted():
    """Four joints short is the case that produced a 1242-char reply."""
    ends: list[tuple[float, float]] = []
    for x1, y1, x2, y2 in ((0, 0, 40, 0), (40, 0, 40, 20),
                           (40, 20, 0, 20), (0, 20, 0, 0)):
        ends.extend([(x1 + 0.0, y1 + 0.0), (x2 + 0.0002, y2 + 0.0002)])
    tools = _tools(ends)
    breaks = tools._profile_breaks()

    assert breaks["closed"] is False
    assert len(breaks["breaks"]) == 4
    assert breaks["within_tolerance"] == 4
    assert breaks["widest_gap_mm"] == pytest.approx(0.0002828, abs=1e-5)
    assert breaks["unpaired"] == []


def test_a_closed_profile_says_closed_and_lists_nothing():
    square = [(0.0, 0.0), (40.0, 0.0), (40.0, 0.0), (40.0, 20.0),
              (40.0, 20.0), (0.0, 20.0), (0.0, 20.0), (0.0, 0.0)]
    breaks = _tools(square)._profile_breaks()

    assert breaks["closed"] is True
    assert breaks["breaks"] == []
    assert breaks["smallest_gap_mm"] is None


def test_a_sketch_reopened_from_disk_is_null_not_closed():
    """The 2026-11-06 lesson, in the data this time.

    After reopening a file the coordinates cannot be read back, so an empty
    echo is not an empty sketch. ``closed: true`` here would be a claim about a
    sketch this session never saw - the same mistake the sentence avoided by
    staying silent.
    """
    breaks = _tools([])._profile_breaks()

    assert breaks["closed"] is None
    assert breaks["endpoints"] == 0
    assert breaks["breaks"] == []


def test_an_intentionally_open_profile_is_reported_but_not_nagged_about():
    """12 mm apart is a profile that is open on purpose (a sweep guide)."""
    tools = _tools([(0.0, 0.0), (40.0, 0.0), (40.0, 0.0), (40.0, 20.0),
                    (40.0, 20.0), (0.0, 20.0), (0.0, 20.0), (0.0, 12.0)])
    breaks = tools._profile_breaks()

    assert breaks["closed"] is False
    assert breaks["smallest_gap_mm"] == pytest.approx(12.0, abs=1e-6)
    assert breaks["within_tolerance"] == 0
    # The sentence still stays quiet - that was deliberate in 0.3.71 and must
    # not regress just because the data is now available.
    assert tools._profile_warning() == ""


# ── opt-in, and the sentence is unchanged ─────────────────────────────────

def test_structured_off_is_the_same_sentence_as_before():
    tools = _tools(_open_square(0.0002))
    text = tools._close_sketch()

    assert isinstance(text, str)
    assert "Sketch '草图.1' closed." in text
    assert "WARNING: the profile is not closed - 2 loose endpoint(s)" in text
    assert "(0.0000, 0.0000) <-> (0.0000, 0.0002) = 0.0002 mm" in text


def test_structured_on_carries_the_same_sentence_plus_the_data():
    tools = _tools(_open_square(0.0002))
    payload = tools._close_sketch(structured=True)

    assert isinstance(payload, dict)
    assert payload["ok"] is True
    assert payload["code"] == "SKETCH_CLOSED"
    assert payload["tool"] == "catia_close_sketch"
    # The message is the reply a caller already knows, character for character.
    plain = _tools(_open_square(0.0002))._close_sketch()
    assert payload["message"] == plain
    assert payload["data"]["sketch"] == "草图.1"
    assert payload["data"]["breaks"]["closed"] is False
    assert len(payload["data"]["breaks"]["breaks"]) == 1


def test_modified_follows_what_this_session_actually_drew():
    """A reopen-and-close wrote nothing; it must not claim it did."""
    nothing_drawn = _tools(_open_square(0.0002))._close_sketch(structured=True)
    assert nothing_drawn["effects"]["modified"] is False

    drew = _tools(_open_square(0.0002),
                  drawn=[{"kind": "line", "x1": 0, "y1": 0, "x2": 40, "y2": 0}])
    assert drew._close_sketch(structured=True)["effects"]["modified"] is True


def test_the_argument_is_optional_and_boolean_in_the_schema():
    schema = [tool for tool in SketcherTools(None).get_tool_definitions()
              if tool["name"] == "catia_close_sketch"][0]
    prop = schema["inputSchema"]["properties"]["structured"]

    assert prop["type"] == "boolean"
    assert "data.breaks[]" in prop["description"]
    # Opt-in: a schema that required it would break every existing caller.
    assert "required" not in schema["inputSchema"]
    # And the description says how to get it, so a model can find it.
    assert "structured=true" in schema["description"]


def test_execute_passes_the_flag_through():
    # A separate instance per call: closing a sketch clears the handle, which is
    # the correct behaviour and would make the second call a precondition error.
    assert isinstance(
        _tools(_open_square(0.0002)).execute("catia_close_sketch", {}), str)
    payload = _tools(_open_square(0.0002)).execute(
        "catia_close_sketch", {"structured": True})
    assert isinstance(payload, dict)
    assert payload["data"]["breaks"]["closed"] is False


def test_the_server_forwards_a_dict_reply_as_an_envelope():
    """The plumbing that makes the flag useful: a dict is not stringified."""
    source = inspect.getsource(__import__("catia_mcp.server", fromlist=["x"]))
    assert "if isinstance(result, dict):" in source
    assert "structured_result(result)" in source
