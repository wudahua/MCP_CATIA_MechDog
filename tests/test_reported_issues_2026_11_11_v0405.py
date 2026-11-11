"""0.3.105: answer the message boxes instead of chasing one of them, and withdraw
the "same session" advice that five cells could not reproduce.

Three things this pins down:

* ``watched`` reports a COUNT and never claims to know what the box said, because
  the text belongs to another process and is not read back.
* ``catia_surface_engrave`` really runs inside ``dialogs.watch()`` - the wrap is in
  the dispatcher, where it covers the boolean and the four deletes too, so a test
  that only reads the function body would miss it.
* the capability table no longer asks for the projection and the engrave to share
  a session.
"""
from __future__ import annotations

import contextlib
import pathlib
import types

import pytest

import catia_mcp.tools.gsd as gsd_module
from catia_mcp import dialogs
from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS
from catia_mcp.tools.gsd import GSDTools, _answered_boxes


# ── the note ──────────────────────────────────────────────────────────
def test_a_call_with_no_box_still_says_the_watcher_ran() -> None:
    """A heartbeat, asked for by the 2026-11-11 report: with nothing printed, "the
    watcher ran and found nothing" and "the watcher was never armed" look the
    same, and that report lost two runs to modal boxes."""
    note = _answered_boxes("Engraved.", 0)
    assert note.startswith("Engraved.")
    assert "dialogs answered: 0" in note
    assert "NOTE" not in note


def test_one_box_is_reported_with_its_number() -> None:
    note = _answered_boxes("Engraved.", 1)
    assert note.startswith("Engraved.")
    assert "1 message box" in note
    assert "message boxes" not in note.replace("message box", "")


def test_several_boxes_are_reported_as_a_count() -> None:
    assert "3 message boxes" in _answered_boxes("Engraved.", 3)


def test_it_never_pretends_to_know_what_the_box_said() -> None:
    """The point of the note is the count. Naming a warning would be a guess: this
    side only sends the button message, it does not read the dialog's text."""
    note = _answered_boxes("Engraved.", 2)
    assert "COULD NOT BE READ BACK" in note
    assert "not a report that the operation was clean" in note
    assert "catia_list_features" in note


# ── the watcher ───────────────────────────────────────────────────────
def test_watch_yields_a_counter_even_when_it_is_disabled(monkeypatch) -> None:
    monkeypatch.setattr(dialogs, "_ENABLED", False)
    with dialogs.watch() as answered:
        assert isinstance(answered, dialogs.Answered)
        assert answered.clicks == 0


def test_a_disabled_watcher_does_not_need_a_catia_session(monkeypatch) -> None:
    """`_ENABLED` False must not call catia_pid(): on a machine without CATIA the
    pid lookup scans every window, which is both slow and none of its business."""
    monkeypatch.setattr(dialogs, "_ENABLED", False)

    def explode():
        raise AssertionError("catia_pid() must not run when the watcher is off")

    monkeypatch.setattr(dialogs, "catia_pid", explode)
    with dialogs.watch() as answered:
        assert answered.clicks == 0


# ── the dispatcher really wraps the call ──────────────────────────────
def _tools_with_engrave(reply: str) -> GSDTools:
    tools = GSDTools(connection=None)
    tools._surface_engrave = lambda args: reply  # type: ignore[method-assign]
    return tools


def _fake_watch(clicks: int):
    @contextlib.contextmanager
    def _watch(*args, **kwargs):
        yield types.SimpleNamespace(clicks=clicks)

    return _watch


def test_the_engrave_runs_inside_the_watcher(monkeypatch) -> None:
    """A modal box raised DURING the engrave would freeze the whole session, so the
    wrap has to be in the dispatcher - around the boolean and the deletes - not
    around the returned string."""
    monkeypatch.setattr(gsd_module.dialogs, "watch", _fake_watch(1))
    reply = _tools_with_engrave("Engraved.").execute(
        "catia_surface_engrave", {"curve": "C", "depth": 0.4})
    assert "1 message box" in reply


def test_a_clean_engrave_gets_the_heartbeat_and_no_warning(monkeypatch) -> None:
    monkeypatch.setattr(gsd_module.dialogs, "watch", _fake_watch(0))
    reply = _tools_with_engrave("Engraved.").execute(
        "catia_surface_engrave", {"curve": "C", "depth": 0.4})
    assert reply.startswith("Engraved.")
    assert "dialogs answered: 0" in reply
    assert "NOTE: CATIA raised" not in reply


def test_the_watcher_is_asked_for_every_engrave(monkeypatch) -> None:
    seen: list[int] = []

    @contextlib.contextmanager
    def _watch(*args, **kwargs):
        seen.append(1)
        yield types.SimpleNamespace(clicks=0)

    monkeypatch.setattr(gsd_module.dialogs, "watch", _watch)
    tools = _tools_with_engrave("Engraved.")
    for _ in range(3):
        tools.execute("catia_surface_engrave", {"curve": "C", "depth": 0.4})
    assert len(seen) == 3


# ── the withdrawn advice ──────────────────────────────────────────────
def test_the_reopen_row_no_longer_promises_a_working_groove() -> None:
    entry = next(e for e in _CAPABILITY_CEILINGS
                 if "saved, closed and reopened" in e["member"])
    assert entry["status"] == "SEEN_ONCE_NOT_REPRODUCED"
    detail = entry["detail"]
    assert "Reopening is not the variable" in detail
    assert "NO_MATERIAL on a reopened file is reported as NO_MATERIAL" in detail


def test_the_readme_no_longer_asks_for_one_session() -> None:
    readme = pathlib.Path(__file__).resolve().parent.parent / "README.md"
    text = readme.read_text(encoding="utf-8")
    assert "Do the projection and the engrave in ONE session" not in text
    assert "Reopening is NOT a reason to keep the calls in one session" in text


def test_the_readme_carries_the_measured_projection_grid() -> None:
    """The grid exists because advice was twice written from memory. The numbers
    below are the ones actually measured; if a row is edited, the number has to be
    re-measured, not smoothed."""
    readme = pathlib.Path(__file__).resolve().parent.parent / "README.md"
    text = readme.read_text(encoding="utf-8")
    for number in ("68.284", "32.111 mm (48.2 %)", "13.6 %", "16.0 %", "15.1 %"):
        assert number in text, number
    assert "always accepts the projection and" in text


@pytest.mark.parametrize("entry", _CAPABILITY_CEILINGS)
def test_every_ceiling_status_is_one_of_the_vocabulary(entry) -> None:
    assert entry["status"] in {
        "UNSUPPORTED_ON_B30", "NOT_IN_TYPELIB", "DECLARED_NOT_MEASURED",
        "AVAILABLE_WITH_LIMIT", "MEASURED_UNEXPLAINED",
        "SEEN_ONCE_NOT_REPRODUCED",
    }
