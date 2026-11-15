"""B30 acceptance for the sheet-metal parameter tools (0.3.133).

The 2026-11-03 report's items 1 and 2: "read/write thickness, K factor, default
bend radius, relief" and "a new part that is a sheet-metal part". Everything
here is measured, including the parts that only work under a condition.

One NEW document per case: a document-level failure (a rejected update, a dialog
on the automation thread) would otherwise hide every later case.

    .venv/Scripts/python.exe -u scripts/b30_v0435_audit.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.sheetmetal import SheetMetalTools  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "catia_probe_v0435_audit")
CELLS: list[tuple[int, str, bool, str]] = []


def check(number: int, name: str, ok: bool, detail: str = "") -> None:
    CELLS.append((number, name, ok, detail))
    print(f"  [{number:2d}] {'PASS' if ok else 'FAIL'}  {name}"
          + (f"\n         {detail}" if detail else ""), flush=True)


def say(text: str = "") -> None:
    print(text, flush=True)


def close_probe_documents(conn: Any) -> None:
    """Close only documents under this probe's own folder (%TEMP%)."""
    root = os.path.abspath(ROOT).lower()
    for index in range(int(conn.documents.Count), 0, -1):
        try:
            document = conn.documents.Item(index)
            full = str(document.FullName or "")
        except Exception:  # noqa: BLE001 - a pathless document is not ours
            continue
        if full and os.path.abspath(full).lower().startswith(root):
            try:
                document.Close()
            except Exception:  # noqa: BLE001 - a dialog is the caller's problem
                pass


def fresh(conn: Any, docs: Any, name: str) -> str:
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, name + ".CATPart")
    close_probe_documents(conn)
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    return path


def factory_methods(part: Any) -> list[str]:
    """Every function name of Part.SheetMetalFactory, from its type library."""
    try:
        info = part.SheetMetalFactory._oleobj_.GetTypeInfo()
        count = info.GetTypeAttr().cFuncs
    except Exception:  # noqa: BLE001
        return []
    names = []
    for index in range(count):
        try:
            names.append(info.GetNames(info.GetFuncDesc(index).memid)[0])
        except Exception:  # noqa: BLE001
            continue
    return names


