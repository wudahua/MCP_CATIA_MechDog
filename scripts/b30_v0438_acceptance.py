"""0.3.137 acceptance on B30 - the two 2026-11-15 reports on 0.3.136.

    A  two views 70 mm apart on the sheet, both on 'xy': NOT aligned, and the
       world boxes print the right way round (N1, N2)
    B  the same two views with align={'view': 1, 'axis': 'x'}: aligned, and two
       pads share an X range (N3)
    C  a refused sketch name leaves no sketch behind (N4)
    D  deviation=true leaves no empty MCP_Probes (N5)
    E  a solid in a second body is measured instead of "no solid shape yet" (C3)
    F  activate_document says the work body is empty and names the one that is
       not (C4)
    G  a named boolean reports type 'boolean' (C6)
    H  a plan that only activates a document attributes nothing to itself (C7)
    I  the report's collapsing sweep warns about the NEGATIVE gap (C1)
    J  a loft closing point is honoured (C2)

    .venv/Scripts/python.exe -u scripts/b30_v0438_acceptance.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.blueprint import BlueprintTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0438a")
ROWS: list[tuple[str, bool, str]] = []

# Two views SIDE BY SIDE, as a real sheet draws them: 70 mm apart in x.
SHEET = b'''<svg xmlns="http://www.w3.org/2000/svg" width="200mm" height="120mm"
viewBox="0 0 200 120"><rect x="10" y="10" width="60" height="40"/>
<rect x="80" y="10" width="60" height="25"/></svg>'''


def say(line: str = "") -> None:
    print(line, flush=True)


def cell(what: str, ok: bool, detail: str = "") -> None:
    ROWS.append((what, ok, detail))
    say(f"  [{'OK  ' if ok else 'FAIL'}] {what}" + (f"  -> {detail}" if detail else ""))


def close_ours(conn) -> None:
    root = os.path.abspath(ROOT).lower()
    for index in range(int(conn.documents.Count), 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = str(doc.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).lower().startswith(root):
            try:
                doc.Close()
            except Exception:  # noqa: BLE001
                pass


def fresh(conn, docs, name: str, keep: bool = False) -> str:
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, name + ".CATPart")
    if not keep:
        close_ours(conn)
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    return path


def sheet() -> str:
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, "side_by_side.svg")
    with open(path, "wb") as handle:
        handle.write(SHEET)
    return path


def box(measurement) -> dict:
    return json.loads(measurement.execute("catia_get_bounding_box",
                                          {"method": "vertices"}))


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    blueprint = BlueprintTools(conn)
    sketcher = SketcherTools(conn)
    parts = PartDesignTools(conn)
    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)
    svg = sheet()
    say(f"MCP acceptance v0.3.137 on {conn.app.Name}")

    # ── A: a real sheet, aligned by nothing ────────────────────────────────
    say("\nA. two views 70 mm apart, both on 'xy'")
    fresh(conn, docs, "Acc137Unaligned")
    reply = blueprint.execute("catia_import_drawing", {
        "file_path": svg,
        "views": [
            {"region": [0, -60, 75, 0], "plane": "xy", "name": "V1"},
            {"region": [75, -60, 200, 0], "plane": "xy", "name": "V2"},
        ]})
    check = re.search(r"Alignment check: ([^.]{0,220})", reply)
    say("        " + " ".join(reply.split())[:420])
    cell("A1 the verdict is NOT aligned", "NOT aligned" in reply,
         check.group(1) if check else "no sentence")
    cell("A2 it is not the false positive any more",
         "agree on Z" not in reply and "agree on Y (0.000..0.000" not in reply)
    boxes = re.findall(r"world box X (-?\d+\.\d+)\.\.(-?\d+\.\d+) Y "
                       r"(-?\d+\.\d+)\.\.(-?\d+\.\d+) Z (-?\d+\.\d+)\.\.(-?\d+\.\d+)",
                       reply)
    cell("A3 every world box prints min before max",
         bool(boxes) and all(float(low) <= float(high) + 1e-9
                             for row in boxes for low, high in
                             ((row[0], row[1]), (row[2], row[3]), (row[4], row[5]))),
         f"{len(boxes)} box(es): {boxes[0] if boxes else None}")
    cell("A4 the X ranges are the sheet's, not bbox-normalised",
         bool(boxes) and abs(float(boxes[0][1]) - 60.0) < 1e-6
         and abs(float(boxes[1][0]) - 70.0) < 1e-6,
         f"V1 X ..{boxes[0][1]} V2 X {boxes[1][0]}.." if boxes else "no boxes")

    # ── B: the same two views, aligned by one argument ─────────────────────
    say("\nB. align={'view': 1, 'axis': 'x', 'anchor': 'max'} moves view 2 onto it")
    fresh(conn, docs, "Acc137Aligned")
    reply = blueprint.execute("catia_import_drawing", {
        "file_path": svg,
        "views": [
            {"region": [0, -60, 75, 0], "plane": "xy", "name": "V1"},
            {"region": [75, -60, 200, 0], "plane": "xy", "name": "V2",
             "align": {"view": 1, "axis": "x", "anchor": "min"}},
        ]})
    check = re.search(r"Alignment check: ([^.]{0,200})", reply)
    cell("B1 the reply says they agree on X", "agree on X" in reply,
         check.group(1) if check else "no sentence")
    cell("B2 the move is reported with its amount",
         "moved -70.0000 mm along X" in reply,
         " | ".join(line for line in reply.split(". ") if "moved" in line)[:120])
    front = parts.execute("catia_pad", {"height": 30, "sketch_name": "V1"})
    top = parts.execute("catia_pad", {"height": 20, "sketch_name": "V2"})
    cell("B3 both views pad", "Pad created" in front and "Pad created" in top)
    whole = box(measurement)
    low = float((whole.get("min") or {}).get("x", 999))
    high = float((whole.get("max") or {}).get("x", -999))
    cell("B4 both solids start at the anchor's X, not 70 mm apart",
         abs(low - 0.0) < 0.01 and abs(high - 60.0) < 0.01,
         f"X {low:.4f}..{high:.4f} - the anchor is at sheet x=10")

    # ── C: a refused name leaves nothing behind ────────────────────────────
    say("\nC. a refused sketch name")
    fresh(conn, docs, "Acc137Name")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "Taken"})
    sketcher.execute("catia_close_sketch", {})
    before = len(json.loads(parts.execute("catia_list_features", {})).get("sketches", []))
    try:
        sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "Taken"})
        cell("C1 the second 'Taken' is refused", False, "it was accepted")
    except Exception as error:  # noqa: BLE001
        text = str(error)
        cell("C1 the second 'Taken' is refused", "SKETCH_NAME_TAKEN" in text
             or "already holds a sketch" in text, text.splitlines()[0][:110])
        cell("C2 the refusal says the part is unchanged",
             "DELETED again" in text, " | ".join(
                 line for line in text.split(". ") if "DELETED" in line)[:110])
    after = len(json.loads(parts.execute("catia_list_features", {})).get("sketches", []))
    cell("C3 no sketch was left behind", after == before, f"{before} -> {after}")

    # ── D: no empty MCP_Probes after a deviation measurement ───────────────
    say("\nD. deviation=true cleans up after itself")
    fresh(conn, docs, "Acc137Probes")
    blueprint.execute("catia_import_drawing",
                      {"file_path": svg, "region": [0, -60, 75, 0],
                       "plane": "xy", "profile_mode": "spline"})
    features = json.loads(parts.execute("catia_list_features", {}))
    sets = [item.get("name") for item in features.get("geometrical_sets") or []]
    cell("D1 no MCP_Probes set is left", "MCP_Probes" not in sets, f"sets={sets}")

    # ── E: a solid in a second body ────────────────────────────────────────
    say("\nE. the solid lives in a second body")
    fresh(conn, docs, "Acc137SecondBody")
    parts.execute("catia_new_body", {"name": "SideBody"})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SideSketch"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 20, "y2": 10})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 5, "sketch_name": "SideSketch"})
    reply = measurement.execute("catia_get_bounding_box", {"method": "vertices"})
    payload = json.loads(reply)
    cell("E1 the box is measured instead of refused",
         payload.get("body") == "SideBody", f"body={payload.get('body')}")
    cell("E2 the reply says why", "main body is empty" in str(payload.get("scope")),
         str(payload.get("scope"))[:120])
    dims = payload.get("dimensions") or {}
    cell("E3 and it is the right box",
         abs(float(dims.get("length_x", 0)) - 20.0) < 0.01,
         str(dims))

    # ── F: activate_document says which body is in work ────────────────────
    say("\nF. activate_document, switching in from another document")
    fresh(conn, docs, "Acc137Other", keep=True)
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "OtherSketch"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 5, "y2": 5})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 5, "sketch_name": "OtherSketch"})
    # Put the EMPTY main body in work before switching away: that is the state
    # report C4 came back to, and the state this note exists for.
    parts.execute("catia_set_active_body", {"name": "零件几何体"})
    docs.execute("catia_activate_document", {"name": "Acc137Other"})
    reply = docs.execute("catia_activate_document", {"name": "Acc137SecondBody"})
    say("        " + " ".join(reply.split())[:300])
    cell("F1 it says the work body holds no solid",
         "holds NO solid" in reply, " ".join(reply.split())[:150])
    cell("F2 and names the body that does", "SideBody" in reply)

    # ── G: a named boolean has a type ──────────────────────────────────────
    say("\nG. a boolean named by the caller")
    fresh(conn, docs, "Acc137Boolean", keep=True)
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "Base"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 40, "y2": 20})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 10, "sketch_name": "Base"})
    parts.execute("catia_new_body", {"name": "ToolBody"})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "Tongue",
                                             "offset": 10})
    sketcher.execute("catia_sketch_rectangle", {"x1": 10, "y1": 5, "x2": 30, "y2": 15})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 10, "sketch_name": "Tongue"})
    parts.execute("catia_boolean", {"operation": "add", "tool_body": "ToolBody",
                                    "target_body": "零件几何体",
                                    "name": "Unified"})
    features = json.loads(parts.execute("catia_list_features", {}))
    kinds = {item.get("name"): item.get("type") for item in features.get("features", [])}
    cell("G1 the boolean reports a type", kinds.get("Unified") == "boolean",
         f"types={kinds}")

    # ── H: a plan that only activates a document ───────────────────────────
    say("\nH. run_plan across documents")
    from catia_mcp.tools.planner import PlannerTools  # noqa: PLC0415

    planner = PlannerTools(conn)
    router: dict[str, object] = {}
    for module in (docs, sketcher, parts, gsd, measurement, blueprint):
        for definition in module.get_tool_definitions():
            router[definition["name"]] = module
    planner.bind(router)
    plan = planner.execute("catia_run_plan", {"steps": [
        {"tool": "catia_activate_document", "args": {"name": "Acc137SecondBody"}},
        {"tool": "catia_set_parameter",
         "args": {"name": "NoSuchParameter", "value": 1}},
    ], "stop_on_error": True, "summary": True})
    say("        " + " ".join(str(plan).split())[:420])
    cell("H1 no pre-existing element is attributed to the plan",
         "element(s) this plan had already built" not in str(plan))
    cell("H2 the status says nothing is attributable",
         "not_attributable" in str(plan) or "Nothing is attributed" in str(plan))

    # ── I: the collapsing sweep ────────────────────────────────────────────
    say("\nI. the report's collapsing sweep")
    fresh(conn, docs, "Acc137Collapse")
    gsd.execute("catia_gsd_spline", {
        "points": [[-1.2, 0, 0], [-0.9, 0, 8], [-0.5, 0, 16], [0, 0, 23.8],
                   [1.2, 0, 26.5], [3.2, 0, 29], [5, 0, 32], [5.5, 0, 35],
                   [5.5, 0, 38], [5.5, 0, 40.5]],
        "name": "BodySpine", "closed": False, "deviation": False})
    reply = gsd.execute("catia_solid_sweep_variable", {
        "spine": "BodySpine",
        "sections": [[0, 2.4, 2.1], [0.02, 2.5, 2.2], [0.55, 2.5, 2.2],
                     [0.6, 2.7, 2.4], [0.66, 3.8, 3.3], [0.72, 5.6, 4.9],
                     [0.78, 7, 6], [0.84, 7.6, 6.3], [0.89, 7.2, 6],
                     [0.93, 6, 5.1], [0.96, 4.2, 3.7], [0.985, 2.2, 2],
                     [1, 0.6, 0.6]],
        "major_axis": [1, 0, 0], "name": "Collapsed", "helpers": "hide"})
    say("        " + " ".join(str(reply).split())[:500])
    cell("I1 the volume agreement no longer claims to prove the shape",
         "does NOT mean the surface cannot intersect itself" in str(reply))
    cell("I2 the steep growth between stations is named",
         "STEEP GROWTH" in str(reply),
         " | ".join(part for part in str(reply).split(". ")
                    if "STEEP" in part)[:160])
    cell("I3 the warning names what to check with",
         "catia_shape_audit" in str(reply) and "narrowest_bins" in str(reply))
    growth = re.search(r"(\d+\.\d+) mm of section radius per mm", str(reply))
    cell("I4 the growth rate is the measured one",
         growth is not None and float(growth.group(1)) >= 0.5,
         f"rate={growth.group(1) if growth else '?'}")

    # ── J: the loft closing point ──────────────────────────────────────────
    say("\nJ. a loft closing point")
    fresh(conn, docs, "Acc137Closing")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SecA",
                                             "offset": 0})
    sketcher.execute("catia_sketch_ellipse", {"cx": 0, "cy": 0, "major": 8,
                                              "minor": 6})
    sketcher.execute("catia_close_sketch", {})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SecB",
                                             "offset": 30})
    sketcher.execute("catia_sketch_ellipse", {"cx": 4, "cy": 0, "major": 8,
                                              "minor": 6})
    sketcher.execute("catia_close_sketch", {})
    auto = parts.execute("catia_solid_loft", {"sections": ["SecA", "SecB"],
                                              "name": "Auto"})
    auto_volume = json.loads(measurement.execute("catia_get_inertia", {})).get("volume_mm3")

    # The closing-point loft is built in its OWN document: two lofts in one body
    # union, and a union of two nearly identical solids reads the same volume
    # whether the second one differs or not (the first version of this cell could
    # not tell them apart).
    fresh(conn, docs, "Acc137ClosingGuided")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SecA",
                                             "offset": 0})
    sketcher.execute("catia_sketch_ellipse", {"cx": 0, "cy": 0, "major": 8,
                                              "minor": 6})
    sketcher.execute("catia_close_sketch", {})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SecB",
                                             "offset": 30})
    sketcher.execute("catia_sketch_ellipse", {"cx": 4, "cy": 0, "major": 8,
                                              "minor": 6})
    sketcher.execute("catia_close_sketch", {})
    gsd.execute("catia_gsd_point", {"x": 0, "y": 6, "z": 0, "name": "CloseA"})
    gsd.execute("catia_gsd_point", {"x": 4, "y": 6, "z": 30, "name": "CloseB"})
    guided = parts.execute("catia_solid_loft", {
        "sections": ["SecA", "SecB"], "name": "Guided",
        "closing_points": ["CloseA", "CloseB"]})
    cell("J1 the reply names the closing points",
         "Closing points" in str(guided) and "CloseA" in str(guided),
         " | ".join(line for line in str(guided).split(". ")
                    if "Closing points" in line)[:140])
    guided_volume = json.loads(measurement.execute("catia_get_inertia", {})).get("volume_mm3")
    cell("J2 the closing point changed the solid",
         auto_volume is not None and guided_volume is not None
         and auto_volume != guided_volume,
         f"auto {auto_volume} vs guided {guided_volume}")

    say("\n--- summary ---")
    ok = sum(1 for _, good, _ in ROWS if good)
    say(f"{ok}/{len(ROWS)} cells passed")
    for what, good, detail in ROWS:
        if not good:
            say(f"  FAILED {what}: {detail}")
    close_ours(conn)
    return 0 if ok == len(ROWS) else 1


if __name__ == "__main__":
    sys.exit(main())
