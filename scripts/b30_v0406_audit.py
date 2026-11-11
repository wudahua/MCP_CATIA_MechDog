"""0.3.106 acceptance: the outline goes ON the face instead of being guessed onto it.

  1  catia_curve_on_surface snaps a glyph onto a closed lofted bottle and threads
     it into a spline, reporting how far every point moved
  2  that spline engraves at ratio ~1 with NO offset tuning - the thing the
     2026-11-11 report had to calibrate by hand six times for one word
  3  catia_sketch_ellipse makes a real ellipse, not a 28-flat polyline: the pad's
     volume is pi*a*b*h and the bounding box is the rotated one
  4  catia_new_part(overwrite=true) replaces a file instead of refusing it - the
     parameter its own refusal message had been telling callers to pass
  5  catia_text_strokes hands its points back when asked
  6  the engrave reply carries the dialogs-answered heartbeat even when it is 0
  7  the ceilings carry the measured snap limits

Everything happens in this script's own folder.
"""
from __future__ import annotations

import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp import fonts  # noqa: E402
from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0406\audit"
RESULTS: list[tuple[int, str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, label: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, label, passed, detail))
    say(f"[{'OK ' if passed else 'FAIL'}] {number}. {label}"
        + (f"  |  {detail}" if detail else ""))


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


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    for name in os.listdir(ROOT):
        if name.lower().endswith(".catpart"):
            try:
                os.remove(os.path.join(ROOT, name))
            except OSError:
                pass
    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    docs, gsd = DocumentTools(conn), GSDTools(conn)
    sk, pd = SketcherTools(conn), PartDesignTools(conn)
    me = MeasurementTools(conn)

    say("0.3.106 acceptance - put the outline on the face")
    say("=" * 78)

    # ── 1 + 2: snap a glyph, then engrave it ──────────────────────────
    docs.execute("catia_new_part", {"file_path": os.path.join(ROOT, "Snap.CATPart")})
    for index, (radius, z) in enumerate(((30, 0), (34, 25), (22, 60))):
        gsd.execute("catia_gsd_point", {"x": 0, "y": 0, "z": z, "name": f"L{index}"})
        gsd.execute("catia_gsd_circle", {"center": f"L{index}", "support_plane": "xy",
                                         "radius": radius, "name": f"Sec{index}"})
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["Sec0", "Sec1", "Sec2"], "name": "Bottle"})
    gsd.execute("catia_gsd_close_surface", {"surface": "Bottle"})

    text = fonts.text_to_strokes("C", height_mm=8.0, origin=(-4.0, 20.0))
    # zx maps u -> Z and v -> X, so (glyph_y, glyph_x) stands the letter up.
    strokes = [[[v, u] for (u, v) in stroke] for stroke in text.strokes]
    snap = " ".join(str(gsd.execute("catia_curve_on_surface",
                                    {"strokes": strokes, "plane": "zx",
                                     "offset": -60.0,
                                     "name": "SnapC"})).split())
    moved = re.search(r"travelled ([\d.]+) to ([\d.]+) mm \(mean ([\d.]+)\)", snap)
    check(1, "a glyph is snapped onto the bottle and reports how far it moved",
          bool(re.search(r"Snapped \d+ point\(s\)", snap)) and moved is not None,
          (f"{moved.group(1)}-{moved.group(2)} mm, mean {moved.group(3)}"
           if moved else snap[:110]))

    part = conn.get_active_part()
    body = conn.get_active_part_body()
    before = pd._body_volume_mm3(part, body)
    engrave = " ".join(str(gsd.execute("catia_surface_engrave",
                                       {"curve": "SnapC", "depth": 0.4})).split())
    after = pd._body_volume_mm3(part, body)
    removed = None if before is None or after is None else before - after
    predicted = re.search(r"against the ([\d.]+) mm3", engrave)
    ratio = (removed / float(predicted.group(1))
             if removed is not None and predicted else None)
    check(2, "...and the snapped spline engraves at ratio ~1 with no tuning",
          ratio is not None and 0.90 <= ratio <= 1.10,
          f"ratio {ratio:.3f}" if ratio is not None else engrave[:110])
    check(6, "the engrave reply carries the dialogs-answered heartbeat",
          "dialogs answered: 0" in engrave or "dialogs answered:" in engrave,
          engrave[-40:])

    # ── 3: a real ellipse ────────────────────────────────────────────
    docs.execute("catia_new_part", {"file_path": os.path.join(ROOT, "Ell.CATPart")})
    sk.execute("catia_create_sketch", {"plane": "xy"})
    ellipse = " ".join(str(sk.execute("catia_sketch_ellipse",
                                      {"cx": 0, "cy": 0, "major": 30, "minor": 10,
                                       "angle": 30})).split())
    sk.execute("catia_close_sketch", {})
    pd.execute("catia_pad", {"height": 5.0})
    volume = pd._body_volume_mm3(conn.get_active_part(),
                                conn.get_active_part_body())
    expected = math.pi * 30 * 10 * 5
    box = me.execute("catia_get_bounding_box", {})
    dims = re.findall(r'"(length_[xy])": ([\d.]+)', str(box))
    size = {name: float(value) for name, value in dims}
    # Rotated 30 deg the extents are 2*sqrt(a^2cos^2 + b^2sin^2) and its partner;
    # unrotated they would be 60 and 20, so this is what proves the angle arrived.
    want_x = 2 * math.sqrt(900 * math.cos(math.radians(30)) ** 2
                           + 100 * math.sin(math.radians(30)) ** 2)
    want_y = 2 * math.sqrt(900 * math.sin(math.radians(30)) ** 2
                           + 100 * math.cos(math.radians(30)) ** 2)
    good = (volume is not None and abs(volume - expected) < 0.01
            and abs(size.get("length_x", 0) - want_x) < 0.5
            and abs(size.get("length_y", 0) - want_y) < 0.5)
    check(3, "the ellipse is a real ellipse, rotated to the angle asked for",
          good,
          f"volume {volume:.4f} vs {expected:.4f}, box "
          f"{size.get('length_x')}x{size.get('length_y')} vs {want_x:.3f}x{want_y:.3f}")

    # ── 4: new_part overwrite ────────────────────────────────────────
    target = os.path.join(ROOT, "Over.CATPart")
    docs.execute("catia_new_part", {"file_path": target})
    docs.execute("catia_close_document", {})
    try:
        overwritten = " ".join(str(docs.execute("catia_new_part",
                                               {"file_path": target,
                                                "overwrite": True})).split())
        ok = "replaced" in overwritten.lower() or "saved" in overwritten.lower()
        detail = overwritten[:110]
    except Exception as error:  # noqa: BLE001
        ok, detail = False, str(error).splitlines()[0][:130]
    check(4, "catia_new_part(overwrite=true) replaces a file instead of refusing",
          ok, detail)

    # ── 5: text points come back ────────────────────────────────────
    sk.execute("catia_create_sketch", {"plane": "xy", "offset": 40.0})
    with_points = " ".join(str(sk.execute("catia_text_strokes",
                                         {"text": "L", "height": 8.0,
                                          "include_points": True})).split())
    without = " ".join(str(sk.execute("catia_text_strokes",
                                      {"text": "L", "height": 8.0})).split())
    check(5, "catia_text_strokes hands the points back when asked",
          "POINTS (sketch mm" in with_points and "POINTS (sketch mm" not in without,
          with_points[with_points.find("POINTS"):][:70])

    # ── 7: the ceilings ─────────────────────────────────────────────
    entry = next(e for e in _CAPABILITY_CEILINGS
                 if "PointOnSurfaceWithReference" in e["member"])
    check(7, "the ceiling carries the measured limits of the snap",
          all(said in entry["detail"]
              for said in ("26.0052", "0.0022", "DIRECTION is ignored")),
          entry["status"])

    say()
    say("=" * 78)
    passed = sum(1 for _n, _l, ok, _d in RESULTS if ok)
    say(f"{passed}/{len(RESULTS)} passed")
    close_ours(conn)
    if passed != len(RESULTS):
        for number, label, ok, detail in RESULTS:
            if not ok:
                say(f"  FAILED {number}: {label} | {detail}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
