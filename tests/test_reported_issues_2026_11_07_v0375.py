# -*- coding: utf-8 -*-
"""A non-ASCII file NAME is refused in BOTH directions (0.3.75).

The 2026-11-07 report's P0-1 was about saving. While measuring it, the other
direction got measured too, and it changes the advice this package had been
giving since 0.3.71:

    save a part as ASCII, then rename it to 中文.CATPart   <- told to do this

On B30 the renamed file cannot be opened (``Documents.Open`` refuses it with
DISP_E_EXCEPTION, and leaves a modal "突发事件报告" dialog on the automation
thread - the reporter photographed exactly that). So "the .CATPart does not care
what it is called" was false, and four messages said it.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from catia_mcp.results import PathNotSupported


def _conn(opened: list[str] | None = None):
    """A connection whose documents collection records Open() calls."""
    opened = opened if opened is not None else []
    made = SimpleNamespace(Name="Opened.CATPart", FullName="")

    class _Docs:
        Count = 0

        @staticmethod
        def Item(index):  # pragma: no cover - nothing is open in these tests
            raise AssertionError("no document should be looked up")

        @staticmethod
        def Open(native):
            opened.append(native)
            return made

    return SimpleNamespace(ensure_connected=lambda: None, documents=_Docs(),
                           last_sketch="stale", active_body="stale",
                           refresh_display=lambda: None,
                           app=SimpleNamespace(ActiveDocument=made)), opened


@pytest.mark.parametrize("path", [
    r"F:\catia_probe\副本.CATPart",
    r"F:\catia_probe\中文目录\副本.CATPart",
    "F:/catia_probe/零件.CATProduct",
])
def test_open_refuses_a_non_ascii_file_name_before_catia_sees_it(path):
    """The refusal must happen here: CATIA's own refusal pops a modal dialog."""
    from catia_mcp.tools.document import DocumentTools

    conn, opened = _conn()
    tools = DocumentTools(connection=None)
    tools.conn = conn
    with pytest.raises(PathNotSupported) as caught:
        tools._open_document(path)
    assert opened == [], "Documents.Open must not be called at all"
    message = str(caught.value)
    assert "cannot open" in message
    assert "modal" in message
    assert "part number" in message.lower() or "零件" in message


def test_open_still_accepts_an_ascii_name_in_a_chinese_folder():
    """The FOLDER is innocent - that is the half this package got wrong twice."""
    from catia_mcp.tools.document import DocumentTools

    conn, opened = _conn()
    tools = DocumentTools(connection=None)
    tools.conn = conn
    reply = tools._open_document(r"F:\catia_probe\中文目录\probe_ascii.CATPart")
    assert opened and "Opened document" in reply


def test_no_message_tells_the_caller_to_rename_the_file():
    """Four sites said "rename it afterwards; the name does not matter"."""
    import catia_mcp
    from pathlib import Path

    package = Path(catia_mcp.__file__).parent
    banned = ("does not care what it is called", "rename the file afterwards",
              "rename afterwards", "and rename the file")
    offenders = []
    for path in sorted(package.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for phrase in banned:
            if phrase in text:
                offenders.append(f"{path.name}: {phrase}")
    assert not offenders, offenders


def test_the_save_refusal_says_where_the_chinese_wording_belongs():
    from catia_mcp.results import PathNotSupported as _Path

    actions = " ".join(_Path.next_actions)
    assert "part number" in actions
    assert "cannot open" in actions
    # The one advice that produced unusable files is gone.
    assert "rename" not in actions.lower() or "does NOT work" in actions


def test_the_copy_route_still_writes_a_chinese_named_file():
    """catia_save_as_copy is a filesystem copy, so the name is not CATIA's call.

    Measured: a 57 022 B copy appeared at F:\\...\\副本.CATPart. Useful only as
    an artefact - CATIA cannot reopen it, which is why nothing recommends it as
    a working file.
    """
    from catia_mcp.tools.document import DocumentTools

    source = inspect.getsource(DocumentTools._save_as_copy)
    assert "CopyFile" in source or "copyfile" in source
    # and it must not claim the copy is a usable working document
    assert "still points at" in source
