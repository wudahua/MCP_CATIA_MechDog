"""The 2026-11-14 report's two P0s, asked of CATIA directly.

The claim under test is written in the tool: "a CATIA Point2D exposes no readable
X/Y through automation on this build". That sentence is about a POINT. An ellipse
is an Ellipse2D and a circle is a Circle2D, and a conclusion about one 2D type has
already been wrongly carried to another in this project (0.3.121: GetMeasurable on
a hybrid reference does not behave like GetMeasurable on a topological face).

A  what every 2D element exposes, read one member at a time
B  the Constraints collection - what a constraint can be created with
C  HybridShapeFactory, for a 3D ellipse (the shortcut for elliptic sweep sections)
D  whether a constraint's Dimension is readable AND writable, which is the whole
   of "can a sketch be driven by a parameter"

    .venv/Scripts/python.exe scripts/b30_v0427_probe_sketch_read.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0427")
ROOT = os.path.join(_PROBE_ROOT, "sketchread")

# Everything a 2D element might plausibly answer, tried by name: this is a probe,
# so the question is "which of these exist", not "does my favourite one work".
CANDIDATES = (
    "X", "Y", "StartX", "StartY", "EndX", "EndY", "CenterX", "CenterY",
    "Radius", "MajorRadius", "MinorRadius", "MajorAxis", "MinorAxis",
    "HorizontalAxis", "VerticalAxis", "StartParam", "EndParam", "GetParameter",
    "GetCoordinates", "StartPoint", "EndPoint", "Center", "GetPoint",
    "GeometricType", "Construction", "Name",
)


def say(line: str = "") -> None:
    print(line, flush=True)


def members(obj: object) -> list[str]:
    try:
        info = obj._oleobj_.GetTypeInfo()
        count = info.GetTypeAttr().cFuncs
    except Exception:  # noqa: BLE001
        return []
    names: list[str] = []
    for index in range(count):
        try:
            names.append(str(info.GetNames(info.GetFuncDesc(index).memid)[0]))
        except Exception:  # noqa: BLE001
            continue
    return names


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


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)

    path = os.path.join(ROOT, "SketchRead.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": "SketchRead", "file_path": path, "overwrite": True})
    for index in range(1, int(conn.documents.Count) + 1):
        if conn.documents.Item(index).Name == "SketchRead.CATPart":
            conn.documents.Item(index).Activate()
    part = conn.get_active_part()

    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_line", {"x1": 0, "y1": 0, "x2": 40, "y2": 0})
    sketcher.execute("catia_sketch_circle", {"cx": 10, "cy": 10, "radius": 6})
    sketcher.execute("catia_sketch_ellipse",
                     {"cx": 25, "cy": 15, "major": 9, "minor": 4.5, "angle": 30})
    sketcher.execute("catia_sketch_spline",
                     {"points": [[0, 20], [10, 26], [20, 18], [30, 24]]})
    sketch = part.MainBody.Sketches.Item(int(part.MainBody.Sketches.Count))
    say(f"sketch '{sketch.Name}' has {int(sketch.GeometricElements.Count)} elements")

    # ── A: what each 2D element answers ─────────────────────────────────
    say("")
    say("=" * 74)
    say("A: 2D element members and readable values")
    say("=" * 74)
    for index in range(1, int(sketch.GeometricElements.Count) + 1):
        element = sketch.GeometricElements.Item(index)
        name = str(element.Name)
        if name.startswith("绝对轴"):
            continue
        say(f"  [{index}] {name}")
        got = members(element)
        say(f"      members ({len(got)}): {', '.join(got[:28])}")
        for attribute in CANDIDATES:
            try:
                value = getattr(element, attribute)
            except Exception:  # noqa: BLE001 - absent is the normal case
                continue
            if callable(value):
                continue
            say(f"      {attribute} = {value!r}")

    # ── B: the constraints collection ───────────────────────────────────
    say("")
    say("=" * 74)
    say("B: Sketch.Constraints")
    say("=" * 74)
    constraints = sketch.Constraints
    say(f"  {int(constraints.Count)} constraint(s) to start with")
    if int(constraints.Count):
        first = constraints.Item(1)
        say(f"  a constraint's members: {', '.join(members(first)[:30])}")
    say(f"  collection members: {', '.join(members(constraints)[:40])}")

    # ── C: a 3D ellipse in the factory ──────────────────────────────────
    say("")
    say("=" * 74)
    say("C: HybridShapeFactory, ellipse-related members")
    say("=" * 74)
    factory = part.HybridShapeFactory
    all_members = members(factory)
    say(f"  {len(all_members)} members")
    say("  " + ", ".join(n for n in all_members
                         if any(k in n.lower()
                                for k in ("ellipse", "conic", "circle"))))

    # ── D: is a Dimension writable? ─────────────────────────────────────
    say("")
    say("=" * 74)
    say("D: create a radius constraint on the circle, then change it")
    say("=" * 74)
    circle = None
    for index in range(1, int(sketch.GeometricElements.Count) + 1):
        element = sketch.GeometricElements.Item(index)
        if "Circle" in str(element.Name) or "圆" in str(element.Name):
            circle = element
            break
    if circle is None:
        say("  no circle found")
    else:
        say(f"  circle: {circle.Name}")
        created = None
        for label, call in (
            ("AddMonoEltCst(catCstRadius, circle)",
             lambda: constraints.AddMonoEltCst(0, circle)),
            ("AddMonoEltCst(1, circle)", lambda: constraints.AddMonoEltCst(1, circle)),
        ):
            try:
                created = call()
                say(f"  {label} -> {created!r}")
                break
            except Exception as error:  # noqa: BLE001
                say(f"  {label} -> {str(error).splitlines()[0][:100]}")
        if created is not None:
            say(f"  constraints now {int(constraints.Count)}")
            try:
                say(f"  Dimension = {getattr(created, 'Dimension', '?')!r}")
                dimension = created.Dimension
                say(f"  Dimension members: {', '.join(members(dimension)[:25])}")
                try:
                    say(f"  Dimension.Value = {dimension.Value!r}")
                    dimension.Value = 8.0
                    say(f"  after Value := 8 -> {dimension.Value!r}")
                except Exception as error:  # noqa: BLE001
                    say("  Dimension.Value -> " + str(error).splitlines()[0][:110])
            except Exception as error:  # noqa: BLE001
                say("  Dimension -> " + str(error).splitlines()[0][:110])
            try:
                say("  constraint name: " + str(created.Name))
            except Exception as error:  # noqa: BLE001
                say("  name -> " + str(error).splitlines()[0][:80])

    # ── E: does the constraint show up as a PARAMETER? ──────────────────
    say("")
    say("=" * 74)
    say("E: part.Parameters after the constraint exists")
    say("=" * 74)
    try:
        sketcher.execute("catia_close_sketch", {})
    except Exception as error:  # noqa: BLE001
        say("  close refused: " + " ".join(str(error).split())[:200])
    params = part.Parameters
    names = []
    for index in range(1, int(params.Count) + 1):
        try:
            names.append(str(params.Item(index).Name))
        except Exception:  # noqa: BLE001
            continue
    say(f"  {len(names)} parameter(s): " + ", ".join(names[:25]))

    say("")
    say("done")
    close_ours(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
