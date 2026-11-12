"""Does hiding the geometrical set a loft's sections live in hide the SOLID?

The 2026-11-12 report (P0-2) tried three ways to get a clean picture and got three
different answers, one of which was an EMPTY viewport:

    screenshot(clean=true) only                      solid visible, 250 elements hidden
    set_visibility(kind='construction', show=false)  EMPTY viewport, twice, same MD5
    set_visibility(name='GSD_Set', show=false)       solid gone; body show=true did not help

Our own docstring says a geometrical set is a separate branch and hiding it does not
hide a solid in the body - measured on a THICKENED SURFACE. A loft is a different
thing: its sections live in that set, so if CATIA hides the dependencies too, the
solid goes with them and a caller reads an empty picture as a broken model.

The measurable here is the picture itself: a blank 3D view compresses to a much
smaller JPEG than one with a solid in it. Same window, same size, same call.

    .venv/Scripts/python.exe scripts/b30_v0417_visibility.py
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
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0417")
ROOT = os.path.join(_PROBE_ROOT, "visible")


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


def shot(exporter, tag: str, clean: bool = False) -> int:
    path = os.path.join(ROOT, f"shot_{tag}.jpg")
    if os.path.exists(path):
        os.remove(path)
    try:
        exporter.execute("catia_screenshot", {"file_path": path, "clean": clean})
    except Exception as error:  # noqa: BLE001
        say(f"    screenshot {tag}: FAILED {str(error).splitlines()[0][:100]}")
        return -1
    size = os.path.getsize(path) if os.path.exists(path) else -1
    say(f"    screenshot {tag}: {size} bytes")
    return size


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)
    documents = DocumentTools(conn)
    gsd = GSDTools(conn)
    part_design = PartDesignTools(conn)
    exporter = ExportTools(conn)

    path = os.path.join(ROOT, "LoftVis.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part", {"name": "LoftVis", "file_path": path,
                                         "overwrite": True})
    activate(conn, "LoftVis.CATPart")
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "radius": 20,
                                     "support_plane": "xy", "name": "S1"})
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 30], "radius": 8,
                                     "support_plane": "xy", "offset": 30,
                                     "name": "S2"})
    loft = " ".join(str(part_design.execute(
        "catia_solid_loft", {"sections": ["S1", "S2"]})).split())
    say("loft: " + loft[:160])

    say("")
    say("── baseline ──")
    shot(exporter, "0_before")

    say("")
    say("── hide the set the sections live in ──")
    try:
        reply = part_design.execute("catia_set_visibility",
                                     {"name": "GSD_Set", "show": False})
        say("    set_visibility: " + " ".join(str(reply).split())[:220])
    except Exception as error:  # noqa: BLE001
        say("    set_visibility REFUSED: " + str(error).splitlines()[0][:160])
    hidden = shot(exporter, "1_set_hidden")

    say("")
    say("── ask for the body back ──")
    try:
        reply = part_design.execute("catia_set_visibility",
                                     {"name": "零件几何体", "show": True})
        say("    set_visibility(body): " + " ".join(str(reply).split())[:200])
    except Exception as error:  # noqa: BLE001
        say("    set_visibility(body) REFUSED: " + str(error).splitlines()[0][:160])
    restored = shot(exporter, "2_body_shown")

    say("")
    say("── show the set again ──")
    part_design.execute("catia_set_visibility", {"name": "GSD_Set", "show": True})
    back = shot(exporter, "3_set_shown")

    # The report's second route: hide every construction element, then ask for a
    # clean picture. That combination is the one it reproduced twice with the same
    # MD5, so it is a separate cell - "hiding a set is fine" does not settle it.
    say("")
    say("── hide kind='construction', then clean=true ──")
    try:
        reply = part_design.execute("catia_set_visibility",
                                     {"kind": "construction", "show": False})
        say("    construction hidden: " + " ".join(str(reply).split())[:200])
    except Exception as error:  # noqa: BLE001
        say("    REFUSED: " + str(error).splitlines()[0][:160])
    plain = shot(exporter, "5_construction_hidden")
    cleaned = shot(exporter, "5_construction_hidden_clean", clean=True)

    say("")
    say(f"sizes: before={shot(exporter, '4_again')}  set_hidden={hidden}  "
        f"body_shown={restored}  set_shown={back}  construction_hidden={plain}  "
        f"construction_hidden_clean={cleaned}")
    if hidden > 0:
        say("VERDICT: " + (
            "hiding the set did NOT blank the picture - P0-2 not reproduced"
            if hidden > (back * 0.5 if back > 0 else 0)
            else "the picture collapsed when the set was hidden - P0-2 stands"))
        say("  (a blank viewport compresses to a small file; compare with the "
            "baseline, not with an absolute size)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
