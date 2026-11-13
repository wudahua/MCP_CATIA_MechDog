"""0.3.118 acceptance on B30: batch engrave, one cutter body, one boolean.

The probe (b30_v0420_probe_batch_engrave.py) established WHICH route works:
catia_gsd_join refuses three disjoint tubes, one tool body holding three
CloseSurfaces does not. This script checks the shipped tool against that
measurement, one fresh document per cell.

    1  three traces in one call      volume drops by the control's 37.699 mm3
    2  the tree afterwards           ONE 移除 feature, no leftover cutter body
    3  a typo in the list            refused before anything is built
    4  curves=[one] vs curve=one     the two paths agree on the same trace

    .venv/Scripts/python.exe scripts/b30_v0420_audit.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0420")
ROOT = os.path.join(_PROBE_ROOT, "audit")

TRACES = ((5.0, 55.0, 8.0), (5.0, 55.0, 20.0), (5.0, 55.0, 32.0))
RESULTS: list[tuple[int, str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, title: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, title, bool(passed), detail))
    say(f"  [{'PASS' if passed else 'FAIL'}] {number}. {title}"
        + (f"  ({detail})" if detail else ""))


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


def block(conn, documents, sketcher, part_design, name: str) -> str:
    path = os.path.join(ROOT, f"{name}.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    for index in range(1, conn.documents.Count + 1):
        candidate = conn.documents.Item(index)
        if candidate.Name == f"{name}.CATPart":
            candidate.Activate()
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 60, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})
    return path


def volume(conn, part_design):
    part = conn.get_active_part()
    return part_design._body_volume_mm3(part, part.MainBody)


def features(part_design) -> dict:
    """The tree, as data: the reply is JSON, so a substring count is not a test."""
    raw = part_design.execute("catia_list_features", {})
    return raw if isinstance(raw, dict) else json.loads(raw)


def names_of(payload: dict) -> list[str]:
    return [str(item.get("name")) for item in payload.get("features") or []]


def traces(gsd, part_design, conn, name: str, count: int = 3) -> list[str]:
    made = []
    for index, (x1, x2, y) in enumerate(TRACES[:count], start=1):
        gsd.execute("catia_gsd_line",
                    {"point1": [x1, y, 10], "point2": [x2, y, 10],
                     "name": f"{name}{index}"})
        made.append(f"{name}{index}")
    return made


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    gsd = GSDTools(conn)

    # ── 1 + 2: three traces, one call ────────────────────────────────────
    say("")
    say("── 1: three traces in ONE call ──")
    block(conn, documents, sketcher, part_design, "Batch")
    before = volume(conn, part_design)
    curves = traces(gsd, part_design, conn, "T")
    reply = gsd.execute("catia_surface_engrave",
                        {"curves": curves, "depth": 0.4, "name": "Word"})
    after = volume(conn, part_design)
    dropped = before - after
    say("  " + " ".join(str(reply).split())[:420])
    check(1, "one call removed the same 37.699 mm3 the control measured",
          abs(dropped - 37.699) < 0.01, f"{before:.1f} -> {after:.1f}")

    say("")
    say("── 2: what the tree holds afterwards ──")
    names = names_of(features(part_design))
    say(f"  features: {names}")
    bodies = part_design.execute("catia_list_bodies", {})
    bodies = bodies if isinstance(bodies, dict) else json.loads(bodies)
    listed = [str(item.get("name")) for item in bodies.get("bodies") or []]
    say(f"  bodies: {listed}")
    removes = [name for name in names if name.startswith("移除")]
    check(2, "exactly ONE boolean feature and no leftover cutter body",
          len(removes) == 1 and listed == ["零件几何体"],
          f"removes={removes}, bodies={listed}")

    # 0.3.118 nearly shipped "the body touches the material and still removes
    # nothing" on a call that had just removed 37.699 mm3: the gap is 0.0000 by
    # definition when the curve lies on the surface. The sentence belongs to the
    # failure path only, and this pins that.
    check(3, "a successful call does not call its own curves grazing",
          "GRAZE" not in str(reply) and "still removes nothing" not in str(reply),
          "the grazing sentence stays in the failure path")

    # ── 3: a typo costs nothing ─────────────────────────────────────────
    say("")
    say("── 3: one bad name in the list ──")
    block(conn, documents, sketcher, part_design, "Typo")
    before = volume(conn, part_design)
    curves = traces(gsd, part_design, conn, "U", count=2)
    try:
        gsd.execute("catia_surface_engrave",
                    {"curves": curves + ["NoSuchElement"], "depth": 0.4,
                     "name": "Ty"})
        check(4, "a bad name is refused", False, "the call returned")
    except Exception as error:  # noqa: BLE001 - the refusal is the point
        message = " ".join(str(error).split())
        say("  refused: " + message[:220])
        after = volume(conn, part_design)
        check(4, "a bad name is refused before anything is built",
              "NoSuchElement" in message and before == after
              and "Ty" not in features(part_design),
              f"volume {before:.1f} -> {after:.1f}")

    # ── 4: curves=[one] and curve=one agree ─────────────────────────────
    say("")
    say("── 4: the same trace both ways ──")
    block(conn, documents, sketcher, part_design, "OneA")
    before = volume(conn, part_design)
    gsd.execute("catia_gsd_line",
                {"point1": [5.0, 20.0, 10], "point2": [55.0, 20.0, 10],
                 "name": "Solo"})
    gsd.execute("catia_surface_engrave",
                {"curve": "Solo", "depth": 0.4, "name": "Solo"})
    single = before - volume(conn, part_design)

    block(conn, documents, sketcher, part_design, "OneB")
    before = volume(conn, part_design)
    gsd.execute("catia_gsd_line",
                {"point1": [5.0, 20.0, 10], "point2": [55.0, 20.0, 10],
                 "name": "Solo"})
    gsd.execute("catia_surface_engrave",
                {"curves": ["Solo"], "depth": 0.4, "name": "Solo"})
    listed = before - volume(conn, part_design)
    say(f"  curve= removed {single:.4f} mm3, curves=[one] removed {listed:.4f} mm3")
    check(5, "curve= and curves=[one] remove the same material",
          abs(single - listed) < 0.001, f"{single:.4f} vs {listed:.4f}")

    say("")
    failed = [row for row in RESULTS if not row[2]]
    say(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        for number, title, _, detail in failed:
            say(f"  FAILED {number}: {title} ({detail})")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
