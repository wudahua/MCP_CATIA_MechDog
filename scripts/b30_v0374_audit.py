"""0.3.74 on B30: the three things this release claims, measured.

70-1  catia_open_document must not answer "activated it" for a part that has no
      window of its own (a component of an open product). Built here with a
      throwaway part + product so the claim is tested on the real thing.
70-2  catia_list_documents must type a CATDrawing "CATDrawing", not "Other".
frame the datum plane and the three points of a frame sketch must be hidden when
      they are created, findable by name afterwards, and must not stop a pad.

Everything is created under F:\\catia_probe_v0374 and closed again; the documents
that were open before this script are left alone (and the one that was in front
is put back in front).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.drawing import DraftingTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0374"
RESULTS: list[tuple[str, bool, str]] = []


def say(line: str) -> None:
    print(line, flush=True)


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    say(f"   {'OK  ' if ok else 'FAIL'} {label}" + (f"  [{detail[:220]}]" if detail else ""))


def shown_of(part_doc, feature) -> int | None:
    """The visibility flag of one tree node, read through its document."""
    try:
        selection = part_doc.Selection
        selection.Clear()
        selection.Add(feature)
        value = selection.VisProperties.GetShow()
        selection.Clear()
    except Exception as error:  # noqa: BLE001
        say(f"      (GetShow failed: {error})")
        return None
    # GetShow() returns a 2-tuple on this build; the flag is the second element.
    return int(value[1]) if isinstance(value, (tuple, list)) else int(value)


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs_tool = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    assembly = AssemblyTools(conn)
    drafting = DraftingTools(conn)

    try:
        before_active = str(conn.app.ActiveDocument.Name)
    except Exception:  # noqa: BLE001
        before_active = ""
    existing_before = {str(conn.documents.Item(i).Name)
                       for i in range(1, conn.documents.Count + 1)}
    say(f"session before: {conn.documents.Count} document(s), active '{before_active}'")
    os.makedirs(ROOT, exist_ok=True)
    for stale in os.listdir(ROOT):
        try:
            os.remove(os.path.join(ROOT, stale))
        except OSError as error:
            say(f"   (could not remove stale {stale}: {error})")
    mine: set[str] = set()

    # ── 70-2: a drawing is a CATDrawing ────────────────────────────────────
    say("\n70-2  catia_list_documents types a CATDrawing")
    try:
        drafting.execute("catia_new_drawing", {})
        drawing_name = str(conn.active_document.Name)
        mine.add(drawing_name)
        import json

        rows = json.loads(docs_tool.execute("catia_list_documents", {}))
        row = next((r for r in rows if r["name"] == drawing_name), None)
        check("drawing is listed as CATDrawing",
              row is not None and row.get("type") == "CATDrawing",
              str(row))
        check("no document is typed 'Other' in this session",
              not [r["name"] for r in rows if r.get("type") == "Other"],
              str([r["name"] for r in rows if r.get("type") == "Other"]))
    except Exception as error:  # noqa: BLE001
        check("catia_new_drawing + list_documents", False, f"{type(error).__name__}: {error}")

    # ── 70-1: a component with no window of its own ────────────────────────
    say("\n70-1  open_document on a part that has no window")
    part_path = os.path.join(ROOT, "probe_bare.CATPart")
    asm_path = os.path.join(ROOT, "probe_host.CATProduct")
    try:
        docs_tool.execute("catia_new_part", {"name": "ProbeBare", "file_path": part_path})
        bare = str(conn.active_document.Name)
        mine.add(bare)
        docs_tool.execute("catia_new_product", {"name": "ProbeHost", "file_path": asm_path})
        host = str(conn.active_document.Name)
        mine.add(host)
        assembly.execute("catia_add_component", {"file_path": part_path})
        # Close the part's own document; the product keeps its component loaded.
        for index in range(conn.documents.Count, 0, -1):
            doc = conn.documents.Item(index)
            if str(doc.Name) == bare:
                doc.Close()
                break
        still_there = any(str(conn.documents.Item(i).Name) == bare
                          for i in range(1, conn.documents.Count + 1))
        check("the part is still in Documents after closing its window", still_there,
              f"{conn.documents.Count} document(s)")
        try:
            reply = docs_tool.execute("catia_open_document", {"file_path": part_path})
            check("open_document refuses to claim it activated", False, reply[:160])
        except Exception as error:  # noqa: BLE001
            code = getattr(error, "code", type(error).__name__)
            text = str(error)
            check("open_document answers ACTIVATION_FAILED", code == "ACTIVATION_FAILED",
                  f"{code}: {text.splitlines()[0][:120]}")
            check("and says the part has no window of its own",
                  "no window of its own" in text)
            check("and does not send the caller to catia_open_document",
                  "open it with catia_open_document" not in text)
            check("and offers close-then-reopen",
                  "catia_close_document" in text)
        active_now = str(conn.app.ActiveDocument.Name)
        check("the active document really did not change", active_now == host, active_now)
    except Exception as error:  # noqa: BLE001
        check("70-1 setup", False, f"{type(error).__name__}: {error}")

    # ── the frame sketch's datum plane ────────────────────────────────────
    say("\nframe  plane hidden, nameable, and harmless when hidden")
    frame_path = os.path.join(ROOT, "probe_frame.CATPart")
    try:
        docs_tool.execute("catia_new_part", {"name": "ProbeFrame", "file_path": frame_path})
        frame_doc = str(conn.active_document.Name)
        mine.add(frame_doc)
        reply = sketcher.execute("catia_create_sketch", {
            "plane": {"type": "frame", "origin": [0, 0, 0],
                      "u": [1, 0, 0], "v": [0, 0, -1]}})
        say(f"      create_sketch: {reply[:220]}")
        check("the reply names the hidden plane", "hidden" in reply, reply[:160])
        doc = conn.active_document
        part = doc.Part
        body = part.Bodies.Item(1)
        hybrids = [(hybrids_j, body.HybridShapes.Item(hybrids_j))
                   for hybrids_j in range(1, body.HybridShapes.Count + 1)]
        names = [str(feature.Name) for _j, feature in hybrids]
        say(f"      body HybridShapes: {names}")
        check("4 features were created (plane + 3 points)", len(names) == 4, str(names))
        check("the plane is called Datum_frame", "Datum_frame" in names, str(names))
        flags = [shown_of(doc, feature) for _j, feature in hybrids]
        say(f"      GetShow()[1] for each: {flags}")
        check("all four are hidden (1 = hidden)", flags and set(flags) == {1}, str(flags))

        # A pad on a sketch drawn on a HIDDEN plane must still build.
        sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 40, "y2": 20})
        sketcher.execute("catia_close_sketch", {})
        padded = part_design.execute("catia_pad", {"height": 5})
        say(f"      pad: {padded[:200]}")
        check("a pad on the hidden datum plane still builds",
              "4000.0" in padded or "4000" in padded, padded[:140])

        # And it can be brought back by the name the reply published.
        try:
            back = part_design.execute("catia_set_visibility",
                                       {"name": "Datum_frame", "show": True})
            say(f"      set_visibility: {back[:200]}")
            after = shown_of(doc, body.HybridShapes.Item(
                [i for i, n in enumerate(names) if n == "Datum_frame"][0] + 1))
            check("set_visibility(name='Datum_frame', show=true) finds it", True, back[:120])
            check("and it reads back as shown (0)", after == 0, str(after))
        except Exception as error:  # noqa: BLE001
            check("set_visibility by the published name", False,
                  f"{getattr(error, 'code', type(error).__name__)}: {error}")
    except Exception as error:  # noqa: BLE001
        check("frame sketch", False, f"{type(error).__name__}: {error}")

    # ── clean up: close only what this script created ─────────────────────
    say("\ncleanup")
    # "Anything that was not open when this started" - not a name list: a
    # catia_new_part that fails to save has already added its document, and a
    # cleanup driven by the names this script remembered would leave it behind
    # (which is exactly what happened on the first run).
    mine = {str(conn.documents.Item(i).Name)
            for i in range(1, conn.documents.Count + 1)} - existing_before
    # Close BY NAME from the end: indices shift under a Close, and saving one
    # IndexError in the cleanup is how a probe leaves documents behind.
    for name in sorted(mine, reverse=True):
        for attempt in range(4):
            index = None
            for position in range(1, conn.documents.Count + 1):
                if str(conn.documents.Item(position).Name) == name:
                    index = position
                    break
            if index is None:
                break
            try:
                conn.documents.Item(index).Close()
            except Exception as error:  # noqa: BLE001
                say(f"   (could not close {name}: {error})")
                break
    say(f"   still open, but mine: "
        f"{[n for n in mine if any(str(conn.documents.Item(i).Name) == n for i in range(1, conn.documents.Count + 1))]}")
    for path in (part_path, asm_path, frame_path):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
    try:
        os.rmdir(ROOT)
    except OSError:
        pass
    if before_active:
        try:
            for index in range(1, conn.documents.Count + 1):
                doc = conn.documents.Item(index)
                if str(doc.Name) == before_active:
                    doc.Activate()
                    break
        except Exception:  # noqa: BLE001
            pass
    say(f"session after: {conn.documents.Count} document(s), "
        f"active '{getattr(conn.app.ActiveDocument, 'Name', '?')}'")

    say("")
    failures = [label for label, ok, _detail in RESULTS if not ok]
    for label, ok, detail in RESULTS:
        say(f"{'OK  ' if ok else 'FAIL'} {label}")
    say(f"\n{len(RESULTS) - len(failures)}/{len(RESULTS)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
