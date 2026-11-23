"""B30 probe, part 10 of 0.3.162: the two facts the tool needs.

A  a body with a THROUGH POCKET, moved into a new body: volume must be identical
   (the tunnel has to survive) and the moved body's OWN box must be the source's
   box + delta - measured with body_name, not the whole part.
B  can a body be removed at all (part.Bodies.Remove), which decides whether
   replace=true is offerable or the source must stay.
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


def say(text: str = "") -> None:
    print(text, flush=True)


def payload(reply):
    return reply if isinstance(reply, (dict, list)) else json.loads(str(reply))


def box_of(measure, body_name: str) -> dict:
    data = payload(measure.execute("catia_get_bounding_box",
                                    {"body_name": body_name}))
    core = data.get("data") if isinstance(data.get("data"), dict) else data
    return {"min": core.get("min"), "max": core.get("max")}


def volume_of(spa, part, body_name: str) -> float | None:
    try:
        body = part.Bodies.Item(body_name)
        return spa.GetMeasurable(part.CreateReferenceFromObject(body)).Volume * 1e9
    except Exception as error:  # noqa: BLE001
        say(f"    volume({body_name}) raises: {str(error).splitlines()[0][:70]}")
        return None


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sk = SketcherTools(conn)
    design = PartDesignTools(conn)
    measure = MeasurementTools(conn)

    root = os.path.join(tempfile.gettempdir(), "p0499move")
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "MV10.CATPart")
    try:
        docs.execute("catia_close_document", {"document": path, "save": False})
    except Exception:  # noqa: BLE001
        pass
    if os.path.isfile(path):
        os.remove(path)

    docs.execute("catia_new_part", {"name": "MV10", "file_path": path})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "S"})
    sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 25, "y2": 45})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"sketch_name": "S", "height": 80})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "H"})
    sk.execute("catia_sketch_circle", {"cx": 12.5, "cy": 22.5, "radius": 5})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pocket", {"sketch_name": "H", "depth": 80})

    part = conn.get_active_part()
    source = part.MainBody
    spa = conn.app.ActiveDocument.GetWorkbench("SPAWorkbench")
    say(f"source: V={volume_of(spa, part, source.Name):.4f} "
        f"box={json.dumps(box_of(measure, source.Name))}")

    delta = (0.0, 0.0, 10.0)
    norm = 10.0
    factory = part.HybridShapeFactory
    direction = factory.AddNewDirectionByCoord(0.0, 0.0, 1.0)
    geoset = part.HybridBodies.Add()
    geoset.Name = "MV10_SURF"
    translate = factory.AddNewTranslate(
        part.CreateReferenceFromObject(source), direction, norm)
    geoset.AppendHybridShape(translate)
    part.UpdateObject(translate)

    moved = part.Bodies.Add()
    moved.Name = "MOVED"
    part.InWorkObject = moved
    closed = part.ShapeFactory.AddNewCloseSurface(
        part.CreateReferenceFromObject(translate))
    part.UpdateObject(closed)
    say(f"moved:  V={volume_of(spa, part, moved.Name):.4f} "
        f"box={json.dumps(box_of(measure, moved.Name))}")
    say(f"moved features: {[str(s.Name) for s in moved.Shapes]}")

    say("\nB. part.Bodies.Remove(moved) - does removal work at all?")
    try:
        part.Bodies.Remove(moved)
        say(f"  accepted; bodies now {[str(b.Name) for b in part.Bodies]}")
    except Exception as error:  # noqa: BLE001
        say(f"  {type(error).__name__}: {str(error).splitlines()[0][:140]}")

    say("\nB2. and the SOURCE body, with the moved solid still depending on it?")
    try:
        part.Bodies.Remove(source)
        part.Update()
        say(f"  accepted; bodies now {[str(b.Name) for b in part.Bodies]}")
        say(f"  moved V={volume_of(spa, part, 'MOVED')} "
            f"box={json.dumps(box_of(measure, 'MOVED'))}")
    except Exception as error:  # noqa: BLE001
        say(f"  {type(error).__name__}: {str(error).splitlines()[0][:140]}")

    docs.execute("catia_close_document", {"document": path, "save": False})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
