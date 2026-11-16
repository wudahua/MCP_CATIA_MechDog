"""Does a stored colour ever reach the display?

The acceptance run set two bodies red and green, read both back, and captured a
picture with neither colour in it (its most common pixels were (51,51,101) and
(166,166,200) - the default solid under this lighting). One flat box, five
routes, a pixel count after each, using the viewer's own CaptureToFile so the
capture itself adds no repaint:

  A  Selection.Add(<body>) + SetRealColor, then capture as-is
  B  ... then refresh_display(), capture
  C  ... then part.Update(), capture
  D  Selection.Add(<reference to the body>) + SetRealColor, capture
  E  ... then refresh_display(), capture

    .venv/Scripts/python.exe -u scripts/b30_v0439i_probe_colour_display.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.colours import APPLY, read_rgb  # noqa: E402
from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0439i")


def say(line: str = "") -> None:
    print(line, flush=True)


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    parts = PartDesignTools(conn)
    path = os.path.join(ROOT, "ProbeColourDisplay.CATPart")
    for index in range(int(conn.documents.Count), 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = str(doc.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).lower().startswith(
                os.path.abspath(ROOT).lower()):
            try:
                doc.Close()
            except Exception:  # noqa: BLE001
                pass
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": "ProbeColourDisplay",
                                    "file_path": path})
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 20, "y2": 20})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 10.0})
    part = conn.get_active_part()
    body = conn.get_active_part_body()
    selection = conn.hso
    viewer = conn.active_window.ActiveViewer
    say(f"probe v0.3.138 colour display on {conn.app.Name}")

    def capture(tag: str) -> str:
        from PIL import Image

        file = os.path.join(ROOT, tag + ".jpg")
        if os.path.exists(file):
            os.remove(file)
        viewer.CaptureToFile(5, os.path.normpath(file))
        if not os.path.exists(file):
            return "MISSING"
        with Image.open(file) as image:
            pixels = list(image.convert("RGB").getdata())
        red = sum(1 for r, g, b in pixels if r > 110 and r > 2 * g and r > 2 * b)
        pale = sum(1 for r, g, b in pixels if r > 150 and g > 150 and b > 150)
        return f"red={red} pale={pale}"

    say("  before any colour:            " + capture("D0"))

    say("\n  A. Selection.Add(<body>) + SetRealColor")
    selection.Clear()
    selection.Add(body)
    selection.VisProperties.SetRealColor(200, 40, 40, APPLY)
    say(f"     read back {read_rgb(selection)}")
    selection.Clear()
    say("     capture as-is:             " + capture("D1"))

    say("\n  B. then refresh_display()")
    conn.refresh_display()
    say("     capture:                   " + capture("D2"))

    say("\n  C. then part.Update()")
    try:
        part.Update()
    except Exception as error:  # noqa: BLE001
        say("     (Update failed: " + str(error).splitlines()[0][:60] + ")")
    say("     capture:                   " + capture("D3"))

    say("\n  D. Selection.Add(<Reference to the body>) + SetRealColor")
    reference = part.CreateReferenceFromObject(body)
    selection.Clear()
    try:
        selection.Add(reference)
        selection.VisProperties.SetRealColor(40, 200, 40, APPLY)
        say(f"     read back {read_rgb(selection)}")
        added = True
    except Exception as error:  # noqa: BLE001
        added = False
        say("     refused: " + str(error).splitlines()[0][:80])
    selection.Clear()
    say("     capture as-is:             " + capture("D4"))

    say("\n  E. then refresh_display()")
    conn.refresh_display()
    say("     capture:                   " + capture("D5"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
