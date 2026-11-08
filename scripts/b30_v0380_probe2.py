"""0.3.80 probe 3: classify a body's hybrid nodes, and hunt for replace / spec-tree.

Probe 2 found that `body.HybridShapes` of a frame sketch holds 点.1 / 点.2 / 点.3 /
Datum_frame, and that probing `.Plane` / `.Point` / `.Line` / `.Surface` on them
returns nothing - so those attribute names are not how this build identifies a
datum element. Everything else it found was blocked by a typelib call that throws
on Product.

So: read each node's OWN type library (the way the Join method names were read in
0.3.77), and ask the Product/Application directly for candidate members by name.
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0380c"


def say(line: str = "") -> None:
    print(line, flush=True)


def funcs(obj) -> list[str]:
    """Function names from the object's own type library, or an error note."""
    try:
        info = obj.GetTypeInfo()
        attr = info.GetTypeAttr()
        names = []
        for index in range(attr.cFuncs):
            try:
                desc = info.GetFuncDesc(index)
                names.append(str(info.GetNames(desc.memid)[0]))
            except Exception:  # noqa: BLE001
                continue
        return names
    except Exception as error:  # noqa: BLE001
        return [f"<{type(error).__name__}: {str(error).splitlines()[0][:50]}>"]


def has(obj, name: str) -> bool:
    try:
        getattr(obj, name)
        return True
    except Exception:  # noqa: BLE001 - absent is the normal answer
        return False


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    before = {str(conn.documents.Item(i).Name)
              for i in range(1, conn.documents.Count + 1)}
    say(f"session before: {conn.documents.Count}")

    try:
        docs.execute("catia_new_part",
                     {"name": "FR", "file_path": os.path.join(ROOT, "fr.CATPart")})
        sketcher.execute("catia_create_sketch", {
            "plane": {"type": "frame", "origin": [20, 0, 20],
                      "u": [1, 0, 0], "v": [0, 0, 1]}})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 20, "y2": 10})
        sketcher.execute("catia_close_sketch", {})
        part = conn.get_active_part()
        body = conn.get_active_part_body()

        say("\n(a) type library of each body hybrid node")
        for index in range(1, body.HybridShapes.Count + 1):
            node = body.HybridShapes.Item(index)
            names = funcs(node)
            say(f"   {node.Name}: {len(names)} -> {names[:12]}")

        say("\n(a2) can a plane be told apart by its own members?")
        plane = body.HybridShapes.Item(body.HybridShapes.Count)
        for candidate in ("Plane", "GetPlane", "Surface", "GetSurface"):
            say(f"   {plane.Name}.{candidate}: {has(plane, candidate)}")

        say("\n(b) does Product expose a replace / re-link member?")
        docs.execute("catia_new_product",
                     {"name": "P", "file_path": os.path.join(ROOT, "p.CATProduct")})
        product_doc = conn.active_document
        product = conn.product_of(product_doc)
        candidates = ("ReplaceComponent", "ReplaceComponentFile", "Replace",
                      "Relink", "Repath", "SetReferenceProduct", "Substitute",
                      "Connections", "ReferenceProduct")
        for candidate in candidates:
            say(f"   Product.{candidate}: {has(product, candidate)}")
        for owner, label in ((product_doc, "ProductDocument"),):
            for candidate in ("ReplaceComponent", "Replace", "Relink"):
                say(f"   {label}.{candidate}: {has(owner, candidate)}")

        say("\n(c) specification tree")
        for owner, label in ((conn.app, "Application"),
                             (conn.active_window, "Window"),
                             (conn.active_window.ActiveViewer, "Viewer")):
            for candidate in ("SpecTreeVisibility", "SpecTreeVisible",
                              "ShowSpecTree", "SpecificationTree",
                              "CaptureToFile", "Hidden3DFaces"):
                if has(owner, candidate):
                    say(f"   {label}.{candidate}: PRESENT")
        say("   candidate command names through StartCommand:")
        for command in ("SpecTreeVisu", "HideSpecTree", "SpecTree", "ToggleSpecTree"):
            try:
                conn.app.StartCommand(command)
                say(f"      {command}: ACCEPTED")
            except Exception as error:  # noqa: BLE001
                say(f"      {command}: refused ({str(error).splitlines()[0][:70]})")

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
