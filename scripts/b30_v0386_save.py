"""0.3.86 probe 1: which save call runs, and what the Chinese folder does to it.

Three reports since 0.3.81 say ``catia_new_part(file_path=<Chinese folder>)``
fails with ``SAVE_FAILED`` while ``catia_save_document`` into the SAME folder
works. The message they quote is

    Save failed after 3 attempts: verify_0385_cn.CATPart ...

and in this package that string can only come from ``dialogs.save`` - the bare
``doc.Save()``. ``new_part`` calls ``_save_document(file_path)``, which takes the
``dialogs.save_as`` branch. So either the report is quoting a failure from
another call (a ``close_document(save=true)`` is the obvious candidate: it is the
one place a part named after the target file is bare-Saved), or a path I cannot
see reaches it.

This decides which by instrumenting the two functions instead of reasoning about
them: every call is recorded, before and after the fact.

Safety: the files live in the reporter's own working folder because that folder
is the variable under test, they are named ``_probe86_*``, the script refuses to
run if such a file already exists (so it can never delete someone else's), and
every file it creates is removed at the end.
"""
from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp import dialogs  # noqa: E402
from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402

CJK = r"F:\WokrBuddy Catia Space\饮料瓶瓶底曲面建模"
ASCII_DIR = r"F:\catia_probe_v0386\ascii"
CASES = [
    ("report path, forward slashes",
     CJK.replace("\\", "/") + "/_probe86_a.CATPart".replace("/", "/")),
    ("same folder, backslashes", CJK + r"\_probe86_b.CATPart"),
    ("ascii folder", ASCII_DIR + r"\_probe86_c.CATPart"),
]

CALLS: list[tuple[str, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def instrument() -> None:
    original_save = dialogs.save
    original_save_as = dialogs.save_as

    def spy_save(doc, tries=3):
        CALLS.append(("save", str(getattr(doc, "Name", "?"))))
        say(f"      -> dialogs.save({getattr(doc, 'Name', '?')!r})")
        return original_save(doc, tries)

    def spy_save_as(doc, path, tries=6, wait=0.6):
        CALLS.append(("save_as", str(path)))
        say(f"      -> dialogs.save_as({path!r})")
        return original_save_as(doc, path, tries, wait)

    dialogs.save = spy_save
    dialogs.save_as = spy_save_as


def main() -> None:
    os.makedirs(ASCII_DIR, exist_ok=True)
    say(f"python cwd: {os.getcwd()}")
    say(f"Chinese folder exists: {os.path.isdir(CJK)}")
    say(f"Chinese folder writable: {os.access(CJK, os.W_OK)}")
    for label, path in CASES:
        if os.path.exists(path):
            raise SystemExit(
                f"refusing to run: {path} already exists, and this probe deletes "
                "the files it creates")

    instrument()
    conn = CATIAConnection()
    conn.connect()
    documents = DocumentTools(conn)
    say(f"documents open before the probe: {conn.documents.Count}")

    created: list[str] = []
    for label, path in CASES:
        say("")
        say(f"=== {label} ===")
        say(f"   path: {path}")
        CALLS.clear()
        try:
            reply = documents.execute(
                "catia_new_part",
                {"name": f"P86_{len(created)}", "file_path": path})
            say("   OK   " + reply.splitlines()[0][:220])
            created.append(path)
            say(f"   calls: {CALLS}")
            # The report's other suspect: is the document really on disk, and
            # does a plain Save() on it work afterwards?
            doc = conn.active_document
            try:
                full = str(doc.FullName)
            except Exception as error:  # noqa: BLE001
                full = f"<FullName raised: {str(error).splitlines()[0][:60]}>"
            say(f"   FullName={full}  file exists="
                f"{os.path.isfile(path)}  size="
                f"{os.path.getsize(path) if os.path.isfile(path) else 0} B")
            CALLS.clear()
            try:
                say("   save_document() with no path: "
                    + documents.execute("catia_save_document", {}).splitlines()[0][:160])
                say(f"   calls: {CALLS}")
            except Exception as error:  # noqa: BLE001
                say(f"   save_document() FAILED: {str(error).splitlines()[0][:200]}")
                say(f"   calls: {CALLS}")
        except Exception as error:  # noqa: BLE001
            say(f"   FAILED {str(error).splitlines()[0][:300]}")
            say(f"   calls: {CALLS}")
            say("   " + "\n   ".join(traceback.format_exc().splitlines()[-6:]))
        finally:
            # Close only what this probe named, and never with save=true.
            try:
                for index in range(conn.documents.Count, 0, -1):
                    doc = conn.documents.Item(index)
                    if str(doc.Name).startswith(("P86_", "_probe86")):
                        doc.Close()
            except Exception:  # noqa: BLE001
                pass

    say("")
    say("=== cleanup ===")
    for path in created:
        try:
            if os.path.isfile(path):
                os.remove(path)
                say(f"   removed {path}")
        except OSError as error:
            say(f"   could not remove {path}: {error}")
    say(f"documents open after the probe: {conn.documents.Count}")


if __name__ == "__main__":
    main()
