"""0.3.139 acceptance on B30 - the chamfer P0 from the 2026-11-16 report.

The report's own words: `1/2 edge(s) done` came back as ordinary prose, so every
caller that branches on status was told the call succeeded, and `run_plan` counted
the step as passed. This run checks the failure is now visible, on the geometry
that reproduces it (a plain box with two ADJACENT top edges - the report's part
was a four-lobe column, but a box is enough, measured).

    0  the same shape twice: two OPPOSITE top edges are 2/2 (the 2026-11-13
       experiment's shape, which is why it found nothing)
    A  two ADJACENT top edges: PARTIAL_EDGES_FAILED, modified=true, one applied
       and one rejected, with the reason
    B  the solid moved by ONE bevel's worth, not two - the half-applied state the
       report's volume reading caught
    C  all four top edges: 2/4, still a coded failure
    D  run_plan counts the step as FAILED (the report's point 3)
    E  the all-edges path (no edge named) still answers in prose, because there
       the caller did not ask for a specific set

    .venv/Scripts/python.exe -u scripts/b30_v0440_chamfer_acceptance.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.results import CodedError  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.planner import PlannerTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0440a")
TOP = 55.8
ROWS: list[tuple[str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def cell(what: str, ok: bool, detail: str = "") -> None:
    ROWS.append((what, ok, detail))
    say(f"  [{'OK  ' if ok else 'FAIL'}] {what}" + (f"  -> {detail}" if detail else ""))


def close_ours(conn) -> None:
    root = os.path.abspath(ROOT).lower()
    for index in range(int(conn.documents.Count), 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = str(doc.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).lower().startswith(root):
            try:
                doc.Close()
            except Exception:  # noqa: BLE001
                pass


def fresh(conn, docs, name: str):
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, name + ".CATPart")
    close_ours(conn)
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    return conn.get_active_part()


def box(conn, docs, sketcher, parts, name: str) -> Any:
    part = fresh(conn, docs, name)
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": -27.5, "y1": -27.5, "x2": 27.5, "y2": 27.5})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": TOP})
    return part


def volume(measurement) -> float | None:
    try:
        return json.loads(measurement.execute("catia_get_inertia", {})).get("volume_mm3")
    except Exception:  # noqa: BLE001
        return None


def chamfer(parts, measurement, anchors, length=8.0, angle=70.0):
    """Run one chamfer and return (status, text, data, before, after)."""
    before = volume(measurement)
    text, data, code = "", {}, ""
    try:
        text = parts.execute("catia_chamfer",
                             {"length": length, "angle": angle,
                              "edge_anchors": anchors, "anchor_tol": 1.0,
                              "on_anchor_miss": "skip"})
        code = "NO_ERROR"
        data = {}
    except CodedError as error:
        text = str(error)
        code = error.code
        data = dict(error.data or {})
        data["_modified"] = error.modified
    except Exception as error:  # noqa: BLE001
        text = str(error)
        code = "OTHER:" + type(error).__name__
    return code, text, data, before, volume(measurement)


ADJACENT = [[27.5, 0.0, TOP], [0.0, 27.5, TOP]]
OPPOSITE = [[27.5, 0.0, TOP], [-27.5, 0.0, TOP]]
FOUR_TOP = [[27.5, 0.0, TOP], [0.0, 27.5, TOP],
            [-27.5, 0.0, TOP], [0.0, -27.5, TOP]]


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    parts = PartDesignTools(conn)
    measurement = MeasurementTools(conn)
    planner = PlannerTools(conn)
    # A planner with no router answers "Planner is not bound to the tool router",
    # which is a test of the binding, not of the plan.
    planner.bind({"catia_chamfer": parts, "catia_new_part": docs,
                  "catia_list_features": parts})
    say(f"acceptance 0.3.139 on {conn.app.Name}")

    # ── 0: the shape that does NOT fail ────────────────────────────────────
    say("\n0. two OPPOSITE top edges - the 2026-11-13 shape")
    box(conn, docs, sketcher, parts, "Acc139Opposite")
    code, text, data, before, after = chamfer(parts, measurement, OPPOSITE)
    cell("0.1 it still succeeds, so the trigger is not 'two chamfers'",
         code == "NO_ERROR" and "2/2" in text, f"{code}: "
         + " ".join(text.split())[:110])
    two_bevels = (before or 0) - (after or 0)
    say(f"        one call removed {two_bevels:.1f} mm3 (two bevels)")

    # ── A: the shape that DOES fail ────────────────────────────────────────
    say("\nA. two ADJACENT top edges (they share a corner)")
    box(conn, docs, sketcher, parts, "Acc139Adjacent")
    code, text, data, before, after = chamfer(parts, measurement, ADJACENT)
    say("        " + " ".join(text.split())[:340])
    cell("A1 it is a coded failure, not a sentence", code == "PARTIAL_EDGES_FAILED",
         code)
    cell("A2 modified=true, because one bevel WAS applied",
         data.get("_modified") is True, str(data.get("_modified")))
    cell("A3 one applied and one rejected, by name",
         len(data.get("applied") or []) == 1
         and len(data.get("rejected") or []) == 1,
         f"applied={data.get('applied')} rejected={data.get('rejected')}")
    cell("A4 the counts are in the data",
         data.get("done") == 1 and data.get("requested") == 2,
         f"{data.get('done')}/{data.get('requested')}")
    reason = " ".join((data.get("rejected_reasons") or {}).values())
    cell("A5 the reason is the one the report saw (UpdateObject failed)",
         "UpdateObject" in reason, reason[:120])
    cell("A6 the message still names both anchors and the batch retry",
         "one-at-a-time retry" in text and "Anchor" in text)

    # ── B: half-applied, measured ──────────────────────────────────────────
    say("\nB. how much material moved - one bevel or two?")
    moved = (before or 0) - (after or 0)
    cell("B1 it removed ONE bevel's worth, not two",
         0 < moved < two_bevels * 0.75,
         f"moved {moved:.1f} mm3, two bevels would be {two_bevels:.1f}")

    # ── C: four edges, two of them fail ────────────────────────────────────
    say("\nC. all four top edges")
    box(conn, docs, sketcher, parts, "Acc139FourTop")
    code, text, data, before, after = chamfer(parts, measurement, FOUR_TOP)
    cell("C1 2 of 4 is a coded failure", code == "PARTIAL_EDGES_FAILED", code)
    cell("C2 the data says 2 and 2",
         data.get("done") == 2 and data.get("requested") == 4,
         f"{data.get('done')}/{data.get('requested')}")

    # ── D: run_plan must count it ──────────────────────────────────────────
    say("\nD. does catia_run_plan count the step as failed?")
    box(conn, docs, sketcher, parts, "Acc139Plan")
    plan = planner.execute("catia_run_plan", {
        "steps": [
            {"tool": "catia_chamfer",
             "args": {"length": 8.0, "angle": 70.0,
                      "edge_anchors": ADJACENT, "anchor_tol": 1.0,
                      "on_anchor_miss": "skip"}},
        ],
        "stop_on_error": True, "summary": True})
    say("        " + " ".join(str(plan).split())[:320])
    cell("D1 the step is FAILED in the plan", "failed" in str(plan).lower())
    cell("D2 it carries the code", "PARTIAL_EDGES_FAILED" in str(plan))

    # ── E: the all-edges path keeps answering in prose ─────────────────────
    say("\nE. every edge at once (the caller named none)")
    box(conn, docs, sketcher, parts, "Acc139AllEdges")
    answer, code = "", ""
    try:
        answer = parts.execute("catia_chamfer",
                               {"length": 8.0, "angle": 70.0, "max_edges": 1})
        code = "NO_ERROR"
    except Exception as error:  # noqa: BLE001
        answer = str(error)
        code = getattr(error, "code", type(error).__name__)
    say("        " + " ".join(answer.split())[:300])
    cell("E1 a short edge being refused is not a failed call",
         code == "NO_ERROR", code)
    cell("E2 and the reply still counts them", "edge(s) done" in answer)

    say("\n--- summary")
    bad = [row for row in ROWS if not row[1]]
    say(f"{len(ROWS) - len(bad)}/{len(ROWS)} ok")
    for what, _, detail in bad:
        say(f"  FAIL {what}  {detail}")
    close_ours(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
