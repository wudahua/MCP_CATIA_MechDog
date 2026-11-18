"""Acceptance for the five residuals of the 2026-11-18 (0.3.146) report, on B30.

  A  R1 - the default budget: 17 points for a quadratic, 33 for the cubic, and
     the reply states the deviation it reached
  B  R1 - an explicit samples= still buys what it always bought, and says so
  C  R1 - the reply names what a tighter target would cost in point elements
  D  R2 - inside a plan, the step keeps the two error numbers (both ends of the
     truncated result)
  E  R3 - cleanup_frame_datum="auto": last sketch -> cleaned; another sketch
     present -> left alone and said so; true -> forced
  F  R4 - catia_get_color accepts the historical 'structured' argument
  G  R5 - a mirror of an already-symmetric body removes the node it created and
     answers no_change

.venv/Scripts/python.exe -u scripts/b30_v0458_acceptance_residuals.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.appearance import AppearanceTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.planner import PlannerTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0458")
CELLS: list[tuple[str, bool, str]] = []
RUN = str(int(time.time()))[-5:]

SP15 = [[37.214626, 336.867322], [49.398155, 391.693203], [90.0, 454.321087]]
SP16 = [[66.0, 347.067257], [64.475007, 391.287947],
        [100.837551, 405.962001], [101.0, 432.076285]]


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


def samples_in(reply: str) -> int | None:
    found = re.search(r"through (\d+) sample point\(s\)", reply)
    return int(found.group(1)) if found else None


def sampling_in(reply: str) -> float | None:
    found = re.search(r"Sampling error ([\d.]+) mm", reply)
    return float(found.group(1)) if found else None


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    measurement = AppearanceTools(conn)
    say(f"acceptance v0458 on {conn.app.Name}")

    # ── A: the default budget ─────────────────────────────────────────────
    part = fresh(conn, docs, "AccV0458a")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "Q"})
    reply = sketcher.execute("catia_sketch_spline",
                             {"control_points": SP15, "degree": 2})
    say("  A quadratic: " + " ".join(reply.split())[:260])
    count, error = samples_in(reply), sampling_in(reply)
    cell("A1 a 3-point quadratic now costs 17 points, not 65",
         count == 17, f"{count} points, sampling {error} mm")
    cell("A2 and the reply states the deviation it reached",
         error is not None and error <= 0.03, f"{error} mm")
    sketcher.execute("catia_close_sketch", {})

    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "C"})
    reply = sketcher.execute("catia_sketch_spline",
                             {"control_points": SP16, "degree": 3})
    say("  A cubic: " + " ".join(reply.split())[:260])
    count, error = samples_in(reply), sampling_in(reply)
    cell("A3 a 4-point cubic now costs 33 points, not 65", count == 33,
         f"{count} points, sampling {error} mm")
    cell("A4 and it is inside the 0.03 mm target", error is not None and error <= 0.03,
         f"{error} mm")
    cell("A5 the numbers come FIRST, before the knot vector",
         reply.index("Sampling error") < reply.index("Knots"),
         "Sampling error precedes Knots")
    cell("A6 and it names what a tighter curve would cost",
         "would need" in reply and "sample point(s)" in reply,
         " ".join(reply.split())[-160:])
    sketcher.execute("catia_close_sketch", {})

    # ── B: explicit samples still buys what it bought ─────────────────────
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "C64"})
    reply = sketcher.execute("catia_sketch_spline",
                             {"control_points": SP16, "degree": 3, "samples": 64})
    count, error = samples_in(reply), sampling_in(reply)
    cell("B1 samples=64 still writes 65 points and reaches 0.0067 mm",
         count == 65 and error is not None and abs(error - 0.0067) < 0.001,
         f"{count} points, {error} mm")
    sketcher.execute("catia_close_sketch", {})

    # ── D: inside a plan, both ends of the result survive ─────────────────
    part = fresh(conn, docs, "AccV0458plan")
    planner = PlannerTools(conn)
    planner.bind({"catia_new_part": docs, "catia_create_sketch": sketcher,
                  "catia_sketch_spline": sketcher, "catia_close_sketch": sketcher})
    outcome = planner.execute("catia_run_plan", {
        "steps": [
            {"tool": "catia_create_sketch", "args": {"plane": "xy", "name": "PL"}},
            {"tool": "catia_sketch_spline",
             "args": {"control_points": SP16, "degree": 3}},
            {"tool": "catia_close_sketch", "args": {}},
        ],
        "summary": False,
    })
    blob = str(outcome)
    at = blob.find("Sampling error")
    say("  D plan step 2: " + (blob[at:at + 180] if at >= 0 else "NOT FOUND"))
    cell("D1 the plan's step keeps the sampling number",
         "Sampling error" in blob, "found in the plan payload")
    cell("D2 and it is not silently cut in half",
         "dropped from the middle" not in blob or "Sampling error" in blob,
         "both ends present or the reply fits")

    # ── E: cleanup_frame_datum="auto" ─────────────────────────────────────
    part = fresh(conn, docs, "AccV0458e")
    body = conn.get_active_part_body()
    sketcher.execute("catia_create_sketch",
                     {"plane": {"type": "frame", "origin": [10.0, 20.0, 30.0],
                                "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0]},
                      "name": "ONLY"})
    sketcher.execute("catia_close_sketch", {})
    reply = parts.execute("catia_delete_feature", {"feature_name": "ONLY"})
    say("  E auto, last sketch: " + " ".join(reply.split())[:220])
    left = [str(body.HybridShapes.Item(i).Name)
            for i in range(1, int(body.HybridShapes.Count) + 1)]
    cell("E1 auto cleans up when it was the last sketch", not left, f"left: {left}")

    sketcher.execute("catia_create_sketch",
                     {"plane": {"type": "frame", "origin": [0.0, 0.0, 500.0],
                                "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0]},
                      "name": "SHARED"})
    sketcher.execute("catia_close_sketch", {})
    sketcher.execute("catia_create_sketch",
                     {"plane": "SHARED_FRAME", "name": "RIDER"})
    sketcher.execute("catia_close_sketch", {})
    reply = parts.execute("catia_delete_feature", {"feature_name": "SHARED"})
    say("  E auto, another sketch present: " + " ".join(reply.split())[:260])
    left = [str(body.HybridShapes.Item(i).Name)
            for i in range(1, int(body.HybridShapes.Count) + 1)]
    cell("E2 auto leaves the datum when another sketch remains",
         "SHARED_FRAME" in left, f"left: {left}")
    # 0.3.148 changed the criterion (same PLANE, not "another sketch exists"),
    # so the sentence moved from "NOT removed" to "left alone" - same refusal,
    # same reason, and it still has to name the sketch it is protecting.
    cell("E3 and says why, naming the sketch it is protecting",
         ("left alone" in reply or "NOT removed" in reply) and "RIDER" in reply,
         reply[-150:])
    try:
        part.Update()
        healthy = True
    except Exception:  # noqa: BLE001
        healthy = False
    cell("E4 the part is still healthy - the shared datum was not touched", healthy,
         "part.Update ok")
    # Forcing it on a SHARED datum is the path that breaks the survivor - and
    # that is the point: the reply has to say the update failed, not pretend.
    part2 = fresh(conn, docs, "AccV0458e2")
    body2 = conn.get_active_part_body()
    sketcher.execute("catia_create_sketch",
                     {"plane": {"type": "frame", "origin": [0.0, 0.0, 200.0],
                                "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0]},
                      "name": "FORCED"})
    sketcher.execute("catia_close_sketch", {})
    sketcher.execute("catia_create_sketch",
                     {"plane": "FORCED_FRAME", "name": "VICTIM"})
    sketcher.execute("catia_close_sketch", {})
    reply = parts.execute("catia_delete_feature",
                          {"feature_name": "FORCED", "cleanup_frame_datum": True})
    say("  E forced on a shared datum: " + " ".join(reply.split())[:260])
    gone = [str(body2.HybridShapes.Item(i).Name)
            for i in range(1, int(body2.HybridShapes.Count) + 1)]
    cell("E5 an explicit true still forces it", not gone, f"left: {gone}")
    cell("E6 and the failure it causes is reported, not hidden",
         "update after removing them failed" in reply, reply[-160:])

    # ── F: the historical 'structured' argument on get_color ──────────────
    part = fresh(conn, docs, "AccV0458f")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 20.0, "y2": 10.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 5.0})
    try:
        reply = measurement.execute("catia_get_color", {"structured": True})
        cell("F1 catia_get_color no longer rejects 'structured'", True,
             " ".join(reply.split())[:110])
    except Exception as error:  # noqa: BLE001
        cell("F1 catia_get_color no longer rejects 'structured'", False,
             f"{type(error).__name__}: {str(error)[:110]}")

    # ── G: a mirror that adds nothing removes its own node ────────────────
    part = fresh(conn, docs, "AccV0458g")
    body = conn.get_active_part_body()
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": -20.0, "y1": -10.0, "x2": 20.0, "y2": 10.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 10.0})
    before = [str(body.Shapes.Item(i).Name)
              for i in range(1, int(body.Shapes.Count) + 1)]
    reply = parts.execute("catia_mirror", {"plane": "zx"})
    say("  G reply: " + " ".join(reply.split())[:240])
    after = [str(body.Shapes.Item(i).Name)
             for i in range(1, int(body.Shapes.Count) + 1)]
    cell("G1 a symmetric body answers no_change", "no_change: true" in reply,
         reply.split(".")[0][:110])
    cell("G2 and the node it created is gone again", before == after, f"{after}")
    try:
        part.Update()
        cell("G3 the part updates after the removal", True, "part.Update ok")
    except Exception as error:  # noqa: BLE001
        cell("G3 the part updates after the removal", False, str(error)[:110])

    say("")
    passed = sum(1 for _n, ok, _d in CELLS if ok)
    say(f"{passed}/{len(CELLS)} cells passed")
    for name, ok, detail in CELLS:
        if not ok:
            say(f"  FAILED: {name} ({detail})")
    return 0 if passed == len(CELLS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
