"""The textbook surface-fillet case: two walls meeting at 90 degrees.

The first attempt (two parallel cylinders) failed at UpdateObject for all nine
orientation/radius combinations. A rolling-ball fillet between two DISJOINT
surfaces may simply be an invalid arrangement, which would make that a bad test
rather than a verdict. This is the arrangement the CATIA UI's Edge Fillet is
used on every day: two extruded walls sharing an edge along Z.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools

OUT = os.path.join(tempfile.gettempdir(), "b30_v0353", "CORNER.CATPart")


def say(text: str = "") -> None:
    print(text, flush=True)


def main() -> None:
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    document.execute("catia_new_part", {"name": "CORNER", "file_path": OUT})
    assert str(conn.active_document.Name) == "CORNER.CATPart", conn.active_document.Name
    part = conn.get_active_part()
    factory = gsd._factory()
    geoset = gsd._get_geoset()

    # wall A: the XZ plane patch (y=0), wall B: the YZ plane patch (x=0).
    # They share the Z axis, so a 5 mm rolling ball fits in the corner.
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [60, 0, 0],
                                   "name": "W_LA"})
    gsd.execute("catia_gsd_extrude", {"profile": "W_LA", "direction": [0, 0, 1],
                                      "limit1": 40.0, "name": "W_A"})
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [0, 60, 0],
                                   "name": "W_LB"})
    gsd.execute("catia_gsd_extrude", {"profile": "W_LB", "direction": [0, 0, 1],
                                      "limit1": 40.0, "name": "W_B"})
    ref_a = part.CreateReferenceFromObject(part.FindObjectByName("W_A"))
    ref_b = part.CreateReferenceFromObject(part.FindObjectByName("W_B"))

    say("── AddNewFilletBiTangent(el1, el2, radius, o1, o2, trimMode, relim) ──")
    built = 0
    for radius in (2.0, 5.0):
        for o1, o2 in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
            for trim in (0, 1):
                try:
                    fillet = factory.AddNewFilletBiTangent(ref_a, ref_b, radius,
                                                           o1, o2, trim, 0)
                    geoset.AppendHybridShape(fillet)
                    fillet.Name = f"W_F_{int(radius * 10)}_{o1}_{o2}_{trim}"
                    part.UpdateObject(fillet)
                    area = None
                    try:
                        measurable = (conn.active_document.GetWorkbench("SPAWorkbench")
                                      .GetMeasurable(
                                          part.CreateReferenceFromObject(fillet)))
                        area = round(float(measurable.Area), 3)
                    except Exception:  # noqa: BLE001
                        pass
                    say(f"  r={radius} o={o1},{o2} trim={trim}: BUILT, area {area}")
                    built += 1
                except Exception as error:  # noqa: BLE001
                    say(f"  r={radius} o={o1},{o2} trim={trim}: "
                        f"{str(error).splitlines()[0][:80]}")
    say(f"\n{built} of 16 combinations updated")

    for index in range(conn.documents.Count, 0, -1):
        try:
            if str(conn.documents.Item(index).Name).startswith("CORNER"):
                conn.documents.Item(index).Activate()
                document.execute("catia_close_document", {"save": False})
        except Exception:  # noqa: BLE001
            pass
    say(f"documents open now: {conn.documents.Count}")


if __name__ == "__main__":
    main()
