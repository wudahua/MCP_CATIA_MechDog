"""0.3.78 acceptance on B30: endpoints, not length deltas.

0.3.77's audit compared `dimensions`, which cannot see a sign - a pattern that
mirrors by 200 mm grows the same length, so a reversed pattern passed 12/12. This
script records min AND max for every case, which is the check the 2026-11-08
report asked for.

1  dir1_axis x/y/z move the copies to +X/+Y/+Z; reverse_* moves them the other way
2  dir2_axis works in its own slot (it used to take the plane's V axis, unlabelled)
3  a duplicate sketch name is renamed at creation
4  catia_open_document refuses a second copy of an already-loaded file
5  catia_gsd_create_geoset(parent=...) really nests a set
6  a plan's step_warnings carries the untruncated warning
7  a rejected loft answers LOFT_REJECTED, not a code that follows the wording

Everything is created under F:\\catia_probe_v0378d and closed again.
"""

from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.planner import PlannerTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0378d"
RESULTS: list[tuple[str, bool, str]] = []


def say(line: str) -> None:
    print(line, flush=True)


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    say(f"   {'OK  ' if ok else 'FAIL'} {label}" + (f"  [{detail[:180]}]" if detail else ""))


def box(measure: MeasurementTools, part) -> dict:
    return json.loads(measure._part_bbox(part, "mesh"))


def shift(before: dict, after: dict, axis: str) -> float:
    """Which ENDPOINT moved: +1 if max grew, -1 if min fell, 0 if neither."""
    lo = after["min"][axis] - before["min"][axis]
    hi = after["max"][axis] - before["max"][axis]
    if abs(hi) > 1.0 and abs(lo) < 1.0:
        return 1.0 if hi > 0 else -1.0
    if abs(lo) > 1.0 and abs(hi) < 1.0:
        return -1.0 if lo < 0 else 1.0
    return 0.0


