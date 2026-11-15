"""B30 probe: is there ANY sheet-metal route in this automation, or only a note?

The 2026-11-03 report (0.3.48) asked for eight sheet-metal tools. What this
project recorded instead is a ceiling saying `Part.SheetMetalFactory` has 23
members of which one is real, and that its parameter object adds only
`GetThickness`. That was written from a type-library reading — the same class of
evidence that produced the WRONG "a topological edge cannot be sampled" note
corrected in 0.3.131. So measure it again, and this time ask the two questions
the note did not:

  A  every member name of SheetMetalFactory, not just the AddNew* subset
  B  every member name of the parameter object, then WRITE each thickness-like
     property and READ IT BACK (a read-only property and a working one look the
     same in a member list)
  C  whether the Part has a SheetMetalParameters property of its own
  D  whether any sheet-metal workbench is even listed, and whether the app
     exposes StartCommand (the only non-API route to a UI command)

    .venv/Scripts/python.exe -u scripts/b30_v0434_probe_sheetmetal.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "catia_probe_v0434_smd")
CELLS: list[tuple[str, bool, str]] = []


def say(text: str = "") -> None:
    print(text, flush=True)


def cell(name: str, ok: bool, detail: str = "") -> None:
    CELLS.append((name, ok, detail))
    say(f"  [{'OK ' if ok else 'NO '}] {name}" + (f"  <- {detail}" if detail else ""))


def all_members(obj: Any) -> list[str] | None:
    """Every function name in the object's type library (not just AddNew*)."""
    try:
        info = obj._oleobj_.GetTypeInfo()
        count = info.GetTypeAttr().cFuncs
    except Exception:  # noqa: BLE001 - reported as unavailable
        return None
    names: list[str] = []
    for index in range(count):
        try:
            names.append(info.GetNames(info.GetFuncDesc(index).memid)[0])
        except Exception:  # noqa: BLE001 - undocumented entries
            continue
    return names


def try_set(obj: Any, names: tuple[str, ...], value: Any) -> str:
    """Write through each candidate name and read back; never assume it stuck."""
    for name in names:
        try:
            before = getattr(obj, name)
        except Exception:  # noqa: BLE001 - not a readable property
            before = "<unreadable>"
        try:
            setattr(obj, name, value)
        except Exception as error:  # noqa: BLE001 - read-only is an answer
            continue
        try:
            after = getattr(obj, name)
        except Exception:  # noqa: BLE001
            after = "<unreadable>"
        return f"{name}: {before!r} -> {after!r} (wrote {value!r})"
    return ""


def close_ours(conn: Any) -> None:
    root = os.path.abspath(ROOT).lower()
    for index in range(int(conn.documents.Count), 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = str(doc.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).lower().startswith(root):
            try:
                doc.Close()
            except Exception:  # noqa: BLE001
                pass


def fresh(conn: Any, docs: Any, name: str) -> Any:
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, name + ".CATPart")
    close_ours(conn)
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    return conn.get_active_part()


