"""Three questions the 0.3.136 reports raise, before anything is changed.

Report B says `catia_solid_loft` has "no closing point / coupling / spine" and
that a 24-section head still comes out faceted on one side. The code has been
passing a THIRD argument to `AddSectionToLoft` since 0.3.77 - always `None`, with
a comment saying "the closing point may be None". If that argument is real, the
capability exists and nothing exposes it, which would be the same defect class as
0.3.135's.

    A  which coupling/closing members does HybridShapeLoft have on B30?
    B  does a closing point change the surface? Two eccentric ellipses, built
       twice: automatic, then with an explicit closing point on each section.
    C  does part.HybridBodies.Remove(<empty set>) work? 0.3.136 leaves
       'MCP_Probes' behind when it had to create the container (report A, N5).
    D  can `shape_audit` metric be computed on a swept solid at all - i.e. is
       C1's "detect the collapse" reachable through the tools that exist?

    .venv/Scripts/python.exe -u scripts/b30_v0439_probe_loft_coupling.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0439c")
ROWS: list[tuple[str, bool, str]] = []

CANDIDATES = (
    "AddSectionToLoft", "GetSectionFromLoft", "SetSectionClosingPoint",
    "GetSectionClosingPoint", "SectionCoupling", "SetSectionCoupling",
    "Smooth", "SetSmooth", "SetTransition", "GetTransition", "Spine",
    "SetSpine", "RemoveSection", "SetSectionOrientation", "GetSectionOrientation",
    "CanBeRemoved", "SetStartSectionTangent", "SetEndSectionTangent",
)


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


def fresh(conn, docs, name: str):
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, name + ".CATPart")
    close_ours(conn)
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    return conn.get_active_part()


def two_eccentric_ellipses(sketcher, parts, names=("SecA", "SecB")):
    """Two ellipses 5 mm apart in x, 30 mm apart in z: a loft whose coupling is
    decided entirely by where each section's parameter 0 sits."""
    sketcher.execute("catia_create_sketch",
                     {"plane": "xy", "name": names[0], "offset": 0})
    sketcher.execute("catia_sketch_ellipse",
                     {"cx": 0, "cy": 0, "major": 8, "minor": 6})
    sketcher.execute("catia_close_sketch", {})
    sketcher.execute("catia_create_sketch",
                     {"plane": "xy", "name": names[1], "offset": 30})
    sketcher.execute("catia_sketch_ellipse",
                     {"cx": 5, "cy": 0, "major": 8, "minor": 6})
    sketcher.execute("catia_close_sketch", {})
    return list(names)


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    parts = PartDesignTools(conn)
    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)
    say(f"MCP probe v0.3.139 on {conn.app.Name}")

    # ── A: which members exist ───────────────────────────────────────────────
    say("\nA. HybridShapeLoft members on B30")
    part = fresh(conn, docs, "ProbeLoftMembers")
    sections = two_eccentric_ellipses(sketcher, parts)
    factory = conn.active_document.Part.HybridShapeFactory
    body = conn.get_active_part_body()
    loft = factory.AddNewLoft()
    for name in sections:
        ref = part.CreateReferenceFromObject(part.FindObjectByName(name))
        loft.AddSectionToLoft(ref, 1, None)
    found = {}
    for member in CANDIDATES:
        try:
            value = getattr(loft, member)
        except Exception as error:  # noqa: BLE001
            found[member] = f"absent ({str(error).splitlines()[0][:40]})"
            continue
        kind = "method" if callable(value) else "property"
        found[member] = kind
        say(f"        {member:26s} {kind}")
    for member, kind in found.items():
        cell(f"A {member}", not str(kind).startswith("absent"), str(kind))

    # ── B: does a closing point change the surface? ─────────────────────────
    say("\nB. automatic coupling vs explicit closing points")
    part = fresh(conn, docs, "ProbeLoftClosing")
    sections = two_eccentric_ellipses(sketcher, parts)
    auto = parts.execute("catia_solid_loft",
                         {"sections": sections, "name": "AutoLoft"})
    say("        auto:  " + " ".join(str(auto).split())[:200])
    auto_volume = json.loads(measurement.execute("catia_get_inertia", {}))
    say(f"        auto volume {auto_volume.get('volume_mm3')} mm3")

    # The closing point of a section is a POINT on it. Build one on each sketch
    # at parameter 0 and hand it over: if the argument is honoured the surface
    # changes (a 5 mm offset ellipse has 8 mm of major axis, so a quadrant
    # rotation of the coupling is visible in the volume and the centroid).
    part = fresh(conn, docs, "ProbeLoftClosing2")
    sections = two_eccentric_ellipses(sketcher, parts)
    factory = conn.active_document.Part.HybridShapeFactory
    sets = part.HybridBodies
    gsd.execute("catia_gsd_create_geoset", {"name": "ProbePoints"})
    holder = sets.Item(int(sets.Count))
    closing: list[object] = []
    for name in sections:
        sketch = part.FindObjectByName(name)
        # The spline/ellipse in the sketch is what carries a point-on-curve; a
        # datum point is what AddSectionToLoft expects as a closing point.
        element = None
        for index in range(1, int(sketch.GeometricElements.Count) + 1):
            item = sketch.GeometricElements.Item(index)
            if "椭圆" in str(item.Name) or "Ellipse" in str(item.Name):
                element = item
        if element is None:
            cell(f"B find the ellipse in '{name}'", False, "no ellipse element")
            closing.append(None)
            continue
        try:
            point = factory.AddNewPointOnCurveFromPercent(
                part.CreateReferenceFromObject(element), 0.0, 0)
            holder.AppendHybridShape(point)
            part.UpdateObject(point)
            coords = conn.active_document.GetWorkbench("SPAWorkbench").GetMeasurable(
                part.CreateReferenceFromObject(point)).GetPoint()
            cell(f"B point on '{name}' at t=0", True,
                 f"({float(coords[0]):.3f}, {float(coords[1]):.3f}, "
                 f"{float(coords[2]):.3f})")
            closing.append(point)
        except Exception as error:  # noqa: BLE001
            cell(f"B point on '{name}' at t=0", False,
                 str(error).splitlines()[0][:100])
            closing.append(None)

    if all(item is not None for item in closing):
        loft = factory.AddNewLoft()
        built = []
        for name, point in zip(sections, closing):
            ref = part.CreateReferenceFromObject(part.FindObjectByName(name))
            try:
                loft.AddSectionToLoft(ref, 1, part.CreateReferenceFromObject(point))
                built.append(name)
            except Exception as error:  # noqa: BLE001
                say(f"        AddSectionToLoft('{name}', closing) failed: "
                    f"{str(error).splitlines()[0][:110]}")
        holder.AppendHybridShape(loft)
        try:
            part.UpdateObject(loft)
            cell("B loft with explicit closing points builds", True,
                 f"{len(built)} section(s) took a closing point")
        except Exception as error:  # noqa: BLE001
            cell("B loft with explicit closing points builds", False,
                 str(error).splitlines()[0][:120])
        try:
            volume = json.loads(measurement.execute("catia_get_inertia", {}))
            say(f"        explicit volume {volume.get('volume_mm3')} mm3 "
                f"(auto was {auto_volume.get('volume_mm3')})")
            cell("B the closing point changes the result",
                 volume.get("volume_mm3") != auto_volume.get("volume_mm3"),
                 f"{volume.get('volume_mm3')} vs {auto_volume.get('volume_mm3')}")
        except Exception as error:  # noqa: BLE001
            cell("B read the explicit loft's volume", False, str(error)[:120])

    # ── C: removing an empty geometrical set ────────────────────────────────
    say("\nC. part.HybridBodies.Remove(<empty set>)")
    part = fresh(conn, docs, "ProbeRemoveSet")
    sets = part.HybridBodies
    before = int(sets.Count)
    block = sets.Add()
    block.Name = "ToBeRemoved"
    made = int(sets.Count)
    removed = False
    try:
        sets.Remove(block)
        removed = True
    except Exception as error:  # noqa: BLE001
        say(f"        Remove(set) failed: {str(error).splitlines()[0][:110]}")
    after = int(sets.Count)
    cell("C an empty geometrical set can be removed", removed and after == before,
         f"{before} -> {made} -> {after}")
    # And the Selection route, which is what the tool uses for elements.
    block2 = sets.Add()
    block2.Name = "ToBeRemoved2"
    try:
        selection = conn.active_document.Selection
        selection.Clear()
        selection.Add(block2)
        selection.Delete()
        selection.Clear()
    except Exception as error:  # noqa: BLE001
        say(f"        Selection.Delete on a set failed: "
            f"{str(error).splitlines()[0][:110]}")
    cell("C a set can be removed through the selection", int(sets.Count) == before,
         f"count now {int(sets.Count)} (was {before})")

    # ── D: is a shape audit reachable on a swept solid? ─────────────────────
    say("\nD. can the collapse be detected with the tools that exist?")
    part = fresh(conn, docs, "ProbeSweepCollapse")
    spine = gsd.execute("catia_gsd_spline", {
        "points": [[-1.2, 0, 0], [-0.9, 0, 8], [-0.5, 0, 16], [0, 0, 23.8],
                   [1.2, 0, 26.5], [3.2, 0, 29], [5, 0, 32], [5.5, 0, 35],
                   [5.5, 0, 38], [5.5, 0, 40.5]],
        "name": "BodySpine", "closed": False, "deviation": False})
    say("        spine: " + " ".join(str(spine).split())[:150])
    swept = parts.execute("catia_solid_sweep_variable", {
        "spine": "BodySpine",
        "sections": [[0, 2.4, 2.1], [0.02, 2.5, 2.2], [0.55, 2.5, 2.2],
                     [0.6, 2.7, 2.4], [0.66, 3.8, 3.3], [0.72, 5.6, 4.9],
                     [0.78, 7, 6], [0.84, 7.6, 6.3], [0.89, 7.2, 6],
                     [0.93, 6, 5.1], [0.96, 4.2, 3.7], [0.985, 2.2, 2],
                     [1, 0.6, 0.6]],
        "major_axis": [1, 0, 0], "name": "CollapsedSweep", "helpers": "hide"})
    say("        sweep: " + " ".join(str(swept).split())[:220])
    for tool_name in ("catia_shape_audit", "catia_check_surface"):
        try:
            reply = measurement.execute(tool_name, {})
            cell(f"D {tool_name} runs on the swept body", True,
                 " ".join(str(reply).split())[:150])
        except Exception as error:  # noqa: BLE001
            cell(f"D {tool_name} runs on the swept body", False,
                 str(error).splitlines()[0][:120])

    say("\n--- summary ---")
    ok = sum(1 for _, good, _ in ROWS if good)
    say(f"{ok}/{len(ROWS)} cells answered")
    for what, good, detail in ROWS:
        if not good:
            say(f"  no: {what}: {detail}")
    close_ours(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
