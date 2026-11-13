"""What one engrave leaves behind, and whether any of it can be deleted.

The 2026-11-12 report (P1-4) counted ~194 intermediate elements after 10 strokes
and asked for ``cleanup=true`` or a prefix purge. Before building either, one
question has to be answered by measurement: AFTER A SUCCESSFUL ENGRAVE, are those
elements still needed by the boolean that did the cutting?

There is already a cleanup path - ``_engrave_cleanup`` - and it only runs when the
call FAILS. Reusing it on success is only legitimate if deleting the helpers leaves
a part that still updates and holds the same volume.

Cell 0 builds one groove and lists every name it created, so the later cells can
name what they delete. Each cell gets its own document: a failed update is
document-wide.

    .venv/Scripts/python.exe scripts/b30_v0419_probe_engrave_cleanup.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0419")
ROOT = os.path.join(_PROBE_ROOT, "engrave")


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


def build(documents, sketcher, part_design, gsd, conn, name: str) -> float:
    """A 60x40x10 block with one 50 mm line lying on its top face."""
    path = os.path.join(ROOT, f"{name}.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    activate(conn, f"{name}.CATPart")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 60, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})
    gsd.execute("catia_gsd_line",
                {"point1": [5, 20, 10], "point2": [55, 20, 10], "name": "Trace"})
    return 0.0


def volume(part_design, conn):
    try:
        part = conn.get_active_part()
        body = part.MainBody
        return part_design._body_volume_mm3(part, body)
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)

    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    gsd = GSDTools(conn)
    meas = MeasurementTools(conn)

    # ── cell 0: one groove, then list what it left ─────────────────────────
    say("")
    say("── cell 0: one groove, and the names it created ──")
    build(documents, sketcher, part_design, gsd, conn, "EG0")
    before_volume = volume(part_design, conn)
    reply = gsd.execute("catia_surface_engrave",
                        {"curve": "Trace", "depth": 0.4, "name": "EG0"})
    say("  " + " ".join(str(reply).split())[:220])
    say(f"  volume {before_volume} -> {volume(part_design, conn)}")
    try:
        listing = gsd.execute("catia_gsd_list_elements", {})
        say("  gsd elements: " + " ".join(str(listing).split())[:900])
    except Exception as error:  # noqa: BLE001
        say("  list_elements failed: " + str(error).splitlines()[0][:120])
    try:
        features = part_design.execute("catia_list_features", {})
        say("  features: " + " ".join(str(features).split())[:400])
    except Exception as error:  # noqa: BLE001
        say("  list_features failed: " + str(error).splitlines()[0][:120])

    # ── cells A-D: is any helper still required by the boolean? ────────────
    # One document per cell, because a failed update is document-wide. The
    # question is not "does delete raise" but "does the tree still update, and
    # does the solid still hold the volume the groove gave it".
    for cell, victim in (("A", "EG0_T"), ("B", "EG0_N"), ("C", "Trace_at0.5"),
                         ("D", "EG0_P")):
        say("")
        say(f"── cell {cell}: delete '{victim}' after a successful groove ──")
        build(documents, sketcher, part_design, gsd, conn, f"EG{cell}")
        gsd.execute("catia_surface_engrave",
                    {"curve": "Trace", "depth": 0.4, "name": "EG0"})
        engraved = volume(part_design, conn)
        try:
            answer = part_design.execute("catia_delete_feature",
                                          {"feature_name": victim})
            say("  delete: " + " ".join(str(answer).split())[:150])
        except Exception as error:  # noqa: BLE001
            say("  delete REFUSED: " + str(error).splitlines()[0][:150])
        try:
            update = meas.execute("catia_update_part", {})
            say("  update: " + " ".join(str(update).split())[:180])
        except Exception as error:  # noqa: BLE001
            say("  update RAISED: " + str(error).splitlines()[0][:180])
        after = volume(part_design, conn)
        say(f"  volume {engraved} -> {after}")

    # ── cell E: they cannot be DELETED - can they be HIDDEN? ───────────────
    # Hiding is a display state, not a dependency, so it should not break the
    # boolean - but 0.3.115 measured a solid vanishing when its inputs were
    # hidden, so this is measured rather than assumed, with a picture either way.
    say("")
    say("── cell E: hide the four helpers after a successful groove ──")
    from catia_mcp.tools.export import ExportTools

    exporter = ExportTools(conn)
    build(documents, sketcher, part_design, gsd, conn, "EGE")
    gsd.execute("catia_surface_engrave",
                {"curve": "Trace", "depth": 0.4, "name": "EG0"})
    engraved = volume(part_design, conn)

    def shot(tag: str) -> int:
        path = os.path.join(ROOT, f"hide_{tag}.jpg")
        if os.path.exists(path):
            os.remove(path)
        try:
            exporter.execute("catia_screenshot", {"file_path": path})
        except Exception as error:  # noqa: BLE001
            say("  shot failed: " + str(error).splitlines()[0][:80])
            return -1
        return os.path.getsize(path) if os.path.exists(path) else -1

    say(f"  shot with helpers: {shot('0_before')} B")
    for helper in ("EG0_T", "EG0_P", "EG0_N", "Trace_at0.5"):
        try:
            answer = part_design.execute("catia_set_visibility",
                                          {"name": helper, "show": False})
            say(f"  hide {helper:14}: " + " ".join(str(answer).split())[:90])
        except Exception as error:  # noqa: BLE001
            say(f"  hide {helper:14}: RAISED " + str(error).splitlines()[0][:90])
    say(f"  shot hidden      : {shot('1_hidden')} B")
    try:
        update = meas.execute("catia_update_part", {})
        say("  update: " + " ".join(str(update).split())[:160])
    except Exception as error:  # noqa: BLE001
        say("  update RAISED: " + str(error).splitlines()[0][:160])
    say(f"  volume {engraved} -> {volume(part_design, conn)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
