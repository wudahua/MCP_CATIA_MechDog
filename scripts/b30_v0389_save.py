"""0.3.89 probe: which save call runs on the report's spaced-ASCII folder?

The 0.3.88 report says `new_part` into
``F:/WokrBuddy Catia Space/饮料瓶瓶底曲面建模/x.CATPart`` answers SAVE_FAILED with
``data.call = "Document.Save"`` - a bare Save - and no trace of the staged route.
Their hypothesis is that the "is the folder non-ASCII" test splits the path on
whitespace, so ``F:/WokrBuddy`` decides the verdict.

`_path_is_ascii` is ``str(path).isascii()`` on the whole string; there is no
split. This probe settles it by INSTRUMENTING both save functions and counting
the calls, instead of arguing about the text.

Only this script's own file is written, and only that one is removed.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools import document as document_module  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402

CJK = r"F:\WokrBuddy Catia Space\饮料瓶瓶底曲面建模"
TARGET = os.path.join(CJK, "verify_0389_probe.CATPart")


def main() -> None:
    calls: list[tuple[str, str]] = []
    real_save = document_module.dialogs.save
    real_save_as = document_module.dialogs.save_as

    def spy_save(doc, *args, **kwargs):
        calls.append(("save", str(getattr(doc, "Name", "?"))))
        return real_save(doc, *args, **kwargs)

    def spy_save_as(doc, path, *args, **kwargs):
        calls.append(("save_as", str(path)))
        return real_save_as(doc, path, *args, **kwargs)

    document_module.dialogs.save = spy_save
    document_module.dialogs.save_as = spy_save_as

    conn = CATIAConnection()
    conn.connect()
    tools = DocumentTools(conn)

    print("target  =", TARGET, flush=True)
    print("dirname =", os.path.dirname(TARGET), flush=True)
    print("whole path is ascii        :", TARGET.isascii(), flush=True)
    print("dirname is ascii           :", os.path.dirname(TARGET).isascii(), flush=True)
    print("_path_is_ascii(target)     :",
          DocumentTools._path_is_ascii(TARGET), flush=True)
    print("staged branch would fire   :",
          not DocumentTools._path_is_ascii(TARGET), flush=True)

    if os.path.isfile(TARGET):
        os.remove(TARGET)
        print("removed a leftover probe file", flush=True)

    try:
        reply = tools.execute("catia_new_part",
                              {"name": "PROBE89", "file_path": TARGET})
        print("\nREPLY:", reply, flush=True)
    except Exception as error:  # noqa: BLE001
        print("\nREFUSED:", type(error).__name__, flush=True)
        print(str(error), flush=True)
        data = getattr(error, "data", None)
        print("data:", data, flush=True)

    print("\ncalls, in order:", flush=True)
    for kind, where in calls:
        print(f"  {kind:8s} {where}", flush=True)
    print("save (bare) called   :", any(k == "save" for k, _ in calls), flush=True)
    print("save_as called       :", sum(1 for k, _ in calls if k == "save_as"),
          flush=True)
    print("file exists          :", os.path.isfile(TARGET),
          os.path.getsize(TARGET) if os.path.isfile(TARGET) else 0, "bytes",
          flush=True)

    # Leave nothing behind: close this script's document, then remove its file.
    for index in range(conn.documents.Count, 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = os.path.normcase(os.path.abspath(str(doc.FullName)))
        except Exception:  # noqa: BLE001 - never saved
            continue
        if full == os.path.normcase(os.path.abspath(TARGET)):
            print("closing", doc.Name, flush=True)
            try:
                doc.Close()
            except Exception as error:  # noqa: BLE001
                print("  close failed:", str(error).splitlines()[0][:80], flush=True)
    if os.path.isfile(TARGET):
        os.remove(TARGET)
        print("removed the probe file again", flush=True)


if __name__ == "__main__":
    main()
