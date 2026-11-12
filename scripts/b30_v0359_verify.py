"""0.3.59 end-to-end on B30: region scopes the curvature block, and a
duplicated sheet is reported as such instead of as a radius.

The failure MESSAGE cannot be produced here (B30 thickens everything - see
0.3.58: a 0.1 mm slot at 0.6, an r=0.2 cylinder at 0.3, an r=5 cylinder at 400),
so it is pinned with stubs in the test file. What B30 can do is the measurement:
call check_surface on real geometry, with and without a duplicated surface, and
with and without a region.

Run with -u; closes only its own V0359_* documents.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.measurement import MeasurementTools
from catia_mcp.tools.part_design import PartDesignTools

TMP = r"C:\Users\ADMINI~1\AppData\Local\Temp\b30_v0359"


def say(text: str = "") -> None:
    print(text, flush=True)


def clipped(block: dict) -> str:
    """The fields under test, in one line each."""
    out = []
    for key in ("edges_measured", "min_radius_mm", "min_radius_at_open_border",
                "tightest_smooth_radius_mm"):
        out.append(f"{key}={block.get(key)}")
    excluded = block.get("excluded") or {}
    out.append(f"fold_back={excluded.get('fold_back_edges')}")
    out.append(f"ambiguous={excluded.get('ambiguous_edges')}")
    out.append(f"boundary={excluded.get('open_boundary_edges')}")
    out.append(f"outside_region={excluded.get('outside_region')}")
    return "  " + ", ".join(out)


def main() -> None:
    conn = CATIAConnection()
    conn.connect()
    if conn.documents.Count:
        raise SystemExit("a document is already open; close it first")
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)
    part_design = PartDesignTools(conn)

    def own(tag: str):
        document.execute("catia_new_part",
                         {"name": tag, "file_path": rf"{TMP}\{tag}.CATPart"})
        assert str(conn.active_document.Name).startswith(tag), \
            conn.active_document.Name
        return conn.get_active_part()

    def check(**arguments) -> dict:
        return json.loads(measurement.execute("catia_check_surface", arguments))

    try:
        # --- A. one real curved surface, open at both rims.
        own("V0359_A")
        part = conn.get_active_part()
        gsd.execute("catia_gsd_circle", {"center": [0, 0, 0],
                                         "support_plane": "xy", "radius": 10.0,
                                         "name": "CA"})
        gsd.execute("catia_gsd_extrude", {"profile": "CA",
                                          "direction": [0, 0, 1],
                                          "limit1": 40.0, "name": "SA"})
        part.Update()
        whole = check(crease_deg=15, thickness=0.3, top_creases=3)
        say("A. one tube, whole mesh:")
        say(clipped(whole["curvature"]))
        say(f"  min at open border: {whole['curvature']['min_radius_at_open_border']}"
            f"   verdict: {whole['curvature']['thickness_check']['verdict'][:70]}")

        lower = check(crease_deg=15, thickness=0.3, top_creases=3,
                      region={"z": [0.0, 5.0]})
        say("\nA. same tube, region z 0..5:")
        say(clipped(lower["curvature"]))
        say(f"  curvature region: {lower['curvature'].get('region')}")
        say(f"  note: {lower['curvature'].get('region_note')}")
        say(f"  fold pass also scoped: interior_edges={lower.get('interior_edges')}"
            f" outside={lower.get('interior_edges_outside_region')}")

        # --- B. a second, coincident extrusion: the duplicated sheet.
        gsd.execute("catia_gsd_extrude", {"profile": "CA",
                                          "direction": [0, 0, -1],
                                          "limit1": -40.0, "name": "SB"})
        part.Update()
        doubled = check(crease_deg=15, thickness=0.3, top_creases=3)
        say("\nB. same surface plus a coincident one:")
        say(clipped(doubled["curvature"]))
        excluded = doubled["curvature"]["excluded"]
        say(f"  fold_back_sample: {(excluded.get('fold_back_sample') or [None])[0]}")
        say(f"  ambiguous_note: {(excluded.get('ambiguous_note') or '-')[:90]}")
        say(f"  verdict: {doubled['curvature']['thickness_check']['verdict'][:110]}")

        # --- C. hide the duplicate: the reading has to come back unchanged.
        part_design.execute("catia_set_visibility", {"name": "SB", "show": False})
        part.Update()
        single = check(crease_deg=15, thickness=0.3, top_creases=3)
        say("\nC. duplicate hidden:")
        say(clipped(single["curvature"]))
    finally:
        for index in range(conn.documents.Count, 0, -1):
            doc = conn.documents.Item(index)
            name = str(doc.Name)
            if name.startswith("V0359_"):
                doc.Activate()
                document.execute("catia_close_document", {"save": False})
                say(f"closed {name}")
        say(f"left {conn.documents.Count} document(s)")


main()
