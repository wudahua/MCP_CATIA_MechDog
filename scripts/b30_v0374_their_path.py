"""The reporter's evidence B, reproduced EXACTLY - `catia_new_part`, their path.

They report (2026-11-07, second revision): a Chinese DIRECTORY with a pure ASCII
file name makes catia_new_part answer SAVE_FAILED on 0.3.73, "clean comparison,
no dialog". That contradicts two measurements made here the same day:

  scripts/b30_v0374_paths.py  raw SaveAs, Chinese dir + ASCII name -> SAVED
  scripts/b30_v0374_zh_save.py  catia_save_document, same combination -> SAVED

The difference between those and their case is the CALL (new_part vs
save_document) and the PATH itself, so this probe varies both:

  (a) their literal path, typo and all: F:\\WokrBuddy Catia Space\\文本建模-…\\
  (b) a short Chinese folder under this workspace
  (c) an ASCII control

Each part is closed and its file deleted afterwards.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402

CASES = [
    ("their path (typo + CJK folder)",
     r"F:\WokrBuddy Catia Space\文本建模-简易机械爪夹具结构\probe_ascii.CATPart"),
    ("short CJK folder",
     r"F:\WorkBuddy Space\2026-10-20-13-31-14\中文测试目录\probe_ascii.CATPart"),
    ("ascii control", r"F:\catia_probe_v0374\probe_ascii.CATPart"),
]


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    tools = DocumentTools(conn)
    say = lambda line: print(line, flush=True)  # noqa: E731
    before = {str(conn.documents.Item(i).Name) for i in range(1, conn.documents.Count + 1)}
    before_active = str(conn.app.ActiveDocument.Name)
    say(f"{conn.documents.Count} document(s) open, active '{before_active}'")

    for label, path in CASES:
        say(f"\n{label}\n  {path}")
        try:
            reply = tools.execute("catia_new_part",
                                  {"name": "probe_ascii", "file_path": path})
            say(f"  OK   {reply[:150]}")
        except Exception as error:  # noqa: BLE001
            code = getattr(error, "code", type(error).__name__)
            say(f"  FAIL {code}: {str(error).splitlines()[0][:200]}")
        say(f"  file on disk: {os.path.exists(path)}")
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError as error:
                say(f"  (could not delete: {error})")
        # Delete the folders this probe may have created, shallowest last.
        folder = os.path.dirname(path)
        try:
            os.rmdir(folder)
        except OSError:
            pass

    for index in range(conn.documents.Count, 0, -1):
        doc = conn.documents.Item(index)
        try:
            if str(doc.Name) not in before:
                doc.Close()
        except Exception as error:  # noqa: BLE001
            say(f"   (could not close {doc.Name}: {error})")
    for index in range(1, conn.documents.Count + 1):
        if str(conn.documents.Item(index).Name) == before_active:
            conn.documents.Item(index).Activate()
            break
    say(f"\n{conn.documents.Count} document(s) after, active "
        f"'{getattr(conn.app.ActiveDocument, 'Name', '?')}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
