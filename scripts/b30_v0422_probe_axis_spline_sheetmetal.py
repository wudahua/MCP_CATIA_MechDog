"""Can a shaft revolve about an ARBITRARY axis, and does the typelib have more?

Three questions, because they were the three left on the list:

A  shaft about an external line (a gsd_line), volume checked by Pappus
B  shaft about a construction line INSIDE the sketch - the docstring says E_FAIL
C  every "Spline" / "Control" / "Sheet" member the factory really exposes
D  any sheet-metal member at all, anywhere in the app/part type info

A and B are one fresh document each: a failed update is document-wide, and the
whole point is to find out which of the two refusals is real.

    .venv/Scripts/python.exe scripts/b30_v0422_probe_axis_spline_sheetmetal.py
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
ROOT = os.path.join(_PROBE_ROOT, "axis")


def say(line: str = "") -> None:
    print(line, flush=True)


def activate(conn: CATIAConnection, name: str) -> None:
    for index in range(1, conn.documents.Count + 1):
        candidate = conn.documents.Item(index)
        if candidate.Name == name:
            candidate.Activate()
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


def volume(conn, part_design):
    part = conn.get_active_part()
    return part_design._body_volume_mm3(part, part.MainBody)


def members(obj, containing: str) -> list[str]:
    try:
        info = obj._oleobj_.GetTypeInfo()
        count = info.GetTypeAttr().cFuncs
    except Exception as error:  # noqa: BLE001
        say("  typelib unreadable: " + str(error).splitlines()[0][:80])
        return []
    names = set()
    for index in range(count):
        try:
            name = info.GetNames(info.GetFuncDesc(index).memid)[0]
        except Exception:  # noqa: BLE001
            continue
        if containing.lower() in name.lower():
            names.add(name)
    return sorted(names)


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    gsd = GSDTools(conn)

    part = None
    factory = None

    # ── A: profile off the axis, revolved about an EXTERNAL line ─────────
    say("")
    say("── A: rectangle x 30..40, z 0..20 revolved about a gsd_line on Z ──")
    path = os.path.join(ROOT, "AxisA.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": "AxisA", "file_path": path, "overwrite": True})
    activate(conn, "AxisA.CATPart")
    part = conn.get_active_part()
    factory = part.ShapeFactory
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 0], "point2": [0, 0, 50], "name": "SpinAxis"})
    sketcher.execute("catia_create_sketch", {"plane": "zx"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 30, "y1": 0, "x2": 40, "y2": 20})
    sketcher.execute("catia_close_sketch", {})
    try:
        reply = part_design.execute("catia_shaft",
                                    {"angle": 360, "axis": "SpinAxis"})
        say("  shaft: " + " ".join(str(reply).split())[:200])
        got = volume(conn, part_design)
        # Pappus: 2*pi*R_centroid*area = 2*pi*35*200
        expected = 2 * 3.14159265358979 * 35.0 * 200.0
        say(f"  volume {got:.1f} mm3, Pappus predicts {expected:.1f}")
    except Exception as error:  # noqa: BLE001
        say("  REFUSED " + " ".join(str(error).split())[:260])

    # ── B: a construction line inside the sketch ─────────────────────────
    say("")
    say("── B: the same profile, axis = a construction line in the sketch ──")
    path = os.path.join(ROOT, "AxisB.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": "AxisB", "file_path": path, "overwrite": True})
    activate(conn, "AxisB.CATPart")
    sketcher.execute("catia_create_sketch", {"plane": "zx"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 30, "y1": 0, "x2": 40, "y2": 20})
    sketcher.execute("catia_sketch_line",
                     {"x1": 0, "y1": 0, "x2": 0, "y2": 50,
                      "construction": True, "name": "InnerAxis"})
    sketcher.execute("catia_close_sketch", {})
    try:
        reply = part_design.execute("catia_shaft",
                                    {"angle": 360, "axis": "InnerAxis"})
        say("  shaft: " + " ".join(str(reply).split())[:200])
        say(f"  volume {volume(conn, part_design):.1f} mm3")
    except Exception as error:  # noqa: BLE001
        say("  REFUSED " + " ".join(str(error).split())[:260])

    # ── C: what the factory really exposes ───────────────────────────────
    say("")
    say("── C: factory members mentioning Spline / Control / Sheet ──")
    for word in ("Spline", "Control", "Sheet", "Revolution", "Revolve"):
        found = members(factory, word)
        say(f"  {word}: {found}")

    # ── D: any sheet-metal member at all ─────────────────────────────────
    say("")
    say("── D: sheet metal anywhere in the part's type info ──")
    for word in ("Sheet", "Metal", "Flange", "Bend", "Stiffener"):
        say(f"  part.{word}: {members(part, word)}")
    try:
        shelves = conn.catia.GetWorkbenchNames()
        metal = [name for name in shelves
                 if "sheet" in name.lower() or "metal" in name.lower()]
        say(f"  workbenches mentioning sheet/metal: {metal}")
        say(f"  total workbenches: {len(shelves)}")
    except Exception as error:  # noqa: BLE001
        say("  workbench list failed: " + str(error).splitlines()[0][:100])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
