"""Acceptance for the mirror refusal, on B30.

The report's sequence: pad -> groove -> mirror(xy), which used to answer success
and the pre-groove volume. What is accepted here:

  A  a body whose last feature is a cut is REFUSED, with the volume in the payload
     and no feature created
  B  feature='<name>' is refused too, with the recipe
  C  allow_body_mirror=true still performs the body operation, and says what the
     volume did
  D  a body whose last feature is a PAD is still mirrored (the legitimate use)
  E  nothing is created by a refusal - the feature count is unchanged

.venv/Scripts/python.exe -u scripts/b30_v0451_acceptance_mirror.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.results import CodedError  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0451")
CELLS: list[tuple[str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def cell(name: str, ok: bool, detail: str = "") -> None:
    CELLS.append((name, ok, detail))
    say(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def fresh(conn, docs, name):
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, name + ".CATPart")
    for index in range(int(conn.documents.Count), 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = str(doc.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).lower().startswith(
                os.path.abspath(ROOT).lower()):
            try:
                doc.Close()
            except Exception:  # noqa: BLE001
                pass
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    return conn.get_active_part()


def volume(part) -> float:
    spa = part.Parent.GetWorkbench("SPAWorkbench")
    measurable = spa.GetMeasurable(part.CreateReferenceFromObject(part.MainBody))
    return float(measurable.Volume) * 1e9


def features(part) -> int:
    return int(part.MainBody.Shapes.Count)


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    say(f"acceptance v0451 on {conn.app.Name}")

    # ── A: a body whose last feature is a cut ─────────────────────────────
    part = fresh(conn, docs, "AccV0451Cut")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0.0, "y1": 0.0,
                                               "x2": 60.0, "y2": 40.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 50.0})
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 40.0, "y1": 0.0,
                                               "x2": 60.0, "y2": 40.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pocket", {"depth": 50.0})
    say(f"  box+pocket: {features(part)} features, {volume(part):.4f} mm3")
    before_count, before_volume = features(part), volume(part)
    try:
        parts.execute("catia_mirror", {"plane": "xy"})
        cell("A1 a mirror over a cut is refused", False, "the call succeeded")
    except CodedError as error:
        cell("A1 a mirror over a cut is refused",
             error.code == "MIRROR_WOULD_FILL_CUT",
             f"code={error.code}")
        volume_sentence = f"{float(error.data.get('volume_mm3') or 0):.4f}"
        cell("A2 the refusal carries the volume it would have changed",
             error.data.get("volume_mm3") is not None
             and abs(float(error.data["volume_mm3"]) - before_volume) < 1e-3,
             f"volume_mm3={volume_sentence} vs measured {before_volume:.4f}")
        cell("A3 the refusal names the cut it refused",
             error.data.get("kind") == "pocket"
             and "pocket" in str(error.data.get("last_feature", "")).lower()
             or error.data.get("kind") == "pocket",
             f"kind={error.data.get('kind')}, "
             f"last_feature={error.data.get('last_feature')}")
        cell("A4 it says what to do instead",
             any("twice" in action for action in (error.next_actions or [])),
             (error.next_actions or ["<none>"])[0][:90])
    cell("E1 nothing was created by the refusal",
         features(part) == before_count
         and abs(volume(part) - before_volume) < 1e-6,
         f"{features(part)} features (was {before_count}), "
         f"{volume(part):.4f} mm3 (was {before_volume:.4f})")

    # ── B: naming a feature is refused with the measured reason ───────────
    names = [str(part.MainBody.Shapes.Item(i).Name)
             for i in range(1, features(part) + 1)]
    try:
        parts.execute("catia_mirror", {"plane": "xy", "feature": names[-1]})
        cell("B1 feature=<name> is refused", False, "the call succeeded")
    except CodedError as error:
        cell("B1 feature=<name> is refused",
             error.code == "MIRROR_SEED_UNSUPPORTED", f"code={error.code}")
        cell("B2 and says the seed property is read-only",
             "can not be set" in str(error),
             str(error)[:120].replace("\n", " "))

    # ── C: the body operation is still available, with numbers ────────────
    try:
        reply = parts.execute("catia_mirror", {"plane": "xy",
                                               "allow_body_mirror": True})
        after = volume(part)
        cell("C1 allow_body_mirror=true performs the union",
             after > before_volume * 1.5,
             f"{before_volume:.4f} -> {after:.4f} mm3")
        cell("C2 and the reply states both volumes",
             f"{before_volume:.4f}" in reply and f"{after:.4f}" in reply,
             reply[:150].replace("\n", " "))
    except CodedError as error:
        cell("C1 allow_body_mirror=true performs the union", False, error.code)
        cell("C2 and the reply states both volumes", False, "no reply")

    # ── D: a pad is still mirrored (the legitimate use) ───────────────────
    solid = fresh(conn, docs, "AccV0451Pad")
    sketcher.execute("catia_create_sketch", {"plane": "zx"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0.0, "y1": 0.0,
                                               "x2": 40.0, "y2": 20.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 10.0})
    pad_volume = volume(solid)
    try:
        reply = parts.execute("catia_mirror", {"plane": "zx"})
        doubled = volume(solid)
        cell("D1 a pad mirror still answers, and says what it did",
             doubled > pad_volume * 1.5 and "Transform" in reply,
             f"{pad_volume:.4f} -> {doubled:.4f} mm3")
    except CodedError as error:
        cell("D1 a pad mirror still answers, and says what it did", False,
             error.code)

    say("")
    passed = sum(1 for _name, ok, _detail in CELLS if ok)
    say(f"{passed}/{len(CELLS)} cells passed")
    for name, ok, detail in CELLS:
        if not ok:
            say(f"  FAILED: {name}  ({detail})")
    return 0 if passed == len(CELLS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
