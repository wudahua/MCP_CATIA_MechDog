"""0.3.151 acceptance on B30, 2026-11-19.

What the 2026-11-19 (0.3.150) report asked for, each cell measured rather than
argued:

  A  an arc whose ends are 0.0027 mm off its circle keeps R72.367 - proved by
     the BOUNDING BOX of the padded result (the apex sits at 15 - R), not by the
     reply's own sentence. The old rebuild would put it 0.09 mm further out.
  B  the same arc, reported: "R kept", the centre move, and the amplification it
     avoided.
  C  the dog-bone's A01 arc (R0.35 over ends at 0.25) is refused with the two
     real fixes and a warning about R0.1768 - the half circle.
  D  on a 288-edge part, naming the edge is far cheaper than anchoring it, and
     the reply says the edge count and the per-edge cost.
  E  catia_screenshot(width, height) writes exactly that size, and puts the
     window back afterwards.
  F  crop=[x,y,w,h] cuts the written file and reports the new size.

One new document per cell.
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "catia_probe_v0472")
os.makedirs(ROOT, exist_ok=True)
CHECKS: list[tuple[str, bool, str]] = []
WEB_RADIUS = 72.367
WEB_CENTRE = [15.0, 0.0]
WEB_END_X = -55.222
WEB_END_Y = 17.5


def say(text: str) -> None:
    print(text, flush=True)


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), detail))
    say(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def fresh(docs, name: str) -> str:
    """A NEW document for every cell: a failure is document-level."""
    try:
        while True:
            docs.execute("catia_close_document", {"save": False})
    except Exception:  # noqa: BLE001
        pass
    path = os.path.join(ROOT, f"{name}.CATPart")
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    return path


def data_of(reply) -> dict:
    import json
    if isinstance(reply, dict):
        return reply.get("data", {})
    return json.loads(reply).get("data", {})


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    exporter = ExportTools(conn)
    measurement = MeasurementTools(conn)
    say(f"acceptance v0472 on {conn.app.Name}")

    # ══ A + B: the radius is KEPT, and the bounding box proves it ═════════
    say("A: the report's web arc, R72.367 with ends 0.0027 mm off")
    fresh(docs, "ArcKeep")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "WEB"})
    reply = sketcher.execute("catia_sketch_profile", {"segments": [
        {"type": "arc", "center": WEB_CENTRE, "radius": WEB_RADIUS,
         "p1": [WEB_END_X, WEB_END_Y], "p2": [WEB_END_X, -WEB_END_Y],
         "direction": "ccw"},
        {"type": "line", "p1": [WEB_END_X, -WEB_END_Y],
         "p2": [WEB_END_X, WEB_END_Y]},
    ]})
    text = " ".join(str(reply).split())
    say("  profile: " + text[:300])
    check("A the profile is closed", "NOT CLOSED" not in text, text[:120])
    sketcher.execute("catia_close_sketch", {})
    padded = parts.execute("catia_pad", {"sketch_name": "WEB", "height": 5.0})
    say("  pad: " + " ".join(str(padded).split())[:140])
    # THE APEX IS NOT IN A VERTEX BOX: a curved face has no vertex at its
    # extremum (the tool says so itself), so the bbox reads -55.222 - the chord -
    # whichever circle was drawn. The RADIUS is the number that differs, and the
    # sketch reports it.
    import json
    geometry = sketcher.execute("catia_sketch_get_geometry", {"sketch_name": "WEB"})
    geometry = geometry if isinstance(geometry, dict) else json.loads(geometry)
    radii = [float(element["radius_mm"]) for element in geometry.get("elements", [])
             if element.get("radius_mm") is not None]
    say(f"  radii drawn: {radii}")
    check("A the arc was drawn at R72.3670, not at the rebuilt 72.4594",
          any(abs(value - WEB_RADIUS) <= 1e-3 for value in radii),
          f"{radii}")

    # ══ B: what the reply says about it ══════════════════════════════════
    say("B: the sentence")
    check("B the radius is reported as kept", "R72.3670 is KEPT" in text, text[:160])
    check("B the centre move is reported",
          "moved 0.002" in text, text[:200])
    check("B the amplification it avoided is named",
          "further" in text and "midpoint" in text, text[:200])
    check("B it no longer claims a circle was rebuilt",
          "circle was rebuilt" not in text)

    # ══ C: the dog-bone arc is refused with two real fixes ═══════════════
    say("C: R0.35 over ends that are R0.25 from the centre")
    fresh(docs, "ArcMismatch")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "DB"})
    try:
        sketcher.execute("catia_sketch_profile", {"segments": [
            {"type": "arc", "center": [-2.75, -1.10], "radius": 0.35,
             "p1": [-3.00, -1.10], "p2": [-2.75, -1.35], "direction": "ccw"},
            {"type": "line", "p1": [-2.75, -1.35], "p2": [-3.00, -1.10]},
        ]})
        check("C the contradiction is refused", False, "it was drawn")
    except Exception as error:  # noqa: BLE001
        message = str(error)
        check("C the contradiction is refused",
              getattr(error, "code", "") == "ARC_RADIUS_MISMATCH", message[:90])
        check("C the fix that keeps the centre is named",
              "radius=0.2500" in message, "")
        check("C the fix that keeps the radius is named",
              "0.1000" in message, "")
        check("C the half circle is named as a trap",
              "R0.1768" in message and "NOT one of them" in message, "")
    import json
    geometry = sketcher.execute("catia_sketch_get_geometry", {"sketch_name": "DB"})
    geometry = geometry if isinstance(geometry, dict) else json.loads(geometry)
    check("C the sketch holds nothing but its axis",
          int(geometry.get("count", -1)) == 1,
          f"count {geometry.get('count')}")

    # ══ D: name vs anchor on a part with a few hundred edges ═════════════
    say("D: 288 edges, one chamfer each way")
    fresh(docs, "EdgeCost")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "STAR"})
    points: list[list[float]] = []
    for index in range(96):
        angle = 2 * math.pi * index / 96
        radius = 50.0 if index % 2 == 0 else 40.0
        points.append([round(radius * math.cos(angle), 6),
                       round(radius * math.sin(angle), 6)])
    points.append(points[0])
    sketcher.execute("catia_sketch_strokes", {"strokes": [points]})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "STAR", "height": 8.0})

    started = time.perf_counter()
    anchored = " ".join(str(parts.execute(
        "catia_chamfer", {"edge_anchors": [[50.0, 0.0, 4.0]], "length": 0.4,
                          "angle": 45})).split())
    anchor_s = time.perf_counter() - started
    say(f"  by anchor: {anchor_s:.1f} s | {anchored[-260:]}")
    check("D the reply names the edge count", "edge(s)" in anchored, anchored[-160:])
    check("D and the hint to name the edge instead",
          "Edge.N" in anchored and "skips that pass" in anchored, "")

    started = time.perf_counter()
    named = " ".join(str(parts.execute(
        "catia_chamfer", {"edge_names": ["Edge.12"], "length": 0.4,
                          "angle": 45})).split())
    named_s = time.perf_counter() - started
    say(f"  by name: {named_s:.1f} s | {named[-160:]}")
    check("D naming the edge is at least 3x cheaper",
          named_s * 3 < anchor_s, f"{named_s:.1f} s vs {anchor_s:.1f} s")

    # ══ E + F: the capture size and the crop ═════════════════════════════
    say("E: an explicit capture size")
    from PIL import Image
    fresh(docs, "Shot")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "S"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 60.0, "y2": 40.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "S", "height": 12.0})
    exporter.execute("catia_fit_all", {})
    window = conn.active_window
    before = (int(window.Width), int(window.Height))
    shot = os.path.join(ROOT, "sized.jpg")
    reply = exporter.execute("catia_screenshot",
                             {"file_path": shot, "width": 1200, "height": 800})
    say("  reply: " + " ".join(str(reply).split())[:200])
    with Image.open(shot) as image:
        got = f"{image.size[0]}x{image.size[1]}"
    check("E the file is the size that was asked for", got == "1200x800", got)
    after = (int(window.Width), int(window.Height))
    check("E the window was put back", after == before, f"{before} -> {after}")

    say("F: crop")
    reply = exporter.execute("catia_screenshot",
                             {"file_path": shot, "width": 1200, "height": 800,
                              "crop": [0, 0, 300, 200]})
    say("  reply: " + " ".join(str(reply).split())[:200])
    with Image.open(shot) as image:
        got = f"{image.size[0]}x{image.size[1]}"
    check("F the file was cropped", got == "300x200", got)
    check("F and the reply says so", "cropped to 300x200" in str(reply), "")

    say("")
    passed = sum(1 for _label, ok, _detail in CHECKS if ok)
    say(f"{passed}/{len(CHECKS)} checks passed")
    for label, ok, detail in CHECKS:
        if not ok:
            say(f"  FAILED: {label}  {detail}")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
