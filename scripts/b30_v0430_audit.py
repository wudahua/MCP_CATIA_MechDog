"""0.3.128 acceptance on B30: reading a drawing into a sketch (P0-1).

Every case is one fresh document, and every volume is one whose value is known
by hand from the geometry in the file - so the check is not "the tool said it
worked", it is "the solid has the area the drawing has".

    1  SVG    40 x 20 plate with a D6 hole, pad 10   -> 7 717.26
    2  DXF    30 x 30 square drawn as FOUR separate LINEs (stitching), pad 4
    3  DXF    40 x 20 stadium with two bulge semicircles, pad 5 -> 5 570.80
    4  DXF    a 1 x 0.5 INCH rectangle ($INSUNITS=1), pad 2 -> 645.16
    5  DXF    the same rectangle at 1:100 (scale=100), pad 2 -> 10 000
    6  DXF    the real 6 489-element vectorised drawing: must REFUSE, and the
              document must still hold no sketch
    7  PDF    the real A3 drawing, closed_only + min_size, imported as-is

    .venv/Scripts/python.exe scripts/b30_v0430_audit.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.blueprint import BlueprintTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0430")
ROOT = os.path.join(_PROBE_ROOT, "audit")
SAMPLE = r"F:/WorkBuddy Space/图片转三维"
RESULTS: list[tuple[int, str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, title: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, title, bool(passed), detail))
    say(f"  [{'PASS' if passed else 'FAIL'}] {number}. {title}"
        + (f"  ({detail})" if detail else ""))


# ── drawing files with geometry known by hand ─────────────────────────────
def dxf(body: str, units: int = 4) -> str:
    """A minimal ASCII DXF. `units` is $INSUNITS: 4 mm, 1 inch."""
    return ("0\nSECTION\n2\nHEADER\n9\n$INSUNITS\n70\n"
            f"{units}\n0\nENDSEC\n"
            "0\nSECTION\n2\nENTITIES\n" + body + "0\nENDSEC\n0\nEOF\n")


def pair(code: int, value) -> str:
    return f"{code}\n{value}\n"


def line(x1, y1, x2, y2, layer="0") -> str:
    return ("0\nLINE\n" + pair(8, layer) + pair(10, x1) + pair(20, y1)
            + pair(11, x2) + pair(21, y2))


def circle(cx, cy, r, layer="0") -> str:
    return ("0\nCIRCLE\n" + pair(8, layer) + pair(10, cx) + pair(20, cy)
            + pair(40, r))


def lwpolyline(points, closed=True, layer="0") -> str:
    """points: [(x, y, bulge), ...] - bulge 0 is a straight run."""
    body = "0\nLWPOLYLINE\n" + pair(8, layer) + pair(90, len(points))
    body += pair(70, 1 if closed else 0)
    for x, y, bulge in points:
        body += pair(10, x) + pair(20, y)
        if bulge:
            body += pair(42, bulge)
    return body


def write(name: str, text: str) -> str:
    path = os.path.join(ROOT, name)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def clear_ours(conn) -> None:
    """Close anything left open under the probe root by an earlier run.

    Without this the second run refuses its own files: a new part at a path that
    is already open would leave two documents with one name (measured here on the
    rerun after the first failure).
    """
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
    path = os.path.join(ROOT, name + ".CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    for index in range(1, int(conn.documents.Count) + 1):
        if conn.documents.Item(index).Name == name + ".CATPart":
            conn.documents.Item(index).Activate()
    return conn.get_active_part()


def volume(measurement) -> float:
    payload = json.loads(measurement.execute("catia_get_inertia", {}))
    return float(payload.get("volume_mm3") or 0.0)


def near(got: float, want: float, pct: float = 0.5) -> bool:
    return want > 0 and abs(got - want) / want * 100.0 <= pct


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    clear_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    blueprint = BlueprintTools(conn, sketcher)
    part_design = PartDesignTools(conn)
    measurement = MeasurementTools(conn)
    say(f"CATIA {conn.version if hasattr(conn, 'version') else ''} | probe root {ROOT}")

    # ── 1: SVG, with a hole ──────────────────────────────────────────────
    say("")
    say("── 1: SVG 40 x 20 plate, D6 hole, pad 10 ──")
    svg = write("plate.svg", """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="60mm"
     viewBox="0 0 100 60">
  <g transform="translate(10,10)">
    <rect x="0" y="0" width="40" height="20" id="plate"/>
    <circle cx="20" cy="10" r="3" id="hole"/>
  </g>
