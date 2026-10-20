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
import threading
import time
from ctypes import wintypes as _wt

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


@contextlib.contextmanager
def watch(interval: float = 0.25, prefer: tuple[str, ...] | None = None):
    """Dismiss message boxes *while* a blocking COM call is in flight.

    Dismissing after the fact is not always enough: a modal dialog can keep the
    COM call from returning at all, and the worker thread that would click it is
    the thread that is blocked. A separate thread can still send the button
    message, so this context manager polls for CATIA message boxes and answers
    them for the duration of the ``with`` block.

    Deliberately narrow: only ``#32770`` message boxes are answered. CATIA's own
    ``CATDlgDialog`` windows are left alone, because those include panels a human
    may have opened deliberately in the running session.
    """
    if not _ENABLED:
        yield
        return

    stop = threading.Event()
    target_pid = catia_pid()

    def loop() -> None:
        while not stop.is_set():
            try:
                dismiss(rounds=1, wait=0, prefer=prefer, pid=target_pid, classes=("#32770",))
            except Exception:
                pass
            stop.wait(interval)

    worker = threading.Thread(target=loop, name="catia-dialog-watch", daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join(timeout=2.0)


def save_as(doc, path: str, tries: int = 6, wait: float = 0.6) -> str:
    """``doc.SaveAs(path)`` retried around modal dialogs; returns the real path.

    An existing target is removed first: overwriting would only earn an extra
    confirmation prompt, and the caller asked for this path anyway.
    """
    target = _normalize(path)
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
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
    raise RuntimeError(f"SaveAs failed after {tries} attempts: {target} ({last})")


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
    raise RuntimeError(f"Save failed after {tries} attempts: {name} ({last})")
