"""0.3.102 acceptance: the first projection in a document is a fragment.

The 2026-11-11 report's P2 route - "move the element closer with
catia_gsd_translate" - was measured and is wrong: a translated copy lands 3.5 %
where the sketch lands 48.2 %. What is true is that the FIRST projection made in
a document answers with a fragment and every later one answers with the real
curve (eight in a row, measured 2026-11-11: 2.200 / 32.111 x7). So the tool
projects twice and keeps the second.

  1  a glyph outline onto a lofted face: 48.2 %, and the reply says both passes
  2  the probe is gone - one new element, not two
  3  a straight cylinder is the case where both passes agree
  4  a rectangle onto a 45-degree plane lands the real perimeter (68.284, not 20)
  5  a translated COPY is still a fragment, and the reply says not to do that
  6  the projection 0.3.102 produces can be engraved

Everything happens in this script's own folder.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0402")
ROOT = os.path.join(_PROBE_ROOT, "audit")
RESULTS: list[tuple[int, str, bool, str]] = []
GLYPH = [[-9, 0], [-4, 20], [0, 20], [5, 0], [2.5, 0], [1, 5], [-1, 5],
         [-2.5, 0], [-9, 0]]


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, label: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, label, passed, detail))
    say(f"[{'OK ' if passed else 'FAIL'}] {number}. {label}"
        + (f"  |  {detail}" if detail else ""))


def close_ours(conn: CATIAConnection) -> None:
    for i in range(int(conn.app.Documents.Count), 0, -1):
        document = conn.app.Documents.Item(i)
        try:
            full = str(document.FullName or "")
        except Exception:  # noqa: BLE001
            full = ""
        if full and os.path.abspath(full).startswith(os.path.abspath(ROOT)):
            try:
                document.Close()
            except Exception:  # noqa: BLE001
                pass
    if os.path.isdir(ROOT):
        for name in os.listdir(ROOT):
            if name.lower().endswith(".catpart"):
                try:
                    os.remove(os.path.join(ROOT, name))
                except OSError:
                    pass


def elements(part) -> list[str]:
    names: list[str] = []

    def walk(container, depth: int) -> None:
        if depth > 4:
            return
        try:
            for i in range(1, int(container.HybridShapes.Count) + 1):
                names.append(str(container.HybridShapes.Item(i).Name))
        except Exception:  # noqa: BLE001
            pass
        try:
            for i in range(1, int(container.HybridBodies.Count) + 1):
                walk(container.HybridBodies.Item(i), depth + 1)
        except Exception:  # noqa: BLE001
            pass

    try:
        for i in range(1, int(part.HybridBodies.Count) + 1):
            walk(part.HybridBodies.Item(i), 1)
    except Exception:  # noqa: BLE001
        pass
    return names


def share_of(reply) -> float | None:
    found = re.search(r"projected curve is [\d.]+ mm long, ([\d.]+) %", str(reply))
    return float(found.group(1)) if found else None


def build_bottle(docs, gsd) -> None:
    for index, (radius, z) in enumerate(((30, 0), (34, 25), (22, 60))):
        gsd.execute("catia_gsd_point", {"x": 0, "y": 0, "z": z, "name": f"L{index}"})
        gsd.execute("catia_gsd_circle", {"center": f"L{index}",
                                         "support_plane": "xy",
                                         "radius": radius, "name": f"Sec{index}"})
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["Sec0", "Sec1", "Sec2"], "name": "Bottle"})


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    docs, gsd, sk = DocumentTools(conn), GSDTools(conn), SketcherTools(conn)
    pd = PartDesignTools(conn)

    say("0.3.102 acceptance - the first projection is a fragment")
    say("=" * 78)

    # ── 1 + 2: the bottle shape, and what is left in the tree ─────────
    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, "bottle_0402.CATPart")})
    build_bottle(docs, gsd)
    sk.execute("catia_create_sketch", {"plane": "zx", "offset": -40})
    sk.execute("catia_sketch_strokes", {"strokes": [GLYPH]})
    sk.execute("catia_close_sketch", {})
    stroke = str(conn.last_sketch.Name)
    part = conn.get_active_part()
    before = elements(part)
    reply = gsd.execute("catia_gsd_project",
                        {"element": stroke, "support": "Bottle", "name": "GlyphP"})
    after = elements(part)
    text = " ".join(str(reply).split())
    share = share_of(reply)
    check(1, "a glyph outline on a lofted face now lands 48.2 %, not 3.3 %",
          share is not None and 45.0 <= share <= 52.0, f"{share} %")
    check(2, "...and the reply says it projected twice",
          "projected TWICE and kept the second" in text
          and "2.200" in text and "32.111" in text, text[:120])
    added = [name for name in after if name not in before]
    check(3, "...and the throw-away pass is not left in the tree",
          len(added) == 1, f"added: {added}")

    # ── 3: the shape where both passes agree ──────────────────────────
    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, "cylinder_0402.CATPart")})
    gsd.execute("catia_gsd_point", {"x": 0, "y": 0, "z": 0, "name": "C0"})
    gsd.execute("catia_gsd_circle", {"center": "C0", "support_plane": "xy",
                                     "radius": 40, "name": "Lo"})
    gsd.execute("catia_gsd_point", {"x": 0, "y": 0, "z": 100, "name": "C1"})
    gsd.execute("catia_gsd_circle", {"center": "C1", "support_plane": "xy",
                                     "radius": 40, "name": "Hi"})
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["Lo", "Hi"], "name": "Wall"})
    sk.execute("catia_create_sketch", {"plane": "zx", "offset": -45})
    sk.execute("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": 6})
    sk.execute("catia_close_sketch", {})
    stroke = str(conn.last_sketch.Name)
    reply = gsd.execute("catia_gsd_project",
                        {"element": stroke, "support": "Wall", "name": "Round"})
    text = " ".join(str(reply).split())
    check(4, "on a straight cylinder both passes agree and the reply says so",
          "Both passes" in text and share_of(reply) is not None,
          f"{share_of(reply)} % | {text[text.find('Both passes'):][:70]}"
          if "Both passes" in text else text[:120])

    # ── 4: the flat case, where the first pass lost 75 % ──────────────
    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, "tilt_0402.CATPart")})
    gsd.execute("catia_gsd_plane_angle",
                {"plane": "xy", "axis": "x", "angle": 45, "name": "Tilt"})
    sk.execute("catia_create_sketch", {"plane": "xy", "offset": 40})
    sk.execute("catia_sketch_strokes",
               {"strokes": [[[-10, -10], [10, -10], [10, 10], [-10, 10],
                             [-10, -10]]]})
    sk.execute("catia_close_sketch", {})
    stroke = str(conn.last_sketch.Name)
    reply = gsd.execute("catia_gsd_project",
                        {"element": stroke, "support": "Tilt", "name": "Flat"})
    length = re.search(r"projected curve is ([\d.]+) mm", str(reply))
    value = float(length.group(1)) if length else 0.0
    check(5, "a rectangle on a 45-degree plane lands 68.284 mm, not 20.000",
          abs(value - 68.284) < 0.6, f"{value} mm")

    # ── 5: a translated copy is still a fragment ──────────────────────
    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, "copy_0402.CATPart")})
    build_bottle(docs, gsd)
    sk.execute("catia_create_sketch", {"plane": "zx", "offset": -40})
    sk.execute("catia_sketch_strokes", {"strokes": [GLYPH]})
    sk.execute("catia_close_sketch", {})
    stroke = str(conn.last_sketch.Name)
    gsd.execute("catia_gsd_translate",
                {"element": stroke, "direction": "y", "distance": 5.0,
                 "name": "Near"})
    reply = gsd.execute("catia_gsd_project",
                        {"element": "Near", "support": "Bottle", "name": "CopyP"})
    text = " ".join(str(reply).split())
    share = share_of(reply)
    check(6, "a translated copy still lands only 3.5 % (twice or not)",
          share is not None and share < 10.0, f"{share} %")
    check(7, "...and the warning tells the caller NOT to translate",
          "Do NOT reach for catia_gsd_translate" in text
          and "48.2" in text, text[:110])

    # ── 6: the projection can be engraved ─────────────────────────────
    # Project the SKETCH (not the copy) and engrave THAT: the copy above is kept
    # only to show what translating does.
    good = gsd.execute("catia_gsd_project",
                       {"element": stroke, "support": "Bottle",
                        "name": "GlyphGood"})
    good_share = share_of(good)
    part = conn.get_active_part()
    body = conn.get_active_part_body()
    gsd.execute("catia_gsd_close_surface", {"surface": "Bottle"})
    before_volume = pd._body_volume_mm3(part, body)
    gsd.execute("catia_surface_engrave",
                {"curve": "GlyphGood", "depth": 0.4})
    after_volume = pd._body_volume_mm3(part, body)
    removed = (None if before_volume is None or after_volume is None
               else before_volume - after_volume)
    check(8, "the curve 0.3.102 produces can be engraved",
          removed is not None and removed > 3.0,
          f"{good_share} % landed, removed {removed:.4f} mm3"
          if removed is not None else "volume unreadable")

    # ── 7: the ceilings carry it ──────────────────────────────────────
    entry = next((e for e in _CAPABILITY_CEILINGS
                  if "first projection in a document" in e["member"]), None)
    check(9, "the ceilings record the first-projection behaviour",
          entry is not None and "32.111" in entry["detail"]
          and "unknown" in entry["detail"].lower(),
          entry["status"] if entry else "missing")

    say("")
    passed = sum(1 for _n, _l, ok, _d in RESULTS if ok)
    say(f"{passed}/{len(RESULTS)} passed")
    for number, label, ok, detail in RESULTS:
        if not ok:
            say(f"  FAILED {number}. {label}  {detail}")
    say(f"documents left open under {ROOT}")


if __name__ == "__main__":
    main()
