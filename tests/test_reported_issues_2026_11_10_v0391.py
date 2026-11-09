"""0.3.91: the three things a 0.3.89 report found by running the suite on a
machine with no F: drive.

  * one test hardcoded `F:/ascii/x.CATPart`, so `os.makedirs('F:\\ascii')` raised
    before the fake SaveAs ever ran and the suite was 1445/1446 there. The test
    now uses tmp_path - and the reason it could fail at all is the second item
  * `os.makedirs` sat OUTSIDE every try in `dialogs.save_as`, so a folder that
    cannot be created escaped as a bare PermissionError and the envelope called
    it COM_ERROR, with advice about `in_work_object` for a path problem
  * staging set `_staging_attempted` BEFORE the call, and the failure prose read
    that as "the document was saved to %TEMP% and the move was refused too" -
    plus a causal conclusion on top of it. Their fake-doc reproduction: two
    SaveAs calls, no staging file, sentence unchanged
"""
from __future__ import annotations

import ast
import pathlib
import re
import types

import pytest

from catia_mcp.results import CodedError


# ── the folder: a filesystem refusal is a save refusal ──────────────────


def test_a_folder_that_cannot_be_created_is_a_save_failure(monkeypatch, tmp_path):
    """The refusal happens BEFORE CATIA is asked, and the envelope says so.

    Reported 2026-11-09 (no F: drive): `os.makedirs(parent, exist_ok=True)` was
    called outside the try that wraps `doc.SaveAs`, so the PermissionError escaped
    and `server._execute_tool` reported it as COM_ERROR - with the advice that
    belongs to a COM failure. Nothing was written, and retrying cannot help.
    """
    from catia_mcp import dialogs

    asked: list[str] = []

    def refuse(*args, **kwargs):
        raise PermissionError(13, "拒绝访问", r"F:\\")

    monkeypatch.setattr(dialogs.os, "makedirs", refuse)
    doc = types.SimpleNamespace(Name="P.CATPart",
                               SaveAs=lambda path: asked.append(str(path)))

    with pytest.raises(CodedError) as caught:
        dialogs.save_as(doc, str(tmp_path / "ascii" / "x.CATPart"), tries=1, wait=0)

    error = caught.value
    assert error.code == "SAVE_FAILED"
    assert error.modified is False
    assert error.data["call"] == "os.makedirs"
    assert error.data["folder"].endswith("ascii")
    # CATIA was never asked: the fake would have recorded it.
    assert asked == []
    assert "CATIA was NOT asked to save anything yet" in str(error)
    # And the advice is about the drive, not about COM.
    assert any("drive" in action for action in error.next_actions)
    assert not any("in_work_object" in action for action in error.next_actions)


def test_the_filesystem_refusal_names_the_step_not_com(monkeypatch, tmp_path):
    """`data.call` is a diagnostic contract: it must cover the steps BEFORE the
    COM call too, or the next report cannot tell them apart."""
    from catia_mcp import dialogs
    from catia_mcp.results import failure_code

    monkeypatch.setattr(dialogs.os, "makedirs",
                        lambda *a, **k: (_ for _ in ()).throw(
                            OSError(3, "系统找不到指定的路径")))
    doc = types.SimpleNamespace(Name="P.CATPart", SaveAs=lambda path: None)

    with pytest.raises(CodedError) as caught:
        dialogs.save_as(doc, str(tmp_path / "gone" / "x.CATPart"), tries=1, wait=0)

    assert caught.value.data["call"] == "os.makedirs"
    assert failure_code(str(caught.value), caught.value,
                        default="COM_ERROR") == "SAVE_FAILED"


# ── the staging route: "tried" is not "worked" ─────────────────────────


