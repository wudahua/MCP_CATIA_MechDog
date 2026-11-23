"""B30 probe, part 7 of 0.3.162: the last two routes to a moved solid.

E. Selection-driven AddNewTranslate2: the UI's Translate needs a selection, so
   the automation may read the object from there instead of the argument list.
F. PartDocument.Product.Position: a CATPart carries a Product, and a Product has
   a writable Position. If the part's own geometry honours it, one call moves
   everything - which is what catia_move_body would then be built on.
G. HybridShapeFactory.AddNewPositionTransfo, declared with one argument.

Same block and the same two readings every time: volume must not change, and the
box must move by exactly the requested amount.
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


def state(measure, spa, part) -> str:
    body = part.MainBody
    try:
        volume = f"{spa.GetMeasurable(part.CreateReferenceFromObject(body)).Volume * 1e9:.4f}"
    except Exception as error:  # noqa: BLE001
        volume = f"raises({str(error).splitlines()[0][:30]})"
    try:
        data = payload(measure.execute("catia_get_bounding_box", {}))
        core = data.get("data") if isinstance(data.get("data"), dict) else data
        box = f"{core.get('min')} .. {core.get('max')}"
    except Exception as error:  # noqa: BLE001
        box = f"raises({str(error).splitlines()[0][:40]})"
    return f"V={volume} box={box} shapes={[str(s.Name) for s in body.Shapes]}"


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sk = SketcherTools(conn)
    design = PartDesignTools(conn)
    measure = MeasurementTools(conn)

    root = os.path.join(tempfile.gettempdir(), "p0499move")
    os.makedirs(root, exist_ok=True)

    def fresh(tag: str):
        path = os.path.join(root, f"MV7_{tag}.CATPart")
        try:
            docs.execute("catia_close_document", {"document": path, "save": False})
        except Exception:  # noqa: BLE001
            pass
        if os.path.isfile(path):
            os.remove(path)
        docs.execute("catia_new_part", {"name": f"MV7_{tag}", "file_path": path})
        sk.execute("catia_create_sketch", {"plane": "xy", "name": "S"})
        sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 25, "y2": 45})
        sk.execute("catia_close_sketch", {})
        design.execute("catia_pad", {"sketch_name": "S", "height": 80})
        return conn.get_active_part()

    say("E. Selection = body, then AddNewTranslate2(5.0)")
    part = fresh("E")
    spa = conn.app.ActiveDocument.GetWorkbench("SPAWorkbench")
    part.InWorkObject = part.MainBody
    conn.hso.Clear()
    conn.hso.Add(part.MainBody)
    try:
        translate = part.ShapeFactory.AddNewTranslate2(5.0)
        say(f"  returned {translate.Name}")
    except Exception as error:  # noqa: BLE001
        say(f"  FAILED: {type(error).__name__}: {str(error).splitlines()[0][:150]}")
    try:
        part.Update()
        say("  part.Update() ok")
    except Exception as error:  # noqa: BLE001
        say(f"  part.Update() -> {str(error).splitlines()[0][:110]}")
    say("  " + state(measure, spa, part))
    conn.hso.Clear()

    say("\nF. PartDocument.Product.Position, translated +5 in x")
    part = fresh("F")
    spa = conn.app.ActiveDocument.GetWorkbench("SPAWorkbench")
    document = conn.app.ActiveDocument
    try:
        product = document.Product
        say(f"  document.Product = {product!r}")
        position = product.Position
        say(f"  Position before: {position.GetComponents()}")
        components = list(position.GetComponents())
        components[3] = components[3] + 5.0        # x翻译, in mm
        position.SetComponents(components)
        product.Position = position
        part.Update()
        say("  position written, part.Update() ok")
        say(f"  Position after: {product.Position.GetComponents()}")
    except Exception as error:  # noqa: BLE001
        say(f"  FAILED: {type(error).__name__}: {str(error).splitlines()[0][:180]}")
    say("  " + state(measure, spa, part))

    say("\nG. HybridShapeFactory.AddNewPositionTransfo(0)")
    part = fresh("G")
    spa = conn.app.ActiveDocument.GetWorkbench("SPAWorkbench")
    try:
        built = part.HybridShapeFactory.AddNewPositionTransfo(0)
        say(f"  returned {getattr(built, 'Name', built)!r}")
        try:
            part.UpdateObject(built)
            say("  UpdateObject ok")
        except Exception as error:  # noqa: BLE001
            say(f"  UpdateObject -> {str(error).splitlines()[0][:110]}")
    except Exception as error:  # noqa: BLE001
        say(f"  FAILED: {type(error).__name__}: {str(error).splitlines()[0][:180]}")
    say("  " + state(measure, spa, part))

    for name in os.listdir(root):
        if name.startswith("MV7_"):
            try:
                docs.execute("catia_close_document",
                             {"document": os.path.join(root, name), "save": False})
            except Exception:  # noqa: BLE001
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
