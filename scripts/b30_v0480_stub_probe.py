"""Is StartWorkbench a stub?  (v0480)

v0479 measured two things in one session:

  · Fix on a SKETCH works - ``固定的.1`` created, code 0, a Reference argument.
    So AddMonoEltCst(0, Reference) is fine on this build, in this session.
  · Fix on a PRODUCT answers E_FAIL for all 14 type codes, on both the root's
    collection and the child's, and the UI command 'Fix' is "未知命令" no matter
    which workbench name is passed to StartWorkbench.

And ``app.ActiveWindow`` / ``app.ActiveWorkbench`` answer AttributeError - which
cannot be true of a live CATIA Application.  That is the signature
``connection._dynamic`` exists for: a gen_py cache class exposing only a stale
subset of the interface.  So StartWorkbench returning None for a workbench name
that does not exist may not be a switch that silently failed - it may be a stub
that never called anything.

Cells:

  A  the app object: gen_py wrapper vs a purely dynamic one, member by member,
     including StartWorkbench('NoSuchWorkbenchName') - a real method must raise.
  B  GetWorkbenchId: does this build know a workbench called 'Assembly Design'?
  C  after a switch through the dynamic object: is 'Fix' a known command, and
     does AddMonoEltCst(0, reference) work on the product?
  D  if a switch does happen: ReadObject/Observe or the window title, so the
     change is evidence rather than a None.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import win32com.client.dynamic  # noqa: E402

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from b30_v0479_workbench_probe import command, dismiss, say  # noqa: E402

ROOT = os.path.join(os.environ.get("TEMP", "."), "catia_probe_v0480")
os.makedirs(ROOT, exist_ok=True)


def probe(obj, label: str, name: str) -> None:
    try:
        value = getattr(obj, name)
    except Exception as exc:  # noqa: BLE001
        say(f"  {label}.{name}: AttributeError ({type(exc).__name__})")
        return
    say(f"  {label}.{name}: {'<callable>' if callable(value) else repr(value)}")


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    app = conn.app
    docs = DocumentTools(conn)
    asm = AssemblyTools(conn)
    say(f"probe v0480 on {app.Name}")
    dismiss()

    dyn = win32com.client.dynamic.Dispatch(app._oleobj_)
    say(f"  gen_py class: {type(app).__name__};  dynamic class: {type(dyn).__name__}")

    say("\nA: the same members on both wrappers")
    for name in ("ActiveWindow", "ActiveWorkbench", "ActiveDocument",
                 "StartWorkbench", "StartCommand", "GetWorkbenchId"):
        probe(app, "gen_py ", name)
        probe(dyn, "dynamic", name)
    try:
        say(f"  dynamic.ActiveDocument.Name: {dyn.ActiveDocument.Name!r}")
    except Exception as exc:  # noqa: BLE001
        say(f"  dynamic.ActiveDocument.Name: FAILED {exc}")
    try:
        say(f"  gen_py .ActiveDocument.Name: {app.ActiveDocument.Name!r}")
    except Exception as exc:  # noqa: BLE001
        say(f"  gen_py .ActiveDocument.Name: FAILED {exc}")

    say("\nA2: a real method must reject a name that does not exist")
    for label, obj in (("gen_py ", app), ("dynamic", dyn)):
        try:
            value = obj.StartWorkbench("NoSuchWorkbenchName")
            say(f"  {label}.StartWorkbench('NoSuchWorkbenchName') -> {value!r} "
                "(no complaint: this is a stub or a silent no-op)")
        except Exception as exc:  # noqa: BLE001
            say(f"  {label}.StartWorkbench('NoSuchWorkbenchName') -> "
                f"{type(exc).__name__}: {' '.join(str(exc).split())[:80]}")

    say("\nB: does this build know the workbench at all?")
    for name in ("Assembly Design", "Assembly", "装配设计",
                 "Assembly Design Workbench", "NoSuchWorkbenchName"):
        for label, obj in (("gen_py ", app), ("dynamic", dyn)):
            try:
                say(f"  {label}.GetWorkbenchId({name!r}) -> {obj.GetWorkbenchId(name)!r}")
            except Exception as exc:  # noqa: BLE001
                say(f"  {label}.GetWorkbenchId({name!r}) -> {type(exc).__name__}: "
                    f"{' '.join(str(exc).split())[:70]}")

    # ══ C: switch through the object that can actually call it =============
    say("\nC: product + Part.1, switch through the dynamic app, then re-measure")
    try:
        while True:
            docs.execute("catia_close_document", {"save": False})
    except Exception:  # noqa: BLE001
        pass
    asm_path = os.path.join(ROOT, "CstAsm2.CATProduct")
    if os.path.exists(asm_path):
        os.remove(asm_path)
    docs.execute("catia_new_product", {"name": "CstAsm2", "file_path": asm_path})
    product = conn.get_active_product()
    asm.execute("catia_add_new_part", {})
    comp = product.Products.Item(1)
    sel = conn.hso

    def measure(label: str) -> None:
        sel.Clear()
        sel.Add(comp)
        before = product.Connections("CATIAConstraints").Count
        verdict = command(app, "Fix")
        after = product.Connections("CATIAConstraints").Count
        say(f"  {label}: 'Fix' -> {verdict}; constraints {before} -> {after}")
        try:
            sel.Clear()
            sel.Add(comp)
            ref = sel.Item(1).Reference
            cst = product.Connections("CATIAConstraints").AddMonoEltCst(0, ref)
            say(f"        AddMonoEltCst -> CREATED {cst.Name!r}")
        except Exception as exc:  # noqa: BLE001
            say(f"        AddMonoEltCst -> {' '.join(str(exc).split())[:70]}")

    measure("before any switch")
    for name in ("Assembly Design", "装配设计"):
        try:
            value = dyn.StartWorkbench(name)
            say(f"  dynamic.StartWorkbench({name!r}) -> {value!r}")
        except Exception as exc:  # noqa: BLE001
            say(f"  dynamic.StartWorkbench({name!r}) -> {type(exc).__name__}: "
                f"{' '.join(str(exc).split())[:80]}")
        time.sleep(1.5)
        try:
            say(f"        doc title now: {dyn.ActiveDocument.Name!r}")
        except Exception:  # noqa: BLE001
            pass
        measure(f"after dynamic switch to {name!r}")

    dismiss()
    say("\ndone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
