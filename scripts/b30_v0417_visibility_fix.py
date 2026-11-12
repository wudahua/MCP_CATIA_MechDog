"""The visibility traphole, after the 0.3.115 fix: four frames, in order.

Frame 1  loft built                      solid on screen
Frame 2  GSD_Set hidden                  the solid leaves with it (the report)
Frame 3  '零件几何体' show=true          the solid comes BACK (0.3.115: showing a
                                         body now shows the features in it)
Frame 4  kind='construction' hidden      solid still on screen, without the
         + clean=true                    white frames and the tree

Every frame is written to disk, so "did it come back" is a picture and not a
sentence. Sizes are printed because a blank viewport compresses small.

    .venv/Scripts/python.exe scripts/b30_v0417_visibility_fix.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0417")
ROOT = os.path.join(_PROBE_ROOT, "visible_fix")


def say(line: str = "") -> None:
    print(line, flush=True)


def activate(conn: CATIAConnection, name: str) -> None:
    for index in range(1, conn.documents.Count + 1):
        candidate = conn.documents.Item(index)
        if candidate.Name == name:
            candidate.Activate()
            if conn.active_document.Name != name:
                raise RuntimeError(f"could not activate {name}")
            return
    raise RuntimeError(f"{name} is not open")


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
            except Exception:  # noqa: BLE001
                pass


def shot(exporter, tag: str, clean: bool = False) -> int:
    path = os.path.join(ROOT, f"{tag}.jpg")
    if os.path.exists(path):
        os.remove(path)
    try:
        exporter.execute("catia_screenshot", {"file_path": path, "clean": clean})
    except Exception as error:  # noqa: BLE001
        say(f"  {tag:26} FAILED {str(error).splitlines()[0][:90]}")
        return -1
    size = os.path.getsize(path) if os.path.exists(path) else -1
    say(f"  {tag:26} {size} bytes  {path}")
    return size


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)
    documents = DocumentTools(conn)
    gsd, part_design = GSDTools(conn), PartDesignTools(conn)
    exporter = ExportTools(conn)

    path = os.path.join(ROOT, "VisFix.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part", {"name": "VisFix", "file_path": path,
                                         "overwrite": True})
    activate(conn, "VisFix.CATPart")
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "radius": 20,
                                     "support_plane": "xy", "name": "S1"})
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 30], "radius": 8,
                                     "support_plane": "xy", "offset": 30,
                                     "name": "S2"})
    loft = " ".join(str(part_design.execute(
        "catia_solid_loft", {"sections": ["S1", "S2"]})).split())
    say("loft: " + loft[:150])

    say("")
    say("frame 1: built")
    shot(exporter, "1_built")

    say("")
    say("frame 2: GSD_Set hidden")
    reply = " ".join(str(part_design.execute(
        "catia_set_visibility", {"name": "GSD_Set", "show": False})).split())
    say("  reply: " + reply[:400])
    shot(exporter, "2_set_hidden")

    say("")
    say("frame 3: the body asked to be shown")
    reply = " ".join(str(part_design.execute(
        "catia_set_visibility", {"name": "零件几何体", "show": True})).split())
    say("  reply: " + reply[:300])
    shot(exporter, "3_body_shown")

    say("")
    say("frame 4: construction hidden, then clean=true")
    reply = " ".join(str(part_design.execute(
        "catia_set_visibility", {"kind": "construction", "show": False})).split())
    say("  reply: " + reply[:220])
    shot(exporter, "4_construction_hidden")
    shot(exporter, "5_construction_hidden_clean", clean=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
