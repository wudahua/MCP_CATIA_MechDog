"""What of P1-8 can actually be reached? Resolution, spec tree, crop.

The 2026-11-19 (0.3.150) report asked for a conclusion rather than another
version. So: measure each of the three, and let the reply say which is possible
and what it costs.

  A  does the viewer/window expose ANYTHING about capture size or the tree?
     (member names, so the answer is a list and not an opinion)
  B  is Window.Width/Height writable, and does the capture follow it?
  C  does StartCommand know a spec-tree command? (unknown names raise, so a
     success here would be real)
  D  how big is the client area, and does a crop of it survive a JPEG round trip?
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "catia_probe_v0470")
os.makedirs(ROOT, exist_ok=True)
INTERESTING = ("tree", "axis", "compass", "grid", "crop", "capture", "width",
               "height", "size", "resol", "zoom", "window")


def say(text: str) -> None:
    print(text, flush=True)


def probe_members(label: str, obj) -> None:
    try:
        names = sorted(str(name) for name in obj.__dir__()
                       if not str(name).startswith("_"))
    except Exception as error:  # noqa: BLE001
        say(f"  {label}: could not list members ({str(error).splitlines()[0][:80]})")
        return
    hits = [name for name in names if any(word in name.lower()
                                          for word in INTERESTING)]
    say(f"  {label}: {len(names)} members, interesting: {hits}")


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    exporter = ExportTools(conn)
    say(f"probe v0470 on {conn.app.Name}")
    try:
        while True:
            docs.execute("catia_close_document", {"save": False})
    except Exception:  # noqa: BLE001
        pass

    path = os.path.join(ROOT, "Shot.CATPart")
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": "Shot", "file_path": path})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "S"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 60.0, "y2": 40.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "S", "height": 12.0})

    window = conn.active_window
    say("")
    say("A what the objects expose")
    probe_members("Window", window)
    probe_members("ActiveViewer", window.ActiveViewer)

    say("")
    say("B Window.Width / Height")
    for attribute in ("Width", "Height"):
        try:
            before = getattr(window, attribute)
        except Exception as error:  # noqa: BLE001
            say(f"  {attribute}: unreadable ({str(error).splitlines()[0][:60]})")
            continue
        try:
            setattr(window, attribute, 1280)
            say(f"  {attribute}: {before} -> set 1280 -> reads "
                f"{getattr(window, attribute)}")
        except Exception as error:  # noqa: BLE001
            say(f"  {attribute}: {before}, NOT writable "
                f"({str(error).splitlines()[0][:60]})")

    shot = os.path.join(ROOT, "shot.jpg")
    reply = exporter.execute("catia_screenshot",
                             {"file_path": shot, "width": 1920, "height": 1080})
    say(f"  capture: {' '.join(str(reply).split())[:300]}")

    say("")
    say("C StartCommand for the specification tree")
    for command in ("SpecTree", "Specification Tree", "Tree", "HideSpecTree"):
        try:
            conn.app.StartCommand(command)
            say(f"  StartCommand('{command}'): returned OK")
        except Exception as error:  # noqa: BLE001
            say(f"  StartCommand('{command}'): "
                f"{str(error).splitlines()[0][:70]}")

    say("")
    say("D a crop of the captured frame")
    try:
        from PIL import Image
        with Image.open(shot) as image:
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
