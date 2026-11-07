"""Does CATIA's FIRST SaveAs refuse a non-ASCII DIRECTORY, or only the file name?

Two sentences in this package disagreed and both claimed a B30 measurement:

  results.PathNotSupported  "a non-ASCII DIRECTORY is fine"  (measured 2026-10-31)
  tools/document.py         "the folder counts, not only the file name"
                            (written 2026-11-06 from a report)

The 2026-11-06 reporter had a Chinese folder AND a Chinese file name at first,
then an ASCII file name in the same Chinese folder, and both failed. This probe
separates the two variables with the same CATIA session, same document type,
same depth of path.
"""

from __future__ import annotations

import os
import sys

import pythoncom
import win32com.client

ROOT = r"F:\catia_probe_v0374"
ZH_DIR = os.path.join(ROOT, "中文目录")

CASES = [
    ("ascii dir / ascii name", os.path.join(ROOT, "probe_ascii.CATPart")),
    ("ascii dir / CHINESE name", os.path.join(ROOT, "probe_中文.CATPart")),
    ("CHINESE dir / ascii name", os.path.join(ZH_DIR, "probe_ascii2.CATPart")),
    ("CHINESE dir / CHINESE name", os.path.join(ZH_DIR, "探针.CATPart")),
]


def main() -> int:
    pythoncom.CoInitialize()
    app = win32com.client.GetActiveObject("CATIA.Application")
    os.makedirs(ZH_DIR, exist_ok=True)
    say = lambda line: print(line, flush=True)  # noqa: E731

    say(f"CATIA {getattr(app, 'Version', '?')}, {app.Documents.Count} document(s) open")
    results = []
    for label, path in CASES:
        doc = None
        try:
            doc = app.Documents.Add("Part")
            before = app.Documents.Count
            try:
                # The raw call, exactly what a naive client does.
                doc.SaveAs(path)
                outcome = "saved"
            except Exception as error:  # noqa: BLE001
                outcome = f"REFUSED {str(error).splitlines()[0][:90]}"
        finally:
            try:
                if doc is not None:
                    doc.Close()
            except Exception as error:  # noqa: BLE001
                say(f"   (close failed: {error})")
        on_disk = os.path.exists(path)
        if on_disk:
            os.remove(path)
        results.append((label, outcome, on_disk))
        say(f"   {label:<28} -> {outcome}   file on disk: {on_disk}")

    say("")
    for label, outcome, on_disk in results:
        say(f"{'OK  ' if outcome == 'saved' and on_disk else 'FAIL'} {label}: {outcome}")
    try:
        os.rmdir(ZH_DIR)
        os.rmdir(ROOT)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
