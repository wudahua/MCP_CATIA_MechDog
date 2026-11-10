"""0.3.94 acceptance: the split tool, the cut that is judged before it is built.

  1  a wall that divides a 100x100x10 block completely splits it: the tool's own
     volume numbers agree with a fresh measurement, and the kept half is 70% of
     the body (the 2026-11-10 geometry, measured through the TOOL and not just
     through raw COM)
  2  the same body split with keep='back' keeps the complement: 70000 + 30000 =
     100000, so neither answer is "the feature did something" without being the
     right size
  3  a wall that only covers part of the cross-section is refused with
     SPLIT_FAILED, `rolled_back: true`, and a part that still updates - the dead
     node does not poison the tree
  4  `catia_boolean(remove)` against a body 30 mm away is refused BEFORE the
     feature exists (shape count unchanged) with TOOL_DOES_NOT_TOUCH and the gap
  5  `catia_sketch_copy(target_body=...)` puts the copy in the named body and
     says so

Everything happens in this script's own folder; only documents whose FullName is
under it are closed, and only files this script created are removed.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.results import CodedError, FeatureRejected  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0394\audit"
RESULTS: list[tuple[int, str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, label: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, label, passed, detail))
    say(f"[{'OK ' if passed else 'FAIL'}] {number}. {label}"
        + (f"  |  {detail}" if detail else ""))


def volume(pd: PartDesignTools, body_name: str | None = None) -> float | None:
    part = pd.conn.get_active_part()
    body = pd._body_named(part, body_name or main_body_name(pd))
    return pd._body_volume_mm3(part, body)


def main_body_name(pd: PartDesignTools) -> str:
    """The main body's name on THIS build. A zh install calls it 零件几何体, and
    a check that hard-codes 'PartBody' fails on the name rather than the geometry."""
    return str(pd.conn.get_active_part().MainBody.Name)


def last_feature(pd: PartDesignTools) -> str:
    """The newest node in the main body - the split that was just made.

    Reading the tree instead of parsing the reply: the reply is prose and this is
    a name, and the audit should not break when the prose changes.
    """
    part = pd.conn.get_active_part()
    body = pd._body_named(part, main_body_name(pd))
    return str(body.Shapes.Item(int(body.Shapes.Count)).Name)


def shapes(pd: PartDesignTools, body_name: str | None = None) -> int:
    part = pd.conn.get_active_part()
    body = pd._body_named(part, body_name or main_body_name(pd))
    return int(body.Shapes.Count)


def close_ours(conn: CATIAConnection) -> None:
    """Close documents whose FILE is in this script's folder, and delete them.

    Called at the start as well as at the end: a run that dies half-way (this one
    did, on a hard-coded body name) otherwise leaves the document open, and the
    next run's `new_part` refuses - correctly - to open a second copy with the
    same name. The user's own documents are never touched: the filter is a
    FullName under ROOT.
    """
    for i in range(int(conn.app.Documents.Count), 0, -1):
        document = conn.app.Documents.Item(i)
        try:
            full = str(document.FullName or "")
        except Exception:  # noqa: BLE001
            full = ""
        if full and os.path.abspath(full).startswith(os.path.abspath(ROOT)):
            document.Close()
            say(f"closed {os.path.basename(full)}")
    for name in os.listdir(ROOT):
        if name.lower().endswith(".catpart"):
            try:
                os.remove(os.path.join(ROOT, name))
                say(f"removed {name}")
            except OSError:
                pass


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    target = os.path.join(ROOT, "split_0394.CATPart")
    for leftover in (target,):
        if os.path.isfile(leftover):
            os.remove(leftover)

    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    pd = PartDesignTools(conn)
    gsd = GSDTools(conn)

    say("0.3.94 acceptance - split_solid, the distance check, sketch_copy body")
    say("=" * 78)

    reply = docs.execute("catia_new_part", {"file_path": target})
    say(reply.splitlines()[0] if reply else "(no reply)")

    # ── the block and its two walls ─────────────────────────────────────
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": -50, "y1": -50,
                                                "x2": 50, "y2": 50})
    sketcher.execute("catia_close_sketch", {})
    # The zh build names it 草图.1, so the source is read, never assumed.
    source = str(conn.last_sketch.Name)
    pd.execute("catia_pad", {"height": 10})
    block = volume(pd)
    check(0, "block built", block is not None and abs(block - 100000) < 5,
          f"{block:.1f} mm3")

    gsd.execute("catia_gsd_create_geoset", {"name": "Geom"})
    for name, x in (("A", -200.0), ("B", 200.0), ("C", -20.0), ("D", 20.0)):
        gsd.execute("catia_gsd_point", {"x": x, "y": -20.0, "z": 3.0, "name": name})
    gsd.execute("catia_gsd_line", {"point1": "A", "point2": "B", "name": "FullLine"})
    gsd.execute("catia_gsd_extrude", {"profile": "FullLine", "direction": [0, 0, 1],
                                      "limit1": 100, "limit2": 100, "name": "WallFull"})
    gsd.execute("catia_gsd_line", {"point1": "C", "point2": "D", "name": "ShortLine"})
    gsd.execute("catia_gsd_extrude", {"profile": "ShortLine", "direction": [0, 0, 1],
                                      "limit1": 100, "limit2": 100, "name": "WallShort"})

    # ── 1: keep='front' ─────────────────────────────────────────────────
    try:
        reply = pd.execute("catia_split_solid", {"cutter": "WallFull",
                                                 "keep": "front"})
        after = volume(pd)
        said = "70000.0" in reply or "30000.0" in reply
        check(1, "split by a wall that divides the body",
              after is not None and min(abs(after - 70000), abs(after - 30000)) < 5
              and said,
              f"volume {after:.1f} mm3 | {reply.split('. ')[0][:96]}")
        first_half = after
        feature = last_feature(pd)
        say(f"     feature: {feature}")
    except (CodedError, FeatureRejected) as error:
        check(1, "split by a wall that divides the body", False,
              f"{error.code}: {str(error).splitlines()[0][:120]}")
        first_half, feature = None, ""

    # ── 2: the other side, and the two halves add up ────────────────────
    if feature:
        pd.execute("catia_delete_feature", {"feature_name": feature})
        check(1.5, "the split can be taken back out and the block restored",
              abs((volume(pd) or 0) - 100000) < 5, f"{volume(pd):.1f} mm3")
        try:
            reply = pd.execute("catia_split_solid", {"cutter": "WallFull",
                                                     "keep": "back"})
            second = volume(pd)
            total = (first_half or 0) + (second or 0)
            check(2, "keep='back' keeps the complement",
                  abs(total - 100000) < 10,
                  f"{first_half:.1f} + {second:.1f} = {total:.1f} mm3")
            feature = last_feature(pd)
            pd.execute("catia_delete_feature", {"feature_name": feature})
        except (CodedError, FeatureRejected) as error:
            check(2, "keep='back' keeps the complement", False,
                  f"{error.code}: {str(error).splitlines()[0][:120]}")

    # ── 3: a wall that does not divide the body ─────────────────────────
    before = shapes(pd)
    try:
        pd.execute("catia_split_solid", {"cutter": "WallShort"})
        check(3, "a partial wall is refused with the measured rule", False,
              "it answered success")
    except (CodedError, FeatureRejected) as error:
        text = str(error)
        rolled = getattr(error, "rolled_back", None)
        check(3, "a partial wall is refused with the measured rule",
              error.code == "SPLIT_FAILED" and rolled is True
              and "completely" in text,
              f"code={error.code} rolled_back={rolled} "
              f"shapes {before} -> {shapes(pd)}")
        update = pd.conn.get_active_part()
        try:
            update.Update()
            check(3.5, "the part still updates after the refused split", True,
                  "no dead node left behind")
        except Exception as exc:  # noqa: BLE001
            check(3.5, "the part still updates after the refused split", False,
                  str(exc).splitlines()[0][:100])

    # ── 4: a cut whose tool body never reaches the target ───────────────
    pd.execute("catia_new_body", {"name": "FarTool"})
    # OFFSET, or the "far" body is not far: a pad sketched on xy sits inside the
    # block and the remove legitimately cuts (this audit's own first attempt).
    sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 50})
    sketcher.execute("catia_sketch_rectangle", {"x1": -10, "y1": -10,
                                                "x2": 10, "y2": 10})
    sketcher.execute("catia_close_sketch", {})
    # The zh build names it 草图.1, so the source is read, never assumed.
    source = str(conn.last_sketch.Name)
    pd.execute("catia_pad", {"height": 10})
    pd.execute("catia_set_active_body", {"name": main_body_name(pd)})
    far_shapes = shapes(pd, "FarTool")
    try:
        pd.execute("catia_boolean", {"operation": "remove", "tool_body": "FarTool",
                                     "target_body": main_body_name(pd)})
        check(4, "a cut 30 mm short of the material is refused before it is built",
              False, "it answered success")
    except (CodedError, FeatureRejected) as error:
        check(4, "a cut 30 mm short of the material is refused before it is built",
              error.code == "TOOL_DOES_NOT_TOUCH" and "mm away" in str(error),
              f"{str(error).splitlines()[0][:110]}")
    check(4.5, "the refusal left the tree untouched",
          shapes(pd, "FarTool") == far_shapes and abs((volume(pd) or 0) - 100000) < 5,
          f"FarTool shapes {shapes(pd, 'FarTool')}, main body {volume(pd):.1f} mm3")

    # ── 5: the copy goes where it was asked to ──────────────────────────
    pd.execute("catia_new_body", {"name": "Label"})
    try:
        reply = sketcher.execute("catia_sketch_copy",
                                 {"source": source, "plane": "xy",
                                  "target_body": "Label"})
        part = pd.conn.get_active_part()
        body = pd._body_named(part, "Label")
        landed = [str(body.Sketches.Item(i).Name)
                  for i in range(1, int(body.Sketches.Count) + 1)]
        check(5, "the copy lands in the body it was told to",
              "in body 'Label'" in reply and len(landed) == 1,
              f"Label holds {landed}")
    except (CodedError, FeatureRejected) as error:
        check(5, "the copy lands in the body it was told to", False,
              f"{error.code}: {str(error).splitlines()[0][:110]}")

    # ── close and clean, only inside our own folder ─────────────────────
    say("-" * 78)
    close_ours(conn)

    failed = [row for row in RESULTS if not row[2]]
    say("=" * 78)
    say(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    for number, label, _, detail in failed:
        say(f"  FAILED {number}: {label} | {detail}")
    sys.exit(len(failed))


if __name__ == "__main__":
    main()
