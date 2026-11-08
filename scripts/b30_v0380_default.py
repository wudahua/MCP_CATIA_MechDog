"""0.3.80 probe 1: what does an UNCOLOURED component read as?

0.3.79's colorize_assembly counted "already carried a colour" by comparing the
read-back with (210, 210, 255) - the default measured on a PART BODY. The
2026-11-08 report (P1-1) says a never-coloured 25-instance assembly answered
"11 already carried a colour", which is 11 = the number of parts: every part was
counted, so the comparison must be against the wrong value.

Probe 1 of 0.3.79 printed `component 1 -> (0, 255, 255, 255)` before anything was
written - i.e. (255, 255, 255). This measures it properly: component vs body,
before and after colouring, and after a reopen.
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.colours import read_rgb  # noqa: E402
from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.appearance import AppearanceTools  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0380a"


def say(line: str = "") -> None:
    print(line, flush=True)


def colour_of(selection, node) -> tuple[int, int, int] | None:
    selection.Clear()
    selection.Add(node)
    value = read_rgb(selection)
    selection.Clear()
    return value


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    design = PartDesignTools(conn)
    assembly = AssemblyTools(conn)
    tools = AppearanceTools(conn)
    before = {str(conn.documents.Item(i).Name)
              for i in range(1, conn.documents.Count + 1)}
    say(f"session before: {conn.documents.Count}")

    try:
        for index, size in ((1, 20), (2, 12)):
            docs.execute("catia_new_part",
                         {"name": f"T{index}",
                          "file_path": os.path.join(ROOT, f"t{index}.CATPart")})
            sketcher.execute("catia_create_sketch", {"plane": "xy"})
            sketcher.execute("catia_sketch_rectangle",
                             {"x1": 0, "y1": 0, "x2": size, "y2": size})
            sketcher.execute("catia_close_sketch", {})
            design.execute("catia_pad", {"height": size})
            docs.execute("catia_save_document", {})
        asm_path = os.path.join(ROOT, "asm.CATProduct")
        docs.execute("catia_new_product", {"name": "ASM", "file_path": asm_path})
        assembly.execute("catia_add_component",
                         {"file_path": os.path.join(ROOT, "t1.CATPart"), "count": 2})
        assembly.execute("catia_add_component",
                         {"file_path": os.path.join(ROOT, "t2.CATPart")})
        docs.execute("catia_save_document", {})

        product_doc = conn.active_document
        product = conn.product_of(product_doc)
        sel = product_doc.Selection

        say("\n1  BEFORE any colour - component instances")
        for index in range(1, product.Products.Count + 1):
            node = product.Products.Item(index)
            say(f"   {node.Name}: {colour_of(sel, node)}")

        say("\n2  BEFORE any colour - the part bodies, in their own documents")
        for name in ("t1.CATPart", "t2.CATPart"):
            for index in range(1, conn.documents.Count + 1):
                if str(conn.documents.Item(index).Name) == name:
                    conn.documents.Item(index).Activate()
                    break
            part = conn.get_active_part()
            body = conn.get_active_part_body()
            say(f"   {name} body: {colour_of(conn.active_document.Selection, body)}")
        product_doc.Activate()

        say("\n3  colorize, then read the same instances again")
        say("   " + tools.execute("catia_colorize_assembly", {}).splitlines()[0])
        say("   " + [line for line in tools.execute("catia_get_color", {}).splitlines()
                     if "colour" not in line][0])
        for index in range(1, product.Products.Count + 1):
            node = product.Products.Item(index)
            say(f"   {node.Name}: {colour_of(sel, node)}")

        say("\n4  re-run colorize - what does it NOW say about existing colours?")
        say("   " + tools.execute("catia_colorize_assembly", {})
            .splitlines()[-1])

        say("\n5  does a coloured instance read (255,255,255) anywhere?")

        say("\n6  save, close, reopen, read again")
        docs.execute("catia_save_document", {})
        product_doc.Close()
        reopened = conn.documents.Open(os.path.normpath(asm_path))
        product = conn.product_of(reopened)
        sel = reopened.Selection
        for index in range(1, product.Products.Count + 1):
            node = product.Products.Item(index)
            say(f"   {node.Name}: {colour_of(sel, node)}")

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
        say(f"\nsession after: {conn.documents.Count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