def _tools_double(monkeypatch):
    """A DocumentTools whose part is created and whose saves all fail."""
    from catia_mcp.tools import document as document_module

    doc = types.SimpleNamespace(Name="Part1.CATPart", FullName="", Close=lambda: None,
                                Product=types.SimpleNamespace(PartNumber=None))
    conn = types.SimpleNamespace(
        active_document=doc,
        ensure_connected=lambda: None,
        refresh_display=lambda: None,
        documents=types.SimpleNamespace(Add=lambda kind: doc, Count=0),
        part_of=lambda d: types.SimpleNamespace(Name="Part1"),
        last_sketch=None, last_part_name=None, active_body=None)

    tools = document_module.DocumentTools(connection=None)
    tools.conn = conn
    monkeypatch.setattr(tools, "_document_at_path", lambda path: None)
    monkeypatch.setattr(tools, "_existing_file_note", lambda path: None)
    return document_module, tools


def test_a_staging_save_that_failed_is_not_reported_as_saved_to_temp(monkeypatch):
    """The reported shape: both SaveAs calls fail, %TEMP% never gets a file.

    0.3.89's reply said "this document was saved to %TEMP% first and then moved to
    the path asked for, and THAT move was refused too - so this is not the 'first
    SaveAs' limitation". All three clauses were false, and the last one is a cause.
    """
    document_module, tools = _tools_double(monkeypatch)
    calls: list[str] = []

    def always_fails(doc, path, *args, **kwargs):
        calls.append(str(path))
        raise RuntimeError("(0, 'CATIAPartDocument', '方法 SaveAs 失败')")

    monkeypatch.setattr(document_module.dialogs, "save_as", always_fails)

    with pytest.raises(CodedError) as caught:
        tools._new_part(None, "E:/ascii/final.CATPart")

    error = caught.value
    text = str(error)
    assert len(calls) == 2                       # staging, then the path
    assert "WAS saved to %TEMP%" not in text
    assert "REFUSED at the %TEMP% step" in text
    assert "Cause undetermined" in text
    assert "not the 'first SaveAs' limitation" not in text

    route = error.data["staging_route"]
    assert route["tried"] is True
    assert route["temp_file_saved"] is False
    assert route["tried_first"] is True
    assert "方法 SaveAs 失败" in route["temp_error"]

    steps = [step["step"] for step in error.data["save_route"]]
    assert steps == ["enter", "staging_save", "staging", "save_as"]
    assert error.data["save_route"][1]["ok"] is False
    assert error.data["save_route"][2]["where"] == "temp file"


def test_a_refused_move_does_say_the_temp_save_happened(monkeypatch, tmp_path):
    """The one case where the old sentence was true - and now it can be told
    apart from the other one, with the move's own error."""
    document_module, tools = _tools_double(monkeypatch)

    def move_fails(doc, path, *args, **kwargs):
        if "mechdog" in str(path):               # the %TEMP% staging file
            pathlib.Path(path).write_bytes(b"staged")
            return str(path)
        raise RuntimeError("(0, 'CATIAPartDocument', '方法 SaveAs 失败')")

    monkeypatch.setattr(document_module.dialogs, "save_as", move_fails)

    with pytest.raises(CodedError) as caught:
        tools._new_part(None, str(tmp_path / "final.CATPart"))

    error = caught.value
    route = error.data["staging_route"]
    assert route["temp_file_saved"] is True
    assert route["temp_error"] is None
    assert "方法 SaveAs 失败" in route["move_error"]
    assert "WAS saved to %TEMP%" in str(error)
    assert "not the 'first SaveAs' limitation" in str(error)
    steps = [step["step"] for step in error.data["save_route"]]
    assert steps == ["enter", "staging_save", "staging_move", "staging", "save_as"]


def test_an_untried_staging_route_says_it_was_not_tried(monkeypatch, tmp_path):
    """A document that already has a path never enters it - and the prose must
    not invent a staging attempt (that was the 0.3.82 defect, in the other
    direction)."""
    document_module, tools = _tools_double(monkeypatch)
    monkeypatch.setattr(tools, "_document_has_path", lambda doc: True)
    monkeypatch.setattr(document_module.dialogs, "save_as",
                        lambda doc, path, *a, **k: (_ for _ in ()).throw(
                            RuntimeError("E_FAIL")))

    with pytest.raises(CodedError) as caught:
        tools._new_part(None, str(tmp_path / "final.CATPart"))

    text = str(caught.value)
    assert "before any staging attempt" in text
    assert caught.value.data["staging_route"]["tried"] is False


