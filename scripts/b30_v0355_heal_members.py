"""Does a Healing feature have anything besides Continuity to drive it?

The seam measurement showed Continuity 0, 1 and 2 producing byte-identical
meshes (112 triangles, 136 interior edges, 0.52 deg at the seam). Before calling
healing useless, check that nothing else is needed: read the Healing object's own
type info and print every member, so "it does nothing" cannot be a wrong usage.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools

OUT = os.path.join(tempfile.gettempdir(), "b30_v0355_heal3")


def say(text: str = "") -> None:
    print(text, flush=True)


def members(obj: object, label: str) -> None:
    say(f"\n=== {label} ===")
    try:
        info = obj._oleobj_.GetTypeInfo()
        attributes = info.GetTypeAttr()
    except Exception as error:  # noqa: BLE001
        say(f"  no type info: {str(error).splitlines()[0][:90]}")
        return
    names = []
    for index in range(attributes.cFuncs):
        try:
            desc = info.GetFuncDesc(index)
            names.extend(info.GetNames(desc.memid)[:1])
        except Exception:  # noqa: BLE001
            continue
    wanted = ("AddBodyToHeal", "SetMergingDistance", "SetTangencyAngle",
              "SetSharpnessAngle", "SetDistanceObjective", "SetTangencyObjective",
              "ReplaceToHealElement")
    for index in range(attributes.cFuncs):
        try:
            desc = info.GetFuncDesc(index)
            names = info.GetNames(desc.memid)
        except Exception:  # noqa: BLE001
            continue
        if names and names[0] in wanted:
            say(f"  {names[0]}(" + ", ".join(names[1:]) + ")")

    for index in range(attributes.cVars):
        try:
            desc = info.GetVarDesc(index)
            names.extend(info.GetNames(desc.memid)[:1])
        except Exception:  # noqa: BLE001
            continue
    say("  " + ", ".join(sorted(set(names))))


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)

    for index in range(conn.documents.Count, 0, -1):
        try:
            name = str(conn.documents.Item(index).Name)
        except Exception:  # noqa: BLE001
            continue
        if name.startswith("V0355_"):
            conn.documents.Item(index).Activate()
            document.execute("catia_close_document", {"save": False})

    document.execute("catia_new_part",
                     {"name": "V0355_HEAL3",
                      "file_path": os.path.join(OUT, "V0355_HEAL3.CATPart")})
    assert str(conn.active_document.Name) == "V0355_HEAL3.CATPart"
    part = conn.get_active_part()
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": 10.0, "name": "S_C"})
    gsd.execute("catia_gsd_extrude", {"profile": "S_C", "direction": [0, 0, 1],
                                      "limit1": 20.0, "name": "S_S"})
    healed = gsd._factory().AddNewHealing(
        part.CreateReferenceFromObject(part.FindObjectByName("S_S")))
    gsd._get_geoset().AppendHybridShape(healed)
    members(healed, "HybridShapeHealing")
    try:
        say(f"  Continuity = {healed.Continuity}")
    except Exception as error:  # noqa: BLE001
        say(f"  Continuity unreadable: {str(error).splitlines()[0][:70]}")

    for index in range(conn.documents.Count, 0, -1):
        try:
            name = str(conn.documents.Item(index).Name)
        except Exception:  # noqa: BLE001
            continue
        if name.startswith("V0355_"):
            conn.documents.Item(index).Activate()
            document.execute("catia_close_document", {"save": False})
    say(f"\ndocuments open now: {conn.documents.Count}")


if __name__ == "__main__":
    main()
