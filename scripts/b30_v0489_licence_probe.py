"""Which licences does THIS machine actually hold, and is ASD one of them?

The four mate tools refuse on this surface because the constraint collection
answers E_FAIL for every type code (0.3.152 measured 14 of 14) while the same
call on a sketch succeeds. That leaves two candidates: the licence, or the
configuration. This reads the licence list from CATIA itself - not from a file
on disk, which can be stale - so the answer is the session's own view.

Run: ./.venv/Scripts/python.exe -u scripts/b30_v0489_licence_probe.py
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from catia_mcp.connection import CATIAConnection  # noqa: E402


def say(text: str = "") -> None:
    print(text, flush=True)


def call(owner, name: str, *args):
    """True/False for 'can this be called', plus what it said."""
    try:
        member = getattr(owner, name)
    except Exception as exc:  # noqa: BLE001 - the absence is the reading
        return f"<getattr raised {type(exc).__name__}: {str(exc).splitlines()[0][:60]}>"
    if not callable(member):
        return f"<property: {member!r}>"
    try:
        return f"-> {member(*args)!r}"
    except Exception as exc:  # noqa: BLE001
        return f"<raised {type(exc).__name__}: {str(exc).splitlines()[0][:90]}>"


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    app = conn.app

    # Application.Version does not exist on this type (measured 2026-11-20) -
    # the version lives on SystemConfiguration, so read it from there and treat
    # every other member as unknown until it answers.
    say(f"Application: {app!r}")
    say("\n── Application members worth trying ──")
    for name in ("Version", "SystemConfiguration", "SystemService", "Product",
                 "ActiveDocument", "GetWorkbenchId"):
        say(f"  {name:20} {call(app, name)}")

    try:
        sc = app.SystemConfiguration
    except Exception as exc:  # noqa: BLE001
        say(f"\nSystemConfiguration unreadable: {exc} - licence route closed.")
        return 1

    say(f"\n── SystemConfiguration: {sc!r} ──")
    say(f"  {'Version':16} {call(sc, 'Version')}")
    say("\n── SystemConfiguration members that could answer this ──")
    for name in ("Licenses", "License", "Products", "Product", "Environment",
                 "Licence", "IsInSession", "Name", "Version"):
        say(f"  {name:16} {call(sc, name)}")

    say("\n── the licence list, one line per product ──")
    licences = None
    for name in ("Licenses", "License", "Licence"):
        try:
            candidate = getattr(sc, name)
        except Exception:  # noqa: BLE001
            continue
        if candidate is not None and not callable(candidate):
            licences = candidate
            say(f"  using SystemConfiguration.{name}")
            break
    if licences is None:
        say("  NONE of Licenses/License/Licence is a readable collection - the "
            "licence route is closed on this build, so the verdict has to come "
            "from behaviour (catia_probe_assembly_constraints).")
        return 1

    try:
        count = int(licences.Count)
    except Exception as exc:  # noqa: BLE001
        say(f"  Count unreadable: {exc}")
        return 1
    say(f"  {count} entries")

    rows = []
    for index in range(1, count + 1):
        try:
            item = licences.Item(index)
        except Exception as exc:  # noqa: BLE001
            say(f"  [{index}] unreadable: {str(exc).splitlines()[0][:60]}")
            continue
        product = available = None
        for attribute in ("Product", "Name", "LicenceName", "LicenseName"):
            try:
                product = getattr(item, attribute)
                break
            except Exception:  # noqa: BLE001
                continue
        for attribute in ("Available", "IsAvailable", "Granted", "Status"):
            try:
                available = getattr(item, attribute)
                break
            except Exception:  # noqa: BLE001
                continue
        rows.append((str(product), available))

    for product, available in sorted(rows):
        flag = "YES" if available is True or available == 1 else (
            "no " if available is False or available == 0 else "?  ")
        say(f"  [{flag}] {product}")

    say("\n── the question this was run for ──")
    wanted = ("ASD", "Assembly Design", "ASSEMBLY", "ASM")
    hits = [row for row in rows
            if any(token.lower() in row[0].lower() for token in wanted)]
    if not hits:
        say("  no entry mentions ASD / Assembly Design at all")
    for product, available in hits:
        say(f"  {product}: available={available}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
