"""0.3.82 probe 2: the two claims that came back negative, asked properly.

(a) The report's P1-5 says AddNewGSDCircPattern / AddNewGSDRectPattern /
    AddNewSurfacicCircPattern are "in the typeinfo, only not exposed". The first
    probe found none of them on part.HybridShapeFactory - so either they live on
    another object, or they carry another name. Dump every AddNew* on the factory
    and grep for Pattern, instead of concluding from twelve chosen names.

(b) P2-1: Application.CaptureToFile does not exist on this build at all, yet
    catia_screenshot produces a 762x744 JPG - so the tool goes through the
    viewer. Ask the viewer whether it takes a size and whether the extension
    decides the format.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402

ROOT = r"F:\catia_probe_v0382_caps2"


def say(line: str = "") -> None:
    print(line, flush=True)


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)

    part_path = os.path.join(ROOT, "caps2.CATPart")
    document.execute("catia_new_part",
                     {"name": "CAPS2", "file_path": part_path, "overwrite": True})
    say(f"active: {conn.active_document.Name}")

    part = conn.get_active_part()
    factory = part.HybridShapeFactory
    try:
        info = factory._oleobj_.GetTypeInfo()
        count = info.GetTypeAttr().cFuncs
    except Exception as error:  # noqa: BLE001
        say(f"no type info: {str(error).splitlines()[0][:90]}")
        count = 0
    names = []
    for index in range(count):
        try:
            desc = info.GetFuncDesc(index)
            got = info.GetNames(desc.memid)
        except Exception:  # noqa: BLE001
            continue
        if got:
            names.append(got[0])
    say(f"\nfactory declares {len(names)} members")
    hits = [name for name in names if "attern" in name or "GSD" in name]
    say("Pattern/GSD members: " + (", ".join(hits) if hits else "NONE"))
    adds = [name for name in names if name.startswith("AddNew")]
    say(f"AddNew* ({len(adds)}): " + ", ".join(adds))

    # ── (b) the viewer ───────────────────────────────────────────────────
    say("\n=== viewer CaptureToFile ===")
    viewer = None
    try:
        viewer = conn.app.ActiveWindow.ActiveViewer
    except Exception as error:  # noqa: BLE001
        say(f"   no viewer: {str(error).splitlines()[0][:90]}")
    if viewer is not None:
        for label, name, extra in (
            ("1 arg .jpg", "shot1.jpg", ()),
            ("1 arg .png", "shot2.png", ()),
            ("3 args 1200x900 .png", "shot3.png", (1200, 900)),
            ("3 args 1200x900 .jpg", "shot4.jpg", (1200, 900)),
        ):
            path = os.path.join(ROOT, name)
            try:
                viewer.CaptureToFile(path, *extra)
                size = os.path.getsize(path) if os.path.isfile(path) else -1
                # Header bytes tell the real format; the extension is a request.
                head = b""
                if os.path.isfile(path):
                    with open(path, "rb") as handle:
                        head = handle.read(4)
                say(f"   {label}: wrote {name} {size} bytes head={head!r}")
            except Exception as error:  # noqa: BLE001 - the refusal is the answer
                say(f"   {label}: REFUSED {str(error).splitlines()[0][:100]}")

    own = os.path.normcase(os.path.abspath(ROOT))
    for index in range(conn.documents.Count, 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = os.path.normcase(os.path.abspath(str(doc.FullName)))
        except Exception:  # noqa: BLE001
            continue
        if full.startswith(own):
            doc.Activate()
            document.execute("catia_close_document", {"save": False})
    say("\ncleanup: closed the documents under " + ROOT)


if __name__ == "__main__":
    main()
