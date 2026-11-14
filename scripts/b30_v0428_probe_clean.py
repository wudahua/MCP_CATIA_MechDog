"""Can the solid be PUT BACK after its construction elements are hidden?

The 2026-11-14 report's P1-3 is a real dead end and this project already knew half
of it: 0.3.115 measured that hiding kind="construction" takes a solid loft off the
screen, because those section curves are its input, and wrote the fact into the
warning of catia_set_visibility. The report's addition is that
catia_screenshot(clean=true) cannot help either - it only hides geometrical sets,
and a sketch-section part has none ("this part has no geometrical sets, so nothing
was hidden"). So there is no path to a clean picture.

This probe asks the only question that can open one: after hiding the sections, can
the solid be made visible again by setting ITS OWN show flag? One fresh document,
three states, and a picture of each:

  A  everything visible            (what the part looks like now)
  B  sections hidden               (the reported dead end)
  C  sections hidden, loft shown   (the candidate fix)

    .venv/Scripts/python.exe scripts/b30_v0428_probe_clean.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0428")
ROOT = os.path.join(_PROBE_ROOT, "clean")


def say(line: str = "") -> None:
    print(line, flush=True)


def fresh(conn: CATIAConnection, documents: DocumentTools, name: str):
    path = os.path.join(ROOT, name + ".CATPart")
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
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    for index in range(1, int(conn.documents.Count) + 1):
        if conn.documents.Item(index).Name == name + ".CATPart":
            conn.documents.Item(index).Activate()
    return conn.get_active_part()


def show_of(conn, node):
    """The node's own show flag. None when it cannot be read."""
    try:
        selection = conn.hso
        selection.Clear()
        selection.Add(node)
        value = int(selection.VisProperties.Show)
        selection.Clear()
        return value
    except Exception:  # noqa: BLE001
        return None


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    export = ExportTools(conn)

    part = fresh(conn, documents, "ProbeClean")
    # Two sketch ellipses on offset planes, lofted - the report's own shape.
    for offset, major, minor in ((0, 9, 4.5), (20, 6, 3)):
        sketcher.execute("catia_create_sketch",
                         {"plane": {"type": "offset", "base": "xy",
                                    "distance": offset}})
        sketcher.execute("catia_sketch_ellipse",
                         {"cx": 0, "cy": 0, "major": major, "minor": minor})
        sketcher.execute("catia_close_sketch", {})
    reply = " ".join(str(part_design.execute("catia_solid_loft",
                                             {"sections": ["草图.1", "草图.2"],
                                              "name": "Body"})).split())
    say("loft: " + reply[:200])

    loft = part.Bodies.Item(1).Shapes.Item(1)
    sketches = [part.Bodies.Item(1).Sketches.Item(i)
                for i in range(1, int(part.Bodies.Item(1).Sketches.Count) + 1)]
    say(f"loft '{loft.Name}' show={show_of(conn, loft)}; "
        + ", ".join(f"'{s.Name}' show={show_of(conn, s)}" for s in sketches))

    def capture(tag: str) -> str:
        path = os.path.join(ROOT, f"{tag}.jpg")
        try:
            export.execute("catia_screenshot", {"file_path": path})
        except Exception as error:  # noqa: BLE001
            return f"{tag}: screenshot failed {str(error)[:120]}"
        return path

    say("")
    say("── A: everything visible ──")
    export.execute("catia_fit_all", {})
    say("  " + capture("A_visible"))

    say("")
    say("── B: hide kind=construction (the reported dead end) ──")
    say("  " + " ".join(str(part_design.execute(
        "catia_set_visibility", {"kind": "construction", "show": False})).split())[:260])
    for sketch in sketches:
        say(f"    '{sketch.Name}' show={show_of(conn, sketch)}  "
            f"loft show={show_of(conn, loft)}")
    export.execute("catia_fit_all", {})
    say("  " + capture("B_hidden"))

    say("")
    say("── C: sections hidden, the LOFT's own flag set back to shown ──")
    for node, label in ((loft, "loft"),):
        try:
            selection = conn.hso
            selection.Clear()
            selection.Add(node)
            selection.VisProperties.SetShow(1)
            selection.Clear()
            say(f"    SetShow(1) on {label} -> {show_of(conn, loft)}")
        except Exception as error:  # noqa: BLE001
            say(f"    SetShow(1) on {label} FAILED {str(error)[:140]}")
    export.execute("catia_fit_all", {})
    say("  " + capture("C_reshown"))

    say("")
    say("── D: restore, and confirm the picture comes back ──")
    say("  " + " ".join(str(part_design.execute(
        "catia_set_visibility", {"kind": "construction", "show": True})).split())[:200])
    export.execute("catia_fit_all", {})
    say("  " + capture("D_restored"))
    for sketch in sketches:
        say(f"    '{sketch.Name}' show={show_of(conn, sketch)}  "
            f"loft show={show_of(conn, loft)}")

    say("")
    say("pictures: " + ROOT)
    say(json.dumps({"loft": str(loft.Name)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
