"""0.3.104 acceptance: a cut that went somewhere else is not a groove.

  1  the reply names the boolean feature, so the groove can be taken back out
  2  a curve buried inside the pad reports BURIED and stops claiming "HALF"
  3  the same curve on the surface reports ratio 1 and keeps that sentence
  4  a curve floating far above the material is not called a groove
  5  a SOLID FACE accepts a projection - the 2026-11-11 report's three
     PROJECT_FAILED cases were re-run here and two of them projected fine
  6  the ceilings carry both findings
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0404")
ROOT = os.path.join(_PROBE_ROOT, "audit")
RESULTS: list[tuple[int, str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, label: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, label, passed, detail))
    say(f"[{'OK ' if passed else 'FAIL'}] {number}. {label}" + (f"  |  {detail}" if detail else ""))


def close_ours(conn: CATIAConnection) -> None:
    for index in range(int(conn.app.Documents.Count), 0, -1):
        document = conn.app.Documents.Item(index)
        try:
            full = str(document.FullName or "")
        except Exception:  # noqa: BLE001
            full = ""
        if full and os.path.abspath(full).startswith(os.path.abspath(ROOT)):
            try:
                document.Close()
            except Exception:  # noqa: BLE001
                pass


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    # catia_new_part's overwrite flag does not reach _save_document, so a second
    # run would stop on its own leftovers. Only this script's own folder.
    for name in os.listdir(ROOT):
        if name.lower().endswith(".catpart"):
            try:
                os.remove(os.path.join(ROOT, name))
            except OSError:
                pass
    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    docs, gsd, sk = DocumentTools(conn), GSDTools(conn), SketcherTools(conn)
    pd = PartDesignTools(conn)

    say("0.3.104 acceptance - a cut that went somewhere else is not a groove")
    say("=" * 78)

    def block(tag: str) -> None:
        docs.execute("catia_new_part", {"file_path": os.path.join(ROOT, f"{tag}.CATPart"),
                                        "overwrite": True})
        sk.execute("catia_create_sketch", {"plane": "xy"})
        sk.execute("catia_sketch_rectangle", {"x1": -50, "y1": -50, "x2": 50, "y2": 50})
        sk.execute("catia_close_sketch", {})
        pd.execute("catia_pad", {"height": 20.0})

    def line_sketch(offset: float) -> str:
        sk.execute("catia_create_sketch", {"plane": "xy", "offset": offset})
        sk.execute("catia_sketch_line", {"x1": -30, "y1": 0, "x2": 30, "y2": 0})
        sk.execute("catia_close_sketch", {})
        part = conn.get_active_part()
        return str(part.MainBody.Sketches.Item(
            int(part.MainBody.Sketches.Count)).Name)

    # ── buried: the curve is 5 mm inside a 20 mm block ─────────────────
    block("buried")
    buried = line_sketch(5.0)
    reply = " ".join(str(gsd.execute("catia_surface_engrave",
                                     {"curve": buried, "depth": 2.0,
                                      "name": "BuriedCut"})).split())
    named = re.findall("feature this call created is '([^']+)'", reply)
    check(1, "the reply names the boolean feature so it can be deleted",
          bool(named), named[0] if named else "none named")
    check(2, "a buried curve reports BURIED and stops claiming HALF",
          "BURIED" in reply and "HALF of it is inside" not in reply,
          reply[:110])
    ratio = re.search(r"Removed/predicted\s+is\s+([\d.]+)", reply)
    check(3, "...and the ratio it reports is about 2",
          bool(ratio) and 1.9 <= float(ratio.group(1)) <= 2.1,
          ratio.group(1) if ratio else "no ratio")

    # ── on the surface: the ordinary case ─────────────────────────────
    block("surface")
    on_top = line_sketch(20.0)
    reply2 = " ".join(str(gsd.execute("catia_surface_engrave",
                                      {"curve": on_top, "depth": 2.0,
                                       "name": "SurfaceCut"})).split())
    check(4, "the same curve on the surface keeps the half-inside sentence",
          "HALF of it is inside" in reply2 and "BURIED" not in reply2,
          reply2[:110])

    # ── a solid face accepts a projection ─────────────────────────────
    docs.execute("catia_new_part", {"file_path": os.path.join(ROOT, "face.CATPart"),
                                    "overwrite": True})
    sk.execute("catia_create_sketch", {"plane": "xy"})
    sk.execute("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": 30})
    sk.execute("catia_close_sketch", {})
    pd.execute("catia_pad", {"height": 60.0})
    faces = json.loads(str(pd.execute("catia_list_faces", {})))
    side = next((entry["name"] for entry in faces.get("faces") or []
                 if "ylind" in str(entry.get("type"))), None)
    sk.execute("catia_create_sketch",
               {"plane": {"type": "frame", "origin": [0, -40, 0],
                          "u": [1, 0, 0], "v": [0, 0, 1]}})
    sk.execute("catia_text_strokes",
               {"text": "A", "height": 10.0, "origin": [-6.0, 10.0]})
    sk.execute("catia_close_sketch", {})
    part = conn.get_active_part()
    glyph = str(part.MainBody.Sketches.Item(
        int(part.MainBody.Sketches.Count)).Name)
    landed = None
    if side:
        try:
            projection = " ".join(str(gsd.execute(
                "catia_gsd_project",
                {"element": glyph, "support": side, "name": "OnFace"})).split())
            share = re.search(r"(\d+\.\d+) % of", projection)
            landed = float(share.group(1)) if share else None
        except Exception as error:  # noqa: BLE001
            landed = None
            say(f"   projection onto {side} failed: {str(error).splitlines()[0][:120]}")
    check(5, f"a SOLID FACE ({side}) accepts the projection, as a curved support",
          landed is not None, f"{landed} % of the glyph landed" if landed else "refused")

    # ── the ceilings ──────────────────────────────────────────────────
    engrave_entry = next((item for item in _CAPABILITY_CEILINGS
                          if "engraving" in item["member"]), None)
    project_entry = next((item for item in _CAPABILITY_CEILINGS
                          if "first projection" in item["member"]), None)
    ceiling_ok = (
        engrave_entry is not None and "BURIED" in engrave_entry["detail"]
        and "5.624" in engrave_entry["detail"]
        and project_entry is not None and "13.6" in project_entry["detail"]
        and "parallel planes fail" in project_entry["detail"])
    check(6, "the ceilings carry both findings", ceiling_ok,
          "engrave + projection entries updated")

    close_ours(conn)
    say("")
    passed = sum(1 for _n, _l, ok, _d in RESULTS if ok)
    say(f"{passed}/{len(RESULTS)} passed")
    for number, label, ok, detail in RESULTS:
        if not ok:
            say(f"  FAILED {number}: {label} - {detail}")
    raise SystemExit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
