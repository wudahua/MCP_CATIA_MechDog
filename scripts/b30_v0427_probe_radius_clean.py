"""One radius constraint on one circle, in a document where nothing else happened.

Round two's C step failed because it ran in the same sketch where 0..20 had already
been tried: six radius constraints on one circle, so `Dimension` answered
"未指定的错误". The type itself worked - type 7 made '半径.2' with Dimension=6.0 -
so the question is whether ONE such constraint can be driven, and whether the
driven value reaches the geometry and the parameters list.

A  circle: constraint type 7, Dimension read, Dimension := 11, update, Radius back
B  the same for an ellipse: which type makes a semi-axis constraint
C  whether the constraint appears in part.Parameters with a path set_formula takes

    .venv/Scripts/python.exe scripts/b30_v0427_probe_radius_clean.py
"""

from __future__ import annotations

import math
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
ROOT = os.path.join(_PROBE_ROOT, "radius")


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


def parameter_names(part) -> list[str]:
    out = []
    params = part.Parameters
    for index in range(1, int(params.Count) + 1):
        try:
            out.append(str(params.Item(index).Name))
        except Exception:  # noqa: BLE001
            continue
    return out


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    pd = PartDesignTools(conn)

    # ── A: one radius constraint, driven ───────────────────────────────
    say("=" * 74)
    say("A: a single radius constraint on a circle")
    say("=" * 74)
    part = fresh(conn, documents, "RadiusOne")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_circle", {"cx": 10, "cy": 10, "radius": 6})
    sketch = part.MainBody.Sketches.Item(int(part.MainBody.Sketches.Count))
    circle = find(sketch, "圆")
    constraints = sketch.Constraints
    created = constraints.AddMonoEltCst(7, circle)
    say(f"  constraint '{created.Name}', count {int(constraints.Count)}")
    dimension = created.Dimension
    say(f"  Dimension.Value = {dimension.Value!r}")
    dimension.Value = 11.0
    say(f"  set to 11 -> {dimension.Value!r}")
    try:
        part.Update()
        say(f"  after part.Update: circle.Radius = {circle.Radius!r}")
    except Exception as error:  # noqa: BLE001
        say("  update refused: " + " ".join(str(error).split())[:200])
    say(f"  broken {int(constraints.BrokenConstraintsCount)}, "
        f"unupdated {int(constraints.UnUpdatedConstraintsCount)}")
    say("  parameters after the constraint:")
    for name in parameter_names(part):
        if "草图" in name:
            say(f"    {name}")

    try:
        say("  " + " ".join(str(sketcher.execute(
            "catia_close_sketch", {})).split())[:240])
    except Exception as error:  # noqa: BLE001
        say("  close refused: " + " ".join(str(error).split())[:200])
    try:
        say("  " + " ".join(str(pd.execute(
            "catia_pad", {"height": 10})).split())[:180])
        volume = pd._body_volume_mm3(part, part.MainBody)
        say(f"  volume {volume:.1f} mm3; pi*121*10 = {math.pi * 121 * 10:.1f}")
    except Exception as error:  # noqa: BLE001
        say("  pad refused: " + " ".join(str(error).split())[:200])

    # ── B: the ellipse's semi-axes ─────────────────────────────────────
    say("")
    say("=" * 74)
    say("B: which type makes a semi-axis constraint on an ellipse")
    say("=" * 74)
    part = fresh(conn, documents, "RadiusEll")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_ellipse",
                     {"cx": 25, "cy": 15, "major": 9, "minor": 4.5})
    sketch = part.MainBody.Sketches.Item(int(part.MainBody.Sketches.Count))
    ellipse = find(sketch, "椭圆")
    say(f"  ellipse {ellipse.MajorRadius!r}/{ellipse.MinorRadius!r}")
    constraints = sketch.Constraints
    for kind in range(0, 21):
        try:
            created = constraints.AddMonoEltCst(kind, ellipse)
        except Exception:  # noqa: BLE001
            continue
        name = str(getattr(created, "Name", "?"))
        try:
            value = created.Dimension.Value
        except Exception:  # noqa: BLE001
            value = "no Dimension"
        say(f"  type {kind:2d} -> '{name}' Value={value!r} "
            f"(count {int(constraints.Count)})")
        if kind < 20:
            try:
                constraints.Remove(created)
            except Exception:  # noqa: BLE001
                pass

    say("")
    say("done")
    close_ours(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
