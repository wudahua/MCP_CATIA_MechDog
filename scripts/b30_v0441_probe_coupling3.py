"""Round 3: the two cells round 2 got wrong, and one full reading.

Round 2 built its lofts with `HybridShapeFactory.AddNewLoft`, which makes a
SURFACE - so "no solid in 零件几何体" was my mistake, not CATIA's refusal, and the
coupling values were never actually judged. It also used a point drawn inside the
section sketch as the closing point; `AddSectionToLoft` accepted it and
`UpdateObject` then failed.

    A  a GSD point ON the section as the closing point (the documented shape of
       the argument), on a SOLID loft
    B  the same two eccentric ellipses, solid loft, SetSectionCoupling 1/2/3,
       with the volume of each - does the coupling change the surface?
    C  the report's collapsing sweep: the FULL catia_shape_audit reply, to see
       whether it names the collapse rather than just the outline

    .venv/Scripts/python.exe -u scripts/b30_v0441_probe_coupling3.py
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
                    or tempfile.gettempdir(), "catia_probe_v0441c")
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


def solid_loft(conn, part, sections, coupling=None, closing=()):
    """A SOLID loft (ShapeFactory), so a volume can be asked for afterwards."""
    factory = part.ShapeFactory
    feature = factory.AddNewLoft()
    inner = feature.HybridShape
    note = []
    for index, name in enumerate(sections):
        ref = part.CreateReferenceFromObject(part.FindObjectByName(name))
        point = closing[index] if index < len(closing) else None
        try:
            if point is not None:
                inner.AddSectionToLoft(ref, 1, part.CreateReferenceFromObject(point))
            else:
                inner.AddSectionToLoft(ref, 1, None)
        except Exception:
            inner.AddSectionToLoft(ref, 1)
    if coupling is not None:
        try:
            inner.SetSectionCoupling(int(coupling))
            note.append(f"coupling={coupling}")
        except Exception as error:  # noqa: BLE001
            note.append(f"SetSectionCoupling({coupling}): "
                        f"{str(error).splitlines()[0][:50]}")
    try:
        note.append(f"reads {inner.SectionCoupling}")
    except Exception as error:  # noqa: BLE001
        note.append(f"SectionCoupling unreadable: {str(error).splitlines()[0][:30]}")
    part.InWorkObject = part.MainBody
    part.UpdateObject(inner)
    return feature, "; ".join(note)


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    parts = PartDesignTools(conn)
    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)
    say(f"MCP probe v0.3.1441 on {conn.app.Name}")

    # ── A: a GSD point ON the section, as the closing point ────────────────
    say("\nA. closing point as a GSD point on the section")
    part = fresh(conn, docs, "ProbeClosingGsd")
    sections = two_eccentric_ellipses(sketcher)
    gsd.execute("catia_gsd_point", {"x": 0, "y": 6, "z": 0, "name": "CloseA"})
    gsd.execute("catia_gsd_point", {"x": 4, "y": 6, "z": 30, "name": "CloseB"})
    points = [part.FindObjectByName("CloseA"), part.FindObjectByName("CloseB")]
    say(f"        points: {[str(getattr(p, 'Name', None)) for p in points]}")
    try:
        feature, note = solid_loft(conn, part, sections, closing=points)
        volume = json.loads(measurement.execute("catia_get_inertia", {}))
        cell("A solid loft with GSD closing points", True,
             f"{volume.get('volume_mm3')} mm3; {note}")
    except Exception as error:  # noqa: BLE001
        cell("A solid loft with GSD closing points", False,
             str(error).splitlines()[0][:140])

    # ── B: coupling values on a SOLID loft ─────────────────────────────────
    say("\nB. SetSectionCoupling on a solid loft (two eccentric ellipses)")
    volumes = {}
    for coupling in (None, 1, 2, 3):
        part = fresh(conn, docs, f"ProbeSolidCoupling{coupling}")
        sections = two_eccentric_ellipses(sketcher)
        try:
            feature, note = solid_loft(conn, part, sections, coupling=coupling)
            volume = json.loads(measurement.execute("catia_get_inertia", {}))
            volumes[coupling] = volume.get("volume_mm3")
            cell(f"B coupling={coupling}", True, f"{volume.get('volume_mm3')} mm3; {note}")
        except Exception as error:  # noqa: BLE001
            volumes[coupling] = None
            cell(f"B coupling={coupling}", False, str(error).splitlines()[0][:130])
    values = [value for value in volumes.values() if value]
    cell("B the coupling value changes the surface",
         len(values) >= 2 and (max(values) - min(values)) > 0.01,
         f"{volumes}")

    # ── C: the full shape audit on the collapsing sweep ────────────────────
    say("\nC. the report's collapsing sweep, full audit")
    part = fresh(conn, docs, "ProbeSweepAudit")
    gsd.execute("catia_gsd_spline", {
        "points": [[-1.2, 0, 0], [-0.9, 0, 8], [-0.5, 0, 16], [0, 0, 23.8],
                   [1.2, 0, 26.5], [3.2, 0, 29], [5, 0, 32], [5.5, 0, 35],
                   [5.5, 0, 38], [5.5, 0, 40.5]],
        "name": "BodySpine", "closed": False, "deviation": False})
    gsd.execute("catia_solid_sweep_variable", {
        "spine": "BodySpine",
        "sections": [[0, 2.4, 2.1], [0.02, 2.5, 2.2], [0.55, 2.5, 2.2],
                     [0.6, 2.7, 2.4], [0.66, 3.8, 3.3], [0.72, 5.6, 4.9],
                     [0.78, 7, 6], [0.84, 7.6, 6.3], [0.89, 7.2, 6],
                     [0.93, 6, 5.1], [0.96, 4.2, 3.7], [0.985, 2.2, 2],
                     [1, 0.6, 0.6]],
        "major_axis": [1, 0, 0], "name": "CollapsedSweep", "helpers": "hide"})
    say("        audit:")
    try:
        reply = measurement.execute("catia_shape_audit", {})
        text = json.dumps(json.loads(reply), ensure_ascii=False, indent=1)
        for line in text.splitlines():
            say("          " + line)
        data = json.loads(reply)
        cell("C shape_audit reports the mesh", True,
             f"triangles={data.get('triangles')}")
        cell("C shape_audit has a fold/self-intersection field",
             any(key in data for key in ("fold", "fold_back", "boundary_edges",
                                         "non_manifold_edges", "dihedral")),
             f"keys={sorted(data)}")
    except Exception as error:  # noqa: BLE001
        cell("C shape_audit", False, str(error).splitlines()[0][:140])

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
