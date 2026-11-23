"""B30 probe, part 2 of 0.3.162: WHAT is `AddNewTranslate(bodyRef, ...)`?

Part 1 showed the call is accepted and `平移.1` appears, while the part's own
volume stays 90000. Three possibilities, and only measurement separates them:

  1. a translated SOLID copy (volume 90000)  -> a move may be buildable on it
  2. a translated SURFACE copy (volume 0/raises)
  3. a feature that points at nothing

Measured here on a 25 x 45 x 80 pad with SPAWorkbench, plus the two things that
would make a move out of it: boolean against the body, and InsertHybridShape
into the body followed by a rebuild.
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


def probe_measures(spa, ref, label: str) -> None:
    try:
        measurable = spa.GetMeasurable(ref)
    except Exception as error:  # noqa: BLE001
        say(f"  {label}: GetMeasurable -> {type(error).__name__}: "
            f"{str(error).splitlines()[0][:110]}")
        return
    for name in ("Volume", "Area", "Perimeter", "Length"):
        try:
            say(f"  {label}.{name} = {getattr(measurable, name):.6f}")
        except Exception as error:  # noqa: BLE001
            say(f"  {label}.{name} -> {type(error).__name__}: "
                f"{str(error).splitlines()[0][:90]}")


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sk = SketcherTools(conn)
    design = PartDesignTools(conn)
    measure = MeasurementTools(conn)

    root = os.path.join(tempfile.gettempdir(), "p0499move")
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "MOVE0499B.CATPart")
    try:
        docs.execute("catia_close_document", {"document": path, "save": False})
    except Exception:  # noqa: BLE001
        pass
    if os.path.isfile(path):
        os.remove(path)

    docs.execute("catia_new_part", {"name": "MOVE0499B", "file_path": path})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "S"})
    sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 25, "y2": 45})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"sketch_name": "S", "height": 80})

    doc = conn.app.ActiveDocument
    part = conn.get_active_part()
    body = part.MainBody
    spa = doc.GetWorkbench("SPAWorkbench")
    body_ref = part.CreateReferenceFromObject(body)

    say("body itself:")
    probe_measures(spa, body_ref, "body")

    factory = part.HybridShapeFactory
    direction = factory.AddNewDirectionByCoord(1.0, 0.0, 0.0)
    geoset = part.HybridBodies.Add()
    geoset.Name = "MOVE_PROBE"
    translate = factory.AddNewTranslate(body_ref, direction, 5.0)
    geoset.AppendHybridShape(translate)
    part.UpdateObject(translate)
    translate_ref = part.CreateReferenceFromObject(translate)

    say(f"\n'{translate.Name}' (AddNewTranslate of the body):")
    probe_measures(spa, translate_ref, "translate")

    say("\n1. boolean Add(body, translate)?")
    try:
        part.InWorkObject = body
        added = part.ShapeFactory.AddNewAdd(body_ref, translate_ref)
        part.UpdateObject(added)
        say(f"  AddNewAdd accepted: {added.Name}")
        probe_measures(spa, part.CreateReferenceFromObject(added), "added")
    except Exception as error:  # noqa: BLE001
        say(f"  AddNewAdd: {type(error).__name__}: {str(error).splitlines()[0][:160]}")

    say("\n2. InsertHybridShape(translate) into the body, then rebuild")
    try:
        body.InsertHybridShape(translate)
        part.Update()
        say(f"  body volume after insert+Update: "
            f"{spa.GetMeasurable(body_ref).Volume:.6f}")
        say(f"  body.Shapes: {[str(s.Name) for s in body.Shapes]}")
    except Exception as error:  # noqa: BLE001
        say(f"  InsertHybridShape: {type(error).__name__}: "
            f"{str(error).splitlines()[0][:160]}")

    say("\n3. what does the reference resolve to?")
    for label, obj in (("translate", translate),):
        for member in ("ElemToTranslate", "Direction", "Distance", "Name"):
            try:
                say(f"  {label}.{member} = {getattr(obj, member)}")
            except Exception as error:  # noqa: BLE001
                say(f"  {label}.{member} -> {type(error).__name__}: "
                    f"{str(error).splitlines()[0][:90]}")

    say("\nfinal box: " + json.dumps(
        (payload(measure.execute("catia_get_bounding_box", {}))).get("data"))[:200])
    docs.execute("catia_close_document", {"document": path, "save": False})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
