"""0.3.126 acceptance on B30: clean="solid_only" (the 2026-11-14 report's P1-3).

The question a clean screenshot has to answer is not "did something get hidden" but
"is the SOLID still in the picture". That is measured here, not eyeballed: the solid
fills the middle of the frame and the background does not, so the mean grey level of
the centre third separates the two states by a wide margin. Calibrated on B30
2026-11-14 against pictures whose content was known:

    solid on screen, wide body ... 71 % of the centre box is light (>150 grey)
    solid on screen, narrow body . 35 %
    solid gone (grid only) ....... 14 %

A MEAN was tried first and rejected: a narrow solid pulls the mean down to the
same place a missing one does (115 against 62), so it would have called a good
picture empty. The fraction of light pixels separates both shapes from the grid
with the threshold at 25 %, which is why that is the measure.

    .venv/Scripts/python.exe scripts/b30_v0428_audit_clean.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0428")
ROOT = os.path.join(_PROBE_ROOT, "audit_clean")
RESULTS: list[tuple[int, str, bool, str]] = []

SOLID_ON = 0.25          # fraction of light pixels in the centre box


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, title: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, title, bool(passed), detail))
    say(f"  [{'PASS' if passed else 'FAIL'}] {number}. {title}"
        + (f"  ({detail})" if detail else ""))


def centre_brightness(path: str) -> float:
    """Fraction of the centre box that is LIGHT - the solid, not the background."""
    image = Image.open(path).convert("L")
    width, height = image.size
    box = image.crop((int(width * 0.30), int(height * 0.20),
                      int(width * 0.70), int(height * 0.60)))
    pixels = list(box.get_flattened_data()) if hasattr(box, "get_flattened_data") \
        else list(box.getdata())
    return sum(1 for value in pixels if value > 150) / len(pixels)


def fresh(conn: CATIAConnection, documents: DocumentTools, name: str):
    path = os.path.join(ROOT, name + ".CATPart")
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
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    for index in range(1, int(conn.documents.Count) + 1):
        if conn.documents.Item(index).Name == name + ".CATPart":
            conn.documents.Item(index).Activate()
    return conn.get_active_part()


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    export = ExportTools(conn)

    # The report's shape: sketch sections, no geometrical set anywhere.
    fresh(conn, documents, "AuditClean")
    for offset, major, minor in ((0, 9, 4.5), (20, 6, 3)):
        sketcher.execute("catia_create_sketch",
                         {"plane": {"type": "offset", "base": "xy",
                                    "distance": offset}})
        sketcher.execute("catia_sketch_ellipse",
                         {"cx": 0, "cy": 0, "major": major, "minor": minor})
        sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_solid_loft",
                        {"sections": ["草图.1", "草图.2"], "name": "Body"})
    # A light grey so the centre-brightness test has a stable target whatever the
    # CATIA default colour happens to be.
    try:
        appearance_colour = part_design.execute(
            "catia_set_color", {"target": "Body", "color": [215, 220, 230]})
        say("colour: " + " ".join(str(appearance_colour).split())[:110])
    except Exception as error:  # noqa: BLE001 - the test does not depend on it
        say(f"colour not set: {str(error)[:100]}")

    def shoot(tag: str, clean=None):
        path = os.path.join(ROOT, f"{tag}.jpg")
        arguments = {"file_path": path}
        if clean is not None:
            arguments["clean"] = clean
        export.execute("catia_fit_all", {})
        try:
            reply = str(export.execute("catia_screenshot", arguments))
        except Exception as error:  # noqa: BLE001 - a refusal is an answer
            return path, f"REFUSED {getattr(error, 'code', '')}: {str(error)[:160]}"
        return path, " ".join(reply.split())

    say("")
    say("── A: no cleaning - the baseline the report complained about ──")
    path_a, reply_a = shoot("A_plain")
    bright_a = centre_brightness(path_a)
    say("  " + reply_a[:150])
    check(1, "the baseline picture has the solid in it", bright_a > SOLID_ON,
          f"{bright_a * 100:.1f} % light")

    say("")
    say("── B: clean='solid_only' ──")
    path_b, reply_b = shoot("B_solid_only", "solid_only")
    bright_b = centre_brightness(path_b)
    say("  " + reply_b[:330])
    check(2, "the solid is STILL in the picture after cleaning",
          bright_b > SOLID_ON, f"{bright_b * 100:.1f} % light")
    check(3, "the sketches inside the body were hidden, and the reply says so",
          "sketch(es)" in reply_b and "hidden for the capture" in reply_b,
          "reply names the sketches")
    check(4, "something actually changed - the picture is not the plain one",
          open(path_a, "rb").read() != open(path_b, "rb").read(),
          "bytes differ")

    say("")
    say("── C: clean=true keeps its 0.3.53 meaning (geometrical sets only) ──")
    path_c, reply_c = shoot("C_sets", True)
    bright_c = centre_brightness(path_c)
    say("  " + reply_c[:300])
    check(5, "clean=true does not hide sketches, and says why it did nothing",
          bright_c > SOLID_ON and "no geometrical sets" in reply_c,
          "solid kept, mode still 'sets'")

    say("")
    say("── D: the ROOT CAUSE - kind='construction' no longer takes the solid ──")
    hidden = " ".join(str(part_design.execute(
        "catia_set_visibility",
        {"kind": "construction", "show": False})).split())
    say("  " + hidden[:320])
    path_d, reply_d = shoot("D_construction_hidden")
    bright_d = centre_brightness(path_d)
    check(6, "hiding construction leaves the solid on screen now",
          bright_d > SOLID_ON, f"{bright_d * 100:.1f} % light (14 % = the old failure)")
    check(7, "the reply no longer warns that the solid can disappear",
          "can take the SOLID off the screen" not in hidden
          and "SOLID stays exactly where it is" in hidden,
          "warning replaced by the measurement")

    shown = " ".join(str(part_design.execute(
        "catia_set_visibility",
        {"kind": "construction", "show": True})).split())
    path_e, _reply_e = shoot("E_restored")
    bright_e = centre_brightness(path_e)
    check(8, "showing them again puts the picture back",
          bright_e > SOLID_ON and "now shown" in shown,
          f"{bright_e * 100:.1f} % light")

    say("")
    say("── F: the pathological case - a solid whose sections are GSD curves ──")
    # The report's second experiment: a solid loft whose section CURVES are GSD
    # elements (the 0.3.124 sweep shape). Hiding those is the case 0.3.115 wrote
    # about, and it must still leave the solid alone.
    fresh(conn, documents, "AuditCleanGSD")
    from catia_mcp.tools.gsd import GSDTools
    gsd = GSDTools(conn)
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [0, 0, 40],
                                   "name": "Spine"})
    gsd.execute("catia_solid_sweep_variable",
                {"spine": "Spine", "sections": [[0, 5.4, 4.8], [1, 2.7, 2.4]],
                 "name": "Stem"})
    path_f, reply_f = shoot("F_gsd_solid_only", "solid_only")
    bright_f = centre_brightness(path_f)
    say("  " + reply_f[:260])
    check(9, "a GSD-section solid also survives clean='solid_only'",
          bright_f > SOLID_ON, f"{bright_f * 100:.1f} % light")
    hidden_f = " ".join(str(part_design.execute(
        "catia_set_visibility",
        {"kind": "construction", "show": False})).split())
    path_g, _ = shoot("G_gsd_construction_hidden")
    bright_g = centre_brightness(path_g)
    check(10, "and survives kind='construction' too (the 0.3.115 case)",
          bright_g > SOLID_ON, f"{bright_g * 100:.1f} % light")

    say("")
    passed = sum(1 for _n, _t, ok, _d in RESULTS if ok)
    say(f"{passed}/{len(RESULTS)} checks passed")
    for number, title, ok, detail in RESULTS:
        if not ok:
            say(f"  FAILED {number}. {title}  {detail}")
    say("pictures: " + ROOT)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
