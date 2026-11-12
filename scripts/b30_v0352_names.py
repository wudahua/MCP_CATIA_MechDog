"""Two behaviours after adding the name-uniqueness guard.

    1  building twice under the same name: the second element must come back
       with a different name, and the reply must say so (before: two elements
       with one name, which is what made a delete+rebuild unfalsifiable).
    2  deleting by a name that two elements carry: must be REFUSED, naming the
       count, instead of removing an arbitrary one.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402

OUT = os.path.join(tempfile.gettempdir(), "b30_v0352")
os.makedirs(OUT, exist_ok=True)


def say(line: str = "") -> None:
    print(line, flush=True)


def activate(conn, name: str):
    for index in range(1, conn.documents.Count + 1):
        candidate = conn.documents.Item(index)
        if str(candidate.Name) == name:
            candidate.Activate()
            if str(conn.active_document.Name) != name:
                raise RuntimeError(f"could not activate {name}")
            return candidate
    raise RuntimeError(f"{name} is not open")


def main() -> int:
    conn = CATIAConnection()
    say(conn.connect())
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    part_design = PartDesignTools(conn)

    name = "N_Names"
    path = os.path.join(OUT, f"{name}.CATPart")
    for index in range(1, conn.documents.Count + 1):
        if str(conn.documents.Item(index).Name) == f"{name}.CATPart":
            activate(conn, f"{name}.CATPart")
            document.execute("catia_close_document", {"save": False})
    if os.path.exists(path):
        os.remove(path)
    document.execute("catia_new_part", {"name": name, "file_path": path})
    activate(conn, f"{name}.CATPart")

    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "yz",
                                     "radius": 10.0, "name": "N_C1"})
    gsd.execute("catia_gsd_circle", {"center": [20, 0, 0], "support_plane": "yz",
                                     "radius": 14.0, "name": "N_C2"})

    def tree(label):
        geoset = gsd._get_geoset()
        names = [str(geoset.HybridShapes.Item(i).Name)
                 for i in range(1, geoset.HybridShapes.Count + 1)]
        say(f"{label}: {names}")

    say("\n── 1. the same name twice ──")
    say("1a " + gsd.execute("catia_gsd_multi_section_surface",
                            {"sections": ["N_C1", "N_C2"], "name": "N_P"}))
    tree("1b tree")
    say("1c " + gsd.execute("catia_gsd_multi_section_surface",
                            {"sections": ["N_C1", "N_C2"], "name": "N_P"}))
    tree("1d tree")
    part = conn.get_active_part()
    say(f"1e FindObjectByName('N_P') -> "
        + str(getattr(part.FindObjectByName("N_P"), "Name", None)))
    say(f"1f FindObjectByName('N_P.2') -> "
        + str(getattr(part.FindObjectByName("N_P.2"), "Name", None)))

    say("\n── 2. two elements forced onto one name, then delete by it ──")
    # Force a duplicate the way an outside tool (or an older session) would:
    # rename the .2 element onto the base name.
    victim = part.FindObjectByName("N_P.2")
    victim.Name = "N_P"
    tree("2a tree after the forced rename")
    try:
        say("2b " + part_design.execute("catia_delete_feature",
                                        {"feature_name": "N_P"}))
    except Exception as error:  # noqa: BLE001
        first = str(error).splitlines()[0]
        say(f"2b REFUSED ({type(error).__name__}): {first[:220]}")

    say("\n── 3. the route the refusal points at: names=[...] takes every one ──")
    say("3 " + part_design.execute("catia_delete_feature", {"names": ["N_P"]}))
    tree("3 tree")
    say(f"3 FindObjectByName('N_P') -> {part.FindObjectByName('N_P')!r}")

    activate(conn, f"{name}.CATPart")
    document.execute("catia_close_document", {"save": False})
    say(f"\ndocuments open: {conn.documents.Count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
