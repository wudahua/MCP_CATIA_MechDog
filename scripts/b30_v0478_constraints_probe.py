"""Assembly constraints on B30, round three: the routes never tried.

The 2026-11-19 report (0.3.151) says every route this surface offers answers
E_FAIL.  Two rounds of that conclusion rest on the same two assumptions, and
neither was ever measured:

  1. that ``AddMonoEltCst`` accepts a *Product* as its element (the sketch code
     in this very repo passes a Reference - ``part.CreateReferenceFromObject``);
  2. that the document under test is the interesting one - every earlier probe
     constrained an assembly built by ``AddComponentsFromFiles`` from files that
     were already on disk, i.e. a document whose parts are *loaded*.

So: a fresh product with an internal new part, then every way of getting a
Reference out of it, then the UI command, then the workbench that is actually
active (asserted by name, not by the return of StartWorkbench).

One new document per cell.  Every failure is printed, not argued.
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "catia_probe_v0478")
os.makedirs(ROOT, exist_ok=True)


def say(text: str) -> None:
    print(text, flush=True)


def fresh(docs, name: str) -> str:
    try:
        while True:
            docs.execute("catia_close_document", {"save": False})
    except Exception:  # noqa: BLE001
        pass
    path = os.path.join(ROOT, f"{name}.CATProduct")
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_product", {"name": name, "file_path": path})
    return path


def attempt(label: str, fn):
    """Call fn(); print what happened, return its value or None."""
    try:
        value = fn()
        say(f"  [ok  ] {label}: {value!r}")
        return value
    except Exception as exc:  # noqa: BLE001
        say(f"  [FAIL] {label}: {type(exc).__name__}: {exc}")
        return None


def workbench(app) -> str:
    try:
        return f"{app.ActiveWorkbench.Name!r} / id={app.ActiveWorkbench.Id!r}"
    except Exception as exc:  # noqa: BLE001
        return f"<no ActiveWorkbench: {exc}>"


def count(product) -> object:
    try:
        return product.Connections("CATIAConstraints").Count
    except Exception as exc:  # noqa: BLE001
        return f"<Count failed: {exc}>"


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    app = conn.app
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    asm = AssemblyTools(conn)

    say(f"probe v0478 on {app.Name} {getattr(app, 'Version', '?')}")
    say(f"workbench at start: {workbench(app)}")

    # ══ cell 0: workbench -------------------------------------------------
    say("\n0: what StartWorkbench actually does (return value vs active WB)")
    for name in ("Assembly Design", "Assembly", "ASD", "AssemblyDesign",
                 "NoSuchWorkbenchName"):
        attempt(f"StartWorkbench({name!r})", lambda n=name: app.StartWorkbench(n))
        say(f"        -> active: {workbench(app)}")

    # ══ cell 1: a fresh product with an internal part ----------------------
    say("\n1: fresh product + add_new_part (no file, nothing loaded from disk)")
    path = fresh(docs, "V0478_asm")
    product = conn.get_active_product()
    say(f"  product: {getattr(product, 'Name', '?')}  parts={product.Products.Count}")
    attempt("add_new_part", lambda: asm.execute("catia_add_new_part", {}))
    say(f"  parts now: {product.Products.Count}  constraints={count(product)}")
    if product.Products.Count >= 1:
        comp = product.Products.Item(1)
        say(f"  component: {comp.Name!r} type={type(comp)}")
        attempt("comp.PartNumber", lambda: comp.PartNumber)
        attempt("comp.ReferenceProduct", lambda: comp.ReferenceProduct)
        attempt("comp.ReferenceProduct.Name", lambda: comp.ReferenceProduct.Name)

        say("  -- what can make a Reference out of a product instance?")
        attempt("product.CreateReferenceFromObject(comp)",
                lambda: product.CreateReferenceFromObject(comp))
        attempt("hasattr(product,'CreateReferenceFromObject')",
                lambda: hasattr(product, "CreateReferenceFromObject"))
        attempt("comp.CreateReferenceFromObject(comp)",
                lambda: comp.CreateReferenceFromObject(comp))
        attempt("comp.Parent.CreateReferenceFromObject(comp)",
                lambda: comp.Parent.CreateReferenceFromObject(comp))
        attempt("part_of_reference_doc",
                lambda: comp.ReferenceProduct.Parent)
        try:
            ref_doc = comp.ReferenceProduct.Parent
            say(f"        ref doc: {ref_doc.Name!r}")
            attempt("ref_doc.Part.CreateReferenceFromObject(ref_doc.Part)",
                    lambda: ref_doc.Part.CreateReferenceFromObject(ref_doc.Part))
        except Exception as exc:  # noqa: BLE001
            say(f"        <no ref doc: {exc}>")

        sel = conn.hso
        attempt("sel.Add(comp)", lambda: sel.Add(comp))
        attempt("sel.Count", lambda: sel.Count)
        attempt("sel.Item(1)", lambda: sel.Item(1))
        attempt("sel.Item(1).Reference", lambda: sel.Item(1).Reference)
        attempt("sel.Item2(1)", lambda: sel.Item2(1))
        attempt("sel.Item2(1).Reference", lambda: sel.Item2(1).Reference)
        attempt("sel.Item2(1).Value", lambda: sel.Item2(1).Value)

        say("  -- AddMonoEltCst with every element form we can build")
        constraints = None
        attempt("Connections('CATIAConstraints')",
                lambda: product.Connections("CATIAConstraints"))

        def cs():
            nonlocal constraints
            constraints = product.Connections("CATIAConstraints")
            return constraints
        cs()
        for label, maker in (
            ("raw product", lambda: comp),
            ("ReferenceProduct", lambda: comp.ReferenceProduct),
            ("root product", lambda: product),
            ("sel.Item(1).Reference", lambda: sel.Item(1).Reference),
        ):
            def go(m=maker):  # noqa: BLE001
                element = m()
                cst = constraints.AddMonoEltCst(0, element)
                return f"created {cst.Name!r}, count={constraints.Count}"
            attempt(f"AddMonoEltCst(0, {label})", go)

        say("  -- and the UI command (Fix is one click, no dialog)")
        def ui_fix():
            sel.Clear()
            sel.Add(comp)
            app.StartCommand("Fix")
            return f"count={constraints.Count}, sel={sel.Count}"
        attempt("StartCommand('Fix')", ui_fix)
        attempt("constraints.Count after StartCommand",
                lambda: product.Connections("CATIAConstraints").Count)

    say(f"\nworkbench at end: {workbench(app)}")
    traceback.print_exc() if False else None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
