"""Can a non-ASCII file NAME be reached at all - by copy, and then by Save?

CATIA refuses a first SaveAs when the file NAME is non-ASCII (`PathNotSupported`,
measured repeatedly). The open question is whether the refusal is about SaveAs
only, or about the name everywhere:

  1. `catia_save_as_copy` to a Chinese-named target - CATIA's FileSystem.CopyFile
     first, a plain Python copy as fallback. Does the file appear?
  2. `Documents.Open` on that Chinese-named file - does CATIA read it back?
  3. `doc.Save()` after editing it - the file exists, so this is an overwrite,
     not a first save. Does it work?

If all three hold, "save ASCII, copy to the Chinese name, reopen" is a real
workaround instead of "rename and never save again", which is what the failure
message says today.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.knowledge import KnowledgeTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0374"
ZH_DIR = os.path.join(ROOT, "中文目录")
RESULT: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULT.append((label, ok, detail))
    print(f"   {'OK  ' if ok else 'FAIL'} {label}" + (f"  [{detail[:200]}]" if detail else ""),
          flush=True)


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    knowledge = KnowledgeTools(conn)
    before = {str(conn.documents.Item(i).Name) for i in range(1, conn.documents.Count + 1)}
    before_active = str(conn.app.ActiveDocument.Name)
    os.makedirs(ZH_DIR, exist_ok=True)
    ascii_path = os.path.join(ROOT, "copy_src.CATPart")
    zh_path = os.path.join(ZH_DIR, "副本.CATPart")

    print("1. build a part with a solid, saved under an ASCII name", flush=True)
    docs.execute("catia_new_part", {"name": "CopySrc", "file_path": ascii_path})
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 30, "y2": 20})
    sketcher.execute("catia_close_sketch", {})
    print("   " + part_design.execute("catia_pad", {"height": 4})[:120], flush=True)

    print("\n2. catia_save_as_copy to a Chinese file NAME", flush=True)
    try:
        reply = docs.execute("catia_save_as_copy", {"file_path": zh_path})
        print(f"   {reply[:160]}", flush=True)
    except Exception as error:  # noqa: BLE001
        print(f"   {getattr(error, 'code', '?')}: {str(error).splitlines()[0][:160]}", flush=True)
    check("the Chinese-named copy exists on disk", os.path.exists(zh_path),
          f"{os.path.getsize(zh_path) if os.path.exists(zh_path) else 0} B")

    print("\n3. Documents.Open on that Chinese-named file", flush=True)
    ascii_name = os.path.basename(ascii_path)
    doc = None
    for index in range(1, conn.documents.Count + 1):
        if str(conn.documents.Item(index).FullName).lower() == ascii_path.lower():
            doc = conn.documents.Item(index)
            break
    try:
        if doc is not None:
            doc.Close()
        reopened = conn.documents.Open(os.path.normpath(zh_path))
        reopened.Activate()
        name = str(conn.active_document.Name)
        check("CATIA opens a non-ASCII file name", True, name)
    except Exception as error:  # noqa: BLE001
        check("CATIA opens a non-ASCII file name", False, f"{type(error).__name__}: {error}")
        reopened = None

    print("\n4. edit it and doc.Save() - the file exists, so this is not a first SaveAs", flush=True)
    if reopened is not None:
        try:
            knowledge.execute("catia_create_parameter",
                              {"name": "L1", "type": "LENGTH", "value": 12.0})
            before_mtime = os.path.getmtime(zh_path)
            time.sleep(0.3)
            saved = docs.execute("catia_save_document", {})
            after_mtime = os.path.getmtime(zh_path)
            print(f"   {saved[:120]}", flush=True)
            check("Save() on a non-ASCII-named document works", after_mtime > before_mtime,
                  f"mtime {before_mtime:.0f} -> {after_mtime:.0f}")
        except Exception as error:  # noqa: BLE001
            check("Save() on a non-ASCII-named document works", False,
                  f"{getattr(error, 'code', type(error).__name__)}: "
                  f"{str(error).splitlines()[0][:150]}")

    for index in range(conn.documents.Count, 0, -1):
        doc = conn.documents.Item(index)
        try:
            if str(doc.Name) not in before:
                doc.Close()
        except Exception as error:  # noqa: BLE001
            print(f"   (could not close {doc.Name}: {error})", flush=True)
    for path in (ascii_path, zh_path):
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
    for index in range(1, conn.documents.Count + 1):
        if str(conn.documents.Item(index).Name) == before_active:
            conn.documents.Item(index).Activate()
            break
    print(f"\n{conn.documents.Count} document(s) after, active "
          f"'{getattr(conn.app.ActiveDocument, 'Name', '?')}'")
    bad = [label for label, ok, _d in RESULT if not ok]
    print("VERDICT: " + ("the Chinese name is reachable end-to-end" if not bad
                         else f"{len(bad)} step(s) refused: {bad}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
