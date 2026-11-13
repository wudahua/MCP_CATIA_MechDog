"""Two questions that decide how 0.3.116 may be written, asked on B30.

A. Does a solid loft expose a readable SECTION COUNT? The 2026-11-12 report put
   45 sections into catia_solid_loft and had no way to tell whether CATIA used
   all of them - it compared its own numeric integral (69976 mm3) against 70047.2
   and called the 0.1% difference agreement. If the object can be asked, the
   reply can answer; if it cannot, the reply has to say what it DID count
   (accepted AddSectionToLoft calls) instead of implying more.

B. Is Document.Saved readable, and what does it say? catia_close_document is to
   gain all_unmodified=true, and "unmodified" must be CATIA's own answer rather
   than a guess from names.

Both are read-only questions about COM. Each cell that builds geometry gets its
own new document, because one failed update poisons the whole document.

    .venv/Scripts/python.exe scripts/b30_v0418_probe_loft_saved.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0418")
ROOT = os.path.join(_PROBE_ROOT, "loft_saved")


def say(line: str = "") -> None:
    print(line, flush=True)


def activate(conn: CATIAConnection, name: str) -> None:
    for index in range(1, conn.documents.Count + 1):
        candidate = conn.documents.Item(index)
        if candidate.Name == name:
            candidate.Activate()
            if conn.active_document.Name != name:
                raise RuntimeError(f"could not activate {name}")
            return
    raise RuntimeError(f"{name} is not open")


def close_ours(conn: CATIAConnection) -> None:
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


def members(obj: object) -> list[str] | str:
    """Every name the object's own type library publishes."""
    try:
        info = obj._oleobj_.GetTypeInfo()  # type: ignore[attr-defined]
    except Exception as error:  # noqa: BLE001
        return f"no typeinfo: {str(error).splitlines()[0][:80]}"
    try:
        attr = info.GetTypeAttr()
    except Exception as error:  # noqa: BLE001
        return f"no typeattr: {str(error).splitlines()[0][:80]}"
    names: list[str] = []
    for index in range(attr.cFuncs):
        try:
            described = info.GetFuncDesc(index)
            names.append(info.GetNames(described.memid)[0])
            info.ReleaseFuncDesc(described)
        except Exception:  # noqa: BLE001 - one unnamed member is not a failure
            continue
    return sorted(set(names))


def try_read(obj: object, attribute: str) -> str:
    try:
        value = getattr(obj, attribute)
    except Exception as error:  # noqa: BLE001
        return f"RAISED {str(error).splitlines()[0][:70]}"
    try:
        if hasattr(value, "Count"):
            return f"collection of {value.Count}"
    except Exception:  # noqa: BLE001
        pass
    return repr(value)[:60]


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)

    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)

    # ── A: a 3-section solid loft, then ask the object what it holds ────────
    say("")
    say("── A: three sections, then read the loft back ──")
    path = os.path.join(ROOT, "Loft.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": "Loft", "file_path": path, "overwrite": True})
    activate(conn, "Loft.CATPart")
    for height, radius in ((0.0, 8.0), (10.0, 10.0), (20.0, 12.0)):
        sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": height})
        sketcher.execute("catia_sketch_circle",
                         {"cx": 0, "cy": 0, "radius": radius})
        sketcher.execute("catia_close_sketch", {})
    reply = part_design.execute("catia_solid_loft",
                               {"sections": ["草图.1", "草图.2", "草图.3"]})
    say("  " + " ".join(str(reply).split())[:300])

    part = conn.get_active_part()
    body = part.MainBody
    feature = body.Shapes.Item(body.Shapes.Count)
    say(f"  last shape: {feature.Name}")
    inner = getattr(feature, "HybridShape", None)
    for label, obj in (("feature", feature), ("HybridShape", inner)):
        if obj is None:
            say(f"  {label}: none")
            continue
        names = members(obj)
        if isinstance(names, str):
            say(f"  {label}: {names}")
            continue
        say(f"  {label}: {len(names)} members")
        wanted = [name for name in names
                  if "ection" in name or "ount" in name.lower()]
        say(f"    section/count members: {wanted}")
        for name in wanted[:12]:
            say(f"      {name} -> {try_read(obj, name)}")

    # The name AddSectionToLoft suggests there is a getter for one section.
    for label, obj in (("HybridShape", inner), ("feature", feature)):
        if obj is None:
            continue
        for name in ("GetSectionToLoft", "SectionToLoft", "Sections"):
            say(f"  {label}.{name} -> {try_read(obj, name)}")

    # ── B: what Document.Saved says, on a document that is being written ─────
    say("")
    say("── B: Document.Saved through a save cycle (same loft part) ──")
    doc = conn.active_document
    say(f"  after building   : Saved={try_read(doc, 'Saved')}")
    documents.execute("catia_save_document", {})
    say(f"  after save       : Saved={try_read(doc, 'Saved')}")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 30.0})
    sketcher.execute("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": 4})
    sketcher.execute("catia_close_sketch", {})
    say(f"  after one circle : Saved={try_read(doc, 'Saved')}")
    documents.execute("catia_save_document", {})
    say(f"  after save again : Saved={try_read(doc, 'Saved')}")

    # A brand-new, never-saved part: the case all_unmodified must NOT close.
    say("")
    say("── B2: a brand-new part that has never been saved ──")
    documents.execute("catia_new_part", {"name": "Fresh"})
    fresh = conn.active_document
    say(f"  fresh part       : Saved={try_read(fresh, 'Saved')} "
        f"FullName={str(getattr(fresh, 'FullName', '') or '<empty>')[:70]}")

    # A document reopened from disk: unmodified by definition.
    say("")
    say("── B3: the same file reopened from disk ──")
    try:
        fresh.Close()
    except Exception:  # noqa: BLE001
        pass
    documents.execute("catia_open_document", {"file_path": path})
    activate(conn, "Loft.CATPart")
    say(f"  reopened         : Saved={try_read(conn.active_document, 'Saved')}")

    say("")
    say(f"documents open after the probe: {conn.documents.Count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
