"""What does CreationMode DO on Rotate / Translate?

The 0.3.60 report asked whether "the source element is kept" is a property we
could set explicitly instead of relying on the automation default. The type
library answers half of it: both interfaces expose
``SetCreationMode`` / ``GetCreationMode``. This measures the values and the
observable effect, per mode, on a fresh part each time - the same rule the
0.3.49 performance work taught: one condition per CATIA process.

Mode is read back AFTER UpdateObject, and the source line's survival is read
from the geoset's own list, not from the Python object (a consumed element can
still answer .Name while being gone from the tree).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection


def build(app, mode):
    """One part, one line, one rotate at `mode`; returns a dict of facts."""
    documents = app.Documents
    for index in range(documents.Count, 0, -1):
        try:
            documents.Item(index).Close()
        except Exception:  # noqa: BLE001
            pass
    documents.Add("Part")
    part = app.ActiveDocument.Part
    factory = part.HybridShapeFactory
    geoset = part.HybridBodies.Add()
    geoset.Name = "Geometry"

    a = factory.AddNewPointCoord(20, 0, 0)
    b = factory.AddNewPointCoord(20, 0, 30)
    axle_a = factory.AddNewPointCoord(0, 0, 0)
    axle_b = factory.AddNewPointCoord(0, 0, 100)
    for shape in (a, b, axle_a, axle_b):
        geoset.AppendHybridShape(shape)
    line = factory.AddNewLinePtPt(part.CreateReferenceFromObject(a),
                                  part.CreateReferenceFromObject(b))
    geoset.AppendHybridShape(line)
    axis = factory.AddNewLinePtPt(part.CreateReferenceFromObject(axle_a),
                                  part.CreateReferenceFromObject(axle_b))
    geoset.AppendHybridShape(axis)
    part.Update()

    rotate = factory.AddNewRotate(line, part.CreateReferenceFromObject(axis), 30.0)
    geoset.AppendHybridShape(rotate)
    set_result = ""
    if mode is not None:
        try:
            rotate.SetCreationMode(mode)
            set_result = f"SetCreationMode({mode}) ok"
        except Exception as error:  # noqa: BLE001
            set_result = f"SetCreationMode({mode}) FAILED {str(error).splitlines()[0][:60]}"
    try:
        part.UpdateObject(rotate)
        update = "update ok"
    except Exception as error:  # noqa: BLE001
        update = f"update FAILED {str(error).splitlines()[0][:60]}"

    try:
        read_mode = rotate.GetCreationMode()
    except Exception as error:  # noqa: BLE001
        read_mode = f"unreadable: {str(error).splitlines()[0][:50]}"
    names = [str(geoset.HybridShapes.Item(i).Name)
             for i in range(1, geoset.HybridShapes.Count + 1)]
    try:
        still = str(rotate.ElemToRotate.Name)
    except Exception as error:  # noqa: BLE001
        still = f"unreadable: {str(error).splitlines()[0][:50]}"

    return {"mode_set": mode, "set": set_result, "update": update,
            "mode_read": read_mode, "shapes": names,
            "elem_to_rotate": still, "line_in_tree": "直线.1" in names}


def main() -> None:
    conn = CATIAConnection()
    conn.connect()
    app = conn.app
    app.DisplayFileAlerts = False
    for mode in (None, 0, 1, 2):
        try:
            facts = build(app, mode)
        except Exception as error:  # noqa: BLE001
            print(f"mode={mode}: build FAILED {str(error).splitlines()[0][:80]}",
                  flush=True)
            continue
        print(f"\nmode={mode}", flush=True)
        for key in ("set", "update", "mode_read", "elem_to_rotate", "line_in_tree"):
            print(f"   {key}: {facts[key]}", flush=True)
        print(f"   shapes: {facts['shapes']}", flush=True)

    for index in range(app.Documents.Count, 0, -1):
        try:
            app.Documents.Item(index).Close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
