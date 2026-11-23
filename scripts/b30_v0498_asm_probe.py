"""B30 probe for 0.3.161 - can this build READ or SET a component's fixed state?

The 2026-11-23 report (P1-5) needs three things it cannot do: check whether an
instance is fixed, fix one, and hide one by instance name (P2-1, P2-2's precise
box is separate). Everything here is a MEMBER question, so it is answered by
calling each candidate and printing what it says - not by reading a manual.

  1  Product / instance members: Visibility, IsFixed, Move.IsFixed, ...
  2  Selection.Add(instance) + VisProperties.SetShow(1): does it hide the instance
  3  Selection.Add(instance) + VisProperties.SetShow(0): does it come back
  4  what catia_list_components currently reports per instance
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402


def say(text: str = "") -> None:
    print(text, flush=True)


def payload(reply):
    return reply if isinstance(reply, (dict, list)) else json.loads(str(reply))


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sk = SketcherTools(conn)
    design = PartDesignTools(conn)
    assembly = AssemblyTools(conn)

    root = os.path.join(tempfile.gettempdir(), "p0498asm")
    os.makedirs(root, exist_ok=True)

    def fresh(name):
        path = os.path.join(root, name)
        try:
            docs.execute("catia_close_document", {"document": path, "save": False})
        except Exception:  # noqa: BLE001
            pass
        if os.path.isfile(path):
            os.remove(path)
        return path

    part_path = fresh("P0498.CATPart")
    docs.execute("catia_new_part", {"name": "P0498", "file_path": part_path})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "S"})
    sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 40, "y2": 20})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"sketch_name": "S", "height": 10})
    docs.execute("catia_save_document", {})

    asm_path = fresh("A0498.CATProduct")
    docs.execute("catia_new_product", {"name": "A0498", "file_path": asm_path})
    product = conn.app.ActiveDocument.Product
    for _ in range(2):
        assembly.execute("catia_add_component", {"file_path": part_path})
    product = conn.app.ActiveDocument.Product
    docs.execute("catia_save_document", {})
    say(f"  assembly {conn.app.ActiveDocument.Name}: "
        f"{product.Products.Count} instance(s)")

    listed = payload(assembly.execute("catia_list_components", {}))
    say("  catia_list_components -> "
        + json.dumps(listed.get("components") or listed, ensure_ascii=False)[:500])

    def instance(index: int = 1):
        return product.Products.Item(index)

    item = instance(1)
    name = str(item.Name)
    say(f"\n== 1 members of an instance ('{name}')")
    for member in ("Name", "PartNumber", "Visibility", "IsFixed", "IsInactive",
                   "IsRoot", "IsReference", "Move", "Position", "ReferenceProduct",
                   "Products", "Children", "Constraints", "Definition"):
        try:
            value = getattr(item, member)
            shown = str(value)[:80]
            say(f"  {member:18s} -> {type(value).__name__} {shown}")
        except Exception as error:  # noqa: BLE001 - the answer IS the probe
            say(f"  {member:18s} -> RAISES {str(error).splitlines()[0][:70]}")

    for member in ("IsFixed", "IsFixed2"):
        try:
            move = item.Move
            say(f"  Move.{member:12s} -> {getattr(move, member)}")
        except Exception as error:  # noqa: BLE001
            say(f"  Move.{member:12s} -> RAISES {str(error).splitlines()[0][:70]}")

    say("\n== 2 Selection.Add(instance) + VisProperties.SetShow(1)")
    selection = conn.hso
    try:
        selection.Clear()
        selection.Add(item)
        selection.VisProperties.SetShow(1)
        say("  accepted")
    except Exception as error:  # noqa: BLE001
        say(f"  RAISES {str(error).splitlines()[0][:120]}")
    finally:
        try:
            selection.Clear()
        except Exception:  # noqa: BLE001
            pass
    try:
        say(f"  instance.Visibility after hide -> {item.Visibility}")
    except Exception as error:  # noqa: BLE001
        say(f"  instance.Visibility -> RAISES {str(error).splitlines()[0][:70]}")

    say("\n== 3 the same with SetShow(0)")
    try:
        selection.Clear()
        selection.Add(item)
        selection.VisProperties.SetShow(0)
        say("  accepted")
    except Exception as error:  # noqa: BLE001
        say(f"  RAISES {str(error).splitlines()[0][:120]}")
    finally:
        try:
            selection.Clear()
        except Exception:  # noqa: BLE001
            pass
    try:
        say(f"  instance.Visibility after show -> {item.Visibility}")
    except Exception as error:  # noqa: BLE001
        say(f"  instance.Visibility -> RAISES {str(error).splitlines()[0][:70]}")

    say("\n== 4 the root product's own state")
    for member in ("Name", "IsRoot", "Visibility", "Move"):
        try:
            value = getattr(product, member)
            say(f"  root.{member:12s} -> {type(value).__name__} {str(value)[:60]}")
        except Exception as error:  # noqa: BLE001
            say(f"  root.{member:12s} -> RAISES {str(error).splitlines()[0][:70]}")
    try:
        move = product.Move
        say(f"  root.Move.IsFixed -> {move.IsFixed}")
    except Exception as error:  # noqa: BLE001
        say(f"  root.Move.IsFixed -> RAISES {str(error).splitlines()[0][:70]}")

    docs.execute("catia_close_document", {"document": asm_path, "save": False})
    docs.execute("catia_close_document", {"document": part_path, "save": False})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
