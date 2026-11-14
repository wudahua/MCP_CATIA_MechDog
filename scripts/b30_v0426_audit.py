"""0.3.124 acceptance on B30: the two things the 2026-11-13 report could not do.

Nine checks, one fresh document per case. The numbers are the ones the tool
replies with AND the ones measured independently here.

    1  three stations on a straight 50 mm spine: 8168.2 mm3, ruled sum 8168.1
    2  the bare-radius form [8, 8, 8] gives a cylinder: 10052.7 against 10053.1
    3  a closed spine is refused with code POINT_ON_CURVE_FAILED and leaves nothing
    4  a bent spine is NOT reported as a failure - the reply says the ruled sum
       only holds on a straight spine (measured +20.60 % on a 55.642 mm spline)
    5  body= into a body holding a pad: the INCREMENT is judged, 3518.4 vs 3518.6
    6  source_file= reads a CSV of three 18-point rings and lofts to 27945.3 mm3 -
       the same number the inline entries gave in 0.3.122
    7  a CSV ring whose last point repeats the first is marked closed
    8  entries= and source_file= together are refused before CATIA is touched
    9  the router holds 147 tools

    .venv/Scripts/python.exe scripts/b30_v0426_audit.py
"""

from __future__ import annotations

import math
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
    tempfile.gettempdir(), "catia_probe_v0426")
ROOT = os.path.join(_PROBE_ROOT, "audit")
RESULTS: list[tuple[int, str, bool, str]] = []


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


