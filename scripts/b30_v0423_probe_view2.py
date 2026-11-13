"""Is the picture taken with ProjectionMode=1 actually axis-aligned?

The read-back of GetSightDirection/GetUpDirection already proves the Put was
accepted: after set_view the sight reads exactly (0,1,0) and up (0,0,1). So the
tilt the two reports describe cannot come from the direction vectors - the
remaining suspect is ProjectionMode, which reads 0 on this install (a 60x40x4
plate therefore shows its TOP and BOTTOM faces at once, which is what "the
bottom of the bottle is visible" looks like).

One fresh document, one plate + one tower at +X, the SAME sight/up, two
captures that differ only in ProjectionMode.

    .venv/Scripts/python.exe scripts/b30_v0423_probe_view2.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0423")
ROOT = os.path.join(_PROBE_ROOT, "view2")


def say(line: str = "") -> None:
    print(line, flush=True)


def close_ours(conn: CATIAConnection) -> None:
    for index in range(int(conn.documents.Count), 0, -1):
        document = conn.documents.Item(index)
        try:
            full = str(document.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).startswith(os.path.abspath(_PROBE_ROOT)):
            try:
                document.Close()
                say(f"  closed {os.path.basename(full)}")
            except Exception:  # noqa: BLE001
                pass


def activate(conn: CATIAConnection, name: str) -> None:
    for index in range(1, conn.documents.Count + 1):
        if conn.documents.Item(index).Name == name:
            conn.documents.Item(index).Activate()
            return
    raise RuntimeError(f"{name} is not open")


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)

    path = os.path.join(ROOT, "ViewTest.CATPart")
    if os.path.exists(path):
        os.remove(path)
    say(documents.execute("catia_new_part",
                          {"name": "ViewTest", "file_path": path,
                           "overwrite": True})[:120])
    activate(conn, "ViewTest.CATPart")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 60, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    say(part_design.execute("catia_pad", {"height": 4})[:90])
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 50, "y1": 10, "x2": 58, "y2": 18})
    sketcher.execute("catia_close_sketch", {})
    say(part_design.execute("catia_pad", {"height": 26})[:90])
    activate(conn, "ViewTest.CATPart")
    part = conn.get_active_part()
    say(f"  pad count {part.MainBody.Shapes.Count}, volume "
        f"{part_design._body_volume_mm3(part, part.MainBody):.1f} mm3")

    viewer = conn.active_window.ActiveViewer
    viewpoint = viewer.Viewpoint3D
    for mode, tag in ((0, "persp"), (1, "parallel")):
        viewpoint.ProjectionMode = mode
        viewpoint.PutSightDirection([0, 1, 0])
        viewpoint.PutUpDirection([0, 0, 1])
        viewer.Reframe()
        say(f"  mode asked {mode} reads {viewpoint.ProjectionMode}; "
            f"caption {conn.active_window.Caption}")
        shot = os.path.join(ROOT, f"front_{tag}.jpg")
        viewer.CaptureToFile(5, os.path.normpath(shot))
        say(f"  wrote {shot} ({os.path.getsize(shot)} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
