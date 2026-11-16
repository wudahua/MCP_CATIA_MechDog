"""C8's real untested cell: hide the datum INSIDE body.HybridShapes, keep the solid.

0.3.126 measured the extremes - "hide the whole collection: SOLID GONE" and
"hide only the 3 origin planes: picture IDENTICAL to all shown". Neither is the
call a clean screenshot needs. `body.HybridShapes` on a Part Design loft holds
['Datum_xy_20', 'Body']: the loft is a MEMBER, so the collection cannot be
emptied - but it can be walked and every entry whose name is NOT in body.Shapes
can be switched off on its own.

CALIBRATION FIRST, because v0439b's 0.303 -> 0.926 turned out to be two different
capture sizes (400x300 vs 768x744), not two different scenes: the same state is
captured twice and must be byte-identical before any difference is believed.

    .venv/Scripts/python.exe -u scripts/b30_v0439g_probe_hybrid_datum.py
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0439g")


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


def shot(export, name: str) -> str:
    path = os.path.join(ROOT, name + ".jpg")
    if os.path.exists(path):
        os.remove(path)
    export.execute("catia_screenshot", {"file_path": path, "view": "isometric"})
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
        return (sum(1 for r, g, b in pixels if (r + g + b) / 3 > 150) / len(pixels)
                if pixels else -1.0)


def size(path: str) -> str:
    if not path:
        return "?"
    from PIL import Image

    with Image.open(path) as image:
        return f"{image.size[0]}x{image.size[1]}"


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    parts = PartDesignTools(conn)
    export = ExportTools(conn)
    say(f"probe v0.3.138 body.HybridShapes datum on {conn.app.Name}")

    part = fresh(conn, docs, "ProbeHybridDatum")
    for offset in (0, 30):
        sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": offset})
        sketcher.execute("catia_sketch_ellipse", {"cx": 0, "cy": 0,
                                                 "major": 8, "minor": 6})
        sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_solid_loft", {"sections": ["草图.1", "草图.2"]})
    body = conn.get_active_part_body()

    solid_names, members = set(), []
    for index in range(1, int(body.Shapes.Count) + 1):
        try:
            solid_names.add(str(body.Shapes.Item(index).Name))
        except Exception:  # noqa: BLE001
            pass
    for index in range(1, int(body.HybridShapes.Count) + 1):
        entry = body.HybridShapes.Item(index)
        try:
            members.append((str(entry.Name), entry))
        except Exception:  # noqa: BLE001
            members.append(("?", entry))
    say(f"  body.Shapes        = {sorted(solid_names)}")
    say(f"  body.HybridShapes  = {[name for name, _ in members]}")
    extras = [(name, entry) for name, entry in members if name not in solid_names]
    say(f"  entries that are NOT the solid: {[name for name, _ in extras]}")

    # ── calibration: the same state, twice ────────────────────────────────
    first = shot(export, "H1_all_shown_a")
    second = shot(export, "H1_all_shown_b")
    say(f"  1 all shown, twice      {digest(first)} / {digest(second)}  "
        f"{size(first)} {size(second)}  light {light(first):.3f}")
    say(f"  [{'OK  ' if digest(first) == digest(second) else 'FAIL'}] the same "
        "state captures identically")
    if digest(first) != digest(second):
        say("  (a difference below cannot be attributed until this holds)")
    before_light = light(second)

    # ── hide only the non-solid members ───────────────────────────────────
    selection = conn.hso
    hidden = 0
    for name, entry in extras:
        selection.Clear()
        try:
            selection.Add(entry)
            selection.VisProperties.SetShow(0)
            hidden += 1
        except Exception as error:  # noqa: BLE001
            say(f"    {name}: {str(error).splitlines()[0][:60]}")
        selection.Clear()
    three = shot(export, "H2_datum_hidden")
    say(f"  2 datum hidden ({hidden}/{len(extras)})   {digest(three)}  "
        f"{size(three)}  light {light(three):.3f}")
    say(f"  [{'OK  ' if digest(three) != digest(second) else 'FAIL'}] the picture "
        "CHANGED - the datum was drawn")
    say(f"  [{'OK  ' if light(three) > 0.2 else 'FAIL'}] the solid is still there")
    for name, entry in extras:
        selection.Clear()
        try:
            selection.Add(entry)
            selection.VisProperties.SetShow(1)
        except Exception:  # noqa: BLE001
            pass
        selection.Clear()

    # ── the extreme 0.3.126 measured, for the record ──────────────────────
    for name, entry in members:
        selection.Clear()
        try:
            selection.Add(entry)
            selection.VisProperties.SetShow(0)
        except Exception:  # noqa: BLE001
            pass
        selection.Clear()
    four = shot(export, "H3_everything_hidden")
    say(f"  3 every member hidden   {digest(four)}  {size(four)}  "
        f"light {light(four):.3f}   (0.3.126 saw the solid disappear)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
