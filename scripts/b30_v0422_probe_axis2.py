"""The three left-over questions, asked directly instead of through our wrappers.

A  RevoluteAxis = an external gsd_line reference - accepted at update?
B  RevoluteAxis = a construction line inside the sketch - the docstring says E_FAIL
C  what a HybridShapeSpline object exposes (is there a control-point route?)
D  what SheetMetalFactory.CreateSheetMetalParameters() returns, and whether that
   object can make geometry

A and B are one fresh document each. The wrappers are bypassed on purpose: our own
argument validation refuses "axis: <element name>" before CATIA sees it, so
asking the tool would measure our validation, not CATIA.

    .venv/Scripts/python.exe scripts/b30_v0422_probe_axis2.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0422")
ROOT = os.path.join(_PROBE_ROOT, "axis2")


def say(line: str = "") -> None:
    print(line, flush=True)


def activate(conn: CATIAConnection, name: str) -> None:
    for index in range(1, conn.documents.Count + 1):
        if conn.documents.Item(index).Name == name:
            conn.documents.Item(index).Activate()
            return
    raise RuntimeError(f"{name} is not open")


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


def fresh(conn, documents, name: str) -> None:
    path = os.path.join(ROOT, f"{name}.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    activate(conn, f"{name}.CATPart")


def volume(conn, part_design):
    part = conn.get_active_part()
    return part_design._body_volume_mm3(part, part.MainBody)


def revolve(part, sketch, axis_ref, angle: float = 360.0):
    """The same chain _build_revolution uses, with the axis handed in."""
    factory = part.ShapeFactory
    feature = factory.AddNewShaftFromRef(part.CreateReferenceFromName(""))
    feature.FirstAngle.Value = angle
    feature.SetProfileElement(part.CreateReferenceFromObject(sketch))
    feature.RevoluteAxis = axis_ref
    part.UpdateObject(feature)
    return feature


def members(obj) -> list[tuple[str, list[str]]]:
    try:
        info = obj._oleobj_.GetTypeInfo()
        count = info.GetTypeAttr().cFuncs
    except Exception as error:  # noqa: BLE001
        say("  typelib unreadable: " + str(error).splitlines()[0][:80])
        return []
    rows = []
    for index in range(count):
        try:
            desc = info.GetFuncDesc(index)
            names = info.GetNames(desc.memid)
        except Exception:  # noqa: BLE001
            continue
        rows.append((names[0], list(names[1:])))
    return rows


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    gsd = GSDTools(conn)

    # ── A: external line as the axis ─────────────────────────────────────
    say("")
    say("── A: profile x 30..40 on zx, axis = gsd_line (0,0,0)->(0,0,50) ──")
    fresh(conn, documents, "AxA")
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 0], "point2": [0, 0, 50], "name": "SpinAxis"})
    sketcher.execute("catia_create_sketch", {"plane": "zx"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 30, "y1": 0, "x2": 40, "y2": 20})
    sketcher.execute("catia_close_sketch", {})
    part = conn.get_active_part()
    sketch = part_design._get_last_sketch(None)
    try:
        feature = revolve(part, sketch, gsd._ref("SpinAxis"))
        got = volume(conn, part_design)
        expected = 2 * 3.14159265358979 * 35.0 * 200.0
        say(f"  ACCEPTED: '{feature.Name}', volume {got:.1f} mm3 "
            f"(Pappus {expected:.1f})")
    except Exception as error:  # noqa: BLE001
        say("  REFUSED " + " ".join(str(error).split())[:260])

    # ── B: construction line inside the sketch ───────────────────────────
    say("")
    say("── B: the same profile, axis = a construction line in the sketch ──")
    fresh(conn, documents, "AxB")
    sketcher.execute("catia_create_sketch", {"plane": "zx"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 30, "y1": 0, "x2": 40, "y2": 20})
    sketcher.execute("catia_sketch_line",
                     {"x1": 0, "y1": 0, "x2": 0, "y2": 50,
                      "construction": True, "name": "InnerAxis"})
    sketcher.execute("catia_close_sketch", {})
    part = conn.get_active_part()
    sketch = part_design._get_last_sketch(None)
    try:
        inner = part.FindObjectByName("InnerAxis")
        say(f"  found '{inner.Name}' in the tree")
        feature = revolve(part, sketch, part.CreateReferenceFromObject(inner))
        say(f"  ACCEPTED: '{feature.Name}', volume "
            f"{volume(conn, part_design):.1f} mm3")
    except Exception as error:  # noqa: BLE001
        say("  REFUSED " + " ".join(str(error).split())[:260])

    # ── C: is there a control-point route in the spline object? ──────────
    say("")
    say("── C: HybridShapeSpline members ──")
    fresh(conn, documents, "Spl")
    spline = gsd._spline({"points": [[0, 0, 0], [10, 10, 0], [20, 0, 0]],
                          "name": "S"})
    part = conn.get_active_part()
    element = None
    for name in ("S", "样条.1", "Spline.1"):
        try:
            element = part.FindObjectByName(name)
            if element is not None:
                break
        except Exception:  # noqa: BLE001
            continue
    if element is None:
        say("  could not find the spline element by name")
    else:
        rows = members(element)
        say(f"  {len(rows)} member(s): {[r[0] for r in rows]}")

    # ── D: sheet metal parameters ────────────────────────────────────────
    say("")
    say("── D: SheetMetalFactory.CreateSheetMetalParameters() ──")
    try:
        params = part.SheetMetalFactory.CreateSheetMetalParameters()
        say(f"  returned {type(params).__name__}")
        rows = members(params)
        say(f"  {len(rows)} member(s): {[r[0] for r in rows]}")
    except Exception as error:  # noqa: BLE001
        say("  REFUSED " + " ".join(str(error).split())[:260])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
