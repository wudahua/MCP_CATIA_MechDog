"""The two P0s of the 2026-11-13 report, asked on B30 with one document each.

A  a batch delete of a PAIR of one-sided pockets (the reporter's own shape): the
   part must be healthy afterwards and the reply must not list healthy features as
   casualties
B  a delete that breaks a real dependency (pad removed under a pocket): the reply
   must describe a TERMINAL state and must not order the caller to delete more
C  set_view('isometric') - readback is normalised by CATIA, so the comparison
   must be an angle, not a component
D  a non-unit direction ([0.45, 1, -0.18]) - same reason

    .venv/Scripts/python.exe scripts/b30_v0424_probe_delete_and_view.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0424")
ROOT = os.path.join(_PROBE_ROOT, "delete_view")


def say(line: str = "") -> None:
    print(line, flush=True)


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


def fresh(conn, documents, name):
    path = os.path.join(ROOT, f"{name}.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    for index in range(1, conn.documents.Count + 1):
        if conn.documents.Item(index).Name == f"{name}.CATPart":
            conn.documents.Item(index).Activate()
    return conn.get_active_part()


def feature_names(part) -> list[str]:
    shapes = part.MainBody.Shapes
    return [str(shapes.Item(i).Name) for i in range(1, shapes.Count + 1)]


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)
    documents = DocumentTools(conn)
    export = ExportTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)

    # ── A: the reporter's own pair, deleted in ONE batch ─────────────────
    say("")
    say("── A: a mid-plane pocket pair (凹槽.1 + 凹槽.2), one batch call ──")
    part = fresh(conn, documents, "DelPair")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 40, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 5})
    sketcher.execute("catia_sketch_circle", {"cx": 20, "cy": 20, "radius": 5})
    sketcher.execute("catia_close_sketch", {})
    say("  " + part_design.execute(
        "catia_pocket", {"depth": 6, "direction": "both"})[:150])
    names = feature_names(part)
    say("  features now: " + ", ".join(names))
    pair = [name for name in names if name.startswith("凹槽")]
    reply = part_design.execute("catia_delete_feature", {"names": pair})
    text = " ".join(str(reply).split())
    say("  " + text[:400])
    volume = part_design._body_volume_mm3(part, part.MainBody)
    say(f"  survived: {', '.join(feature_names(part))}; volume "
        + (f"{volume:.1f} mm3 (16000.0 is the untouched block)"
           if volume is not None else "unreadable"))
    say("  says 'delete those too': " + str("delete those too" in text)
        + ";  says ONE update: " + str("ONE update at the end" in text))
    say("  update: " + " ".join(str(
        measurement.execute("catia_update_part", {})).split())[:120])

    # ── B: a delete that really does break the next update ───────────────
    say("")
    say("── B: delete the swept surface a CloseSurface references ──")
    # CATIA REFUSES the earlier idea: deleting a pad under a pocket raises
    # '方法 Delete 失败' inside the selection, so that path cannot produce a
    # terminal update failure at all. The one measured to break the update is a
    # GSD element that a Part Design feature still references (2026-11-13).
    part = fresh(conn, documents, "DelBreak")
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 0], "point2": [50, 0, 0], "name": "Spine"})
    gsd.execute("catia_solid_tube",
                {"curve": "Spine", "radius": 5, "name": "Neck"})
    names = feature_names(part)
    say("  features: " + ", ".join(names))
    reply = part_design.execute("catia_delete_feature",
                                {"feature_name": "Neck_T"})
    text = " ".join(str(reply).split())
    say("  " + text[:520])
    say("  says 'delete those too': " + str("delete those too" in text)
        + ";  says TERMINAL: " + str("TERMINAL" in text))
    try:
        say("  update after: " + " ".join(str(
            measurement.execute("catia_update_part", {})).split())[:160])
    except Exception as error:  # noqa: BLE001 - a broken part is the point here
        say("  update after REFUSED: " + " ".join(str(error).split())[:200])

    # ── C / D: the view read-back ────────────────────────────────────────
    say("")
    say("── C: set_view('isometric') ──")
    part = fresh(conn, documents, "ViewIso")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 40, "y2": 20})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})
    reply = export.execute("catia_set_view", {"view": "isometric"})
    say("  " + " ".join(str(reply).split())[:360])

    say("")
    say("── D: direction=[0.45, 1, -0.18], up=[0, 0, 1] ──")
    reply = export.execute("catia_set_view",
                           {"direction": [0.45, 1.0, -0.18], "up": [0, 0, 1]})
    say("  " + " ".join(str(reply).split())[:360])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
