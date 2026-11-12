"""0.3.103 acceptance: text from a font in the box, and the Rib that was not built.

  1  a capital is exactly the cap height asked for
  2  the font's strokes reach the sketch, segment for segment
  3  the sketch holds the coordinates the font computed
  4  the text projects onto a lofted face
  5  coverage decides whether the projection can carry a parameter: the SAME 'O'
     fails at 81 % and passes at 57 % - the finding that made a warning necessary
  6  AddNewRib exists and updates into a solid
  7  ...and sweep + close makes the SAME solid, which is why no Rib tool was added
  8  the ceilings record both of those

Every document is this script's own, and each experiment gets a fresh one.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.fonts import text_to_strokes  # noqa: E402
from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0403")
ROOT = os.path.join(_PROBE_ROOT, "audit")
RESULTS: list[tuple[int, str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, label: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, label, passed, detail))
    say(f"[{'OK ' if passed else 'FAIL'}] {number}. {label}" + (f"  |  {detail}" if detail else ""))


def close_ours(conn: CATIAConnection) -> None:
    for index in range(int(conn.app.Documents.Count), 0, -1):
        document = conn.app.Documents.Item(index)
        try:
            full = str(document.FullName or "")
        except Exception:  # noqa: BLE001
            full = ""
        if full and os.path.abspath(full).startswith(os.path.abspath(ROOT)):
            try:
                document.Close()
            except Exception:  # noqa: BLE001
                pass


def bottle(docs, gsd, sk, tag: str):
    docs.execute("catia_new_part", {"file_path": os.path.join(ROOT, f"{tag}.CATPart"),
                                    "overwrite": True})
    for index, (radius, z) in enumerate(((30, 0), (34, 25), (22, 60))):
        gsd.execute("catia_gsd_point", {"x": 0, "y": 0, "z": z,
                                        "name": f"L{index}"})
        gsd.execute("catia_gsd_circle", {"center": f"L{index}",
                                         "support_plane": "xy", "radius": radius,
                                         "name": f"Sec{index}"})
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["Sec0", "Sec1", "Sec2"], "name": "Bottle"})
    sk.execute("catia_create_sketch", {"plane": "zx", "offset": -40})


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    docs, gsd, sk = DocumentTools(conn), GSDTools(conn), SketcherTools(conn)
    pd = PartDesignTools(conn)

    say("0.3.103 acceptance - text from the box, and the Rib that was not built")
    say("=" * 78)

    # ── 1: the font's own arithmetic ──────────────────────────────────
    glyph = text_to_strokes("A", height_mm=10.0)
    ys = [point[1] for stroke in glyph.strokes for point in stroke]
    check(1, "a 10 mm capital is exactly 10 mm tall",
          abs(max(ys) - 10.0) < 1e-9 and abs(min(ys)) < 1e-9,
          f"y {min(ys):.4f} .. {max(ys):.4f}, {len(glyph.strokes)} strokes")

    # ── 2 + 3: the strokes reach the sketch unchanged ────────────────
    bottle(docs, gsd, sk, "text")
    reply = sk.execute("catia_text_strokes",
                       {"text": "O", "height": 20.0, "origin": [0.0, 0.0]})
    flat = " ".join(str(reply).split())
    sk.execute("catia_close_sketch", {})
    wanted = text_to_strokes("O", height_mm=20.0, origin=(0.0, 0.0))
    segments = sum(len(stroke) - 1 for stroke in wanted.strokes)
    check(2, f"the font's {segments} segments are drawn in one call",
          f"drawn as {segments} straight line" in flat,
          f"{len(wanted.strokes)} stroke(s), {segments} segment(s)")
    check(3, "...and the reply warns about coverage before the caller engraves",
          "81 %" in flat and "57 %" in flat, "warning present")

    part = conn.get_active_part()
    name = str(part.MainBody.Sketches.Item(int(part.MainBody.Sketches.Count)).Name)
    geometry = sk.execute("catia_sketch_get_geometry", {})
    xs = [point[0] for stroke in wanted.strokes for point in stroke]
    found = [value for value in xs if f"{value:.4f}" in geometry]
    check(4, "the sketch holds the coordinates the font computed",
          bool(found), f"{len(found)}/{len(xs)} x-values verbatim")

    # ── 5: coverage decides whether the projection can carry a parameter ──
    projected = gsd.execute("catia_gsd_project",
                            {"element": name, "support": "Bottle", "name": "P_high"})
    flat_projection = " ".join(str(projected).split())
    match = re.search(r"(\d+\.\d+) % of", flat_projection)
    share = float(match.group(1)) if match else None
    refused = False
    try:
        gsd.execute("catia_gsd_plane_normal_to_curve",
                    {"curve": "P_high", "position": 0.5, "name": "PN_high"})
    except Exception:  # noqa: BLE001
        refused = True
    check(5, f"'{'O'}' at origin (0,0) lands {share} % and refuses a parameter",
          share is not None and share > 75.0 and refused,
          "refused, as measured" if refused else "it accepted - behaviour changed")

    # the same letter, moved, in its own document
    bottle(docs, gsd, sk, "text_low")
    sk.execute("catia_text_strokes",
               {"text": "O", "height": 20.0, "origin": [-9.0, 0.0]})
    sk.execute("catia_close_sketch", {})
    part = conn.get_active_part()
    low_name = str(part.MainBody.Sketches.Item(
        int(part.MainBody.Sketches.Count)).Name)
    low = gsd.execute("catia_gsd_project",
                      {"element": low_name, "support": "Bottle", "name": "P_low"})
    low_match = re.search(r"(\d+\.\d+) % of", " ".join(str(low).split()))
    low_share = float(low_match.group(1)) if low_match else None
    accepted = True
    try:
        gsd.execute("catia_gsd_plane_normal_to_curve",
                    {"curve": "P_low", "position": 0.5, "name": "PN_low"})
    except Exception:  # noqa: BLE001
        accepted = False
    check(6, f"the same 'O' at origin (-9,0) lands {low_share} % and DOES take it",
          low_share is not None and low_share < 65.0 and accepted,
          f"share {low_share} %, accepted {accepted}")

    # ── 7 + 8: a Rib is available, and it buys nothing ─────────────
    docs.execute("catia_new_part", {"file_path": os.path.join(ROOT, "rib.CATPart"),
                                    "overwrite": True})
    part = conn.get_active_part()
    sk.execute("catia_create_sketch", {"plane": "xy"})
    sk.execute("catia_sketch_arc", {"cx": 0, "cy": 0, "radius": 40,
                                    "start_angle": 0, "end_angle": 90})
    part.Update()
    centre = part.MainBody.Sketches.Item(1)
    import math
    across = math.sqrt(1600 - 900)
    sk.execute("catia_create_sketch", {"plane": "yz", "offset": 30})
    sk.execute("catia_sketch_circle", {"cx": across, "cy": 0, "radius": 3})
    profile = part.MainBody.Sketches.Item(int(part.MainBody.Sketches.Count))
    rib = None
    try:
        rib = part.ShapeFactory.AddNewRib(profile, centre)
        part.InWorkObject = part.MainBody
        part.UpdateObject(rib)
        volume = pd._body_volume_mm3(part, conn.get_active_part_body())
        check(7, "AddNewRib updates into a solid on B30",
              volume is not None and volume > 1000.0,
              f"{volume:.4f} mm3" if volume is not None else "unreadable")
        rib_volume = volume
    except Exception as error:  # noqa: BLE001
        rib_volume = None
        check(7, "AddNewRib updates into a solid on B30", False,
              str(error).splitlines()[0][:120])

    docs.execute("catia_new_part", {"file_path": os.path.join(ROOT, "sweep.CATPart"),
                                    "overwrite": True})
    part = conn.get_active_part()
    sk.execute("catia_create_sketch", {"plane": "xy"})
    sk.execute("catia_sketch_arc", {"cx": 0, "cy": 0, "radius": 40,
                                    "start_angle": 0, "end_angle": 90})
    part.Update()
    arc_name = str(part.MainBody.Sketches.Item(1).Name)
    sk.execute("catia_create_sketch", {"plane": "yz", "offset": 30})
    sk.execute("catia_sketch_circle", {"cx": across, "cy": 0, "radius": 3})
    profile_name = str(part.MainBody.Sketches.Item(
        int(part.MainBody.Sketches.Count)).Name)
    gsd.execute("catia_gsd_sweep", {"profile": profile_name, "guide": arc_name,
                                    "name": "Tube"})
    gsd.execute("catia_gsd_close_surface", {"surface": "Tube"})
    swept = pd._body_volume_mm3(part, conn.get_active_part_body())
    same = (rib_volume is not None and swept is not None
            and abs(rib_volume - swept) < 1e-6)
    check(8, "sweep + close makes the SAME solid, so a Rib tool would add nothing",
          same,
          f"rib {rib_volume:.4f} vs sweep {swept:.4f}" if same else
          f"rib {rib_volume} vs sweep {swept}")

    # ── 9: the ceilings carry both ──────────────────────────────────
    rib_entry = next((item for item in _CAPABILITY_CEILINGS
                      if item["member"] == "AddNewRib / AddNewStiffener"), None)
    glyph_entry = next((item for item in _CAPABILITY_CEILINGS
                        if "PROJECTED GLYPH" in item["member"]), None)
    ceiling_ok = (rib_entry is not None
                  and rib_entry["status"] == "AVAILABLE_WITH_LIMIT"
                  and glyph_entry is not None
                  and glyph_entry["status"] == "MEASURED_UNEXPLAINED")
    check(9, "the ceilings say Rib is available-with-limit and the glyph case is "
             "measured-unexplained", ceiling_ok,
          f"{rib_entry['status'] if rib_entry else 'missing'} / "
          f"{glyph_entry['status'] if glyph_entry else 'missing'}")

    close_ours(conn)
    say("")
    passed = sum(1 for _n, _l, ok, _d in RESULTS if ok)
    say(f"{passed}/{len(RESULTS)} passed")
    for number, label, ok, detail in RESULTS:
        if not ok:
            say(f"  FAILED {number}: {label} - {detail}")
    raise SystemExit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
