"""Round 4: one question, because the answer decides whether `coupling` ships.

Round 3 showed `HybridShapeLoft.SectionCoupling` READS back (1) while
`SetSectionCoupling(...)` answers `<unknown>.SetSectionCoupling` under pywin32's
late binding - the same "the method exists but the interface does not describe
it" wall as `GetCurve` on a topological edge. This round asks whether the
dynamic dispatcher the connection already carries gets through it, and measures
what a coupling value does to the surface when it does.

    A  conn._dynamic(loft).SetSectionCoupling(n) for n = 1, 2, 3
    B  the volume of each, against the automatic one (4523.6938 mm3, measured)

    .venv/Scripts/python.exe -u scripts/b30_v0442_probe_coupling4.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0442c")
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


def two_eccentric_ellipses(sketcher):
    sketcher.execute("catia_create_sketch",
                     {"plane": "xy", "name": "SecA", "offset": 0})
    sketcher.execute("catia_sketch_ellipse",
                     {"cx": 0, "cy": 0, "major": 8, "minor": 6})
    sketcher.execute("catia_close_sketch", {})
    sketcher.execute("catia_create_sketch",
                     {"plane": "xy", "name": "SecB", "offset": 30})
    sketcher.execute("catia_sketch_ellipse",
                     {"cx": 4, "cy": 0, "major": 8, "minor": 6})
    sketcher.execute("catia_close_sketch", {})
    return ["SecA", "SecB"]


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    measurement = MeasurementTools(conn)
    say(f"MCP probe v0.3.1442 on {conn.app.Name}")

    for coupling in (1, 2, 3, 4):
        part = fresh(conn, docs, f"ProbeDynCoupling{coupling}")
        sections = two_eccentric_ellipses(sketcher)
        feature = part.ShapeFactory.AddNewLoft()
        inner = feature.HybridShape
        for name in sections:
            ref = part.CreateReferenceFromObject(part.FindObjectByName(name))
            inner.AddSectionToLoft(ref, 1, None)
        applied = ""
        for candidate, label in ((inner, "direct"),
                                 (conn._dynamic(inner), "dynamic")):
            try:
                getattr(candidate, "SetSectionCoupling")(coupling)
                applied = label
                break
            except Exception as error:  # noqa: BLE001
                applied += f"{label}: {str(error).splitlines()[0][:50]} | "
        part.InWorkObject = part.MainBody
        try:
            part.UpdateObject(inner)
            volume = json.loads(measurement.execute("catia_get_inertia", {}))
            try:
                read = inner.SectionCoupling
            except Exception:  # noqa: BLE001
                read = "?"
            cell(f"coupling={coupling}", True,
                 f"{volume.get('volume_mm3')} mm3, reads {read}, via {applied}")
        except Exception as error:  # noqa: BLE001
            cell(f"coupling={coupling}", False,
                 f"{str(error).splitlines()[0][:110]}; via {applied}")

    say("\n--- summary ---")
    ok = sum(1 for _, good, _ in ROWS if good)
    say(f"{ok}/{len(ROWS)} cells answered")
    close_ours(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
