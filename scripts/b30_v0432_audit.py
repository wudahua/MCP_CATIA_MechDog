"""0.3.130 acceptance on B30 - the 2026-11-14 (v0.3.128) report's blueprint items.

Every case is either a measurement of the REPORT'S OWN FILE (page size against
the page a PDF reader reports, arcs fitted, geometry clipped away, the frame's
thin edge lines) or one fresh document whose volume is known by hand.

    1  page selection       the real 2-page AirPods sheet: each page is A4
                            landscape, and page 1 and page 2 are different content
    2  clipping             the same sheet: clip paths applied, outside-paint dropped
    3  curves               the same sheet: Beziers that ARE circles come back as arcs
    4  N9 frame lines       max_aspect removes the 346 x 0.169 mm frame edge
    5  N7 histogram         the size cliff, so min_size is read not guessed
    6  N6 round hole        synthetic PDF, circle as 4 Beziers, pad 10 ->
                            7 214.6 mm3 (= (40*20 - pi*25) * 10); a 16-gon hole
                            would be 7 234.6, so the measurement decides it
    7  N4 clip on the solid the same synthetic sheet with a clip: the imported
                            area is the clipped one, not the painted one
    8  N5 ascii_fallback    two Chinese names, both created, second disambiguated
    9  N10 semi-axis names  an ellipse's constraints say MajorRadius / MinorRadius

    .venv/Scripts/python.exe -u scripts/b30_v0432_audit.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp import blueprint  # noqa: E402
from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.blueprint import BlueprintTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

PDF = ("F:/WokrBuddy Catia Space/苹果入耳式耳机AirPods/"
       "苹果入耳式耳机AirPods尺寸平面图.pdf")
ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0432a")
CHECKS: list[tuple[int, str, bool, str]] = []
PT = 25.4 / 72.0


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, title: str, passed: bool, detail: str = "") -> None:
    CHECKS.append((number, title, bool(passed), detail))
    say(f"  [{'PASS' if passed else 'FAIL'}] {number}. {title}"
        + (f"  ({detail})" if detail else ""))


# ── a synthetic one-page vector PDF whose area is known by hand ────────────
def make_pdf(path: str, content: bytes, width: float = 595.276,
             height: float = 841.89) -> str:
    """A minimal, uncompressed, single-page PDF - readable by any conforming parser."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width:g} {height:g}] "
         f"/Resources << >> /Contents 4 0 R >>").encode(),
        (f"<< /Length {len(content)} >>\nstream\n").encode() + content
        + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    start = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n"
            f"{start}\n%%EOF\n").encode()
    with open(path, "wb") as handle:
        handle.write(bytes(out))
    return path


K = 0.5522847498


def circle_as_beziers(cx: float, cy: float, r: float) -> bytes:
    """The four cubics every drawing program uses for a circle - in POINTS."""
    k = K * r
    return (f"{cx + r:g} {cy:g} m\n"
            f"{cx + r:g} {cy + k:g} {cx + k:g} {cy + r:g} {cx:g} {cy + r:g} c\n"
            f"{cx - k:g} {cy + r:g} {cx - r:g} {cy + k:g} {cx - r:g} {cy:g} c\n"
            f"{cx - r:g} {cy - k:g} {cx - k:g} {cy - r:g} {cx:g} {cy - r:g} c\n"
            f"{cx + k:g} {cy - r:g} {cx + r:g} {cy - k:g} {cx + r:g} {cy:g} c\n"
            "h\n").encode()


def plate_with_hole(path: str, hole_radius_mm: float = 5.0) -> str:
    """40 x 20 mm plate with a round hole in the middle, 1:1."""
    w, h = 40 / PT, 20 / PT
    cx, cy = w / 2, h / 2
    content = ((f"q\n0 0 m\n{w:g} 0 l\n{w:g} {h:g} l\n0 {h:g} l\nh\nS\n").encode()
               + circle_as_beziers(cx, cy, hole_radius_mm / PT)
               + b"S\nQ\n")
    return make_pdf(path, content, width=600, height=300)


def plate_with_clip(path: str) -> str:
    """A 20 x 20 mm square visible, and a 40 x 40 mm one painted OUTSIDE the clip.

    The report's N4: 112 clipping paths were ignored, so geometry that was never
    on the page was imported. The outside square is what must not arrive - and the
    inside one must, unchanged, or the clip has eaten real geometry.
    """
    def square(origin: float, side: float) -> str:
        return (f"{origin:g} {origin:g} m\n{origin + side:g} {origin:g} l\n"
                f"{origin + side:g} {origin + side:g} l\n"
                f"{origin:g} {origin + side:g} l\nh\nS\n")

    inside = 20 / PT
    outside = 40 / PT
    content = (f"q\n0 0 200 200 re W n\n"
               + square(14.17, inside)          # 5 mm in, 20 mm across
               + square(280, outside)           # mm 98.4..138.6: outside the clip
               + "Q\n").encode()
    return make_pdf(path, content, width=600, height=600)


