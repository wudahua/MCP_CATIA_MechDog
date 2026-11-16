"""Round two on the three undone items - through the routes the project owns.

Round one failed on the probe, not on CATIA, and each failure named its own fix:

  * `part.CreateReferenceFromObject(<topology cell>)` is refused - topo_search
    already documents this ("CreateReferenceFromObject rejects HSO-resolved
    topology cells (field-verified E_INVALIDARG)") and `topology_reference`
    exists precisely to take the reference off the still-live selection.
  * a visibility flag written and then judged from the picture needs a REDRAW
    first - 0.3.131's zoom bug was exactly this, and two byte-identical JPEGs
    said nothing about the planes.
  * `body.HybridShapes.Count` is 0 on a padded box, so that collection is not
    where a solid-body part's leftovers live. A GSD point and plane in a
    geometrical set are the shape the report actually photographed.
  * `_drive_curve` passes the curve OBJECT to AddMonoEltCst on the circle/ellipse
    path; only the `catia_sketch_constraint` tool wraps it in a reference. Both
    are tried on the spline.

    .venv/Scripts/python.exe -u scripts/b30_v0439b_probe_three_gaps.py
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
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402
from catia_mcp.topo_search import topology_reference  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0439b")
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
    part = fresh(conn, docs, name)
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": size, "y2": size})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 10.0})
    return part


def shot(conn, export, name: str) -> str:
    path = os.path.join(ROOT, name + ".jpg")
    if os.path.exists(path):
        os.remove(path)
    try:
        conn.refresh_display()
    except Exception:  # noqa: BLE001 - the capture still runs
        pass
    export.execute("catia_screenshot", {"file_path": path, "view": "isometric"})
    return path if os.path.exists(path) else ""


def red_pixels(path: str) -> int:
    if not path:
        return -1
    from PIL import Image

    with Image.open(path) as image:
        return sum(1 for red, green, blue in image.convert("RGB").getdata()
                   if red > 110 and red > 2 * green and red > 2 * blue)


def centre_light(path: str) -> float:
    if not path:
        return -1.0
    from PIL import Image

    with Image.open(path) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        x0, x1 = int(width * 0.3), int(width * 0.7)
        y0, y1 = int(height * 0.3), int(height * 0.7)
        pixels = list(rgb.crop((x0, y0, x1, y1)).getdata())
        light = sum(1 for red, green, blue in pixels
                    if (red + green + blue) / 3.0 > 150)
        return light / len(pixels) if pixels else -1.0


def same_file(first: str, second: str) -> bool:
    if not first or not second:
        return True
    with open(first, "rb") as handle:
        left = handle.read()
    with open(second, "rb") as handle:
        right = handle.read()
    return left == right


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    parts = PartDesignTools(conn)
    gsd = GSDTools(conn)
    export = ExportTools(conn)
    say(f"probe v0.3.138 round 2 on {conn.app.Name}")

    # ── A: colour one face, through topology_reference ─────────────────────
    say("\nA. C5 - one face, one colour")
    part = box_part(conn, docs, sketcher, parts, "ProbeFaceColour2")
    body = conn.get_active_part_body()
    selection = conn.hso
    colours.apply_colour(selection, body, (255, 255, 255))
    plain = shot(conn, export, "A2_plain")
    base_red = red_pixels(plain)
    cell("A1 the neutral baseline's red pixels are the axis triad, not the part",
         base_red >= 0, f"{base_red} red pixel(s) - these are the calibration")

    ref = topology_reference(conn, body, "Face", 1)
    cell("A2 topology_reference resolves Face.1 and keeps the selection",
         ref.reference is not None, f"'{ref.name}'")
    painted_ok = False
    try:
        selection.VisProperties.SetRealColor(200, 40, 40, colours.APPLY)
        painted_ok = True
    except Exception as error:  # noqa: BLE001
        cell("A3 SetRealColor on the selected face", False,
             str(error).splitlines()[0][:90])
    if painted_ok:
        read = colours.read_rgb(selection)
        cell("A3 SetRealColor on the selected face", True, f"reads back {read}")
        cell("A4 it reads back as the colour it was given", read == (200, 40, 40),
             f"{read}")
        painted = shot(conn, export, "A2_face1")
        gained = red_pixels(painted) - max(base_red, 0)
        cell("A5 the PICTURE gained red - the colour is displayed, not just stored",
             gained > 200, f"{red_pixels(painted)} red vs {base_red} baseline "
                           f"({gained:+d})")
    selection.Clear()

    # Is it the face alone, or did the whole body turn red? Paint the other
    # faces green and check face 1 still reads red.
    try:
        others = [topology_reference(conn, body, "Face", index).reference
                  for index in range(2, 7)]
        selection.Clear()
        for one in others:
            selection.Add(one)
        selection.VisProperties.SetRealColor(60, 200, 60, colours.APPLY)
        cell("A6 the other five faces take a different colour", True,
             f"reads back {colours.read_rgb(selection)}")
        selection.Clear()
        back = topology_reference(conn, body, "Face", 1)
        cell("A7 face 1 still reads its OWN colour",
             colours.read_rgb(selection) == (200, 40, 40),
             f"'{back.name}' reads {colours.read_rgb(selection)}")
        mixed = shot(conn, export, "A2_mixed")
        cell("A8 both colours are in the picture", red_pixels(mixed) > base_red + 200,
             f"{red_pixels(mixed)} red pixel(s)")
    except Exception as error:  # noqa: BLE001
        cell("A6/A7 per-face colour survives its neighbours", False,
             str(error).splitlines()[0][:90])
    selection.Clear()

    # ── B: the absolute datum planes ───────────────────────────────────────
    say("\nB. C8 - the three absolute planes")
    part = box_part(conn, docs, sketcher, parts, "ProbeOriginPlanes2")
    selection = conn.hso
    before = shot(conn, export, "B2_before")
    states = {}
    for name in ("PlaneXY", "PlaneYZ", "PlaneZX"):
        try:
            plane = getattr(part.OriginElements, name)
            selection.Clear()
            selection.Add(plane)
            selection.VisProperties.SetShow(0)
            states[name] = repr(selection.VisProperties.GetShow())
        except Exception as error:  # noqa: BLE001
            states[name] = "FAILED " + str(error).splitlines()[0][:60]
        selection.Clear()
    cell("B1 each absolute plane accepts SetShow(0) on its own",
         all("FAILED" not in value for value in states.values()),
         ", ".join(f"{key}={value}" for key, value in states.items()))
    after = shot(conn, export, "B2_planes_off")
    cell("B2 the PICTURE changed, so the planes really were drawn",
         not same_file(before, after),
         f"{os.path.getsize(before) if before else 0} vs "
         f"{os.path.getsize(after) if after else 0} bytes")
    cell("B3 centre light before / after", True,
         f"{centre_light(before):.3f} -> {centre_light(after):.3f}")
    cell("B4 the solid is still there", centre_light(after) > 0.2,
         f"{centre_light(after):.3f}")

    # ── C: what a GSD point and plane look like, and hiding them alone ─────
    say("\nC. C8 - GSD construction in a geometrical set")
    part = box_part(conn, docs, sketcher, parts, "ProbeGsdLeftovers")
    gsd.execute("catia_gsd_create_geoset", {"name": "Construction"})
    gsd.execute("catia_gsd_set_active_geoset", {"name": "Construction"})
    gsd.execute("catia_gsd_point", {"x": 10, "y": 10, "z": 10, "name": "ProbePt"})
    try:
        gsd.execute("catia_gsd_plane_offset", {"plane": "xy", "offset": 30,
                                               "name": "ProbePlane"})
        made_plane = True
    except Exception as error:  # noqa: BLE001
        made_plane = False
        say("        (offset plane refused: "
            + str(error).splitlines()[0][:70] + ")")
    drawn = shot(conn, export, "C2_with_construction")
    cell("C1 a GSD point (and plane) are drawn over the solid", True,
         f"offset plane: {made_plane}, centre light {centre_light(drawn):.3f}")

    # Hide the SET, then hide its MEMBERS: the report's two different outcomes.
    selection = conn.hso
    selection.Clear()
    try:
        selection.Add(part.HybridBodies.Item("Construction"))
        selection.VisProperties.SetShow(0)
        set_hidden = True
    except Exception as error:  # noqa: BLE001
        set_hidden = False
        say("        (hiding the set failed: "
            + str(error).splitlines()[0][:70] + ")")
    selection.Clear()
    by_set = shot(conn, export, "C2_set_hidden")
    cell("C2 hiding the SET leaves the solid on screen", set_hidden
         and centre_light(by_set) > 0.2,
         f"centre light {centre_light(by_set):.3f}")

    # and back on, then hide the members one by one
    selection.Clear()
    try:
        selection.Add(part.HybridBodies.Item("Construction"))
        selection.VisProperties.SetShow(1)
    except Exception:  # noqa: BLE001
        pass
    selection.Clear()
    holder = part.HybridBodies.Item("Construction")
    members = []
    for index in range(1, int(holder.HybridShapes.Count) + 1):
        entry = holder.HybridShapes.Item(index)
        try:
            members.append((str(entry.Name), entry))
        except Exception:  # noqa: BLE001
            members.append(("?", entry))
    hidden_members = 0
    for _name, entry in members:
        selection.Clear()
        try:
            selection.Add(entry)
            selection.VisProperties.SetShow(0)
            hidden_members += 1
        except Exception:  # noqa: BLE001
            pass
    selection.Clear()
    by_member = shot(conn, export, "C2_members_hidden")
    cell("C3 the members were hidden one by one",
         hidden_members == len(members) and bool(members),
         f"{hidden_members}/{len(members)}: {[n for n, _ in members]}")
    cell("C4 the solid survives hiding the members", centre_light(by_member) > 0.2,
         f"centre light {centre_light(by_member):.3f}")
    cell("C5 hiding the members changes the picture (they were drawn)",
         not same_file(by_set, by_member),
         f"{os.path.getsize(by_set) if by_set else 0} vs "
         f"{os.path.getsize(by_member) if by_member else 0} bytes")

    # ── D: a driving dimension on a closed spline ──────────────────────────
    say("\nD. N7 - can a closed spline take a driving dimension?")
    part = fresh(conn, docs, "ProbeSplineDimension2")
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
    cell("D1 the outline is ONE spline element", spline is not None,
         str(getattr(spline, "Name", "?")))
    if spline is not None:
        for label, maker in (
            ("object", lambda: spline),
            ("reference", lambda: part.CreateReferenceFromObject(spline)),
            ("geom item 2", lambda: part.CreateReferenceFromObject(
                sketch.GeometricElements.Item(2))),
        ):
            try:
                argument = maker()
            except Exception as error:  # noqa: BLE001
                cell(f"D2 argument as <{label}>", False,
                     str(error).splitlines()[0][:80])
                continue
            for kind, kind_label in ((sketcher._CST_LENGTH, "LENGTH"),
                                     (sketcher._CST_RADIUS, "RADIUS")):
                try:
                    constraint = sketch.Constraints.AddMonoEltCst(kind, argument)
                    try:
                        value = float(constraint.Dimension.Value)
                    except Exception:  # noqa: BLE001
                        value = None
                    cell(f"D2 AddMonoEltCst({kind_label}) as <{label}>", True,
                         f"'{constraint.Name}' value={value}")
                except Exception as error:  # noqa: BLE001
                    cell(f"D2 AddMonoEltCst({kind_label}) as <{label}>", False,
                         str(error).splitlines()[0][:80])
    sketcher.execute("catia_close_sketch", {})

    say("\n--- summary")
    bad = [row for row in ROWS if not row[1]]
    say(f"{len(ROWS) - len(bad)}/{len(ROWS)} ok")
    for what, _, detail in bad:
        say(f"  FAIL {what}  {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
