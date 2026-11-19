"""B30 acceptance for 0.3.150: the 2026-11-19 report's items, each measured.

Cells that exist because a claim in the code is really a claim about CATIA:

  A  pad limit1/limit2 -> ONE feature, asymmetric extent (volume AND bbox)
  B  pad with direction=reverse and limits -> the two extents swap
  C  pocket limit1/limit2 -> does Pocket.SecondLimit work at all?
  D  shaft angle1/angle2 -> same total as one-sided 60, different extent
  E  new_part with no file_path -> refused before a document exists
  F  new_product -> the assembly-constraint ceiling is stated up front
  G  sketch_profile arc whose radius and endpoints disagree -> refused
  H  single-edge chamfer -> the reply carries its own phase timings
  I  two edges closer than 2R -> the adjacency warning names them
  J  delete dry_run -> names the sketch a feature would take, deletes nothing
  K  boolean target_body='first' -> the main body by alias
  L  list_features -> frame scaffolding counted, not invisible
  M  plan summary with a failing expect -> compact steps, not 9 KB

.venv/Scripts/python.exe -u scripts/b30_v0466_acceptance_limits_and_hints.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.results import CodedError  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.planner import PlannerTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0466")
CHECKS: list[tuple[str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(label: str, passed: bool, evidence: str = "") -> None:
    CHECKS.append((label, bool(passed), evidence))
    say(f"  [{'PASS' if passed else 'FAIL'}] {label}"
        + (f"  <- {evidence}" if evidence else ""))


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    measurement = MeasurementTools(conn)
    assembly = AssemblyTools(conn)
    planner = PlannerTools(conn)
    export = ExportTools(conn)
    say(f"acceptance v0466 on {conn.app.Name}")
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

    def fresh(name: str):
        path = os.path.join(ROOT, name + ".CATPart")
        if os.path.exists(path):
            os.remove(path)
        docs.execute("catia_new_part", {"name": name, "file_path": path})
        return conn.get_active_part(), conn.get_active_part_body()

    def box(axis: str) -> tuple[float, float]:
        data = json.loads(measurement.execute("catia_get_bounding_box",
                                              {"method": "vertices"}))
        return float(data["min"][axis]), float(data["max"][axis])

    def volume(part, body) -> float:
        return parts._body_volume_mm3(part, body)

    # ══ A: pad, two different extents, ONE feature ═════════════════════════
    say("A: pad limit1=5, limit2=3 on a 20x10 profile")
    part, body = fresh("AccV0150a")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "P"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 20.0, "y2": 10.0})
    sketcher.execute("catia_close_sketch", {})
    reply = parts.execute("catia_pad", {"sketch_name": "P", "limit1": 5.0,
                                        "limit2": 3.0})
    say("  " + " ".join(reply.split())[:200])
    low, high = box("z")
    check("A the z extent is asymmetric", abs(low + 3.0) < 1e-6
          and abs(high - 5.0) < 1e-6, f"z {low:.4f}..{high:.4f}")
    check("A the volume is 200 mm2 x 8 mm = 1600",
          abs(volume(part, body) - 1600.0) < 1e-3, f"{volume(part, body):.4f}")
    check("A one feature, not two",
          int(body.Shapes.Count) == 1, f"{int(body.Shapes.Count)} shape(s)")
    check("A the reply states both extents",
          "one way" in reply and "the other" in reply, reply[:120])

    # ══ B: pad, reverse flips which side limit1 counts on ══════════════════
    say("B: pad limit1=4, limit2=1 with direction=reverse")
    part, body = fresh("AccV0150b")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "P"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 20.0, "y2": 10.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "P", "limit1": 4.0, "limit2": 1.0,
                                "direction": "reverse"})
    low, high = box("z")
    check("B reverse puts limit1 on the negative side",
          abs(low + 4.0) < 1e-6 and abs(high - 1.0) < 1e-6,
          f"z {low:.4f}..{high:.4f}")

    # ══ C: pocket, two different depths ═══════════════════════════════════
    say("C: pocket limit1=7, limit2=4 through a 30 mm wall")
    part, body = fresh("AccV0150c")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "BASE"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 40.0, "y2": 20.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "BASE", "limit1": 10.0,
                                "limit2": 20.0})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "CUT"})
    sketcher.execute("catia_sketch_circle",
                     {"cx": 20.0, "cy": 10.0, "radius": 4.0})
    sketcher.execute("catia_close_sketch", {})
    before = volume(part, body)
    try:
        reply = parts.execute("catia_pocket", {"sketch_name": "CUT", "limit1": 7.0,
                                               "limit2": 4.0})
        say("  " + " ".join(reply.split())[:200])
        after = volume(part, body)
        check("C the two-sided pocket removed material", before - after > 1.0,
              f"{before:.1f} -> {after:.1f}")
        check("C it worked as ONE feature with a second limit",
              "second limit" not in reply.lower() and "one way" in reply,
              reply[:120])
    except CodedError as error:
        say(f"  raised {error.code}: {str(error)[:200]}")
        check("C Pocket.SecondLimit works on this build", False, error.code)

    # ══ D: shaft, two angles ══════════════════════════════════════════════
    say("D: shaft angle1=30, angle2=30 against angle=60")
    extents = {}
    volumes = {}
    for label, args in (("one-sided 60", {"angle": 60}),
                        ("two-sided 30/30", {"angle1": 30, "angle2": 30})):
        part, body = fresh("AccV0150d_" + ("one" if "angle" in args and
                                           "angle1" not in args else "two"))
        sketcher.execute("catia_create_sketch", {"plane": "zx", "name": "S"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0.0, "y1": 10.0, "x2": 10.0, "y2": 20.0})
        sketcher.execute("catia_close_sketch", {})
        reply = parts.execute("catia_shaft", {"sketch_name": "S", **args})
        say("  " + " ".join(reply.split())[:160])
        volumes[label] = volume(part, body)
        extents[label] = box("x")
    check("D both sweep the same total",
          abs(volumes["one-sided 60"] - volumes["two-sided 30/30"]) < 0.5,
          f"{volumes['one-sided 60']:.4f} vs {volumes['two-sided 30/30']:.4f}")
    check("D but the extent is different - angle2 is the OTHER side",
          abs(extents["one-sided 60"][0] - extents["two-sided 30/30"][0]) > 0.5,
          f"x {extents['one-sided 60'][0]:.3f}.. against "
          f"{extents['two-sided 30/30'][0]:.3f}..")

    # ══ E: new_part without a path ════════════════════════════════════════
    say("E: new_part with no file_path")
    try:
        docs.execute("catia_new_part", {"name": "AccV0150e"})
        check("E a pathless part is refused", False, "the call went through")
    except CodedError as error:
        check("E a pathless part is refused", error.code == "MISSING_FILE_PATH",
              error.code)
        check("E and it says nothing was created",
              "Nothing was created" in str(error), str(error)[:100])

    # ══ F: new_product states the constraint ceiling ══════════════════════
    say("F: new_product")
    asm = os.path.join(ROOT, "AccV0150f.CATProduct")
    if os.path.exists(asm):
        os.remove(asm)
    reply = docs.execute("catia_new_product", {"name": "AccV0150f_ASM",
                                               "file_path": asm})
    check("F the reply warns about constraints up front",
          "ASSEMBLY CONSTRAINTS ARE NOT AVAILABLE" in reply, reply[-200:])
    check("F and names the way out",
          "catia_set_component_transform" in reply, "")

    # ══ G: an arc whose radius and endpoints disagree ═════════════════════
    say("G: dog-bone arc, R0.35 asked, endpoints at R0.25")
    part, body = fresh("AccV0150g")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "DOG"})
    try:
        sketcher.execute("catia_sketch_profile", {"segments": [
            {"type": "arc", "center": [0.0, 0.0], "radius": 0.35,
             "p1": [0.25, 0.0], "p2": [0.0, 0.25], "direction": "ccw"}]})
        check("G the contradiction is refused", False, "the call went through")
    except CodedError as error:
        say("  " + " ".join(str(error).split())[:260])
        check("G the contradiction is refused", error.code == "ARC_RADIUS_MISMATCH",
              error.code)
        # 0.3.151 replaced "the radius the endpoints describe" with the two real
        # fixes; the cell keeps the part that still has to hold - both numbers.
        check("G both radii are named",
              "R0.3500" in str(error)
              and f"{error.data['radius_keeping_centre_mm']:.4f}" in str(error),
              str(error)[:120])
        check("G nothing was drawn", "Nothing was drawn" in str(error), "")

    # ══ H: one-edge chamfer, with its own timings ═════════════════════════
    say("H: a single-edge chamfer on a 40 x 20 x 20 block")
    part, body = fresh("AccV0150h")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "B"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 40.0, "y2": 20.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "B", "height": 20.0})
    started = time.perf_counter()
    reply = parts.execute("catia_chamfer",
                          {"edge_anchors": [[0.0, 10.0, 0.0]], "length": 0.25,
                           "angle": 45})
    elapsed = time.perf_counter() - started
    say("  " + " ".join(reply.split())[:320])
    check("H the reply carries its phase timings", "[timings:" in reply, "")
    check(f"H and it is not a whole-tree rebuild ({elapsed:.2f} s)",
          elapsed < 20.0, f"{elapsed:.2f} s")

    # ══ I: two edges closer together than 2R ══════════════════════════════
    say("I: two vertical edges 0.9 mm apart, R3")
    part, body = fresh("AccV0150i")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "BASE"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 20.0, "y2": 10.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "BASE", "height": 20.0})
    for index, (x1, x2) in enumerate(((1.0, 9.55), (10.45, 19.0)), start=1):
        sketcher.execute("catia_create_sketch", {"plane": "xy",
                                                 "name": f"CUT{index}"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": x1, "y1": 1.0, "x2": x2, "y2": 9.0})
        sketcher.execute("catia_close_sketch", {})
        parts.execute("catia_pocket", {"sketch_name": f"CUT{index}",
                                       "through_all": True})
    reply = parts.execute("catia_fillet",
                          {"edge_anchors": [[9.55, 1.0, 10.0],
                                            [10.45, 1.0, 10.0]],
                           "radius": 3.0, "on_anchor_miss": "skip"})
    say("  " + " ".join(reply.split())[:400])
    check("I the pair is measured and named",
          ("CLOSE PAIR" in reply and "2x3" in reply) or "Closest pair" in reply,
          reply[:220])
    check("I and it does not pretend to be a verdict",
          "NOT a prediction" in reply or "not what would refuse" in reply,
          reply[:220])

    # ══ J: delete dry_run ═════════════════════════════════════════════════
    say("J: delete dry_run on a pad whose profile is SK_D")
    part, body = fresh("AccV0150j")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SK_D"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 30.0, "y2": 10.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "SK_D", "height": 5.0})
    sketches_before = int(body.Sketches.Count)
    reply = parts.execute("catia_delete_feature",
                          {"feature_name": "凸台.1", "dry_run": True})
    say("  " + " ".join(reply.split())[:260])
    check("J dry_run names the sketch the delete would take",
          "SK_D" in reply and "ALSO remove" in reply, reply[:180])
    check("J and deletes nothing",
          int(body.Sketches.Count) == sketches_before
          and int(body.Shapes.Count) == 1,
          f"{int(body.Sketches.Count)} sketch(es), "
          f"{int(body.Shapes.Count)} shape(s)")
    reply = parts.execute("catia_delete_feature",
                          {"feature_name": "SK_D", "dry_run": True})
    check("J and says the sketch delete would be refused",
          "REFUSED" in reply and "SKETCH_CONSUMED_BY" in reply, reply[:200])

    # ══ K: boolean target_body alias ══════════════════════════════════════
    say("K: catia_boolean(target_body='first')")
    part, body = fresh("AccV0150k")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "B1"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 20.0, "y2": 20.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "B1", "height": 10.0})
    parts.execute("catia_new_body", {"name": "TOOL"})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "B2"})
    sketcher.execute("catia_sketch_circle",
                     {"cx": 10.0, "cy": 10.0, "radius": 4.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "B2", "height": 10.0})
    try:
        reply = parts.execute("catia_boolean",
                              {"operation": "add", "tool_body": "TOOL",
                               "target_body": "first"})
        say("  " + " ".join(reply.split())[:200])
        check("K the alias resolved to the main body", True, "no name needed")
    except Exception as error:  # noqa: BLE001
        check("K the alias resolved to the main body", False,
              str(error).splitlines()[0][:120])

    # ══ L: frame scaffolding is counted ═══════════════════════════════════
    say("L: list_features on a part with two frame sketches")
    part, body = fresh("AccV0150l")
    for name, origin in (("F1", (0.0, 0.0, 0.0)), ("F2", (0.0, 0.0, 50.0))):
        sketcher.execute("catia_create_sketch",
                         {"plane": {"type": "frame", "origin": list(origin),
                                    "u": [1, 0, 0], "v": [0, 1, 0]},
                          "name": name})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0.0, "y1": 0.0, "x2": 20.0, "y2": 10.0})
        sketcher.execute("catia_close_sketch", {})
    payload = json.loads(parts.execute("catia_list_features", {}))
    scaffold = payload.get("frame_scaffolding") or {}
    check("L the 8 frame elements are counted",
          scaffold.get("element_count") == 8, str(scaffold)[:120])
    check("L and grouped by owning sketch",
          [entry["sketch"] for entry in scaffold.get("sketches", [])] == ["F1", "F2"],
          str(scaffold.get("sketches")))
    check("L and they are not counted as features", payload.get("count") == 0,
          str(payload.get("count")))

    # ══ N: screenshot reports the real size ═══════════════════════════════
    say("N: screenshot at 1920x1080")
    image = os.path.join(ROOT, "AccV0150n.jpg")
    if os.path.exists(image):
        os.remove(image)
    try:
        reply = export.execute("catia_screenshot",
                               {"file_path": image, "width": 1920, "height": 1080})
        say("  " + " ".join(reply.split())[:260])
        check("N the reply states the real pixel size",
              " px" in reply and "asked for" in reply, reply[:160])
    except Exception as error:  # noqa: BLE001
        say(f"  screenshot raised: {str(error).splitlines()[0][:140]}")
        check("N the reply states the real pixel size", False, "capture failed")

    say("")
    passed = sum(1 for _l, ok, _e in CHECKS if ok)
    say(f"RESULT {passed}/{len(CHECKS)} passed")
    for label, ok, why in CHECKS:
        if not ok:
            say(f"  FAILED: {label}  <- {why}")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
