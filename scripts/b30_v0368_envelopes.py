"""The 2026-11-05 P1/P2/P3 calls, through the real server envelope, on B30.

The unit tests prove the shape offline; this proves CATIA is not somehow reached
before the refusal, and that a failed create_parameter leaves nothing behind.
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.server import CATIAMCPServer

RESULTS: list[tuple[str, bool, str]] = []


def say(message: str) -> None:
    print(message, flush=True)


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    say(f"   {'OK  ' if ok else 'FAIL'} {label}" + (f"  [{detail[:220]}]" if detail else ""))


def main() -> None:
    server = CATIAMCPServer()
    server.connection.connect()
    server.connection.app.DisplayFileAlerts = False

    for index in range(server.connection.documents.Count, 0, -1):
        try:
            server.connection.documents.Item(index).Close()
        except Exception:  # noqa: BLE001
            pass
    say(f"open at start: {server.connection.documents.Count}")

    def call(name: str, arguments: dict) -> dict:
        result = server._execute_tool(name, arguments)
        text = result.content[0].text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # A successful call returns prose, not the envelope.
            return {"ok": True, "message": text}

    say("\n=== a plate, so a refused call has a real model next to it ===")
    call("catia_new_part", {})
    call("catia_create_sketch", {"plane": "xy"})
    call("catia_sketch_centered_rectangle", {"width": 120, "height": 80})
    call("catia_close_sketch", {})
    call("catia_pad", {"height": 12})
    volume_before = call("catia_get_inertia", {}).get("data", {}).get("volume")
    say(f"   volume before = {volume_before}")

    say("\n=== P1: the six refusals that used to answer COM_ERROR/execute ===")
    cases = [
        ("fillet unknown edge", "catia_fillet",
         {"radius": 3, "edge_name": "NO_SUCH_EDGE"}),
        ("circ_pattern count 0", "catia_circ_pattern", {"count": 0}),
        ("shell 0/0", "catia_shell", {"thickness": 0, "external_thickness": 0}),
        ("empty part number", "catia_set_part_number", {"part_number": ""}),
        ("frame language klingon", "catia_drawing_add_frame", {"language": "klingon"}),
        ("frame margin 0", "catia_drawing_add_frame", {"margin": 0}),
    ]
    for label, tool, arguments in cases:
        reply = call(tool, arguments)
        stage = reply.get("data", {}).get("failure_stage")
        modified = reply.get("effects", {}).get("modified")
        has_recovery = "recovery" in reply
        actions = reply.get("data", {}).get("next_actions") or []
        say(f"   {label}: code={reply.get('code')} stage={stage} "
            f"modified={modified} recovery={has_recovery} actions={len(actions)}")
        check(f"P1 {label}: validate + not modified + no recovery",
              stage == "validate" and modified is False and not has_recovery,
              f"{reply.get('code')} / {stage} / {modified} / recovery={has_recovery}")
        check(f"P1 {label}: says what to do next", bool(actions),
              str(actions)[:120])
        check(f"P1 {label}: no model advice",
              "inspect the CATIA model" not in reply.get("message", ""),
              reply.get("message", "")[:100])

    volume_after = call("catia_get_inertia", {}).get("data", {}).get("volume")
    check("P1: the six refusals changed nothing", volume_before == volume_after,
          f"{volume_before} -> {volume_after}")

    say("\n=== P2: a failed create_parameter must not leave an orphan ===")
    before = call("catia_get_parameters", {})
    before_text = json.dumps(before, ensure_ascii=False)
    reply = call("catia_create_parameter",
                 {"name": "LEN366B", "type": "length", "value": "abc"})
    after = call("catia_get_parameters", {})
    after_text = json.dumps(after, ensure_ascii=False)
    say(f"   reply: code={reply.get('code')} stage={reply.get('data', {}).get('failure_stage')}")
    check("P2: refused at validate", reply.get("data", {}).get("failure_stage") == "validate",
          reply.get("message", "")[:150])
    check("P2: no orphan parameter in the model",
          "LEN366B" not in after_text, "still present" if "LEN366B" in after_text else "")
    check("P2: the parameter list did not change", before_text == after_text,
          "" if before_text == after_text else "before != after")

    say("\n=== P3: plan and bare call name the same problem ===")
    plan = call("catia_run_plan",
                {"steps": [{"tool": "catia_get_inertia",
                            "args": {"component": "X"}}]})
    bare = call("catia_get_inertia", {"component": "X"})
    say(f"   plan: code={plan.get('code')} problem_code={plan.get('data', {}).get('problem_code')}")
    say(f"   bare: code={bare.get('code')}")
    check("P3: plan names UNKNOWN_ARGUMENT",
          plan.get("data", {}).get("problem_code") == "UNKNOWN_ARGUMENT",
          str(plan.get("data", {}))[:200])
    check("P3: both refuse before the model", plan.get("effects", {}).get("modified") is False
          and bare.get("effects", {}).get("modified") is False)

    say("\n=== summary ===")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    say(f"   {passed}/{len(RESULTS)} passed")
    for label, ok, detail in RESULTS:
        if not ok:
            say(f"   FAILED: {label} - {detail}")

    for index in range(server.connection.documents.Count, 0, -1):
        try:
            server.connection.documents.Item(index).Close()
        except Exception as error:  # noqa: BLE001
            say(f"   close failed: {str(error).splitlines()[0][:80]}")


if __name__ == "__main__":
    main()
