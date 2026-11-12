"""0.3.61 on B30: the four fixes that had only been offline so far.

  A. catia_gsd_revolve with axis='z' - it used to answer "Element 'z' not found
     ... searched 12 tree name(s)", a message that reads like a data problem.
  B. catia_gsd_circle with the 'support' spelling - it used to reach the server
     as a KeyError, and it never even got there through a schema-checking client
     because support_plane was in `required`.
  C. a mesh export when every GSD element is HIDDEN - B32 got a 37-byte STL and
     "success"; the guard has to refuse it before the call.
  D. catia_get_inertia on a part while no product is open - it must not spend
     3 s walking assemblies that cannot contain it. The number is printed.

What is NOT here: the thick-surface retry. It only fires on a failure that is
session-sensitive, and nothing in this session reproduces one - forcing it would
prove the retry branch runs, not that it helps.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.export import ExportTools
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.measurement import MeasurementTools
from catia_mcp.tools.part_design import PartDesignTools

say = lambda text: print(text, flush=True)  # noqa: E731


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)
    export = ExportTools(conn)
    design = PartDesignTools(conn)

    docs.execute("catia_new_part", {})

    say("B. circle with the 'support' spelling")
    try:
        say("   " + gsd.execute("catia_gsd_circle",
                                {"center": [30, 0, 0], "support": "xy",
                                 "radius": 6, "name": "C_ALIAS"}))
    except Exception as error:  # noqa: BLE001
        say(f"   FAILED: {str(error).splitlines()[0][:160]}")

    say("A. revolve with axis='z'")
    try:
        gsd.execute("catia_gsd_line", {"point1": [30, 0, 0], "point2": [30, 0, 20],
                                       "name": "GEN"})
        # A vertical line, so the sweep about z is a cylinder. Revolving the
        # CIRCLE about z would degenerate (the profile plane contains the axis),
        # which is geometry, not the parameter I am testing.
        say("   " + gsd.execute("catia_gsd_revolve",
                                {"profile": "GEN", "axis": "z",
                                 "angle1": 90, "name": "REV_Z"}))
    except Exception as error:  # noqa: BLE001
        say(f"   FAILED: {str(error).splitlines()[0][:160]}")

    say("C. mesh export with everything hidden")
    for element in ("C_ALIAS", "GEN", "REV_Z"):
        try:
            design.execute("catia_set_visibility",
                           {"name": element, "visible": False})
            say(f"   hid {element}")
        except Exception as error:  # noqa: BLE001
            say(f"   hide {element} failed: {str(error).splitlines()[0][:100]}")
    try:
        export.execute("catia_export",
                       {"file_path": r"C:\Temp\v0361_hidden.stl", "format": "stl"})
        import os
        size = os.path.getsize(r"C:\Temp\v0361_hidden.stl")
        say(f"   EXPORTED anyway - the guard did not fire ({size} bytes)")
    except Exception as error:  # noqa: BLE001
        say(f"   refused: {str(error).splitlines()[0][:200]}")

    say("D. inertia on a part with a solid, no assembly open")
    for index in range(1, conn.documents.Count + 1):
        name = str(conn.documents.Item(index).Name)
        if name.startswith("ColaBottle_PetalBase"):
            conn.documents.Item(index).Activate()
            started = time.perf_counter()
            payload = measurement.execute("catia_get_inertia", {})
            elapsed = (time.perf_counter() - started) * 1000
            say(f"   {elapsed:.0f} ms")
            for line in str(payload).splitlines():
                if "tensor" in line or "uniform_thickness" in line:
                    say("   " + line.strip())
            break

    docs.execute("catia_close_document", {"save": False})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
