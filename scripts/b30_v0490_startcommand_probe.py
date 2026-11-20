"""Does ``StartCommand`` know ANY command on this machine?

0.3.152 concluded "the Fix UI command is not registered" from
``StartCommand("Fix")`` answering "unknown command". That inference needs a
control: an unknown name and a known name must behave differently, or the answer
"unknown command" says nothing about the command - only about the string.

This probes a spread of names whose existence is not in doubt (the resource file
``CATAssembly.CATRsc`` names ``CATAssembly.CreateFix`` and
``CATAssembly.CreateFixT`` explicitly), next to ``Update`` - the one string
0.3.152 reported as accepted - and a deliberate nonsense string as the negative
control.

Run: ./.venv/Scripts/python.exe -u scripts/b30_v0490_startcommand_probe.py
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, ".")

from catia_mcp import dialogs  # noqa: E402
from catia_mcp.connection import CATIAConnection  # noqa: E402

#: name -> what it should be, if StartCommand resolves the way the docs say
NAMES = [
    ("Update", "a known global command (0.3.152 reported it accepted)"),
    ("Fit All In", "the doc's own example of a readable command name"),
    ("Hide/Show", "another readable command name"),
    ("CreateFix", "CATAssembly.CreateFix without its module prefix"),
    ("CATAssembly.CreateFix", "the exact key in CATAssembly.CATRsc"),
    ("CATAssembly.CreateFixT", "Fix Together, also in CATAssembly.CATRsc"),
    ("CATAssembly.Move", "a sibling assembly command, plausible prefix"),
    ("Fix", "the string 0.3.152 used"),
    ("ZzzNoSuchCommand", "NEGATIVE CONTROL - nothing can know this"),
]


def say(text: str = "") -> None:
    print(text, flush=True)


def workbench(app) -> str:
    try:
        return str(app.GetWorkbenchId())
    except Exception as exc:  # noqa: BLE001
        return f"<unreadable: {type(exc).__name__}>"


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    app = conn.app

    try:
        doc = app.ActiveDocument.Name
    except Exception:  # noqa: BLE001
        doc = "<none>"
    say(f"active document: {doc}")
    say(f"workbench:       {workbench(app)}")
    say("")

    rows = []
    for name, why in NAMES:
        before = workbench(app)
        started = time.perf_counter()
        with dialogs.watch() as answered:
            try:
                returned = app.StartCommand(name)
                verdict = f"accepted (returned {returned!r})"
            except Exception as exc:  # noqa: BLE001 - the refusal is the reading
                verdict = f"raised {type(exc).__name__}: {' '.join(str(exc).split())[:70]}"
            time.sleep(0.5)
        took = (time.perf_counter() - started) * 1000
        after = workbench(app)
        rows.append((name, why, verdict, answered.clicks, took, before, after))
        say(f"  {name:26} {verdict}")
        say(f"  {'':26} boxes answered={answered.clicks} "
            f"{took:6.0f} ms  workbench {before} -> {after}   [{why}]")

    say("\n── what this says ──")
    accepted = [r for r in rows if r[2].startswith("accepted")]
    refused = [r for r in rows if "raised" in r[2]]
    say(f"  accepted: {[r[0] for r in accepted] or 'none'}")
    say(f"  refused:  {[r[0] for r in refused] or 'none'}")

    control = next((r for r in rows if r[0] == "ZzzNoSuchCommand"), None)
    update = next((r for r in rows if r[0] == "Update"), None)
    if control and update:
        same = (control[2].startswith("accepted")) == (update[2].startswith("accepted"))
        if same:
            say("  the negative control behaves EXACTLY like 'Update', so StartCommand's "
                "answer to a name carries NO information about whether that command "
                "exists. 0.3.152's 'unknown command' evidence is void and has to be "
                "withdrawn.")
        else:
            say("  the negative control is distinguishable from 'Update', so a name "
                "that is refused really is unknown - the Fix string was simply not "
                "the command's name.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
