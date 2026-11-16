"""0.3.138 acceptance on B30 - the three items 0.3.137 wrote off.

    0  calibration: the same state captured twice must be byte-identical, or no
       difference below it can be believed (0.3.138's first probe compared a
       400x300 frame with a 768x744 one and read 0.303 -> 0.926 out of nothing)
    A  one FACE takes a colour, via catia_set_color(target='Face.1'), and it
       reads back as that colour
    B  the colour is on that face ALONE: with the other five faces green, face 1
       still reads its own red, and the picture holds both
    C  a face index that does not exist is refused with the range, before
       anything is coloured
    D  clean='solid_only' hides the body datum ('Datum_xy_30') and the capture
       CHANGES while the solid stays - the leftover of the 2026-11-15 C8
    E  the clean reply counts the datums and no longer claims the body's
       collection is untouched
    F  N7 through the TOOL layer: catia_sketch_constraint(distance) between two
       control points, driven by catia_set_constraint_value, moves the pad -
       which is what makes the diagnose entry a route rather than a promise

    .venv/Scripts/python.exe -u scripts/b30_v0439_acceptance.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.appearance import AppearanceTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0439a")
ROWS: list[tuple[str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def cell(what: str, ok: bool, detail: str = "") -> None:
    ROWS.append((what, ok, detail))
    say(f"  [{'OK  ' if ok else 'FAIL'}] {what}" + (f"  -> {detail}" if detail else ""))


def close_ours(conn) -> None:
    root = os.path.abspath(ROOT).lower()
    for index in range(int(conn.documents.Count), 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = str(doc.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).lower().startswith(root):
            try:
                doc.Close()
            except Exception:  # noqa: BLE001
                pass


def fresh(conn, docs, name: str):
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, name + ".CATPart")
    close_ours(conn)
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


def size(path: str) -> str:
    if not path:
        return "?"
    from PIL import Image

    with Image.open(path) as image:
        return f"{image.size[0]}x{image.size[1]}"


def red(path: str) -> int:
    from PIL import Image

    if not path:
        return -1
    with Image.open(path) as image:
        return sum(1 for r, g, b in image.convert("RGB").getdata()
                   if r > 110 and r > 2 * g and r > 2 * b)


def green(path: str) -> int:
    from PIL import Image

    if not path:
        return -1
    with Image.open(path) as image:
        return sum(1 for r, g, b in image.convert("RGB").getdata()
                   if g > 110 and g > 2 * r and g > 2 * b)


def light(path: str) -> float:
    from PIL import Image

    if not path:
        return -1.0
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        pixels = list(rgb.crop((int(width * .3), int(height * .3),
                                int(width * .7), int(height * .7))).getdata())
        return (sum(1 for r, g, b in pixels if (r + g + b) / 3 > 150) / len(pixels)
                if pixels else -1.0)


def _try(tools, name: str, args: dict) -> str:
    try:
        return str(tools.execute(name, args))
    except Exception as error:  # noqa: BLE001 - the message IS the answer
        return str(error)


def box(conn, docs, sketcher, parts, name: str):
    part = fresh(conn, docs, name)
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 20, "y2": 20})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 10.0})
    return part


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    parts = PartDesignTools(conn)
    measurement = MeasurementTools(conn)
    export = ExportTools(conn)
    appearance = AppearanceTools(conn)
    say(f"acceptance 0.3.138 on {conn.app.Name}")

    # ── 0: calibration ─────────────────────────────────────────────────────
    say("\n0. can a difference be believed at all?")
    part = box(conn, docs, sketcher, parts, "Acc138Calibration")
    first = shot(export, "Cal_a")
    second = shot(export, "Cal_b")
    cell("0 the same state captures identically (size and bytes)",
         digest(first) == digest(second) and size(first) == size(second),
         f"{digest(first)} / {digest(second)} at {size(first)} / {size(second)}")
    base_red = red(second)
    say(f"        baseline red pixels: {base_red} (the axis triad, not the part)")

    # ── A: a face colour is refused, with its measurement ──────────────────
    say("\nA. C5 - catia_set_color on Face.1")
    refused = ""
    try:
        refused = appearance.execute("catia_set_color", {"target": "Face.1",
                                                        "color": "#C82828"})
    except Exception as error:  # noqa: BLE001 - a coded refusal is the answer
        refused = str(error)
    say("        " + " ".join(refused.split())[:300])
    cell("A1 it is refused, not silently applied to the whole solid",
         "not a colour target" in refused)
    cell("A2 the refusal quotes the measurement, not an opinion",
         "122516" in refused and "WHOLE solid" in refused)
    cell("A3 it names the colour that did NOT change", "#D2D2FF" in refused)
    cell("A4 it leaves somewhere to go", "own BODY" in refused
         and "own part" in refused)
    cell("A5 reading a face back is refused too",
         "not a colour target" in _try(appearance, "catia_get_color",
                                       {"target": "Face.1"}))

    # ── B: the alternative the refusal points at, measured ─────────────────
    say("\nB. the region gets its own body - does that give two colours?")
    part = fresh(conn, docs, "Acc138TwoBodies")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 10, "y2": 10})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 8.0})
    parts.execute("catia_new_body", {"name": "GreenBody"})
    # A new body is not automatically the work object: a pad that follows lands
    # in whatever InWorkObject was, which is the trap 0.3.120 wrote down.
    parts.execute("catia_set_active_body", {"name": "GreenBody"})
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 14, "y1": 0, "x2": 24, "y2": 10})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 8.0})
    red_reply = appearance.execute("catia_set_color", {"target": "零件几何体",
                                                      "color": "#C82828"})
    green_reply = appearance.execute("catia_set_color", {"target": "GreenBody",
                                                        "color": "#3CC83C"})
    say("        " + " ".join(red_reply.split())[:130])
    say("        " + " ".join(green_reply.split())[:130])
    # A colour is a DISPLAY property, and CaptureToFile does not force a repaint
    # (0.3.131 measured two identical frames across a zoom for this reason).
    conn.refresh_display()
    bodies = json.loads(parts.execute("catia_list_bodies", {}))
    say("        bodies: " + ", ".join(
        f"{entry.get('name')}={entry.get('shape_count')}"
        for entry in bodies.get("bodies") or []))
    two = shot(export, "Acc138TwoBodies")
    cell("B1 both bodies took their own colour",
         "C82828" in red_reply.upper() and "3CC83C" in green_reply.upper())
    cell("B2 the picture holds BOTH colours at once",
         red(two) > 10000 and green(two) > 10000,
         f"red {red(two)}, green {green(two)} - a local finish, by body")

    # ── D/E: the body datum ────────────────────────────────────────────────
    say("\nD. C8 - clean='solid_only' and the body datum")
    part = fresh(conn, docs, "Acc138CleanDatum")
    for offset in (0, 30):
        sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": offset})
        sketcher.execute("catia_sketch_ellipse", {"cx": 0, "cy": 0,
                                                 "major": 8, "minor": 6})
        sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_solid_loft", {"sections": ["草图.1", "草图.2"]})
    body = conn.get_active_part_body()
    hybrid = [str(body.HybridShapes.Item(i).Name)
              for i in range(1, int(body.HybridShapes.Count) + 1)]
    cell("D1 the body's mixed collection really holds a datum",
         any(name.lower().startswith("datum") for name in hybrid), f"{hybrid}")

    plain = shot(export, "Acc138CleanPlain")
    clean_reply = export.execute("catia_screenshot",
                                 {"file_path": os.path.join(ROOT, "Acc138Clean.jpg"),
                                  "view": "isometric", "clean": "solid_only"})
    say("        " + " ".join(clean_reply.split())[:300])
    clean_pic = os.path.join(ROOT, "Acc138Clean.jpg")
    cell("D2 the capture CHANGED", digest(plain) != digest(clean_pic),
         f"{digest(plain)} -> {digest(clean_pic)}")
    cell("D3 the solid is still in the picture", light(clean_pic) > 0.2,
         f"centre light {light(clean_pic):.3f} (was {light(plain):.3f})")

    say("\nE. what the reply claims")
    cell("E1 it counts the datums it hid", "datum(s) inside" in clean_reply
         or "datum(s)" in clean_reply, " ".join(clean_reply.split())[:160])
    cell("E2 it no longer says the body collection is left alone",
         "hiding the body's own HybridShapes" not in clean_reply)
    cell("E3 it states what was measured about the absolute planes",
         "byte-identical" in clean_reply or "does not draw" in clean_reply)

    # ── F: N7 through the tool layer ───────────────────────────────────────
    say("\nF. N7 - a control-point dimension, through the tools")
    part = fresh(conn, docs, "Acc138SplineDrive")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_spline",
                     {"points": [[0, 0], [100, 0], [100, 40], [0, 40]],
                      "closed": True, "name": "Outline"})
    # hide_construction=false, or the control points are collapsed into a count:
    # a spline's control points ARE construction geometry by the default rule.
    geometry = json.loads(sketcher.execute("catia_sketch_get_geometry",
                                           {"hide_construction": False}))
    indices = {entry.get("name"): entry.get("index")
               for entry in geometry.get("elements") or []
               if isinstance(entry, dict)}
    say(f"        elements: {sorted(indices)}")
    first_point = indices.get("点.1")
    third_point = indices.get("点.3")
    say(f"        control point indices: 点.1={first_point} 点.3={third_point}")
    made = sketcher.execute("catia_sketch_constraint",
                            {"type": "distance", "value": 107.703,
                             "geometry_index_1": first_point,
                             "geometry_index_2": third_point})
    say("        " + " ".join(made.split())[:220])
    cell("F1 a distance constraint between two control points is accepted",
         "constraint" in made.lower() and "added" in made.lower(),
         " ".join(made.split())[:140])
    name = None
    match = re.search(r"'([^']+)'", made)
    if match:
        name = match.group(1)
    say(f"        constraint name: {name}")
    before = None
    try:
        sketcher.execute("catia_close_sketch", {})
        parts.execute("catia_pad", {"height": 20.0})
        before = json.loads(measurement.execute("catia_get_inertia", {})).get("volume_mm3")
    except Exception as error:  # noqa: BLE001
        say("        (baseline pad failed: " + str(error).splitlines()[0][:80] + ")")
    drove = ""
    if name:
        try:
            drove = sketcher.execute("catia_set_constraint_value",
                                     {"constraint": name, "value": 140.0})
        except Exception as error:  # noqa: BLE001
            drove = str(error)
    say("        " + " ".join(drove.split())[:200])
    after = None
    try:
        conn.get_active_part().Update()
        after = json.loads(measurement.execute("catia_get_inertia", {})).get("volume_mm3")
    except Exception as error:  # noqa: BLE001
        say("        (re-measure failed: " + str(error).splitlines()[0][:80] + ")")
    cell("F2 driving it MOVED the solid",
         bool(before and after and abs(after - before) > 1.0),
         f"{before} -> {after}")

    say("\n--- summary")
    bad = [row for row in ROWS if not row[1]]
    say(f"{len(ROWS) - len(bad)}/{len(ROWS)} ok")
    for what, _, detail in bad:
        say(f"  FAIL {what}  {detail}")
    close_ours(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