</svg>
""")
    fresh(conn, documents, "AuditSvg")
    reply = " ".join(str(blueprint.execute(
        "catia_import_drawing", {"file_path": svg})).split())
    say("  " + reply[:340])
    part_design.execute("catia_pad", {"height": 10})
    got = volume(measurement)
    want = (40 * 20 - math.pi * 9) * 10
    check(1, "SVG plate with a hole pads to the analytic volume",
          near(got, want), f"{got:.2f} vs {want:.2f}")
    check(2, "the reply reports the drawing's bounds in mm",
          "X 0.000..40.000" in reply and "Y 0.000..20.000" in reply,
          "bounds quoted")

    # ── 2: four separate LINEs, which is how a CAD export writes a profile ─
    say("")
    say("── 2: DXF square as FOUR LINE entities, pad 4 ──")
    square = write("square.dxf", dxf(line(0, 0, 30, 0) + line(30, 0, 30, 30)
                                     + line(30, 30, 0, 30) + line(0, 30, 0, 0)))
    fresh(conn, documents, "AuditSquare")
    reply = " ".join(str(blueprint.execute(
        "catia_import_drawing", {"file_path": square})).split())
    say("  " + reply[:340])
    part_design.execute("catia_pad", {"height": 4})
    got = volume(measurement)
    check(3, "four edges are stitched into one closed profile",
          near(got, 3600.0), f"{got:.2f} vs 3600.00")
    check(4, "and the reply says it stitched them",
          "stitched" in reply.lower() or "1 of 1" in reply, reply[:80])

    # ── 3: bulge semicircles, which are real arcs or they are not ─────────
    say("")
    say("── 3: DXF 40 x 20 stadium, two semicircle ends (bulge 1), pad 5 ──")
    # A stadium of length 40, height 20: two straight sides and two semicircular
    # ENDS. The bulges sit on the two vertical edges and are NEGATIVE, because
    # the cap has to run clockwise from its top point to its bottom one to bulge
    # outward - the first version of this put bulge=1 on the long edges, which
    # makes each edge a 40 mm semicircle and the shape a four-petal flower whose
    # area (800 + 400*pi) is what the volume reported. The code was right.
    stadium = write("stadium.dxf", dxf(lwpolyline([
        (0, 10, 0), (40, 10, -1), (40, -10, 0), (0, -10, -1)])))
    fresh(conn, documents, "AuditBulge")
    reply = " ".join(str(blueprint.execute(
        "catia_import_drawing", {"file_path": stadium})).split())
    say("  " + reply[:340])
    part_design.execute("catia_pad", {"height": 5})
    got = volume(measurement)
    # 40*20 + pi*10^2, times 5. Chords instead of arcs would give 4 000.
    want = (40 * 20 + math.pi * 100) * 5
    check(5, "a bulge is drawn as an arc, not as its chord",
          near(got, want), f"{got:.1f} vs {want:.1f} (chords would be 4000.0)")
    check(6, "and the caps put the bounds at 60 x 20 mm",
          "X 0.000..60.000" in reply and "Y 0.000..20.000" in reply,
          reply[60:130])

    # ── 4: inches ────────────────────────────────────────────────────────
    say("")
    say("── 4: DXF 1 x 0.5 INCH ($INSUNITS=1), pad 2 ──")
    inch = write("inch.dxf", dxf(lwpolyline([(0, 0, 0), (1, 0, 0), (1, 0.5, 0),
                                             (0, 0.5, 0)]), units=1))
    fresh(conn, documents, "AuditInch")
    reply = " ".join(str(blueprint.execute(
        "catia_import_drawing", {"file_path": inch})).split())
    say("  " + reply[:300])
    part_design.execute("catia_pad", {"height": 2})
    got = volume(measurement)
    want = 25.4 * 12.7 * 2
    check(7, "$INSUNITS is honoured without a scale argument",
          near(got, want), f"{got:.2f} vs {want:.2f}")

    # ── 5: a 1:100 sheet ─────────────────────────────────────────────────
    say("")
    say("── 5: DXF 1 x 0.5 mm at 1:100, scale=100, pad 2 ──")
    fresh(conn, documents, "AuditScale")
    scaled = write("scale100.dxf", dxf(lwpolyline(
        [(0, 0, 0), (1, 0, 0), (1, 0.5, 0), (0, 0.5, 0)])))
    reply = " ".join(str(blueprint.execute(
        "catia_import_drawing", {"file_path": scaled, "scale": 100})).split())
    say("  " + reply[:300])
    part_design.execute("catia_pad", {"height": 2})
    got = volume(measurement)
    check(8, "scale=100 turns a 1:100 sheet into a 1:1 part",
          near(got, 10000.0), f"{got:.1f} vs 10000.00")

    # ── 6: the vectorised drawing must be refused, not truncated ─────────
    say("")
    say("── 6: the real 6 489-element vectorised DXF ──")
    fresh(conn, documents, "AuditTooBig")
    refused, message = False, ""
    try:
        blueprint.execute("catia_import_drawing",
                          {"file_path": os.path.join(
                              SAMPLE, "image062_vectorized.dxf")})
    except Exception as error:  # noqa: BLE001 - the refusal is the result
        refused = True
        message = str(error)
    say("  " + " ".join(message.split())[:300])
    check(9, "a 6 489-element import is refused with the count",
          refused and "6489" in message.replace(" ", ""), message[:60])
    features = json.loads(part_design.execute("catia_list_features", {}))
    check(10, "and nothing was drawn into the document",
          not (features.get("sketches") or []),
          f"sketches={len(features.get('sketches') or [])}")

    # ── 7: a real PDF, filtered ──────────────────────────────────────────
    say("")
    say("── 7: the real A3 PDF, closed_only + min_size=20 ──")
    fresh(conn, documents, "AuditPdf")
    report = json.loads(blueprint.execute(
        "catia_read_drawing",
        {"file_path": os.path.join(SAMPLE, "032.pdf"),
         "closed_only": True, "min_size": 20}))
    say(f"  read: {report['counts']['contours']} contour(s), "
        f"{report['counts']['elements']} element(s), "
        f"bounds {report['bounds_mm']}")
    reply = " ".join(str(blueprint.execute(
        "catia_import_drawing",
        {"file_path": os.path.join(SAMPLE, "032.pdf"),
         "closed_only": True, "min_size": 20})).split())
    say("  " + reply[:360])
    features = json.loads(part_design.execute("catia_list_features", {}))
    check(11, "the PDF imports the contours the read step promised",
          report["counts"]["contours"] > 0
          and f"{report['counts']['contours']} contour" in reply,
          f"{report['counts']['contours']} contour(s)")
    check(12, "and the sketch really holds them",
          bool(features.get("sketches")), f"{len(features.get('sketches') or [])} sketch")

    # ── summary ──────────────────────────────────────────────────────────
    say("")
    passed = sum(1 for _, _, ok, _ in RESULTS if ok)
    say(f"RESULT: {passed}/{len(RESULTS)}")
    for number, title, ok, detail in RESULTS:
        if not ok:
            say(f"  FAILED {number}: {title}  ({detail})")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
