"""Replay the 2026-11-05 acceptance report against the current build.

The report was written against 0.3.66: P1 (the refusal envelope regressed in the
new modules), P2 (a failed create_parameter left value=0 behind) and P3 (plan and
bare call named the same mistake differently). 0.3.68 fixed the cases it listed;
0.3.69 converted every remaining raise site.

This script answers with the report's OWN calls, one per row of its tables, so
the closure is checkable rather than claimed. Run it on B30.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.server import CATIAMCPServer

RESULTS: list[tuple[str, bool, str]] = []


def say(message: str) -> None:
    print(message, flush=True)


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    say(f"   {'OK  ' if ok else 'FAIL'} {label}" + (f"  [{detail[:180]}]" if detail else ""))


def main() -> None:
    server = CATIAMCPServer()
    server.connection.connect()
    server.connection.app.DisplayFileAlerts = False
    for index in range(server.connection.documents.Count, 0, -1):
        try:
            server.connection.documents.Item(index).Close()
        except Exception:  # noqa: BLE001
            pass

    def call(name: str, arguments: dict) -> dict:
        text = server._execute_tool(name, arguments).content[0].text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"ok": True, "message": text}

    def assert_validate(label: str, reply: dict, allowed: set[str]) -> None:
        code = reply.get("code")
        stage = reply.get("data", {}).get("failure_stage")
        modified = reply.get("effects", {}).get("modified")
        message = reply.get("message", "")
        actions = reply.get("data", {}).get("next_actions") or []
        say(f"   {label}: code={code} stage={stage} modified={modified} "
            f"recovery={'recovery' in reply} actions={len(actions)}")
        check(f"{label}: code in {sorted(allowed)}", code in allowed, str(code))
        check(f"{label}: validate", stage == "validate", str(stage))
        check(f"{label}: modified is false", modified is False, str(modified))
        check(f"{label}: no recovery block", "recovery" not in reply)
        check(f"{label}: no lecture", "inspect the CATIA model" not in message,
              message[:80])
        check(f"{label}: says what to fix", bool(actions), str(actions)[:120])

    say("=== the model the report used: a 30 x 16 x 12 box ===")
    call("catia_new_part", {})
    call("catia_create_sketch", {"plane": "xy"})
    call("catia_sketch_centered_rectangle", {"width": 30, "height": 16})
    call("catia_close_sketch", {})
    call("catia_pad", {"height": 12})
    part_doc = str(server.connection.active_document.Name)
    volume_before = call("catia_get_inertia", {}).get("volume_mm3")
    say(f"   volume = {volume_before}, document = {part_doc}")

    say("\n=== §2.1 the one row 0.3.43's own fix table named ===")
    assert_validate("fillet NO_SUCH_EDGE",
                    call("catia_fillet", {"radius": 3, "edge_name": "NO_SUCH_EDGE"}),
                    {"ARGUMENT_REJECTED"})

    say("\n=== §2.3 the eight cross-module refusals it spot-checked ===")
    spot = [
        ("circ_pattern count 0", "catia_circ_pattern", {"count": 0},
         {"ARGUMENT_REJECTED", "INVALID_ARGUMENT"}),
        ("shell thickness 0 + external 0", "catia_shell",
         {"thickness": 0, "external_thickness": 0},
         {"ARGUMENT_REJECTED", "INVALID_ARGUMENT"}),
        ("fillet edge_anchors not a list", "catia_fillet",
         {"radius": 1, "edge_anchors": "notalist"},
         {"ARGUMENT_REJECTED", "INVALID_ARGUMENT"}),
        ("set_part_number empty", "catia_set_part_number", {"part_number": ""},
         {"ARGUMENT_REJECTED", "INVALID_ARGUMENT"}),
        ("shell thickness -1", "catia_shell", {"thickness": -1},
         {"ARGUMENT_REJECTED", "INVALID_ARGUMENT"}),
        ("create_sketch plane nope", "catia_create_sketch", {"plane": "nope"},
         {"ARGUMENT_REJECTED", "INVALID_ARGUMENT"}),
        ("create_parameter type nope", "catia_create_parameter",
         {"name": "P", "type": "nope", "value": 1},
         {"ARGUMENT_REJECTED", "INVALID_ARGUMENT"}),
    ]
    for label, tool, arguments, allowed in spot:
        assert_validate(label, call(tool, arguments), allowed)

    say("\n=== §2.2 the four Drafting refusals, and the sheet they must not touch ===")
    call("catia_new_drawing", {})
    call("catia_drawing_set_sheet", {"paper": "A3", "orientation": "landscape"})
    sheet = server.connection.active_document.Sheets.ActiveSheet
    background = sheet.Views.Item(2)

    def snapshot() -> tuple[int, int]:
        return (background.GeometricElements.Count, len(background.Tables))

    frames = [
        ("language klingon", {"language": "klingon"}),
        ("labels nope", {"labels": {"nope": "x"}}),
        ("title block 9999", {"title_block": {"width": 9999}}),
        ("margin 0", {"margin": 0}),
    ]
    for label, arguments in frames:
        before = snapshot()
        reply = call("catia_drawing_add_frame", arguments)
        after = snapshot()
        assert_validate(f"add_frame {label}", reply,
                        {"ARGUMENT_REJECTED", "INVALID_ARGUMENT"})
        check(f"add_frame {label}: sheet untouched", before == after,
              f"{before} -> {after}")

    say("\n=== §3 P2: a failed create_parameter must leave nothing behind ===")
    # The Drafting block above made the drawing active; the report's P2 runs on a
    # Part, so bring it back - a refusal about the wrong document type would
    # look like the defect being tested.
    call("catia_activate_document", {"document": part_doc})
    before_params = json.dumps(call("catia_get_parameters", {}), ensure_ascii=False,
                               sort_keys=True)
    reply = call("catia_create_parameter",
                 {"name": "LEN366B", "type": "length", "value": "abc"})
    after_params = json.dumps(call("catia_get_parameters", {}), ensure_ascii=False,
                              sort_keys=True)
    assert_validate("create_parameter value abc", reply, {"ARGUMENT_REJECTED"})
    check("no orphan LEN366B", "LEN366B" not in after_params)
    check("parameter list unchanged", before_params == after_params)

    say("\n=== §4 P3: plan and bare call name the same mistake ===")
    plan = call("catia_run_plan",
                {"steps": [{"tool": "catia_get_inertia",
                            "args": {"component": "X"}}]})
    bare = call("catia_get_inertia", {"component": "X"})
    say(f"   plan: code={plan.get('code')} "
        f"problem_code={plan.get('data', {}).get('problem_code')}")
    say(f"   bare: code={bare.get('code')}")
    check("plan keeps INVALID_PLAN", plan.get("code") == "INVALID_PLAN",
          str(plan.get("code")))
    check("plan carries the bare call's code",
          plan.get("data", {}).get("problem_code") == bare.get("code"),
          f"{plan.get('data', {}).get('problem_code')} vs {bare.get('code')}")

    say("\n=== nothing the report called a refusal moved the model ===")
    volume_after = call("catia_get_inertia", {}).get("volume_mm3")
    check("volume unchanged by every refusal", volume_before == volume_after,
          f"{volume_before} -> {volume_after}")

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
