"""0.3.81 probe 1: the two facts the 2026-11-08 reports turn on.

(a) Is a component/body colour of (210,210,255) distinguishable from "never
    coloured"? 0.3.80 compares the instance read-back with (255,255,255), so a
    reporter who set every part to #D2D2FF - CATIA's own uncoloured appearance
    - got "11 already carried a colour". Read the raw GetRealColor tuple in
    every state before deciding what the test should be.
(b) Does a component instance expose the file it really references? 0.3.80
    announces "the new instance references the file at that path" without ever
    reading it back. If ReferenceProduct.Parent.FullName exists, it is the
    judgement; if it does not, the tool has nothing to verify with and must say
    so instead of claiming success.

Everything lives under F:\\catia_probe_v0381 and is closed by FullName.
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402

ROOT = r"F:\catia_probe_v0381"


def say(line: str = "") -> None:
    print(line, flush=True)


def raw_colour(selection) -> tuple:
    try:
        return tuple(selection.VisProperties.GetRealColor())
    except Exception as error:  # noqa: BLE001
        return ("<failed: %s>" % str(error).splitlines()[0][:50],)


def main() -> int:
    os.makedirs(os.path.join(ROOT, "A"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "B"), exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    asm = AssemblyTools(conn)

    mine = lambda path: os.path.normcase(os.path.abspath(path))  # noqa: E731
    before = {}
    for index in range(1, conn.documents.Count + 1):
        doc = conn.documents.Item(index)
        try:
            before[mine(str(doc.FullName))] = str(doc.Name)
        except Exception:  # noqa: BLE001
            pass
    say(f"session before: {conn.documents.Count} document(s)")

    try:
        # ── (a) colour states on a body ─────────────────────────────────
        say("\n(a) raw GetRealColor() on a part body, four states")
        part_a = os.path.join(ROOT, "A", "PZ81A.CATPart")
        docs.execute("catia_new_part", {"name": "PZ81A", "file_path": part_a, "overwrite": True})
        part = conn.get_active_part()
        body = conn.get_active_part_body()
        doc = conn.active_document

        def read_body(label: str) -> None:
            doc.Selection.Clear()
            doc.Selection.Add(body)
            say(f"   {label:34s} -> {raw_colour(doc.Selection)}")
            doc.Selection.Clear()

        read_body("fresh body (never coloured)")
        for rgb in ((210, 210, 255), (255, 255, 255), (228, 87, 46)):
            doc.Selection.Clear()
            doc.Selection.Add(body)
            doc.Selection.VisProperties.SetRealColor(rgb[0], rgb[1], rgb[2], 1)
            doc.Selection.Clear()
            read_body(f"explicit {rgb} flag=1")
        doc.Selection.Clear()
        doc.Selection.Add(body)
        doc.Selection.VisProperties.SetRealColor(210, 210, 255, 0)
        doc.Selection.Clear()
        read_body("explicit (210,210,255) flag=0")

        # ── (b) same states on a component instance ─────────────────────
        say("\n(b) raw GetRealColor() on a component instance")
        shutil.copyfile(part_a, os.path.join(ROOT, "B", "PZ81A.CATPart"))
        asm_path = os.path.join(ROOT, "B", "ASM81.CATProduct")
        docs.execute("catia_new_product", {"name": "ASM81", "file_path": asm_path, "overwrite": True})
        asm.execute("catia_add_component",
                    {"file_path": os.path.join(ROOT, "B", "PZ81A.CATPart")})
        product = conn.get_active_product()
        instance = product.Products.Item(1)
        pdoc = conn.active_document

        def read_instance(label: str) -> None:
            pdoc.Selection.Clear()
            pdoc.Selection.Add(instance)
            say(f"   {label:34s} -> {raw_colour(pdoc.Selection)}")
            pdoc.Selection.Clear()

        read_instance("fresh instance")
        pdoc.Selection.Clear()
        pdoc.Selection.Add(instance)
        pdoc.Selection.VisProperties.SetRealColor(210, 210, 255, 1)
        pdoc.Selection.Clear()
        read_instance("explicit (210,210,255) flag=1")

        # ── (c) can the referenced file be read back? ───────────────────
        say("\n(c) what does the instance actually reference?")
        for label, getter in (
            ("ReferenceProduct.Parent.FullName",
             lambda: instance.ReferenceProduct.Parent.FullName),
            ("ReferenceProduct.Parent.Name",
             lambda: instance.ReferenceProduct.Parent.Name),
            ("ReferenceProduct.PartNumber",
             lambda: instance.ReferenceProduct.PartNumber),
            ("ReferenceProduct.Name", lambda: instance.ReferenceProduct.Name),
            ("Definition", lambda: instance.Definition),
        ):
            try:
                say(f"   {label:34s} -> {getter()!r}")
            except Exception as error:  # noqa: BLE001
                say(f"   {label:34s} -> FAILED {str(error).splitlines()[0][:60]}")

        say("\n   after loading A/PZ81A explicitly, then adding A's file to ASM81:")
        docs.execute("catia_open_document", {"file_path": part_a})
        say(f"   documents now: {conn.documents.Count}, A loaded")
        docs.execute("catia_activate_document", {"document": "ASM81.CATProduct"})
        say(f"   active document: {conn.active_document.Name}")
        asm.execute("catia_add_component", {"file_path": part_a})
        product = conn.get_active_product()
        second = product.Products.Item(product.Products.Count)
        say(f"   added A's OWN file; instance count = {product.Products.Count}")
        for label, getter in (
            ("2nd instance ReferenceProduct.Parent.FullName",
             lambda: second.ReferenceProduct.Parent.FullName),
            ("2nd instance ReferenceProduct.Parent.Name",
             lambda: second.ReferenceProduct.Parent.Name),
        ):
            try:
                say(f"   {label:34s} -> {getter()!r}")
            except Exception as error:  # noqa: BLE001
                say(f"   {label:34s} -> FAILED {str(error).splitlines()[0][:60]}")

        # The point of the whole report: add the BYTE COPY while the original is
        # open, and see which file the new instance actually resolves to.
        say("\n   now the byte copy (same internal id, original still open):")
        copy_b = os.path.join(ROOT, "B", "PZ81A.CATPart")
        asm.execute("catia_add_component", {"file_path": copy_b})
        product = conn.get_active_product()
        third = product.Products.Item(product.Products.Count)
        resolved = None
        for label, getter in (
            ("3rd instance (copy) ReferenceProduct.Parent.FullName",
             lambda: third.ReferenceProduct.Parent.FullName),
            ("3rd instance (copy) ReferenceProduct.Parent.Name",
             lambda: third.ReferenceProduct.Parent.Name),
        ):
            try:
                value = getter()
                say(f"   {label:34s} -> {value!r}")
                if resolved is None and "FullName" in label:
                    resolved = str(value)
            except Exception as error:  # noqa: BLE001
                say(f"   {label:34s} -> FAILED {str(error).splitlines()[0][:60]}")
        say("\n   VERDICT: asked for "
            f"{copy_b!r}")
        say(f"            resolved to {resolved!r}")
        if resolved and mine(resolved) == mine(copy_b):
            say("            => the copy is used as its own file (identity is per path here)")
        elif resolved:
            say("            => CATIA reused the ALREADY-OPEN document; the copy was "
                "silently ignored (this is what the report claims)")

    finally:
        say("\ncleanup: closing only documents under " + ROOT)
        for index in range(int(conn.documents.Count), 0, -1):
            try:
                doc = conn.documents.Item(index)
                full = mine(str(doc.FullName))
            except Exception:  # noqa: BLE001
                continue
            if not full.startswith(mine(ROOT)):
                continue
            try:
                doc.Close()
                say(f"   closed {os.path.basename(full)}")
            except Exception as error:  # noqa: BLE001
                say(f"   could not close {os.path.basename(full)}: "
                    f"{str(error).splitlines()[0][:60]}")
        say(f"session after: {conn.documents.Count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
