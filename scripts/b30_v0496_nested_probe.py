"""P2-23: which states make `catia_close_document` blame a modal dialog?

The first probe (b30_v0496_probe.py) reproduced the DIRECT case and it worked:
`component_usage()` named the holder and the reply was "STILL OPEN ... not a
failure". So the reporter's state is one of the neighbours, and guessing which
is how the wrong fix gets written. This measures the neighbours:

  1  a part held by a NESTED product (assembly B > assembly A > part)
  2  `_window_state()` for each state - the close path does not read it today
  3  a part opened in the BACKGROUND (`open_document(activate=false)`): does it
     have a window? If it does, "no window" really does mean "held by a product"
     and that reading can carry the diagnosis.

Everything created here is closed; leftovers are reported.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402


def say(text: str) -> None:
    print(text, flush=True)


def short(value, limit: int = 400) -> str:
    return " ".join(str(value).split())[:limit]


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    design = PartDesignTools(conn)
    assembly = AssemblyTools(conn)

    root = os.path.join(tempfile.gettempdir(), "probe0496b")
    os.makedirs(root, exist_ok=True)
    part_path = os.path.join(root, "Nest23.CATPart")
    inner_path = os.path.join(root, "Inner23.CATProduct")
    outer_path = os.path.join(root, "Outer23.CATProduct")

    def close_quietly(*paths: str) -> None:
        for path in paths:
            try:
                say(f"    close({os.path.basename(path)}): "
                    f"{short(docs.execute('catia_close_document', {'document': path, 'save': False}), 130)}")
            except Exception as exc:  # noqa: BLE001
                say(f"    close({os.path.basename(path)}) refused: {short(exc, 130)}")

    def leftovers() -> list[str]:
        listing = json.loads(docs.execute("catia_list_documents", {}))
        items = listing.get("documents") if isinstance(listing, dict) else listing
        return [item.get("name") for item in items or []
                if "probe0496" in (item.get("path") or "").casefold()]

    try:
        for stale in (outer_path, inner_path, part_path):
            if os.path.isfile(stale):
                os.remove(stale)
        for path in list(leftovers()):
            try:
                docs.execute("catia_close_document", {"document": path, "save": False})
            except Exception:  # noqa: BLE001
                pass

        say("== setup: part -> inner product -> outer product (two levels)")
        docs.execute("catia_new_part", {"name": "Nest23", "file_path": part_path})
        sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SQ"})
        sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 50, "y2": 30})
        sketcher.execute("catia_close_sketch", {})
        design.execute("catia_pad", {"sketch_name": "SQ", "height": 10})
        docs.execute("catia_save_document", {})
        say("  " + short(docs.execute("catia_new_product",
                                      {"name": "Inner23", "file_path": inner_path}), 100))
        say("  " + short(assembly.execute("catia_add_component",
                                          {"file_path": part_path}), 110))
        docs.execute("catia_save_document", {})
        say("  " + short(docs.execute("catia_new_product",
                                      {"name": "Outer23", "file_path": outer_path}), 100))
        say("  " + short(assembly.execute("catia_add_component",
                                          {"file_path": inner_path}), 130))
        docs.execute("catia_save_document", {})

        # The part is now two levels down: outer > inner > part.
        docs.execute("catia_activate_document", {"name": "Nest23.CATPart"})

        say("\n== 1. what the close path can see")
        usage = conn.component_usage()
        mine = {k: v for k, v in usage.items() if "nest23" in k.casefold()}
        say(f"  component_usage() for the part: {json.dumps(mine, ensure_ascii=False)}")
        def child_names(document_name: str) -> list[str]:
            try:
                doc = [conn.documents.Item(i)
                       for i in range(1, conn.documents.Count + 1)
                       if str(conn.documents.Item(i).Name) == document_name][0]
                product = conn.product_of(doc)
                return [str(product.Products.Item(j).Name)
                        for j in range(1, int(product.Products.Count) + 1)]
            except Exception as exc:  # noqa: BLE001
                return [f"<unreadable: {short(exc, 80)}>"]

        say(f"  Outer23 children (top level only): {child_names('Outer23.CATProduct')}")
        say(f"  Inner23 children: {child_names('Inner23.CATProduct')}")

        say(f"  _window_state(Nest23.CATPart) = {docs._window_state('Nest23.CATPart')}")
        say(f"  _window_state(Inner23.CATProduct) = {docs._window_state('Inner23.CATProduct')}")

        say("\n== 2. close the nested part, exactly as the report did")
        for save in (False, True):
            try:
                reply = docs.execute("catia_close_document",
                                     {"document": part_path, "save": save})
                say(f"  save={save} -> {short(reply, 460)}")
            except Exception as exc:  # noqa: BLE001
                say(f"  save={save} raised {type(exc).__name__}: {short(exc, 460)}")

        say("\n== 3. a part opened in the BACKGROUND - does it have a window?")
        docs.execute("catia_activate_document", {"name": "Outer23.CATProduct"})
        docs.execute("catia_close_document", {"document": outer_path, "save": False})
        docs.execute("catia_close_document", {"document": inner_path, "save": False})
        say(f"  after closing the products, the part is gone too: "
            f"{leftovers()}")
        docs.execute("catia_open_document", {"file_path": part_path, "activate": False})
        say(f"  active is {short(conn.active_document.Name, 60)}")
        say(f"  _window_state(Nest23.CATPart) after open(activate=false) = "
            f"{docs._window_state('Nest23.CATPart')}")
        say(f"  component_usage() now = "
            f"{json.dumps({k: v for k, v in conn.component_usage().items() if 'nest23' in k.casefold()}, ensure_ascii=False)}")

        say("\n== 4. the state the report was in: open ONLY the assembly from disk")
        # The part file is on disk and nothing holds it. Opening the OUTER
        # product loads inner + part in context - and the part never had a
        # window of its own in this session, which is what the reporter's
        # activate refusal said ("no window of its own").
        docs.execute("catia_open_document", {"file_path": outer_path})
        say(f"  active is {short(conn.active_document.Name, 60)}")
        say(f"  open documents: {leftovers()}")
        say(f"  _window_state(Nest23.CATPart) = {docs._window_state('Nest23.CATPart')}")
        mine = {k: v for k, v in conn.component_usage().items()
                if "nest23" in k.casefold()}
        say(f"  component_usage() for the part: {json.dumps(mine, ensure_ascii=False)}")
        for save in (False, True):
            try:
                reply = docs.execute("catia_close_document",
                                     {"document": part_path, "save": save})
                say(f"  save={save} -> {short(reply, 460)}")
            except Exception as exc:  # noqa: BLE001
                say(f"  save={save} raised {type(exc).__name__}: {short(exc, 460)}")

        say("\n== cleanup")
        close_quietly(part_path)
        say(f"  left open: {leftovers()}")
        return 0
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        say(f"  left open: {leftovers()}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
