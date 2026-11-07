"""0.3.79 probe 8: does a colour show in the NEXT screenshot, or after a repaint?

The acceptance run showed the palette colours on screen after a reopen but not in
the screenshot taken immediately after catia_colorize_assembly. Two candidate
causes: CATIA repaints lazily, or the just-created product's window had not been
drawn yet. This measures the gap directly - colour, shoot, nudge the viewer,
shoot again - on the same assembly the acceptance uses (two parts at the origin,
so the small one sits inside the big one).
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

ROOT = r"F:\catia_probe_v0379i"
SHOT = os.path.join(os.environ.get("TEMP", ROOT), "v0379_lag")


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
    top = counts.most_common(4)
    wanted = (parse_colour("#E4572E"), parse_colour("#2E86AB"))
    hit = [sum((a - b) ** 2 for a, b in zip(value, rgb)) <= 40 ** 2
           for value, _ in top for rgb in wanted]
    say(f"   {label}: " + ", ".join(f"{rgb}x{n}" for rgb, n in top)
        + f"  -> palette visible: {any(hit)}")


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
        for index, size in ((1, 20), (2, 12)):
            docs.execute("catia_new_part",
                         {"name": f"S{index}",
                          "file_path": os.path.join(ROOT, f"s{index}.CATPart")})
            sketcher.execute("catia_create_sketch", {"plane": "xy"})
            sketcher.execute("catia_sketch_rectangle",
                             {"x1": 0, "y1": 0, "x2": size, "y2": size})
            sketcher.execute("catia_close_sketch", {})
            design.execute("catia_pad", {"height": size})
            docs.execute("catia_save_document", {})
        docs.execute("catia_new_product",
                     {"name": "ASM", "file_path": os.path.join(ROOT, "asm.CATProduct")})
        assembly.execute("catia_add_component",
                         {"file_path": os.path.join(ROOT, "s1.CATPart")})
        assembly.execute("catia_add_component",
                         {"file_path": os.path.join(ROOT, "s2.CATPart")})

        say("\n1  colourise, shoot immediately")
        say("   " + colour.execute("catia_colorize_assembly", {}).splitlines()[0])
        shoot(export, "1_now")

        say("\n2  ActiveViewer.Update() + Reframe(), shoot again")
        try:
            viewer = conn.active_window.ActiveViewer
            viewer.Update()
            viewer.Reframe()
            say("   ok")
        except Exception as error:  # noqa: BLE001
            say(f"   failed: {str(error).splitlines()[0][:80]}")
        shoot(export, "2_viewer")

        say("\n3  activate the document again, shoot")
        conn.active_document.Activate()
        shoot(export, "3_activate")

        say("\n4  one more colour write, shoot")
        colour.execute("catia_set_color", {"target": "S1.1", "color": "#E4572E"})
        shoot(export, "4_write")

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
