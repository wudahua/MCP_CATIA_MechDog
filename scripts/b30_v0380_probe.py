"""0.3.80 probe 2: three unknowns behind the 2026-11-08 report.

(a) The frame sketch's datum nodes live in body.HybridShapes. set_visibility(kind=
    "construction") hides sketches and the THREE ORIGIN PLANES only - 33 planes for
    11 parts is 11x3 - so those body-level datum elements were never touched. This
    finds out what they expose, to hide exactly the datum ones and leave surfaces.
(b) P1-2 asks for "re-link a component to another file". Does this build's Product
    expose anything of the sort? Enumerated from the type library, not guessed.
(c) P2-1 asks for a screenshot without the specification tree. Does the
    Application expose a command or property for it?
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0380b"


def say(line: str = "") -> None:
    print(line, flush=True)


def members(obj) -> list[str]:
    """Member names from the type library (falls back to dir())."""
    names: list[str] = []
    try:
        info = obj.GetTypeInfo()
        attr = info.GetTypeAttr()
        for index in range(attr.cFuncs):
            desc = info.GetFuncDesc(index)
            try:
                names.append(str(info.GetNames(desc.memid)[0]))
            except Exception:  # noqa: BLE001
                continue
    except Exception as error:  # noqa: BLE001 - not all objects expose one
        say(f"   (no type info: {str(error).splitlines()[0][:60]})")
    return names or [name for name in dir(obj) if not name.startswith("_")]


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

        say("\n(a) a frame sketch: what ends up in body.HybridShapes?")
        sketcher.execute("catia_create_sketch", {
            "plane": {"type": "frame", "origin": [20, 0, 20],
                      "u": [1, 0, 0], "v": [0, 0, 1]}})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 20, "y2": 10})
        sketcher.execute("catia_close_sketch", {})
        part = conn.get_active_part()
        body = conn.get_active_part_body()
        try:
            hybrids = body.HybridShapes
            for index in range(1, hybrids.Count + 1):
                node = hybrids.Item(index)
                probes = []
                for attribute in ("Plane", "Point", "Line", "Curve", "Surface"):
                    try:
                        getattr(node, attribute)
                        probes.append(attribute)
                    except Exception:  # noqa: BLE001
                        continue
                say(f"   {index} {node.Name}: {probes or '<none>'}")
        except Exception as error:  # noqa: BLE001
            say(f"   body.HybridShapes failed: {error}")

        say("\n   origin planes of the part:")
        origin = part.OriginElements
        for name in ("PlaneXY", "PlaneYZ", "PlaneZX"):
            try:
                origin_plane = getattr(origin, name)
                say(f"   {name} ok")
            except Exception as error:  # noqa: BLE001
                say(f"   {name}: {str(error).splitlines()[0][:60]}")

        say("\n(b) Product members mentioning replace / re-path")
        docs.execute("catia_new_product",
                     {"name": "P", "file_path": os.path.join(ROOT, "p.CATProduct")})
        product_doc = conn.active_document
        product = conn.product_of(product_doc)
        for owner, label in ((product, "Product"),
                             (product.Products, "Products"),
                             (product_doc, "ProductDocument")):
            names = members(owner)
            interesting = [name for name in names
                           if any(key in name.lower()
                                  for key in ("repl", "repath", "replace",
                                              "recod", "relink", "reroute",
                                              "substitute"))]
            say(f"   {label}: {len(names)} members; matches: {interesting}")

        say("\n(c) Application / Viewer members mentioning spec tree")
        for owner, label in ((conn.app, "Application"),
                             (conn.active_window, "Window"),
                             (conn.active_window.ActiveViewer, "Viewer")):
            try:
                names = members(owner)
            except Exception as error:  # noqa: BLE001
                say(f"   {label}: unavailable ({str(error).splitlines()[0][:50]})")
                continue
            interesting = [name for name in names
                           if any(key in name.lower()
                                  for key in ("spec", "tree", "conc", "capture"))]
            say(f"   {label}: {len(names)} members; matches: {interesting}")

        say("\n   StartCommand probes (accepted / refused):")
        for command in ("SpecTreeVisu", "HideSpecTree", "SpecificationTree",
                        "View.SpecTree", "TreeVisible"):
            try:
                conn.app.StartCommand(command)
                say(f"   {command}: ACCEPTED")
            except Exception as error:  # noqa: BLE001
                say(f"   {command}: refused ({str(error).splitlines()[0][:60]})")

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
