"""Do the now-known signatures actually build anything?

b30_v0353_signatures.py read the real parameter lists off the typelib:

    AddNewFilletBiTangent(el1, el2, radius, o1, o2, trimMode, relimitation)
    AddNewFilletTriTangent(el1, el2, removeEl, o1, o2, removeOrient, trimMode,
                           relimitation)
    AddNewSpine()                    - no arguments at all
    AddNewBoundaryOfSurface(surface)

A declared signature is still not a working call. This tries each one on real
geometry and only reports what CATIA accepted AND updated.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.measurement import MeasurementTools

OUT = os.path.join(tempfile.gettempdir(), "b30_v0353", "SIG2.CATPart")


def say(text: str = "") -> None:
    print(text, flush=True)


def main() -> None:
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)
    document.execute("catia_new_part", {"name": "SIG2", "file_path": OUT})
    assert str(conn.active_document.Name) == "SIG2.CATPart", conn.active_document.Name
    part = conn.get_active_part()
    factory = gsd._factory()
    geoset = gsd._get_geoset()

    # Two parallel cylinders 30 apart (r=10). A bi-tangent fillet with a small
    # radius should be able to roll between them.
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": 10.0, "name": "S_C1"})
    gsd.execute("catia_gsd_extrude", {"profile": "S_C1", "direction": [0, 0, 1],
                                      "limit1": 30.0, "name": "S_S1"})
    gsd.execute("catia_gsd_circle", {"center": [30, 0, 0], "support_plane": "xy",
                                     "radius": 10.0, "name": "S_C2"})
    gsd.execute("catia_gsd_extrude", {"profile": "S_C2", "direction": [0, 0, 1],
                                      "limit1": 30.0, "name": "S_S2"})
    ref1 = part.CreateReferenceFromObject(part.FindObjectByName("S_S1"))
    ref2 = part.CreateReferenceFromObject(part.FindObjectByName("S_S2"))

    say("── fillet (BiTangent), 7 args as declared ──")
    for radius in (1.0, 2.0, 4.0):
        for o1, o2 in ((1, 1), (-1, 1), (1, -1)):
            try:
                fillet = factory.AddNewFilletBiTangent(ref1, ref2, radius,
                                                       o1, o2, 0, 0)
                geoset.AppendHybridShape(fillet)
                fillet.Name = f"S_F{radius}_{o1}_{o2}".replace(".", "_")
                part.UpdateObject(fillet)
                area = None
                try:
                    area = round(float(measurement.conn.app.ActiveDocument
                                       .GetWorkbench("SPAWorkbench")
                                       .GetMeasurable(
                                           part.CreateReferenceFromObject(fillet))
                                       .Area), 2)
                except Exception:  # noqa: BLE001
                    pass
                say(f"  r={radius} o={o1},{o2}: built + updated, area {area}")
                break
            except Exception as error:  # noqa: BLE001
                say(f"  r={radius} o={o1},{o2}: {str(error).splitlines()[0][:90]}")

    say("\n── tri-tangent, 8 args as declared (needs a third element) ──")
    try:
        tri = factory.AddNewFilletTriTangent(ref1, ref2, ref1, 1, -1, 1, 0, 0)
        geoset.AppendHybridShape(tri)
        part.UpdateObject(tri)
        say("  built + updated")
    except Exception as error:  # noqa: BLE001
        say(f"  {str(error).splitlines()[0][:120]}")

    say("\n── boundary of surface ──")
    try:
        boundary = factory.AddNewBoundaryOfSurface(ref1)
        geoset.AppendHybridShape(boundary)
        boundary.Name = "S_BND"
        part.UpdateObject(boundary)
        measurable = (conn.active_document.GetWorkbench("SPAWorkbench")
                      .GetMeasurable(part.CreateReferenceFromObject(boundary)))
        say(f"  built + updated; boundary length {round(float(measurable.Length), 3)} mm "
            f"(two circles r=10 = {round(2 * 2 * 3.141592653589793 * 10, 3)})")
    except Exception as error:  # noqa: BLE001
        say(f"  {str(error).splitlines()[0][:120]}")

    say("\n── spine: 0 args, then sections ──")
    try:
        spine = factory.AddNewSpine()
        method_names = [n for n in dir(spine) if "ection" in n or "Add" in n]
        say(f"  created; add-looking methods: {method_names}")
    except Exception as error:  # noqa: BLE001
        say(f"  create failed: {str(error).splitlines()[0][:90]}")

    for index in range(conn.documents.Count, 0, -1):
        try:
            if str(conn.documents.Item(index).Name).startswith("SIG2"):
                conn.documents.Item(index).Activate()
                document.execute("catia_close_document", {"save": False})
        except Exception:  # noqa: BLE001
            pass
    say(f"\ndocuments open now: {conn.documents.Count}")


if __name__ == "__main__":
    main()
