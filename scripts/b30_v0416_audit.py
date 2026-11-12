"""0.3.114 acceptance on B30: the four things this release changed.

Each check asserts BOTH the geometry and the words the tool sent back - a reply
that says "closed" while the sketch holds 8 breaks is the defect this release is
about, and a reply that says "refused" while the pad exists is the other one.

    1  a profile given out of order is not reported as broken, and pads to the
       analytic volume (the 2026-11-13 report's problem 6)
    2  curve_on_surface says so when every point moved the same distance - the
       signature of an outline that landed on a wall it was not drawn against
    3  catia_text_strokes returns a structured box (the report parsed prose)
    4  catia_split_solid cuts a tip off along an INCLINED plane: 20 353 mm3
    5  on an assembly, method='auto' now answers like method='mesh'

    .venv/Scripts/python.exe scripts/b30_v0416_audit.py
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0416")
ROOT = os.path.join(_PROBE_ROOT, "audit")
RESULTS: list[tuple[int, str, bool, str]] = []


def say(text: str = "") -> None:
    print(text, flush=True)


def check(number: int, what: str, okay: bool, detail: str = "") -> None:
    RESULTS.append((number, what, bool(okay), detail))
    say(f"[{'PASS' if okay else 'FAIL'}] {number}. {what}")
    if detail:
        say(f"        {detail}")


def flat(reply) -> str:
    return " ".join(str(reply).split())


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


def fresh(conn, documents, sketcher, name: str) -> None:
    path = os.path.join(ROOT, name)
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part", {"name": name.split(".")[0],
                                         "file_path": path, "overwrite": True})
    activate(conn, name)
    sketcher.execute("catia_create_sketch", {"plane": "xy"})


def volume(conn) -> float | None:
    part = conn.get_active_part()
    return PartDesignTools(conn)._body_volume_mm3(part, part.MainBody)


SIDES = [
    {"type": "line", "p1": [0, 0], "p2": [40, 0]},
    {"type": "line", "p1": [40, 0], "p2": [40, 20]},
    {"type": "line", "p1": [40, 20], "p2": [0, 20]},
    {"type": "line", "p1": [0, 20], "p2": [0, 0]},
]
SHUFFLED = [SIDES[1], SIDES[3], SIDES[0], SIDES[2]]


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    gsd = GSDTools(conn)
    assembly = AssemblyTools(conn)
    measurement = MeasurementTools(conn)

    # ── 1: the same rectangle, four segments, quoted out of order ───────────
    say("")
    say("--- 1: a closed profile given out of order ---")
    for label, order in (("in order", SIDES), ("shuffled", SHUFFLED)):
        fresh(conn, documents, sketcher, f"Order_{label.replace(' ', '')}.CATPart")
        reply = flat(sketcher.execute("catia_sketch_profile",
                                      {"segments": order}))
        closed = flat(sketcher.execute("catia_close_sketch", {}))
        part_design.execute("catia_pad", {"height": 10})
        got = volume(conn)
        check(1 if label == "in order" else 2,
              f"{label}: no NOT CLOSED, close_sketch closes, pad = 8000 mm3",
              "NOT CLOSED" not in reply
              and "not closed" not in closed.lower()
              and got is not None and abs(got - 8000.0) < 1.0,
              f"profile='{reply[-120:]}' volume={got}")

    # ── 2: curve_on_surface - a uniform 0.6 mm landing says so ──────────────
    say("")
    say("--- 2: every point moved the same distance ---")
    fresh(conn, documents, sketcher, "Snap.CATPart")
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 100, "y2": 20})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})
    # zx is u = Z, v = X, so these are (z, x) with y = -0.6: 0.6 mm in front of
    # the y = 0 wall, well inside x 0..100 and z 0..10.
    snapped = flat(gsd.execute("catia_curve_on_surface", {
        "plane": "zx", "offset": -0.6,
        "strokes": [[[3.0, 10.0], [5.0, 25.0], [3.0, 40.0]]], "name": "Snap"}))
    check(3, "a uniform non-zero landing is called out, not just counted",
          "moved the SAME distance" in snapped and "0.6000 to 0.6000" in snapped,
          snapped[snapped.find("Each of"):][:160])

    # ── 3: text_strokes returns numbers, not a sentence ─────────────────────
    say("")
    say("--- 3: catia_text_strokes(structured=true) ---")
    # text needs a sketch that is OPEN, so open a fresh one on the Tip part.
    fresh(conn, documents, sketcher, "Text.CATPart")
    payload = json.loads(sketcher.execute("catia_text_strokes", {
        "text": "CATIA", "height": 20, "structured": True}))
    box = payload.get("bbox") or {}
    check(4, "structured text carries the box, the centre and the strokes",
          bool(payload.get("strokes")) and box.get("width", 0) > 10
          and "center" in payload and abs(
              payload["center"][0] - (box["h_min"] + box["h_max"]) / 2) < 1e-6,
          f"bbox={box} center={payload.get('center')} "
          f"strokes={len(payload.get('strokes') or [])}")

    # ── 4: a tip cut off along an inclined plane ────────────────────────────
    say("")
    say("--- 4: split by a plane tilted 20 degrees ---")
    fresh(conn, documents, sketcher, "Tip.CATPart")
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 40, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 40})
    rise = 40.0 * math.tan(math.radians(20.0))
    gsd.execute("catia_gsd_plane_3points", {
        "point1": [0.0, 0.0, 20.0], "point2": [40.0, 0.0, 20.0 + rise],
        "point3": [0.0, 40.0, 20.0], "name": "CutPlane"})
    # The block is 0..40 on every axis and the plane is z = 20 + x*tan20, so the
    # part UNDER the plane is 40 * integral(0..40)(20 + x*tan20) = 43 647 mm3 and
    # the part above it is 64 000 - 43 647 = 20 353. 'front' is the side the
    # cutter's normal points to, which for this 3-point plane is the UPPER half -
    # so the expected number is the upper one, and it is stated as such.
    below = 40.0 * (20.0 * 40.0
                    + 0.5 * math.tan(math.radians(20.0)) * 40.0 * 40.0)
    split = flat(part_design.execute("catia_split_solid",
                                     {"cutter": "CutPlane", "keep": "front",
                                      "name": "Tip"}))
    got = volume(conn)
    check(5, "the inclined cut leaves the hand-calculated half",
          got is not None and abs(got - (64000.0 - below)) < 1.0
          and abs(below - 43647.0) < 1.0,
          f"volume={got} want {64000.0 - below:.1f} | {split[:110]}")

    # ── 5: an assembly's default box is the mesh route ──────────────────────
    say("")
    say("--- 5: method='auto' on an assembly ---")
    part_path = os.path.join(ROOT, "BoxA.CATPart")
    asm_path = os.path.join(ROOT, "BoxAsm.CATProduct")
    for stale in (part_path, asm_path):
        if os.path.exists(stale):
            os.remove(stale)
    documents.execute("catia_new_part", {"name": "BoxA", "file_path": part_path,
                                         "overwrite": True})
    activate(conn, "BoxA.CATPart")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 100, "y2": 20})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})
    documents.execute("catia_new_product", {"name": "BoxAsm",
                                            "file_path": asm_path,
                                            "overwrite": True})
    activate(conn, "BoxAsm.CATProduct")
    assembly.execute("catia_add_component", {"file_path": part_path, "count": 1})
    boxes = {}
    for method in ("auto", "mesh", "vertices"):
        boxes[method] = json.loads(measurement.execute(
            "catia_get_bounding_box",
            {"document": "BoxAsm.CATProduct", "method": method}))
    auto_route = boxes["auto"].get("mesh_route", "")
    check(6, "auto answers with mesh on an assembly, and says so",
          boxes["auto"]["dimensions"] == boxes["mesh"]["dimensions"]
          and "mesh" in auto_route and "assembly" in auto_route,
          f"auto={boxes['auto']['dimensions']} route='{auto_route[:90]}'")
    check(7, "and 'vertices' still names itself as the lower-bound route",
          "LOWER bound" in boxes["vertices"].get("mesh_route", ""),
          boxes["vertices"].get("mesh_route", "")[:110])

    say("")
    passed = sum(1 for _n, _w, okay, _d in RESULTS if okay)
    say(f"{passed}/{len(RESULTS)} checks passed")
    for number, what, okay, detail in RESULTS:
        if not okay:
            say(f"  FAILED {number}: {what} | {detail}")


if __name__ == "__main__":
    main()
