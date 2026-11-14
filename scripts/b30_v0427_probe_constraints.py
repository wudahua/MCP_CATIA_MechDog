"""Can a sketch dimension be CHANGED, and can a constraint be created with a value?

Round one proved the geometry is readable: 圆.1.Radius = 6.0, 椭圆.1.MajorRadius =
9.0, MinorRadius = 4.5. That already contradicts "coordinates are NOT returned" as
it was written. Two questions decide whether the 2026-11-14 report's P0-2/P0-3 can
be fixed at all:

A  is Radius / MajorRadius WRITABLE, and does the sketch then rebuild?
B  which CatConstraintType makes a RADIUS (or semi-axis) constraint, so that a
   value shows up in part.Parameters and can be driven by set_parameter?
C  if such a constraint exists, is Dimension.Value readable AND writable?

    .venv/Scripts/python.exe scripts/b30_v0427_probe_constraints.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0427")
ROOT = os.path.join(_PROBE_ROOT, "constraints")


def say(line: str = "") -> None:
    print(line, flush=True)


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


def fresh(conn, documents, name):
    path = os.path.join(ROOT, f"{name}.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    for index in range(1, int(conn.documents.Count) + 1):
        if conn.documents.Item(index).Name == f"{name}.CATPart":
            conn.documents.Item(index).Activate()
    return conn.get_active_part()


def find(sketch, prefix: str):
    for index in range(1, int(sketch.GeometricElements.Count) + 1):
        element = sketch.GeometricElements.Item(index)
        if str(element.Name).startswith(prefix):
            return element
    return None


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)

    # ── A: write the radii back ─────────────────────────────────────────
    say("=" * 74)
    say("A: Radius / MajorRadius / MinorRadius, written")
    say("=" * 74)
    part = fresh(conn, documents, "WriteRadii")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_circle", {"cx": 10, "cy": 10, "radius": 6})
    sketcher.execute("catia_sketch_ellipse",
                     {"cx": 25, "cy": 15, "major": 9, "minor": 4.5})
    sketch = part.MainBody.Sketches.Item(int(part.MainBody.Sketches.Count))
    circle = find(sketch, "圆")
    ellipse = find(sketch, "椭圆")
    say(f"  before: circle.Radius={circle.Radius}, "
        f"ellipse {ellipse.MajorRadius}/{ellipse.MinorRadius}")
    for label, element, attribute, value in (
        ("circle.Radius", circle, "Radius", 9.0),
        ("ellipse.MajorRadius", ellipse, "MajorRadius", 12.0),
        ("ellipse.MinorRadius", ellipse, "MinorRadius", 6.0),
    ):
        try:
            setattr(element, attribute, value)
            say(f"  {label} := {value} ACCEPTED, reads back "
                f"{getattr(element, attribute)!r}")
        except Exception as error:  # noqa: BLE001
            say(f"  {label} := {value} refused: "
                + str(error).splitlines()[0][:100])
    try:
        closed = str(sketcher.execute("catia_close_sketch", {}))
        say("  " + " ".join(closed.split())[:260])
    except Exception as error:  # noqa: BLE001
        say("  close refused: " + " ".join(str(error).split())[:200])
    # does the PAD that consumes it get bigger?
    from catia_mcp.tools.part_design import PartDesignTools

    pd = PartDesignTools(conn)
    try:
        say("  " + " ".join(str(pd.execute(
            "catia_pad", {"height": 10})).split())[:200])
        volume = pd._body_volume_mm3(part, part.MainBody)
        say(f"  volume {volume:.1f} mm3 "
            "(pi*81*10 = 2544.7 for the circle alone; the ellipse does not pad)")
    except Exception as error:  # noqa: BLE001
        say("  pad refused: " + " ".join(str(error).split())[:200])

    # ── B: which CatConstraintType is a radius? ─────────────────────────
    say("")
    say("=" * 74)
    say("B: AddMonoEltCst(type, circle) for type 0..20")
    say("=" * 74)
    part = fresh(conn, documents, "ConstraintTypes")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_circle", {"cx": 10, "cy": 10, "radius": 6})
    sketch = part.MainBody.Sketches.Item(int(part.MainBody.Sketches.Count))
    circle = find(sketch, "圆")
    constraints = sketch.Constraints
    for kind in range(0, 21):
        before = int(constraints.Count)
        try:
            created = constraints.AddMonoEltCst(kind, circle)
        except Exception as error:  # noqa: BLE001
            say(f"  type {kind:2d} -> refused "
                + str(error).splitlines()[0][:70])
            continue
        name = ""
        try:
            name = str(created.Name)
        except Exception:  # noqa: BLE001
            name = "?"
        dimension = ""
        try:
            dimension = f"Dimension={created.Dimension.Value!r}"
        except Exception as error:  # noqa: BLE001
            dimension = "no Dimension"
        say(f"  type {kind:2d} -> '{name}' ({dimension}), "
            f"count {before}->{int(constraints.Count)}")
        try:
            constraints.Remove(created)
        except Exception:  # noqa: BLE001
            pass

    # ── C: drive the radius constraint ──────────────────────────────────
    say("")
    say("=" * 74)
    say("C: keep the radius constraint and change its Dimension")
    say("=" * 74)
    for candidate in range(0, 21):
        before = int(constraints.Count)
        try:
            created = constraints.AddMonoEltCst(candidate, circle)
        except Exception:  # noqa: BLE001
            continue
        name = str(getattr(created, "Name", ""))
        if "半径" not in name and "adius" not in name:
            try:
                constraints.Remove(created)
            except Exception:  # noqa: BLE001
                pass
            continue
        say(f"  radius constraint is type {candidate}: '{name}'")
        try:
            dimension = created.Dimension
            say(f"    members: {', '.join(str(n) for n in dir(dimension)[:20])}")
            say(f"    Value = {dimension.Value!r}")
            dimension.Value = 11.0
            say(f"    Value := 11 -> {dimension.Value!r}")
            try:
                part.Update()
                say(f"    circle.Radius after update = {circle.Radius!r}")
            except Exception as error:  # noqa: BLE001
                say("    update -> " + str(error).splitlines()[0][:100])
        except Exception as error:  # noqa: BLE001
            say("    Dimension -> " + str(error).splitlines()[0][:110])
        break

    say("")
    say("done")
    close_ours(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
