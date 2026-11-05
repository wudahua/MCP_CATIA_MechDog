"""Is there an explicit copy-mode on Rotate / Translate? (0.3.60 suggestion, still open)

0.3.60's `catia_gsd_rotate` / `catia_gsd_translate` report that the source
element is KEPT, which is what the automation default did. The 0.3.60
acceptance report asked whether the type library exposes that as a property
rather than leaving it to a default - three releases later it is still
unanswered. This enumerates the real members instead of guessing names.
"""

from __future__ import annotations

import sys

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.gsd import GSDTools


def members(obj) -> list[str]:
    out = []
    type_info = obj._oleobj_.GetTypeInfo()
    attr = type_info.GetTypeAttr()
    for i in range(attr.cFuncs):
        try:
            out.append(type_info.GetNames(type_info.GetFuncDesc(i).memid)[0])
        except Exception:  # noqa: BLE001
            pass
    for i in range(attr.cVars):
        try:
            out.append(type_info.GetNames(type_info.GetVarDesc(i).memid)[0])
        except Exception:  # noqa: BLE001
            pass
    return out


def main() -> None:
    conn = CATIAConnection()
    conn.connect()
    conn.app.DisplayFileAlerts = False
    for index in range(conn.documents.Count, 0, -1):
        try:
            conn.documents.Item(index).Close()
        except Exception:  # noqa: BLE001
            pass

    conn.documents.Add("Part")
    part = conn.active_document.Part
    body = part.MainBody
    factory = part.HybridShapeFactory
    hybrid_bodies = part.HybridBodies
    try:
        geoset = hybrid_bodies.Item("Geometry")
    except Exception:  # noqa: BLE001
        geoset = hybrid_bodies.Add()
        geoset.Name = "Geometry"

    point = factory.AddNewPointCoord(20, 0, 0)
    geoset.AppendHybridShape(point)
    point2 = factory.AddNewPointCoord(20, 0, 30)
    geoset.AppendHybridShape(point2)
    line = factory.AddNewLinePtPt(part.CreateReferenceFromObject(point),
                                  part.CreateReferenceFromObject(point2))
    geoset.AppendHybridShape(line)
    axle_a = factory.AddNewPointCoord(0, 0, 0)
    axle_b = factory.AddNewPointCoord(0, 0, 100)
    geoset.AppendHybridShape(axle_a)
    geoset.AppendHybridShape(axle_b)
    axis = factory.AddNewLinePtPt(part.CreateReferenceFromObject(axle_a),
                                  part.CreateReferenceFromObject(axle_b))
    geoset.AppendHybridShape(axis)
    part.Update()

    rotated = factory.AddNewRotate(line, part.CreateReferenceFromObject(axis), 30.0)
    geoset.AppendHybridShape(rotated)
    direction = factory.AddNewDirectionByCoord(0, 0, 1)
    geoset.AppendHybridShape(direction)
    translated = factory.AddNewTranslate(line, direction, 10.0)
    geoset.AppendHybridShape(translated)
    part.Update()

    for label, shape in (("Rotate", rotated), ("Translate", translated)):
        names = members(shape)
        print(f"\n=== {label} ({len(names)} members) ===", flush=True)
        print(names, flush=True)
        interesting = [n for n in names
                       if any(word in n.lower()
                              for word in ("copy", "keep", "dup", "mode",
                                           "creation", "original", "output"))]
        print(f"  copy-ish: {interesting}", flush=True)
        for name in interesting:
            try:
                print(f"    {name} = {getattr(shape, name)!r}", flush=True)
            except Exception as error:  # noqa: BLE001
                print(f"    {name} unreadable: {str(error).splitlines()[0][:60]}",
                      flush=True)

    # And the observable answer: does the source still exist after both?
    print("\n=== source survives? ===", flush=True)
    print(f"  geoset holds {geoset.HybridShapes.Count} shapes", flush=True)
    for index in range(1, geoset.HybridShapes.Count + 1):
        shape = geoset.HybridShapes.Item(index)
        print(f"    {index}: {shape.Name}", flush=True)
    print(f"  line still addressable: {line.Name!r}", flush=True)

    for index in range(conn.documents.Count, 0, -1):
        try:
            conn.documents.Item(index).Close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
