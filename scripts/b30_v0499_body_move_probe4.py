"""B30 probe, part 4 of 0.3.162: ShapeFactory's solid-side transforms.

Part 3 (with GetNames instead of GetDocumentation, which throws on this build)
found that Part.ShapeFactory - the SOLID factory - declares AddNewTranslate2,
AddNewRotate2, AddNewAxisToAxis2 and AddNewMirror. This prints their parameter
names and then tries the translate on a 25 x 45 x 80 pad, measuring volume and
box before and after.
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

WANTED = ("AddNewTranslate2", "AddNewRotate2", "AddNewAxisToAxis2",
          "AddNewMirror", "AddNewPositionTransfo", "AddNewEmptyTranslate")


def say(text: str = "") -> None:
    print(text, flush=True)


def signatures(obj, label: str) -> None:
    say(f"\n=== {label}")
    info = obj._oleobj_.GetTypeInfo()
    count = info.GetTypeAttr().cFuncs
    for index in range(count):
        try:
            desc = info.GetFuncDesc(index)
            names = info.GetNames(desc.memid)
        except Exception:  # noqa: BLE001
            continue
        if not names or names[0] not in WANTED:
            continue
        args = list(names[1:])
        say(f"  {names[0]}({', '.join(f'{i}:{a}' for i, a in enumerate(args, 1))})")


def payload(reply):
    return reply if isinstance(reply, (dict, list)) else json.loads(str(reply))


def snapshot(spa, ref, label: str) -> None:
    measurable = spa.GetMeasurable(ref)
    try:
        volume = measurable.Volume
    except Exception as error:  # noqa: BLE001
        volume = f"raises ({str(error).splitlines()[0][:40]})"
    try:
        area = measurable.Area
    except Exception as error:  # noqa: BLE001
        area = f"raises ({str(error).splitlines()[0][:40]})"
    say(f"  {label}: V={volume} A={area}")


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sk = SketcherTools(conn)
    design = PartDesignTools(conn)
    measure = MeasurementTools(conn)

    root = os.path.join(tempfile.gettempdir(), "p0499move")
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "MOVE0499D.CATPart")
    try:
        docs.execute("catia_close_document", {"document": path, "save": False})
    except Exception:  # noqa: BLE001
        pass
    if os.path.isfile(path):
        os.remove(path)

    docs.execute("catia_new_part", {"name": "MOVE0499D", "file_path": path})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "S"})
    sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 25, "y2": 45})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"sketch_name": "S", "height": 80})

    doc = conn.app.ActiveDocument
    part = conn.get_active_part()
    body = part.MainBody
    spa = doc.GetWorkbench("SPAWorkbench")

    signatures(part.ShapeFactory, "Part.ShapeFactory")
    signatures(part.HybridShapeFactory, "Part.HybridShapeFactory")

    say("\nbefore:")
    snapshot(spa, part.CreateReferenceFromObject(body), "body")
    before = payload(measure.execute("catia_get_inertia", {}))
    say(f"  inertia: {json.dumps(before.get('data') if isinstance(before.get('data'), dict) else before)[:160]}")

    factory = part.ShapeFactory
    part.InWorkObject = body
    direction = factory.AddNewDirectionByCoord(1.0, 0.0, 0.0)

    say("\nAddNewTranslate2(bodyRef, direction) - the shape the signature suggests:")
    try:
        moved = factory.AddNewTranslate2(part.CreateReferenceFromObject(body), direction)
        say(f"  returned {moved.Name}")
        part.UpdateObject(moved)
        say(f"  body.Shapes: {[str(s.Name) for s in body.Shapes]}")
        snapshot(spa, part.CreateReferenceFromObject(body), "body after")
        box = payload(measure.execute("catia_get_bounding_box", {}))
        data = box.get("data") if isinstance(box.get("data"), dict) else box
        say(f"  box: {json.dumps({k: v for k, v in (data or {}).items() if k in ('x_min','x_max','y_min','y_max','z_min','z_max','min','max')})}")
    except Exception as error:  # noqa: BLE001
        say(f"  FAILED: {type(error).__name__}: {str(error).splitlines()[0][:200]}")

    say("\nwhat a Translate2 feature exposes:")
    try:
        for shape in body.Shapes:
            say(f"  {shape.Name}")
            for member in ("Vector", "Direction", "Distance", "Value", "ElemToTranslate"):
                try:
                    say(f"    .{member} = {getattr(shape, member)}")
                except Exception:  # noqa: BLE001
                    pass
    except Exception as error:  # noqa: BLE001
        say(f"  {type(error).__name__}: {str(error).splitlines()[0][:120]}")

    docs.execute("catia_close_document", {"document": path, "save": False})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
