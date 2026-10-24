# -*- coding: utf-8 -*-
"""The isolated worker: a call that never returns must not take the server with it.

The reporter's ``catia_add_component`` sat for 2 h 14 min on a CATIA file dialog
(2026-10-24). The dialog is answered by ``dialogs.watch`` when it is a ``#32770``
message box, but CATIA's own file prompts are ``CATDlgDialog`` windows, which the
watchdog deliberately leaves alone. So the call moved to a child process that can
be killed; these tests exercise that mechanism without needing CATIA, by pointing
``CATIA_MCP_GUARD_CMD`` at a script that behaves like each outcome.
"""

from __future__ import annotations

import json
import os
import textwrap

import pytest

from catia_mcp import guard


def _fake_child(tmp_path, body: str):
    """Write a stand-in child and return the CATIA_MCP_GUARD_CMD value for it."""
    script = tmp_path / "fake_child.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    return f'"{os.sys.executable}" "{script}"'


def test_a_hanging_call_is_killed_and_reported(monkeypatch, tmp_path):
    monkeypatch.setenv("CATIA_MCP_GUARD_CMD", _fake_child(tmp_path, """
        import sys, time
        open(sys.argv[2], "w").write("{}")
        time.sleep(120)          # the dialog nobody answers
    """))
    result = guard.run("add_components", {"path": "x.CATPart"}, seconds=1.5)

    assert result.timed_out
    assert result.code == "GUARD_TIMEOUT"
    assert result.seconds < 20, "the kill has to be immediate, not eventual"
    assert "isolated" in result.message


def test_a_child_that_reports_success_is_believed(monkeypatch, tmp_path):
    monkeypatch.setenv("CATIA_MCP_GUARD_CMD", _fake_child(tmp_path, """
        import json, sys
        json.dump({"ok": True, "code": "OK", "message": "",
                   "data": {"before": 1, "after": 2, "added": ["BLK.1"]}},
                  open(sys.argv[2], "w"))
    """))
    result = guard.run("add_components", {"path": "x.CATPart"}, seconds=30)

    assert result.ok and not result.timed_out
    assert result.data["after"] == 2
    assert result.data["added"] == ["BLK.1"]


def test_a_child_that_cannot_report_is_not_treated_as_success(monkeypatch, tmp_path):
    """Died before writing: the caller must fall back, not assume it worked."""
    monkeypatch.setenv("CATIA_MCP_GUARD_CMD", _fake_child(tmp_path, """
        import sys
        raise SystemExit(3)
    """))
    result = guard.run("add_components", {"path": "x.CATPart"}, seconds=30)

    assert not result.ok
    assert result.unavailable
    assert "no result" in result.message


def test_an_unknown_operation_is_rejected_before_spawning():
    with pytest.raises(ValueError):
        guard.run("format_hard_disk", {}, seconds=1)


def test_the_child_never_touches_the_server_streams():
    """A stdio transport reads JSON-RPC from stdin; a chatty child corrupts it."""
    import inspect

    source = inspect.getsource(guard.run)
    assert "stdin=subprocess.DEVNULL" in source
    assert "stdout=subprocess.DEVNULL" in source


def test_the_child_imports_the_package_the_server_runs():
    import inspect

    source = inspect.getsource(guard.run)
    assert "PYTHONPATH" in source
    assert "_package_root" in source


def test_the_child_attaches_instead_of_launching_catia():
    """Dispatch here would start a SECOND CATIA when none is running."""
    import inspect

    from catia_mcp import _guard_child

    source = inspect.getsource(_guard_child._attach)
    assert "GetActiveObject" in source
    assert "Dispatch(" not in source


def test_only_the_recovery_path_clicks_catia_own_dialogs():
    """CATDlgDialog is a human's panel everywhere else - see dialogs.watch."""
    import inspect

    cleanup = inspect.getsource(guard.cleanup)
    assert 'classes=("#32770", "CATDlgDialog")' in cleanup

    from catia_mcp import dialogs

    watcher = inspect.getsource(dialogs.watch)
    assert 'classes=("#32770",)' in watcher


def test_the_guard_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("CATIA_MCP_GUARD", "0")
    assert not guard.enabled()
    monkeypatch.setenv("CATIA_MCP_GUARD", "1")
    assert guard.enabled()


def test_add_component_reports_a_timeout_with_the_state_it_found(monkeypatch):
    """The reply has to say whether the insert happened anyway."""
    import inspect

    from catia_mcp.tools.assembly import AssemblyTools

    source = inspect.getsource(AssemblyTools._add_component)
    assert "outcome.timed_out" in source
    assert "guard.cleanup()" in source
    assert "components(s) - " in source or "component(s)" in source
    assert "catia_list_components" in source
