"""B30 probe, part 8 of 0.3.162: translate the boundary, close it back into a solid.

This is the only route left that can produce a MOVED SOLID:
  body --GSD translate--> the same closed boundary, displaced
       --CloseSurface--> a solid with that boundary

If it works, catia_move_body is buildable on it - with two costs the reply must
state: the result is a dead solid (no feature history), and it has to be a
separate body or the original has to go.

Measured: the new body's volume must be 90000.0000 (the source's) and its box
must be x 5..30, not 0..25.
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


def measure_body(spa, part, body) -> str:
    try:
        measurable = spa.GetMeasurable(part.CreateReferenceFromObject(body))
        volume = f"{measurable.Volume * 1e9:.4f}"
        area = f"{measurable.Area * 1e6:.4f}"
    except Exception as error:  # noqa: BLE001
        return f"V raises({str(error).splitlines()[0][:40]})"
    return f"V={volume} A={area}"


def box(measure) -> str:
    try:
        data = payload(measure.execute("catia_get_bounding_box", {}))
        core = data.get("data") if isinstance(data.get("data"), dict) else data
        return f"{core.get('min')} .. {core.get('max')}"
    except Exception as error:  # noqa: BLE001
        return f"raises({str(error).splitlines()[0][:40]})"


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sk = SketcherTools(conn)
    design = PartDesignTools(conn)
    measure = MeasurementTools(conn)

    root = os.path.join(tempfile.gettempdir(), "p0499move")
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "MV8.CATPart")
    try:
        docs.execute("catia_close_document", {"document": path, "save": False})
    except Exception:  # noqa: BLE001
        pass
    if os.path.isfile(path):
        os.remove(path)

    docs.execute("catia_new_part", {"name": "MV8", "file_path": path})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "S"})
    sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 25, "y2": 45})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"sketch_name": "S", "height": 80})

    part = conn.get_active_part()
    body = part.MainBody
    spa = conn.app.ActiveDocument.GetWorkbench("SPAWorkbench")
    say(f"source body: {measure_body(spa, part, body)} box={box(measure)}")

    factory = part.HybridShapeFactory
    direction = factory.AddNewDirectionByCoord(1.0, 0.0, 0.0)
    geoset = part.HybridBodies.Add()
    geoset.Name = "MOVE_PROBE"
    translate = factory.AddNewTranslate(
        part.CreateReferenceFromObject(body), direction, 5.0)
    geoset.AppendHybridShape(translate)
    part.UpdateObject(translate)
    say(f"translated boundary: {translate.Name}")

    target = part.Bodies.Add()
    target.Name = "MOVED"
    part.InWorkObject = target
    try:
        closed = part.ShapeFactory.AddNewCloseSurface(
            part.CreateReferenceFromObject(translate))
        part.UpdateObject(closed)
        say(f"CloseSurface: {closed.Name} built")
    except Exception as error:  # noqa: BLE001
        say(f"CloseSurface FAILED: {type(error).__name__}: "
            f"{str(error).splitlines()[0][:200]}")
        docs.execute("catia_close_document", {"document": path, "save": False})
        return 1

    say(f"MOVED body: {measure_body(spa, part, target)}")
    say(f"part box now (both bodies present): {box(measure)}")

    moved_ref = part.CreateReferenceFromObject(closed)
    try:
        more = spa.GetMeasurable(moved_ref)
        say(f"the closed feature itself: V={more.Volume * 1e9:.4f} A={more.Area * 1e6:.4f}")
    except Exception as error:  # noqa: BLE001
        say(f"closed feature measurable raises: {str(error).splitlines()[0][:90]}")

    say("\nis the moved body where it should be? (hide the source, re-measure)")
    try:
        hso = conn.hso
        hso.Clear()
        hso.Add(body)
        hso.VisProperties.SetShow(0)
        part.Update()
        conn.refresh_display()
        say(f"with the source hidden, box={box(measure)}")
        hso.Clear()
        hso.Add(body)
        hso.VisProperties.SetShow(1)
        part.Update()
    except Exception as error:  # noqa: BLE001
        say(f"hide/show failed: {type(error).__name__}: {str(error).splitlines()[0][:120]}")

    docs.execute("catia_close_document", {"document": path, "save": False})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
