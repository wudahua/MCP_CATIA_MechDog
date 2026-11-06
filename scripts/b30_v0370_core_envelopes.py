"""0.3.70 on B30: the core-layer refusals through the real envelope.

Three things only CATIA can answer:

  * a Part is not an assembly - `catia_list_components` on a Part now says
    PRECONDITION_FAILED / validate instead of COM_ERROR;
  * a Drawing document has no Part behind it - a Part Design tool asked for one
    says the same, instead of "method failed";
  * an internal defect (injected here) carries stage `internal`, `modified:false`
    and no recovery block - and the ordinary path still builds geometry.
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.results import InternalError  # noqa: E402
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

    def envelope(label: str, reply: dict, code: str, stage: str) -> None:
        got_code = reply.get("code")
        got_stage = reply.get("data", {}).get("failure_stage")
        modified = reply.get("effects", {}).get("modified")
        actions = reply.get("data", {}).get("next_actions") or []
        say(f"   {label}: code={got_code} stage={got_stage} modified={modified} "
            f"recovery={'recovery' in reply} actions={len(actions)}")
        check(f"{label}: code {code}", got_code == code, str(got_code))
        check(f"{label}: stage {stage}", got_stage == stage, str(got_stage))
        check(f"{label}: modified false", modified is False, str(modified))
        check(f"{label}: no recovery", "recovery" not in reply)
        check(f"{label}: says what next", bool(actions), str(actions)[:120])
        check(f"{label}: no 'inspect the CATIA model'",
              "inspect the CATIA model" not in reply.get("message", ""),
              reply.get("message", "")[:80])

    say("=== a Part is not an assembly ===")
    call("catia_new_part", {})
    envelope("list_components on a Part",
             call("catia_list_components", {}),
             "PRECONDITION_FAILED", "validate")

    say("\n=== a Drawing has no Part behind it ===")
    call("catia_new_drawing", {})
    envelope("catia_pad while a drawing is active",
             call("catia_pad", {"height": 5}),
             "PRECONDITION_FAILED", "validate")

    say("\n=== an internal defect says so, and asks for a report ===")
    class _Boom:
        def get_tool_definitions(self):
            return [{"name": "catia_probe_internal",
                     "description": "raises a server defect",
                     "inputSchema": {"type": "object", "properties": {}}}]

        def execute(self, tool_name, arguments):
            raise InternalError("Duplicate tool: catia_probe_internal")

    server._tool_router["catia_probe_internal"] = _Boom()
    server._schemas["catia_probe_internal"] = {"type": "object", "properties": {}}
    envelope("injected internal defect",
             call("catia_probe_internal", {}),
             "INTERNAL_ERROR", "internal")

    say("\n=== the ordinary path still works ===")
    part_name = None
    for index in range(1, server.connection.documents.Count + 1):
        name = str(server.connection.documents.Item(index).Name)
        if name.endswith(".CATPart"):
            part_name = name
    say(f"   part document: {part_name}")
    call("catia_activate_document", {"document": part_name})
    call("catia_create_sketch", {"plane": "xy"})
    call("catia_sketch_centered_rectangle", {"width": 40, "height": 20})
    call("catia_close_sketch", {})
    pad = call("catia_pad", {"height": 8})
    check("a pad still builds", pad.get("ok") is not False,
          pad.get("message", "")[:120])
    volume = call("catia_get_inertia", {}).get("volume_mm3")
    say(f"   volume = {volume}")
    check("the volume is 6400 mm3", abs((volume or 0) - 6400.0) < 1.0, str(volume))

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
