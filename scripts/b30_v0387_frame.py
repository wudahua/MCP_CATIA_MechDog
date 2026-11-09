"""0.3.87 probe: can a datum plane's frame be READ, or only reconstructed?

The 2026-11-09 report needs a sketch drawn on a 36/72-degree plane to have its
2D coordinates turned into global XYZ. `Sketch.GetAbsoluteAxisData` is blocked
(by-ref array, pywin32 cannot supply one), so the question is whether the PLANE
it sits on exposes its own axes instead. This dumps the member table of the
object `AddNewPlaneAngle` returns and then calls every plausible getter, so the
answer is a list of what worked rather than a guess.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0387"


def say(line: str = "") -> None:
    print(line, flush=True)


def members(obj, label: str) -> list[str]:
    """Every name the type library declares for this object."""
    try:
        info = obj._oleobj_.GetTypeInfo()
        count = info.GetTypeAttr().cFuncs
        names = [str(info.GetNames(info.GetFuncDesc(i).memid)[0])
                 for i in range(count)]
    except Exception as error:  # noqa: BLE001
        say(f"  {label}: typeinfo failed: {str(error).splitlines()[0][:80]}")
        return []
    say(f"  {label}: {len(names)} member(s)")
    say("    " + ", ".join(sorted(set(names))[:80]))
    return names


def try_call(obj, name: str) -> None:
    try:
        value = getattr(obj, name)
    except Exception as error:  # noqa: BLE001
        say(f"    {name:22s} -> {str(error).splitlines()[0][:90]}")
        return
    if callable(value):
        try:
            value = value()
        except Exception as error:  # noqa: BLE001
            say(f"    {name:22s} -> call failed: {str(error).splitlines()[0][:70]}")
            return
    say(f"    {name:22s} -> {type(value).__name__}: {value}")


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
    sketcher = SketcherTools(conn)

    path = os.path.join(ROOT, "frame.CATPart")
    if os.path.isfile(path):
        os.remove(path)
    document.execute("catia_new_part", {"name": "FRAME87", "file_path": path})
    part = conn.get_active_part()

    gsd.execute("catia_gsd_plane_angle",
                {"plane": "zx", "axis": "z", "angle": 72, "name": "P72"})
    plane = part.FindObjectByName("P72")
    say(f"== plane object: {plane.Name} ({type(plane).__name__}) ==")
    members(plane, "PlaneAngle")

    say("== getters ==")
    for name in ("Angle", "Orientation", "Plane", "Axis", "FirstAxis",
                 "SecondAxis", "Origin", "GetOrigin", "GetAxes", "GetAxis",
                 "Direction", "Support", "Element"):
        try_call(plane, name)

    say("")
    say("== what the reference plane looks like ==")
    try:
        ref = part.OriginElements.PlaneZX
        members(ref, "OriginElements.PlaneZX")
    except Exception as error:  # noqa: BLE001
        say(f"  PlaneZX: {str(error).splitlines()[0][:80]}")

    say("")
    say("== a sketch on P72, and what can be read back ==")
    sketcher.execute("catia_create_sketch", {"plane": "P72"})
    sketcher.execute("catia_sketch_circle", {"cx": 6, "cy": 0, "radius": 2})
    sketcher.execute("catia_close_sketch", {})
    sketch = part.FindObjectByName("草图.1")
    members(sketch, "Sketch")
    say("  getters:")
    for name in ("AbsoluteAxis", "Support", "GetAbsoluteAxisData",
                 "GetAxisData", "Origin", "H", "V"):
        try_call(sketch, name)
    say("")
    say("== the sketch's support, and its typeinfo ==")
    try:
        support = sketch.Support
        say(f"  support = {support.Name if hasattr(support, 'Name') else support}")
        members(support, "Support")
    except Exception as error:  # noqa: BLE001
        say(f"  support: {str(error).splitlines()[0][:90]}")

    document.execute("catia_close_document", {"save": False})
    say("done")


if __name__ == "__main__":
    main()