def main() -> int:
    conn = CATIAConnection()
    conn.ensure_connected()
    docs = DocumentTools(conn)

    say("=" * 72)
    say("A - SheetMetalFactory: the WHOLE member table, not just AddNew*")
    part = fresh(conn, docs, "SMD_Factory")
    try:
        factory = part.SheetMetalFactory
        cell("A1 part.SheetMetalFactory exists", True, type(factory).__name__)
    except Exception as error:  # noqa: BLE001
        cell("A1 part.SheetMetalFactory exists", False,
             str(error).splitlines()[0][:120])
        factory = None
    if factory is not None:
        members = all_members(factory)
        if members is None:
            cell("A2 type library readable", False, "GetTypeInfo failed")
        else:
            cell("A2 type library readable", True, f"{len(members)} members")
            say("    " + ", ".join(members))

    say("")
    say("B - the parameter object: can a thickness / radius / K factor be WRITTEN?")
    params = None
    if factory is not None:
        try:
            params = factory.CreateSheetMetalParameters()
            names = all_members(params)
            cell("B1 CreateSheetMetalParameters() returns an object", True,
                 f"{len(names) if names else '?'} members: "
                 + (", ".join(names) if names else "type library unavailable"))
        except Exception as error:  # noqa: BLE001
            cell("B1 CreateSheetMetalParameters() returns an object", False,
                 str(error).splitlines()[0][:120])
    if params is not None:
        for label, candidates, value in (
            ("B2 thickness", ("Thickness", "SheetThickness"), 2.0),
            ("B3 K factor", ("KFactor", "Kfactor", "K_Factor"), 0.5),
            ("B4 bend radius", ("BendRadius", "DefaultBendRadius",
                                "BendRadiusValue"), 1.905),
            ("B5 relief type", ("ReliefType", "BendReliefType"), 1),
        ):
            wrote = try_set(params, candidates, value)
            cell(label, bool(wrote),
                 wrote or "no writable member among " + ", ".join(candidates))
        # a Get* method is the other way V5 spells a read
        for method, argument in (("SetThickness", 2.0), ("SetKFactor", 0.5),
                                 ("SetBendRadius", 1.905)):
            try:
                getattr(params, method)(argument)
                cell(f"B6 {method}({argument})", True, "accepted")
            except Exception as error:  # noqa: BLE001
                cell(f"B6 {method}({argument})", False,
                     str(error).splitlines()[0][:100])

    say("")
    say("C - part.SheetMetalParameters: a DIFFERENT object from the factory's")
    own = None
    try:
        own = part.SheetMetalParameters
        cell("C1 part.SheetMetalParameters exists", True, repr(own)[:50])
    except Exception as error:  # noqa: BLE001
        cell("C1 part.SheetMetalParameters exists", False,
             str(error).splitlines()[0][:100])
    if own is not None:
        names = all_members(own)
        cell("C2 its type library is readable", names is not None,
             f"{len(names)} members: " + ", ".join(names) if names else "unavailable")
        for getter in ("GetThickness", "Thickness", "GetKFactor", "KFactor",
                       "GetBendRadius", "BendRadius", "GetBendTable",
                       "GetReliefType"):
            try:
                value = getattr(own, getter)
            except Exception as error:  # noqa: BLE001
                say(f"    {getter}: {str(error).splitlines()[0][:70]}")
                continue
            if callable(value):
                try:
                    say(f"    {getter}() -> {value()!r}"[:110])
                except Exception as error:  # noqa: BLE001
                    say(f"    {getter}() failed: {str(error).splitlines()[0][:70]}")
            else:
                say(f"    {getter} = {value!r}"[:110])
        for label, candidates, value in (
            ("C3 thickness writable", ("Thickness",), 2.0),
            ("C4 K factor writable", ("KFactor", "Kfactor"), 0.5),
            ("C5 bend radius writable", ("BendRadius",), 1.905),
        ):
            wrote = try_set(own, candidates, value)
            cell(label, bool(wrote), wrote or "not writable")
    for name in ("SheetMetal", "IsSheetMetal"):
        try:
            value = getattr(part, name)
            say(f"    part.{name} = {value!r}"[:110])
        except Exception as error:  # noqa: BLE001
            say(f"    part.{name}: {str(error).splitlines()[0][:70]}")

    say("")
    say("D - is the workbench listed, and is there a UI-command route at all?")
    try:
        shelves = conn.app.GetWorkbenchNames()
        metal = [n for n in shelves if "sheet" in n.lower() or "metal" in n.lower()]
        cell("D1 a sheet-metal workbench is listed", bool(metal),
             f"{len(metal)} of {len(shelves)}: {metal[:4]}")
    except Exception as error:  # noqa: BLE001
        cell("D1 a sheet-metal workbench is listed", False,
             str(error).splitlines()[0][:100])
    for name in ("StartCommand", "StartWorkbench", "RefreshDisplay"):
        try:
            value = getattr(conn.app, name)
            # exists and is CALLABLE, which is what a UI route would need
            cell(f"D2 CATIA.{name} callable", callable(value), repr(value)[:40])
        except Exception as error:  # noqa: BLE001
            cell(f"D2 CATIA.{name} callable", False,
                 str(error).splitlines()[0][:100])

    say("")
    close_ours(conn)
    passed = sum(1 for _n, ok, _d in CELLS if ok)
    say("-" * 72)
    say(f"{passed}/{len(CELLS)} cells reported a capability")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
