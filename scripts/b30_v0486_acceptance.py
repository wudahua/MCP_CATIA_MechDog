"""0.3.152 acceptance on B30, 2026-11-19: the six items of the 0.3.151 report.

Each cell measures the thing the report complained about, on a real CATIA, one
new document per cell (a failure is document-level):

  A  transform_13 - a column-major 13-tuple with the translation in METRES
     places things where the maths says, and the read-back reports how far a
     transposed feed would have been (180 deg for a 90 deg turn about Z).
  B  strict_arc refuses the web arc instead of moving its centre 0.0028 mm, and
     says the deviation; the default still keeps R72.3670.
  C  a 582-vertex part: method='vertices' stops at 200 and SAYS so, while
     method='mesh' reaches the side it never visited - the numbers, not the
     adjectives, show the truncation.
  D  save_as_copy(part_number=...) makes the COPY carry the new number, so an
     inserted instance is named after the copy and not after the original.
  E  the four constraint tools refuse with the new evidence in the message, and
     catia_diagnose publishes the ceiling entry a plan can read first.
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.diagnostics import DiagnosticsTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "catia_probe_v0486")
os.makedirs(ROOT, exist_ok=True)
CHECKS: list[tuple[str, bool, str]] = []


def say(text: str) -> None:
    print(text, flush=True)


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), detail))
    say(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def fresh_part(docs, name: str) -> str:
    try:
        while True:
            docs.execute("catia_close_document", {"save": False})
    except Exception:  # noqa: BLE001
        pass
    path = os.path.join(ROOT, f"{name}.CATPart")
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    return path


def payload(reply) -> dict:
    return reply if isinstance(reply, dict) else json.loads(reply)


def outline(n: int = 194) -> list[list[float]]:
    points = []
    for i in range(n):
        angle = 2 * math.pi * i / n
        radius = 40 + 8 * math.cos(5 * angle)
        points.append([round(radius * math.cos(angle), 4),
                       round(radius * math.sin(angle), 4)])
    points.append(points[0])
    return points


def build_lug(docs, sketcher, parts, name: str) -> str:
    path = fresh_part(docs, name)
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "P"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 20,
                                                "y2": 10})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "P", "height": 5})
    docs.execute("catia_save_document", {})
    return path


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    app = conn.app
    docs = DocumentTools(conn)
    asm = AssemblyTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    measurement = MeasurementTools(conn)
    diagnostics = DiagnosticsTools(conn)
    say(f"acceptance v0486 on {app.Name}")

    # ══ A: transform_13, column-major, translation in metres ══════════════
    say("\nA: transform_13 = 90 deg about Z, translation (100, 200, 300) mm = 0.1/0.2/0.3 m")
    lug = build_lug(docs, sketcher, parts, "TxLug")
    asm_path = os.path.join(ROOT, "TxAsm.CATProduct")
    if os.path.exists(asm_path):
        os.remove(asm_path)
    docs.execute("catia_new_product", {"name": "TxAsm", "file_path": asm_path})
    asm.execute("catia_add_component", {"file_path": lug, "count": 1})
    product = conn.get_active_product()
    name = product.Products.Item(1).Name
    # columns: x -> y = (0,1,0); y -> -x = (-1,0,0); z -> z
    thirteen = [0, 1, 0,   -1, 0, 0,   0, 0, 1,   0.1, 0.2, 0.3,   1]
    reply = payload(asm.execute("catia_set_component_transform", {
        "component_name": name, "transform_13": thirteen,
        "translation_unit": "m"}))
    say("  " + str(reply.get("source", ""))[:150])
    verified = reply.get("verified", {})
    say(f"  verified: {verified.get('position_components')} "
        f"dev={verified.get('max_deviation')}")
    check("A the translation arrived in mm",
          verified.get("position_components", [0] * 12)[9:12]
          == [100.0, 200.0, 300.0],
          str(verified.get("position_components", [0] * 12)[9:12]))
    check("A the rotation is the one the columns describe",
          verified.get("basis_columns", {}).get("local_x_maps_to")
          == [0.0, 1.0, 0.0],
          str(verified.get("basis_columns", {}).get("local_x_maps_to")))
    check("A the read-back is within tolerance", verified.get("matches_request") is True)
    check("A a transposed feed would have been 180 deg out",
          abs(verified.get("if_read_transposed", {}).get("differs_by_deg", 0) - 180.0) < 1e-6,
          str(verified.get("if_read_transposed")))
    read_back = payload(asm.execute("catia_get_component_transform",
                                    {"component_name": name}))
    check("A get_component_transform reports the same two readings",
          "if_read_transposed" in read_back.get("basis_columns", {}) or
          "if_read_transposed" in read_back,
          str(list(read_back)[:6]))

    # ══ B: strict_arc ═════════════════════════════════════════════════════
    say("\nB: the report's web arc, ends 0.0027 mm off R72.367")
    fresh_part(docs, "StrictArc")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "WEB"})
    strict_args = {"segments": [
        {"type": "arc", "center": [15.0, 0.0], "radius": 72.367,
         "p1": [-55.222, 17.5], "p2": [-55.222, -17.5], "direction": "ccw"},
        {"type": "line", "p1": [-55.222, -17.5], "p2": [-55.222, 17.5]},
    ], "strict_arc": True}
    try:
        sketcher.execute("catia_sketch_profile", strict_args)
        check("B strict_arc refuses the contradiction", False, "it was drawn")
    except Exception as error:  # noqa: BLE001
        text = " ".join(str(error).split())
        say("  refusal: " + text[:260])
        check("B strict_arc refuses with ARC_ENDPOINT_OFF_CIRCLE",
              getattr(error, "code", "") == "ARC_ENDPOINT_OFF_CIRCLE",
              getattr(error, "code", "?"))
        check("B the deviation is named to 1e-6 mm", "0.002" in text, text[:90])
    geometry = payload(sketcher.execute("catia_sketch_get_geometry",
                                        {"sketch_name": "WEB"}))
    check("B nothing was drawn by the refused call",
          not geometry.get("elements") or len(geometry["elements"]) <= 1,
          f"{len(geometry.get('elements') or [])} element(s)")

    reply = sketcher.execute("catia_sketch_profile", {
        "segments": strict_args["segments"]})
    loose = " ".join(str(reply).split())
    check("B the default still keeps R72.3670", "R72.3670 is KEPT" in loose, loose[:120])
    geometry = payload(sketcher.execute("catia_sketch_get_geometry",
                                        {"sketch_name": "WEB"}))
    radii = [float(e["radius_mm"]) for e in geometry.get("elements", [])
             if e.get("radius_mm") is not None]
    check("B and the arc really is R72.367", any(abs(r - 72.367) <= 1e-3 for r in radii),
          str(radii))

    # ══ C: the 200-vertex cap is published, and the mesh sees past it ═════
    say("\nC: 194-point outline, 582 vertices")
    path = fresh_part(docs, "Heavy")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "OUT"})
    sketcher.execute("catia_sketch_strokes", {"strokes": [outline()]})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "OUT", "height": 6})
    docs.execute("catia_save_document", {})
    vertices = payload(measurement.execute("catia_get_bounding_box",
                                           {"method": "vertices"}))
    say(f"  vertices: {vertices.get('min')} .. {vertices.get('max')} "
        f"({vertices.get('elapsed_ms')} ms)")
    say("  summary: " + str(vertices.get("summary", ""))[:180])
    check("C the first key of the reply is the summary",
          list(vertices)[0] == "summary", str(list(vertices)[:3]))
    check("C the cap is reported as truncation",
          vertices.get("bounds_code") == "vertex_sweep_truncated",
          str(vertices.get("bounds_code")))
    check("C how many vertices were missed is a number",
          (vertices.get("vertices_missed") or 0) > 0,
          str(vertices.get("vertices_missed")))
    check("C exact_envelope is false", vertices.get("exact_envelope") is False)
    mesh = payload(measurement.execute("catia_get_bounding_box", {"method": "mesh"}))
    say(f"  mesh:     {mesh.get('min')} .. {mesh.get('max')} "
        f"({mesh.get('elapsed_ms')} ms)")
    check("C the truncated sweep is short on a side the mesh reaches",
          float(mesh["max"]["y"]) - float(vertices["max"]["y"]) > 1.0,
          f"vertices max y {vertices['max']['y']} vs mesh {mesh['max']['y']}")
    check("C the mesh is the cheaper route on this part",
          float(mesh.get("elapsed_ms") or 0) < float(vertices.get("elapsed_ms") or 1),
          f"{mesh.get('elapsed_ms')} ms vs {vertices.get('elapsed_ms')} ms")

    # ══ D: the copy carries its own part number ═══════════════════════════
    say("\nD: save_as_copy(part_number=...) then insert the copy")
    original = build_lug(docs, sketcher, parts, "P05_legrod")
    copy = os.path.join(ROOT, "P06_mir_legrod.CATPart")
    if os.path.exists(copy):
        os.remove(copy)
    reply = docs.execute("catia_save_as_copy",
                         {"file_path": copy, "part_number": "P06_mir_legrod"})
    text = " ".join(str(reply).split())
    say("  " + text[:260])
    check("D the copy's number was rewritten", "P06_mir_legrod" in text, text[:120])
    asm2 = os.path.join(ROOT, "CopyAsm.CATProduct")
    if os.path.exists(asm2):
        os.remove(asm2)
    docs.execute("catia_new_product", {"name": "CopyAsm", "file_path": asm2})
    asm.execute("catia_add_component", {"file_path": copy, "count": 1})
    product2 = conn.get_active_product()
    inserted = product2.Products.Item(1)
    say(f"  inserted: {inserted.Name!r} part_number={inserted.PartNumber!r}")
    check("D the instance is named after the copy, not the original",
          str(inserted.Name).startswith("P06_mir_legrod"),
          str(inserted.Name))
    check("D and its part number is the new one",
          str(inserted.PartNumber) == "P06_mir_legrod", str(inserted.PartNumber))

    # ══ E: the refusal says what was measured, and diagnose publishes it ══
    say("\nE: catia_fix_constraint on a real component")
    try:
        asm.execute("catia_fix_constraint", {"component_name": inserted.Name})
        check("E the constraint is refused", False, "it was created")
    except Exception as error:  # noqa: BLE001
        text = " ".join(str(error).split())
        say("  " + text[:300])
        for said in ("GetWorkbenchId", "SKETCH", "type code 0-13",
                     "Selection.Item(1).Reference", "unknown command"):
            check(f"E the refusal names '{said}'", said in text)
    report = payload(diagnostics.execute("catia_diagnose", {}))
    ceilings = report.get("capability_ceilings", [])
    entry = next((e for e in ceilings
                  if str(e.get("member", "")).startswith("CATIAConstraints")), None)
    check("E diagnose publishes the ceiling entry", entry is not None)
    if entry:
        check("E the entry names the tools and the evidence",
              "catia_fix_constraint" in entry["detail"]
              and "SKETCH" in entry["detail"])

    say("")
    passed = sum(1 for _label, ok, _detail in CHECKS if ok)
    say(f"{passed}/{len(CHECKS)} checks passed")
    for label, ok, detail in CHECKS:
        if not ok:
            say(f"  FAILED: {label}  {detail}")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
