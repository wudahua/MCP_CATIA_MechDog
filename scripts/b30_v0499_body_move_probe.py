"""B30 probe for 0.3.162 - can a SOLID body be moved at all?

The 2026-11-23 report (P1-4) asked for `catia_move_body(dx, dy, dz)`: "with
only this, the 5 mm error would have cost one call instead of a full rebuild".
V5 has no `Body.Translate`, and GSD's translate takes curves and surfaces, so
before promising a tool the routes are tried, in order, on a 25 x 45 x 80 pad:

  A  typeinfo: do `Body` / `Shape` / `Part` declare any move/transform member?
  B  GSD `AddNewTranslate` on a Reference to the BODY
  C  GSD `AddNewAxisToAxis` on the same reference
  D  Selection + StartCommand("Translate")   -- LAST, and guarded: a command left
     running belongs to the operator, so this only reports whether it returns

Every step prints the measured volume and box, because a "move" that quietly
changes the volume is not a move.
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


def members(obj, keywords: tuple[str, ...]) -> list[str]:
    """Typeinfo member names matching any keyword, or the reason it failed."""
    try:
        info = obj._oleobj_.GetTypeInfo()
        count = info.GetTypeAttr().cFuncs
    except Exception as error:  # noqa: BLE001
        return [f"<typeinfo failed: {type(error).__name__}: {error}>"]
    found = []
    for index in range(count):
        try:
            name = info.GetFuncDesc(index).GetDocumentation(-1)[0]
        except Exception:  # noqa: BLE001
            continue
        low = name.lower()
        if any(word in low for word in keywords):
            found.append(name)
    return found


def box(measure) -> dict:
    data = payload(measure.execute("catia_get_bounding_box", {}))
    return data.get("data") if isinstance(data.get("data"), dict) else data


def volume(measure) -> float | None:
    data = payload(measure.execute("catia_get_inertia", {}))
    values = data.get("data") if isinstance(data.get("data"), dict) else data
    value = values.get("volume_mm3")
    return None if value is None else float(value)


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sk = SketcherTools(conn)
    design = PartDesignTools(conn)
    measure = MeasurementTools(conn)

    root = os.path.join(tempfile.gettempdir(), "p0499move")
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "MOVE0499.CATPart")
    try:
        docs.execute("catia_close_document", {"document": path, "save": False})
    except Exception:  # noqa: BLE001
        pass
    if os.path.isfile(path):
        os.remove(path)

    docs.execute("catia_new_part", {"name": "MOVE0499", "file_path": path})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "S"})
    sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 25, "y2": 45})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"sketch_name": "S", "height": 80})
    docs.execute("catia_save_document", {})

    part = conn.get_active_part()
    body = part.MainBody
    start_box = box(measure)
    start_volume = volume(measure)
    say(f"start: V={start_volume} box={json.dumps(start_box)[:220]}")

    say("\nA. typeinfo members mentioning move/transform/position")
    for label, obj in (("Part", part), ("Body", body),
                       ("Shape(0)", body.Shapes.Item(1))):
        found = members(obj, ("move", "trans", "posit", "matrix", "shift", "axis"))
        say(f"  {label}: {found}")

    factory = part.HybridShapeFactory
    say("\nB. AddNewTranslate(bodyRef, direction, 5)")
    try:
        body_ref = part.CreateReferenceFromObject(body)
        direction = factory.AddNewDirectionByCoord(1.0, 0.0, 0.0)
        geoset = part.HybridBodies.Add()
        geoset.Name = "MOVE_PROBE"
        translate = factory.AddNewTranslate(body_ref, direction, 5.0)
        geoset.AppendHybridShape(translate)
        part.UpdateObject(translate)
        say(f"  built '{translate.Name}'; part.UpdateObject returned")
        try:
            measure_ref = part.GetMeasurable(
                part.CreateReferenceFromObject(translate))
            say(f"  translate feature: V={measure_ref.Volume:.4f}")
        except Exception as error:  # noqa: BLE001
            say(f"  translate feature measurable: {type(error).__name__}: "
                f"{str(error).splitlines()[0][:120]}")
        say(f"  part box after: {json.dumps(box(measure))[:220]}")
        say(f"  part V after: {volume(measure)}")
        # And what the body itself measures - a surface translate would leave
        # the solid where it was.
        try:
            body_measurable = part.GetMeasurable(body_ref)
            say(f"  body itself: V={body_measurable.Volume:.4f}")
            say(f"  body bbox member: {getattr(body_measurable, 'GetBox', '<none>')}")
        except Exception as error:  # noqa: BLE001
            say(f"  body measurable: {type(error).__name__}: "
                f"{str(error).splitlines()[0][:120]}")
    except Exception as error:  # noqa: BLE001
        say(f"  FAILED: {type(error).__name__}: {str(error).splitlines()[0][:200]}")

    say("\nC. AddNewAxisToAxis(bodyRef, axisRef)")
    try:
        axis_origin = part.OriginElements
        try:
            axis_ref = part.CreateReferenceFromObject(axis_origin.PlaneXY)
            axis2 = factory.AddNewAxisToAxis(body_ref, axis_ref)
            say(f"  AddNewAxisToAxis accepted: {axis2.Name}")
        except Exception as error:  # noqa: BLE001
            say(f"  AddNewAxisToAxis: {type(error).__name__}: "
                f"{str(error).splitlines()[0][:160]}")
    except Exception as error:  # noqa: BLE001
        say(f"  OriginElements: {type(error).__name__}: "
            f"{str(error).splitlines()[0][:120]}")

    say("\nD. does the tree still hold the original body, and did anything move?")
    say(f"  bodies: {[str(body_2.Name) for body_2 in part.Bodies]}")
    say(f"  geosets: {[str(set_2.Name) for set_2 in part.HybridBodies]}")
    say(f"  final box: {json.dumps(box(measure))[:220]}")
    say(f"  final V: {volume(measure)}")

    docs.execute("catia_close_document", {"document": path, "save": False})
    say("\ndocument closed, nothing saved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
