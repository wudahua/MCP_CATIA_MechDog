"""0.3.87 probe, part 2: `HybridShapePlaneAngle` has GetFirstAxis / GetSecondAxis /
GetOrigin in its member table. This asks the type library for their real
signatures and then calls them, so the frame is either read or proven unreadable.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402

ROOT = r"F:\catia_probe_v0387"


def say(line: str = "") -> None:
    print(line, flush=True)


def signatures(obj, wanted: set[str]) -> None:
    """memid -> (name, how many arguments) straight from the type library."""
    info = obj._oleobj_.GetTypeInfo()
    count = info.GetTypeAttr().cFuncs
    for i in range(count):
        desc = info.GetFuncDesc(i)
        names = info.GetNames(desc.memid)
        if not names:
            continue
        name = str(names[0])
        if name in wanted:
            say(f"    {name:26s} args={len(names) - 1} invkind={desc.invkind} names={names}")


def components(obj, label: str) -> None:
    """Whatever a direction object answers about itself."""
    say(f"  {label}: {type(obj).__name__}")
    for name in ("GetX", "GetY", "GetZ", "X", "Y", "Z", "Direction", "Value"):
        try:
            value = getattr(obj, name)
            if callable(value):
                value = value()
            say(f"    {name:10s} -> {value}")
        except Exception as error:  # noqa: BLE001
            say(f"    {name:10s} -> {str(error).splitlines()[0][:70]}")


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    for i in range(conn.documents.Count, 0, -1):
        doc = conn.documents.Item(i)
        try:
            full = os.path.normcase(os.path.abspath(str(doc.FullName)))
        except Exception:  # noqa: BLE001
            continue
        if full.startswith(os.path.normcase(os.path.abspath(ROOT))):
            doc.Close()

    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    path = os.path.join(ROOT, "frame2.CATPart")
    if os.path.isfile(path):
        os.remove(path)
    document.execute("catia_new_part", {"name": "FRAME87B", "file_path": path})
    part = conn.get_active_part()

    gsd.execute("catia_gsd_plane_angle",
                {"plane": "zx", "axis": "z", "angle": 72, "name": "P72"})
    gsd.execute("catia_gsd_plane_angle",
                {"plane": "xy", "axis": "x", "angle": 90, "name": "PX90"})
    plane = part.FindObjectByName("P72")
    say(f"== signatures on {plane.Name} ==")
    signatures(plane, {"GetFirstAxis", "GetSecondAxis", "GetOrigin", "GetPosition",
                       "Angle", "Orientation", "RevolAxis", "Plane", "Thickness"})

    say("")
    say("== call them ==")
    for name in ("GetFirstAxis", "GetSecondAxis", "GetPosition", "GetOrigin"):
        try:
            value = getattr(plane, name)()
            say(f"  {name}() -> {type(value).__name__}: {value}")
            if value is not None and not isinstance(value, (int, float, str)):
                components(value, name)
        except Exception as error:  # noqa: BLE001
            say(f"  {name}() -> {str(error).splitlines()[0][:110]}")

    say("")
    say("== the same plane's measured normal, for comparison ==")
    try:
        spa = conn.active_document.GetWorkbench("SPAWorkbench")
        measurable = spa.GetMeasurable(part.CreateReferenceFromObject(plane))
        say(f"  measurable: {measurable}")
        for name in ("GetPlane", "Area", "Volume", "Length"):
            try:
                value = getattr(measurable, name)
                if callable(value):
                    value = value()
                say(f"    {name:10s} -> {value}")
            except Exception as error:  # noqa: BLE001
                say(f"    {name:10s} -> {str(error).splitlines()[0][:70]}")
    except Exception as error:  # noqa: BLE001
        say(f"  measurable failed: {str(error).splitlines()[0][:90]}")

    document.execute("catia_close_document", {"save": False})
    say("done")


if __name__ == "__main__":
    main()
