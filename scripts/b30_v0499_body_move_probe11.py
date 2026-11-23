"""B30 probe, part 11 of 0.3.162: the shape that could silently lose material.

The route translates the body's boundary surface and closes it back. That is
exact for one closed shell. Two different bodies in ONE body - and a body with
fillets - are the cases where "close the surface" could quietly return only part
of the solid, which is exactly the class of failure the reports are about. So:

  A  MainBody with two disjoint pads (V 90000 + 12000), moved by (10, 0, 0)
  B  a pad with an edge fillet (curved boundary), moved by (0, 0, 5)

Both must keep the volume to the digit AND move every lump.
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


def move(part, source, delta, tags: str):
    norm = sum(component ** 2 for component in delta) ** 0.5
    factory = part.HybridShapeFactory
    direction = factory.AddNewDirectionByCoord(
        *(component / norm for component in delta))
    geoset = part.HybridBodies.Add()
    geoset.Name = f"{tags}_SURF"
    translate = factory.AddNewTranslate(
        part.CreateReferenceFromObject(source), direction, norm)
    geoset.AppendHybridShape(translate)
    part.UpdateObject(translate)
    moved = part.Bodies.Add()
    moved.Name = f"{tags}_MOVED"
    part.InWorkObject = moved
    closed = part.ShapeFactory.AddNewCloseSurface(
        part.CreateReferenceFromObject(translate))
    part.UpdateObject(closed)
    return moved


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sk = SketcherTools(conn)
    design = PartDesignTools(conn)
    measure = MeasurementTools(conn)

    root = os.path.join(tempfile.gettempdir(), "p0499move")
    os.makedirs(root, exist_ok=True)

    def fresh(tag):
        path = os.path.join(root, f"MV11_{tag}.CATPart")
        try:
            docs.execute("catia_close_document", {"document": path, "save": False})
        except Exception:  # noqa: BLE001
            pass
        if os.path.isfile(path):
            os.remove(path)
        docs.execute("catia_new_part", {"name": f"MV11_{tag}", "file_path": path})
        return path

    say("A. two disjoint pads in one body")
    fresh("A")
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "S1"})
    sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 25, "y2": 45})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"sketch_name": "S1", "height": 80})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "S2"})
    sk.execute("catia_sketch_rectangle", {"x1": 100, "y1": 0, "x2": 110, "y2": 30})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"sketch_name": "S2", "height": 40})

    part = conn.get_active_part()
    source = part.MainBody
    spa = conn.app.ActiveDocument.GetWorkbench("SPAWorkbench")
    before_volume = volume_of(spa, part, source.Name)
    before_box = box_of(measure, source.Name)
    say(f"  source V={before_volume:.4f} box={json.dumps(before_box)}")
    moved = move(part, source, (10.0, 0.0, 0.0), "TWOLUMP")
    after_volume = volume_of(spa, part, moved.Name)
    say(f"  moved  V={after_volume:.4f} box={json.dumps(box_of(measure, moved.Name))}")
    say(f"  lumps kept: {'YES' if before_volume and after_volume and abs(before_volume - after_volume) < 1e-3 else 'NO'}")

    say("\nB. a pad with edge fillets")
    fresh("B")
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "S"})
    sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 25, "y2": 45})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"sketch_name": "S", "height": 40})
    reply = design.execute("catia_list_edges", {})
    edges = payload(reply)
    rows = edges.get("edges") if isinstance(edges, dict) else edges
    vertical = [row["name"] for row in rows
                if abs(float(row["length_mm"]) - 40.0) < 0.01]
    say(f"  40 mm edges: {vertical}")
    design.execute("catia_fillet", {"radius": 3, "edge_names": vertical,
                                    "sequential": True})
    part = conn.get_active_part()
    source = part.MainBody
    spa = conn.app.ActiveDocument.GetWorkbench("SPAWorkbench")
    before_volume = volume_of(spa, part, source.Name)
    before_box = box_of(measure, source.Name)
    say(f"  source V={before_volume:.4f} box={json.dumps(before_box)}")
    moved = move(part, source, (0.0, 0.0, 5.0), "FILLET")
    after_volume = volume_of(spa, part, moved.Name)
    say(f"  moved  V={after_volume:.4f} box={json.dumps(box_of(measure, moved.Name))}")
    say(f"  volume kept: "
        f"{'YES' if before_volume and after_volume and abs(before_volume - after_volume) < 1e-3 else 'NO'}")

    for name in os.listdir(root):
        if name.startswith("MV11_"):
            try:
                docs.execute("catia_close_document",
                             {"document": os.path.join(root, name), "save": False})
            except Exception:  # noqa: BLE001
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
