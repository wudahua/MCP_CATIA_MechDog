"""Same question for Translate - the sibling reports the same "source kept".

Rotate was measured first: GetCreationMode defaults to 1, SetCreationMode(0)
reads back 2, and NO mode removed the source line from the geoset. Translate
shares the SetCreationMode/GetCreationMode pair, so it gets the same treatment
rather than an assumption - two members of one family have already surprised
this project twice (Reference vs Direction argument, and the revolve angles).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection


def build(app, mode):
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
    geoset.AppendHybridShape(a)
    geoset.AppendHybridShape(b)
    line = factory.AddNewLinePtPt(part.CreateReferenceFromObject(a),
                                  part.CreateReferenceFromObject(b))
    geoset.AppendHybridShape(line)
    part.Update()

    direction = factory.AddNewDirectionByCoord(0, 0, 1)
    geoset.AppendHybridShape(direction)
    part.Update()
    translate = factory.AddNewTranslate(line, direction, 10.0)
    geoset.AppendHybridShape(translate)

    set_result = ""
    if mode is not None:
        try:
            translate.SetCreationMode(mode)
            set_result = f"SetCreationMode({mode}) ok"
        except Exception as error:  # noqa: BLE001
            set_result = f"SetCreationMode({mode}) FAILED {str(error).splitlines()[0][:60]}"
    try:
        part.UpdateObject(translate)
        update = "update ok"
    except Exception as error:  # noqa: BLE001
        update = f"update FAILED {str(error).splitlines()[0][:60]}"

    try:
        read_mode = translate.GetCreationMode()
    except Exception as error:  # noqa: BLE001
        read_mode = f"unreadable: {str(error).splitlines()[0][:50]}"
    names = [str(geoset.HybridShapes.Item(i).Name)
             for i in range(1, geoset.HybridShapes.Count + 1)]
    moved = None
    try:
        moved = (translate.ElemToTranslate.Name, float(translate.DistanceValue))
    except Exception as error:  # noqa: BLE001
        moved = f"unreadable: {str(error).splitlines()[0][:50]}"
    return {"set": set_result, "update": update, "mode_read": read_mode,
            "shapes": names, "elem_to_translate": moved,
            "line_in_tree": "直线.1" in names}


def main() -> None:
    conn = CATIAConnection()
    conn.connect()
    app = conn.app
    app.DisplayFileAlerts = False
    for mode in (None, 0, 1):
        try:
            facts = build(app, mode)
        except Exception as error:  # noqa: BLE001
            print(f"translate mode={mode}: FAILED {str(error).splitlines()[0][:90]}",
                  flush=True)
            continue
        print(f"\ntranslate mode={mode}", flush=True)
        for key in ("set", "update", "mode_read", "elem_to_translate", "line_in_tree"):
            print(f"   {key}: {facts[key]}", flush=True)
        print(f"   shapes: {facts['shapes']}", flush=True)

    for index in range(app.Documents.Count, 0, -1):
        try:
            app.Documents.Item(index).Close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
