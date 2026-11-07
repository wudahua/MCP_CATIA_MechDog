"""0.3.79 probe 7: what makes an INSTANCE colour render?

Probe 6 settled two things on B30:

* a part-level colour (the body, in its own document) shows immediately - the
  solid went from (166,166,200) to the green (24,174,25) with no refresh;
* an instance colour set through the assembly's Selection reads back exactly
  (#DC1E1E / #1E1EDC) and the picture does not change AT ALL - same histogram
  before, after, and after ActiveViewer.Update().

So the four-argument SetRealColor stores something that the viewer ignores for a
component. The fourth argument is the only free variable left (three are refused
outright), so this tries every plausible value and looks at the pixels, and it
also colours `component.ReferenceProduct` - the part node inside the assembly -
to see whether THAT renders.
"""
from __future__ import annotations

import os
import shutil
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0379h"
SHOT = os.path.join(os.environ.get("TEMP", ROOT), "v0379_flag")


def say(line: str = "") -> None:
    print(line, flush=True)


def shoot(export, label: str) -> None:
    target = f"{SHOT}_{label}"
    export.execute("catia_screenshot", {"file_path": target + ".png"})
    made = [p for p in (target + ".png", target + ".jpg") if os.path.exists(p)]
    if not made:
        say(f"   {label}: NO FILE")
        return
    from PIL import Image

    counts = Counter(Image.open(made[0]).convert("RGB").getdata())
    say(f"   {label}: " + ", ".join(f"{rgb}x{n}" for rgb, n in counts.most_common(4)))


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    design = PartDesignTools(conn)
    assembly = AssemblyTools(conn)
    export = ExportTools(conn)
    before = {str(conn.documents.Item(i).Name)
              for i in range(1, conn.documents.Count + 1)}
    say(f"session before: {conn.documents.Count}")

    try:
        for index, size in ((1, 40), (2, 20)):
            docs.execute("catia_new_part",
                         {"name": f"R{index}",
                          "file_path": os.path.join(ROOT, f"r{index}.CATPart")})
            sketcher.execute("catia_create_sketch", {"plane": "xy"})
            sketcher.execute("catia_sketch_rectangle",
                             {"x1": 0, "y1": 0, "x2": size, "y2": size})
            sketcher.execute("catia_close_sketch", {})
            design.execute("catia_pad", {"height": size})
            docs.execute("catia_save_document", {})
        docs.execute("catia_new_product",
                     {"name": "ASM", "file_path": os.path.join(ROOT, "asm.CATProduct")})
        assembly.execute("catia_add_component",
                         {"file_path": os.path.join(ROOT, "r1.CATPart")})
        assembly.execute("catia_add_component",
                         {"file_path": os.path.join(ROOT, "r2.CATPart")})
        assembly.execute("catia_set_component_transform",
                         {"component_name": "R2.1",
                          "translation": [60.0, 0.0, 0.0]})
        docs.execute("catia_save_document", {})

        doc = conn.active_document
        product = conn.product_of(doc)
        selection = doc.Selection
        component = product.Products.Item(1)

        say("\n0  baseline")
        shoot(export, "0_plain")

        for flag in (0, 1, 2, 3):
            say(f"\n1  instance SetRealColor(220,30,30,{flag})")
            selection.Clear()
            selection.Add(component)
            try:
                selection.VisProperties.SetRealColor(220, 30, 30, flag)
                say(f"   read back: {tuple(selection.VisProperties.GetRealColor())}")
            except Exception as error:  # noqa: BLE001
                say(f"   refused: {str(error).splitlines()[0][:80]}")
                continue
            shoot(export, f"1_flag{flag}")

        say("\n2  colour component.ReferenceProduct instead (flag 0)")
        selection.Clear()
        selection.Add(component.ReferenceProduct)
        try:
            selection.VisProperties.SetRealColor(30, 220, 30, 0)
            say(f"   read back: {tuple(selection.VisProperties.GetRealColor())}")
        except Exception as error:  # noqa: BLE001
            say(f"   refused: {str(error).splitlines()[0][:80]}")
        shoot(export, "2_reference")

        say("\n3  colour the body through the PART document (flag 0)")
        for index in range(1, conn.documents.Count + 1):
            part_doc = conn.documents.Item(index)
            if str(part_doc.Name) == "r1.CATPart":
                part_doc.Activate()
                break
        body = conn.get_active_part_body()
        selection = conn.active_document.Selection
        selection.Clear()
        selection.Add(body)
        selection.VisProperties.SetRealColor(30, 30, 220, 0)
        say(f"   read back: {tuple(selection.VisProperties.GetRealColor())}")
        docs.execute("catia_save_document", {})
        for index in range(1, conn.documents.Count + 1):
            if str(conn.documents.Item(index).Name) == "asm.CATProduct":
                conn.documents.Item(index).Activate()
                break
        shoot(export, "3_partbody")

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
