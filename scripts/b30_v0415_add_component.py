"""add_component said what, and does the product hold it?

Seen twice while probing the assembly box: a brand-new product answered
``list_components -> names: []`` immediately after ``add_component(count=2)``, and
the NEXT run of the same script found both instances. An instance count that reads
0 right after a successful insert is the kind of thing a caller cannot debug, so
this script does nothing else: it prints the add reply, the listing, and the
listing again after a re-activate.

    .venv/Scripts/python.exe scripts/b30_v0415_add_component.py
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

ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0415")


def say(line: str = "") -> None:
    print(line, flush=True)


def names_of(assembly) -> list[str]:
    listing = json.loads(assembly.execute("catia_list_components",
                                          {"names_only": True}))
    return listing.get("names") or [entry.get("name")
                                    for entry in listing.get("components", [])]


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    part_path = os.path.join(ROOT, "ADDP.CATPart")
    asm_path = os.path.join(ROOT, "ADDASM.CATProduct")
    for stale in (part_path, asm_path):
        if os.path.exists(stale):
            os.remove(stale)

    conn = CATIAConnection()
    say(conn.connect())
    for index in range(int(conn.documents.Count), 0, -1):
        document = conn.documents.Item(index)
        try:
            full = str(document.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).startswith(os.path.abspath(ROOT)):
            try:
                document.Close()
            except Exception:  # noqa: BLE001
                pass

    documents, sketcher = DocumentTools(conn), SketcherTools(conn)
    part_design, assembly = PartDesignTools(conn), AssemblyTools(conn)

    documents.execute("catia_new_part", {"name": "ADDP", "file_path": part_path,
                                         "overwrite": True})
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 40,
                                                "y2": 20})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})
    say(f"part built, saved={conn.active_document.Saved}")

    documents.execute("catia_new_product", {"name": "ADDASM",
                                            "file_path": asm_path,
                                            "overwrite": True})
    say(f"active after new_product: {conn.active_document.Name}")
    reply = assembly.execute("catia_add_component", {"file_path": part_path,
                                                     "count": 2})
    say("add reply: " + " ".join(str(reply).split())[:400])
    say(f"active after add: {conn.active_document.Name}")
    say(f"names right after add: {names_of(assembly)}")

    product = conn.product_of(conn.active_document)
    say(f"product.Products.Count right after add: {product.Products.Count}")

    say(f"names second list: {names_of(assembly)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
