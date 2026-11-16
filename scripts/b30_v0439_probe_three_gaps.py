"""The three items 0.3.137 left undone: can they be done at all?

The 2026-11-15 report (0.3.136) asked for three things this server does not do.
0.3.137 wrote them off in one sentence each. That sentence is itself a claim, so
each one gets measured before it is repeated:

    A  C5 - can ONE FACE be coloured? `catia_set_color` only takes a tree node,
       so "the silver ring" / "the black mesh" cannot be expressed. Measured
       here: Selection.Add(<topology cell>) + VisProperties.SetRealColor, read
       back, and then PROVEN IN THE PICTURE by counting red pixels - a stored
       colour and a displayed colour are two different facts (0.3.79's lesson).
    B  C8 - the leftovers in a "clean" screenshot. `_hide_construct_geometry`
       walks HybridBodies (geometrical sets), and `Part.OriginElements` - the
       three absolute datum planes - belongs to NO set. If that is what is still
       drawn, hiding it is a different call, not an impossible one.
    C  C8 again, second half - the elements inside body.HybridShapes that are
       NOT the solid. 0.3.126 learned that collection carries the solid itself;
       the safe rule is "skip what body.Shapes also lists", and this measures
       whether hiding the rest leaves the solid on screen.
    D  N7 - can an imported outline carry a DRIVING dimension at all? A closed
       spline has no radius to constrain, but Constraints.AddMonoEltCst(5,
       <spline>) is a LENGTH on one element. If that takes, the PDF outline can
       be driven by a named parameter even without a fit.

    .venv/Scripts/python.exe -u scripts/b30_v0439_probe_three_gaps.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp import colours  # noqa: E402
from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402
from catia_mcp.topo_search import topology_cells  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0439")
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


def box_part(conn, docs, sketcher, parts, name: str, size: float = 20.0):
    """A new document holding one padded box - the smallest thing with faces."""
    part = fresh(conn, docs, name)
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": size, "y2": size})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 10.0})
    return part


def shot(export, name: str) -> str:
    path = os.path.join(ROOT, name + ".jpg")
    if os.path.exists(path):
        os.remove(path)
    export.execute("catia_screenshot", {"file_path": path,
                                        "view": "isometric"})
    return path if os.path.exists(path) else ""


def red_pixels(path: str) -> int:
    """How many pixels are clearly RED - the only proof a face colour showed."""
    if not path:
        return -1
    from PIL import Image

    with Image.open(path) as image:
        return sum(1 for red, green, blue in image.convert("RGB").getdata()
                   if red > 110 and red > 2 * green and red > 2 * blue)


def centre_light(path: str) -> float:
    """Share of light pixels in the middle - the existing solid-is-there metric."""
    if not path:
        return -1.0
    from PIL import Image

    with Image.open(path) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        x0, x1 = int(width * 0.3), int(width * 0.7)
        y0, y1 = int(height * 0.3), int(height * 0.7)
        pixels = rgb.crop((x0, y0, x1, y1)).getdata()
        total = 0
        light = 0
        for red, green, blue in pixels:
            total += 1
            if (red + green + blue) / 3.0 > 150:
                light += 1
        return light / total if total else -1.0


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    parts = PartDesignTools(conn)
    export = ExportTools(conn)
    say(f"probe v0.3.138 on {conn.app.Name}")

    # ── A: colour ONE FACE ─────────────────────────────────────────────────
    say("\nA. C5 - can a single topology face carry a colour?")
    part = box_part(conn, docs, sketcher, parts, "ProbeFaceColour")
    body = conn.get_active_part_body()
    selection = conn.active_document.Selection
    back = colours.apply_colour(selection, body, (255, 255, 255))
    cell("A1 the whole body takes a neutral colour first", back == (255, 255, 255),
         f"read back {back}")
    plain = shot(export, "A_plain")
    cell("A2 basline picture has no red in it", red_pixels(plain) == 0,
         f"{red_pixels(plain)} red pixel(s)")

    faces = topology_cells(conn, body, "Face")
    cell("A3 the box has 6 faces", len(faces) == 6, f"{len(faces)}")

    for label, make in (
        ("raw cell", lambda cell_obj: cell_obj),
        ("reference", lambda cell_obj: part.CreateReferenceFromObject(cell_obj)),
    ):
        selection.Clear()
        try:
            selection.Add(make(faces[0]))
            added = True
            problem = ""
        except Exception as error:  # noqa: BLE001 - this IS the measurement
            added = False
            problem = str(error).splitlines()[0][:80]
        cell(f"A4 Selection.Add(<{label}>)", added, problem)
        if not added:
            continue
        try:
            selection.VisProperties.SetRealColor(200, 40, 40, colours.APPLY)
            set_ok, set_problem = True, ""
        except Exception as error:  # noqa: BLE001
            set_ok, set_problem = False, str(error).splitlines()[0][:80]
        cell(f"A5 SetRealColor on that <{label}>", set_ok, set_problem)
        read = colours.read_rgb(selection)
        cell(f"A6 it reads back as the red it was given", read == (200, 40, 40),
             f"read back {read}")
        painted = shot(export, "A_face_" + label.replace(" ", "_"))
        count = red_pixels(painted)
        cell(f"A7 the picture shows red", count > 200, f"{count} red pixel(s)")
        if count > 200:
            break
    selection.Clear()

    # Does the colour live on the face, or did it leak to the whole body? Give
    # the other five faces a different colour and see whether this one survives:
    # a per-face override is exactly what "the silver ring" needs.
    try:
        for other in faces[1:]:
            selection.Add(part.CreateReferenceFromObject(other))
        selection.VisProperties.SetRealColor(60, 200, 60, colours.APPLY)
        selection.Clear()
        selection.Add(part.CreateReferenceFromObject(faces[0]))
        kept = colours.read_rgb(selection)
        cell("A8 face 1 keeps its own colour while the rest turn green",
             kept == (200, 40, 40), f"face 1 reads {kept}")
    except Exception as error:  # noqa: BLE001
        cell("A8 face 1 keeps its own colour while the rest turn green", False,
             str(error).splitlines()[0][:90])
    selection.Clear()
    mixed = shot(export, "A_mixed")
    cell("A9 the picture holds BOTH colours", red_pixels(mixed) > 200,
         f"{red_pixels(mixed)} red pixel(s)")

    # ── B: the absolute datum planes ───────────────────────────────────────
    say("\nB. C8 - are Part.OriginElements' three planes what stays drawn?")
    part = box_part(conn, docs, sketcher, parts, "ProbeOriginPlanes")
    selection = conn.active_document.Selection
    before = shot(export, "B_before")
    try:
        planes = {
            "xy": part.OriginElements.PlaneXY,
            "yz": part.OriginElements.PlaneYZ,
            "zx": part.OriginElements.PlaneZX,
        }
        cell("B1 OriginElements exposes the three planes", True,
             ", ".join(planes))
    except Exception as error:  # noqa: BLE001
        planes = {}
        cell("B1 OriginElements exposes the three planes", False,
             str(error).splitlines()[0][:90])

    hidden: list[str] = []
    for name, plane in planes.items():
        selection.Clear()
        try:
            selection.Add(plane)
            selection.VisProperties.SetShow(0)
            state = int(selection.VisProperties.GetShow())
            hidden.append(f"{name}(GetShow={state})")
        except Exception as error:  # noqa: BLE001
            hidden.append(f"{name}(FAILED {str(error).splitlines()[0][:40]})")
        selection.Clear()
    cell("B2 each absolute plane can be switched off on its own",
         bool(hidden) and all("GetShow=0" in item for item in hidden),
         ", ".join(hidden))
    hidden_shot = shot(export, "B_planes_off")
    same_bytes = (os.path.getsize(before) == os.path.getsize(hidden_shot)
                  and open(before, "rb").read() == open(hidden_shot, "rb").read()
                  if before and hidden_shot else True)
    cell("B3 the picture CHANGED - the planes really were drawn", not same_bytes,
         f"{os.path.getsize(before) if before else 0} vs "
         f"{os.path.getsize(hidden_shot) if hidden_shot else 0} bytes")
    cell("B4 the solid is still in the picture",
         centre_light(hidden_shot) > 0.2,
         f"centre light {centre_light(hidden_shot):.3f}")

    # ── C: body.HybridShapes entries that are not the solid ────────────────
    say("\nC. C8 - what body.HybridShapes holds, and which part of it is the solid")
    part = box_part(conn, docs, sketcher, parts, "ProbeBodyHybrid")
    body = conn.get_active_part_body()
    selection = conn.active_document.Selection
    names = []
    for index in range(1, int(body.HybridShapes.Count) + 1):
        entry = body.HybridShapes.Item(index)
        try:
            names.append((str(entry.Name), entry))
        except Exception:  # noqa: BLE001
            names.append(("?", entry))
    solid_names = set()
    for index in range(1, int(body.Shapes.Count) + 1):
        try:
            solid_names.add(str(body.Shapes.Item(index).Name))
        except Exception:  # noqa: BLE001
            pass
    cell("C1 body.HybridShapes lists something", bool(names),
         f"{[n for n, _ in names]} vs solid {sorted(solid_names)}")
    others = [(n, e) for n, e in names if n not in solid_names]
    cell("C2 there ARE entries that are not the solid", bool(others),
         f"{[n for n, _ in others]}")
    for name, entry in others:
        selection.Clear()
        try:
            selection.Add(entry)
            selection.VisProperties.SetShow(0)
        except Exception:  # noqa: BLE001 - one of them may be the solid
            pass
        selection.Clear()
    after = shot(export, "C_hybrid_hidden")
    cell("C3 the solid survives hiding them", centre_light(after) > 0.2,
         f"centre light {centre_light(after):.3f}")
    shapes_now = int(body.Shapes.Count)
    cell("C4 no shape was consumed", shapes_now == 1, f"{shapes_now} shape(s)")

    # ── D: a driving dimension on a closed spline ──────────────────────────
    say("\nD. N7 - can an imported outline carry a driving dimension?")
    part = fresh(conn, docs, "ProbeSplineDimension")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_spline",
                     {"points": [[0, 0], [100, 0], [100, 40], [0, 40]],
                      "closed": True, "name": "Outline"})
    sketch = conn.last_sketch
    spline = None
    for index in range(1, int(sketch.GeometricElements.Count) + 1):
        element = sketch.GeometricElements.Item(index)
        try:
            if "样条" in str(element.Name) or "Spline" in str(element.Name):
                spline = element
                break
        except Exception:  # noqa: BLE001
            continue
    cell("D1 the closed spline is one element", spline is not None,
         str(getattr(spline, "Name", "?")))
    if spline is not None:
        reference = part.CreateReferenceFromObject(spline)
        for kind, label in ((sketcher._CST_LENGTH, "LENGTH"),
                            (sketcher._CST_RADIUS, "RADIUS")):
            try:
                constraint = sketch.Constraints.AddMonoEltCst(kind, reference)
                try:
                    value = float(constraint.Dimension.Value)
                except Exception:  # noqa: BLE001
                    value = None
                cell(f"D2 AddMonoEltCst({label}) on the spline", True,
                     f"'{constraint.Name}' value={value}")
            except Exception as error:  # noqa: BLE001
                cell(f"D2 AddMonoEltCst({label}) on the spline", False,
                     str(error).splitlines()[0][:90])
    sketcher.execute("catia_close_sketch", {})

    say("\n--- summary")
    bad = [row for row in ROWS if not row[1]]
    say(f"{len(ROWS) - len(bad)}/{len(ROWS)} ok")
    for what, _, detail in bad:
        say(f"  FAIL {what}  {detail}")
    close_ours(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