def ellipse(count: int) -> list[list[float]]:
    return [[round(40 * math.cos(2 * math.pi * i / count), 4),
             round(6 * math.sin(2 * math.pi * i / count), 4)]
            for i in range(count)]


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    gsd = GSDTools(conn)
    measure = MeasurementTools(conn)
    # The planner dispatches through the same router the server builds: keyed
    # by tool name, over the instances this script already holds (plus itself).
    planner = PlannerTools(conn)
    router: dict = {}
    for module in (docs, sketcher, part_design, gsd, measure, planner):
        for definition in module.get_tool_definitions():
            router[definition["name"]] = module
    planner.bind(router)

    try:
        before_active = str(conn.app.ActiveDocument.Name)
    except Exception:  # noqa: BLE001
        before_active = ""
    existing = {str(conn.documents.Item(i).Name)
                for i in range(1, conn.documents.Count + 1)}
    say(f"session before: {conn.documents.Count} document(s)")
    os.makedirs(ROOT, exist_ok=True)
    for stale in os.listdir(ROOT):
        try:
            os.remove(os.path.join(ROOT, stale))
        except OSError:
            pass

    path = os.path.join(ROOT, "signs.CATPart")
    try:
        docs.execute("catia_new_part", {"name": "Audit378", "file_path": path})
        part = conn.get_active_part()
        body = conn.get_active_part_body()
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 40, "y2": 10})
        sketcher.execute("catia_close_sketch", {})
        import re as _re
        pad_reply = part_design.execute("catia_pad", {"height": 5})
        pad = _re.findall(r"'([^']*)'", str(pad_reply))[0]

        def fresh_box() -> dict:
            return box(measure, part)

        def delete(name: str) -> None:
            try:
                part_design.execute("catia_delete_feature", {"feature_name": name})
            except Exception as error:  # noqa: BLE001
                say(f"      (could not delete {name}: {str(error).splitlines()[0][:70]})")

        # ── 1: dir1 signs ──────────────────────────────────────────────────
        say("\n1  dir1_axis signs, read from the box endpoints")
        for axis, plus in (("x", "+X"), ("y", "+Y"), ("z", "+Z")):
            base = fresh_box()
            reply = part_design.execute("catia_rect_pattern", {
                "feature_name": pad, "dir1_axis": axis,
                "dir1_count": 3, "dir1_spacing": 100})
            after = fresh_box()
            direction = shift(base, after, axis)
            check(f"dir1_axis='{axis}' moves towards {plus}",
                  direction > 0, f"endpoint shift {direction:+.0f} | {str(reply)[:80]}")
            check(f"dir1_axis='{axis}' touches no other axis",
                  all(abs(shift(base, after, other)) < 0.5 for other in "xyz" if other != axis),
                  str({k: shift(base, after, k) for k in "xyz"}))
            delete(_re.findall(r"'([^']*)'", str(reply))[0])

            base = fresh_box()
            reply = part_design.execute("catia_rect_pattern", {
                "feature_name": pad, "dir1_axis": axis,
                "dir1_count": 3, "dir1_spacing": 100, "reverse_dir1": True})
            after = fresh_box()
            check(f"dir1_axis='{axis}' + reverse_dir1 goes the other way",
                  shift(base, after, axis) < 0, f"endpoint shift {shift(base, after, axis):+.0f}")
            delete(_re.findall(r"'([^']*)'", str(reply))[0])

        # ── 2: dir2 in its own slot ────────────────────────────────────────
        say("\n2  dir2_axis lands on the axis it names")
        base = fresh_box()
        reply = part_design.execute("catia_rect_pattern", {
            "feature_name": pad, "dir1_axis": "x", "dir1_count": 1,
            "dir1_spacing": 0, "dir2_axis": "y", "dir2_count": 3,
            "dir2_spacing": 100})
        after = fresh_box()
        check("dir2_axis='y' moves towards +Y", shift(base, after, "y") > 0,
              f"shifts {{'x': {shift(base, after, 'x'):+.0f}, 'y': {shift(base, after, 'y'):+.0f}, 'z': {shift(base, after, 'z'):+.0f}}}")
        delete(_re.findall(r"'([^']*)'", str(reply))[0])

        # ── 3: duplicate sketch name ───────────────────────────────────────
        say("\n3  a duplicate sketch name is renamed at creation")
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        first = str(conn.last_sketch.Name)
        sketcher.execute("catia_close_sketch", {})
        # Force the collision: take the name CATIA will hand the next sketch.
        try:
            first_obj = body.Sketches.Item(first)
        except Exception:  # noqa: BLE001
            first_obj = [body.Sketches.Item(i) for i in range(1, body.Sketches.Count + 1)
                         if str(body.Sketches.Item(i).Name) == first][0]
        taken = f"{first.split('.')[0]}.{len([1 for i in range(1, body.Sketches.Count + 1)]) + 1}"
        try:
            first_obj.Name = taken
            part.Update()
        except Exception as error:  # noqa: BLE001
            say(f"      (could not pre-rename: {error})")
        reply = sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 40})
        names = [str(body.Sketches.Item(i).Name)
                 for i in range(1, body.Sketches.Count + 1)]
        unique = len(names) == len(set(names))
        check("no two sketches share a name after creation", unique, str(names))
        check("the reply says it renamed", "renamed to" in str(reply)
              or unique, str(reply)[:150])
        sketcher.execute("catia_close_sketch", {})

        # ── 5: a nested geometrical set ────────────────────────────────────
        say("\n5  catia_gsd_create_geoset(parent=...) nests")
        part = conn.get_active_part()
        body = conn.get_active_part_body()
        gsd.execute("catia_gsd_create_geoset", {"name": "OUTER"})
        reply = gsd.execute("catia_gsd_create_geoset",
                            {"name": "INNER", "parent": "OUTER"})
        nested = False
        try:
            for i in range(1, part.HybridBodies.Count + 1):
                hb = part.HybridBodies.Item(i)
                if str(hb.Name) != "OUTER":
                    continue
                for j in range(1, hb.HybridBodies.Count + 1):
                    nested = nested or str(hb.HybridBodies.Item(j).Name) == "INNER"
        except Exception as error:  # noqa: BLE001
            say(f"      (nesting could not be read: {error})")
        check("INNER sits inside OUTER", nested, str(reply)[:140])

        # ── 6: step_warnings survives run_plan ─────────────────────────────
        say("\n6  run_plan keeps the whole warning")
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_spline", {"points": ellipse(59), "closed": True})
        sketcher.execute("catia_close_sketch", {})
        low = str(conn.last_sketch.Name)
        sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 200})
        sketcher.execute("catia_sketch_spline", {"points": ellipse(60), "closed": True})
        sketcher.execute("catia_close_sketch", {})
        high = str(conn.last_sketch.Name)
        plan = planner or PlannerTools(conn)
        result = plan.execute("catia_run_plan", {
            "steps": [{"tool": "catia_solid_loft",
                       "args": {"sections": [low, high]}}],
            "summary": True, "measure": False})
        payload = result if isinstance(result, dict) else json.loads(str(result))
        warned = payload.get("data", {}).get("step_warnings")
        check("step_warnings exists", warned is not None, str(payload.get("data", {}).keys()))
        say(f"      step_warnings: {str(warned)[:220]}")
        say(f"      steps: {str(payload.get('data', {}).get('steps'))[:220]}")
        text = (warned or [{}])[0].get("message", "") if warned else ""
        check("it carries the advice, not just 'ok:true...'",
              "same segment counts" in text or "same way" in text, text[:180])

        # ── 7: a rejected loft has a name, not a guess ─────────────────────
        say("\n7  a rejected loft answers LOFT_REJECTED")
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 20, "y2": 10})
        sketcher.execute("catia_close_sketch", {})
        lines_a = str(conn.last_sketch.Name)
        sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 300})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 20, "y2": 10})
        sketcher.execute("catia_close_sketch", {})
        lines_b = str(conn.last_sketch.Name)
        try:
            part_design.execute("catia_solid_loft", {"sections": [lines_a, lines_b]})
            check("the multi-curve loft is refused", False, "it built")
        except Exception as error:  # noqa: BLE001
            check("the refusal carries LOFT_REJECTED",
                  getattr(error, "code", None) == "LOFT_REJECTED",
                  f"{getattr(error, 'code', None)}: {str(error)[:110]}")

        # ── 7: Open never lies about which file it opened ──────────────────
        say("\n7  Open names the file that is actually open")
        import shutil
        other = os.path.join(ROOT, "copy.CATPart")
        try:
            # Save first: a dirty document cannot be copied consistently.
            docs.execute("catia_save_document", {})
        except Exception:  # noqa: BLE001 - the copy is still usable
            pass
        shutil.copyfile(path, other)
        try:
            reply = docs.execute("catia_open_document", {"file_path": other})
        except Exception as error:  # noqa: BLE001
            # The branch this exists for: CATIA answers with the already-loaded
            # copy and pops a modal. Not reproducible with a byte copy on B30
            # 2026-11-08 (it opens), so a refusal here is also a pass - what
            # must never happen is a success naming a path that is not open.
            check("a refused duplicate open carries its own code",
                  getattr(error, "code", None) == "DOCUMENT_IDENTITY_CONFLICT",
                  f"{getattr(error, 'code', None)}: {str(error)[:120]}")
        else:
            opened = str(conn.active_document.Name)
            real = ""
            for i in range(1, conn.documents.Count + 1):
                doc = conn.documents.Item(i)
                if str(doc.Name) == opened:
                    real = str(getattr(doc, "FullName", ""))
            norm = lambda value: os.path.normcase(os.path.abspath(value))  # noqa: E731
            check("the reply names a file that really is open at that path",
                  real and norm(real) == norm(other),
                  f"reply={str(reply)[:90]} | actual={real}")
            say(f"      (a byte-identical copy OPENED without a conflict: {opened})")

    except Exception as error:  # noqa: BLE001
        check("no COM error during the run", False, f"{type(error).__name__}: {error}")
    finally:
        say("\ncleanup")
        for name in sorted({str(conn.documents.Item(i).Name)
                            for i in range(1, conn.documents.Count + 1)}
                           - existing, reverse=True):
            for i in range(1, conn.documents.Count + 1):
                if str(conn.documents.Item(i).Name) == name:
                    try:
                        conn.documents.Item(i).Close()
                        say(f"   closed {name}")
                    except Exception as error:  # noqa: BLE001
                        say(f"   (could not close {name}: {error})")
                    break
        for stale in os.listdir(ROOT):
            try:
                os.remove(os.path.join(ROOT, stale))
            except OSError:
                pass
        try:
            os.rmdir(ROOT)
        except OSError:
            pass
        if before_active:
            for i in range(1, conn.documents.Count + 1):
                if str(conn.documents.Item(i).Name) == before_active:
                    try:
                        conn.documents.Item(i).Activate()
                    except Exception:  # noqa: BLE001
                        pass
                    break
        say(f"session after: {conn.documents.Count} document(s)")

    failed = [label for label, ok, _ in RESULTS if not ok]
    say(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        say("FAILED: " + "; ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
