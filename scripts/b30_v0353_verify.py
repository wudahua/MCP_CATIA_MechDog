"""End-to-end check of the two 0.3.53 capabilities, on the live V5 session.

1. catia_gsd_boundary - build a surface, take its border, measure it.
2. catia_check_surface region - the same mesh read whole and then by band, so the
   split is visible in numbers rather than asserted.

Every geometry step asserts the active document (the 2026-10-30 rule).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.measurement import MeasurementTools
from catia_mcp.tools.part_design import PartDesignTools

OUT = os.path.join(tempfile.gettempdir(), "b30_v0353")
os.makedirs(OUT, exist_ok=True)


def say(text: str = "") -> None:
    print(text, flush=True)


def activate(conn, name: str) -> None:
    for index in range(1, conn.documents.Count + 1):
        document = conn.documents.Item(index)
        if str(document.Name) == name:
            document.Activate()
            break
    else:
        raise RuntimeError(f"{name} is not open")
    if str(conn.active_document.Name) != name:
        raise RuntimeError(f"wanted {name} active, got {conn.active_document.Name}")


def main() -> None:
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)
    part_design = PartDesignTools(conn)

    for index in range(conn.documents.Count, 0, -1):
        try:
            existing = str(conn.documents.Item(index).Name)
        except Exception:  # noqa: BLE001
            continue
        if existing.startswith("V0353_"):
            activate(conn, existing)
            document.execute("catia_close_document", {"save": False})

    document.execute("catia_new_part", {"name": "V0353_V",
                                        "file_path": os.path.join(OUT, "V0353_V.CATPart")})
    activate(conn, "V0353_V.CATPart")

    say("── 1. catia_gsd_boundary ──")
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": 10.0, "name": "V_C"})
    gsd.execute("catia_gsd_extrude", {"profile": "V_C", "direction": [0, 0, 1],
                                      "limit1": 30.0, "name": "V_S"})
    reply = gsd.execute("catia_gsd_boundary", {"surface": "V_S", "name": "V_B"})
    say("   " + reply.splitlines()[0][:150])
    # catia_measure_distance needs TWO elements, so read the length straight off
    # SPAWorkbench - the same call catia_check_surface uses internally.
    try:
        part = conn.get_active_part()
        element = part.FindObjectByName("V_B")
        measurable = (conn.active_document.GetWorkbench("SPAWorkbench")
                      .GetMeasurable(part.CreateReferenceFromObject(element)))
        say(f"   boundary length {round(float(measurable.Length), 3)} mm "
            f"(two r=10 circles = 125.664)")
    except Exception as error:  # noqa: BLE001
        say(f"   measure failed: {str(error).splitlines()[0][:90]}")

    say("\n── 2. catia_check_surface, whole part vs region ──")
    # A pad is enough: the mesh is the solid, and the point of the test is the
    # filtering, not the geometry.
    from catia_mcp.tools.sketcher import SketcherTools
    sketcher = SketcherTools(conn)
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 40, "y2": 20})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10.0})

    whole = json.loads(measurement.execute("catia_check_surface", {"crease_deg": 25.0}))
    say(f"   whole : triangles {whole['triangles']}, edges {whole['interior_edges']}, "
        f"median {whole['fold_deg']['median']}, max {whole['fold_deg']['max']}")
    for region, label in (({"r": [0, 5]}, "r 0..5"), ({"z": [5, 15]}, "z 5..15")):
        payload = json.loads(measurement.execute(
            "catia_check_surface", {"crease_deg": 25.0, "region": region}))
        say(f"   {label:<8}: edges {payload['interior_edges']} of "
            f"{payload['interior_edges_total']}, median {payload['fold_deg']['median']}, "
            f"max {payload['fold_deg']['max']}, outside "
            f"{payload['interior_edges_outside_region']}")
    schema = [tool["inputSchema"]["properties"]
              for tool in measurement.get_tool_definitions()
              if tool["name"] == "catia_check_surface"][0]
    say("   schema keys: " + ", ".join(sorted(schema)))

    say("\n── 3. the guards that cost nothing to check ──")
    try:
        measurement.execute("catia_measure_distance", {})
        say("   3 empty distance call: NO ERROR (wrong)")
    except Exception as error:  # noqa: BLE001
        code = getattr(error, "code", "?")
        stage = getattr(error, "stage", "?")
        say(f"   3 empty distance call: code {code}, stage {stage}, "
            f"{str(error).splitlines()[0][:80]}")

    activate(conn, "V0353_V.CATPart")
    document.execute("catia_close_document", {"save": False})
    say(f"\ndocuments open now: {conn.documents.Count}")


if __name__ == "__main__":
    main()
