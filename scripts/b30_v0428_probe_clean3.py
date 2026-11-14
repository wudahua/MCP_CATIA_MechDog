"""Which of the "datum planes" is the one that takes the solid away?

Probe clean2 isolated the class: hiding the SKETCHES leaves the solid on screen
(picture A), hiding the DATUM PLANES takes it away (picture B). But that call hides
two different things at once:

    the 3 origin planes (PlaneXY/YZ/ZX)      ... and ...
    every datum element INSIDE the body       <- the offset planes the sketches use

The second group are the sketches' SUPPORT. Hiding a support hides what depends on
it, which is a rule, not a defect - so the question is whether the ORIGIN planes
alone are innocent. If they are, a clean shot can hide them and keep the solid.

    .venv/Scripts/python.exe scripts/b30_v0428_probe_clean3.py
"""
from __future__ import annotations

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
ROOT = os.path.join(_PROBE_ROOT, "clean3")


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


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    export = ExportTools(conn)

    part = fresh(conn, documents, "ProbeClean3")
    for offset, major, minor in ((0, 9, 4.5), (20, 6, 3)):
        sketcher.execute("catia_create_sketch",
                         {"plane": {"type": "offset", "base": "xy",
                                    "distance": offset}})
        sketcher.execute("catia_sketch_ellipse",
                         {"cx": 0, "cy": 0, "major": major, "minor": minor})
        sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_solid_loft",
                        {"sections": ["草图.1", "草图.2"], "name": "Body"})
    say("part built")

    body = part.Bodies.Item(1)
    try:
        hybrids = [body.HybridShapes.Item(i)
                   for i in range(1, int(body.HybridShapes.Count) + 1)]
        say(f"datum elements inside the body: {[str(h.Name) for h in hybrids]}")
    except Exception as error:  # noqa: BLE001
        hybrids = []
        say(f"body.HybridShapes unreadable: {str(error)[:100]}")
    sketches = [body.Sketches.Item(i)
                for i in range(1, int(body.Sketches.Count) + 1)]

    def set_show(nodes, flag: int) -> None:
        selection = conn.hso
        selection.Clear()
        for node in nodes:
            selection.Add(node)
        selection.VisProperties.SetShow(flag)
        selection.Clear()

    def capture(tag: str) -> None:
        export.execute("catia_fit_all", {})
        export.execute("catia_screenshot",
                       {"file_path": os.path.join(ROOT, f"{tag}.jpg")})
        say(f"  shot {tag}.jpg")

    from catia_mcp.tools.measurement import _restore_visibility

    say("")
    say("── A: sketches hidden, ORIGIN planes untouched (the candidate) ──")
    set_show(sketches, 0)
    capture("A_sketch_only")
    set_show(sketches, 1)

    say("")
    say("── B: sketches hidden AND the 3 origin planes hidden ──")
    origin = [getattr(part.OriginElements, name)
              for name in ("PlaneXY", "PlaneYZ", "PlaneZX")]
    set_show(sketches, 0)
    set_show(origin, 0)
    capture("B_sketch_and_origin")
    set_show(origin, 1)
    set_show(sketches, 1)

    say("")
    say("── C: ONLY the datum elements inside the body (the sketches' support) ──")
    if hybrids:
        set_show(hybrids, 0)
        capture("C_body_datum_only")
        set_show(hybrids, 1)
    else:
        say("  nothing to hide")

    say("")
    say("── D: sketches hidden + body datum hidden (support gone) ──")
    if hybrids:
        set_show(sketches, 0)
        set_show(hybrids, 0)
        capture("D_sketch_and_support")
        set_show(hybrids, 1)
        set_show(sketches, 1)

    say("")
    say("── E: all shown again ──")
    capture("E_restored")
    say("pictures: " + ROOT)


if __name__ == "__main__":
    main()
