"""0.3.99 acceptance: the four things the 2026-11-10 bottle-base report asked for.

  1  `catia_check_surface` measures the SOLID: with a visible geometrical set the
     numbers are the solid's, not the solid plus the construction surfaces
  2  the sets are back on screen afterwards - the flags were restored, not left
     hidden
  3  `include_construction=true` brings the old (polluted) numbers back, so the
     two scopes are both reachable and both labelled
  4  `brief=true` answers with the numbers only
  5  `catia_shape_audit` finds five lobes on a five-lobe solid, and the five-fold
     check agrees - the "flow succeeded, shape is wrong" gap
  6  the tutorial's loft parameters are refused WITH the measurement, and nothing
     is created
  7  `catia_diagnose` publishes the four new ceilings
  8  `catia_screenshot(clean=true)` still has the model in it, and restores the
     visibility it changed

Everything happens in this script's own folder.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.results import ArgumentRejected, CodedError  # noqa: E402
from catia_mcp.tools.diagnostics import DiagnosticsTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.export import ExportTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0399\audit"
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
                except OSError:
                    pass


def set_visible(conn: CATIAConnection, name: str) -> int | None:
    """The set's own VisProperties flag: 0 shown, 1 hidden."""
    part = conn.get_active_part()
    selection = conn.hso
    node = part.FindObjectByName(name)
    if node is None:
        return None
    selection.Clear()
    selection.Add(node)
    state = selection.VisProperties.GetShow()
    selection.Clear()
    if isinstance(state, (tuple, list)):
        state = state[-1] if state else 0
    return int(state)


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    docs, sk, pd, gsd = (DocumentTools(conn), SketcherTools(conn),
                         PartDesignTools(conn), GSDTools(conn))
    mt, diag, ex = MeasurementTools(conn), DiagnosticsTools(conn), ExportTools(conn)

    say("0.3.99 acceptance - the solid, or everything on screen?")
    say("=" * 78)

    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, "scope_0399.CATPart")})
    sk.execute("catia_create_sketch", {"plane": "xy"})
    sk.execute("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": 30})
    sk.execute("catia_close_sketch", {})
    pd.execute("catia_pad", {"height": 20})

    # Construction geometry that lies ON the solid, exactly as a finished part
    # has: two extracted faces in a geometrical set.
    faces = json.loads(pd.execute("catia_list_faces", {}))["faces"]
    planar = [f for f in faces if f.get("type") == "PlanarFace"]
    curved = [f for f in faces if f.get("type") != "PlanarFace"]
    if planar:
        gsd.execute("catia_gsd_extract", {"element": planar[0]["name"],
                                          "name": "Cap"})
    if curved:
        gsd.execute("catia_gsd_extract", {"element": curved[0]["name"],
                                          "name": "Wall"})
    set_name = "GSD_Set"
    before = set_visible(conn, set_name)
    check(0, "the construction set is on screen before the measurement",
          before == 0, f"VisProperties.GetShow() = {before} (0 = shown)")

    solid_only = json.loads(mt._check_surface(25.0, 600000, None, None, 8, None,
                                              False, False))
    everything = json.loads(mt._check_surface(25.0, 600000, None, None, 8, None,
                                              True, False))
    say(f"     solid only: {solid_only['triangles']} triangles, "
        f"{solid_only['boundary_edges']} boundary, "
        f"{solid_only['non_manifold_edges']} non-manifold")
    say(f"     on screen : {everything['triangles']} triangles, "
        f"{everything['boundary_edges']} boundary, "
        f"{everything['non_manifold_edges']} non-manifold")
    check(1, "the default measures the SOLID, not what is on screen",
          solid_only["triangles"] < everything["triangles"],
          f"{solid_only['triangles']} vs {everything['triangles']} triangles")
    check(2, "the reply says so, and says what it restored",
          "the solid only" in solid_only["scope"]
          and "put back" in solid_only["scope"],
          solid_only["scope"][:150])
    after = set_visible(conn, set_name)
    check(3, "the set is back on screen afterwards",
          after == 0, f"VisProperties.GetShow() = {after} (0 = shown)")

    brief = json.loads(mt._check_surface(25.0, 600000, None, None, 8, None,
                                         False, True))
    check(4, "brief drops the prose and keeps the numbers",
          "how_to_read" not in brief and "triangles" in brief,
          f"{len(json.dumps(brief))} chars against {len(json.dumps(solid_only))}")

    shot = ex.execute("catia_screenshot",
                      {"file_path": os.path.join(ROOT, "clean_0399.jpg"),
                       "clean": True})
    size = os.path.getsize(os.path.join(ROOT, "clean_0399.jpg")) \
        if os.path.exists(os.path.join(ROOT, "clean_0399.jpg")) else 0
    restored = set_visible(conn, set_name)
    check(5, "a clean screenshot reports what it hid and still has the model",
          "clean:" in shot and size > 3000,
          f"{size} B | {shot[-150:]}")
    check(6, "...and puts the visibility back",
          restored == 0, f"VisProperties.GetShow() = {restored}")

    # ── the shape: five lobes, which volume cannot see ──────────────────
    say()
    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, "lobes_0399.CATPart")})
    sk.execute("catia_create_sketch", {"plane": "xy"})
    sk.execute("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": 20})
    sk.execute("catia_close_sketch", {})
    pd.execute("catia_pad", {"height": 10})
    sk.execute("catia_create_sketch", {"plane": "xy"})
    sk.execute("catia_sketch_circle", {"cx": 25, "cy": 0, "radius": 8})
    sk.execute("catia_close_sketch", {})
    pd.execute("catia_pad", {"height": 10})
    part = conn.get_active_part()
    body = part.MainBody
    boss = str(body.Shapes.Item(int(body.Shapes.Count)).Name)
    patterned = pd.execute("catia_circ_pattern",
                           {"feature_name": boss, "count": 5, "axis": "z"})
    say(f"     pattern: {patterned.splitlines()[0][:120]}")

    audit = json.loads(mt._shape_audit(bins=72, fold=5))
    circle = audit["circumference"]
    say(f"     amplitude {circle['amplitude_mm']} mm, "
        f"{circle['peak_count']} peak(s) at {circle['peak_angles_deg']}, "
        f"fold max deviation {audit.get('fold_check', {}).get('max_deviation_mm')}")
    check(7, "the profile finds five lobes",
          circle["peak_count"] == 5,
          f"peaks at {circle['peak_angles_deg']}")
    check(8, "...and the amplitude is the one the boss ring makes",
          abs(circle["amplitude_mm"] - 13.0) < 1.5,
          f"{circle['amplitude_mm']} mm (outer 33, inner 20 -> 13)")
    check(9, "the five-fold check agrees with itself",
          audit.get("fold_check", {}).get("verdict") == "the repeats agree",
          str(audit.get("fold_check", {})))

    # ── the tutorial's three loft parameters ────────────────────────────
    say()
    for label, arguments, code in (
            ("tangent_factor", {"tangent_factor": 1.5}, "NO_TANGENT_FACTOR"),
            ("normal_to_profile", {"normal_to_profile": True},
             "NO_NORMAL_TO_PROFILE"),
            ("guide_influence", {"guide_influence": "To Next Guide"},
             "NO_GUIDE_INFLUENCE")):
        try:
            gsd.execute("catia_gsd_multi_section_surface",
                        dict(arguments, sections=["Wall", "Cap"]))
            check(10, f"{label} is answered, not schema-rejected", False,
                  "the call went through instead")
        except (ArgumentRejected, CodedError) as error:
            check(10 if label == "tangent_factor" else
                  11 if label == "normal_to_profile" else 12,
                  f"{label} is answered, not schema-rejected",
                  getattr(error, "code", None) == code,
                  f"{getattr(error, 'code', None)}: {str(error).splitlines()[0][:80]}")

    report = json.loads(diag.execute("catia_diagnose", {}))
    members = [entry["member"] for entry in report["capability_ceilings"]]
    wanted = ("ShapeFactory.AddNewGSDCircPattern", "HybridShapeLoft",
              "Sketch frame", "Viewer3D")
    check(13, "diagnose publishes the four new ceilings",
          all(any(member.startswith(name) for member in members)
              for name in wanted),
          f"{len(members)} entries")

    say()
    passed = sum(1 for _n, _l, ok, _d in RESULTS if ok)
    say("=" * 78)
    say(f"{passed}/{len(RESULTS)} checks passed")
    for number, label, ok, detail in RESULTS:
        if not ok:
            say(f"  FAILED {number}. {label}: {detail}")
    close_ours(conn)


if __name__ == "__main__":
    main()
