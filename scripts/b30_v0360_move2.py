"""catia_gsd_rotate / catia_gsd_translate, through the tool入口, on B30.

Two things the implementation must not guess:

  1. the SIGN of the angle. "Positive is counter-clockwise" is the convention
     every CAD user assumes and none of them will test; a tool that is
     clockwise-only costs a whole run.
  2. that the tools MOVE the geometry, not just return an object. AddNewHealing
     is the precedent: declared in the typelib, does nothing.

Judgement is where the copy can still be cut, so nothing is exported:

  cylinder R, axis (20,0), r5, z 0..20, rotated +90 about z
      counter-clockwise -> axis (0,20): meets the plane y=+20, misses y=-20
      clockwise         -> axis (0,-20): the other way round
  cylinder T, axis (-20,0), translated 30 along +z -> spans 30..50: meets z=40
      and no longer meets z=0
"""

from __future__ import annotations

import sys

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools

say = lambda text: print(text, flush=True)  # noqa: E731


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    gsd = GSDTools(conn)
    docs.execute("catia_new_part", {})

    def cylinder(center: list[float], height: float, name: str) -> None:
        gsd.execute("catia_gsd_circle", {"center": center, "support_plane": "xy",
                                        "radius": 5, "name": f"{name}_C"})
        gsd.execute("catia_gsd_extrude", {"profile": f"{name}_C",
                                          "direction": [0, 0, 1],
                                          "limit1": height, "name": name})

    def meets(cutter: str, element: str) -> bool:
        try:
            gsd.execute("catia_gsd_intersection",
                        {"element1": element, "element2": cutter,
                         "name": f"X_{element}_{cutter}"})
            return True
        except Exception as error:  # noqa: BLE001
            say(f"    (no meet {element}/{cutter}: {str(error).splitlines()[0][:70]})")
            return False

    cylinder([20, 0, 0], 20, "R")
    cylinder([-20, 0, 0], 20, "T")
    gsd.execute("catia_gsd_plane_offset", {"base_plane": "zx", "offset": 20,
                                           "name": "YP20"})
    gsd.execute("catia_gsd_plane_offset", {"base_plane": "zx", "offset": 20,
                                           "reverse": True, "name": "YM20"})
    gsd.execute("catia_gsd_plane_offset", {"base_plane": "xy", "offset": 40,
                                           "name": "Z40"})

    say("--- rotate ---")
    say(gsd.execute("catia_gsd_rotate",
                    {"element": "R", "axis": "z", "angle": 90, "name": "R_ROT"}))
    say("--- translate [0,0,1] ---")
    say(gsd.execute("catia_gsd_translate",
                    {"element": "T", "direction": [0, 0, 1], "distance": 30,
                     "name": "T_MOV"}))
    say("--- translate 'z' ---")
    try:
        say(gsd.execute("catia_gsd_translate",
                        {"element": "T", "direction": "z", "distance": -60,
                         "name": "T_NEG"}))
    except Exception as error:  # noqa: BLE001
        say(f"  FAILED: {str(error).splitlines()[0][:120]}")

    up = meets("YP20", "R_ROT")
    down = meets("YM20", "R_ROT")
    original = meets("zx", "R")
    tall = meets("Z40", "T_MOV")
    low = meets("xy", "T_MOV")
    neg = meets("xy", "T_NEG")

    say("")
    say(f"R_ROT meets y=+20 : {up}    (True => +90 is counter-clockwise about +z)")
    say(f"R_ROT meets y=-20 : {down}")
    say(f"original R kept   : {original} (expect True - rotate COPIES)")
    say(f"T_MOV meets z=40  : {tall}  (expect True)")
    say(f"T_MOV meets z=0   : {low}   (expect False)")
    say(f"T_NEG meets z=0   : {neg}   (40 below the original: expect False)")

    docs.execute("catia_close_document", {"save": False})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
