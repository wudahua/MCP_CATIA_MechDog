"""Run the COM calls that can block a whole session in a separate process.

Why this exists
---------------
CATIA raises **modal** dialogs on whichever thread called the COM method, and the
serial STA worker that runs every tool call is that thread. An unanswered dialog
therefore freezes everything: no later tool call can complete, not even
``catia_disconnect``. One reporter's ``catia_add_component`` with a forward-slash
path sat there for **2 h 14 min** (2026-10-24) - the dialog was CATIA's own file
prompt, and the thread that would have answered it was the blocked one.

``dialogs.watch`` already answers ``#32770`` message boxes from a side thread
while a call is in flight, and that is enough for ``SaveAs``. It is deliberately
narrow: CATIA's ``CATDlgDialog`` panels are left alone, because a human working
alongside the agent may have opened one on purpose. Widening it would mean
clicking buttons in windows nobody asked us to touch.

So the fix is not a bigger watchdog, it is **another process**. CATIA is an
out-of-process COM server: a second client can attach to the same running
instance, make one call, and be killed if it does not come back. Killing the
client cannot dismiss a dialog, but it does release the server, and the dialog
can then be answered with the narrow, target-specific sweep in
:func:`cleanup` - which passes ``CATDlgDialog`` in explicitly, only for this
recovery path.

What is isolated: :data:`OPS` - calls that create or open documents and are known
to raise file dialogs. Everything else stays in-process, because a subprocess
per tool call would cost a COM attach (seconds) on every measurement.

Configuration
-------------
``CATIA_MCP_GUARD=0``            disable and run everything in-process again
``CATIA_MCP_GUARD_TIMEOUT=60``   seconds before the child is killed
``CATIA_MCP_GUARD_CMD``          override the child command (tests use this)
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

#: Operations the child process knows how to run. Kept small on purpose: every
#: entry is a call whose failure mode is "a dialog nobody can answer".
OPS = ("add_components", "ping")

DEFAULT_TIMEOUT = 60.0

_CREATE_NO_WINDOW = 0x08000000


def enabled() -> bool:
    value = os.environ.get("CATIA_MCP_GUARD", "1").strip().lower()
    return value not in ("0", "false", "no", "off")


def timeout() -> float:
    try:
        return float(os.environ.get("CATIA_MCP_GUARD_TIMEOUT", DEFAULT_TIMEOUT))
    except ValueError:
        return DEFAULT_TIMEOUT


@dataclass
class GuardResult:
    """What the child reported, or why there is no report."""

    ok: bool
    code: str
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0

    @property
    def timed_out(self) -> bool:
        return self.code == "GUARD_TIMEOUT"

    @property
    def unavailable(self) -> bool:
        """The child could not be started or could not attach to CATIA."""
        return self.code in ("GUARD_UNAVAILABLE", "GUARD_NO_CATIA")


def _package_root() -> str:
    """Directory that has ``catia_mcp`` in it, so the child imports THIS code."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _command() -> list[str]:
    override = os.environ.get("CATIA_MCP_GUARD_CMD", "").strip()
    if override:
        # posix=False keeps Windows backslashes; it also keeps the surrounding
        # quotes, so strip them per token (a quoted "C:\Program Files\...\python.exe"
        # otherwise arrives as an executable name that does not exist -> WinError 5).
        tokens = shlex.split(override, posix=False)
        return [t[1:-1] if len(t) > 1 and t[0] == t[-1] == '"' else t for t in tokens]
    return [sys.executable, "-m", "catia_mcp._guard_child"]


def run(op: str, payload: dict[str, Any], seconds: float | None = None) -> GuardResult:
    """Run ``op`` in a child process; kill it if it outstays ``seconds``.

    Never raises for a CATIA problem: the caller decides what a timeout means.
    """
    if op not in OPS:
        raise ValueError(f"Unknown guarded operation: {op}")
    limit = timeout() if seconds is None else seconds
    folder = tempfile.mkdtemp(prefix="catia_guard_")
    args_path = os.path.join(folder, "args.json")
    result_path = os.path.join(folder, "result.json")
    with open(args_path, "w", encoding="utf-8") as handle:
        json.dump({"op": op, "payload": payload}, handle)

    env = dict(os.environ)
    # The child must import the same package the server is running, not whatever
    # happens to be on sys.path in its own working directory.
    env["PYTHONPATH"] = os.pathsep.join(
        [_package_root()] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["PYTHONIOENCODING"] = "utf-8"

    started = time.monotonic()
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            _command() + [args_path, result_path],
            cwd=_package_root(),
            env=env,
            # Critical for stdio transports: the child must not touch the
            # server's stdin/stdout, or it would eat or corrupt JSON-RPC frames.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except Exception as exc:  # noqa: BLE001 - the caller falls back in-process
        return GuardResult(False, "GUARD_UNAVAILABLE",
                           f"could not start the isolated worker ({exc})")

    try:
        process.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - kill is enough
            pass
        return GuardResult(
            False, "GUARD_TIMEOUT",
            f"the isolated call did not return within {limit:.0f} s and was killed",
            {"seconds": round(time.monotonic() - started, 1)},
            time.monotonic() - started,
        )

    elapsed = time.monotonic() - started
    try:
        with open(result_path, encoding="utf-8") as handle:
            body = json.load(handle)
    except Exception:  # noqa: BLE001 - the child died before writing anything
        return GuardResult(
            False, "GUARD_UNAVAILABLE",
            f"the isolated worker exited with code {process.returncode} and wrote "
            "no result (it may not be able to attach to CATIA from a second "
            "process)", {}, elapsed)
    return GuardResult(bool(body.get("ok")), str(body.get("code", "GUARD_ERROR")),
                       str(body.get("message", "")), body.get("data") or {}, elapsed)


def cleanup(seconds: float = 4.0) -> int:
    """Answer the dialog a killed child may have left behind.

    This is the one place that clicks CATIA's own ``CATDlgDialog`` windows, and it
    is only reached after a guarded call was abandoned - so the alternative is a
    session with a dialog nobody can reach. ``NO`` first: a file prompt left open
    by a half-finished insert should be cancelled, not confirmed.
    """
    from catia_mcp import dialogs

    prefer = dialogs.NO_WORDS + dialogs.YES_WORDS
    rounds = max(1, int(seconds / 0.7))
    return dialogs.dismiss(rounds=rounds, wait=0.3, prefer=prefer,
                           classes=("#32770", "CATDlgDialog"))
