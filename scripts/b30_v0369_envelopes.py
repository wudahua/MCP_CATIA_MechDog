"""0.3.69 on B30: the three refusal classes through the real envelope.

The unit tests pin the shape offline; this answers three things only CATIA can:

  * a refusal still never reaches the model (volume unchanged, no doc created);
  * the stage each class produces is the one the envelope reads;
  * the ordinary path still works after 168 raise sites changed class - a pad
    built the same way as before, and a GSD element still created.
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
    say(f"   {'OK  ' if ok else 'FAIL'} {label}" + (f"  [{detail[:200]}]" if detail else ""))


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
            return {"ok": True, "message": text}

    say("\n=== a plate, so every refusal has a real model beside it ===")
    call("catia_new_part", {})
    call("catia_create_sketch", {"plane": "xy"})
    call("catia_sketch_centered_rectangle", {"width": 120, "height": 80})
    call("catia_close_sketch", {})
    call("catia_pad", {"height": 12})
    volume_before = call("catia_get_inertia", {}).get("volume_mm3")
    say(f"   volume before = {volume_before}")

    say("\n=== the three classes, and what each one answers ===")
    cases = [
        # label, tool, args, expected code, expected stage
        ("argument: thickness without face_name", "catia_thickness",
         {"offset": 1}, "ARGUMENT_REJECTED", "validate"),
        ("argument: unknown plane", "catia_mirror",
         {"plane": "nope"}, "INVALID_ARGUMENT", "validate"),
        ("precondition: no drawing open", "catia_drawing_add_frame",
         {"paper": "A3"}, "PRECONDITION_FAILED", "validate"),
        ("precondition: no sketch open", "catia_sketch_line",
         {"x1": 0, "y1": 0, "x2": 10, "y2": 0}, "PRECONDITION_FAILED", "validate"),
        ("out of range: feature 99", "catia_delete_feature",
         {"index": 99}, "ARGUMENT_OUT_OF_RANGE", "validate"),
    ]
    for label, tool, arguments, code, stage in cases:
        reply = call(tool, arguments)
        got_code = reply.get("code")
        got_stage = reply.get("data", {}).get("failure_stage")
        modified = reply.get("effects", {}).get("modified")
        has_recovery = "recovery" in reply
        actions = reply.get("data", {}).get("next_actions") or []
        say(f"   {label}: code={got_code} stage={got_stage} modified={modified} "
            f"recovery={has_recovery} actions={len(actions)}")
        check(f"{label}: code is {code}", got_code == code, got_code or "")
        check(f"{label}: stage is {stage}", got_stage == stage, str(got_stage))
        check(f"{label}: nothing modified", modified is False, str(modified))
        check(f"{label}: no recovery block", not has_recovery)
        check(f"{label}: says what to fix", bool(actions), str(actions)[:140])
        check(f"{label}: no 'inspect the CATIA model'",
              "inspect the CATIA model" not in reply.get("message", ""),
              reply.get("message", "")[:90])

    volume_after = call("catia_get_inertia", {}).get("volume_mm3")
    check("the five refusals changed nothing", volume_before == volume_after,
          f"{volume_before} -> {volume_after}")
    check("no document was created by a refusal",
          server.connection.documents.Count == 1,
          f"documents={server.connection.documents.Count}")

    say("\n=== the ordinary path still works (168 raises changed class) ===")
    reply = call("catia_create_sketch", {"plane": "xy"})
    check("a new sketch still opens", reply.get("ok") is not False,
          reply.get("message", "")[:120])
    call("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": 10})
    call("catia_close_sketch", {})
    padded = call("catia_pad", {"height": 20})
    check("a second pad still builds", padded.get("ok") is not False,
          padded.get("message", "")[:140])
    volume2 = call("catia_get_inertia", {}).get("volume_mm3")
    say(f"   volume now = {volume2}")
    check("the volume grew", isinstance(volume2, (int, float))
          and isinstance(volume_before, (int, float)) and volume2 > volume_before,
          f"{volume_before} -> {volume2}")

    say("\n=== a GSD element, to prove the sweep reached that module too ===")
    reply = call("catia_gsd_point", {"name": "P369", "x": 0, "y": 0, "z": 50})
    check("catia_gsd_point still creates", reply.get("ok") is not False,
          reply.get("message", "")[:140])
    reply = call("catia_gsd_translate", {"element": "P369",
                                         "direction": [0, 0, 0],
                                         "distance": 5})
    stage = reply.get("data", {}).get("failure_stage")
    say(f"   zero direction: code={reply.get('code')} stage={stage} "
        f"modified={reply.get('effects', {}).get('modified')}")
    check("a zero direction is a validate-stage refusal, not a COM_ERROR",
          reply.get("code") == "ARGUMENT_REJECTED" and stage == "validate",
          f"{reply.get('code')} / {stage}")

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
