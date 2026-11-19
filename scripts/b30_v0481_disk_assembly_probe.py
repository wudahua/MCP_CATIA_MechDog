"""The report's own scene: parts from disk, two instances (v0481)

Everything so far was measured on an assembly holding a part created *inside*
it.  The 2026-11-19 report built its 17 instances with catia_add_component from
saved .CATPart files, which is the other document lineage.  A failure is a
property of a document, so both lineages get measured before anything is
declared unavailable.

  A  two instances from disk; the visible selection really holds them.
  B  Fix through the API, on the root and on the child, design mode applied.
  C  Fix through the UI command, with the workbench id read off CATIA.
  D  a coincidence (code 2) between the two instances - a second code path.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402
from b30_v0479_workbench_probe import command, dismiss, say  # noqa: E402

ROOT = os.path.join(os.environ.get("TEMP", "."), "catia_probe_v0481")
os.makedirs(ROOT, exist_ok=True)


def short(exc: Exception) -> str:
    return " ".join(str(exc).split())[:90]


def count(product) -> object:
    try:
        return product.Connections("CATIAConstraints").Count
    except Exception as exc:  # noqa: BLE001
        return f"<{short(exc)}>"


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    app = conn.app
    docs = DocumentTools(conn)
    asm = AssemblyTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    say(f"probe v0481 on {app.Name}")
    dismiss()

    try:
        while True:
            docs.execute("catia_close_document", {"save": False})
    except Exception:  # noqa: BLE001
        pass

    part_path = os.path.join(ROOT, "Lug.CATPart")
    if os.path.exists(part_path):
        os.remove(part_path)
    docs.execute("catia_new_part", {"name": "Lug", "file_path": part_path})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "P"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 20,
                                                "y2": 10})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "P", "height": 5})
    docs.execute("catia_save_document", {})
    docs.execute("catia_close_document", {"save": False})

    asm_path = os.path.join(ROOT, "DiskAsm.CATProduct")
    if os.path.exists(asm_path):
        os.remove(asm_path)
    docs.execute("catia_new_product", {"name": "DiskAsm", "file_path": asm_path})
    reply = asm.execute("catia_add_component",
                        {"file_path": part_path, "count": 2})
    say("  add_component: " + " ".join(str(reply).split())[:160])
    product = conn.get_active_product()
    say(f"  parts={product.Products.Count} constraints={count(product)} "
        f"wb={app.GetWorkbenchId()!r}")

    sel = conn.hso
    first = product.Products.Item(1)
    second = product.Products.Item(2)

    # ── A: the selection is real ──────────────────────────────────────────
    say("\nA: the visible selection")
    sel.Clear()
    sel.Add(first)
    say(f"  sel.Count={sel.Count} item1={sel.Item(1).Value.Name!r}")
    try:
        say(f"  sel.Item(1).Reference: {sel.Item(1).Reference!r}")
    except Exception as exc:  # noqa: BLE001
        say(f"  sel.Item(1).Reference failed: {short(exc)}")

    # ── B: the API, with design mode, on three collections ────────────────
    say("\nB: AddMonoEltCst(0, ...) with design mode applied")
    for label, target in (("root", product), ("child", first)):
        for mode in (1, 2, 0):
            try:
                target.ApplyWorkMode(mode)
            except Exception as exc:  # noqa: BLE001
                say(f"  {label}.ApplyWorkMode({mode}) -> {short(exc)}")
                break
        try:
            say(f"  {label}.GetWorkMode? {target.Connections('CATIAConstraints').Count}")
        except Exception:  # noqa: BLE001
            pass
    sel.Clear()
    sel.Add(first)
    reference = sel.Item(1).Reference
    for label, target in (("root", product), ("child", first)):
        try:
            cst = target.Connections("CATIAConstraints").AddMonoEltCst(0, reference)
            say(f"  {label}: CREATED {cst.Name!r}")
        except Exception as exc:  # noqa: BLE001
            say(f"  {label}: {short(exc)}")

    # ── C: the UI command ────────────────────────────────────────────────
    say("\nC: the UI command")
    sel.Clear()
    sel.Add(first)
    before = count(product)
    verdict = command(app, "Fix")
    say(f"  'Fix' -> {verdict}; constraints {before} -> {count(product)}")

    # ── D: a coincidence between the two instances ───────────────────────
    say("\nD: AddBiEltCst(2) between the two instances")
    sel.Clear()
    sel.Add(first)
    ref1 = sel.Item(1).Reference
    sel.Clear()
    sel.Add(second)
    ref2 = sel.Item(1).Reference
    try:
        cst = product.Connections("CATIAConstraints").AddBiEltCst(2, ref1, ref2)
        say(f"  CREATED {cst.Name!r}; count={count(product)}")
    except Exception as exc:  # noqa: BLE001
        say(f"  {short(exc)}")

    dismiss()
    time.sleep(0.5)
    say("\ndone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
