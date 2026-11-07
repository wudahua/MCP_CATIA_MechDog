"""0.3.79 probe: can this build colour parts, and at which level?

Nothing is written to any document the caller had open: everything happens in
F:\\catia_probe_v0379, which is removed at the end, and the session inventory is
compared before/after.

Three questions, in order:
 1. What does the VisProperties type library actually offer?
 2. Does SetRealColor on a COMPONENT change only that instance?
 3. Does it survive save + reopen (i.e. is it stored in the file, not the session)?
"""
from __future__ import annotations

import os
import shutil
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402

ROOT = r"F:\catia_probe_v0379"
LINES = []


def say(text: str = "") -> None:
    print(text, flush=True)
    LINES.append(str(text))


def methods(obj) -> list[str]:
    """Function names the type library exposes on this object."""
    try:
        info = obj.GetTypeInfo()
        attr = info.GetTypeAttr()
    except Exception as error:  # noqa: BLE001
        return [f"<no type info: {str(error).splitlines()[0][:60]}>"]
    names = []
    for index in range(attr.cFuncs):
        try:
            desc = info.GetFuncDesc(index)
            names.append(str(info.GetNames(desc.memid)[0]))
        except Exception:  # noqa: BLE001
            continue
    return names


def read_colour(selection) -> tuple | None:
    """VisProperties.GetRealColor - the read-back that decides if a write took."""
    try:
        value = selection.VisProperties.GetRealColor()
    except Exception as error:  # noqa: BLE001
        return ("<failed>", str(error).splitlines()[0][:60])
    if isinstance(value, (tuple, list)):
        return tuple(int(v) for v in value)
    return value


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    design = PartDesignTools(conn)
    assembly = AssemblyTools(conn)

    before = {str(conn.documents.Item(i).Name)
              for i in range(1, conn.documents.Count + 1)}
    say(f"session before: {conn.documents.Count} document(s)")

    try:
        # ── two different parts, each a box, one product holding both ─────
        for index, (name, size) in enumerate((("A", 20), ("B", 12)), start=1):
            path = os.path.join(ROOT, f"p{index}.CATPart")
            docs.execute("catia_new_part", {"name": name, "file_path": path})
            sketcher.execute("catia_create_sketch", {"plane": "xy"})
            sketcher.execute("catia_sketch_rectangle",
                             {"x1": 0, "y1": 0, "x2": size, "y2": size})
            sketcher.execute("catia_close_sketch", {})
            design.execute("catia_pad", {"height": size})
            docs.execute("catia_save_document", {})

        asm_path = os.path.join(ROOT, "asm.CATProduct")
        docs.execute("catia_new_product", {"name": "ASM", "file_path": asm_path})
        for index in (1, 2):
            assembly.execute("catia_add_component",
                             {"file_path": os.path.join(ROOT, f"p{index}.CATPart")})
        say("assembled: " + " | ".join(
            str(assembly.execute("catia_list_components", {"names_only": True}))[:200]
            for _ in (0,)))

        product_doc = conn.active_document
        product = conn.product_of(product_doc)
        sel = product_doc.Selection

        # ── 1: what does the interface offer? ────────────────────────────
        say("\n1  VisProperties surface")
        sel.Clear()
        sel.Add(product.Products.Item(1))
        names = methods(sel.VisProperties)
        say("   " + ", ".join(names))

        # ── 2: colour one component, read both back ──────────────────────
        say("\n2  SetRealColor on component 1 only")
        sel.Clear()
        sel.Add(product.Products.Item(1))
        try:
            sel.VisProperties.SetRealColor(220, 30, 30)
            say("   SetRealColor(220,30,30) accepted")
        except Exception as error:  # noqa: BLE001
            say(f"   SetRealColor REFUSED: {str(error).splitlines()[0][:100]}")
        first = read_colour(sel)
        sel.Clear()
        sel.Add(product.Products.Item(2))
        second = read_colour(sel)
        say(f"   component 1 -> {first}")
        say(f"   component 2 -> {second}   (must be unchanged)")

        # is there a plain SetColor too, and does it differ?
        sel.Clear()
        sel.Add(product.Products.Item(2))
        for candidate in ("SetColor", "SetRealColor2", "SetVisibleIn3D"):
            if hasattr(sel.VisProperties, candidate):
                say(f"   has {candidate}")

        # ── 3: does it survive close + reopen? ───────────────────────────
        say("\n3  save, close, reopen")
        docs.execute("catia_save_document", {})
        conn.documents.Item(conn.documents.Count).Close() if False else None
        product_doc.Close()
        reopened = conn.documents.Open(os.path.normpath(asm_path))
        sel = reopened.Selection
        sel.Clear()
        sel.Add(conn.product_of(reopened).Products.Item(1))
        say(f"   after reopen, component 1 -> {read_colour(sel)}")
        sel.Clear()
        sel.Add(conn.product_of(reopened).Products.Item(2))
        say(f"   after reopen, component 2 -> {read_colour(sel)}")

    finally:
        current = {str(conn.documents.Item(i).Name)
                   for i in range(1, conn.documents.Count + 1)}
        for name in sorted(current - before, reverse=True):
            for index in range(1, conn.documents.Count + 1):
                if str(conn.documents.Item(index).Name) == name:
                    try:
                        conn.documents.Item(index).Close()
                    except Exception as error:  # noqa: BLE001
                        say(f"   (could not close {name}: {error})")
                    break
        shutil.rmtree(ROOT, ignore_errors=True)
        say(f"\nsession after: {conn.documents.Count} document(s)")
        try:
            conn.app.ActiveDocument.Name  # noqa: B018 - touches nothing
        except Exception:  # noqa: BLE001
            pass

    with open(os.path.join(os.environ.get("TEMP", "."), "v0379_colour.txt"),
              "w", encoding="utf-8") as handle:
        handle.write("\n".join(LINES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
