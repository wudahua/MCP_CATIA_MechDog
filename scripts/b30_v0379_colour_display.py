"""0.3.79 probe 5: does a colour actually reach the SCREEN?

Probes 1-4 checked the read-back, which proves the property is stored. The
acceptance screenshot then showed a near-black box and a grey one while the
property said #3CA03C and #2E86AB - so storage is not display, and a colour that
nobody can see is worth nothing for the reason this feature exists.

This measures it: colour one body red in a part document, sample the middle of
the picture, then try each of the refreshes CATIA offers and sample again.
"""
from __future__ import annotations

import os
import shutil
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.colours import parse_colour  # noqa: E402
from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0379f"
SHOT = os.path.join(os.environ.get("TEMP", ROOT), "v0379_red")


def say(line: str = "") -> None:
    print(line, flush=True)


def palette(path: str) -> list[tuple[tuple[int, int, int], int]]:
    """The dominant colours of a screenshot - the only honest display check."""
    from PIL import Image

    image = Image.open(path).convert("RGB")
    counts = Counter(image.getdata())
    return counts.most_common(6)


def shoot(export, label: str) -> list[tuple[tuple[int, int, int], int]]:
    target = f"{SHOT}_{label}"
    reply = export.execute("catia_screenshot", {"file_path": target + ".png"})
    made = [p for p in (target + ".png", target + ".jpg") if os.path.exists(p)]
    if not made:
        say(f"   {label}: no file ({str(reply)[:90]})")
        return []
    top = palette(made[0])
    say(f"   {label}: {os.path.getsize(made[0])} B -> "
        + ", ".join(f"{rgb}x{n}" for rgb, n in top[:4]))
    return top


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    design = PartDesignTools(conn)
    export = ExportTools(conn)
    before = {str(conn.documents.Item(i).Name)
              for i in range(1, conn.documents.Count + 1)}
    say(f"session before: {conn.documents.Count}")

    try:
        docs.execute("catia_new_part",
                     {"name": "RED", "file_path": os.path.join(ROOT, "red.CATPart")})
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 40, "y2": 40})
        sketcher.execute("catia_close_sketch", {})
        design.execute("catia_pad", {"height": 40})
        doc = conn.active_document
        part = conn.get_active_part()
        body = conn.get_active_part_body()
        selection = doc.Selection

        say("\n1  before anything")
        shoot(export, "1_before")

        say("\n2  SetRealColor(220, 30, 30, 0) on the body, no refresh")
        selection.Clear()
        selection.Add(body)
        selection.VisProperties.SetRealColor(*parse_colour("#DC1E1E"), 0)
        say(f"   read back: {selection.VisProperties.GetRealColor()}")
        shoot(export, "2_set")

        say("\n3  part.Update()")
        try:
            part.Update()
            say("   ok")
        except Exception as error:  # noqa: BLE001
            say(f"   failed: {str(error).splitlines()[0][:80]}")
        shoot(export, "3_update")

        say("\n4  Application.Refresh()")
        try:
            conn.app.Refresh()
            say("   ok")
        except Exception as error:  # noqa: BLE001
            say(f"   failed: {str(error).splitlines()[0][:80]}")
        shoot(export, "4_refresh")

        say("\n5  ActiveViewer.Update() + Reframe()")
        try:
            viewer = conn.active_window.ActiveViewer
            viewer.Update()
            viewer.Reframe()
            say("   ok")
        except Exception as error:  # noqa: BLE001
            say(f"   failed: {str(error).splitlines()[0][:80]}")
        shoot(export, "5_viewer")

        say("\n6  the same via the tool, which reports what it saw")
        say("   (a fresh part, to keep this comparable)")
        docs.execute("catia_save_document", {})

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
