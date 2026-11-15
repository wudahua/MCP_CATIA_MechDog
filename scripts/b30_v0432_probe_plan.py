"""Does rollback take back what a plan built in a document it CREATED?

The 2026-11-14 report's N2: plan = new_part -> import_drawing -> pad, rollback
true, pad fails, reply said "Nothing had been created" while 草图.1 was standing
in the tree. The name diff was taken across two documents.

    python -u scripts/b30_v0432_probe_plan.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.blueprint import BlueprintTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.planner import PlannerTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

PDF = "F:/WokrBuddy Catia Space/苹果入耳式耳机AirPods/苹果入耳式耳机AirPods尺寸平面图.pdf"
ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0432")
CHECKS: list[tuple[int, str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, title: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((number, title, bool(ok), detail))
    say(f"  [{'PASS' if ok else 'FAIL'}] {number:2}. {title}"
        + (f"   ({detail})" if detail else ""))


def clear_ours(conn) -> None:
    for index in range(int(conn.documents.Count), 0, -1):
        document = conn.documents.Item(index)
        try:
            full = str(document.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).startswith(os.path.abspath(ROOT)):
            try:
                document.Close()
            except Exception:  # noqa: BLE001
                pass


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    clear_ours(conn)
    documents = DocumentTools(conn)
    part_design = PartDesignTools(conn)
    blueprint = BlueprintTools(conn)
    planner = PlannerTools(conn)
    sketches = SketcherTools(conn)
    planner.bind({
        "catia_new_part": documents,
        "catia_import_drawing": blueprint,
        "catia_pad": part_design,
        "catia_delete_feature": part_design,
        "catia_list_features": part_design,
    })

    # Start in a part that ALREADY holds 草图.1 - that is what made the name diff
    # answer "nothing was created": the new document's sketch had the same name.
    # Built here rather than borrowed from the reporter's file, so the probe runs
    # on any machine: a name-only snapshot cannot tell this sketch from the one
    # the import is about to draw.
    starter = os.path.join(ROOT, "PlanStarter.CATPart")
    if os.path.exists(starter):
        os.remove(starter)
    documents.execute("catia_new_part",
                      {"name": "PlanStarter", "file_path": starter})
    sketches.execute("catia_create_sketch", {"plane": "xy"})
    sketches.execute("catia_sketch_line", {"x1": 0, "y1": 0, "x2": 10, "y2": 0})
    sketches.execute("catia_close_sketch", {})
    before = part_design.execute("catia_list_features", {})
    say("starting document: " + conn.active_document.Name)
    say("  its sketches: "
        + json.dumps([s["name"] for s in json.loads(before).get("sketches") or []][:3]))

    target = os.path.join(ROOT, "PlanRollback.CATPart")
    if os.path.exists(target):
        os.remove(target)

    say("")
    say("plan: new_part -> import_drawing -> pad(height=0)   rollback=true")
    raw = planner.execute("catia_run_plan", {
        "steps": [
            {"tool": "catia_new_part",
             "args": {"name": "PlanRollback", "file_path": target,
                      "overwrite": True}},
            {"tool": "catia_import_drawing",
             "args": {"file_path": PDF, "region": [250, 85, 270, 140],
                      "min_size": 10, "closed_only": True, "plane": "xy"}},
            {"tool": "catia_pad", "args": {"height": 0}},
        ],
        "rollback": True,
        "summary": True,
    })
    reply = json.loads(raw) if isinstance(raw, str) else raw
    data = reply.get("data") or {}
    created = data.get("created_features")
    say(f"  status  = {reply.get('code')}")
    say(f"  message = {(reply.get('message') or '')[:240]}")
    say(f"  created = {created}")
    say(f"  status  = {data.get('created_features_status')}")

    check(1, "the plan failed at the pad",
          reply.get("ok") is False and reply.get("code") == "STEP_FAILED",
          str(reply.get("code")))
    check(2, "created_features names the sketch the import drew",
          bool(created) and any("草图" in str(name) for name in created),
          str(created)[:80])
    check(3, "the reply says the plan changed document",
          "switched document" in str(data.get("created_features_document") or ""),
          str(data.get("created_features_document")))
    check(4, "rollback reports it verified against the tree",
          (data.get("rollback") or {}).get("verified") is True,
          str((data.get("rollback") or {}).get("left"))[:60])

    after = json.loads(part_design.execute("catia_list_features", {}))
    check(5, "and the sketch really is gone from the tree",
          not after.get("sketches"),
          f"sketches={[s['name'] for s in after.get('sketches') or []]}")
    check(6, "the tool reads the document the plan left active",
          str(after.get("document") or "") == "PlanRollback.CATPart",
          str(after.get("document")))

    say("")
    total = len(CHECKS)
    passed = sum(1 for _, _, ok, _ in CHECKS if ok)
    say(f"RESULT {passed}/{total}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
