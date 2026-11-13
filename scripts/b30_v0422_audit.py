"""0.3.120 acceptance on B30: CJK lettering that pads, and a shaft about any axis.

Two items, five checks, one fresh document each. The numbers are the ones the
probes measured (scripts/b30_v0422_probe_axis3.py and the axis5 run):

    1  '中文' at height 8 pads - an outline font's closed contours are a profile
    2  close_sketch calls that sketch a PROFILE, not lettering
    3  ASCII still comes from Hershey's single lines
    4  shaft about a gsd_line at x=60 gives 84823.00 mm3 (2*pi*45*300)
    5  shaft about the sketch's own H axis still gives 28274.33 mm3

    .venv/Scripts/python.exe scripts/b30_v0422_audit.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0422")
ROOT = os.path.join(_PROBE_ROOT, "audit")
RESULTS: list[tuple[int, str, bool, str]] = []
PAPPUS_H = 2 * 3.14159265358979 * 15.0 * 300.0     # 28274.33
PAPPUS_OFF = 2 * 3.14159265358979 * 45.0 * 300.0   # 84823.00


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, title: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, title, bool(passed), detail))
    say(f"  [{'PASS' if passed else 'FAIL'}] {number}. {title}"
        + (f"  ({detail})" if detail else ""))


def close_ours(conn: CATIAConnection) -> None:
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


def fresh(conn, documents, name: str):
    path = os.path.join(ROOT, f"{name}.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    for index in range(1, conn.documents.Count + 1):
        if conn.documents.Item(index).Name == f"{name}.CATPart":
            conn.documents.Item(index).Activate()
    return conn.get_active_part()


def volume(conn, part_design):
    part = conn.get_active_part()
    return part_design._body_volume_mm3(part, part.MainBody)


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    gsd = GSDTools(conn)

    # ── 1 + 2: Chinese lettering ─────────────────────────────────────────
    say("")
    say("── 1: '中文' at height 8, then pad it ──")
    fresh(conn, documents, "Cjk")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    drawn = sketcher.execute("catia_text_strokes",
                             {"text": "中文", "height": 8.0})
    say("  " + " ".join(str(drawn).split())[:300])
    closed = sketcher.execute("catia_close_sketch", {})
    say("  close: " + " ".join(str(closed).split())[:260])
    check(1, "the glyphs are drawn as closed contours from a real font",
          "OUTLINE FONT" in str(drawn) and "closed contour(s)" in str(drawn),
          "outline face, closed contours")

    # The wording that matters is the ADVICE, not the word "profile": the pad
    # invitation has to be there and the "features will REFUSE this" sentence
    # must not, because that one is about Hershey's open strokes.
    check(2, "close_sketch invites a pad instead of warning it off",
          "You can now apply Part Design features" in str(closed)
          and "will REFUSE" not in str(closed)
          and "CLOSED contours" in str(closed),
          "outline lettering is a profile")

    say("")
    say("── 2b: pad the Chinese text ──")
    pad = part_design.execute("catia_pad", {"height": 2.0})
    say("  " + " ".join(str(pad).split())[:240])
    ink = volume(conn, part_design)
    check(3, "the pad is accepted and adds material",
          ink is not None and ink > 1.0, f"volume {ink:.2f} mm3")

    # ── ASCII is unchanged ───────────────────────────────────────────────
    say("")
    say("── 3: ASCII still comes from Hershey ──")
    fresh(conn, documents, "Ascii")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    ascii_reply = sketcher.execute("catia_text_strokes",
                                   {"text": "AB", "height": 8.0})
    check(4, "ASCII uses the single-line face",
          "Hershey simplex, single-line" in str(ascii_reply)
          and "OUTLINE FONT" not in str(ascii_reply),
          "no font file touched")

    # ── 4: shaft about an external line ──────────────────────────────────
    say("")
    say("── 4: rectangle u 5..15 / v 0..30 on zx, revolved about a line at x=60 ──")
    fresh(conn, documents, "AxisOff")
    sketcher.execute("catia_create_sketch", {"plane": "zx"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 5, "y1": 0, "x2": 15, "y2": 30})
    sketcher.execute("catia_close_sketch", {})
    gsd.execute("catia_gsd_line",
                {"point1": [60, 0, 0], "point2": [60, 0, 60], "name": "FarAxis"})
    reply = part_design.execute("catia_shaft", {"axis": "FarAxis", "angle": 360})
    say("  " + " ".join(str(reply).split())[:240])
    got = volume(conn, part_design)
    check(5, "the named line is honoured (Pappus 84823.00)",
          got is not None and abs(got - PAPPUS_OFF) < 1.0,
          f"{got:.2f} mm3")
    check(6, "the reply names the axis it actually used",
          "FarAxis" in str(reply) and "not a sketch axis" in str(reply),
          "'FarAxis'")

    # ── 5: the sketch's own axis still works ─────────────────────────────
    say("")
    say("── 5: the same profile about the sketch's H axis (regression) ──")
    fresh(conn, documents, "AxisHome")
    sketcher.execute("catia_create_sketch", {"plane": "zx"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 5, "y1": 0, "x2": 15, "y2": 30})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_shaft", {"angle": 360})
    home = volume(conn, part_design)
    check(7, "the default axis still gives 28274.33",
          home is not None and abs(home - PAPPUS_H) < 1.0, f"{home:.2f} mm3")

    say("")
    failed = [row for row in RESULTS if not row[2]]
    say(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    for number, title, _, detail in failed:
        say(f"  FAILED {number}: {title} ({detail})")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
