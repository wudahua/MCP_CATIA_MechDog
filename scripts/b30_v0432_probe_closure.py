"""Does the imported profile actually pad? The 2026-11-14 report's P0.

One fresh document per case. The volume is judged against the contour's OWN area
times the height, computed here from the same pieces the importer drew - so the
check is "did CATIA extrude what the drawing says", not "did some number match".

    python -u scripts/b30_v0432_probe_closure.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp import blueprint  # noqa: E402
from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.blueprint import BlueprintTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402

PDF = "F:/WokrBuddy Catia Space/苹果入耳式耳机AirPods/苹果入耳式耳机AirPods尺寸平面图.pdf"
ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0432")
CHECKS: list[tuple[int, str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, title: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((number, title, bool(ok), detail))
    say(f"  [{'PASS' if ok else 'FAIL'}] {number:2}. {title}"
        + (f"   ({detail})" if detail else ""))


def fresh(conn, documents, name: str):
    path = os.path.join(ROOT, name + ".CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    for index in range(1, int(conn.documents.Count) + 1):
        if conn.documents.Item(index).Name == name + ".CATPart":
            conn.documents.Item(index).Activate()
    return conn.get_active_part()


def clear_ours(conn) -> None:
    for index in range(int(conn.documents.Count), 0, -1):
        document = conn.documents.Item(index)
        try:
            full = str(document.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).startswith(os.path.abspath(ROOT)):
            try:
                document.Close()
            except Exception:  # noqa: BLE001
                pass


def area_mm2(contour: dict) -> float:
    """Shoelace over the contour as the importer drew it."""
    points = blueprint.contour_points(contour, steps=96)
    if points and points[0] != points[-1]:
        points = points + [points[0]]
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def volume(measurement) -> float | None:
    payload = json.loads(measurement.execute("catia_get_inertia", {}))
    value = (payload.get("inertia") or payload.get("data") or {})
    return value.get("volume_mm3", payload.get("volume_mm3"))


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    clear_ours(conn)
    documents = DocumentTools(conn)
    part_design = PartDesignTools(conn)
    measurement = MeasurementTools(conn)
    ink = BlueprintTools(conn)

    say("=" * 72)
    say("A: the report's own sequence - region, min_size, closed_only, pad")
    say("=" * 72)
    fresh(conn, documents, "ClosureA")
    reply = " ".join(str(ink.execute(
        "catia_import_drawing",
        {"file_path": PDF, "region": [250, 85, 270, 140], "min_size": 10,
         "closed_only": True, "plane": "xy"})).split())
    say("  " + reply[:300])
    check(1, "the trimmed import is accepted", "Imported" in reply, reply[:80])

    reading = ink._parse({"file_path": PDF, "region": [250, 85, 270, 140],
                          "min_size": 10, "closed_only": True})
    contour = reading["contours"][0]
    expected = area_mm2(contour) * 4.8
    check(2, "the contour reports no free ends",
          reading["counts"]["free_ends"] == 0,
          f"free_ends={reading['counts']['free_ends']}")

    try:
        pad_reply = " ".join(str(part_design.execute("catia_pad",
                                                    {"height": 4.8})).split())
        pad_ok, pad_code = True, ""
    except Exception as error:  # noqa: BLE001 - the refusal is the finding
        pad_reply, pad_ok = str(error), False
        pad_code = str(getattr(error, "code", "") or "")
    say("  " + pad_reply[:220])
    got = volume(measurement)
    check(3, "catia_pad accepts it (the report got SKETCH_HAS_OPEN_CURVES)",
          pad_ok, pad_code[:60])
    check(4, "and the volume is the contour's area x height",
          got is not None and abs(got - expected) / expected < 0.02,
          f"{got:.2f} vs {expected:.2f}")

    say("")
    say("=" * 72)
    say("B: the unfiltered import - the 14-contour case - still says what it is")
    say("=" * 72)
    fresh(conn, documents, "ClosureB")
    dry = ink._parse({"file_path": PDF, "min_size": 10, "closed_only": True})
    say(f"  parse: contours={dry['counts']['contours']} "
        f"elements={dry['counts']['elements']} "
        f"free_ends={dry['counts']['free_ends']}")
    try:
        reply_b = " ".join(str(ink.execute(
            "catia_import_drawing",
            {"file_path": PDF, "min_size": 10, "closed_only": True,
             "plane": "xy"})).split())
        refused = False
    except Exception as error:  # noqa: BLE001
        reply_b = str(error)
        refused = True
    check(5, "either every contour closes, or the import refuses before drawing",
          (not refused and dry["counts"]["free_ends"] == 0)
          or (refused and "PROFILE_NOT_CLOSED" in type(error).__name__
              or "PROFILE_NOT_CLOSED" in reply_b),
          reply_b[:90])
    features = json.loads(part_design.execute("catia_list_features", {}))
    check(6, "and a refusal left the document empty",
          not refused or not features.get("sketches"),
          f"sketches={len(features.get('sketches') or [])}")

    say("")
    say("=" * 72)
    total = len(CHECKS)
    passed = sum(1 for _, _, ok, _ in CHECKS if ok)
    say(f"RESULT {passed}/{total}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
