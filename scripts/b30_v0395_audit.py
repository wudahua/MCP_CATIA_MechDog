"""0.3.95 acceptance: the copy that warned about the body it was already in.

  1  `catia_sketch_copy(target_body=...)` answers WITHOUT a WARNING and names the
     body - the reported bug was 2/2 "the copy is in the wrong body" on a copy
     that was exactly where it was asked to go (0.3.94 read
     `sketch.Parent.Name`, which is "Sketches", the collection)
  2  the copy really is in that body, read from COPYTARGET's own Sketches
  3  `catia_sketch_strokes` draws a whole glyph outline in one call, the profile
     closes, and a pocket on it removes material - so a letter is one call and a
     usable outline, not dozens of calls and a rounded blob
  4  the arc's angle form describes the direction it actually drew (90 -> 0 is
     clockwise)
  5  a touching tool body still answers NO_MATERIAL, and the envelope keeps the
     name the caller used apart from CATIA's, then `catia_list_bodies` reports an
     active body instead of three `active: false`

Everything happens in this script's own folder; only documents whose FullName is
under it are closed, and only files this script created are removed.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.results import CodedError, FeatureRejected  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0395\audit"
RESULTS: list[tuple[int, str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, label: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, label, passed, detail))
    say(f"[{'OK ' if passed else 'FAIL'}] {number}. {label}"
        + (f"  |  {detail}" if detail else ""))


def close_ours(conn: CATIAConnection) -> None:
    """Close documents whose FILE is in this script's folder, and delete them."""
    for i in range(int(conn.app.Documents.Count), 0, -1):
        document = conn.app.Documents.Item(i)
        try:
            full = str(document.FullName or "")
        except Exception:  # noqa: BLE001
            full = ""
        if full and os.path.abspath(full).startswith(os.path.abspath(ROOT)):
            document.Close()
            say(f"closed {os.path.basename(full)}")
    if os.path.isdir(ROOT):
        for name in os.listdir(ROOT):
            if name.lower().endswith(".catpart"):
                try:
                    os.remove(os.path.join(ROOT, name))
                    say(f"removed {name}")
                except OSError:
                    pass


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    target = os.path.join(ROOT, "copy_0395.CATPart")

    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    pd = PartDesignTools(conn)

    say("0.3.95 acceptance - sketch_copy body, sketch_strokes, arc direction")
    say("=" * 78)

    docs.execute("catia_new_part", {"file_path": target})
    main = str(conn.get_active_part().MainBody.Name)

    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": -50, "y1": -50,
                                                "x2": 50, "y2": 50})
    sketcher.execute("catia_close_sketch", {})
    source = str(conn.last_sketch.Name)
    pd.execute("catia_pad", {"height": 10})
    block = pd._body_volume_mm3(conn.get_active_part(), conn.get_active_part_body())
    check(0, "block built", block is not None and abs(block - 100000) < 5,
          f"{block:.1f} mm3 | source sketch {source}")

    # ── 1 + 2: the copy into another body ───────────────────────────────
    pd.execute("catia_new_body", {"name": "CopyTarget"})
    try:
        reply = sketcher.execute("catia_sketch_copy",
                                 {"source": source, "plane": "xy",
                                  "offset": [200, 0], "target_body": "CopyTarget",
                                  "name": "CopyCheck"})
        check(1, "the copy answers without a WARNING and names the body",
              "WARNING" not in reply and "in body 'CopyTarget'" in reply,
              reply.split(". ")[0][:110])
        part = conn.get_active_part()
        body = pd._body_named(part, "CopyTarget")
        held = [str(body.Sketches.Item(i).Name)
                for i in range(1, int(body.Sketches.Count) + 1)]
        check(2, "the copy is in CopyTarget's own sketch list",
              held == ["CopyCheck"], f"CopyTarget holds {held}")
    except (CodedError, FeatureRejected) as error:
        check(1, "the copy answers without a WARNING and names the body", False,
              f"{getattr(error, 'code', None)}: {str(error).splitlines()[0][:110]}")
        check(2, "the copy is in CopyTarget's own sketch list", False, "skipped")

    # ── 3: a glyph outline in one call ──────────────────────────────────
    pd.execute("catia_set_active_body", {"name": main})
    # On the TOP face (offset 10) so the pocket has material under it, and one
    # CLOSED stroke: a pocket needs a closed profile, and the point here is the
    # number of segments in one call, not whether CATIA accepts an open glyph.
    sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 10})
    glyph = [[[-20, -20], [20, -20], [20, -5], [5, -5], [5, 5], [20, 5],
              [20, 20], [-20, 20], [-20, -20]]]
    try:
        reply = sketcher.execute("catia_sketch_strokes", {"strokes": glyph})
        before = pd._body_volume_mm3(conn.get_active_part(),
                                     conn.get_active_part_body())
        closed = sketcher.execute("catia_close_sketch", {})
        pd.execute("catia_pocket", {"depth": 2})
        after = pd._body_volume_mm3(conn.get_active_part(),
                                    conn.get_active_part_body())
        removed = (before or 0) - (after or 0)
        check(3, "a whole glyph is drawn in one call and pockets",
              "8 straight line(s)" in reply and removed > 0,
              f"{reply.split('.')[0][:64]} | removed {removed:.1f} mm3 | "
              f"{str(closed)[:40]}")
    except (CodedError, FeatureRejected) as error:
        check(3, "a whole glyph is drawn in one call and pockets", False,
              f"{getattr(error, 'code', None)}: {str(error).splitlines()[0][:110]}")

    # ── 4: the arc says which way it went ───────────────────────────────
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    try:
        reply = sketcher.execute("catia_sketch_arc",
                                 {"cx": 0, "cy": 0, "radius": 5,
                                  "start_angle": 90, "end_angle": 0})
        check(4, "90 -> 0 is described as clockwise",
              "from 90° clockwise to 0°" in reply,
              reply.split("Arc created")[1][:90] if "Arc created" in reply else reply[:90])
    except (CodedError, FeatureRejected) as error:
        check(4, "90 -> 0 is described as clockwise", False,
              f"{getattr(error, 'code', None)}: {str(error).splitlines()[0][:110]}")
    sketcher.execute("catia_close_sketch", {})

    # ── 5: NO_MATERIAL keeps both names, and the handle moves ───────────
    pd.execute("catia_new_body", {"name": "TouchTool"})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 10})
    sketcher.execute("catia_sketch_rectangle", {"x1": -10, "y1": -10,
                                                "x2": 10, "y2": 10})
    sketcher.execute("catia_close_sketch", {})
    pd.execute("catia_pad", {"height": 10})       # z 10..20: touches, no volume
    try:
        pd.execute("catia_boolean", {"operation": "remove", "tool_body": "TouchTool",
                                     "target_body": main})
        check(5, "a touching tool body answers NO_MATERIAL", False,
              "it answered success")
    except (CodedError, FeatureRejected) as error:
        data = getattr(error, "data", {}) or {}
        check(5, "a touching tool body answers NO_MATERIAL with both names",
              getattr(error, "code", None) == "NO_MATERIAL"
              and data.get("tool_body_requested") == "TouchTool"
              and data.get("tool_body_consumed") is True,
              f"code={getattr(error, 'code', None)} "
              f"requested={data.get('tool_body_requested')} "
              f"internal={data.get('tool_body_internal')}")

    import json
    payload = json.loads(pd.execute("catia_list_bodies", {}))
    check(6, "list_bodies reports an active body instead of all-false",
          payload.get("active_body") == main
          and not payload.get("active_body_missing"),
          f"active_body={payload.get('active_body')} "
          f"missing={payload.get('active_body_missing')}")

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
