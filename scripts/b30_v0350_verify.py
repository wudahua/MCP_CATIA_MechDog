"""After-the-change check for 0.3.50, on B30.

    1  set_view: one screenshot per view of a plate 40x10x4 with a tower on its
       +Z face at +X. Before 0.3.50 'front' was a top-down picture and 'top' was
       an upside-down elevation; the images are written to %TEMP%\\b30_v0350 so a
       human can look at them, and the reply's direction vectors are printed.
    2  screenshot: the reply must carry the size read from the file, not the
       request (900x900 asked, the window's client area written).
    3  join: a split piece joined to the neighbour it lost the edge to - the
       failure must name the elements and the cause.
    4  mesh box: the warning must disappear once every geometrical set is
       hidden.
    5  delete: cascade=true must take the spline's coordinate points with it,
       and without it the reply must say how many were left behind.
    6  catia_diagnose must report the sheet-metal probe.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.diagnostics import DiagnosticsTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

OUT = os.path.join(tempfile.gettempdir(), "b30_v0350")
os.makedirs(OUT, exist_ok=True)


def say(line: str = "") -> None:
    print(line, flush=True)


def activate(conn, name: str):
    for index in range(1, conn.documents.Count + 1):
        candidate = conn.documents.Item(index)
        if str(candidate.Name) == name:
            candidate.Activate()
            if str(conn.active_document.Name) != name:
                raise RuntimeError(f"could not activate {name}")
            return candidate
    raise RuntimeError(f"{name} is not open")


def close_all(document, conn):
    names = []
    for index in range(1, conn.documents.Count + 1):
        try:
            names.append(str(conn.documents.Item(index).Name))
        except Exception:  # noqa: BLE001
            pass
    for name in names:
        activate(conn, name)
        document.execute("catia_close_document", {"save": False})


def fresh(name: str, document: DocumentTools, conn):
    path = os.path.join(OUT, f"{name}.CATPart")
    document.execute("catia_new_part", {"name": name, "file_path": path})
    activate(conn, f"{name}.CATPart")


def main() -> int:
    conn = CATIAConnection()
    say(conn.connect())
    document = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    gsd = GSDTools(conn)
    export = ExportTools(conn)
    measurement = MeasurementTools(conn)
    diagnostics = DiagnosticsTools(conn)

    close_all(document, conn)

    say("\n── 1. set_view: one capture per view ──")
    fresh("V0350_VIEW", document, conn)
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 40, "y2": 10})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 4.0})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 4.0})
    sketcher.execute("catia_sketch_rectangle", {"x1": 30, "y1": 2, "x2": 34, "y2": 6})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 30.0})
    for view in ("front", "top", "bottom", "left", "right", "isometric"):
        export.execute("catia_set_view", {"view": view})
        export.execute("catia_fit_all", {})
        path = os.path.join(OUT, f"{view}.jpg")
        if os.path.exists(path):
            os.remove(path)
        reply = export.execute("catia_screenshot", {"file_path": path})
        say(f"1 {view:<9}: {reply}")

    say("\n── 2. screenshot: size is read from the file ──")
    path = os.path.join(OUT, "sized.jpg")
    if os.path.exists(path):
        os.remove(path)
    say("2 " + export.execute("catia_screenshot",
                              {"file_path": path, "width": 900, "height": 900}))
    say(f"2 real file: {ExportTools._image_size(path)}")

    say("\n── 3. join: a split piece joined to its old neighbour ──")
    fresh("V0350_JOIN", document, conn)
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": 10.0, "name": "J_C0"})
    gsd.execute("catia_gsd_extrude", {"profile": "J_C0", "direction": [0, 0, 1],
                                      "limit1": 20.0, "name": "J_LOW"})
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 20], "support_plane": "xy",
                                     "radius": 10.0, "name": "J_C1"})
    gsd.execute("catia_gsd_extrude", {"profile": "J_C1", "direction": [0, 0, 1],
                                      "limit1": 20.0, "name": "J_HIGH"})
    say("3a join untouched        : " + (
        gsd.execute("catia_gsd_join", {"elements": ["J_LOW", "J_HIGH"], "name": "J_OK"})[:60]))
    gsd.execute("catia_gsd_plane_offset", {"base_plane": "xy", "offset": 10.0,
                                           "name": "J_MID"})
    gsd.execute("catia_gsd_split", {"element": "J_LOW", "cutter": "J_MID",
                                    "reverse": True, "name": "J_LOW_BOT"})
    try:
        gsd.execute("catia_gsd_join", {"elements": ["J_LOW_BOT", "J_HIGH"], "name": "J_BAD"})
        say("3b NOT refused - the join built something")
    except Exception as error:  # noqa: BLE001
        text = str(error)
        say(f"3b join after split      : {len(text)} chars")
        for marker in ("Joined: 'J_LOW_BOT'", "SHARED EDGE",
                       "JOINING FIRST and SPLITTING AFTERWARDS"):
            say(f"   contains {marker!r}: {marker in text}")
        say("   " + text[text.find("Joined:"):][:150])

    say("\n── 4. mesh box: warning follows the set's visibility ──")
    fresh("V0350_BOX", document, conn)
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": -5, "y1": -5, "x2": 5, "y2": 5})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10.0})
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 30], "support_plane": "xy",
                                     "radius": 4.0, "name": "B_C"})
    gsd.execute("catia_gsd_extrude", {"profile": "B_C", "direction": [0, 0, 1],
                                      "limit1": 5.0, "name": "B_SURF"})

    def box(label: str) -> None:
        payload = json.loads(measurement.execute("catia_get_bounding_box",
                                                 {"method": "mesh"}))
        warnings = payload.get("warnings") or []
        say(f"4 {label:<18}: z max {payload['max']['z']}  sets {payload['geometrical_sets']}"
            f"  warnings {len(warnings)}  scope {payload['box_scope'][:60]}")

    box("surface visible")
    part_design.execute("catia_set_visibility", {"name": "GSD_Set", "show": False})
    box("set hidden")

    say("\n── 5. delete: cascade and batch ──")
    fresh("V0350_DEL", document, conn)
    geoset = gsd._get_geoset()
    gsd.execute("catia_gsd_spline",
                {"points": [[i * 10.0, 0, 0] for i in range(6)], "name": "D_SP"})
    say(f"5a after spline        : {geoset.HybridShapes.Count} element(s)")
    say("5b delete, no cascade  : " + part_design.execute(
        "catia_delete_feature", {"feature_name": "D_SP"}))
    say(f"   elements now        : {geoset.HybridShapes.Count}")
    gsd.execute("catia_gsd_spline",
                {"points": [[i * 10.0, 5, 0] for i in range(4)], "name": "D_SP2"})
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 20], "support_plane": "xy",
                                     "radius": 3.0, "name": "D_C"})
    say(f"5c rebuilt + circle    : {geoset.HybridShapes.Count} element(s)")
    say("5d batch with cascade  : " + part_design.execute(
        "catia_delete_feature", {"names": ["D_SP2", "D_C"], "cascade": True}))
    say(f"   elements now        : {geoset.HybridShapes.Count}")

    say("\n── 6. catia_diagnose: sheet metal probe ──")
    info = json.loads(diagnostics.execute("catia_diagnose", {}))
    probe = info.get("sheetmetal", {})
    say(f"6 installed: {probe.get('installed')}")
    say(f"  files    : {probe.get('files')}")
    say(f"  folder   : {probe.get('workbench_folder')}")

    say("\nclosing probe documents")
    close_all(document, conn)
    say(f"documents open at the end: {conn.documents.Count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
