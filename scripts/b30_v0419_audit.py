"""0.3.117 on B30: the refusal, its escape hatch, and the engrave helpers.

A  a circle alone cuts (603.2 mm3) - the control, so the refusal is about the
   open curve and not about the sketch
B  the same circle plus an open spline is REFUSED by catia_pocket before CATIA
   runs: code SKETCH_HAS_OPEN_CURVES, no feature, no volume change, sketch intact
C  allow_open_curves=true sends it anyway, and CATIA then removes nothing - which
   is the measurement the refusal rests on, taken through the tool this time
D  an engrave hides its four helpers by default, still updates, and the volume is
   unchanged to the last digit
E  helpers='show' leaves them visible and is equally harmless

    .venv/Scripts/python.exe scripts/b30_v0419_audit.py
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
ROOT = os.path.join(_PROBE_ROOT, "audit")

results: list[tuple[int, str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, title: str, passed: bool, evidence: str) -> None:
    results.append((number, title, bool(passed), evidence))
    say(f"  [{'PASS' if passed else 'FAIL'}] {number}. {title}")
    say(f"         {evidence}")


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


def sketch_names(conn) -> list[str]:
    try:
        body = conn.get_active_part().MainBody
        return [str(body.Sketches.Item(i).Name)
                for i in range(1, int(body.Sketches.Count) + 1)]
    except Exception:  # noqa: BLE001
        return []


def volume(part_design, conn):
    try:
        part = conn.get_active_part()
        return part_design._body_volume_mm3(part, part.MainBody)
    except Exception:  # noqa: BLE001
        return None


def block(documents, sketcher, part_design, conn, name: str) -> None:
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

    # ── A: the control ─────────────────────────────────────────────────────
    say("")
    say("── A: a circle alone ──")
    block(documents, sketcher, part_design, conn, "AU_A")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 10.0})
    sketcher.execute("catia_sketch_circle", {"cx": 30, "cy": 20, "radius": 8})
    sketcher.execute("catia_close_sketch", {})
    before = volume(part_design, conn)
    answer = part_design.execute("catia_pocket", {"depth": 3.0})
    after = volume(part_design, conn)
    removed = (before - after) if (before and after) else None
    check(1, "a closed sketch still cuts",
          removed is not None and abs(removed - 603.2) < 1.0,
          f"removed {removed:.1f} mm3 ({before:.1f} -> {after:.1f})")

    # ── B: the refusal, before CATIA ───────────────────────────────────────
    say("")
    say("── B: the same circle plus an open spline ──")
    block(documents, sketcher, part_design, conn, "AU_B")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 10.0})
    sketcher.execute("catia_sketch_circle", {"cx": 30, "cy": 20, "radius": 8})
    sketcher.execute("catia_sketch_spline",
                     {"points": [[27, 17], [30, 20], [33, 23]]})
    sketcher.execute("catia_close_sketch", {})
    before = volume(part_design, conn)
    sketches_before = sketch_names(conn)
    refused = ""
    try:
        part_design.execute("catia_pocket", {"depth": 3.0})
    except Exception as error:  # noqa: BLE001
        refused = str(error)
    after = volume(part_design, conn)
    check(2, "the pocket is refused before CATIA is asked",
          "SKETCH_HAS_OPEN_CURVES" in refused
          or "free endpoint" in refused,
          " ".join(refused.split())[:150] or "no refusal raised")
    check(3, "...and nothing was built, rolled back or removed",
          before == after and sketch_names(conn) == sketches_before,
          f"volume {before} -> {after}, sketches {sketch_names(conn)}")

    # ── C: the escape hatch, which is how the claim was measured ───────────
    say("")
    say("── C: allow_open_curves=true ──")
    escaped = ""
    try:
        part_design.execute("catia_pocket",
                            {"depth": 3.0, "allow_open_curves": True})
    except Exception as error:  # noqa: BLE001
        escaped = str(error)
    check(4, "with the check bypassed CATIA removes nothing",
          "removed no material" in escaped,
          " ".join(escaped.split())[:150] or "the cut succeeded")

    # ── D: the engrave hides its helpers ───────────────────────────────────
    say("")
    say("── D: engrave, helpers hidden by default ──")
    block(documents, sketcher, part_design, conn, "AU_D")
    gsd.execute("catia_gsd_line",
                {"point1": [5, 20, 10], "point2": [55, 20, 10], "name": "Trace"})
    reply = gsd.execute("catia_surface_engrave",
                        {"curve": "Trace", "depth": 0.4, "name": "AU"})
    text = " ".join(str(reply).split())
    say("  " + text[:260])
    check(5, "the reply names the helpers it hid and says they cannot be deleted",
          "helper element(s) this stroke created are now hidden" in text
          and "'AU_T'" in text and "Do NOT delete them" in text,
          "helpers hidden, refusal to delete explained")
    try:
        update = meas.execute("catia_update_part", {})
        updated = "successfully" in str(update)
    except Exception as error:  # noqa: BLE001
        update, updated = error, False
    check(6, "hiding them leaves the part updating and the volume intact",
          updated and abs((volume(part_design, conn) or 0) - 23987.4336290779) < 0.001,
          f"update: {' '.join(str(update).split())[:60]}, "
          f"volume {volume(part_design, conn)}")

    # ── E: helpers='show' ──────────────────────────────────────────────────
    say("")
    say("── E: helpers='show' ──")
    block(documents, sketcher, part_design, conn, "AU_E")
    gsd.execute("catia_gsd_line",
                {"point1": [5, 20, 10], "point2": [55, 20, 10], "name": "Trace"})
    reply = gsd.execute("catia_surface_engrave",
                        {"curve": "Trace", "depth": 0.4, "name": "AU",
                         "helpers": "show"})
    text = " ".join(str(reply).split())
    check(7, "'show' says shown and still warns about deleting",
          "now shown" in text and "Do NOT delete them" in text,
          "shown, with the same warning")

    say("")
    passed = sum(1 for _n, _t, ok, _e in results if ok)
    say(f"{passed}/{len(results)} checks passed")
    for number, title, ok, _evidence in results:
        if not ok:
            say(f"  FAILED: {number}. {title}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
