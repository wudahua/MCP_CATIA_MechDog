"""What can CATIA be asked about a sketch's SUPPORT, and about who consumes it?

Two questions from the 2026-11-18 0.3.148 report, and neither can be answered
from the code - both are claims about what B30 will tell us:

  A  "same plane" was the wrong proxy for "same datum". Comparing ORIGINS was
     the reporter's fallback suggestion. That only works if a sketch reports the
     datum's own point. If ``GetAbsoluteAxisData`` reports the projection of the
     part origin instead, two coplanar datums report the SAME origin and the
     suggestion cannot work at all. Measured here: two frame sketches, both on
     z = 0, datum points (0, 0, 0) and (50, 0, 0).
  B  a sketch built ON another sketch's frame by name - does its axis system
     differ from the frame's owner, or is it identical?
  C  is there any readable handle on the support itself? ``Sketch.Plane`` and
     ``Sketch.Support`` were measured absent on 2026-11-18; ``Reference`` is
     what the reporter suggested, so it gets its own cell.
  D  ``catia_delete_feature`` on a sketch a pad consumes: reproduce the reply,
     then look for a member on the pad that names its profile, which is what a
     "SKETCH_CONSUMED_BY" answer would have to read.

.venv/Scripts/python.exe -u scripts/b30_v0462_probe_support_and_consume.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp import sketch_frames  # noqa: E402
from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0462")


def say(line: str = "") -> None:
    print(line, flush=True)


def member(obj, name: str) -> str:
    """What ``obj.name`` answers - or the error, so absence is evidence too."""
    try:
        return repr(getattr(obj, name))
    except Exception as error:  # noqa: BLE001
        return f"<{str(error).splitlines()[0][:60]}>"


def axes_of(app, sketch) -> str:
    read = sketch_frames.read_absolute_axes(app, [sketch])
    if not read:
        return "<unreadable>"
    origin, h, v = read[0]
    return (f"o=({origin[0]:.6f},{origin[1]:.6f},{origin[2]:.6f}) "
            f"H=({h[0]:.3f},{h[1]:.3f},{h[2]:.3f}) "
            f"V=({v[0]:.3f},{v[1]:.3f},{v[2]:.3f})")


def distance(first, second) -> float:
    return sum((a - b) ** 2 for a, b in zip(first, second)) ** 0.5


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    say(f"probe v0462 on {conn.app.Name}")

    name = "ProbeV0462"
    path = os.path.join(ROOT, name + ".CATPart")
    for index in range(int(conn.documents.Count), 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = str(doc.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).lower().startswith(
                os.path.abspath(ROOT).lower()):
            try:
                doc.Close()
            except Exception:  # noqa: BLE001
                pass
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    part = conn.get_active_part()
    body = conn.get_active_part_body()
    app = conn.app

    # ── A: two coplanar datums, different datum points ────────────────────
    for sketch_name, origin in (("SK_A", (0.0, 0.0, 0.0)),
                                ("SK_B", (50.0, 0.0, 0.0))):
        sketcher.execute("catia_create_sketch",
                         {"plane": {"type": "frame", "origin": list(origin),
                                    "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0]},
                          "name": sketch_name})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0.0, "y1": 0.0, "x2": 20.0, "y2": 10.0})
        sketcher.execute("catia_close_sketch", {})
    say("A tree: " + str([str(body.Sketches.Item(i).Name)
                          for i in range(1, int(body.Sketches.Count) + 1)]))

    read = sketch_frames.read_absolute_axes(app, [body.Sketches.Item("SK_A"),
                                                  body.Sketches.Item("SK_B")])
    say(f"A SK_A axes: {axes_of(app, body.Sketches.Item('SK_A'))}")
    say(f"A SK_B axes: {axes_of(app, body.Sketches.Item('SK_B'))}")
    if read:
        gap = distance(read[0][0], read[1][0])
        say(f"A origin gap between the two sketches: {gap:.6f} mm  "
            f"<-- {'DISTINGUISHES the datums' if gap > 1e-3 else 'SAME origin: '
               'projection, not the datum point'}")

    # ── B: a sketch built ON SK_A's frame by name ─────────────────────────
    sketcher.execute("catia_create_sketch",
                     {"plane": "SK_A_FRAME", "name": "ON_FRAME"})
    sketcher.execute("catia_sketch_circle",
                     {"cx": 5.0, "cy": 5.0, "radius": 3.0})
    sketcher.execute("catia_close_sketch", {})
    on_frame = body.Sketches.Item("ON_FRAME")
    say(f"B ON_FRAME axes: {axes_of(app, on_frame)}")
    read = sketch_frames.read_absolute_axes(app, [body.Sketches.Item("SK_A"),
                                                 on_frame])
    if read:
        say(f"B origin gap ON_FRAME vs SK_A: "
            f"{distance(read[0][0], read[1][0]):.6f} mm")

    # ── C: is the support object readable at all? ─────────────────────────
    # A successful getattr on a late-bound COM object is NOT evidence that the
    # member exists - the first run printed <Item.Reference> for names recorded
    # as absent, so every name is used as well as looked up.
    sketch = body.Sketches.Item("SK_A")
    for attribute in ("Reference", "Support", "Plane", "AbsoluteAxis", "Parent"):
        looked = member(sketch, attribute)
        used = member(getattr(sketch, attribute, None), "Name") if "COMObject" in looked \
            or looked.startswith("<Item.") else "-"
        say(f"C SK_A.{attribute}: {looked}   .Name -> {used}")
    for name in ("CATIAReference", "Reference", "Support"):
        try:
            item = sketch.GetItem(name)
            say(f"C GetItem({name!r}): {getattr(item, 'Name', item)!r}")
        except Exception as error:  # noqa: BLE001
            say(f"C GetItem({name!r}): <{str(error).splitlines()[0][:70]}>")

    # ── D: a pad that consumes SK_A ───────────────────────────────────────
    try:
        reply = parts.execute("catia_pad", {"sketch_name": "SK_A", "height": 10.0})
        say("D pad: " + " ".join(reply.split())[:180])
    except Exception as error:  # noqa: BLE001
        say(f"D pad raised: {type(error).__name__}: "
            f"{str(error).splitlines()[0][:160]}")
    shapes = body.Shapes
    say("D shapes: " + str([str(shapes.Item(i).Name)
                            for i in range(1, int(shapes.Count) + 1)]))
    for position in range(1, int(shapes.Count) + 1):
        shape = shapes.Item(position)
        line = f"D  {shape.Name}:"
        for attribute in ("Sketch", "Profile", "FirstLimit", "Sections",
                          "Section", "Spine"):
            line += f"  {attribute}={member(shape, attribute)[:70]}"
        say(line)
        for attribute in ("Sketch",):
            try:
                profile = getattr(shape, attribute)
                say(f"D  {shape.Name}.{attribute}.Name = {getattr(profile, 'Name', '?')}")
            except Exception:  # noqa: BLE001
                pass

    # ── E: reproduce the misdiagnosis, and see whether it is recoverable ──
    try:
        reply = parts.execute("catia_delete_feature", {"feature_name": "SK_A"})
        say("E delete SK_A: " + " ".join(reply.split())[:600])
    except Exception as error:  # noqa: BLE001
        say(f"E delete SK_A raised: {type(error).__name__}: "
            f"{str(error).splitlines()[0][:200]}")
    try:
        say("E update: " + " ".join(
            MeasurementTools(conn).execute("catia_update_part", {}).split())[:120])
    except Exception as error:  # noqa: BLE001
        say(f"E update raised: {type(error).__name__}: "
            f"{str(error).splitlines()[0][:160]}")
    say("E tree after: " + str([str(body.Sketches.Item(i).Name)
                                for i in range(1, int(body.Sketches.Count) + 1)]))

    # ── F: a consumed sketch that is ALSO in the same delete call ─────────
    try:
        reply = parts.execute("catia_delete_feature",
                              {"feature_name": "凸台.1"})
        say("F delete pad: " + " ".join(reply.split())[:300])
    except Exception as error:  # noqa: BLE001
        say(f"F delete pad raised: {type(error).__name__}: "
            f"{str(error).splitlines()[0][:200]}")
    say("F tree after: " + str([str(body.Sketches.Item(i).Name)
                                for i in range(1, int(body.Sketches.Count) + 1)]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
