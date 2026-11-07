"""0.3.79 acceptance on B30: colour, through the tools, on real geometry.

The probes (b30_v0379_colour_*.py) established what the COM layer does. This
script checks the TOOLS against it, on an assembly it builds and then throws away:

1  catia_colorize_assembly: one colour per part, the two instances of one part
   share it, the other part differs
2  catia_get_color reads those colours back (hex + RGB)
3  catia_list_components reports no colour by default and the real colour on
   include_color=true
4  catia_set_color on ONE instance leaves its sibling alone (instance colour)
5  all_instances=true colours every instance of that part and nothing else
6  the .CATPart file is untouched by an instance colour
7  a part-level colour: the body takes it, a feature can carry its own
8  save + close + reopen: the colours are in the CATProduct, not the session
9  the PICTURE: sample the screenshot's pixels, because the first version of
   this feature stored every colour correctly (read-back matched to the digit)
   and rendered a gray assembly - the fourth argument of SetRealColor is a
   status flag whose EVEN values are ignored by the viewer

Everything is created under F:\\catia_probe_v0379e and closed again; the
document that was active on entry is re-activated at the end.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.colours import parse_colour  # noqa: E402
from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.appearance import AppearanceTools  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0379e"
RESULTS: list[tuple[str, bool, str]] = []


def say(line: str) -> None:
    print(line, flush=True)


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, bool(ok), detail))
    say(f"   {'OK  ' if ok else 'FAIL'} {label}" + (f"  [{detail[:200]}]" if detail else ""))


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    design = PartDesignTools(conn)
    assembly = AssemblyTools(conn)
    colour = AppearanceTools(conn)
    export = ExportTools(conn)

    try:
        before_active = str(conn.app.ActiveDocument.Name)
    except Exception:  # noqa: BLE001
        before_active = ""
    existing = {str(conn.documents.Item(i).Name)
                for i in range(1, conn.documents.Count + 1)}
    say(f"session before: {conn.documents.Count} document(s), active '{before_active}'")
    os.makedirs(ROOT, exist_ok=True)
    for stale in os.listdir(ROOT):
        try:
            os.remove(os.path.join(ROOT, stale))
        except OSError:
            pass

    try:
        # ── two parts and an assembly with 3 instances ────────────────────
        say("\n0  two parts, one assembly")
        for index, size in ((1, 20), (2, 12)):
            docs.execute("catia_new_part", {"name": f"P{index}",
                                            "file_path": os.path.join(ROOT, f"p{index}.CATPart")})
            sketcher.execute("catia_create_sketch", {"plane": "xy"})
            sketcher.execute("catia_sketch_rectangle",
                             {"x1": 0, "y1": 0, "x2": size, "y2": size})
            sketcher.execute("catia_close_sketch", {})
            design.execute("catia_pad", {"height": size})
            docs.execute("catia_save_document", {})
        asm_path = os.path.join(ROOT, "asm.CATProduct")
        docs.execute("catia_new_product", {"name": "ASM", "file_path": asm_path})
        assembly.execute("catia_add_component",
                         {"file_path": os.path.join(ROOT, "p1.CATPart"), "count": 2})
        assembly.execute("catia_add_component",
                         {"file_path": os.path.join(ROOT, "p2.CATPart")})
        listing = json.loads(assembly.execute("catia_list_components",
                                              {"names_only": True}))
        names = [entry["name"] for entry in listing["components"]]
        say(f"      components: {names}")
        check("three instances of two parts are in the assembly",
              len(names) == 3, str(names))

        # ── 1: colourize ──────────────────────────────────────────────────
        say("\n1  catia_colorize_assembly")
        reply = colour.execute("catia_colorize_assembly", {})
        say("      " + reply.replace("\n", "\n      "))
        check("one colour per part: 2 colours over 3 instances",
              "2 colour(s) over 3 instance(s)" in reply, reply.splitlines()[0])
        check("the reply says the colours are instance colours in the CATProduct",
              "CATProduct" in reply and "untouched" in reply)
        check("every colour read back exactly", "read back exactly" in reply)
        pixels = _shot(export, "colourised")
        first_rgb = parse_colour("#E4572E")
        second_rgb = parse_colour("#2E86AB")
        say("      pixels: " + ", ".join(f"{rgb}x{n}" for rgb, n in pixels[:4]))
        check("the first palette colour is ON SCREEN, not only in the property",
              _shows(pixels, first_rgb), f"{first_rgb} in {pixels[:3]}")
        check("the second palette colour is ON SCREEN too",
              _shows(pixels, second_rgb), f"{second_rgb} in {pixels[:3]}")

        # ── 2: read them back ─────────────────────────────────────────────
        say("\n2  catia_get_color")
        read = colour.execute("catia_get_color", {})
        say("      " + read.replace("\n", "\n      "))
        first, second, third = _colours_of(assembly, read)
        check("the two instances of P1 share one colour", first == second,
              f"{first} vs {second}")
        check("P2 has a different colour", third not in (first, second),
              f"{third} vs {first}/{second}")
        check("no target is still uncoloured", "D2D2FF" not in read, read[:120])

        # ── 3: the listing field ──────────────────────────────────────────
        say("\n3  catia_list_components colour field")
        plain = json.loads(assembly.execute("catia_list_components", {}))
        check("no colour field unless asked for",
              all("color" not in entry for entry in plain["components"]),
              str(list(plain["components"][0])))
        with_colour = json.loads(assembly.execute("catia_list_components",
                                                  {"include_color": True}))
        entries = with_colour["components"]
        colours = [entry.get("color") for entry in entries]
        say(f"      {[(e['name'], e.get('color')) for e in entries]}")
        check("include_color=true reports the real colour on every instance",
              all(value and value.startswith("#") for value in colours), str(colours))
        check("the listing agrees with catia_get_color",
              colours[0] == first and colours[2] == third,
              f"{colours} vs {first}/{third}")

        # ── 4: one instance only ──────────────────────────────────────────
        say("\n4  catia_set_color on ONE instance")
        reply = colour.execute("catia_set_color",
                               {"target": "P1.2", "color": "#1A2B3C"})
        say("      " + reply.replace("\n", "\n      "))
        after = colour.execute("catia_get_color", {})
        first, second, third = _colours_of(assembly, after)
        check("the named instance took the colour", second == "#1A2B3C", second)
        check("its sibling did NOT", first != "#1A2B3C", first)
        check("the other part did NOT", third != "#1A2B3C", third)
        check("the reply names the sibling it left alone",
              "P1.1" in reply and "INSTANCE colour" in reply, reply.splitlines()[0])

        # ── 5: all_instances ──────────────────────────────────────────────
        say("\n5  all_instances=true")
        reply = colour.execute("catia_set_color",
                               {"target": "P1.1", "color": "green",
                                "all_instances": True})
        after = colour.execute("catia_get_color", {})
        first, second, third = _colours_of(assembly, after)
        check("both instances of P1 are green now",
              first == second == "#3CA03C", f"{first} {second}")
        check("P2 is untouched", third != "#3CA03C", third)
        check("the reply says 'every instance of'", "every instance of" in reply)

        # ── 6: the part file is untouched ─────────────────────────────────
        say("\n6  the .CATPart is not touched by an instance colour")
        part_one = _document(conn, "p1.CATPart")
        part_one.Activate()
        part_read = colour.execute("catia_get_color", {})
        say("      " + part_read.replace("\n", "\n      "))
        check("the part document still reads its uncoloured default",
              "D2D2FF" in part_read, part_read.splitlines()[0])

        # ── 7: part level, and one feature ────────────────────────────────
        say("\n7  part-level colour")
        reply = colour.execute("catia_set_color", {"color": "#AA5500"})
        say("      " + reply.replace("\n", "\n      "))
        check("the whole solid took it", "#AA5500" in reply)
        part = conn.get_active_part()
        body = conn.get_active_part_body()
        feature = body.Shapes.Item(1)
        feature_read = colour.execute("catia_get_color",
                                      {"target": str(feature.Name)})
        check("a feature inherits the body colour", "#AA5500" in feature_read,
              feature_read)
        reply = colour.execute("catia_set_color",
                               {"target": str(feature.Name), "color": "#001122"})
        body_read = colour.execute("catia_get_color", {})
        feature_read = colour.execute("catia_get_color",
                                      {"target": str(feature.Name)})
        check("a feature can carry its own colour",
              "#001122" in feature_read, feature_read)
        check("... and the body keeps its own",
              "#AA5500" in body_read and "#001122" not in body_read.split("\n")[1],
              body_read)
        docs.execute("catia_save_document", {})

        # ── 8: persistence through the tools ──────────────────────────────
        say("\n8  save, close, reopen")
        asm_doc = _document(conn, "asm.CATProduct")
        asm_doc.Activate()
        docs.execute("catia_save_document", {})
        asm_doc.Close()
        docs.execute("catia_open_document", {"file_path": asm_path})
        after = colour.execute("catia_get_color", {})
        say("      " + after.replace("\n", "\n      "))
        first, second, third = _colours_of(assembly, after)
        check("the instance colours survived the reopen",
              first == second == "#3CA03C" and third.startswith("#"),
              f"{first} {second} {third}")
        part_one.Activate() if _has_document(conn, "p1.CATPart") else None
        if _has_document(conn, "p1.CATPart"):
            part_read = colour.execute("catia_get_color", {})
            check("the part colour survived the reopen too",
                  "#AA5500" in part_read, part_read.splitlines()[0])

        # ── 9: the picture, after a reopen ────────────────────────────────
        say("\n9  the picture survives the reopen with the colours")
        _document(conn, "asm.CATProduct").Activate()
        pixels = _shot(export, "after_reopen")
        say("      pixels: " + ", ".join(f"{rgb}x{n}" for rgb, n in pixels[:4]))
        check("the green the instances were given is on screen after the reopen",
              _shows(pixels, parse_colour("#3CA03C")), f"in {pixels[:3]}")
        check("the part colour is not what the assembly shows "
              "(an instance colour wins)",
              not _shows(pixels, parse_colour("#AA5500")), f"in {pixels[:3]}")

    finally:
        say("\ncleanup")
        for name in sorted({str(conn.documents.Item(i).Name)
                            for i in range(1, conn.documents.Count + 1)}
                           - existing, reverse=True):
            for i in range(1, conn.documents.Count + 1):
                if str(conn.documents.Item(i).Name) == name:
                    try:
                        conn.documents.Item(i).Close()
                        say(f"   closed {name}")
                    except Exception as error:  # noqa: BLE001
                        say(f"   (could not close {name}: {error})")
                    break
        for stale in os.listdir(ROOT):
            try:
                os.remove(os.path.join(ROOT, stale))
            except OSError:
                pass
        try:
            os.rmdir(ROOT)
        except OSError:
            pass
        if before_active and _has_document(conn, before_active):
            _document(conn, before_active).Activate()
        say(f"session after: {conn.documents.Count} document(s)")

    failed = [label for label, ok, _ in RESULTS if not ok]
    say(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        say("FAILED: " + "; ".join(failed))
    return 1 if failed else 0


def _shot(export, label: str) -> list[tuple[tuple[int, int, int], int]]:
    """Screenshot and return its dominant colours - the only display check.

    The file lands in %TEMP%, never in the repo. CATIA writes JPEG whatever the
    extension asked for, and the size is the 3D window's, not the request.
    """
    target = os.path.join(os.environ.get("TEMP", ROOT), f"v0379_check_{label}")
    reply = export.execute("catia_screenshot", {"file_path": target + ".png"})
    made = [path for path in (target + ".png", target + ".jpg")
            if os.path.exists(path)]
    if not made:
        say(f"      (no screenshot: {str(reply)[:100]})")
        return []
    from collections import Counter

    from PIL import Image

    counts = Counter(Image.open(made[0]).convert("RGB").getdata())
    return counts.most_common(8)


def _hsv(rgb):
    from colorsys import rgb_to_hsv

    return rgb_to_hsv(*[channel / 255 for channel in rgb])


def _shows(pixels, rgb, tolerance: float = 0.02, mass: int = 1000) -> bool:
    """Is ``rgb`` on screen, allowing for CATIA's shading?

    Comparing RGB distances does NOT work and the first run of this script
    "failed" on a perfectly coloured picture: #E4572E (228, 87, 46) renders as
    (181, 68, 36) on a shaded face, which is 52 units away and obviously the
    same colour. Hue survives shading, so that is what is compared - with a
    saturation floor, because a grey cannot be a colour, and a pixel-mass floor,
    because a stray JPEG artefact is not a part.
    """
    target_hue, _target_sat, _target_val = _hsv(rgb)
    for value, count in pixels:
        if count < mass:
            continue
        hue, saturation, _val = _hsv(value)
        if saturation < 0.25:
            continue
        difference = min(abs(hue - target_hue), 1 - abs(hue - target_hue))
        if difference <= tolerance:
            return True
    return False


def _document(conn, name: str):
    for index in range(1, conn.documents.Count + 1):
        doc = conn.documents.Item(index)
        if str(doc.Name) == name:
            return doc
    raise LookupError(name)


def _has_document(conn, name: str) -> bool:
    try:
        _document(conn, name)
        return True
    except LookupError:
        return False


def _colours_of(assembly, text: str) -> tuple[str, str, str]:
    """The three hex values in component order, from catia_get_color's text."""
    found = []
    for line in text.splitlines():
        marker = line.find("#")
        if marker >= 0:
            found.append(line[marker:marker + 7])
    while len(found) < 3:
        found.append("")
    return found[0], found[1], found[2]


if __name__ == "__main__":
    raise SystemExit(main())
