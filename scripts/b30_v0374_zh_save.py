"""The reporter's P0-1, step by step, through THIS package (not raw COM).

The 2026-11-06 report has three steps that must be told apart:

  1. catia_new_part(file_path="<zh folder>/<zh name>.CATPart")  -> save fails
  2. catia_save_document(file_path="<zh folder>/ASCII.CATPart") -> the report says
     this fails too, and concluded "the folder counts, not only the file name"
  3. the same with a fully ASCII path -> works

Step 2 is the claim under test. Raw COM says a Chinese DIRECTORY is fine
(scripts/b30_v0374_paths.py), so if step 2 fails here it is this package's
FileSystem route, not CATIA's SaveAs.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402

ROOT = r"F:\catia_probe_v0374"
ZH_DIR = os.path.join(ROOT, "中文目录")


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    tools = DocumentTools(conn)
    os.makedirs(ZH_DIR, exist_ok=True)
    say = lambda line: print(line, flush=True)  # noqa: E731
    mine: list[str] = []
    failures = 0

    def close_mine() -> None:
        for index in range(conn.documents.Count, 0, -1):
            doc = conn.documents.Item(index)
            try:
                if str(doc.Name) in mine:
                    doc.Close()
            except Exception:  # noqa: BLE001
                pass

    say(f"{conn.documents.Count} document(s) open before; touching only new ones")

    # --- step 1: Chinese file name in a Chinese folder -------------------
    zh_path = os.path.join(ZH_DIR, "探针.CATPart")
    try:
        reply = tools.execute("catia_new_part", {"name": "Probe1", "file_path": zh_path})
        say(f"step 1 (zh name, zh dir): RETURNED {reply[:140]}")
        failures += 1
        say("   FAIL - a non-ASCII file name was accepted silently")
    except Exception as error:  # noqa: BLE001
        code = getattr(error, "code", type(error).__name__)
        say(f"step 1 (zh name, zh dir): {code}")
        say(f"   {str(error).splitlines()[0][:170]}")
        say(f"   file on disk: {os.path.exists(zh_path)}")
    for index in range(1, conn.documents.Count + 1):
        name = str(conn.documents.Item(index).Name)
        if name.startswith(("Probe1", "Part")) and name not in mine:
            mine.append(name)

    # --- step 2: the same folder, ASCII file name ------------------------
    ascii_in_zh = os.path.join(ZH_DIR, "probe_ascii.CATPart")
    try:
        reply = tools.execute("catia_save_document",
                              {"file_path": ascii_in_zh, "overwrite": True})
        say(f"step 2 (ascii name, zh dir): {reply[:140]}")
        ok = os.path.exists(ascii_in_zh)
        say(f"   file on disk: {ok}")
        if not ok:
            failures += 1
    except Exception as error:  # noqa: BLE001
        code = getattr(error, "code", type(error).__name__)
        say(f"step 2 (ascii name, zh dir): {code} - {str(error).splitlines()[0][:150]}")
        failures += 1

    # --- step 3: control, all ASCII --------------------------------------
    ascii_path = os.path.join(ROOT, "probe_plain.CATPart")
    try:
        reply = tools.execute("catia_save_document",
                              {"file_path": ascii_path, "overwrite": True})
        say(f"step 3 (ascii name, ascii dir): {reply[:140]}")
    except Exception as error:  # noqa: BLE001
        say(f"step 3 (ascii name, ascii dir): {getattr(error, 'code', '?')} - "
            f"{str(error).splitlines()[0][:150]}")
        failures += 1

    close_mine()
    for path in (zh_path, ascii_in_zh, ascii_path):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
    try:
        os.rmdir(ZH_DIR)
        os.rmdir(ROOT)
    except OSError:
        pass
    say("")
    say("VERDICT: a non-ASCII DIRECTORY is "
        + ("refused by this package" if failures else "fine; only the file NAME is refused"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
