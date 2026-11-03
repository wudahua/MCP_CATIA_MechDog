"""Can a fillet that failed UpdateObject be deleted, leaving the part healthy?

A tool that searches orientation has to try calls that fail. Each failure leaves
an element in ERROR in the tree, and b30_v0356_fillet_geom.py proved the cost:
any later part.Update() on that document fails too - which is why the matrix and
the geometry check had to run in separate documents.

If the broken element can be deleted and the part then updates cleanly, the
search can live in ONE document (and in the user's own): fail, delete, try the
next, and no wreckage is left behind. If it cannot, the tool must run the search
in a temporary document, and the answer comes back as a copy of the surface.

This tests the deletion through the part-design tool, which is where
catia_delete_feature actually lives (calling it on DocumentTools just says
"Unknown document tool" - the mistake made in the first version of the probe).
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.part_design import PartDesignTools

OUT = os.path.join(tempfile.gettempdir(), "b30_v0356", "CLEANUP.CATPart")
TAG = "CLEANUP"


def say(text: str = "") -> None:
    print(text, flush=True)


def main() -> None:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    part_design = PartDesignTools(conn)
    document.execute("catia_new_part", {"name": TAG, "file_path": OUT})
    assert str(conn.active_document.Name).startswith(TAG), conn.active_document.Name
    part = conn.get_active_part()
    factory = gsd._factory()
    geoset = gsd._get_geoset()

    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [60, 0, 0],
                                   "name": f"{TAG}_LA"})
    gsd.execute("catia_gsd_extrude", {"profile": f"{TAG}_LA", "direction": [0, 0, 1],
                                      "limit1": 40.0, "name": f"{TAG}_A"})
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [0, 60, 0],
                                   "name": f"{TAG}_LB"})
    gsd.execute("catia_gsd_extrude", {"profile": f"{TAG}_LB", "direction": [0, 0, 1],
                                      "limit1": 40.0, "name": f"{TAG}_B"})
    ref_a = part.CreateReferenceFromObject(part.FindObjectByName(f"{TAG}_A"))
    ref_b = part.CreateReferenceFromObject(part.FindObjectByName(f"{TAG}_B"))

    say("── build one fillet that is known to fail (o=1,1) ──")
    fillet = factory.AddNewFilletBiTangent(ref_a, ref_b, 5.0, 1, 1, 0, 0)
    geoset.AppendHybridShape(fillet)
    fillet.Name = f"{TAG}_BAD"
    try:
        part.UpdateObject(fillet)
        say("  unexpectedly updated")
    except Exception as error:  # noqa: BLE001
        say(f"  failed as expected: {str(error).splitlines()[0][:60]}")

    say("── does part.Update() still work with the broken element present? ──")
    try:
        part.Update()
        say("  part.Update() OK even with the broken element")
    except Exception as error:  # noqa: BLE001
        say(f"  part.Update() FAILS: {str(error).splitlines()[0][:60]}")

    say("── delete the broken element by name ──")
    try:
        say("  " + str(part_design.execute("catia_delete_feature",
                                           {"feature_name": f"{TAG}_BAD"}))[:200])
    except Exception as error:  # noqa: BLE001
        say(f"  delete failed: {str(error).splitlines()[0][:90]}")

    say("── is the part healthy again (Update, then build the good one)? ──")
    try:
        part.Update()
        say("  part.Update() OK after deleting the broken element")
    except Exception as error:  # noqa: BLE001
        say(f"  part.Update() still FAILS: {str(error).splitlines()[0][:60]}")
    try:
        good = factory.AddNewFilletBiTangent(ref_a, ref_b, 5.0, -1, 1, 0, 0)
        geoset.AppendHybridShape(good)
        good.Name = f"{TAG}_GOOD"
        part.UpdateObject(good)
        say("  the good fillet builds after the cleanup")
    except Exception as error:  # noqa: BLE001
        say(f"  the good fillet FAILS: {str(error).splitlines()[0][:60]}")
    try:
        part.Update()
        say("  part.Update() OK with the good fillet")
    except Exception as error:  # noqa: BLE001
        say(f"  part.Update() FAILS with the good fillet: {str(error).splitlines()[0][:60]}")

    say(f"\n  names left in the set: "
        f"{[str(geoset.HybridShapes.Item(i).Name) for i in range(1, geoset.HybridShapes.Count + 1)]}")

    for index in range(conn.documents.Count, 0, -1):
        try:
            if str(conn.documents.Item(index).Name).startswith(TAG):
                conn.documents.Item(index).Activate()
                document.execute("catia_close_document", {"save": False})
        except Exception:  # noqa: BLE001
            pass
    say(f"documents open now: {conn.documents.Count}")


if __name__ == "__main__":
    main()
