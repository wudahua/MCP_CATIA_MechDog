"""0.3.84 probe 4: the exact path shape of the P0 report.

Probe 3 saved into 'F:\\catia_probe_v0384\\中文目录' with three documents already
open and it worked, so "Chinese folder + an open document" is not sufficient.
The report's own path has one more property this probe had not reproduced: a
SPACE in the folder name ('F:/WokrBuddy Catia Space/饮料瓶瓶底曲面建模').

Four combinations, in one session, each on a fresh part:
  1  no-spaces ASCII folder            (control)
  2  spaces, ASCII folder              (isolates the space)
  3  no-spaces Chinese folder          (isolates the Chinese, already OK)
  4  spaces + Chinese, the report's shape
Then the same folder through catia_save_document on a document that ALREADY has
a path - the call the report says works. That comparison is the whole point: if
4 fails and its save_document succeeds, the difference is first-save vs
later-save, not the path.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402

ROOT = r"F:\catia_probe_v0384"


def say(line: str = "") -> None:
    print(line, flush=True)


def try_new_part(document, label, folder, name):
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, f"{name}.CATPart")
    if os.path.isfile(target):
        os.remove(target)
    try:
        reply = document.execute("catia_new_part",
                                 {"name": name, "file_path": target})
        say(f"  {label}: OK  " + str(reply).splitlines()[0][-120:])
        return True
    except Exception as error:  # noqa: BLE001 - the refusal is the measurement
        say(f"  {label}: REFUSED code={getattr(error, 'code', '')}")
        for line in str(error).splitlines():
            say("      | " + line[:260])
        return False


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)

    own_root = os.path.normcase(os.path.abspath(ROOT))
    for index in range(conn.documents.Count, 0, -1):
        try:
            stale = conn.documents.Item(index)
            full = os.path.normcase(os.path.abspath(str(stale.FullName)))
        except Exception:  # noqa: BLE001
            continue
        if full.startswith(own_root):
            say(f"closing a document left by an earlier run: {stale.Name}")
            stale.Close()

    say(f"documents open before: {conn.documents.Count}")
    cases = [
        ("1 plain ASCII folder", os.path.join(ROOT, "plain"), "PRB84A"),
        ("2 spaces in ASCII folder", os.path.join(ROOT, "Probe Space"), "PRB84B"),
        ("3 Chinese, no spaces", os.path.join(ROOT, "中文目录二"), "PRB84C"),
        ("4 spaces + Chinese", os.path.join(ROOT, "Probe Space", "饮料瓶瓶底曲面建模"),
         "PRB84D"),
    ]
    say("")
    say("=== catia_new_part, four path shapes, one session ===")
    for label, folder, name in cases:
        try_new_part(document, label, folder, name)
        say(f"      (documents open: {conn.documents.Count})")

    say("")
    say("=== the report's control: save_document on a part that ALREADY has a path ===")
    start = os.path.join(ROOT, "plain", "PRB84E.CATPart")
    if os.path.isfile(start):
        os.remove(start)
    try:
        document.execute("catia_new_part",
                         {"name": "PRB84E", "file_path": start})
        say("  created at an ASCII path first: OK")
    except Exception as error:  # noqa: BLE001
        say(f"  could not create the control part: {error}")
        return
    again = os.path.join(ROOT, "Probe Space", "饮料瓶瓶底曲面建模", "PRB84E.CATPart")
    if os.path.isfile(again):
        os.remove(again)
    try:
        reply = document.execute("catia_save_document",
                                 {"file_path": again, "overwrite": True})
        say("  save_document into the same folder: OK  "
            + str(reply).splitlines()[0][:160])
    except Exception as error:  # noqa: BLE001
        say(f"  save_document into the same folder: REFUSED "
            f"code={getattr(error, 'code', '')}")
        for line in str(error).splitlines():
            say("      | " + line[:260])


if __name__ == "__main__":
    main()