def main() -> int:
    conn = CATIAConnection()
    conn.ensure_connected()
    docs = DocumentTools(conn)
    metal = SheetMetalTools(conn, docs)
    os.makedirs(ROOT, exist_ok=True)
    close_probe_documents(conn)

    # ── 1. a part with no set refuses, and names the way out ──────────────
    say("1 - a part that has never had the parameter set")
    fresh(conn, docs, "V0435_NoSet")
    try:
        metal.execute("catia_sheetmetal_parameters", {})
        check(1, "read on a part without the set is refused, with the way out",
              False, "it answered instead of refusing")
    except Exception as error:  # noqa: BLE001
        code = getattr(error, "code", "?")
        text = str(error)
        check(1, "read on a part without the set is refused, with the way out",
              code == "NO_SHEETMETAL_PARAMETERS" and "create=true" in text,
              f"{code}: {text.splitlines()[0][:100]}")

    # ── 2. create=true adds it, and CATIA's defaults are what arrive ──────
    say("")
    say("2 - create=true: the set, its defaults, and the cross-check")
    out = json.loads(metal.execute("catia_sheetmetal_parameters", {"create": True}))
    values = {key: entry["value"] for key, entry in out["parameters"].items()}
    check(2, "create=true adds the parameter set with CATIA's defaults",
          out["created"] is True
          and values.get("thickness") == 2.0
          and values.get("bend_radius") == 4.0
          and out["cross_check"].get("agrees") is True,
          f"container {out['container']}, values {values}, "
          f"cross_check {out['cross_check']}")

    # ── 3. asking again does not create a second one ──────────────────────
    say("")
    say("3 - create=true on a part that already has it")
    again = json.loads(metal.execute("catia_sheetmetal_parameters", {"create": True}))
    check(3, "a second create=true is a no-op, not a second set",
          again["created"] is False
          and again["container"] == out["container"],
          f"created {again['created']}, container {again['container']}")

    # ── 4. the thickness really moves, on both sides ──────────────────────
    say("")
    say("4 - write thickness 1.5 and bend radius 1.905")
    written = json.loads(metal.execute("catia_sheetmetal_parameters",
                                       {"thickness": 1.5, "bend_radius": 1.905}))
    by_key = {entry["parameter"]: entry for entry in written["writes"]}
    check(4, "thickness and bend radius are written and read back",
          by_key["thickness"]["written"] and by_key["thickness"]["after"] == 1.5
          and by_key["bend_radius"]["written"]
          and by_key["bend_radius"]["after"] == 1.905,
          f"thickness {by_key['thickness'].get('before')} -> "
          f"{by_key['thickness'].get('after')}, bend_radius "
          f"{by_key['bend_radius'].get('before')} -> "
          f"{by_key['bend_radius'].get('after')}")
    check(5, "GetThickness() agrees with the parameter AFTER the write",
          written["cross_check"].get("get_thickness_mm") == 1.5
          and written["cross_check"].get("agrees") is True,
          f"cross_check {written['cross_check']}")

    # ── 6. the K factor is locked by the DIN rule, and the reply says so ──
    say("")
    say("6 - write K factor 0.5 (locked by the DIN rule on this build)")
    k_out = json.loads(metal.execute("catia_sheetmetal_parameters",
                                     {"k_factor": 0.5}))
    k_entry = {entry["parameter"]: entry for entry in k_out["writes"]}["k_factor"]
    check(6, "K factor is written, and the rule it needed switched off is named",
          k_entry["written"] and k_entry["after"] == 0.5
          and (k_entry.get("unlocked") or {}).get("switched_off"),
          f"{k_entry.get('before')} -> {k_entry.get('after')}, switched off "
          f"{(k_entry.get('unlocked') or {}).get('switched_off')}")

    # ── 7. the relief slots move too ──────────────────────────────────────
    say("")
    say("7 - write relief radius 1.2")
    r_out = json.loads(metal.execute("catia_sheetmetal_parameters",
                                     {"relief_radius": 1.2}))
    r_entry = {entry["parameter"]: entry for entry in r_out["writes"]}["relief_radius"]
    check(7, "corner relief radius is written and read back",
          r_entry["written"] and r_entry["after"] == 1.2,
          f"{r_entry.get('before')} -> {r_entry.get('after')}")

    # ── 8. a new document that carries the set from the start ─────────────
    say("")
    say("8 - catia_new_sheetmetal with thickness 1.2")
    os.makedirs(ROOT, exist_ok=True)
    close_probe_documents(conn)
    new_path = os.path.join(ROOT, "V0435_NewSheetMetal.CATPart")
    if os.path.exists(new_path):
        os.remove(new_path)
    new_out = json.loads(metal.execute("catia_new_sheetmetal",
                                       {"name": "V0435_NewSheetMetal",
                                        "file_path": new_path, "thickness": 1.2}))
    new_values = {key: entry["value"] for key, entry in new_out["parameters"].items()}
    check(8, "catia_new_sheetmetal gives a document whose thickness is 1.2",
          new_out["created"] is True and new_values.get("thickness") == 1.2
          and os.path.exists(new_path),
          f"values {new_values}, file {os.path.basename(new_path)} "
          f"exists {os.path.exists(new_path)}")

    # ── 9. a bad number is refused before CATIA is asked anything ─────────
    say("")
    say("9 - a negative thickness")
    try:
        metal.execute("catia_sheetmetal_parameters", {"thickness": -1})
        check(9, "a negative thickness is refused as a bad argument", False,
              "it was accepted")
    except Exception as error:  # noqa: BLE001
        check(9, "a negative thickness is refused as a bad argument",
              getattr(error, "code", "?") == "BAD_ARGUMENT",
              f"{getattr(error, 'code', '?')}: {str(error).splitlines()[0][:90]}")

    # ── 10. the wall/flange/bend gap is still a gap, and it is measured ───
    say("")
    say("10 - what the factory still cannot do")
    part = conn.get_active_part()
    names = factory_methods(part)
    addnew = [name for name in names if name.startswith("AddNew")]
    check(10, "SheetMetalFactory still exposes no AddNew* (so: no wall tool)",
          bool(names) and not addnew,
          f"{len(names)} members, AddNew* = {addnew}")

    say("")
    close_probe_documents(conn)
    passed = sum(1 for _n, _s, ok, _d in CELLS if ok)
    say("-" * 72)
    say(f"{passed}/{len(CELLS)} checks passed")
    for number, name, ok, _detail in CELLS:
        if not ok:
            say(f"  FAILED {number}: {name}")
    return 0 if passed == len(CELLS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
