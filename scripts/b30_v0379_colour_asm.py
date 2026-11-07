"""0.3.79 probe 6: does an INSTANCE colour reach the screen?

Probe 5 showed a part-level colour appear immediately: the body went from the
grey (166,166,200) to the red (174,24,25) with no refresh at all. So the write
works and the display follows it.

The acceptance screenshot of an ASSEMBLY then showed a near-black box and a grey
one where the properties said green and blue. This measures the assembly case
the same way - build it, colour it, look at the pixels - and then tries the
part-level route while the product is open, to see which one the picture follows.
"""
from __future__ import annotations

import os
import shutil
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.colours import parse_colour  # noqa: E402
from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.appearance import AppearanceTools  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0379g"
SHOT = os.path.join(os.environ.get("TEMP", ROOT), "v0379_asm")


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

    counts = Counter(Image.open(made[0]).convert("RGB").get_flattened_data()
                     if hasattr(Image.open(made[0]), "get_flattened_data")
                     else Image.open(made[0]).convert("RGB").getdata())
    say(f"   {label}: " + ", ".join(f"{rgb}x{n}" for rgb, n in counts.most_common(5)))


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    design = PartDesignTools(conn)
    assembly = AssemblyTools(conn)
    colour = AppearanceTools(conn)
    export = ExportTools(conn)
    before = {str(conn.documents.Item(i).Name)
              for i in range(1, conn.documents.Count + 1)}
    say(f"session before: {conn.documents.Count}")

    try:
        for index, size in ((1, 40), (2, 20)):
            docs.execute("catia_new_part",
                         {"name": f"Q{index}",
                          "file_path": os.path.join(ROOT, f"q{index}.CATPart")})
            sketcher.execute("catia_create_sketch", {"plane": "xy"})
            sketcher.execute("catia_sketch_rectangle",
                             {"x1": 0, "y1": 0, "x2": size, "y2": size})
            sketcher.execute("catia_close_sketch", {})
            design.execute("catia_pad", {"height": size})
            docs.execute("catia_save_document", {})
        asm = os.path.join(ROOT, "asm.CATProduct")
        docs.execute("catia_new_product", {"name": "ASM", "file_path": asm})
        assembly.execute("catia_add_component",
                         {"file_path": os.path.join(ROOT, "q1.CATPart")})
        assembly.execute("catia_add_component",
                         {"file_path": os.path.join(ROOT, "q2.CATPart")})
        # Move the small one clear of the big one, so both are visible.
        assembly.execute("catia_set_component_transform",
                         {"component_name": "Q2.1",
                          "translation": [60.0, 0.0, 0.0]})
        docs.execute("catia_save_document", {})

        say("\n1  no colour")
        shoot(export, "1_plain")

        say("\n2  instance colours via catia_set_color (red + blue)")
        colour.execute("catia_set_color", {"target": "Q1.1", "color": "#DC1E1E"})
        colour.execute("catia_set_color", {"target": "Q2.1", "color": "#1E1EDC"})
        say("   read back: " + colour.execute("catia_get_color", {}).replace("\n", " | "))
        shoot(export, "2_instance")

        say("\n3  ActiveViewer.Update() after that")
        try:
            viewer = conn.active_window.ActiveViewer
            viewer.Update()
        except Exception as error:  # noqa: BLE001
            say(f"   failed: {str(error).splitlines()[0][:70]}")
        shoot(export, "3_viewer")

        say("\n4  part-level colour while the product is open")
        for name, hexcode in (("q1.CATPart", "#1EDC1E"), ("q2.CATPart", "#DCDC1E")):
            for index in range(1, conn.documents.Count + 1):
                doc = conn.documents.Item(index)
                if str(doc.Name) == name:
                    doc.Activate()
                    break
            reply = colour.execute("catia_set_color", {"color": hexcode})
            say(f"   {name}: {reply.splitlines()[0][:100]}")
            docs.execute("catia_save_document", {})
        for index in range(1, conn.documents.Count + 1):
            doc = conn.documents.Item(index)
            if str(doc.Name) == "asm.CATProduct":
                doc.Activate()
                break
        shoot(export, "4_partlevel")

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