def test_a_non_ascii_name_is_not_blamed_on_the_folder(monkeypatch):
    """The staging route refuses a non-ASCII NAME before its first SaveAs, so no
    %TEMP% save happened either - and the sentence has to say that instead of
    claiming a move was refused."""
    document_module, tools = _tools_double(monkeypatch)
    monkeypatch.setattr(document_module.dialogs, "save_as",
                        lambda doc, path, *a, **k: (_ for _ in ()).throw(
                            RuntimeError("E_FAIL")))

    with pytest.raises(CodedError) as caught:
        tools._new_part(None, "E:/ascii/零件.CATPart")

    text = str(caught.value)
    assert "WAS saved to %TEMP%" not in text
    assert "the name is the problem, not the folder" in text


def test_a_coded_save_failure_carries_the_staging_route_too(monkeypatch, tmp_path):
    """The production path: `dialogs.save_as` raises SAVE_FAILED itself.

    That error is re-raised unchanged (it keeps its own code and advice), so
    before 0.3.91 it reached the caller WITHOUT any staging information - the
    sentence that explained the two steps only existed in the fake-doc path. Which
    is exactly why the wrong sentence survived: nobody read it in a real run.
    """
    document_module, tools = _tools_double(monkeypatch)

    def fails_with_a_code(doc, path, *args, **kwargs):
        raise CodedError(
            f"SaveAs failed after 1 attempts: {path} (this call was "
            f"doc.SaveAs('{path}'); the COM error was E_FAIL)",
            code="SAVE_FAILED", modified=False,
            data={"call": "Document.SaveAs", "target": str(path), "attempts": 1})

    monkeypatch.setattr(document_module.dialogs, "save_as", fails_with_a_code)

    with pytest.raises(CodedError) as caught:
        tools._save_document(str(tmp_path / "final.CATPart"))

    error = caught.value
    assert error.code == "SAVE_FAILED"                       # its own code, kept
    assert error.data["call"] == "Document.SaveAs"           # its own data, kept
    assert error.data["staging_route"]["temp_file_saved"] is False
    assert "REFUSED at the %TEMP% step" in str(error)
    # One staging sentence, appended to the COM failure - not a second copy of it.
    assert str(error).count("An ASCII staging file was tried") == 1
    assert str(error).startswith("SaveAs failed after 1 attempts")


# ── the guard: no test may depend on the author's drive letters ────────
_DRIVE_LITERAL = re.compile(r"^[A-Za-z]:[\\/]")


def test_no_test_saves_to_a_hardcoded_drive():
    """A save test that names `F:/...` only passes on a machine with F:.

    The 2026-11-09 report ran the suite where the drive does not exist: the real
    `os.makedirs` raised, the fake SaveAs never ran, and 1445/1446 passed. Any
    path passed to a `*save_as(...)` call in tests/ must therefore come from
    tmp_path (or something else that exists), never from a drive letter.
    """
    offenders: list[str] = []
    root = pathlib.Path(__file__).parent
    for path in sorted(root.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (func.attr if isinstance(func, ast.Attribute) else
                    func.id if isinstance(func, ast.Name) else "")
            if name not in {"save_as", "save"}:
                continue
            for argument in list(node.args) + [kw.value for kw in node.keywords]:
                if (isinstance(argument, ast.Constant)
                        and isinstance(argument.value, str)
                        and _DRIVE_LITERAL.match(argument.value)):
                    offenders.append(
                        f"{path.name}:{argument.lineno}: {argument.value}")
    assert offenders == [], (
        "tests must not save to a hardcoded drive:\n  " + "\n  ".join(offenders))
