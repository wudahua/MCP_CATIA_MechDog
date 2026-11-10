"""0.3.98 acceptance: the engraving chain three reports could not find.

  1  one call, `catia_surface_engrave`, cuts a groove along a curve that lies on
     a CURVED face - the step the tutorial calls 曲面切割文字与图案
  2  the volume that came off matches the geometry (half of a tube of radius =
     depth, centred on the curve), so the depth really is the radius
  3  the reply prints that prediction, so a groove that stopped following the
     surface is visible in the same call
  4  `catia_diagnose` lists the sweep route as available, with its limit
  5  a curve that does not lie on the body removes nothing, and the call leaves
     NOTHING behind - no cutter body, no plane, no circle, no sweep

Order matters: the deliberate failure is LAST, because an update that fails
leaves the document unable to update new elements (measured 2026-11-09).

Everything happens in this script's own folder.
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.results import CodedError, FeatureRejected  # noqa: E402
from catia_mcp.tools.diagnostics import DiagnosticsTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0398\audit"
RESULTS: list[tuple[int, str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, label: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, label, passed, detail))
    say(f"[{'OK ' if passed else 'FAIL'}] {number}. {label}"
        + (f"  |  {detail}" if detail else ""))


def close_ours(conn: CATIAConnection) -> None:
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


def body_names(conn: CATIAConnection) -> list[str]:
    part = conn.get_active_part()
    return [str(part.Bodies.Item(i).Name)
            for i in range(1, int(part.Bodies.Count) + 1)]


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    docs, sk, pd, gsd = (DocumentTools(conn), SketcherTools(conn),
                         PartDesignTools(conn), GSDTools(conn))

    say("0.3.98 acceptance - surfacing text into a curved face")
    say("=" * 78)

    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, "engrave_0398.CATPart")})
    main_body = str(conn.get_active_part().MainBody.Name)

    # A cylinder, so the face is genuinely curved - the case three reports were about.
    sk.execute("catia_create_sketch", {"plane": "xy"})
    sk.execute("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": 40})
    sk.execute("catia_close_sketch", {})
    pd.execute("catia_pad", {"height": 100})
    volume0 = pd._body_volume_mm3(conn.get_active_part(), conn.get_active_part_body())
    check(0, "cylinder built", volume0 is not None and abs(volume0 - 502654.8) < 5,
          f"{volume0:.1f} mm3 | pi*40^2*100")

    faces = json.loads(pd.execute("catia_list_faces", {}))
    curved = [f for f in faces["faces"] if f.get("type") != "PlanarFace"]
    if not curved:
        check(1, "the solid has a curved face", False,
              f"faces: {[(f['name'], f.get('type')) for f in faces['faces']]}")
        return
    gsd.execute("catia_gsd_extract", {"element": curved[0]["name"],
                                      "name": "Wall"})

    sk.execute("catia_create_sketch", {"plane": "zx", "offset": -45})
    sk.execute("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": 4})
    sk.execute("catia_close_sketch", {})
    source = str(conn.last_sketch.Name)
    gsd.execute("catia_gsd_project", {"element": source, "support": "Wall",
                                      "name": "Trace"})

    # ── 1 + 2 + 3: one call does the whole chain ────────────────────────
    try:
        reply = gsd.execute("catia_surface_engrave",
                            {"curve": "Trace", "depth": 0.4})
        after = pd._body_volume_mm3(conn.get_active_part(),
                                    conn.get_active_part_body())
        removed = (volume0 or 0) - (after or 0)
        predicted = re.search(r"in the material: (\d+\.\d{3}) mm3", reply)
        check(1, "one call engraves a groove on a curved face",
              removed > 0 and "surface normal" in reply,
              f"removed {removed:.3f} mm3 | {reply.split('.')[0][:70]}")
        check(2, "the removed volume is the half-tube the geometry predicts",
              predicted is not None
              and abs(removed - float(predicted.group(1))) < 0.05,
              f"removed {removed:.3f} vs predicted "
              f"{predicted.group(1) if predicted else '?'} mm3")
        check(3, "the reply prints that prediction and the depth rule",
              predicted is not None and "0.4 mm along the surface normal" in reply,
              "reply carries the number a wrong groove would falsify")
        check(4, "no WARNING in a clean engrave", "WARNING" not in reply, "")
        check(5, "the cutter body was consumed, not left in the tree",
              body_names(conn) == [main_body],
              f"bodies: {body_names(conn)}")
    except (CodedError, FeatureRejected) as error:
        for number in (1, 2, 3, 4, 5):
            check(number, "engrave", False,
                  f"{getattr(error, 'code', None)}: {str(error).splitlines()[0][:110]}")

    # ── 6: diagnose names the route ─────────────────────────────────────
    try:
        report = json.loads(DiagnosticsTools(conn).execute("catia_diagnose", {}))
        entries = {e["member"]: e["status"]
                   for e in report.get("capability_ceilings", [])}
        sweep = next((v for k, v in entries.items() if "SweepExplicit" in k), None)
        check(6, "diagnose lists the sweep engraving route as available",
              sweep == "AVAILABLE_WITH_LIMIT",
              f"{len(entries)} ceilings | sweep: {sweep}")
    except (CodedError, FeatureRejected) as error:
        check(6, "diagnose lists the sweep engraving route", False,
              f"{getattr(error, 'code', None)}: {str(error).splitlines()[0][:110]}")

    # ── 7: a curve that is not on the target body, and what survives ────
    # A second solid 200 mm away, so the tube is built, closed, and then meets
    # no material at all - the "the curve is not on this body" case, without
    # needing a curve that fails earlier for a different reason.
    try:
        pd.execute("catia_new_body", {"name": "Away"})
        sk.execute("catia_create_sketch", {"plane": "xy"})
        sk.execute("catia_sketch_rectangle", {"x1": 200, "y1": 0,
                                              "x2": 260, "y2": 40})
        sk.execute("catia_close_sketch", {})
        pd.execute("catia_pad", {"height": 10})
        pd.execute("catia_set_active_body", {"name": main_body})
        far_reply = gsd.execute("catia_surface_engrave",
                                {"curve": "Trace", "depth": 0.4,
                                 "target_body": "Away", "name": "FarEngrave"})
        check(7, "a curve that is not on the target is refused", False,
              f"it reported success: {far_reply[:90]}")
        check(8, "...and leaves nothing behind", False, "no refusal to inspect")
    except (CodedError, FeatureRejected) as error:
        code = getattr(error, "code", None)
        message = str(error)
        leftovers = [n for n in body_names(conn)
                     if n not in (main_body, "Away")]
        check(7, "a curve that is not on the target is refused, not accepted",
              code in {"TOOL_DOES_NOT_TOUCH", "NO_MATERIAL"},
              f"{code} | {message.splitlines()[0][:80]}")
        check(8, "...and leaves no cutter body behind",
              not leftovers and "Nothing this call created was left behind" in message,
              f"bodies: {body_names(conn)} | "
              f"{'cleanup sentence present' if 'Nothing this call' in message else 'no cleanup sentence'}")

    say("")
    passed = sum(1 for _, _, ok, _ in RESULTS if ok)
    say(f"{passed}/{len(RESULTS)} checks passed")
    failed = [f"{n}. {label}" for n, label, ok, _ in RESULTS if not ok]
    if failed:
        say("FAILED: " + "; ".join(failed))
    try:
        close_ours(conn)
    except Exception as error:  # noqa: BLE001
        say(f"cleanup: {error}")


if __name__ == "__main__":
    main()