def fresh(conn, documents, name):
    path = os.path.join(ROOT, f"{name}.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    for index in range(1, int(conn.documents.Count) + 1):
        if conn.documents.Item(index).Name == f"{name}.CATPart":
            conn.documents.Item(index).Activate()
    return conn.get_active_part()


def gsd_tree(part) -> list[str]:
    out = []
    for index in range(1, int(part.HybridBodies.Count) + 1):
        body = part.HybridBodies.Item(index)
        out.append(str(body.Name))
        try:
            shapes = body.HybridShapes
            out += ["  " + str(shapes.Item(k).Name)
                    for k in range(1, int(shapes.Count) + 1)]
        except Exception:  # noqa: BLE001
            pass
    return out


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    documents = DocumentTools(conn)
    gsd = GSDTools(conn)
    part_design = PartDesignTools(conn)
    sketcher = SketcherTools(conn)

    # ── 1: three stations on a straight spine ───────────────────────────
    say("")
    say("── 1: 50 mm line, r 10 -> 7 -> 4 ──")
    part = fresh(conn, documents, "AuditCone")
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [50, 0, 0],
                                   "name": "Spine"})
    reply = str(gsd.execute("catia_solid_sweep_variable",
                            {"spine": "Spine", "name": "Cone",
                             "sections": [[0, 10], [0.5, 7], [1, 4]]}))
    say("  " + " ".join(reply.split())[:300])
    body = part.Bodies.Item(int(part.Bodies.Count))
    volume = part_design._body_volume_mm3(part, body)
    expected = math.pi * 25 / 3 * 219 + math.pi * 25 / 3 * 93
    check(1, "the three-station cone measures the ruled sum",
          volume is not None and abs(volume - expected) < 2.0,
          f"{volume:.1f} vs {expected:.1f}" if volume is not None else "unreadable")
    check(1, "the reply itself reports the agreement",
          "+0.0" in reply and "81" in reply, "verdict present")

    # ── 2: the bare-radius form ─────────────────────────────────────────
    say("")
    say("── 2: 50 mm line, sections [8, 8, 8] ──")
    part = fresh(conn, documents, "AuditRod")
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [50, 0, 0],
                                   "name": "Spine"})
    gsd.execute("catia_solid_sweep_variable",
                {"spine": "Spine", "name": "Rod", "sections": [8, 8, 8]})
    body = part.Bodies.Item(int(part.Bodies.Count))
    volume = part_design._body_volume_mm3(part, body)
    check(2, "three equal radii give a cylinder, not a cone",
          volume is not None and abs(volume - math.pi * 64 * 50) < 2.0,
          f"{volume:.1f} vs {math.pi * 64 * 50:.1f}"
          if volume is not None else "unreadable")

    # ── 3: a closed spine ───────────────────────────────────────────────
    say("")
    say("── 3: a gsd circle as the spine ──")
    part = fresh(conn, documents, "AuditClosed")
    gsd.execute("catia_gsd_point", {"x": 0, "y": 0, "z": 0, "name": "O"})
    gsd.execute("catia_gsd_circle", {"center": "O", "support_plane": "yz",
                                     "radius": 20, "name": "Ring"})
    before = gsd_tree(part)
    refused = ""
    code = ""
    try:
        gsd.execute("catia_solid_sweep_variable",
                    {"spine": "Ring", "name": "Bad", "sections": [5, 5]})
    except Exception as error:  # noqa: BLE001
        code = str(getattr(error, "code", ""))
        refused = " ".join(str(error).split())
    say("  " + (code + ": " + refused[:220] if refused else "NOT REFUSED"))
    after = gsd_tree(part)
    check(3, "a closed spine is refused by name and the tree is unchanged",
          code == "POINT_ON_CURVE_FAILED" and before == after
          and "CLOSED spline" in refused, f"{len(before)} -> {len(after)} entries")

    # ── 4: a bent spine must not be called a failure ────────────────────
    say("")
    say("── 4: a bent spline spine, r 6 -> 9 -> 3 ──")
    part = fresh(conn, documents, "AuditBent")
    gsd.execute("catia_gsd_spline",
                {"points": [[0, 0, 0], [15, 8, 0], [30, 8, 10], [45, 0, 20]],
                 "name": "Arc"})
    reply = str(gsd.execute("catia_solid_sweep_variable",
                            {"spine": "Arc", "name": "Horn",
                             "sections": [[0, 6], [0.5, 9], [1, 3]]}))
    text = " ".join(reply.split())
    say("  " + text[:320])
    body = part.Bodies.Item(int(part.Bodies.Count))
    volume = part_design._body_volume_mm3(part, body)
    check(4, "the bent sweep builds",
          volume is not None and volume > 9000,
          f"{volume:.1f} mm3" if volume is not None else "unreadable")
    check(4, "the reply says the ruled sum only holds on a STRAIGHT spine",
          "ONLY APPLIES TO A STRAIGHT SPINE" in text
          and "THAT IS NOT A RULED LOFT" not in text,
          "no false accusation")

    # ── 5: body= judges the INCREMENT ───────────────────────────────────
    say("")
    say("── 5: body= into a body holding a 40x40x10 pad ──")
    part = fresh(conn, documents, "AuditJoin")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 40, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})
    gsd.execute("catia_gsd_line", {"point1": [20, 20, 10], "point2": [20, 20, 40],
                                   "name": "Up"})
    reply = str(gsd.execute("catia_solid_sweep_variable",
                            {"spine": "Up", "name": "Boss", "body": "零件几何体",
                             "sections": [[0, 8], [1, 4]]}))
    text = " ".join(reply.split())
    say("  " + text[:320])
    volume = part_design._body_volume_mm3(part, part.MainBody)
    gained = None if volume is None else volume - 16000.0
    check(5, "the increment is judged, not the body's total",
          gained is not None and abs(gained - 3518.6) < 2.0,
          f"+{gained:.1f} vs 3518.6" if gained is not None else "unreadable")
    check(5, "no '+454 %' style verdict on a union",
          "454" not in text and "Agreement here means" in text, "increment used")

    # ── 6/7: source_file= ───────────────────────────────────────────────
    say("")
    say("── 6: three 18-point rings from a CSV, then a loft ──")
    csv_path = os.path.join(ROOT, "rings.csv")
    rows = ["# three closed stations, last point repeats the first"]
    for name, z, radius in (("S01", 0, 10), ("S02", 30, 14), ("S03", 60, 6)):
        for index in range(19):
            angle = 2 * math.pi * (index % 18) / 18
            rows.append(f"{name}, {radius * math.cos(angle):.4f}, "
                        f"{radius * math.sin(angle):.4f}, {z}")
        rows.append("")
    with open(csv_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(rows))
    part = fresh(conn, documents, "AuditCsv")
    reply = str(gsd.execute("catia_gsd_splines", {"source_file": csv_path}))
    text = " ".join(reply.split())
    say("  " + text[:280])
    present = [name for name in ("S01", "S02", "S03")
               if part.FindObjectByName(name) is not None]
    check(6, "one CSV call builds three closed sections",
          len(present) == 3 and "3 sections" in text and "closed" in text,
          ", ".join(present))
    part_design.execute("catia_solid_loft",
                        {"sections": ["S01", "S02", "S03"], "name": "Cone"})
    volume = part_design._body_volume_mm3(part, part.MainBody)
    check(6, "a loft through them measures the same 27945.3 mm3 as the inline form",
          volume is not None and abs(volume - 27945.3) < 2.0,
          f"{volume:.1f} mm3" if volume is not None else "unreadable")

    say("")
    say("── 7: a CSV ring is marked closed by its repeated first point ──")
    ring_path = os.path.join(ROOT, "ring.csv")
    with open(ring_path, "w", encoding="utf-8") as handle:
        handle.write("R01, 10, 0, 0\nR01, 0, 10, 0\nR01, -10, 0, 0\nR01, 10, 0, 0\n")
    entries, note = GSDTools._entries_from_file(ring_path)
    check(7, "the repeat is read as CLOSED and the note says so",
          entries[0].get("closed") is True and "1 closed" in note,
          note.split(" from ")[0])

    # ── 8: entries= and source_file= together ───────────────────────────
    say("")
    say("── 8: entries= and source_file= in one call ──")
    code = ""
    try:
        gsd.execute("catia_gsd_splines",
                    {"entries": [{"points": [[0, 0, 0], [1, 0, 0]]}],
                     "source_file": ring_path})
    except Exception as error:  # noqa: BLE001
        code = str(getattr(error, "code", ""))
    check(8, "the ambiguous call is refused before CATIA is touched",
          code == "ENTRIES_OR_FILE", code or "NOT REFUSED")

    # ── 9: the router ───────────────────────────────────────────────────
    say("")
    from catia_mcp.server import CATIAMCPServer

    count = len(CATIAMCPServer()._tool_router)
    check(9, "the router holds 147 tools", count == 147, str(count))

    say("")
    failed = [row for row in RESULTS if not row[2]]
    say(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        for number, title, _, detail in failed:
            say(f"  FAILED {number}: {title} ({detail})")
    close_ours(conn)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
