"""0.3.110 acceptance - a projection after a REOPEN, and what actually refuses.

The 2026-11-11 seventh report (§3) said it got PROJECT_FAILED three times on
"a document reopened from disk with InWorkObject pointing at a GSD_Set", and
asked for an acceptance case that separates that from a fresh document. The
case already existed since 0.3.101 (scripts/b30_v0401_audit.py, cells 1-4), but
it only covered one configuration. This script separates the two variables the
report never separated:

  1  fresh, body IO         sketch -> lofted Wall          accepted, share read
  2  reopened, body IO      same sketch -> same Wall        same share
  3  reopened, GSD set IO   same sketch -> same Wall        same share
  4  reopened, GSD set IO   sketch -> the face it lies ON   accepted (0.0000 mm)
  5  reopened, GSD set IO   sketch -> a curve EXTRACTED from that face
  6  after the reopen the sketch's own curves can still be measured

Every cell is its own document. Nothing is written outside F:\\catia_probe_v0410.
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection          # noqa: E402
from catia_mcp.tools.document import DocumentTools       # noqa: E402
from catia_mcp.tools.gsd import GSDTools                 # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools       # noqa: E402

ROOT = r"F:\catia_probe_v0410\audit"
RADIUS_LO, RADIUS_HI, HEIGHT = 30.0, 24.0, 100.0
RESULTS: list[tuple[int, str, bool, str]] = []


def say(text: str = "") -> None:
    print(text, flush=True)


def check(number: int, label: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, label, passed, detail))
    say(f"[{'OK ' if passed else 'FAIL'}] {number}. {label}"
        + (f"  |  {detail}" if detail else ""))


def close_ours(conn: CATIAConnection) -> None:
    for index in range(int(conn.app.Documents.Count), 0, -1):
        document = conn.app.Documents.Item(index)
        try:
            full = str(document.FullName or "")
        except Exception:                                     # noqa: BLE001
            full = ""
        if full and os.path.abspath(full).startswith(os.path.abspath(ROOT)):
            try:
                document.Close()
            except Exception:                                 # noqa: BLE001
                pass


def share_of(text: str) -> float | None:
    found = re.search(r"([\d.]+) % of the ([\d.]+) mm", text)
    return float(found.group(1)) if found else None


def build_wall(tag: str, conn, docs, gsd, sk) -> tuple[str, str]:
    path = os.path.join(ROOT, f"{tag}.CATPart")
    docs.execute("catia_new_part", {"file_path": path, "overwrite": True})
    gsd.execute("catia_gsd_create_geoset", {"name": "ProbeSet"})
    gsd.execute("catia_gsd_point", {"x": 0, "y": 0, "z": 0, "name": "B0"})
    gsd.execute("catia_gsd_circle", {"center": "B0", "support_plane": "xy",
                                     "radius": RADIUS_LO, "name": "B_lo"})
    gsd.execute("catia_gsd_point", {"x": 0, "y": 0, "z": HEIGHT, "name": "B1"})
    gsd.execute("catia_gsd_circle", {"center": "B1", "support_plane": "xy",
                                     "radius": RADIUS_HI, "name": "B_hi"})
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["B_lo", "B_hi"], "name": "Wall"})
    gsd.execute("catia_gsd_close_surface", {"surface": "Wall"})
    sk.execute("catia_create_sketch", {"plane": "zx", "offset": -45})
    sk.execute("catia_sketch_strokes",
               {"strokes": [[[0, 0], [40, 0], [40, 10], [0, 10], [0, 0]]]})
    sk.execute("catia_close_sketch", {})
    return path, str(conn.last_sketch.Name)


def build_box(tag: str, conn, docs, sk, pd) -> tuple[str, str]:
    """A 40x40 pad whose base face is the sketch's own plane."""
    path = os.path.join(ROOT, f"{tag}.CATPart")
    docs.execute("catia_new_part", {"file_path": path, "overwrite": True})
    sk.execute("catia_create_sketch", {"plane": "zx", "offset": -45})
    sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 40, "y2": 40})
    sk.execute("catia_close_sketch", {})
    pd.execute("catia_pad", {"height": 20})
    return path, str(conn.last_sketch.Name)


def reopen(path: str, conn) -> None:
    conn.active_document.Save()
    conn.active_document.Close()
    conn.app.Documents.Open(path)


def set_io(conn, geoset: bool) -> str:
    part = conn.get_active_part()
    if geoset:
        try:
            target = part.HybridBodies.Item("ProbeSet")
        except Exception:                                     # noqa: BLE001
            target = part.HybridBodies.Add()
            target.Name = "ProbeSet"
        part.InWorkObject = target
    else:
        # Without this the document keeps whatever GSD left as InWorkObject, so
        # "body IO" would silently be the same cell as "set IO".
        part.InWorkObject = part.MainBody
    return str(part.InWorkObject.Name)


