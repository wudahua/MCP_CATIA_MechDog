"""0.3.125 acceptance on B30: the 2026-11-14 report's P0-2, P0-3, P1-2, P2-4.

Eleven checks, one fresh document per case.

   1  catia_sketch_ellipse is DRIVEN by default: two constraints, and the reply
      quotes the parameter path it read from catia_get_parameters
   2  catia_sketch_get_geometry answers major_radius_mm / minor_radius_mm
   3  catia_set_constraint_value 9 -> 12 moves the ellipse, and the pad with it
      (pi*12*4.5*10 = 1696.5 mm3)
   4  catia_sketch_circle is driven too: one radius constraint
   5  driven=false leaves the curve with no constraint
   6  a user parameter drives a semi-axis: TipMajor 9 -> 14 gives pi*14*4.5*10
   7  a parameter that a formula writes is refused with PARAMETER_FORMULA_DRIVEN
      and the relation's name - not a bare 方法 Value 失败
   8  after deleting that relation the same set_parameter succeeds
   9  a solid loft renamed by the caller still reports type "loft"
  10  a Fix constraint is refused as NOT_A_DIMENSION, with nothing changed
  11  the router holds 148 tools

    .venv/Scripts/python.exe scripts/b30_v0427_audit.py
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.knowledge import KnowledgeTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0427")
ROOT = os.path.join(_PROBE_ROOT, "audit")
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


def fresh(conn, documents, name):
    path = os.path.join(ROOT, f"{name}.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    for index in range(1, int(conn.documents.Count) + 1):
        if conn.documents.Item(index).Name == f"{name}.CATPart":
            conn.documents.Item(index).Activate()
    return conn.get_active_part()


def parameters(measurement) -> list[dict]:
    return json.loads(measurement.execute("catia_get_parameters", {}))


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    measurement = MeasurementTools(conn)
    knowledge = KnowledgeTools(conn)

    # ── 1/2/3: the ellipse, driven, read, and changed ───────────────────
    say("")
    say("── 1: catia_sketch_ellipse with its default driven=true ──")
    part = fresh(conn, documents, "AuditEllipse")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    reply = str(sketcher.execute("catia_sketch_ellipse",
                                 {"cx": 0, "cy": 0, "major": 9, "minor": 4.5}))
    text = " ".join(reply.split())
    say("  " + text[:300])
    check(1, "both semi-axes became constraints, and the path is quoted",
          "DRIVEN" in text and "semi-major" in text and "半径" in text
          and "\\半径" in text, "path present")

    geometry = json.loads(sketcher.execute("catia_sketch_get_geometry", {}))
    ellipse = next((item for item in geometry["elements"]
                    if item.get("major_radius_mm")), None)
    check(1, "get_geometry reports both semi-axes", ellipse is not None
          and abs(ellipse["major_radius_mm"] - 9) < 1e-9
          and abs(ellipse["minor_radius_mm"] - 4.5) < 1e-9,
          f"{ellipse.get('major_radius_mm')}/{ellipse.get('minor_radius_mm')}"
          if ellipse else "not found")
    say(f"  constraints: {geometry['constraints']}")
    check(2, "and lists the constraints with their values",
          len(geometry["constraints"]) == 2
          and all("value_mm" in item for item in geometry["constraints"]),
          str([item.get("value_mm") for item in geometry["constraints"]]))

    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})
    before = part_design._body_volume_mm3(part, part.MainBody)
    reply = str(sketcher.execute("catia_set_constraint_value",
                                 {"constraint": "半径.1", "value": 12}))
    after = part_design._body_volume_mm3(part, part.MainBody)
    texture = " ".join(reply.split())
    say("  " + texture[:240])
    check(3, "9 -> 12 mm moves the ellipse AND the solid",
          before is not None and after is not None
          and abs(before - math.pi * 9 * 4.5 * 10) < 2.0
          and abs(after - math.pi * 12 * 4.5 * 10) < 2.0,
          f"{before:.1f} -> {after:.1f}; expected {math.pi * 12 * 4.5 * 10:.1f}")
    check(3, "and the reply says which dimension moved",
          "major_radius 9 -> 12" in texture, "movement reported")

    # ── 4/5: the circle, driven and not ─────────────────────────────────
    say("")
    say("── 4: catia_sketch_circle, driven vs driven=false ──")
    part = fresh(conn, documents, "AuditCircle")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": 6})
    sketcher.execute("catia_sketch_circle",
                     {"cx": 40, "cy": 0, "radius": 6, "driven": False})
    geometry = json.loads(sketcher.execute("catia_sketch_get_geometry", {}))
    constrained = [item for item in geometry["constraints"]
                   if item.get("value_mm") is not None]
    check(4, "one radius constraint for the driven circle",
          len(constrained) == 1 and abs(constrained[0]["value_mm"] - 6.0) < 1e-9,
          str([item.get("value_mm") for item in constrained]))
    check(5, "and driven=false adds nothing",
          len(geometry["constraints"]) == 1,
          f"{len(geometry['constraints'])} constraint(s) for 2 circles")

    # ── 6: a user parameter drives a semi-axis ──────────────────────────
    say("")
    say("── 6: TipMajor as a length parameter driving the semi-major ──")
    part = fresh(conn, documents, "AuditDrive")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_ellipse",
                     {"cx": 0, "cy": 0, "major": 9, "minor": 4.5})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})
    knowledge.execute("catia_create_parameter",
                      {"name": "TipMajor", "type": "length", "value": 9})
    names = [item["name"] for item in parameters(measurement)]
    target = next((name for name in names
                   if "半径.1" in name and name.endswith("半径")), None)
    say(f"  target parameter: {target}")
    knowledge.execute("catia_set_formula",
                      {"name": "DriveTip", "target_parameter": target,
                       "expression": "TipMajor"})
    measurement.execute("catia_set_parameter", {"name": "TipMajor", "value": 14})
    volume = part_design._body_volume_mm3(part, part.MainBody)
    check(6, "the parameter moves the geometry: pi*14*4.5*10",
          volume is not None and abs(volume - math.pi * 14 * 4.5 * 10) < 2.0,
          f"{volume:.1f} vs {math.pi * 14 * 4.5 * 10:.1f}"
          if volume is not None else "unreadable")

    # ── 7/8: a formula-driven parameter ─────────────────────────────────
    say("")
    say("── 7: a parameter that a relation WRITES ──")
    part = fresh(conn, documents, "AuditLocked")
    knowledge.execute("catia_create_parameter",
                      {"name": "StemLength", "type": "length", "value": 23.8})
    knowledge.execute("catia_set_formula",
                      {"name": "DriveStem",
                       "target_parameter": "AuditLocked\\StemLength",
                       "expression": "23.8mm"})
    code = ""
    message = ""
    data = {}
    try:
        measurement.execute("catia_set_parameter",
                            {"name": "StemLength", "value": 30})
    except Exception as error:  # noqa: BLE001
        code = str(getattr(error, "code", ""))
        message = " ".join(str(error).split())
        data = dict(getattr(error, "data", {}) or {})
    say("  " + (code + ": " + message[:220] if code else "NOT REFUSED"))
    check(7, "refused by name, with the relation that drives it",
          code == "PARAMETER_FORMULA_DRIVEN"
          and data.get("relations") == ["DriveStem"]
          and "方法 Value 失败" in message, str(data.get("relations")))
    knowledge.execute("catia_delete_relation", {"name": "DriveStem"})
    reply = str(measurement.execute("catia_set_parameter",
                                    {"name": "StemLength", "value": 30}))
    check(8, "and after removing the relation the same call succeeds",
          "23.8" in reply and "30" in reply, " ".join(reply.split())[:80])

    # ── 9: a renamed loft ───────────────────────────────────────────────
    say("")
    say("── 9: a solid loft named AirPods_Body ──")
    part = fresh(conn, documents, "AuditRenamed")
    for offset, radius in ((0, 8), (30, 14)):
        sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": offset})
        sketcher.execute("catia_sketch_circle",
                         {"cx": 0, "cy": 0, "radius": radius, "driven": False})
        sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_solid_loft",
                        {"sections": ["草图.1", "草图.2"], "name": "AirPods_Body"})
    listing = json.loads(part_design.execute("catia_list_features", {}))
    kinds = {item["name"]: item["type"] for item in listing["features"]}
    say(f"  features: {kinds}")
    check(9, "a caller's own name does not hide the type",
          kinds.get("AirPods_Body") == "loft", str(kinds.get("AirPods_Body")))

    # ── 10: a constraint with no value ──────────────────────────────────
    say("")
    say("── 10: setting a Fix constraint ──")
    part = fresh(conn, documents, "AuditFix")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_point", {"x": 0, "y": 0})
    sketch = part.MainBody.Sketches.Item(int(part.MainBody.Sketches.Count))
    point = sketch.GeometricElements.Item(int(sketch.GeometricElements.Count))
    sketch.Constraints.AddMonoEltCst(0, point)
    code = ""
    message = ""
    try:
        sketcher.execute("catia_set_constraint_value",
                         {"constraint": 1, "value": 5})
    except Exception as error:  # noqa: BLE001
        code = str(getattr(error, "code", ""))
        message = " ".join(str(error).split())
    say("  " + (code + ": " + message[:200] if code else "NOT REFUSED"))
    check(10, "a Fix has no value to set, and the reply says so",
          code == "NOT_A_DIMENSION", code or "NOT REFUSED")

    # ── 11: the router ──────────────────────────────────────────────────
    say("")
    from catia_mcp.server import CATIAMCPServer

    count = len(CATIAMCPServer()._tool_router)
    check(11, "the router holds 148 tools", count == 148, str(count))

    say("")
    failed = [row for row in RESULTS if not row[2]]
    say(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    for number, title, _, detail in failed:
        say(f"  FAILED {number}: {title} ({detail})")
    close_ours(conn)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
