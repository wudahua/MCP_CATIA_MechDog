"""Is a face colour really per face, across separate tool calls?

v0439b set five faces green in ONE selection and Face.1 kept its red. The
acceptance script set them one call at a time and Face.1 read green afterwards.
Two explanations, and they need different fixes:

  (a) the colour covered the whole BODY - then the body reads green too
  (b) Face.N is not stable between calls - then a re-listing reorders the cells

Measured here on one box, step by step, printing the body colour next to the
face colour after every write.

    .venv/Scripts/python.exe -u scripts/b30_v0439h_probe_face_independence.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.appearance import AppearanceTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0439h")


def say(line: str = "") -> None:
    print(line, flush=True)


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    parts = PartDesignTools(conn)
    appearance = AppearanceTools(conn)
    export = ExportTools(conn)
    path = os.path.join(ROOT, "ProbeFaceIndependence.CATPart")
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
    docs.execute("catia_new_part", {"name": "ProbeFaceIndependence",
                                    "file_path": path})
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 20, "y2": 20})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 10.0})
    say(f"probe v0.3.138 face independence on {conn.app.Name}")

    def listing(tag: str) -> None:
        data = json.loads(parts.execute("catia_list_faces", {}))
        rows = [(entry["index"], entry.get("area_mm2")) for entry in data["faces"]]
        say(f"  {tag}: {rows}")

    def shot(name: str) -> str:
        file = os.path.join(ROOT, name + ".jpg")
        if os.path.exists(file):
            os.remove(file)
        export.execute("catia_screenshot", {"file_path": file, "view": "isometric"})
        from PIL import Image

        if not os.path.exists(file):
            return "MISSING"
        with Image.open(file) as image:
            pixels = list(image.convert("RGB").getdata())
        red = sum(1 for r, g, b in pixels if r > 110 and r > 2 * g and r > 2 * b)
        green = sum(1 for r, g, b in pixels if g > 110 and g > 2 * r and g > 2 * b)
        blue = sum(1 for r, g, b in pixels if b > 110 and b > 2 * r and b > 2 * g)
        return f"red={red} green={green} blue={blue}"

    def colour_of(target: str | None) -> str:
        args = {"target": target} if target else {}
        text = appearance.execute("catia_get_color", args)
        return " ".join(text.split())

    listing("faces, first listing")
    say("  body   before: " + colour_of(None)[:120])
    say("  picture before: " + shot("I0_before"))

    say("\n  A. Face.1 red, then ask the BODY")
    say("     " + " ".join(appearance.execute(
        "catia_set_color", {"target": "Face.1", "color": "#C82828"}).split())[:150])
    say("     body   after: " + colour_of(None)[:120])
    say("     Face.1 after: " + colour_of("Face.1")[:120])
    say("     Face.2 after: " + colour_of("Face.2")[:120])
    say("  picture after Face.1 red: " + shot("I1_face1_red"))

    say("\n  B. Face.2 green, then ask Face.1 again")
    say("     " + " ".join(appearance.execute(
        "catia_set_color", {"target": "Face.2", "color": "#3CC83C"}).split())[:150])
    say("     Face.1 after: " + colour_of("Face.1")[:120])
    say("     Face.2 after: " + colour_of("Face.2")[:120])
    say("     body   after: " + colour_of(None)[:120])
    listing("faces, after two writes")
    say("  picture after Face.2 green: " + shot("I2_face2_green"))

    say("\n  C. a third face in blue, and the body's own colour set white")
    say("     " + " ".join(appearance.execute(
        "catia_set_color", {"target": "Face.3", "color": "#2020C8"}).split())[:120])
    say("  picture after Face.3 blue: " + shot("I3_face3_blue"))
    say("     " + " ".join(appearance.execute(
        "catia_set_color", {"color": "#F0F0F0"}).split())[:120])
    say("  picture after whole-body white: " + shot("I4_body_white"))
    say("     Face.1 now: " + colour_of("Face.1")[:110])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
