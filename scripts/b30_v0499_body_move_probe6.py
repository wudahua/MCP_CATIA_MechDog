"""B30 probe, part 6 of 0.3.162: driving the solid Translate feature.

AddNewTranslate2(5.0) appends a real feature to body.Shapes (part 5), but
UpdateObject failed and left the body un-tessellatable - so the feature needs
its direction before it can rebuild. Each attempt below runs in its OWN document
(a failed update leaves the body broken, and reusing it would just reproduce the
first failure), and every attempt ends by measuring volume AND box: a translate
keeps the volume and moves the box by exactly the distance.
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
        path = os.path.join(root, f"MV6_{tag}.CATPart")
        try:
            docs.execute("catia_close_document", {"document": path, "save": False})
        except Exception:  # noqa: BLE001
            pass
        if os.path.isfile(path):
            os.remove(path)
        docs.execute("catia_new_part", {"name": f"MV6_{tag}", "file_path": path})
        sk.execute("catia_create_sketch", {"plane": "xy", "name": "S"})
        sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 25, "y2": 45})
        sk.execute("catia_close_sketch", {})
        design.execute("catia_pad", {"sketch_name": "S", "height": 80})
        return conn.get_active_part()

    say("A. AddNewTranslate2(5.0), then read what it says about itself")
    part = fresh("A")
    spa = conn.app.ActiveDocument.GetWorkbench("SPAWorkbench")
    factory = part.ShapeFactory
    part.InWorkObject = part.MainBody
    translate = factory.AddNewTranslate2(5.0)
    for member in ("Distance", "Value", "Vector", "Direction", "ElemToTranslate",
                   "Name"):
        try:
            value = getattr(translate, member)
            say(f"  .{member} = {value!r}")
        except Exception as error:  # noqa: BLE001
            say(f"  .{member} -> {type(error).__name__}: {str(error).splitlines()[0][:90]}")
    try:
        part.Update()
        say("  part.Update() ok")
    except Exception as error:  # noqa: BLE001
        say(f"  part.Update() -> {type(error).__name__}: {str(error).splitlines()[0][:120]}")
    say("  " + state(measure, spa, part))

    say("\nB. set .Vector = direction, then part.Update()")
    part = fresh("B")
    spa = conn.app.ActiveDocument.GetWorkbench("SPAWorkbench")
    factory = part.ShapeFactory
    part.InWorkObject = part.MainBody
    direction = part.HybridShapeFactory.AddNewDirectionByCoord(1.0, 0.0, 0.0)
    translate = factory.AddNewTranslate2(5.0)
    for member in ("Vector", "Direction"):
        try:
            setattr(translate, member, direction)
            say(f"  set .{member} = direction: accepted")
            break
        except Exception as error:  # noqa: BLE001
            say(f"  set .{member} -> {type(error).__name__}: {str(error).splitlines()[0][:90]}")
    try:
        part.Update()
        say("  part.Update() ok")
    except Exception as error:  # noqa: BLE001
        say(f"  part.Update() -> {type(error).__name__}: {str(error).splitlines()[0][:120]}")
    say("  " + state(measure, spa, part))

    say("\nC. AddNewTranslate2 with InWorkObject = the PAD (feature), not the body")
    part = fresh("C")
    spa = conn.app.ActiveDocument.GetWorkbench("SPAWorkbench")
    factory = part.ShapeFactory
    part.InWorkObject = part.MainBody.Shapes.Item(1)
    try:
        translate = factory.AddNewTranslate2(5.0)
        say(f"  returned {translate.Name}")
    except Exception as error:  # noqa: BLE001
        say(f"  FAILED: {type(error).__name__}: {str(error).splitlines()[0][:150]}")
    try:
        part.Update()
        say("  part.Update() ok")
    except Exception as error:  # noqa: BLE001
        say(f"  part.Update() -> {type(error).__name__}: {str(error).splitlines()[0][:120]}")
    say("  " + state(measure, spa, part))

    say("\nD. AddNewAxisToAxis2(bodyRef, targetPlaneRef)")
    part = fresh("D")
    spa = conn.app.ActiveDocument.GetWorkbench("SPAWorkbench")
    factory = part.ShapeFactory
    part.InWorkObject = part.MainBody
    try:
        built = factory.AddNewAxisToAxis2(
            part.CreateReferenceFromObject(part.MainBody),
            part.CreateReferenceFromObject(
                part.OriginElements.PlaneXY))
        say(f"  returned {built.Name}")
        part.Update()
        say("  part.Update() ok")
    except Exception as error:  # noqa: BLE001
        say(f"  FAILED: {type(error).__name__}: {str(error).splitlines()[0][:180]}")
    say("  " + state(measure, spa, part))

    for name in os.listdir(root):
        if name.startswith("MV6_"):
            try:
                docs.execute("catia_close_document",
                             {"document": os.path.join(root, name), "save": False})
            except Exception:  # noqa: BLE001
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
