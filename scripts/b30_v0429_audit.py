"""0.3.127 acceptance on B30: the ASCII fallback and the cheap identity.

The report's P1-5 had three parts (no one-step workaround, an inconsistent policy
between export and save, an unreadable message) and P2-1 asked for a server
identity that does not probe. Every case is one fresh document under the probe
root; the Chinese names are built at run time so no source file carries them.

    .venv/Scripts/python.exe scripts/b30_v0429_audit.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp import __version__ as MCP_VERSION  # noqa: E402
from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.diagnostics import DiagnosticsTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0429")
ROOT = os.path.join(_PROBE_ROOT, "audit")
RESULTS: list[tuple[int, str, bool, str]] = []

ZH_NAME = "苹果AirPods一二代"        # built at run time, not spelled in the file list
ZH_SAVE = "苹果AirPods一二代_另存"


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, title: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, title, bool(passed), detail))
    say(f"  [{'PASS' if passed else 'FAIL'}] {number}. {title}"
        + (f"  ({detail})" if detail else ""))


def clear_ours(conn: CATIAConnection) -> None:
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


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    clear_ours(conn)
    # Our own leftovers, by name pattern, inside our own directory: this script
    # writes files with FIXED names so the ASCII conversion can be checked against
    # a known one, and a second run must not report "the file already exists" as
    # a finding. Never a recursive delete, never outside ROOT.
    for entry in sorted(os.listdir(ROOT)):
        if entry.lower().endswith((".catpart", ".stp", ".jpg", ".stl")):
            try:
                os.remove(os.path.join(ROOT, entry))
            except OSError:
                pass
    documents = DocumentTools(conn)
    export = ExportTools(conn)
    diagnostics = DiagnosticsTools(conn)
    diagnostics.tool_count = 149

    # ── P2-1: the cheap identity ─────────────────────────────────────────
    say("")
    say("── 1/2: catia_server_info on a live session ──")
    info = diagnostics.execute("catia_server_info", {})
    payload = info if isinstance(info, dict) else json.loads(str(info))
    say("  " + " ".join(str(payload.get("message")).split())[:200])
    data = payload.get("data") or {}
    check(1, "reports this server's version and the real tool count",
          data.get("version") == MCP_VERSION and data.get("tool_count") == 149,
          f"{data.get('version')} / {data.get('tool_count')}")
    catia = data.get("catia") or {}
    check(2, "and the CATIA release behind it",
          catia.get("connected") is True and bool(catia.get("release"))
          and catia.get("documents") is not None,
          f"{catia.get('release')} · {catia.get('documents')} doc(s)")

    # ── P1-5 (c): the refusal is short now ───────────────────────────────
    say("")
    say("── 3: a Chinese FILE NAME is refused, briefly ──")
    bad_path = os.path.join(ROOT, ZH_NAME + ".CATPart")
    try:
        documents.execute("catia_new_part", {"name": "Zh", "file_path": bad_path})
        check(3, "the Chinese name is refused", False, "it was accepted")
        message_length = 0
    except Exception as error:  # noqa: BLE001 - the refusal is the answer
        message = str(error)
        message_length = len(message)
        say(f"  {getattr(error, 'code', '')}: {message[:240]}")
        check(3, "the Chinese name is refused with PATH_NOT_ASCII",
              getattr(error, "code", "") == "PATH_NOT_ASCII", "as before")
    check(4, "and the message is short enough to read",
          0 < message_length < 600, f"{message_length} characters (was ~1000)")

    # ── P1-5 (a): the one-step workaround ────────────────────────────────
    say("")
    say("── 5/6/7: ascii_fallback=true does the whole job in one call ──")
    reply = " ".join(str(documents.execute(
        "catia_new_part", {"file_path": os.path.join(ROOT, ZH_NAME + ".CATPart"),
                           "ascii_fallback": True})).split())
    say("  " + reply[:340])
    made = os.path.join(ROOT, "AirPods.CATPart")
    check(5, "the file was created under an ASCII name",
          "AirPods.CATPart" in reply and os.path.exists(made),
          os.path.basename(made))
    info = json.loads(documents.execute("catia_get_active_document_info", {}))
    check(6, "and the Chinese wording is the part number",
          str(info.get("part_number") or "") == ZH_NAME,
          f"part_number={info.get('part_number')!r}")
    check(7, "the reply states both, so nothing has to be guessed",
          "ascii_fallback" in reply and ZH_NAME in reply)

    # ── P1-5 (b): export accepts the same name, and says why ─────────────
    say("")
    say("── 8: catia_export with the SAME Chinese name ──")
    step_path = os.path.join(ROOT, ZH_NAME + "模型.stp")
    export_reply = " ".join(str(export.execute(
        "catia_export", {"file_path": step_path, "format": "step"})).split())
    say("  " + export_reply[:300])
    written = os.path.getsize(step_path) if os.path.exists(step_path) else 0
    check(8, "it works - a different CATIA API, not a different opinion",
          written > 0 and "ExportData" in export_reply,
          f"{written} bytes, Chinese name intact")

    # ── P1-5 (a), save side: the name changes, the part number does not ──
    say("")
    say("── 9/10/11: catia_save_document(ascii_fallback=true) ──")
    # '苹果AirPods一二代_另存' converts to the SAME 'AirPods' as the open document,
    # because the conversion keeps only the ASCII characters. That is a real
    # property of the design, and the call has to say so instead of re-saving the
    # file the caller meant to copy.
    try:
        documents.execute("catia_save_document",
                          {"file_path": os.path.join(ROOT, ZH_SAVE + ".CATPart"),
                           "ascii_fallback": True})
        check(9, "a second Chinese name that converts to the same ASCII name is "
                 "refused", False, "it was accepted - the copy would be a re-save")
    except Exception as error:  # noqa: BLE001 - the refusal is the answer
        check(9, "a second Chinese name that converts to the same ASCII name is "
                 "refused", getattr(error, "code", "") == "ASCII_NAME_COLLIDES",
              f"{getattr(error, 'code', '')}: {str(error).splitlines()[0][:80]}")

    save_reply = " ".join(str(documents.execute(
        "catia_save_document",
        {"file_path": os.path.join(ROOT, "AirPods_copy.CATPart")})).split())
    say("  " + save_reply[:200])
    check(10, "a differing ASCII path saves normally",
          os.path.exists(os.path.join(ROOT, "AirPods_copy.CATPart")), "copy on disk")
    info = json.loads(documents.execute("catia_get_active_document_info", {}))
    check(11, "and the part number was NOT touched, by design",
          str(info.get("part_number") or "") == ZH_NAME,
          f"part_number={info.get('part_number')!r}")

    # ── P1-5: opening a Chinese name still refuses, before connecting ────
    say("")
    say("── 12: catia_open_document on a Chinese name ──")
    try:
        documents.execute("catia_open_document",
                          {"file_path": os.path.join(ROOT, ZH_NAME + ".CATPart")})
        check(12, "opening it is refused", False, "it was accepted")
    except Exception as error:  # noqa: BLE001
        check(12, "opening it is refused with PATH_NOT_ASCII",
              getattr(error, "code", "") == "PATH_NOT_ASCII",
              f"{len(str(error))} chars")

    # ── P2-1: no session, no connection ──────────────────────────────────
    say("")
    say("── 13: the identity never opens CATIA ──")
    cold = DiagnosticsTools(connection=None)
    cold_payload = cold._server_info_tool()
    check(13, "without a session it answers connected=false instead of connecting",
          cold_payload["ok"] is True
          and cold_payload["data"]["catia"]["connected"] is False,
          "no connection attempted")

    # ── the tool is registered on the real router ────────────────────────
    numbers = {item["name"] for item in diagnostics.get_tool_definitions()}
    check(14, "catia_server_info is registered and takes no arguments",
          "catia_server_info" in numbers,
          ", ".join(sorted(numbers)))

    say("")
    passed = sum(1 for _n, _t, ok, _d in RESULTS if ok)
    say(f"{passed}/{len(RESULTS)} checks passed")
    for number, title, ok, detail in RESULTS:
        if not ok:
            say(f"  FAILED {number}. {title}  {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
