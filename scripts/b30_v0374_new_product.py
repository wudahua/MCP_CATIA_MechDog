"""Does `catia_new_product` answer the same SAVE_FAILED as `catia_new_part`?

The 2026-11-07 report's P0-1 closes with "catia_new_product 是否同样返回 ok:false
尚未测" - and alignment matters, because a product is the document whose missing
path blocks every catia_add_component.

Same two cases as the part probe, plus the ASCII control.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402

ROOT = r"F:\catia_probe_v0374"
ZH_DIR = os.path.join(ROOT, "中文目录")

CASES = [
    ("CJK name, CJK folder", os.path.join(ZH_DIR, "装配体.CATProduct"), False),
    ("ASCII name, CJK folder", os.path.join(ZH_DIR, "asm_ok.CATProduct"), True),
    ("ASCII name, ASCII folder", os.path.join(ROOT, "asm_plain.CATProduct"), True),
]


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    tools = DocumentTools(conn)
    say = lambda line: print(line, flush=True)  # noqa: E731
    before = {str(conn.documents.Item(i).Name) for i in range(1, conn.documents.Count + 1)}
    before_active = str(conn.app.ActiveDocument.Name)
    os.makedirs(ZH_DIR, exist_ok=True)
    failures = 0

    for label, path, should_save in CASES:
        say(f"\n{label}: {path}")
        try:
            reply = tools.execute("catia_new_product",
                                 {"name": "ProbeAsm", "file_path": path})
            say(f"  returned ok: {reply[:160]}")
            saved = os.path.exists(path)
        except Exception as error:  # noqa: BLE001
            code = getattr(error, "code", type(error).__name__)
            stage = getattr(error, "stage", "?")
            modified = getattr(error, "modified", "?")
            say(f"  {code} (stage={stage}, modified={modified}): "
                f"{str(error).splitlines()[0][:170]}")
            saved = os.path.exists(path)
        say(f"  file on disk: {saved}")
        if saved != should_save:
            failures += 1
            say(f"  FAIL expected saved={should_save}")
        if saved:
            try:
                os.remove(path)
            except OSError as error:
                say(f"  (could not delete: {error})")

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
    try:
        os.rmdir(ZH_DIR)
        os.rmdir(ROOT)
    except OSError:
        pass
    say(f"\n{conn.documents.Count} document(s) after")
    say("VERDICT: catia_new_product is "
        + ("MISALIGNED with catia_new_part" if failures else
           "aligned - SAVE_FAILED with the same diagnosis"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
