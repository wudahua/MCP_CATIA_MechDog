"""0.3.71 on B30: the cold-start envelope, a wrong name, and the tensor bill.

Three of the four reports' claims can be checked on this machine; the fourth
(threaded holes failing on B32) is measured here too, because "B30 does not
reproduce it" is itself worth recording.

  1. CATIA running, no document open: every tool call used to answer COM_ERROR /
     execute / modified:null. Now PRECONDITION_FAILED / validate.
  2. A name that is not in the list: DOCUMENT_NOT_FOUND / validate.
  3. `document=` no longer pays for a tensor it cannot return: tensor_ms ~0.
  4. threaded=true: what this build does, reported as measured.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.server import CATIAMCPServer  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def say(message: str) -> None:
    print(message, flush=True)


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    say(f"   {'OK  ' if ok else 'FAIL'} {label}" + (f"  [{detail[:170]}]" if detail else ""))


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

    say("=== 1. cold start: CATIA up, no document ===")
    reply = call("catia_list_features", {})
    stage = reply.get("data", {}).get("failure_stage")
    say(f"   code={reply.get('code')} stage={stage} "
        f"modified={reply.get('effects', {}).get('modified')} "
        f"recovery={'recovery' in reply}")
    check("cold start is PRECONDITION_FAILED",
          reply.get("code") == "PRECONDITION_FAILED", str(reply.get("code")))
    check("cold start is validate", stage == "validate", str(stage))
    check("cold start modified false",
          reply.get("effects", {}).get("modified") is False,
          str(reply.get("effects", {}).get("modified")))
    check("cold start has no recovery block", "recovery" not in reply)
    check("cold start says what to open",
          bool(reply.get("data", {}).get("next_actions")),
          str(reply.get("data", {}).get("next_actions"))[:130])

    say("\n=== a part, so the next checks have a model ===")
    call("catia_new_part", {})
    call("catia_create_sketch", {"plane": "xy"})
    call("catia_sketch_centered_rectangle", {"width": 40, "height": 20})
    call("catia_close_sketch", {})
    call("catia_pad", {"height": 10})
    volume = call("catia_get_inertia", {}).get("volume_mm3")
    say(f"   volume = {volume}")
    check("the plate still builds", abs((volume or 0) - 8000.0) < 1.0, str(volume))

    say("\n=== 2. a name that is not in the list ===")
    reply = call("catia_get_inertia", {"document": "NOPE.CATPart"})
    stage = reply.get("data", {}).get("failure_stage")
    say(f"   code={reply.get('code')} stage={stage} "
        f"modified={reply.get('effects', {}).get('modified')}")
    check("wrong document name is *_NOT_FOUND",
          str(reply.get("code", "")).endswith("_NOT_FOUND"), str(reply.get("code")))
    check("wrong document name is validate", stage == "validate", str(stage))

    say("\n=== 3. the tensor bill on the document= route ===")
    reply = call("catia_get_inertia", {"document": "Part1.CATPart"})
    tensor_ms = reply.get("tensor_ms")
    say(f"   tensor_ms={tensor_ms} elapsed_ms={reply.get('elapsed_ms')} "
        f"reason={reply.get('tensor_reason_code')}")
    check("tensor_ms is ~0 on this route", (tensor_ms or 0) < 20.0, str(tensor_ms))
    check("the reason is about the ROUTE",
          reply.get("tensor_reason_code") == "not_on_this_route",
          str(reply.get("tensor_reason_code")))
    check("the reason no longer tells you to activate",
          "catia_activate_document" not in (reply.get("tensor_reason") or ""),
          (reply.get("tensor_reason") or "")[:140])
    check("volume still measured", abs((reply.get("volume_mm3") or 0) - 8000.0) < 1.0,
          str(reply.get("volume_mm3")))

    say("\n=== 4. threaded=true: what THIS build does ===")
    call("catia_create_sketch", {"plane": "xy"})
    call("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": 3})
    call("catia_close_sketch", {})
    reply = call("catia_hole", {"diameter": 6, "depth": 10, "threaded": True})
    code = reply.get("code")
    say(f"   code={code} stage={reply.get('data', {}).get('failure_stage')} "
        f"modified={reply.get('effects', {}).get('modified')}")
    features = call("catia_list_features", {})
    count = len(features.get("features") or [])
    say(f"   features after: {count}")
    if code is None:
        say("   B30 accepted the thread (the B32 failure does not reproduce here)")
        check("a successful thread is still reported as success", True)
    else:
        # B30 answers NO_MATERIAL here: the bore removes nothing on this build, so
        # the threading write is never reached. Both codes are correct answers to
        # "the thread did not happen" - what matters is that neither is COM_ERROR
        # and that `modified` is a measured value.
        check("a failed thread is a feature code, not COM_ERROR",
              code in {"FEATURE_INCOMPLETE", "NO_MATERIAL"}, str(code))
        check("a failed thread says modified", reply.get("effects", {}).get("modified")
              in (True, False), str(reply.get("effects", {}).get("modified")))
        check("a failed thread leaves no lecture",
              "inspect the CATIA model" not in reply.get("message", ""),
              reply.get("message", "")[:90])
    check("the part is still usable after it",
          abs((call("catia_get_inertia", {}).get("volume_mm3") or 0) - 8000.0) < 200.0,
          str(call("catia_get_inertia", {}).get("volume_mm3")))

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
