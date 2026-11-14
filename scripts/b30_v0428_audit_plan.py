"""0.3.126 acceptance on B30: the plan's semantic dry run and its rollback (P1-1).

The report's story: a 21-step plan was accepted by dry_run, died at step 10 of 21,
and left three sketches and three datum planes in the model with nothing naming
them. This checks the two halves of the fix, and the numbers come from the plan
itself:

  1  dry_run refuses the very plan the report's dry_run accepted
  2  ... and answers the CODE the tool would answer, not a new one
  3  dry_run on a good plan still says PLAN_VALID, and counts what it checked
  4  a plan that fails at run time names what it had already built
  5  rollback=true takes that work back, and the tree is read back to prove it
  6  without rollback the work is kept, and said to be kept

    .venv/Scripts/python.exe scripts/b30_v0428_audit_plan.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.planner import PlannerTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0428")
ROOT = os.path.join(_PROBE_ROOT, "audit_plan")
RESULTS: list[tuple[int, str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, title: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, title, bool(passed), detail))
    say(f"  [{'PASS' if passed else 'FAIL'}] {number}. {title}"
        + (f"  ({detail})" if detail else ""))


def fresh(conn: CATIAConnection, documents: DocumentTools, name: str):
    path = os.path.join(ROOT, name + ".CATPart")
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
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    for index in range(1, int(conn.documents.Count) + 1):
        if conn.documents.Item(index).Name == name + ".CATPart":
            conn.documents.Item(index).Activate()
    return conn.get_active_part()


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    measurement = MeasurementTools(conn)
    planner = PlannerTools(conn)
    planner.bind({
        "catia_create_sketch": sketcher,
        "catia_sketch_circle": sketcher,
        "catia_sketch_ellipse": sketcher,
        "catia_close_sketch": sketcher,
        "catia_activate_document": documents,
        "catia_list_features": part_design,
        "catia_delete_feature": part_design,
        "catia_get_inertia": measurement,
        "catia_solid_loft": part_design,
    })

    # The report's own numbers, as step 4 of a plan whose first three steps work.
    bad_plan = [
        {"tool": "catia_create_sketch", "args": {"plane": "xy"}},
        {"tool": "catia_sketch_circle", "args": {"cx": 0, "cy": 0, "radius": 8}},
        {"tool": "catia_sketch_ellipse",
         "args": {"cx": 0, "cy": 0, "major": 5.2, "minor": 5.8}},
    ]
    # A step that passes every schema and every semantic check and still fails at
    # run time - the only kind that can leave a half-built model to clean up.
    failing_plan = [
        {"tool": "catia_create_sketch", "args": {"plane": "xy"}},
        {"tool": "catia_sketch_circle", "args": {"cx": 0, "cy": 0, "radius": 8}},
        {"tool": "catia_close_sketch", "args": {}},
        {"tool": "catia_activate_document", "args": {"name": "NoSuchDocument_ZZZ"}},
    ]

    say("")
    say("── 1/2/3: dry_run runs the tools' own checks ──")
    dry = planner.execute("catia_run_plan", {"steps": bad_plan, "dry_run": True})
    payload = dry if isinstance(dry, dict) else json.loads(str(dry))
    say("  " + json.dumps({"ok": payload.get("ok"), "code": payload.get("code"),
                           "message": " ".join(str(payload.get("message")).split())[:220]},
                          ensure_ascii=False))
    check(1, "dry_run REFUSES the plan the report's dry_run accepted",
          payload.get("ok") is False and payload.get("code") == "BAD_ARGS",
          str(payload.get("code")))
    semantic = ((payload.get("data") or {}).get("semantic_problems") or [])
    check(2, "and names the step with the tool's own code",
          bool(semantic) and semantic[0].get("code") == "BAD_ARGUMENT"
          and semantic[0].get("step") == 3,
          f"step {semantic[0].get('step')} {semantic[0].get('code')}"
          if semantic else "no semantic_problems")

    fresh(conn, documents, "AuditPlanDry")
    good = planner.execute("catia_run_plan", {"steps": failing_plan,
                                              "dry_run": True})
    good_payload = good if isinstance(good, dict) else json.loads(str(good))
    say("  " + json.dumps({"ok": good_payload.get("ok"),
                           "code": good_payload.get("code"),
                           "semantic_checks": (good_payload.get("data") or {})
                           .get("semantic_checks")}, ensure_ascii=False))
    check(3, "a good plan still passes dry_run, and says how much was checked",
          good_payload.get("ok") is True
          and "semantic_checks" in (good_payload.get("data") or {}),
          str((good_payload.get("data") or {}).get("semantic_checks")))

    say("")
    say("── 4/6: a run-time failure, WITHOUT rollback, keeps the work ──")
    fresh(conn, documents, "AuditPlanKeep")
    kept = planner.execute("catia_run_plan", {"steps": failing_plan})
    kept_payload = kept if isinstance(kept, dict) else json.loads(str(kept))
    kept_data = kept_payload.get("data") or {}
    say("  " + " ".join(str(kept_payload.get("message")).split())[:400])
    created = kept_data.get("created_features") or []
    check(4, "the failure names what the plan had already built",
          kept_payload.get("ok") is False and bool(created),
          ", ".join(created[:4]) or "nothing named")
    check(5, "and says it is still in the model",
          kept_data.get("created_features_status") == "kept",
          str(kept_data.get("created_features_status")))
    features = json.loads(part_design.execute("catia_list_features", {}))
    features_text = json.dumps(features, ensure_ascii=False)
    check(6, "the tree really does still hold them",
          any(name in features_text for name in created),
          ", ".join(created[:3]))

    say("")
    say("── 5: the same failure WITH rollback=true ──")
    fresh(conn, documents, "AuditPlanRoll")
    rolled = planner.execute("catia_run_plan", {"steps": failing_plan,
                                                "rollback": True})
    rolled_payload = rolled if isinstance(rolled, dict) else json.loads(str(rolled))
    rolled_data = rolled_payload.get("data") or {}
    say("  " + " ".join(str(rolled_payload.get("message")).split())[:400])
    rollback = rolled_data.get("rollback") or {}
    check(7, "rollback reports every name it was given",
          bool(rollback.get("names")), str(rollback.get("names")))
    check(8, "and the verdict comes from the tree, not from the reply",
          rollback.get("verified") is True,
          f"verified={rollback.get('verified')} left={rollback.get('left')}")
    check(9, "everything the plan built was deleted again",
          rolled_data.get("created_features_status") == "rolled_back"
          and not rollback.get("left"),
          f"status={rolled_data.get('created_features_status')}")

    after = json.loads(part_design.execute("catia_list_features", {}))
    after_text = json.dumps(after, ensure_ascii=False)
    survived = [name for name in (rollback.get("names") or [])
                if name in after_text]
    check(10, "the document no longer holds any of them",
          not survived, f"still present: {survived}" if survived else "none left")

    # Rollback deletes NEWEST FIRST. A single-feature plan cannot show the order,
    # so the reverse order is asserted on the plan that built three things above.
    check(11, "the rollback order is newest first",
          bool(created) and list(rollback.get("names") or []) == list(reversed(created)),
          f"{rollback.get('names')} against reversed {created}")

    say("")
    passed = sum(1 for _n, _t, ok, _d in RESULTS if ok)
    say(f"{passed}/{len(RESULTS)} checks passed")
    for number, title, ok, detail in RESULTS:
        if not ok:
            say(f"  FAILED {number}. {title}  {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
