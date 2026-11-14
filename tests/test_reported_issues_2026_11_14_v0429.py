"""0.3.127: the non-ASCII file name in one step, and a cheap server identity.

The 2026-11-14 report's P1-5 (three sub-points) and P2-1. No CATIA needed here;
the live half is scripts/b30_v0429_audit.py.
"""
from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace

import pytest

from catia_mcp import dialogs
from catia_mcp.results import PathNotSupported
from catia_mcp.tools.diagnostics import DiagnosticsTools
from catia_mcp.tools.document import DocumentTools


# ── P1-5: the deterministic ASCII conversion ────────────────────────────────


def test_the_ascii_name_keeps_what_is_already_ascii():
    path, title = DocumentTools._ascii_name_for(
        r"F:\parts\苹果AirPods一二代.CATPart")
    assert path == r"F:\parts\AirPods.CATPart"
    # The wording is what the part number should say - the caller asked for it.
    assert title == "苹果AirPods一二代"


def test_a_name_with_nothing_ascii_falls_back_rather_than_to_an_empty_string():
    path, title = DocumentTools._ascii_name_for(r"F:\parts\零件.CATPart")
    assert os.path.basename(path) == "Part.CATPart"
    assert title == "零件"


def test_a_name_that_starts_with_a_digit_gets_a_prefix():
    """'2号件' would have become '2.CATPart', and a file called '2' is legal but a
    poor thing to hand CATIA - and to read in a tree."""
    path, _title = DocumentTools._ascii_name_for(r"F:\parts\2号件.CATPart")
    assert os.path.basename(path) == "Part_2.CATPart"


def test_the_conversion_is_DETERMINISTIC():
    """A retry must land on the same file. A date stamp or a sequence number was
    the obvious alternative and it fails exactly this test: the second attempt
    would write a second file beside the first."""
    first = DocumentTools._ascii_name_for(r"F:\parts\苹果AirPods一二代.CATPart")
    second = DocumentTools._ascii_name_for(r"F:\parts\苹果AirPods一二代.CATPart")
    assert first == second


def test_the_extension_and_folder_survive():
    path, title = DocumentTools._ascii_name_for(r"F:\中文目录\柄部.CATProduct")
    # Nothing in '柄部' is ASCII, so the stem falls back and the FOLDER is
    # untouched - a Chinese folder saves normally on this build (measured).
    assert path == r"F:\中文目录\Part.CATProduct"
    assert title == "柄部"
    # ... and a name with no extension still gets .CATPart, so CATIA knows what
    # kind of document is being written.
    path, _title = DocumentTools._ascii_name_for(r"F:\parts\柄部")
    assert path.endswith(".CATPart")


def test_no_dots_or_spaces_leak_into_the_ascii_name():
    """A name that keeps a '.' becomes 'part.v2.CATPart' -> two extensions, and
    CATIA takes the last one."""
    path, _title = DocumentTools._ascii_name_for(r"F:\parts\件 v2.CATPart")
    kept = os.path.basename(path)
    assert kept.count(".") == 1


# ── P1-5: the refusal is now short enough to read─────────────────────────────


def test_the_saveas_refusal_fits_in_a_reply():
    """It was ~700 characters, carrying probe dates and screenshot evidence. That
    material belongs in PathNotSupported's docstring, where it still is; a reply
    read on every failure needs the action, not the archaeology (2026-11-14,
    P1-5 third point).

    The path comes from the temporary directory, not a drive letter: this suite
    runs on machines without F: (test_reported_issues_2026_11_10_v0391).
    """
    target = os.path.join(tempfile.gettempdir(), "probe", "中文.CATPart")
    with pytest.raises(PathNotSupported) as caught:
        dialogs.save_as(None, target)
    message = str(caught.value)
    assert len(message) < 600, f"{len(message)} characters"
    # What a caller has to be able to act on.
    assert "ASCII" in message
    assert "ascii_fallback" in message
    # And the one fact that stops a wrong retry.
    assert "OPEN" in message


def test_the_open_refusal_says_the_retry_is_worse_than_the_error():
    tools = DocumentTools(connection=None)
    # No conn at all on purpose: the name check has to fire BEFORE the connection,
    # or an argument error starts CATIA on a machine that had none running.
    target = os.path.join(tempfile.gettempdir(), "probe", "副本.CATPart")
    with pytest.raises(PathNotSupported) as caught:
        tools._open_document(target)
    message = str(caught.value)
    assert len(message) < 600, f"{len(message)} characters"
    assert "modal" in message
    assert "ASCII" in message


# ── P2-1: catia_server_info ─────────────────────────────────────────────────


def test_the_cheap_identity_does_not_connect():
    """A version lookup has no business opening a CAD application."""
    tools = DiagnosticsTools(connection=None)
    payload = tools._server_info_tool()

    assert payload["ok"] is True
    assert payload["code"] == "SERVER_INFO"
    assert payload["data"]["catia"]["connected"] is False
    assert "not connected" in payload["message"]


def test_it_reports_the_version_and_the_real_tool_count():
    tools = DiagnosticsTools(connection=None)
    tools.tool_count = 149
    payload = tools._server_info_tool()

    assert payload["data"]["version"] != ""
    assert payload["data"]["tool_count"] == 149
    assert "149 tool(s)" in payload["message"]


def test_a_connected_session_adds_the_release_and_the_document_count():
    tools = DiagnosticsTools(connection=None)
    tools.tool_count = 149
    tools.conn = SimpleNamespace(
        is_connected=True,
        _get_version=lambda: "V5-6R2020  B30 SP0",
        documents=SimpleNamespace(Count=3),
        active_document=SimpleNamespace(Name="Body.CATPart"),
    )
    payload = tools._server_info_tool()

    catia = payload["data"]["catia"]
    assert catia["connected"] is True
    assert catia["release"] == "V5-6R2020  B30 SP0"
    assert catia["documents"] == 3
    assert catia["active_document"] == "Body.CATPart"
    assert "V5-6R2020" in payload["message"]


def test_a_broken_connection_object_is_reported_not_raised():
    """'I could not tell' must not become an exception in a tool whose job is to
    answer cheaply."""
    class _Angry:
        @property
        def is_connected(self):
            raise RuntimeError("COM is gone")

    tools = DiagnosticsTools(connection=None)
    tools.conn = _Angry()
    payload = tools._server_info_tool()
    assert payload["ok"] is True
    assert payload["data"]["catia"]["connected"] is False


def test_it_is_registered_and_needs_no_arguments():
    definitions = {item["name"]: item
                   for item in DiagnosticsTools(connection=None).get_tool_definitions()}
    assert "catia_server_info" in definitions
    schema = definitions["catia_server_info"]["inputSchema"]
    assert schema["type"] == "object"
    assert not schema.get("required")
    # And the description has to say the thing that makes it different from
    # diagnose, or a caller has no reason to prefer it.
    assert "probe" in definitions["catia_server_info"]["description"].lower()
