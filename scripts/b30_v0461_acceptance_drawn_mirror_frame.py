"""Acceptance for the 2026-11-18 (0.3.147) report, on B30.

  A  P0 - the drawn error of a spline on a frame whose u is 1000 mm long: it must
     be the same number as on a unit-u frame, not 99 900 mm
  B  P0 - the report's own weighted quarter circle, and its pad volume
  C  P1 - the report's own body (material at z >= 0): the reply explains the
     doubling with the measured envelope instead of leaving it unexplained
  D  P1 - a body the plane really cuts through: no doubling, and no duplication
     sentence claiming one
  E  P2 - three frame sketches on three planes: deleting one now cleans its
     scaffolding up (the report's P2: "auto never fires")
  F  P2 - a sketch built ON the datum: refused, the survivor is named, datum kept
  G  P3 - structured=true: the two errors as numbers, and a plan step carries them

.venv/Scripts/python.exe -u scripts/b30_v0461_acceptance_drawn_mirror_frame.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.planner import PlannerTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0461")
CELLS: list[tuple[str, bool, str]] = []
RUN = str(int(time.time()))[-5:]

QUARTER = [[100.0, 0.0], [100.0, 100.0], [0.0, 100.0]]
WEIGHTS = [1.0, 0.70710678, 1.0]


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


def drawn_in(text: str) -> float | None:
    found = re.search(r"stays within ([\d.]+) mm of it", text)
    return float(found.group(1)) if found else None


def sampling_in(text: str) -> float | None:
    found = re.search(r"Sampling error ([\d.]+) mm", text)
    return float(found.group(1)) if found else None


def volume(conn, part, body) -> float | None:
    try:
        spa = part.Parent.GetWorkbench("SPAWorkbench")
        return float(spa.GetMeasurable(
            part.CreateReferenceFromObject(body)).Volume) * 1e9
    except Exception:  # noqa: BLE001
        return None


def tree_names(body) -> list[str]:
    names = []
    try:
        for index in range(1, int(body.HybridShapes.Count) + 1):
            names.append(str(getattr(body.HybridShapes.Item(index), "Name", "")))
    except Exception:  # noqa: BLE001
        pass
    return names


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    say(f"acceptance v0461 on {conn.app.Name}  run {RUN}")

    # ── A: the same curve on a 1000 mm u and on a unit u ──────────────────
    part = fresh(conn, docs, "AccV0461long")
    sketcher.execute("catia_create_sketch",
                     {"plane": {"type": "frame", "origin": [0.0, 0.0, 0.0],
                                "u": [1000.0, 0.0, 0.0], "v": [0.0, 1000.0, 0.0]},
                      "name": "LONG"})
    long_reply = sketcher.execute("catia_sketch_spline",
                                  {"control_points": QUARTER, "degree": 2,
                                   "weights": WEIGHTS})
    say("  A long-u: " + " ".join(long_reply.split())[:220])
    sketcher.execute("catia_close_sketch", {})

    part = fresh(conn, docs, "AccV0461unit")
    sketcher.execute("catia_create_sketch",
                     {"plane": {"type": "frame", "origin": [0.0, 0.0, 0.0],
                                "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0]},
                      "name": "UNIT"})
    unit_reply = sketcher.execute("catia_sketch_spline",
                                  {"control_points": QUARTER, "degree": 2,
                                   "weights": WEIGHTS})
    say("  A unit-u: " + " ".join(unit_reply.split())[:220])
    sketcher.execute("catia_close_sketch", {})

    long_drawn, unit_drawn = drawn_in(long_reply), drawn_in(unit_reply)
    cell("A1 a 1000 mm u reports the same drawn error as a unit u",
         long_drawn is not None and unit_drawn is not None
         and abs(long_drawn - unit_drawn) < 0.01,
         f"long-u {long_drawn} mm, unit-u {unit_drawn} mm")
    cell("A2 and it is a curve-sized number, not 99 900 mm",
         long_drawn is not None and long_drawn < 1.0,
         f"{long_drawn} mm (the report saw 99 900.0000)")
    cell("A3 both frames report the same sampling error",
         sampling_in(long_reply) is not None
         and abs(sampling_in(long_reply) - sampling_in(unit_reply)) < 1e-6,
         f"{sampling_in(long_reply)} mm")

    # ── B: the report's quarter circle closes and pads to the analytic area
    part = fresh(conn, docs, "AccV0461pad")
    body = conn.get_active_part_body()
    sketcher.execute("catia_create_sketch",
                     {"plane": {"type": "frame", "origin": [0.0, 0.0, 0.0],
                                "u": [1000.0, 0.0, 0.0], "v": [0.0, 1000.0, 0.0]},
                      "name": "ARC"})
    sketcher.execute("catia_sketch_spline",
                     {"control_points": QUARTER, "degree": 2, "weights": WEIGHTS})
    sketcher.execute("catia_sketch_line", {"x1": 0.0, "y1": 100.0,
                                           "x2": 0.0, "y2": 0.0})
    sketcher.execute("catia_sketch_line", {"x1": 0.0, "y1": 0.0,
                                           "x2": 100.0, "y2": 0.0})
    sketcher.execute("catia_close_sketch", {})
    pad = parts.execute("catia_pad", {"height": 10.0})
    say("  B pad: " + " ".join(pad.split())[:160])
    measured = volume(conn, part, body)
    analytic = 3.14159265358979 * 100.0 * 100.0 / 4 * 10.0
    cell("B1 the quarter circle still pads to its analytic volume",
         measured is not None and abs(measured - analytic) / analytic < 2e-4,
         f"{measured:.1f} mm3 vs {analytic:.1f} (-0.00002 % on the report's build)")

    # ── C: the report's own mirror case, material at z >= 0 ───────────────
    part = fresh(conn, docs, "AccV0461mirror")
    body = conn.get_active_part_body()
    sketcher.execute("catia_create_sketch",
                     {"plane": {"type": "frame", "origin": [0.0, 0.0, 1000.0],
                                "u": [1000.0, 0.0, 0.0], "v": [0.0, 1000.0, 0.0]},
                      "name": "SK_BOX2"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 40.0, "y2": 40.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 20.0, "symmetric": True})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SK_Q"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 100.0, "y2": 100.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 10.0})
    before = volume(conn, part, body)
    reply = parts.execute("catia_mirror", {"plane": "xy", "allow_body_mirror": True})
    say("  C mirror: " + " ".join(reply.split())[:400])
    after = volume(conn, part, body)
    cell("C1 the volume doubles, as a disjoint copy must",
         before is not None and after is not None
         and abs(after - 2 * before) < 1e-6 * max(1.0, before),
         f"{before:.4f} -> {after:.4f} mm3")
    cell("C2 and the reply SAYS why instead of leaving it unexplained",
         "EVERY mm3 was added again" in reply, "duplication sentence present")
    cell("C3 with the measured envelope, not an assumption",
         ("sits on one side" in reply or "all on one side" in reply)
         and "1010.0000" in reply,
         "vertices named in the reply")
    cell("C4 and it does not call the volume suspect", "SUSPECT" not in reply,
         "no false alarm on a legitimate doubling")

    # ── D: a body the plane really cuts through ───────────────────────────
    part = fresh(conn, docs, "AccV0461straddle")
    body = conn.get_active_part_body()
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "H1"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 40.0, "y2": 40.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 20.0, "symmetric": True})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "H2"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 100.0, "y1": 0.0, "x2": 120.0, "y2": 20.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 10.0})
    before = volume(conn, part, body)
    reply = parts.execute("catia_mirror", {"plane": "xy", "allow_body_mirror": True})
    after = volume(conn, part, body)
    say("  D mirror: " + " ".join(reply.split())[:260])
    cell("D1 a symmetric body is NOT double counted (the report's premise)",
         before is not None and after is not None
         and abs(after - (before + 4000.0)) < 1.0,
         f"{before:.4f} -> {after:.4f} mm3 (+4000 = only the pad that moved)")
    cell("D2 and no duplication sentence is printed for it",
         "EVERY mm3" not in reply, "no false positive")

    # ── E: auto cleans up when the other sketches are on their own planes ─
    part = fresh(conn, docs, "AccV0461auto")
    body = conn.get_active_part_body()
    for index, height in enumerate((0.0, 100.0, 200.0), start=1):
        sketcher.execute("catia_create_sketch",
                         {"plane": {"type": "frame", "origin": [0.0, 0.0, height],
                                    "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0]},
                          "name": f"SK_R{index}"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0.0, "y1": 0.0, "x2": 10.0, "y2": 10.0})
        sketcher.execute("catia_close_sketch", {})
    reply = parts.execute("catia_delete_feature", {"feature_name": "SK_R2"})
    say("  E delete: " + " ".join(reply.split())[:300])
    names = tree_names(body)
    cell("E1 auto now removes the scaffolding of a frame sketch",
         "removed the frame scaffolding" in reply, "auto fired")
    cell("E2 the other two frames are untouched",
         "SK_R1_FRAME" in names and "SK_R3_FRAME" in names
         and "SK_R2_FRAME" not in names,
         f"{len(names)} element(s) left: {', '.join(sorted(names)[:6])}")

    # ── F: a sketch standing ON the datum is protected ────────────────────
    part = fresh(conn, docs, "AccV0461shared")
    body = conn.get_active_part_body()
    sketcher.execute("catia_create_sketch",
                     {"plane": {"type": "frame", "origin": [0.0, 0.0, 1000.0],
                                "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0]},
                      "name": "OWNER"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 10.0, "y2": 10.0})
    sketcher.execute("catia_close_sketch", {})
    sketcher.execute("catia_create_sketch", {"plane": "OWNER_FRAME",
                                            "name": "RIDER"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 20.0, "y1": 20.0, "x2": 30.0, "y2": 30.0})
    sketcher.execute("catia_close_sketch", {})
    reply = parts.execute("catia_delete_feature", {"feature_name": "OWNER"})
    say("  F delete: " + " ".join(reply.split())[:340])
    names = tree_names(body)
    cell("F1 the datum under a live sketch is kept, and the sketch is named",
         "left alone" in reply and "RIDER" in reply, "refusal names RIDER")
    cell("F2 nothing was removed from the tree", "OWNER_FRAME" in names,
         "datum still present")
    try:
        part.Update()
        healthy = True
    except Exception as error:  # noqa: BLE001
        healthy = False
        say(f"  F update failed: {str(error).splitlines()[0][:120]}")
    cell("F3 and the part still updates, because nothing was deleted", healthy,
         "part.Update() ok")

    # ── G: the numbers as numbers, and a plan step that keeps them ────────
    part = fresh(conn, docs, "AccV0461metrics")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "M"})
    payload = sketcher.execute("catia_sketch_spline",
                               {"control_points": QUARTER, "degree": 2,
                                "weights": WEIGHTS, "structured": True})
    say(f"  G structured: {sorted(payload.keys()) if isinstance(payload, dict) else type(payload)}")
    data = payload.get("data") if isinstance(payload, dict) else None
    cell("G1 structured=true returns the metrics as numbers",
         isinstance(data, dict) and data.get("sampling_error_mm") is not None
         and data.get("drawn_error_state") == "measured",
         f"sampling {data.get('sampling_error_mm') if data else None} mm, "
         f"drawn {data.get('drawn_error_mm') if data else None} mm")
    cell("G2 the spline still draws the curve it says it did",
         isinstance(data, dict) and data.get("sample_points_written") == 33,
         f"{data.get('sample_points_written') if data else None} sample points")
    sketcher.execute("catia_close_sketch", {})

    part = fresh(conn, docs, "AccV0461plan")
    planner = PlannerTools(conn)
    planner.bind({"catia_new_part": docs, "catia_create_sketch": sketcher,
                  "catia_sketch_spline": sketcher, "catia_close_sketch": sketcher})
    outcome = planner.execute("catia_run_plan", {
        "steps": [
            {"tool": "catia_create_sketch", "args": {"plane": "xy", "name": "PL"}},
            {"tool": "catia_sketch_spline",
             "args": {"control_points": QUARTER, "degree": 2,
                      "weights": WEIGHTS, "structured": True}},
            {"tool": "catia_close_sketch", "args": {}},
        ]})
    steps = outcome.get("data", {}).get("steps", []) if isinstance(outcome, dict) else []
    metrics = steps[1].get("metrics") if len(steps) > 1 else None
    say(f"  G plan step 2 metrics: {metrics}")
    cell("G3 a plan step carries the same numbers under 'metrics'",
         isinstance(metrics, dict) and metrics.get("sampling_error_mm") is not None,
         f"metrics keys: {sorted(metrics) if isinstance(metrics, dict) else metrics}")

    say("")
    passed = sum(1 for _name, ok, _detail in CELLS if ok)
    say(f"{passed}/{len(CELLS)} cells passed")
    return 0 if passed == len(CELLS) else 1


if __name__ == "__main__":
    sys.exit(main())
