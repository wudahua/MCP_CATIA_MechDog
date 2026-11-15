"""0.3.131 acceptance on B30 - the 2026-11-13 report's P1-5 and P2-3.

Every case is one fresh document, and every coordinate is checked against the
geometry it was measured on - a sample that is merely "reported" proves nothing.

    1  straight edges   five points on each 40/60 mm edge, exactly 10/15 mm apart
    2  arc edges        after R5 on four corners: 18/18 edges accept a point
    3  closed rim       a padded circle's two rims refuse, marked, no point invented
    4  no leftovers     the scratch set is gone and the volume is unchanged
    5  the cap          12 edges x 50 points is refused with the numbers in it
    6  screenshot zoom  the camera takes it, the picture changes, the zoom returns

    .venv/Scripts/python.exe -u scripts/b30_v0433_audit.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0433")
CHECKS: list[tuple[int, str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, title: str, passed: bool, detail: str = "") -> None:
    CHECKS.append((number, title, bool(passed), detail))
    say(f"  [{'PASS' if passed else 'FAIL'}] {number}. {title}"
        + (f"  ({detail})" if detail else ""))


def close_ours(conn) -> None:
    root = os.path.abspath(ROOT).lower()
    for index in range(int(conn.documents.Count), 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = str(doc.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).lower().startswith(root):
            try:
                doc.Close()
            except Exception:  # noqa: BLE001
                pass


def fresh(conn, docs, name: str) -> str:
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, name + ".CATPart")
    close_ours(conn)
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    return path


def build_plate(conn, docs, sketcher, parts, name: str):
    fresh(conn, docs, name)
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 60, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 10})
    return conn.get_active_part()


def main() -> int:
    say("0.3.131 acceptance - 2026-11-13 report, P1-5 (samples) and P2-3 (zoom)")
    conn = CATIAConnection()
    conn.ensure_connected()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    measure = MeasurementTools(conn)
    export = ExportTools(conn)

    def volume() -> float:
        payload = json.loads(measure.execute("catia_get_inertia", {}))
        return float(payload.get("volume_mm3") or 0.0)

    # 1 - straight edges ----------------------------------------------------
    say("\n[1] five points along each straight edge")
    part = build_plate(conn, docs, sketcher, parts, "V0433_Straight")
    before_sets = int(part.HybridBodies.Count)
    before_volume = volume()
    payload = json.loads(parts.execute("catia_list_edges",
                                       {"min_length": 39, "samples": 5,
                                        "include_nls": False}))
    edges = payload.get("edges") or []
    steps = []
    for edge in edges:
        points = edge.get("samples_mm") or []
        steps.extend(round(((points[i + 1][0] - points[i][0]) ** 2
                            + (points[i + 1][1] - points[i][1]) ** 2
                            + (points[i + 1][2] - points[i][2]) ** 2) ** 0.5, 3)
                     for i in range(len(points) - 1))
    check(1, "every 40/60 mm edge got five points, evenly spaced",
          len(edges) == 8 and all(len(e.get("samples_mm") or []) == 5 for e in edges)
          and set(steps) <= {10.0, 15.0},
          f"{len(edges)} edges, step sizes {sorted(set(steps))}")

    # 2 - arc edges --------------------------------------------------------
    say("\n[2] the same on arc edges (R5 on four corners)")
    part = build_plate(conn, docs, sketcher, parts, "V0433_Arc")
    parts.execute("catia_fillet", {"radius": 5})
    payload = json.loads(parts.execute("catia_list_edges",
                                       {"samples": 4, "include_nls": False}))
    edges = payload.get("edges") or []
    missing = [e["name"] for e in edges if not e.get("samples_mm")]
    check(2, "all 18 edges accept a point, arcs included",
          len(edges) == 18 and not missing,
          f"{len(edges) - len(missing)}/{len(edges)} edges, missing {missing[:3]}")

    # 3 - a closed rim -----------------------------------------------------
    say("\n[3] a padded circle's rims refuse - and say so")
    fresh(conn, docs, "V0433_Circle")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": 10})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 10})
    part = conn.get_active_part()
    payload = json.loads(parts.execute("catia_list_edges",
                                       {"samples": 4, "include_nls": False}))
    edges = payload.get("edges") or []
    check(3, "a closed rim carries samples_unavailable instead of a point",
          len(edges) == 2
          and all("samples_unavailable" in edge for edge in edges)
          and all("samples_mm" not in edge for edge in edges),
          f"{[edge.get('samples_unavailable', '')[:40] for edge in edges]}")

    # 4 - nothing left behind ---------------------------------------------
    say("\n[4] the scratch set is gone and the solid is unchanged")
    part = build_plate(conn, docs, sketcher, parts, "V0433_Clean")
    before_sets = int(part.HybridBodies.Count)
    before_volume = volume()
    parts.execute("catia_list_edges", {"samples": 5, "include_nls": False})
    after_sets = int(part.HybridBodies.Count)
    after_volume = volume()
    check(4, "no geometrical set and no volume change after sampling",
          after_sets == before_sets and abs(after_volume - before_volume) < 1e-6,
          f"sets {before_sets} -> {after_sets}, volume {before_volume:.3f} -> "
          f"{after_volume:.3f}")

    # 5 - the cap ----------------------------------------------------------
    say("\n[5] 12 edges x 50 points is refused, with the numbers in the message")
    refused = ""
    try:
        parts.execute("catia_list_edges", {"samples": 50, "include_nls": False})
    except Exception as error:  # noqa: BLE001 - the refusal is the result
        refused = str(error)
    check(5, "the cap is stated in the refusal",
          "600" in refused and "400" in refused,
          refused.splitlines()[0][:120] if refused else "it was NOT refused")

    # 6 - screenshot zoom --------------------------------------------------
    say("\n[6] screenshot zoom: taken, changed, and put back")
    part = build_plate(conn, docs, sketcher, parts, "V0433_Zoom")
    viewpoint = conn.active_window.ActiveViewer.Viewpoint3D
    base = float(viewpoint.Zoom)
    plain = os.path.join(ROOT, "zoom_1.jpg")
    zoomed = os.path.join(ROOT, "zoom_16.jpg")
    for path in (plain, zoomed):
        if os.path.exists(path):
            os.remove(path)
    plain_reply = export.execute("catia_screenshot", {"file_path": plain})
    zoomed_reply = export.execute("catia_screenshot",
                                  {"file_path": zoomed, "zoom": 1.6})
    plain_bytes = open(plain, "rb").read() if os.path.exists(plain) else b""
    zoomed_bytes = open(zoomed, "rb").read() if os.path.exists(zoomed) else b""
    check(6, "the reply states the zoom, and the two captures differ",
          "[zoom" in zoomed_reply and "[zoom" not in plain_reply
          and hashlib.sha256(plain_bytes).digest()
          != hashlib.sha256(zoomed_bytes).digest(),
          zoomed_reply[-60:])
    # Relative, not absolute: CATIA quantises Zoom when it stores it, so putting
    # back the exact float it handed out can read back one ulp away. 1e-6 of the
    # value is far below anything that moves a pixel.
    restored = abs(float(viewpoint.Zoom) - base) <= max(1e-9, abs(base) * 1e-6)
    check(6, "the camera's zoom is where it was before the capture", restored,
          f"{base:.9f} -> {float(viewpoint.Zoom):.9f}")

    say("\n" + "-" * 70)
    failed = [row for row in CHECKS if not row[2]]
    say(f"{len(CHECKS) - len(failed)}/{len(CHECKS)} passed")
    for number, title, _passed, detail in failed:
        say(f"  FAILED {number}. {title} - {detail}")
    close_ours(conn)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
