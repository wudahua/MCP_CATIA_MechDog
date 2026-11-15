"""Round 2: what the round-1 probe left open, one fresh document per cell.

Round 1 (b30_v0439_probe_loft_coupling.py) found that report B's C2 premise is
wrong: `HybridShapeLoft` on B30 HAS `SetSectionCoupling`, `SectionCoupling` and
`SetSpine`. It also learned that `HybridBodies.Remove` does not exist and that a
point-on-curve cannot be put on an ellipse inside a sketch (so a closing point
cannot be made that way). This round answers:

    C1  can an empty geometrical set be deleted at all - by Remove (absent),
        by Selection.Delete, or by neither? One fresh part each.
    B1  a POINT element drawn inside a section sketch: is `AddSectionToLoft(
        section, 1, <that point>)` accepted, and does it change the surface?
    E1  SetSectionCoupling(1|2|3) on two eccentric ellipses: which values build,
        and what do they cost in volume?
    E2  SetSpine(<spline>): accepted?
    D1  the report's own collapsing sweep - does `catia_shape_audit` see it?

    .venv/Scripts/python.exe -u scripts/b30_v0440_probe_coupling2.py
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
                    or tempfile.gettempdir(), "catia_probe_v0440c")
ROWS: list[tuple[str, bool, str]] = []


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


def two_eccentric_ellipses(sketcher, names=("SecA", "SecB")):
    sketcher.execute("catia_create_sketch",
                     {"plane": "xy", "name": names[0], "offset": 0})
    sketcher.execute("catia_sketch_ellipse",
                     {"cx": 0, "cy": 0, "major": 8, "minor": 6})
    sketcher.execute("catia_close_sketch", {})
    sketcher.execute("catia_create_sketch",
                     {"plane": "xy", "name": names[1], "offset": 30})
    sketcher.execute("catia_sketch_ellipse",
                     {"cx": 4, "cy": 0, "major": 8, "minor": 6})
    sketcher.execute("catia_close_sketch", {})
    return list(names)


def build_loft(conn, part, sections, coupling=None, spine=None):
    """A loft built by hand, so the coupling argument can be the variable."""
    factory = conn.active_document.Part.HybridShapeFactory
    loft = factory.AddNewLoft()
    for name in sections:
        ref = part.CreateReferenceFromObject(part.FindObjectByName(name))
        loft.AddSectionToLoft(ref, 1, None)
    note = []
    if spine:
        try:
            loft.SetSpine(part.CreateReferenceFromObject(part.FindObjectByName(spine)))
            note.append("spine set")
        except Exception as error:  # noqa: BLE001
            note.append(f"SetSpine failed: {str(error).splitlines()[0][:60]}")
    if coupling is not None:
        try:
            loft.SetSectionCoupling(int(coupling))
            note.append(f"coupling={coupling}")
        except Exception as error:  # noqa: BLE001
            note.append(f"SetSectionCoupling({coupling}) failed: "
                        f"{str(error).splitlines()[0][:60]}")
    try:
        read = loft.SectionCoupling
        note.append(f"reads back {read}")
    except Exception as error:  # noqa: BLE001
        note.append(f"SectionCoupling unreadable: {str(error).splitlines()[0][:40]}")
    sets = part.HybridBodies
    if int(sets.Count) == 0:
        block = sets.Add()
        block.Name = "ProbeSet"
    holder = sets.Item(1)
    holder.AppendHybridShape(loft)
    part.UpdateObject(loft)
    return loft, "; ".join(note)


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    parts = PartDesignTools(conn)
    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)
    say(f"MCP probe v0.3.1440 on {conn.app.Name}")

    # ── C1: deleting an empty geometrical set, three independent documents ──
    say("\nC1. can an empty geometrical set be deleted?")
    part = fresh(conn, docs, "ProbeSetRemove")
    sets = part.HybridBodies
    block = sets.Add()
    block.Name = "Doomed"
    try:
        sets.Remove(block)
        cell("C1a HybridBodies.Remove", True, "removed")
    except Exception as error:  # noqa: BLE001
        cell("C1a HybridBodies.Remove", False, str(error).splitlines()[0][:80])
    cell("C1a count after Remove", int(sets.Count) == 0, f"count {int(sets.Count)}")

    part = fresh(conn, docs, "ProbeSetDelete")
    sets = part.HybridBodies
    block = sets.Add()
    block.Name = "Doomed2"
    before = int(sets.Count)
    try:
        selection = conn.active_document.Selection
        selection.Clear()
        selection.Add(block)
        selection.Delete()
        selection.Clear()
    except Exception as error:  # noqa: BLE001
        say(f"        Selection.Delete failed: {str(error).splitlines()[0][:90]}")
    cell("C1b Selection.Delete on a set", int(sets.Count) == before - 1,
         f"{before} -> {int(sets.Count)}")

    part = fresh(conn, docs, "ProbeSetParent")
    sets = part.HybridBodies
    block = sets.Add()
    block.Name = "Doomed3"
    try:
        parent = block.Parent
        parent.Remove(block)
        cell("C1c block.Parent.Remove", int(sets.Count) == 0,
             f"parent={parent} count={int(sets.Count)}")
    except Exception as error:  # noqa: BLE001
        cell("C1c block.Parent.Remove", False, str(error).splitlines()[0][:80])

    # ── B1: a point drawn in the sketch as the closing point ───────────────
    say("\nB1. a sketch point as the section's closing point")
    part = fresh(conn, docs, "ProbeClosingPoint")
    sketcher.execute("catia_create_sketch",
                     {"plane": "xy", "name": "SecA", "offset": 0})
    sketcher.execute("catia_sketch_ellipse",
                     {"cx": 0, "cy": 0, "major": 8, "minor": 6})
    sketcher.execute("catia_sketch_point", {"x": 0, "y": 6})
    sketcher.execute("catia_close_sketch", {})
    sketch_a = part.FindObjectByName("SecA")
    point_name = None
    for index in range(1, int(sketch_a.GeometricElements.Count) + 1):
        item = sketch_a.GeometricElements.Item(index)
        if "点" in str(item.Name):
            point_name = str(item.Name)
            say(f"        point element in the section sketch: '{point_name}'")
    sketcher.execute("catia_create_sketch",
                     {"plane": "xy", "name": "SecB", "offset": 30})
    sketcher.execute("catia_sketch_ellipse",
                     {"cx": 4, "cy": 0, "major": 8, "minor": 6})
    sketcher.execute("catia_sketch_point", {"x": 4, "y": 6})
    sketcher.execute("catia_close_sketch", {})
    sketch_b = part.FindObjectByName("SecB")
    point_b = None
    for index in range(1, int(sketch_b.GeometricElements.Count) + 1):
        item = sketch_b.GeometricElements.Item(index)
        if "点" in str(item.Name):
            point_b = item
    if point_name is None or point_b is None:
        cell("B1 a sketch point exists to use", False, "no point element drawn")
    else:
        factory = conn.active_document.Part.HybridShapeFactory
        loft = factory.AddNewLoft()
        for name, element in (("SecA", sketch_a), ("SecB", sketch_b)):
            point = None
            for index in range(1, int(element.GeometricElements.Count) + 1):
                item = element.GeometricElements.Item(index)
                if "点" in str(item.Name):
                    point = item
            try:
                loft.AddSectionToLoft(
                    part.CreateReferenceFromObject(element), 1,
                    part.CreateReferenceFromObject(point))
                cell(f"B1 closing point accepted for '{name}'", True,
                     str(getattr(point, "Name", "?")))
            except Exception as error:  # noqa: BLE001
                cell(f"B1 closing point accepted for '{name}'", False,
                     str(error).splitlines()[0][:100])
        sets = part.HybridBodies
        if int(sets.Count) == 0:
            block = sets.Add()
            block.Name = "ProbeSet"
        sets.Item(1).AppendHybridShape(loft)
        try:
            part.UpdateObject(loft)
            volume = json.loads(measurement.execute("catia_get_inertia", {}))
            cell("B1 the loft with closing points builds", True,
                 f"{volume.get('volume_mm3')} mm3")
        except Exception as error:  # noqa: BLE001
            cell("B1 the loft with closing points builds", False,
                 str(error).splitlines()[0][:120])

    # ── E1/E2: coupling values and spine ────────────────────────────────────
    say("\nE. SetSectionCoupling values and SetSpine")
    for coupling in (1, 2, 3):
        part = fresh(conn, docs, f"ProbeCoupling{coupling}")
        sections = two_eccentric_ellipses(sketcher)
        try:
            loft, note = build_loft(conn, part, sections, coupling=coupling)
            volume = json.loads(measurement.execute("catia_get_inertia", {}))
            cell(f"E1 coupling={coupling} builds", True,
                 f"{volume.get('volume_mm3')} mm3; {note}")
        except Exception as error:  # noqa: BLE001
            cell(f"E1 coupling={coupling} builds", False,
                 str(error).splitlines()[0][:140])

    part = fresh(conn, docs, "ProbeLoftSpine")
    sections = two_eccentric_ellipses(sketcher)
    gsd.execute("catia_gsd_spline", {
        "points": [[0, 0, 0], [1, 0, 10], [3, 0, 20], [4, 0, 30]],
        "name": "LoftSpine", "closed": False, "deviation": False})
    try:
        loft, note = build_loft(conn, part, sections, spine="LoftSpine")
        volume = json.loads(measurement.execute("catia_get_inertia", {}))
        cell("E2 SetSpine on a two-section loft", True,
             f"{volume.get('volume_mm3')} mm3; {note}")
    except Exception as error:  # noqa: BLE001
        cell("E2 SetSpine on a two-section loft", False,
             str(error).splitlines()[0][:140])

    # ── D1: the report's collapsing sweep, seen by shape_audit ─────────────
    say("\nD1. the report's collapsing sweep")
    part = fresh(conn, docs, "ProbeSweepCollapse")
    gsd.execute("catia_gsd_spline", {
        "points": [[-1.2, 0, 0], [-0.9, 0, 8], [-0.5, 0, 16], [0, 0, 23.8],
                   [1.2, 0, 26.5], [3.2, 0, 29], [5, 0, 32], [5.5, 0, 35],
                   [5.5, 0, 38], [5.5, 0, 40.5]],
        "name": "BodySpine", "closed": False, "deviation": False})
    swept = gsd.execute("catia_solid_sweep_variable", {
        "spine": "BodySpine",
        "sections": [[0, 2.4, 2.1], [0.02, 2.5, 2.2], [0.55, 2.5, 2.2],
                     [0.6, 2.7, 2.4], [0.66, 3.8, 3.3], [0.72, 5.6, 4.9],
                     [0.78, 7, 6], [0.84, 7.6, 6.3], [0.89, 7.2, 6],
                     [0.93, 6, 5.1], [0.96, 4.2, 3.7], [0.985, 2.2, 2],
                     [1, 0.6, 0.6]],
        "major_axis": [1, 0, 0], "name": "CollapsedSweep", "helpers": "hide"})
    say("        sweep: " + " ".join(str(swept).split())[:240])
    volume = json.loads(measurement.execute("catia_get_inertia", {}))
    say(f"        volume {volume.get('volume_mm3')} mm3")
    for tool_name, args in (("catia_shape_audit", {}),
                            ("catia_get_bounding_box", {"method": "mesh"})):
        try:
            reply = measurement.execute(tool_name, args)
            say(f"        {tool_name}: " + " ".join(str(reply).split())[:400])
            cell(f"D1 {tool_name} runs", True, "see above")
        except Exception as error:  # noqa: BLE001
            cell(f"D1 {tool_name} runs", False, str(error).splitlines()[0][:120])

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