# ── CATIA helpers ─────────────────────────────────────────────────────────
def close_probe_documents(conn) -> None:
    """Close only the documents that live under this probe's own folder.

    A crashed earlier run leaves them open, and catia_new_part then refuses the
    path - correctly, because the file is held. Nothing of the user's is touched:
    the folder is under %TEMP%.
    """
    root = os.path.abspath(ROOT).lower()
    for index in range(int(conn.documents.Count), 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = str(doc.FullName or "")
        except Exception:  # noqa: BLE001 - a pathless document is not ours
            continue
        if full and os.path.abspath(full).lower().startswith(root):
            try:
                doc.Close()
            except Exception:  # noqa: BLE001 - a dialog is the caller's problem
                pass


def fresh(conn, doc_tools, name: str) -> str:
    """One NEW document per case - a document-level failure would hide the rest."""
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, name + ".CATPart")
    close_probe_documents(conn)
    if os.path.exists(path):
        os.remove(path)
    doc_tools.execute("catia_new_part", {"name": name, "file_path": path})
    return path


def main() -> int:
    say("0.3.130 acceptance - 2026-11-14 report (v0.3.128), blueprint items")
    say(f"  sheet: {PDF}")

    # 1-5: measurements of the report's own file, no CATIA involved -----------
    say("\n[1-5] the report's own two-page sheet")
    full = blueprint.read_drawing(PDF)
    pages = full["pages"]
    check(1, "each page is A4 landscape, not a union of two views",
          len(pages) == 2
          and all(abs(p["size_mm"][0] - 297.0) < 0.5
                  and abs(p["size_mm"][1] - 210.0) < 0.5 for p in pages),
          f"pages={[p['size_mm'] for p in pages]}")
    one = blueprint.read_drawing(PDF, page=1)
    two = blueprint.read_drawing(PDF, page=2)
    check(1, "page=1 and page=2 are different content",
          one["counts"]["contours"] != two["counts"]["contours"]
          and one["counts"]["contours"] > 0 and two["counts"]["contours"] > 0,
          f"page1={one['counts']['contours']} page2={two['counts']['contours']} "
          f"contours")
    check(1, "nothing lands outside the page box",
          max(one["bounds_mm"][2] - one["page_bounds_mm"][2],
              one["bounds_mm"][3] - one["page_bounds_mm"][3]) <= 0.01,
          f"bounds={[round(v, 2) for v in one['bounds_mm']]} "
          f"page={[round(v, 2) for v in one['page_bounds_mm']]}")

    stats = one["stats"]
    check(2, "clip paths are APPLIED, not counted",
          stats["clip_ops"] > 0 and stats["clip_applied"] > 0,
          f"{stats['clip_applied']} of {stats['clip_ops']} applied, "
          f"{stats['clip_ignored']} not rectangular")
    check(2, "paint outside the visible area is dropped",
          stats["clipped_out"] > 0,
          f"{stats['clipped_out']} path(s) dropped, {stats['clip_trimmed']} trimmed")

    check(3, "a circle in a PDF comes back as an ARC, not as chords",
          stats["arcs_fitted"] > 100,
          f"{stats['arcs_fitted']} arcs fitted, {stats['curves_flattened']} "
          "genuine free-form curves sampled")

    tight = blueprint.read_drawing(PDF, page=1, min_size=10, closed_only=True,
                                   max_aspect=50)
    loose = blueprint.read_drawing(PDF, page=1, min_size=10, closed_only=True)
    check(4, "max_aspect removes the frame's 346 x 0.169 mm edge line",
          tight["counts"]["dropped"]["thin"] > 0
          and tight["counts"]["contours"] < loose["counts"]["contours"],
          f"{loose['counts']['contours']} -> {tight['counts']['contours']} "
          f"contours, {tight['counts']['dropped']['thin']} dropped as thin")
    widest = max((max(b[2] - b[0], b[3] - b[1]) / max(1e-9, min(b[2] - b[0],
                  b[3] - b[1])) for b in (blueprint.contour_bbox(c)
                                          for c in tight["contours"])), default=0)
    check(4, "nothing longer than 50:1 survives the filter", widest <= 50.0,
          f"widest kept contour {widest:.1f}:1")

    diagonals = sorted(math.hypot(b[2] - b[0], b[3] - b[1])
                       for b in (blueprint.contour_bbox(c)
                                 for c in one["contours"]))
    table = blueprint.size_histogram(diagonals)
    bands = {row["mm"]: row["contours"] for row in table}
    check(5, "the size histogram accounts for every contour",
          sum(bands.values()) == len(diagonals),
          f"{len(diagonals)} contours, cliff at 10-20: "
          f"{bands.get('5-10')} below, {bands.get('10-20')} in it")

    # 6-7: what CATIA makes of it -------------------------------------------
    say("\n[6-9] what CATIA builds from it")
    conn = CATIAConnection()
    conn.ensure_connected()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    measure = MeasurementTools(conn)
    sketcher = SketcherTools(conn)
    drawings = BlueprintTools(conn, sketcher)
    close_probe_documents(conn)

    path = plate_with_hole(os.path.join(tempfile.gettempdir(),
                                        "v0432_plate_hole.pdf"))
    fresh(conn, docs, "V0432_RoundHole")
    drawings.execute("catia_import_drawing",
                     {"file_path": path, "closed_only": True, "min_size": 1})
    parts.execute("catia_pad", {"height": 10})
    volume = float(json.loads(measure.execute("catia_get_inertia", {})).get("volume_mm3") or 0.0)
    expected = (40 * 20 - math.pi * 25) * 10
    sixteen_gon = (40 * 20 - 0.5 * 16 * 25 * math.sin(2 * math.pi / 16)) * 10
    check(6, "the hole is ROUND: volume matches area x 10, not a 16-sided hole",
          abs(volume - expected) < 1.0,
          f"{volume:.2f} vs {expected:.2f} mm3 (a 16-gon would be "
          f"{sixteen_gon:.2f})")

    clipped = plate_with_clip(os.path.join(tempfile.gettempdir(),
                                           "v0432_clip.pdf"))
    parsed = blueprint.read_drawing(clipped, closed_only=True, min_size=1)
    fresh(conn, docs, "V0432_Clipped")
    drawings.execute("catia_import_drawing",
                     {"file_path": clipped, "closed_only": True, "min_size": 1})
    parts.execute("catia_pad", {"height": 5})
    volume = float(json.loads(measure.execute("catia_get_inertia", {})).get("volume_mm3") or 0.0)
    check(7, "only what the clip leaves visible is imported",
          parsed["counts"]["contours"] == 2 - 1
          and parsed["stats"]["clipped_out"] >= 1
          and abs(volume - 20 * 20 * 5) < 1.0,
          f"{volume:.2f} vs {20 * 20 * 5} mm3, "
          f"{parsed['stats']['clipped_out']} painted path(s) dropped, "
          f"{parsed['counts']['contours']} contour(s) kept")

    # 8: two Chinese names, both must be creatable ---------------------------
    os.makedirs(ROOT, exist_ok=True)
    first = os.path.join(ROOT, "苹果AirPods参数化版.CATPart")
    second = os.path.join(ROOT, "苹果AirPods一二代.CATPart")
    for stale in (first, second):
        if os.path.exists(stale):
            os.remove(stale)
    reply = docs.execute("catia_new_part",
                         {"file_path": first, "ascii_fallback": True})
    plain = os.path.join(ROOT, "AirPods.CATPart")
    ok_first = os.path.exists(plain)
    try:
        docs.execute("catia_new_part",
                     {"file_path": second, "ascii_fallback": True})
        hashed = [name for name in os.listdir(ROOT)
                  if name.startswith("AirPods_") and name.endswith(".CATPart")]
        check(8, "a second Chinese name gets its own file instead of a refusal",
              ok_first and len(hashed) == 1,
              f"files={sorted(os.listdir(ROOT))}")
    except Exception as error:  # noqa: BLE001 - the report's exact failure
        check(8, "a second Chinese name gets its own file instead of a refusal",
              False, str(error).splitlines()[0][:160])

    # 9: the semi-axes say which is which ------------------------------------
    fresh(conn, docs, "V0432_EllipseNames")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_ellipse",
                     {"cx": 0, "cy": 0, "major": 9, "minor": 4.5})
    guide = json.loads(sketcher.execute("catia_sketch_get_geometry", {}))
    entries = guide.get("data", guide).get("constraints", [])
    named = {entry.get("name"): entry.get("drives") for entry in entries}
    check(9, "the two semi-axes are named, so a formula cannot bind the wrong one",
          any(drive == "major_radius" for drive in named.values())
          and any(drive == "minor_radius" for drive in named.values()),
          json.dumps(named, ensure_ascii=False))

    say("\n" + "-" * 70)
    failed = [row for row in CHECKS if not row[2]]
    say(f"{len(CHECKS) - len(failed)}/{len(CHECKS)} passed")
    for number, title, _passed, detail in failed:
        say(f"  FAILED {number}. {title} - {detail}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