def project(gsd, sketch: str, support: str, name: str) -> tuple[bool, str, str | None]:
    try:
        reply = gsd.execute("catia_gsd_project",
                            {"element": sketch, "support": support, "name": name})
        return True, " ".join(str(reply).split()), None
    except Exception as caught:                               # noqa: BLE001
        return False, " ".join(str(caught).split()), str(
            getattr(caught, "code", type(caught).__name__))


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    docs, sk, gsd, pd = (DocumentTools(conn), SketcherTools(conn),
                         GSDTools(conn), PartDesignTools(conn))

    say("0.3.110 acceptance - the projection after a reopen")
    say("=" * 78)

    # ── 1: the control - a fresh document ──────────────────────────────
    path, sketch = build_wall("A", conn, docs, gsd, sk)
    say(f"cell A: sketch '{sketch}' in a fresh document")
    ok, text, code = project(gsd, sketch, "Wall", "P_A")
    fresh_share = share_of(text) if ok else None
    check(1, "fresh document: the sketch projects onto the lofted Wall",
          ok and fresh_share is not None and 80.0 < fresh_share < 90.0,
          f"accepted={ok} code={code} share={fresh_share}")

    # ── 2: same thing after save / close / reopen ─────────────────────
    reopen(path, conn)
    io_name = set_io(conn, False)
    ok, text, code = project(gsd, sketch, "Wall", "P_B")
    reopened_share = share_of(text) if ok else None
    check(2, "reopened, InWorkObject = body: the same projection is accepted",
          ok and reopened_share is not None
          and fresh_share is not None and abs(reopened_share - fresh_share) < 0.6,
          f"InWorkObject={io_name} share {fresh_share} -> {reopened_share} "
          f"(code={code})")

    # ── 3: reopened AND InWorkObject pointing at the GSD set ──────────
    io_name = set_io(conn, True)
    ok, text, code = project(gsd, sketch, "Wall", "P_C")
    set_share = share_of(text) if ok else None
    check(3, "reopened, InWorkObject = the GSD set: still accepted",
          ok and set_share is not None
          and fresh_share is not None and abs(set_share - fresh_share) < 0.6,
          f"InWorkObject={io_name} share {set_share} (code={code})")
    check(4, "the reply still names the source length's origin across the reopen",
          ok and "sketch's" in text, text[:170])

    # ── 5: a sketch projected onto the face it lies ON (0.0000 mm) ────
    path_box, box_sketch = build_box("D", conn, docs, sk, pd)
    reopen(path_box, conn)
    io_name = set_io(conn, True)
    faces = json.loads(pd.execute("catia_list_faces", {}))["faces"]
    base = faces[0]["name"] if faces else "Face.1"
    ok, text, code = project(gsd, box_sketch, base, "P_D")
    box_share = share_of(text) if ok else None
    check(5, "a sketch lying ON the face it is projected to is accepted",
          ok and box_share is not None and box_share > 95.0,
          f"support={base} area={faces[0].get('area_mm2') if faces else '?'} "
          f"accepted={ok} share={box_share} (code={code})")

    # ── 6: a support that is a curve EXTRACTED from that face ─────────
    extracted = None
    try:
        edge = json.loads(pd.execute("catia_list_edges", {}))["edges"][0]["name"]
        gsd.execute("catia_gsd_extract", {"element": edge, "name": "FaceCurve"})
        extracted = "FaceCurve"
    except Exception as caught:                               # noqa: BLE001
        say(f"could not extract an edge: {type(caught).__name__}: {caught}")
    ok, text, code = project(gsd, box_sketch, extracted or "Face.1", "P_E")
    # Whatever the answer, it must be an answer: a receipt or a structured code,
    # never a bare COM error. That is the defect the report described.
    structured = code in {None, "PRECONDITION_REJECTED", "PROJECT_FAILED"}
    check(6, "an extracted curve as support answers in words, not a bare COM error",
          extracted is not None and structured,
          f"support={extracted} accepted={ok} code={code} "
          f"share={share_of(text) if ok else None} :: {'' if ok else text[:300]}")

    # ── 6b: and it SAYS why, with a number the caller can check ───────
    check(7, "the refusal carries a measured distance, not just a code",
          ok or ("mm from" in text and "Measured after the refusal" in text),
          ("" if ok else text[:300]))

    # ── 7: the cross-session source measurement still works ───────────
    # Back to the Wall document: the box document is still active, and its
    # 'Sketch.1' is a 40x40 rectangle whose outline is 160 mm - measuring that
    # instead of the 100 mm one is how a test passes with the wrong number.
    reopen(path, conn)
    measured = gsd._sketch_outline_length_mm(sketch)
    total = measured[0] if measured else None
    check(8, "after the reopen the sketch's own outline is still measurable",
          total is not None and abs(total - 100.0) < 0.05,
          f"{total:.4f} mm (hand 100.000)")

    say("")
    passed = sum(1 for _, _, okay, _ in RESULTS if okay)
    say(f"{passed}/{len(RESULTS)} passed")
    for number, label, okay, _ in RESULTS:
        if not okay:
            say(f"  failed: {number}. {label}")
    try:
        conn.active_document.Close()
    except Exception:                                         # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
