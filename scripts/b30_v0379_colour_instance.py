"""0.3.79 probe 3: per-INSTANCE colour in an assembly, and persistence.

Probe 2 found the signature this build wants: SetRealColor(R, G, B, <unused>) -
three arguments are refused with "invalid number of arguments", and GetRealColor()
returns a 4-tuple whose last three values are R, G, B.

So the question this answers is the one a user actually asks: can two instances
of the SAME part carry different colours in one assembly, and does that survive
closing and reopening the product?
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402

ROOT = r"F:\catia_probe_v0379c"
CHECKS = []


def say(text: str = "") -> None:
    print(text, flush=True)


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok)))
    say(f"   [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def read(selection):
    try:
        values = tuple(int(v) for v in selection.VisProperties.GetRealColor())
    except Exception as error:  # noqa: BLE001
        return f"<{str(error).splitlines()[0][:70]}>"
    return values


def rgb(selection) -> tuple[int, int, int] | None:
    value = read(selection)
    if isinstance(value, tuple) and len(value) >= 4:
        return value[-3:]
    return None


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
    say(f"session before: {conn.documents.Count}")

    try:
        docs.execute("catia_new_part",
                     {"name": "CP", "file_path": os.path.join(ROOT, "cp.CATPart")})
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 20, "y2": 12})
        sketcher.execute("catia_close_sketch", {})
        design.execute("catia_pad", {"height": 10})
        docs.execute("catia_save_document", {})

        asm_path = os.path.join(ROOT, "asm.CATProduct")
        docs.execute("catia_new_product", {"name": "ASM", "file_path": asm_path})
        # TWO instances of the SAME part: the only case that separates
        # "instance colour" from "part colour".
        assembly.execute("catia_add_component",
                         {"file_path": os.path.join(ROOT, "cp.CATPart"), "count": 2})

        doc = conn.active_document
        product = conn.product_of(doc)
        sel = doc.Selection
        say(f"instances: {product.Products.Count}")

        def at(index: int):
            sel.Clear()
            sel.Add(product.Products.Item(index))
            return sel

        def paint(index: int, r: int, g: int, b: int) -> None:
            target = at(index)
            target.VisProperties.SetRealColor(r, g, b, 0)

        say("\n1  two instances of one part, two colours")
        paint(1, 220, 30, 30)
        paint(2, 30, 120, 220)
        first, second = rgb(at(1)), rgb(at(2))
        say(f"   instance 1 -> {first} | instance 2 -> {second}")
        check("instance 1 is red", first == (220, 30, 30), str(first))
        check("instance 2 is blue", second == (30, 120, 220), str(second))
        check("the two instances differ", first != second, f"{first} vs {second}")

        say("\n2  does the PART document change too? (it must not)")
        part_doc_index = None
        for index in range(1, conn.documents.Count + 1):
            if str(conn.documents.Item(index).Name) == "cp.CATPart":
                part_doc_index = index
        part_doc = conn.documents.Item(part_doc_index)
        part_doc.Activate()
        psel = part_doc.Selection
        psel.Clear()
        psel.Add(conn.get_active_part_body())
        part_colour = rgb(psel)
        say(f"   part body in cp.CATPart -> {part_colour}")
        check("the part file itself stays uncoloured (instance colour is per-instance)",
              part_colour != (220, 30, 30), str(part_colour))

        say("\n3  persistence: save, close, reopen")
        doc.Activate()
        docs.execute("catia_save_document", {})
        doc.Close()
        reopened = conn.documents.Open(os.path.normpath(asm_path))
        rsel = reopened.Selection
        rproduct = conn.product_of(reopened)

        def rat(index: int):
            rsel.Clear()
            rsel.Add(rproduct.Products.Item(index))
            return rsel

        def rrgb(selection):
            value = read(selection)
            return value[-3:] if isinstance(value, tuple) and len(value) >= 4 else value

        again = (rrgb(rat(1)), rrgb(rat(2)))
        say(f"   after reopen -> {again}")
        check("instance 1 keeps its colour after reopen", again[0] == (220, 30, 30), str(again[0]))
        check("instance 2 keeps its colour after reopen", again[1] == (30, 120, 220), str(again[1]))

        say("\n4  does colour reach the STL / does hiding still work?")
        rsel.Clear()
        rsel.Add(rproduct.Products.Item(1))
        rsel.VisProperties.SetShow(1)
        hidden = tuple(int(v) for v in rsel.VisProperties.GetShow())
        say(f"   GetShow after SetShow(1) -> {hidden}")
        check("SetShow still behaves (flag at [1])", hidden[1] == 1, str(hidden))
        rsel.VisProperties.SetShow(0)

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

    passed = sum(1 for _, ok in CHECKS if ok)
    say(f"\n{passed}/{len(CHECKS)} checks passed")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
