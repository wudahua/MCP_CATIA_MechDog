"""B30 probe, part 5 of 0.3.162: what does ShapeFactory.AddNewTranslate2 build?

Declared as AddNewTranslate2(iDistance) - one argument, and no object argument at
all, unlike GSD's AddNewTranslate(element, direction, distance). So either the
body is implicit (InWorkObject) and the direction is set on the result, or the
single argument is not a length at all. Three call shapes are tried, each in its
own fresh document, and every result is measured (volume + box) because a
translate that changes the volume is not a translate.
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


def all_members(obj) -> list[str]:
    try:
        info = obj._oleobj_.GetTypeInfo()
        count = info.GetTypeAttr().cFuncs
    except Exception as error:  # noqa: BLE001
        return [f"<no typeinfo: {error}>"]
    names = []
    for index in range(count):
        try:
            desc = info.GetFuncDesc(index)
            found = info.GetNames(desc.memid)
        except Exception:  # noqa: BLE001
            continue
        if found:
            names.append(found[0])
    return names


def payload(reply):
    return reply if isinstance(reply, (dict, list)) else json.loads(str(reply))


def box_of(measure) -> dict:
    try:
        data = payload(measure.execute("catia_get_bounding_box", {}))
    except Exception as error:  # noqa: BLE001
        return {"box": f"raises ({type(error).__name__}: {str(error).splitlines()[0][:80]})"}
    core = data.get("data") if isinstance(data.get("data"), dict) else data
    keys = ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max",
            "min", "max", "min_corner", "max_corner")
    return {key: core.get(key) for key in keys if core and key in core}


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sk = SketcherTools(conn)
    design = PartDesignTools(conn)
    measure = MeasurementTools(conn)

    root = os.path.join(tempfile.gettempdir(), "p0499move")
    os.makedirs(root, exist_ok=True)

    def fresh(tag: str) -> None:
        path = os.path.join(root, f"MOVE_{tag}.CATPart")
        try:
            docs.execute("catia_close_document", {"document": path, "save": False})
        except Exception:  # noqa: BLE001
            pass
        if os.path.isfile(path):
            os.remove(path)
        docs.execute("catia_new_part", {"name": f"MOVE_{tag}", "file_path": path})
        sk.execute("catia_create_sketch", {"plane": "xy", "name": "S"})
        sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 25, "y2": 45})
        sk.execute("catia_close_sketch", {})
        design.execute("catia_pad", {"sketch_name": "S", "height": 80})

    def report(label: str) -> None:
        part = conn.get_active_part()
        body = part.MainBody
        spa = conn.app.ActiveDocument.GetWorkbench("SPAWorkbench")
        measurable = spa.GetMeasurable(part.CreateReferenceFromObject(body))
        try:
            volume = f"{measurable.Volume * 1e9:.4f}"
        except Exception as error:  # noqa: BLE001
            volume = f"raises ({str(error).splitlines()[0][:40]})"
        say(f"  {label}: V={volume} box={json.dumps(box_of(measure))}")
        say(f"  shapes: {[str(s.Name) for s in body.Shapes]}")

    for tag, shape in (("A", "double"), ("B", "direction"), ("C", "tuple")):
        say(f"\n=== call shape {tag}: {shape}")
        fresh(f"T{tag}")
        part = conn.get_active_part()
        body = part.MainBody
        factory = part.ShapeFactory
        part.InWorkObject = body
        try:
            if tag == "A":
                built = factory.AddNewTranslate2(5.0)
            elif tag == "B":
                direction = part.HybridShapeFactory.AddNewDirectionByCoord(
                    1.0, 0.0, 0.0)
                built = factory.AddNewTranslate2(direction)
            else:
                built = factory.AddNewTranslate2((5.0, 0.0, 0.0))
            say(f"  returned: {getattr(built, 'Name', built)}")
            say(f"  members: {all_members(built)}")
            for member in ("ElemToTranslate", "Vector", "Direction", "Distance",
                           "Value", "Volume", "Object", "Reference"):
                try:
                    say(f"    .{member} = {getattr(built, member)}")
                except Exception as error:  # noqa: BLE001
                    say(f"    .{member} -> {str(error).splitlines()[0][:70]}")
            part.UpdateObject(built)
            say("  after UpdateObject:")
            report("body")
        except Exception as error:  # noqa: BLE001
            say(f"  FAILED: {type(error).__name__}: {str(error).splitlines()[0][:200]}")
            report("body (unchanged?)")

    say("\n=== AddNewRotate2(axisRef, 30) on the same block")
    fresh("TROT")
    part = conn.get_active_part()
    body = part.MainBody
    part.InWorkObject = body
    try:
        axis = part.HybridShapeFactory.AddNewLinePtPt(
            part.CreateReferenceFromObject(part.OriginElements.Origin),
            part.HybridShapeFactory.AddNewPointCoord(0.0, 0.0, 100.0))
        part.HybridBodies.Add().AppendHybridShape(axis)
        part.UpdateObject(axis)
        rotated = part.ShapeFactory.AddNewRotate2(
            part.CreateReferenceFromObject(axis), 30.0)
        say(f"  returned: {getattr(rotated, 'Name', rotated)}; "
            f"members: {all_members(rotated)}")
        part.UpdateObject(rotated)
        report("body")
    except Exception as error:  # noqa: BLE001
        say(f"  FAILED: {type(error).__name__}: {str(error).splitlines()[0][:200]}")

    for name in os.listdir(root):
        if name.startswith("MOVE_"):
            try:
                docs.execute("catia_close_document",
                             {"document": os.path.join(root, name), "save": False})
            except Exception:  # noqa: BLE001
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
