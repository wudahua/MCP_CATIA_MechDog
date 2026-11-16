"""Why 0.3.126 said "hiding the 3 origin planes changes nothing" and v0439b said
it changes everything (centre light 0.303 -> 0.926).

Same part shape, four captures, one variable at a time:
  1 all shown
  2 planes hidden, NO redraw before the capture
  3 planes hidden, redraw (viewer.Update) then capture
  4 loft part (the 0.3.126 shape) with the same two states

If 2 == 1 and 3 != 1, the old "no effect" was a missed repaint - the same bug
0.3.131 fixed for Zoom, and it means the planes are a real leftover after all.

    .venv/Scripts/python.exe -u scripts/b30_v0439f_probe_planes_redraw.py
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0439f")


def say(line: str = "") -> None:
    print(line, flush=True)


def fresh(conn, docs, name: str):
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, name + ".CATPart")
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
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    return conn.get_active_part()


def capture(conn, name: str, redraw: bool) -> str:
    path = os.path.join(ROOT, name + ".jpg")
    if os.path.exists(path):
        os.remove(path)
    if redraw:
        try:
            conn.refresh_display()
        except Exception:  # noqa: BLE001
            pass
    # CaptureToFile directly, NOT catia_screenshot: the tool calls Update()
    # itself, which would erase the very variable under test.
    viewer = conn.active_window.ActiveViewer
    viewer.CaptureToFile(5, os.path.normpath(path))
    return path if os.path.exists(path) else ""


def digest(path: str) -> str:
    if not path:
        return "MISSING"
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()[:12]


def light(path: str) -> float:
    if not path:
        return -1.0
    from PIL import Image

    with Image.open(path) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        pixels = list(rgb.crop((int(width * .3), int(height * .3),
                                int(width * .7), int(height * .7))).getdata())
        return sum(1 for r, g, b in pixels if (r + g + b) / 3 > 150) / len(pixels)


def set_planes(part, selection, state: int) -> list[str]:
    done = []
    for name in ("PlaneXY", "PlaneYZ", "PlaneZX"):
        try:
            selection.Clear()
            selection.Add(getattr(part.OriginElements, name))
            selection.VisProperties.SetShow(state)
            done.append(name)
        except Exception as error:  # noqa: BLE001
            say(f"    {name} failed: {str(error).splitlines()[0][:50]}")
        selection.Clear()
    return done


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    parts = PartDesignTools(conn)
    say(f"probe v0.3.138 repaint on {conn.app.Name}")

    # ── pad part ───────────────────────────────────────────────────────────
    part = fresh(conn, docs, "ProbeRepaintPad")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 20, "y2": 20})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 10.0})
    selection = conn.hso
    one = capture(conn, "P1_all_shown", redraw=True)
    say(f"  1 all shown                     {digest(one)}  light={light(one):.3f}")
    set_planes(part, selection, 0)
    two = capture(conn, "P2_hidden_noredraw", redraw=False)
    say(f"  2 planes hidden, no redraw      {digest(two)}  light={light(two):.3f}")
    three = capture(conn, "P3_hidden_redraw", redraw=True)
    say(f"  3 planes hidden, redraw         {digest(three)}  light={light(three):.3f}")
    say(f"  -> no-redraw identical to all-shown? "
        f"{digest(one) == digest(two)}")
    say(f"  -> redraw differs from all-shown?  "
        f"{digest(one) != digest(three)}")
    set_planes(part, selection, 1)

    # ── the 0.3.126 shape: two sketch sections + a solid loft ─────────────
    part = fresh(conn, docs, "ProbeRepaintLoft")
    selection = conn.hso
    for offset, major, minor in ((0, 8, 6), (30, 8, 6)):
        sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": offset})
        sketcher.execute("catia_sketch_ellipse", {"cx": 0, "cy": 0,
                                                 "major": major, "minor": minor})
        sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_solid_loft", {"sections": ["草图.1", "草图.2"]})
    four = capture(conn, "L1_all_shown", redraw=True)
    say(f"  4 loft, all shown               {digest(four)}  light={light(four):.3f}")
    set_planes(part, selection, 0)
    five = capture(conn, "L2_planes_hidden", redraw=True)
    say(f"  5 loft, planes hidden           {digest(five)}  light={light(five):.3f}")
    say(f"  -> on a loft it changes too?    {digest(four) != digest(five)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
