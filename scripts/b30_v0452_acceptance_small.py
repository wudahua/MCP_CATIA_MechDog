"""Acceptance for the three smaller 2026-11-17 items, on B30.

  A  fillet by 4 edge_anchors still lands on 4/4 edges (no regression)
  B  an anchor 3 mm off answers ANCHORS_NOT_MATCHED with the candidates in `data`
     (name, distance) instead of only a sentence
  C  catia_close_document accepts the STEM, like catia_activate_document always has
  D  a plan whose expect fails keeps its step records under summary=true
  E  a plan that passes still answers steps: [] under summary=true

.venv/Scripts/python.exe -u scripts/b30_v0452_acceptance_small.py
"""
from __future__ import annotations

import json
import os
import time
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
                    or tempfile.gettempdir(), "catia_probe_v0452")
CELLS: list[tuple[str, bool, str]] = []
#: One suffix per run: a plan that creates a document cannot reuse a name whose
#: file is still on disk from the previous run of this script.
RUN = str(int(time.time()))[-5:]


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


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    planner = PlannerTools(conn)
    measurement = MeasurementTools(conn)
    # The planner executes steps through the server's router, so it has to be
    # bound to one. These are the modules this script uses.
    planner.bind({name: module for name, module in (
        ("catia_new_part", docs), ("catia_create_sketch", sketcher),
        ("catia_sketch_rectangle", sketcher), ("catia_close_sketch", sketcher),
        ("catia_pad", parts), ("catia_get_inertia", measurement))})
    say(f"acceptance v0452 on {conn.app.Name}")

    # ── A: four anchors on the four vertical edges of a box ───────────────
    box = fresh(conn, docs, "AccV0452Box")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0.0, "y1": 0.0,
                                               "x2": 40.0, "y2": 30.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 20.0})
    anchors = [[0.0, 0.0, 10.0], [40.0, 0.0, 10.0],
               [40.0, 30.0, 10.0], [0.0, 30.0, 10.0]]
    reply = parts.execute("catia_fillet", {"radius": 4.0, "edge_anchors": anchors})
    say("  A reply: " + " ".join(reply.split())[-220:])
    cell("A1 four anchors still fillet four edges",
         "4/4 edge(s) done" in reply, reply.split(".")[0][:110])
    cell("A2 every anchor is reported with the edge it landed on",
         reply.count("->") >= 4 and "0.0000 mm" in reply,
         f"{reply.count('->')} anchor match(es) in the reply")

    # ── B: an anchor that misses, with candidates in the payload ──────────
    try:
        parts.execute("catia_fillet", {"radius": 5.0,
                                       "edge_anchors": [[3.0, -3.2, 10.0]]})
        cell("B1 a missing anchor is refused", False, "the call succeeded")
    except CodedError as error:
        missed = (error.data or {}).get("missed") or []
        cell("B1 a missing anchor is refused",
             error.code == "ANCHORS_NOT_MATCHED", f"code={error.code}")
        cell("B2 the payload names the nearest edge and the distance",
             bool(missed) and missed[0].get("nearest_edge")
             and abs(float(missed[0].get("distance_mm") or 0) - 3.2) < 1.0,
             json.dumps(missed[0], ensure_ascii=False)[:150] if missed else "none")
        cell("B3 and offers the calls that fix it",
             len(error.next_actions or []) >= 2,
             (error.next_actions or [""])[0][:90])

    # ── C: close by stem ──────────────────────────────────────────────────
    part = fresh(conn, docs, "AccV0452Stem")
    say(f"  C document name: {part.Parent.Name}")
    try:
        say("  " + docs.execute("catia_close_document",
                                {"document": "AccV0452Stem"})[:120])
        still = any("AccV0452Stem" in str(conn.documents.Item(i).Name)
                    for i in range(1, int(conn.documents.Count) + 1))
        cell("C1 a document closes when named by its stem", not still,
             f"open now: {conn.documents.Count}")
    except CodedError as error:
        cell("C1 a document closes when named by its stem", False, str(error)[:110])
    try:
        docs.execute("catia_close_document", {"document": "NoSuchDoc"})
        cell("C2 an unknown name is still refused", False, "it succeeded")
    except CodedError as error:
        cell("C2 an unknown name is still refused",
             error.code == "DOCUMENT_NOT_FOUND", f"code={error.code}")

    # ── D/E: run_plan with summary, failing and passing ───────────────────
    def plan_steps(which: str) -> list[dict]:
        # A fresh document per run: the second plan cannot reuse the first one's
        # name, and a step that fails for that reason is my script's fault, not
        # the tool's.
        return [
            {"tool": "catia_new_part", "args": {
                "name": f"AccV0452Plan{which}{RUN}",
                "file_path": os.path.join(
                    ROOT, f"AccV0452Plan{which}{RUN}.CATPart")}},
            {"tool": "catia_create_sketch", "args": {"plane": "xy"}},
            {"tool": "catia_sketch_rectangle", "args": {"x1": 0.0, "y1": 0.0,
                                                       "x2": 20.0, "y2": 10.0}},
            {"tool": "catia_close_sketch", "args": {}},
            {"tool": "catia_pad", "args": {"height": 5.0}},
        ]
    failing = planner._run_plan({"steps": plan_steps("A"), "summary": True,
                                 "expect": {"volume_mm3": 999999.0}})
    data = failing["data"]
    say(f"  D code={failing['code']} steps={len(data.get('steps') or [])} "
        f"omitted={data.get('steps_omitted')}")
    cell("D1 a failed assertion keeps all five step records",
         len(data.get("steps") or []) == 5 and data.get("steps_omitted") == 0,
         f"{len(data.get('steps') or [])} record(s), "
         f"omitted={data.get('steps_omitted')}")
    cell("D2 the verdict is still the failed assertion",
         failing["code"] == "ASSERTION_FAILED", failing["code"])
    cell("D3 the note explains why nothing was trimmed",
         "no STEP failed" in str(data.get("summary_note")),
         str(data.get("summary_note"))[:100])

    passing = planner._run_plan({"steps": plan_steps("B"), "summary": True,
                                 "expect": {"volume_mm3": 1000.0,
                                            "volume_tolerance_pct": 5.0}})
    say(f"  E message: {passing['message'][:200]}")
    say(f"  E measurements: {json.dumps(passing['data'].get('measurements'), ensure_ascii=False)[:200]}")
    cell("E a passing plan still trims steps to nothing",
         passing["code"] == "PLAN_OK"
         and (passing["data"].get("steps") or []) == [],
         f"code={passing['code']}, "
         f"steps={len(passing['data'].get('steps') or [])}")

    say("")
    passed = sum(1 for _name, ok, _detail in CELLS if ok)
    say(f"{passed}/{len(CELLS)} cells passed")
    for name, ok, detail in CELLS:
        if not ok:
            say(f"  FAILED: {name}  ({detail})")
    return 0 if passed == len(CELLS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
