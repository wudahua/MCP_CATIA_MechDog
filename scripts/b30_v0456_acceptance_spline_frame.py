"""Acceptance for the two remaining 2026-11-17 items, on B30.

  A  a control-point B-spline is drawn, and the reply carries a MEASURED bound
  B  the same curve drawn by interpolating the control points is off by metres
     of the same kind the report measured  (+4.20 % on P05)
  C  SP16 (cubic, 4 control points) both ways
  D  'samples' says what it costs and what it leaves
  E  a frame sketch names its scaffolding after itself
  F  deleting that sketch with cleanup_frame_datum=true takes all four with it
  G  a sketch with no scaffolding is never given someone else's

.venv/Scripts/python.exe -u scripts/b30_v0456_acceptance_spline_frame.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.results import CodedError  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0456")
CELLS: list[tuple[str, bool, str]] = []
RUN = str(int(time.time()))[-5:]

SP15 = [[37.214626, 336.867322], [49.398155, 391.693203], [90.0, 454.321087]]
SP16 = [[66.0, 347.067257], [64.475007, 391.287947],
        [100.837551, 405.962001], [101.0, 432.076285]]
# The exact curve's t=0.5 points, computed from the definitions (SP15 is the
# report's own number; SP16 is (P0+3P1+3P2+P3)/8).
SP15_MID = [56.502734, 393.643704]
SP16_MID = [82.86720925, 396.36167325]


def say(line: str = "") -> None:
    print(line, flush=True)


def cell(name: str, ok: bool, detail: str = "") -> None:
    CELLS.append((name, ok, detail))
    say(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def fresh(conn, docs, name):
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, name + ".CATPart")
    for index in range(int(conn.documents.Count), 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = str(doc.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).lower().startswith(
                os.path.abspath(ROOT).lower()):
            try:
                doc.Close()
            except Exception:  # noqa: BLE001
                pass
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    return conn.get_active_part()


def distance_to_spline(conn, part, spline, xy) -> float | None:
    """Distance from a sketch-plane point to one Spline2D, in mm.

    Built the way the tool does it: a real 3D point in the body, measured against
    the spline object itself - a sketch's point elements would answer instead of
    the curve (measured 2026-11-18).
    """
    spa = conn.active_document.GetWorkbench("SPAWorkbench")
    body = conn.get_active_part_body()
    factory = part.HybridShapeFactory
    point = factory.AddNewPointCoord(float(xy[0]), float(xy[1]), 0.0)
    body.InsertHybridShape(point)
    part.UpdateObject(point)
    try:
        return float(spa.GetMeasurable(
            part.CreateReferenceFromObject(point)).GetMinimumDistance(
                part.CreateReferenceFromObject(spline)))
    finally:
        try:
            selection = conn.hso
            selection.Clear()
            selection.Add(point)
            selection.Delete()
            selection.Clear()
        except Exception:  # noqa: BLE001
            pass


def reported_bounds(reply: str) -> dict[str, float | None]:
    """The two numbers the reply claims: sampling error and measured error."""
    sampling = re.search(r"misses the exact curve by at most ([\d.]+) mm", reply)
    drawn = re.search(r"distance to the exact B-spline never exceeded ([\d.]+) mm",
                      reply)
    return {"sampling": float(sampling.group(1)) if sampling else None,
            "drawn": float(drawn.group(1)) if drawn else None}


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    say(f"acceptance v0456 on {conn.app.Name}")

    # ── A/B: SP15, both ways, measured against the same witness point ─────
    part = fresh(conn, docs, "AccV0456Quad")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SP15_exact"})
    reply = sketcher.execute("catia_sketch_spline",
                             {"control_points": SP15, "degree": 2})
    say("  A reply: " + " ".join(reply.split())[:400])
    bounds = reported_bounds(reply)
    cell("A1 the reply carries a MEASURED bound, not only the sampling one",
         bounds["drawn"] is not None,
         f"sampling={bounds['sampling']} drawn={bounds['drawn']}")
    cell("A2 the measured bound is within the 0.01 mm tolerance",
         bounds["drawn"] is not None and bounds["drawn"] <= 0.01,
         f"{bounds['drawn']} mm")
    exact = getattr(sketcher, "_last_drawn_spline")
    on_curve = distance_to_spline(conn, part, exact, SP15_MID)
    cell("A3 an independent point on the exact curve sits ON the drawn spline",
         on_curve is not None and on_curve <= 0.05, f"{on_curve:.6f} mm")
    sketcher.execute("catia_close_sketch", {})

    # the report's failure mode, same witness point, different curve
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SP15_interp"})
    sketcher.execute("catia_sketch_spline", {"points": SP15})
    interpolated = getattr(sketcher, "_last_drawn_spline")
    off = distance_to_spline(conn, part, interpolated, SP15_MID)
    cell("A4 interpolating the control points misses the exact curve where the "
         "report says it does", off is not None and off >= 1.0, f"{off:.6f} mm")
    sketcher.execute("catia_close_sketch", {})

    # ── C: SP16, cubic ────────────────────────────────────────────────────
    part = fresh(conn, docs, "AccV0456Cubic")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SP16_exact"})
    reply = sketcher.execute("catia_sketch_spline",
                             {"control_points": SP16, "degree": 3})
    say("  C reply: " + " ".join(reply.split())[:400])
    bounds = reported_bounds(reply)
    cell("C1 a cubic 4-point B-spline is drawn and measured",
         bounds["drawn"] is not None and bounds["drawn"] <= 0.01,
         f"sampling={bounds['sampling']} drawn={bounds['drawn']}")
    exact = getattr(sketcher, "_last_drawn_spline")
    on_curve = distance_to_spline(conn, part, exact, SP16_MID)
    cell("C2 the exact curve's t=0.5 point sits on the drawn spline",
         on_curve is not None and on_curve <= 0.05, f"{on_curve:.6f} mm")
    sketcher.execute("catia_close_sketch", {})

    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SP16_interp"})
    sketcher.execute("catia_sketch_spline", {"points": SP16})
    interpolated = getattr(sketcher, "_last_drawn_spline")
    off = distance_to_spline(conn, part, interpolated, SP16_MID)
    # 0.94 mm on this witness point - not the report's +4.20 %, which was a
    # VOLUME over a whole profile and is not this number. The claim pinned here
    # is only that the two ways of reading the same four points are different
    # curves, by more than the tolerance the spec allows.
    cell("C3 the same four points read as interpolation points are a different curve",
         off is not None and off >= 0.5, f"{off} mm vs 0.0000 for the sampled one")
    sketcher.execute("catia_close_sketch", {})

    # ── D: an explicit sample count says what it costs ────────────────────
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SP16_loose"})
    reply = sketcher.execute("catia_sketch_spline",
                             {"control_points": SP16, "degree": 3, "samples": 16})
    bounds = reported_bounds(reply)
    cell("D1 samples=16 is the report's 17-point curve, and says so",
         "17 sampled points" in reply, " ".join(reply.split())[:150])
    cell("D2 and it reports the sampling error that 17 points really has",
         bounds["sampling"] is not None and bounds["sampling"] > 0.02,
         f"{bounds['sampling']} mm - above the report's 0.02 mm local tolerance, "
         "which is why the default is tighter")
    sketcher.execute("catia_close_sketch", {})

    # ── E/F/G: the frame scaffolding ──────────────────────────────────────
    part = fresh(conn, docs, "AccV0456Frame")
    body = conn.get_active_part_body()
    sketcher.execute("catia_create_sketch",
                     {"plane": {"type": "frame", "origin": [10.0, 20.0, 30.0],
                                "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0]},
                      "name": "FR1"})
    names = [str(body.HybridShapes.Item(i).Name)
             for i in range(1, int(body.HybridShapes.Count) + 1)]
    say(f"  E body after the frame sketch: {names}")
    cell("E1 the datum carries the sketch's name", "FR1_FRAME" in names, str(names))
    cell("E2 and its three points say which one they are",
         all(f"FR1_FRAME.{axis}" in names for axis in "OUV"), str(names))
    sketcher.execute("catia_close_sketch", {})

    # a sketch with no scaffolding must not borrow a neighbour's
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "PLAIN"})
    sketcher.execute("catia_close_sketch", {})
    reply = parts.execute("catia_delete_feature",
                          {"feature_name": "PLAIN", "cleanup_frame_datum": True})
    say("  G reply: " + " ".join(reply.split())[:300])
    still = [str(body.HybridShapes.Item(i).Name)
             for i in range(1, int(body.HybridShapes.Count) + 1)]
    cell("G1 deleting a plain sketch leaves the frame's scaffolding alone",
         "FR1_FRAME" in still and len(still) == 4, str(still))
    cell("G2 and the reply does not claim to have cleaned anything",
         "removed the frame scaffolding" not in reply, reply.split(".")[0][:120])

    try:
        reply = parts.execute("catia_delete_feature",
                              {"feature_name": "FR1", "cleanup_frame_datum": True})
        say("  F reply: " + " ".join(reply.split())[:300])
    except CodedError as error:
        reply = f"FAILED: {error.code}: {error}"
        say("  F " + reply[:300])
    still = [str(body.HybridShapes.Item(i).Name)
             for i in range(1, int(body.HybridShapes.Count) + 1)]
    cell("F1 the sketch and all four scaffolding elements are gone",
         not still, f"left: {still}")
    cell("F2 the reply counts what it removed", "4 element(s)" in reply,
         "4 element(s) named in the reply")

    say("")
    passed = sum(1 for _name, ok, _detail in CELLS if ok)
    say(f"{passed}/{len(CELLS)} cells passed")
    for name, ok, detail in CELLS:
        if not ok:
            say(f"  FAILED: {name} ({detail})")
    return 0 if passed == len(CELLS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
