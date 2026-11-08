"""0.3.80 probe 4: can a component be re-pointed, or only replaced by hand?

P1-2 of the 2026-11-08 report: the session holds `F:\\WB_CATIA_Gripper\\P01.CATPart`
and the caller wants the instance to reference a byte copy elsewhere. Probe 3
showed this build's Product has NO ReplaceComponent / Replace / Relink / Repath
member, so a native re-point is out.

What is left is "insert the new file at the same pose and delete the old
instance". This measures the two halves of that: does `Products.Remove` exist and
work on an instance, and can the 12 placement numbers be read from the old and
written to the new without moving anything.

Nothing here touches the caller's documents: the assembly is built under
F:\\catia_probe_v0380d and closed again.
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0380d"


def say(line: str = "") -> None:
    print(line, flush=True)


def has(obj, name: str) -> bool:
    try:
        getattr(obj, name)
        return True
    except Exception:  # noqa: BLE001
        return False


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
        # two DIFFERENT parts, so a replacement is visible in the tree
        for name, size in (("OLD", 20), ("NEW", 30)):
            docs.execute("catia_new_part",
                         {"name": name,
                          "file_path": os.path.join(ROOT, f"{name}.CATPart")})
            sketcher.execute("catia_create_sketch", {"plane": "xy"})
            sketcher.execute("catia_sketch_rectangle",
                             {"x1": 0, "y1": 0, "x2": size, "y2": size})
            sketcher.execute("catia_close_sketch", {})
            design.execute("catia_pad", {"height": 5})
            docs.execute("catia_save_document", {})

        asm_path = os.path.join(ROOT, "asm.CATProduct")
        docs.execute("catia_new_product", {"name": "ASM", "file_path": asm_path})
        assembly.execute("catia_add_component",
                         {"file_path": os.path.join(ROOT, "OLD.CATPart")})
        assembly.execute("catia_set_component_transform",
                         {"component_name": "OLD.1",
                          "translation": [12.0, 34.0, 56.0],
                          "rotation_rows": [[0, -1, 0], [1, 0, 0], [0, 0, 1]]})

        doc = conn.active_document
        product = conn.product_of(doc)
        products = product.Products
        say(f"\ncomponents: {[str(products.Item(i).Name) for i in range(1, products.Count + 1)]}")

        say("\n1  what Products exposes")
        for candidate in ("Remove", "AddComponent", "AddNewComponent",
                          "Replace", "Item"):
            say(f"   Products.{candidate}: {has(products, candidate)}")

        say("\n2  the old instance's 12 placement numbers")
        old = products.Item(1)
        matrix = assembly._position_components(old.Position)
        say(f"   {[round(value, 4) for value in matrix]}")

        say("\n3  insert the new file, then move it onto that pose")
        assembly.execute("catia_add_component",
                         {"file_path": os.path.join(ROOT, "NEW.CATPart")})
        new = products.Item(products.Count)
        say(f"   inserted: {new.Name} at {[round(v, 3) for v in assembly._position_components(new.Position)][9:12]}")
        reply = assembly.execute("catia_set_component_transform",
                                 {"component_name": str(new.Name),
                                  "translation": [float(v) for v in matrix[9:12]],
                                  "rotation_rows": [[matrix[0], matrix[3], matrix[6]],
                                                    [matrix[1], matrix[4], matrix[7]],
                                                    [matrix[2], matrix[5], matrix[8]]],
                                  "rotation_convention": "matrix"})
        say("   pose: " + str(reply).replace("\n", " ")[:160])
        after = assembly._position_components(new.Position)
        say(f"   new pose: {[round(v, 4) for v in after]}")
        say(f"   matches the old one: {all(abs(a - b) < 1e-6 for a, b in zip(matrix, after))}")

        say("\n4  can the old instance be removed?")
        try:
            products.Remove(old)
            say(f"   removed -> {[str(products.Item(i).Name) for i in range(1, products.Count + 1)]}")
        except Exception as error:  # noqa: BLE001
            say(f"   Products.Remove failed: {str(error).splitlines()[0][:100]}")

        say("\n5  and the product still updates?")
        try:
            product.UpdateObject(product)
            say("   UpdateObject(product) ok")
        except Exception as error:  # noqa: BLE001
            say(f"   UpdateObject failed: {str(error).splitlines()[0][:80]}")

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
