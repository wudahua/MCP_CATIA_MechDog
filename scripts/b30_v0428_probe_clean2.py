"""WHICH construction element takes the solid off the screen?

The dead end is real (reproduced 2026-11-14: after kind="construction" hid 2
sketches and 3 datum planes, the viewport held nothing but the ground grid), and
re-setting the loft's OWN show flag does not bring it back (picture C is
byte-identical to B). So before a clean screenshot can exist, the responsible
element has to be found - catia_set_visibility hides three classes in one call,
and the report only saw the sum:

    kind="sketch"        the 2 section sketches
    kind="datum_plane"   the 3 origin planes AND the datum elements inside bodies

Each is hidden ALONE, photographed, and shown again, on one document whose state
is returned to "all shown" between cases.

    .venv/Scripts/python.exe scripts/b30_v0428_probe_clean2.py
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
ROOT = os.path.join(_PROBE_ROOT, "clean2")


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

    part = fresh(conn, documents, "ProbeClean2")
    for offset, major, minor in ((0, 9, 4.5), (20, 6, 3)):
        sketcher.execute("catia_create_sketch",
                         {"plane": {"type": "offset", "base": "xy",
                                    "distance": offset}})
        sketcher.execute("catia_sketch_ellipse",
                         {"cx": 0, "cy": 0, "major": major, "minor": minor})
        sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_solid_loft",
                        {"sections": ["草图.1", "草图.2"], "name": "Body"})
    say("part built: 2 sketch ellipses + solid loft")

    def capture(tag: str) -> None:
        path = os.path.join(ROOT, f"{tag}.jpg")
        export.execute("catia_fit_all", {})
        try:
            export.execute("catia_screenshot", {"file_path": path})
            say(f"  shot {tag}.jpg")
        except Exception as error:  # noqa: BLE001
            say(f"  shot {tag} FAILED {str(error)[:120]}")

    def hide(kind: str, show: bool) -> str:
        try:
            reply = str(part_design.execute(
                "catia_set_visibility", {"kind": kind, "show": show}))
            return " ".join(reply.split())[:120]
        except Exception as error:  # noqa: BLE001
            return f"FAILED {getattr(error, 'code', '')} {str(error)[:100]}"

    say("")
    say("── A: only the SKETCHES hidden ──")
    say("  " + hide("sketch", False))
    capture("A_sketch_only")
    say("  " + hide("sketch", True))

    say("")
    say("── B: only the DATUM PLANES (and body datum elements) hidden ──")
    say("  " + hide("datum_plane", False))
    capture("B_plane_only")
    say("  " + hide("datum_plane", True))

    say("")
    say("── C: sketches hidden, then UpdateObject on the loft ──")
    say("  " + hide("sketch", False))
    try:
        part.UpdateObject(part.Bodies.Item(1).Shapes.Item(1))
        say("  UpdateObject(loft) ok")
    except Exception as error:  # noqa: BLE001
        say(f"  UpdateObject FAILED {str(error)[:120]}")
    capture("C_update")
    say("  " + hide("sketch", True))

    say("")
    say("── D: sketches hidden, then the BODY hidden and shown again ──")
    say("  " + hide("sketch", False))
    body = part.Bodies.Item(1)
    for flag, label in ((0, "hide"), (1, "show")):
        try:
            selection = conn.hso
            selection.Clear()
            selection.Add(body)
            selection.VisProperties.SetShow(flag)
            selection.Clear()
            say(f"  body {label} ok")
        except Exception as error:  # noqa: BLE001
            say(f"  body {label} FAILED {str(error)[:120]}")
    capture("D_body_toggle")
    say("  " + hide("sketch", True))

    say("")
    say("── E: everything shown, sanity ──")
    capture("E_all_shown")
    say("pictures: " + ROOT)


if __name__ == "__main__":
    main()
