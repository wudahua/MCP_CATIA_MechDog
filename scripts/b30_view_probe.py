"""Is catia_set_view actually putting the camera where it says? (2026-11-03)

The 0.3.49 acceptance report says `set_view('bottom')` still shows an upright
part, that `set_view('top')` merely flips it, and that `width`/`height` are
ignored (900x900 asked, 778x744 written). Round 1 of 0.3.49 only checked that
three views produced three different md5s, which proves nothing about DIRECTION.

This probe builds one obviously asymmetric part (a flat plate with a tower on
its +Z face, offset to +X), then for each of the standard views:

    * reads back viewpoint.SightDirection / UpDirection AFTER the call,
    * writes a screenshot,
    * records the file's real pixel size.

The read-back is the honest test: if it matches the request and the picture
still looks wrong, the camera is fine and the capture is not; if the read-back
disagrees with the request, PutSightDirection is not doing what its name says.
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

OUT = os.path.join(tempfile.gettempdir(), "b30_view")
os.makedirs(OUT, exist_ok=True)


def say(line: str = "") -> None:
    print(line, flush=True)


def jpeg_size(path: str) -> str:
    """Width x height from the JPEG SOF marker (no PIL needed)."""
    with open(path, "rb") as handle:
        data = handle.read()
    index = 2
    while index < len(data) - 9:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            height, width = struct.unpack(">HH", data[index + 5:index + 9])
            return f"{width}x{height}"
        length = struct.unpack(">H", data[index + 2:index + 4])[0]
        index += 2 + length
    return "unknown"


def main() -> int:
    conn = CATIAConnection()
    say(conn.connect())
    document = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    gsd = GSDTools(conn)
    export = ExportTools(conn)

    names = []
    for index in range(1, conn.documents.Count + 1):
        try:
            names.append(str(conn.documents.Item(index).Name))
        except Exception:  # noqa: BLE001
            pass
    for name in [n for n in names if n.startswith("VIEW")]:
        for index in range(1, conn.documents.Count + 1):
            if str(conn.documents.Item(index).Name) == name:
                conn.documents.Item(index).Activate()
                document.execute("catia_close_document", {"save": False})
                break

    document.execute("catia_new_part",
                     {"name": "VIEW", "file_path": os.path.join(OUT, "VIEW.CATPart")})
    conn.documents.Item(1).Activate()
    say(f"active: {conn.active_document.Name}")

    # A plate 40 (x) by 10 (y) by 4 (z), with a 4 x 4 tower on its +Z face out
    # at +X: every one of the six standard views looks different from every
    # other, so a wrong camera cannot hide behind symmetry.
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 40, "y2": 10})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 4.0})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 4.0})
    sketcher.execute("catia_sketch_rectangle", {"x1": 30, "y1": 2, "x2": 34, "y2": 6})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 30.0})

    viewer = conn.active_window.ActiveViewer
    for view in ("front", "back", "left", "right", "top", "bottom", "isometric"):
        reply = export.execute("catia_set_view", {"view": view})
        export.execute("catia_fit_all", {})
        viewpoint = viewer.Viewpoint3D
        try:
            sight = [round(float(v), 3) for v in viewpoint.SightDirection]
        except Exception as error:  # noqa: BLE001
            sight = f"n/a ({str(error).splitlines()[0][:40]})"
        try:
            up = [round(float(v), 3) for v in viewpoint.UpDirection]
        except Exception as error:  # noqa: BLE001
            up = f"n/a ({str(error).splitlines()[0][:40]})"
        path = os.path.join(OUT, f"view_{view}.jpg")
        if os.path.exists(path):
            os.remove(path)
        export.execute("catia_screenshot", {"file_path": path})
        say(f"{view:<9}: sight {sight}  up {up}  image {jpeg_size(path)}  "
            f"({os.path.getsize(path)} B)  reply '{reply}'")

    # Does the width/height argument survive into the file?
    path = os.path.join(OUT, "view_sized.jpg")
    if os.path.exists(path):
        os.remove(path)
    export.execute("catia_screenshot",
                   {"file_path": path, "width": 900, "height": 900})
    say(f"sized 900x900 request: image {jpeg_size(path)}")

    # Can the sight direction be assigned directly, and does it stick?
    viewpoint = viewer.Viewpoint3D
    for attempt in ("property", "put"):
        try:
            if attempt == "property":
                viewpoint.SightDirection = (0, 1, 0)
            else:
                viewpoint.PutSightDirection([0, 1, 0])
            viewer.Reframe()
            read_back = [round(float(v), 3) for v in viewpoint.SightDirection]
            say(f"{attempt:<8}: accepted, reads back {read_back}")
        except Exception as error:  # noqa: BLE001
            say(f"{attempt:<8}: {str(error).splitlines()[0][:100]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
