"""Close CATIA modal dialogs programmatically, and retry saves around them.

Why this exists
---------------
CATIA raises **modal** dialogs on whichever thread called the COM method. The
serial STA worker that runs every tool call is that thread, so an unanswered
dialog freezes the worker: no tool call can complete, not even a later
``catia_disconnect``. Upstream documents this as a runtime *limit* ("close the
blocking dialog yourself"). This module closes it instead.

Two cases matter in practice:

* ``SaveAs`` on a path that already exists pops "要覆盖它吗？ / Do you want to
  replace it?" — and CATIA reports the failure as ``方法 SaveAs 失败`` even
  though the only real problem was the unanswered prompt.
* The **first** ``SaveAs`` of a freshly created document is unreliable and can
  raise a dialog with no error text at all. Retrying after dismissing works.

Scope is deliberately narrow: only dialogs belonging to the CATIA process are
touched, matched by the process id of the main ``CATDlgDocument`` window. A
dialog that belongs to another application is never clicked.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import tempfile
import threading
import time
from ctypes import wintypes as _wt

from catia_mcp.results import CodedError, PathNotSupported

YES_WORDS = ("是", "Yes", "确定", "OK", "&Yes", "&是(Y)", "是(Y)")
NO_WORDS = ("否", "No", "&No", "否(N)", "&否(N)")

_BM_CLICK = 0x00F5
_WM_COMMAND = 0x0111
_IDOK = 1
#: CATIA message boxes are plain Win32 dialogs; its own prompts use CATDlgDialog.
_DIALOG_CLASSES = ("#32770", "CATDlgDialog")
_MAIN_CLASS_MARK = "CATDlgDocument"

_ENABLED = os.name == "nt"


def _u32():
    return ctypes.windll.user32


def _enum_proc():
    return ctypes.WINFUNCTYPE(_wt.BOOL, _wt.HWND, _wt.LPARAM)


def _visible_windows():
    """Yield (hwnd, class_name, title, pid) for every visible top-level window."""
    u32 = _u32()
    out: list[tuple[int, str, str, int]] = []

    def cb(hwnd, _lparam):
        if u32.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(128)
            u32.GetClassNameW(hwnd, cls, 128)
            title = ctypes.create_unicode_buffer(256)
            u32.GetWindowTextW(hwnd, title, 256)
            pid = _wt.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            out.append((hwnd, cls.value, title.value, pid.value))
        return True

    u32.EnumWindows(_enum_proc()(cb), 0)
    return out


def catia_pid() -> int | None:
    """Process id of the running CATIA session, found via its main frame class."""
    for _hwnd, cls, _title, pid in _visible_windows():
        if _MAIN_CLASS_MARK in cls:
            return pid
    return None


def dismiss(rounds: int = 6, wait: float = 0.4, prefer: tuple[str, ...] | None = None,
            pid: int | None = None, classes: tuple[str, ...] | None = None) -> int:
    """Click through CATIA dialogs and return how many buttons were pressed.

    ``prefer`` is a tuple of button-label substrings; the first dialog button
    whose label contains one of them is clicked. When no label matches, the
    dialog's default button (IDOK) is used. ``pid`` restricts the sweep to one
    process (default: auto-detected CATIA). ``classes`` restricts which window
    classes are treated as dialogs.
    """
    if not _ENABLED:
        return 0
    u32 = _u32()
    keys = prefer or YES_WORDS
    wanted = classes or _DIALOG_CLASSES
    target_pid = catia_pid() if pid is None else pid
    clicks = 0

    for _ in range(max(1, rounds)):
        found = [
            (hwnd, cls, title, wpid)
            for hwnd, cls, title, wpid in _visible_windows()
            if cls in wanted and (target_pid is None or wpid == target_pid)
        ]
        if not found:
            if wait:
                time.sleep(wait)
            continue
        for hwnd, _cls, _title, _wpid in found:
            hit = False

            def child_cb(child, _lparam, _hwnd=hwnd):
                nonlocal hit
                cls = ctypes.create_unicode_buffer(64)
                u32.GetClassNameW(child, cls, 64)
                if cls.value != "Button":
                    return True
                label = ctypes.create_unicode_buffer(128)
                u32.GetWindowTextW(child, label, 128)
                if any(key in label.value for key in keys):
                    u32.SendMessageW(child, _BM_CLICK, 0, 0)
                    hit = True
                return True

            u32.EnumChildWindows(hwnd, _enum_proc()(child_cb), 0)
            if not hit:
                u32.SendMessageW(hwnd, _WM_COMMAND, _IDOK, 0)
                hit = True
            if hit:
                clicks += 1
            time.sleep(0.3)
        time.sleep(0.5)
    return clicks


def _normalize(path: str) -> str:
    """CATIA rejects forward slashes in SaveAs/Open/ExportData with E_FAIL."""
    return os.path.normpath(os.path.abspath(path))


class Answered:
    """How many message boxes a :func:`watch` block answered, and that is all.

    Only the COUNT is kept because it is the only thing this side can observe
    honestly. WHAT THE BOX SAID IS NOT AVAILABLE: its text belongs to a Win32
    dialog in another process, and reading it would be a guess dressed up as a
    measurement. So a caller may say "one box was answered here" and must not say
    which warning it was.
    """

    __slots__ = ("clicks",)

    def __init__(self) -> None:
        self.clicks = 0


@contextlib.contextmanager
def watch(interval: float = 0.25, prefer: tuple[str, ...] | None = None):
    """Dismiss message boxes *while* a blocking COM call is in flight.

    Dismissing after the fact is not always enough: a modal dialog can keep the
    COM call from returning at all, and the worker thread that would click it is
    the thread that is blocked. A separate thread can still send the button
    message, so this context manager polls for CATIA message boxes and answers
    them for the duration of the ``with`` block.

    Yields an :class:`Answered` so the caller can report that a box appeared. An
    unanswered box freezes the whole session, so "the call worked but CATIA asked
    something first" is a fact worth passing on - and it is the only fact here.

    Deliberately narrow: only ``#32770`` message boxes are answered. CATIA's own
    ``CATDlgDialog`` windows are left alone, because those include panels a human
    may have opened deliberately in the running session.
    """
    answered = Answered()
    if not _ENABLED:
        yield answered
        return

    stop = threading.Event()
    target_pid = catia_pid()

    def loop() -> None:
        while not stop.is_set():
            try:
                answered.clicks += dismiss(rounds=1, wait=0, prefer=prefer,
                                           pid=target_pid, classes=("#32770",))
            except Exception:
                pass
            stop.wait(interval)

    worker = threading.Thread(target=loop, name="catia-dialog-watch", daemon=True)
    worker.start()
    try:
        yield answered
    finally:
        stop.set()
        worker.join(timeout=2.0)


def save_as(doc, path: str, tries: int = 6, wait: float = 0.6) -> str:
    """``doc.SaveAs(path)`` retried around modal dialogs; returns the real path.

    An existing target is removed first: overwriting would only earn an extra
    confirmation prompt, and the caller asked for this path anyway.
    """
    target = _normalize(path)
    # Reject a non-ASCII FILE NAME before CATIA sees it. Measured on B30: the
    # same folder, same length, 'ascii_probe.CATPart' saves and '中文探针.CATPart'
    # fails with DISP_E_EXCEPTION on all six attempts, each one popping a modal
    # "上述文件名无效" dialog. A non-ASCII FOLDER is fine, so only the basename is
    # checked. Without this the caller waits ~4 s, answers dialogs, and is told
    # "could not be saved" - which reads as a lock, and a retry cannot help
    # (reported 2026-10-31).
    name = os.path.basename(target)
    if not name.isascii():
        # The message was ~700 characters until 0.3.127, carrying the probe dates,
        # the Save As screenshot evidence and the staging explanation. That is the
        # right material for PathNotSupported's docstring, where it still is, and
        # the wrong material for a reply an agent has to read on every failure
        # (reported 2026-11-14, P1-5: "message 约 1000+ 字符, 对 Agent 上下文是显著
        # 负担"). What stays is what a caller has to ACT on.
        raise PathNotSupported(
            f"CATIA cannot SaveAs a non-ASCII file name on this build: '{name}'. "
            "Use an ASCII name and put the wording in the part number - "
            "catia_new_part(file_path=..., ascii_fallback=true) does both in one "
            "call. A non-ASCII FOLDER is fine (measured on B30), and renaming an "
            "ASCII save afterwards is NOT a way out: this build cannot OPEN such a "
            "name either."
        )
    parent = os.path.dirname(target)
    try:
        if parent:
            os.makedirs(parent, exist_ok=True)
    except OSError as error:
        # The filesystem can refuse before CATIA is ever asked. This used to sit
        # outside every try, so a missing drive or a read-only folder escaped as
        # a bare PermissionError and the envelope called it COM_ERROR - with the
        # "CATIA reports most automation failures as a bare method failed"
        # advice, which has nothing to do with a folder that cannot be created
        # (reported 2026-11-09: os.makedirs('F:\\ascii') on a machine with no F:).
        # data.call is a diagnostic contract now, so it has to cover the steps
        # before the COM call as well as the call itself.
        raise CodedError(
            f"Could not create the folder for '{target}' - CATIA was NOT asked "
            f"to save anything yet (this call was os.makedirs({parent!r}); the "
            f"filesystem said: {error}).\n"
            "  Nothing was written and the document keeps its previous state, so "
            "retrying the same path cannot help.",
            code="SAVE_FAILED",
            stage="execute",
            modified=False,
            data={"call": "os.makedirs", "target": str(target),
                  "folder": str(parent), "error": str(error)},
            next_actions=[
                "Check that the drive exists and the folder is writable: a path "
                "on an unmounted drive fails like this on every attempt, and no "
                "retry of catia_save_document can create it",
                f"Write to a folder that exists instead - for example "
                f"'{os.path.join(tempfile.gettempdir(), os.path.basename(target))}' "
                "- and note the document is still open and unsaved",
            ],
        )
    for _ in range(3):
        if not os.path.exists(target):
            break
        try:
            os.remove(target)
        except OSError:
            dismiss(rounds=2, wait=0.3)
            time.sleep(0.5)

    last: Exception | None = None
    for attempt in range(tries):
        try:
            with watch():
                doc.SaveAs(target)
            return target
        except Exception as error:  # noqa: BLE001 - COM errors are opaque
            last = error
            clicks = dismiss(rounds=2, wait=0.3)
            # A retry without a dismissed dialog is just a busy loop.
            time.sleep(wait if clicks else min(0.2 * (attempt + 1), 1.5))
    raise CodedError(
        # Same reason as dialogs.save: the call is named in the text. A SaveAs
        # can fail where a Save succeeds and the other way round, so which one
        # ran is the first thing a report has to establish.
        f"SaveAs failed after {tries} attempts: {target} (this call was "
        f"doc.SaveAs('{target}'); the COM error was {last})",
        code="SAVE_FAILED",
        stage="execute",
        modified=False,
        data={"call": "Document.SaveAs", "target": str(target),
              "attempts": int(tries)},
        next_actions=[
            "A non-ASCII file NAME never reaches CATIA: it is refused above as "
            "PATH_NOT_ASCII before the first attempt, so this failure is "
            "something else - a locked or read-only target, or a dialog nobody "
            "answered. Save to a different path, or close whatever holds this "
            "file, and check whether a partial file was left",
            "Otherwise the file is probably locked or read-only: the document is "
            "still open and unsaved, and whether a partial file exists was not "
            "verified",
        ],
    )


def save(doc, tries: int = 3) -> str:
    """``doc.Save()`` retried around dialogs; returns the document name."""
    name = doc.Name
    last: Exception | None = None
    for _ in range(tries):
        try:
            with watch():
                doc.Save()
            return name
        except Exception as error:  # noqa: BLE001
            last = error
            dismiss(rounds=2, wait=0.3)
            time.sleep(0.5)
    # The three sentences this used to carry ("This document already has a
    # file, so the path is not the problem") were written for the one caller
    # that always has a file and then printed for every caller - including a
    # document that had never been saved at all. A report quoted them back next
    # to "session state: document=<none> | no part document": every suggestion
    # pointed at a condition that did not exist (2026-11-09). Conditions, not
    # assertions: this function cannot see which case it is in.
    raise CodedError(
        # WHICH CALL THIS WAS is in the first line, because a report spent a
        # round arguing that "the first save is a SaveAs, not a Save" contradicted
        # an error that said 「方法 Save 失败」 (2026-11-09). Both statements were
        # this server's, about two different calls: a bare Save and a SaveAs are
        # separate members of the document, and CATIA names them separately in
        # the error it raises. Naming the call here means the text settles it
        # without reading the source.
        f"Save failed after {tries} attempts: {name} (this call was a bare "
        f"doc.Save(), which cannot give a never-saved document a path; the "
        f"COM error was {last})",
        code="SAVE_FAILED",
        stage="execute",
        modified=False,
        data={"call": "Document.Save", "document": str(name),
              "attempts": int(tries)},
        next_actions=[
            "IF this document already had a file: that file is most likely "
            "read-only or held by another program. Free it and retry, or write a "
            "copy elsewhere (catia_save_as_copy)",
            "IF it has never been saved: a bare Save cannot give it a path, so "
            "NO retry of this call can work. The order that does work - measured "
            "on both machines - is: catia_new_part(file_path='<an ASCII "
            "folder>/name.CATPart') to create it with a path, build, then "
            "catia_save_document(file_path='<the non-ASCII folder>/name.CATPart') "
            "to move it there",
            "IF its file NAME is non-ASCII: this build cannot write it at all, "
            "and it cannot open such a name either - write a copy under an ASCII "
            "name and keep that one",
            "Nothing was written, so the document still holds everything it held "
            "before this call",
        ],
    )
