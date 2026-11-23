"""B30 probe for 0.3.161 - the rollback that froze CATIA behind a modal dialog.

The report's P0-3: a plan failed while one of its sketches was still open in the
editor, rollback=true deleted what the plan had built, and CATIA put up
「剪切数据时发生错误 / 无法删除正在编辑的草图」 - a modal box that no automation
can dismiss, so the session hung until a human clicked OK.

  A  the plan fails, the reply is a failure, and the call RETURNS (no hang)
  B  the open sketch was closed first, and the reply says which one
  C  everything the plan built is gone, including the frame scaffolding
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
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.planner import PlannerTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

results: list[tuple[str, bool, str]] = []


def say(text: str) -> None:
    print(text, flush=True)


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    say(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  [{detail}]" if detail else ""))


def payload(reply):
    return reply if isinstance(reply, (dict, list)) else json.loads(str(reply))


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sk = SketcherTools(conn)
    design = PartDesignTools(conn)
    planner = PlannerTools(conn)
    planner.bind({
        "catia_create_sketch": sk, "catia_sketch_rectangle": sk,
        "catia_sketch_profile": sk, "catia_close_sketch": sk,
        "catia_delete_feature": design, "catia_pad": design,
        "catia_activate_document": docs, "catia_save_document": docs,
    })

    root = os.path.join(tempfile.gettempdir(), "p0498")
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "RB0498.CATPart")
    try:
        docs.execute("catia_close_document", {"document": path, "save": False})
    except Exception:  # noqa: BLE001
        pass
    if os.path.isfile(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": "RB0498", "file_path": path})
    say(f"  part {conn.active_document.Name}")

    steps = [
        {"tool": "catia_create_sketch", "args": {"plane": "xy", "name": "SK_RB"}},
        {"tool": "catia_sketch_rectangle",
         "args": {"x1": 0, "y1": 0, "x2": 40, "y2": 30}},
        {"tool": "catia_close_sketch", "args": {}},
        {"tool": "catia_create_sketch",
         "args": {"plane": {"type": "frame", "origin": [0, 0, 50],
                            "u": [1, 0, 0], "v": [0, 1, 0]},
                  "name": "SK_FR"}},
        {"tool": "catia_sketch_rectangle",
         "args": {"x1": 0, "y1": 0, "x2": 20, "y2": 20}},
        # The report's failing step: an arc whose ends are longer apart than its
        # diameter. It fails at validate, with the frame sketch still OPEN.
        {"tool": "catia_sketch_profile",
         "args": {"segments": [{"type": "arc", "center": [0, 0], "radius": 10,
                                "p1": [-12, 0], "p2": [12, 0]}]}},
    ]

    say("\n== A/B run_plan(rollback=true) failing with 'SK_FR' open")
    say(f"  open sketch before: {getattr(conn, 'open_sketch', None)}")
    # catia_run_plan ANSWERS its failure as an envelope (ok:false), it does not
    # raise - so the check is on the dict, not on an exception.
    reply = payload(planner.execute("catia_run_plan",
                                    {"steps": steps, "rollback": True}))
    say(f"  ok={reply.get('ok')} code={reply.get('code')}")
    message = str(reply.get("message") or "")
    say("  " + message[:700])
    check("A the plan is a failure", reply.get("ok") is False, str(reply.get("code")))
    check("B the reply says the open sketch was closed, and which",
          "SK_FR" in message and "CLOSED before the rollback" in message,
          message[:200])

    data = reply.get("data") or {}
    rollback = data.get("rollback") or {}
    say(f"  rollback: {json.dumps(rollback, ensure_ascii=False)[:400]}")
    check("B the rollback report names the closed sketch",
          rollback.get("open_sketch_closed") == "SK_FR",
          str(rollback.get("open_sketch_closed")))
    check("B the plan says what became of its own work",
          data.get("created_features_status") in ("rolled_back", "nothing_created"),
          str(data.get("created_features_status")))
    check("B and nothing is left open now",
          getattr(conn, "open_sketch", None) is None,
          str(getattr(conn, "open_sketch", None)))

    names = (planner._snapshot_names() or {}).get("names") or []
    say(f"  tree after: {names}")
    left = [name for name in names if name.startswith("SK_")]
    check("C both sketches are gone", left == [], str(left))
    frame = [name for name in names if "FRAME" in name.upper()]
    check("C the frame scaffolding went with its sketch", frame == [], str(frame))

    say("")
    passed = sum(1 for _n, ok, _d in results if ok)
    say(f"{passed}/{len(results)} checks passed")
    for name, ok, detail in results:
        if not ok:
            say(f"  FAILED: {name}  [{detail}]")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
