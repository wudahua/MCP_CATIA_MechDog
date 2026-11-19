"""Does resizing the CATIA window change the captured resolution?

The 0.3.103 note says no ("Window.Width/Height do not change it"), and probe
v0470 (B30 2026-11-19) says otherwise: setting Width=1280 moved the window from
772 to 1280 and the capture from 759 to 808 px wide. One of those two is a
measurement with a race in it, so measure it properly - three widths, a repaint
before each capture, and the image size read from the file.

  A  capture at three window widths; does the file follow?
  B  is the window maximized (which would make the setter a no-op)?
  C  what does the client area lose to the frame - and does a crop keep its
     pixels through the JPEG round trip?
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "catia_probe_v0471")
os.makedirs(ROOT, exist_ok=True)


def say(text: str) -> None:
    print(text, flush=True)


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    exporter = ExportTools(conn)
    say(f"probe v0471 on {conn.app.Name}")
    try:
        while True:
            docs.execute("catia_close_document", {"save": False})
    except Exception:  # noqa: BLE001
        pass

    path = os.path.join(ROOT, "Shot.CATPart")
    if os.path.exists(path):
        docs.execute("catia_close_document", {"save": False})
    if not os.path.exists(path):
        docs.execute("catia_new_part", {"name": "Shot", "file_path": path})
        sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "S"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0.0, "y1": 0.0, "x2": 60.0, "y2": 40.0})
        sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "S", "height": 12.0})

    window = conn.active_window
    viewer = window.ActiveViewer
    say("")
    say("B window state before anything")
    for attribute in ("Width", "Height", "WindowState", "State", "Left", "Top"):
        try:
            say(f"  {attribute}: {getattr(window, attribute)!r}")
        except Exception as error:  # noqa: BLE001
            say(f"  {attribute}: not readable ({str(error).splitlines()[0][:60]})")

    say("")
    say("A three widths, capture after each")
    from PIL import Image
    for width in (600, 1100, 1600):
        try:
            window.Width = width
            window.Height = 900
        except Exception as error:  # noqa: BLE001
            say(f"  width {width}: refused ({str(error).splitlines()[0][:60]})")
            continue
        # A repaint before the capture: CaptureToFile does not force one.
        try:
            viewer.Update()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.4)
        shot = os.path.join(ROOT, f"w{width}.jpg")
        reply = exporter.execute("catia_screenshot",
                                 {"file_path": shot, "width": width,
                                  "height": 900})
        try:
            with Image.open(shot) as image:
                size = f"{image.size[0]}x{image.size[1]}"
        except Exception as error:  # noqa: BLE001
            size = f"unreadable ({str(error).splitlines()[0][:50]})"
        say(f"  window {getattr(window, 'Width', '?')}x{getattr(window, 'Height', '?')}"
            f" -> file {size}   reply says: "
            + (" ".join(str(reply).split())[:110]))

    say("")
    say("C a crop of the last capture")
    try:
        with Image.open(os.path.join(ROOT, "w1600.jpg")) as image:
            say(f"  captured {image.size[0]}x{image.size[1]} {image.format}")
            box = (0, 0, min(400, image.width), min(300, image.height))
            image.crop(box).save(os.path.join(ROOT, "crop.jpg"))
        with Image.open(os.path.join(ROOT, "crop.jpg")) as cropped:
            say(f"  cropped {cropped.size[0]}x{cropped.size[1]}")
    except Exception as error:  # noqa: BLE001
        say(f"  crop failed: {type(error).__name__}: "
            f"{str(error).splitlines()[0][:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
