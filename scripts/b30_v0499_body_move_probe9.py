"""B30 probe, part 9 of 0.3.162: where does the moved solid land?

Part 8 proved the geometry: translate the boundary, CloseSurface, and the result
is the SAME solid at the new place (V 90000.0000 / A 13450.0000 to the digit).
Three questions decide the tool's shape:

  A  CloseSurface with InWorkObject = the ORIGINAL body: does it REPLACE the
     body's result (V 90000, box 5..30) or UNION with it (V 180000)?
  B  can the source body be removed afterwards (part.Bodies.Remove)?
  C  does a body with a through-pocket keep its volume through the trip?
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


def body_box(measure, name: str) -> str:
    try:
        data = payload(measure.execute("catia_get_bounding_box",
                                        {"body_name": name}))
        core = data.get("data") if isinstance(data.get("data"), dict) else data
        return f"{core.get('min')} .. {core.get('max')}"
    except Exception as error:  # noqa: BLE001
        return f"raises({str(error).splitlines()[0][:50]})"


def body_volume(spa, part, name: str) -> str:
    try:
        body = part.Bodies.Item(name)
    except Exception as error:  # noqa: BLE001
        return f"no body ({str(error).splitlines()[0][:40]})"
    try:
        return f"{spa.GetMeasurable(part.CreateReferenceFromObject(body)).Volume * 1e9:.4f}"
    except Exception as error:  # noqa: BLE001
        return f"raises({str(error).splitlines()[0][:40]})"


def build(conn, docs, sk, design, path, name, pocket: bool):
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "S"})
    sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 25, "y2": 45})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"sketch_name": "S", "height": 80})
    if pocket:
        sk.execute("catia_create_sketch", {"plane": "xy", "name": "H"})
        sk.execute("catia_sketch_circle", {"cx": 12.5, "cy": 22.5, "radius": 5})
        sk.execute("catia_close_sketch", {})
        design.execute("catia_pocket", {"sketch_name": "H", "depth": 80})


def translate_and_close(part, body, delta, target_owner, geoset_name):
    factory = part.HybridShapeFactory
    norm = sum(component ** 2 for component in delta) ** 0.5
    direction = factory.AddNewDirectionByCoord(*(component / norm for component in delta))
    geoset = part.HybridBodies.Add()
    geoset.Name = geoset_name
    translate = factory.AddNewTranslate(
        part.CreateReferenceFromObject(body), direction, norm)
    geoset.AppendHybridShape(translate)
    part.UpdateObject(translate)
    part.InWorkObject = target_owner
    closed = part.ShapeFactory.AddNewCloseSurface(
        part.CreateReferenceFromObject(translate))
    part.UpdateObject(closed)
    return closed


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
        path = os.path.join(root, f"MV9_{tag}.CATPart")
        try:
            docs.execute("catia_close_document", {"document": path, "save": False})
        except Exception:  # noqa: BLE001
            pass
        if os.path.isfile(path):
            os.remove(path)
        return path

    say("A. CloseSurface INTO the original body")
    path = fresh("A")
    build(conn, docs, sk, design, path, "MV9_A", pocket=False)
    part = conn.get_active_part()
    body = part.MainBody
    spa = conn.app.ActiveDocument.GetWorkbench("SPAWorkbench")
    closed = translate_and_close(part, body, (5.0, 0.0, 0.0), body, "MV9_A_SURF")
    say(f"  body V={body_volume(spa, part, body.Name)} box={body_box(measure, body.Name)}")
    say(f"  features: {[str(s.Name) for s in body.Shapes]}")

    say("\nB. can the source body be removed?")
    extra = part.Bodies.Add()
    extra.Name = "SECOND"
    try:
        part.Bodies.Remove(extra)
        say("  Bodies.Remove(empty body): accepted")
    except Exception as error:  # noqa: BLE001
        say(f"  Bodies.Remove: {type(error).__name__}: {str(error).splitlines()[0][:120]}")

    say("\nC. through-pocket body: does the volume survive?")
    path = fresh("C")
    build(conn, docs, sk, design, path, "MV9_C", pocket=True)
    part = conn.get_active_part()
    body = part.MainBody
    spa = conn.app.ActiveDocument.GetWorkbench("SPAWorkbench")
    before = body_volume(spa, part, body.Name)
    say(f"  source V={before} box={body_box(measure, body.Name)}")
    target = part.Bodies.Add()
    target.Name = "MOVED"
    closed = translate_and_close(part, body, (0.0, 0.0, 10.0), target, "MV9_C_SURF")
    say(f"  moved V={body_volume(spa, part, target.Name)} "
        f"box={body_box(measure, target.Name)}")
    say(f"  moved features: {[str(s.Name) for s in target.Shapes]}")

    for name in os.listdir(root):
        if name.startswith("MV9_"):
            try:
                docs.execute("catia_close_document",
                             {"document": os.path.join(root, name), "save": False})
            except Exception:  # noqa: BLE001
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
