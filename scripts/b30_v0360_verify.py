"""0.3.60 on B30: the three fixes that were only ever offline so far.

  A. a closed spline whose last point repeats the first - refused by CATIA
     before, built now, and the reply has to say what it dropped;
  B. the wall thickness the caller used to compute by hand - checked against a
     part whose answer is already known, the 0.3 mm bottle base delivered on
     2026-11-04 (volume 10099.5 mm3, area 67392.8 mm2 -> 2V/A = 0.2997 mm);
  C. a mesh export of an empty part - CATIA answers with a modal dialog that
     blocks the caller, so the tool must refuse it BEFORE the call. If this
     guard is wrong the script hangs, which is itself the result.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.export import ExportTools
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.measurement import MeasurementTools

say = lambda text: print(text, flush=True)  # noqa: E731


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)
    export = ExportTools(conn)

    # ── B: the known part, if it is open
    for index in range(1, conn.documents.Count + 1):
        if conn.documents.Item(index).Name.startswith("ColaBottle_PetalBase"):
            conn.documents.Item(index).Activate()
            payload = measurement.execute("catia_get_inertia", {})
            say("B. wall thickness on the delivered bottle base")
            for line in str(payload).splitlines():
                if "thickness" in line or "volume_mm3" in line or "area_mm2" in line:
                    say("   " + line.strip())
            break

    # ── A and C: a fresh part
    docs.execute("catia_new_part", {})

    # C FIRST, on a part with nothing in it at all: that is the case the guard
    # is for, and testing it after building the spline answers a different
    # question (a curve-only part, which this run needs to know about too).
    say("C1. mesh export of an EMPTY part")
    try:
        export.execute("catia_export", {"file_path": r"C:\Temp\v0360_empty.stl",
                                        "format": "stl"})
        say("   EXPORTED - the guard did not fire")
    except Exception as error:  # noqa: BLE001
        say(f"   refused: {str(error).splitlines()[0][:200]}")

    say("A. closed spline with a repeated first point")
    ring = [[0, 0, 0], [20, 0, 0], [20, 12, 0], [0, 12, 0], [0, 0, 0]]
    try:
        say("   " + gsd.execute("catia_gsd_spline",
                                {"points": ring, "closed": True, "name": "RING"}))
    except Exception as error:  # noqa: BLE001
        say(f"   REFUSED: {str(error).splitlines()[0][:160]}")

    say("C2. mesh export of a part holding ONE CURVE")
    try:
        export.execute("catia_export", {"file_path": r"C:\Temp\v0360_curve.stl",
                                        "format": "stl"})
        say("   EXPORTED - so a curve-only part does reach CATIA without a dialog")
    except Exception as error:  # noqa: BLE001
        say(f"   refused: {str(error).splitlines()[0][:200]}")

    docs.execute("catia_close_document", {"save": False})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
