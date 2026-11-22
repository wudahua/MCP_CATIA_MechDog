"""P0-2, decisive: what does AddNewMirror(seed) actually do?

Two readings so far, both on a body symmetric about the plane:

  * `AddNewMirror(plane)` = the body operation (disjoint half -> volume doubles);
  * `AddNewMirror(seed)` where the seed is a POCKET -> 232 000 becomes 240 000,
    i.e. +8 000 = the pocket's own volume, so the pocket was FILLED. That is
    "mirror the seed's lump and union it", not "replay the cut mirrored".

This probe uses a MATERIAL seed (a 10x10x10 pad on top of a box) where the two
answers are far apart:

    box x 0..60, y 0..40, z 0..50            = 120 000
    pad 10x10x10 at x 10..20 on top          = 121 000

    feature mirror about yz  -> 122 000 and min x = -20
    body mirror about yz     -> 241 000 and min x = -60
    nothing                  -> 121 000 and min x = 0
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402


def say(text: str) -> None:
    print(text, flush=True)


def short(value, limit: int = 170) -> str:
    return " ".join(str(value).split())[:limit]


def build(docs, sk, design, name: str, path: str) -> None:
    try:
        docs.execute("catia_close_document", {"document": path, "save": False})
    except Exception:  # noqa: BLE001
        pass
    if os.path.isfile(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "SQ"})
    sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 60, "y2": 40})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"sketch_name": "SQ", "height": 50})
    # The material seed: a small pad on top, off to one side.
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "BUMP"})
    sk.execute("catia_sketch_rectangle", {"x1": 10, "y1": 10, "x2": 20, "y2": 20})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"sketch_name": "BUMP", "height": 10})
    docs.execute("catia_save_document", {})


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sk = SketcherTools(conn)
    design = PartDesignTools(conn)

    root = os.path.join(tempfile.gettempdir(), "probe0497e")
    os.makedirs(root, exist_ok=True)

    def volume(part, body) -> str:
        try:
            spa = part.Parent.GetWorkbench("SPAWorkbench")
            measurable = spa.GetMeasurable(part.CreateReferenceFromObject(body))
            return f"{float(measurable.Volume) * 1e9:.1f}"
        except Exception as exc:  # noqa: BLE001
            return f"<{short(exc, 40)}>"

    def min_x(part, body) -> str:
        try:
            spa = part.Parent.GetWorkbench("SPAWorkbench")
            measurable = spa.GetMeasurable(part.CreateReferenceFromObject(body))
            box = measurable.GetBoundingBox()
            return f"{float(box[0]):.2f}"
        except Exception as exc:  # noqa: BLE001
            return f"<{short(exc, 40)}>"

    try:
        # ── the seed form ──────────────────────────────────────────────────
        path = os.path.join(root, "SeedPad.CATPart")
        say("== AddNewMirror(PAD seed) + MirroringPlane = yz")
        build(docs, sk, design, "SeedPad", path)
        part = conn.get_active_part()
        body = part.MainBody
        sf = part.ShapeFactory
        plane_ref = part.CreateReferenceFromObject(conn.get_origin_elements()["yz"])
        bump = body.Shapes.Item(int(body.Shapes.Count))
        bump_ref = part.CreateReferenceFromObject(bump)
        say(f"  seed = {short(getattr(bump, 'Name', '?'), 40)}")
        say(f"  before: {volume(part, body)} mm3, min x = {min_x(part, body)}")
        mirror = None
        try:
            mirror = sf.AddNewMirror(bump_ref)
            mirror.MirroringPlane = plane_ref
            part.Update()
            say(f"  after:  {volume(part, body)} mm3, min x = {min_x(part, body)}"
                "   (122000 / -20 = feature, 241000 / -60 = body)")
        except Exception as exc:  # noqa: BLE001
            say(f"  failed: {short(exc, 160)}")
        try:
            say("  tree: " + str([str(body.Shapes.Item(i).Name)
                                  for i in range(1, int(body.Shapes.Count) + 1)]))
        except Exception as exc:  # noqa: BLE001
            say(f"  tree unreadable: {short(exc, 80)}")

        # ── the control: the plane in the same slot ────────────────────────
        path2 = os.path.join(root, "SeedPad2.CATPart")
        say("\n== control: AddNewMirror(PLANE) on the same geometry")
        build(docs, sk, design, "SeedPad2", path2)
        part = conn.get_active_part()
        body = part.MainBody
        sf = part.ShapeFactory
        plane_ref = part.CreateReferenceFromObject(conn.get_origin_elements()["yz"])
        say(f"  before: {volume(part, body)} mm3, min x = {min_x(part, body)}")
        try:
            sf.AddNewMirror(plane_ref)
            part.Update()
            say(f"  after:  {volume(part, body)} mm3, min x = {min_x(part, body)}")
        except Exception as exc:  # noqa: BLE001
            say(f"  failed: {short(exc, 160)}")

        # ── and the cut seed once more, with the box that makes it visible ─
        path3 = os.path.join(root, "SeedCut.CATPart")
        say("\n== the cut seed again: does mirroring a pocket FILL it?")
        try:
            docs.execute("catia_close_document", {"document": path3, "save": False})
        except Exception:  # noqa: BLE001
            pass
        if os.path.isfile(path3):
            os.remove(path3)
        docs.execute("catia_new_part", {"name": "SeedCut", "file_path": path3})
        sk.execute("catia_create_sketch", {"plane": "xy", "name": "SQ"})
        sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 60, "y2": 40})
        sk.execute("catia_close_sketch", {})
        design.execute("catia_pad", {"sketch_name": "SQ", "height": 50})
        sk.execute("catia_create_sketch", {"plane": "xy", "name": "PK"})
        sk.execute("catia_sketch_rectangle", {"x1": 10, "y1": 10, "x2": 30, "y2": 30})
        sk.execute("catia_close_sketch", {})
        design.execute("catia_pocket", {"sketch_name": "PK", "depth": 20,
                                        "direction": "up"})
        docs.execute("catia_save_document", {})
        part = conn.get_active_part()
        body = part.MainBody
        sf = part.ShapeFactory
        plane_ref = part.CreateReferenceFromObject(conn.get_origin_elements()["yz"])
        cut = body.Shapes.Item(int(body.Shapes.Count))
        cut_ref = part.CreateReferenceFromObject(cut)
        say(f"  seed = {short(getattr(cut, 'Name', '?'), 40)}")
        say(f"  before: {volume(part, body)} mm3 (112000 = box 120000 - pocket 8000)")
        try:
            mirror = sf.AddNewMirror(cut_ref)
            mirror.MirroringPlane = plane_ref
            part.Update()
            say(f"  after:  {volume(part, body)} mm3  (112000 unchanged / 120000 "
                "filled / 104000 a second pocket)")
        except Exception as exc:  # noqa: BLE001
            say(f"  failed: {short(exc, 160)}")
        return 0
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 1
    finally:
        for name in ("SeedPad.CATPart", "SeedPad2.CATPart", "SeedCut.CATPart"):
            try:
                docs.execute("catia_close_document",
                             {"document": os.path.join(root, name), "save": False})
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    sys.exit(main())
