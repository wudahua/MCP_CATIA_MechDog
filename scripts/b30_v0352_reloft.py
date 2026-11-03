"""Does deleting a loft and rebuilding it under the same name break join?

The 0.3.50 report says yes in its §3.1 table and no in its §3.2 step 3 - "delete
P_04_06, rebuild it identically, join succeeds at once". Both cannot be true of
the same sequence, so the sequence is reproduced here with the two surfaces in
one document and nothing else in the tree.

    a  three coaxial circles, two lofts sharing the middle one, join      -> base
    b  delete the second loft, rebuild it under the same name, join again
    c  delete BOTH lofts, rebuild both, join again
    d  same as b but the shared section is deleted and rebuilt too
    e  control: a fresh document, same geometry, join without any delete

Every step prints the actual reply text, because the interesting part may be
which failure comes back, not whether one does.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

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


def close_prefix(document, conn, prefix="R_") -> None:
    while True:
        names = []
        for index in range(1, conn.documents.Count + 1):
            try:
                name = str(conn.documents.Item(index).Name)
            except Exception:  # noqa: BLE001
                continue
            if name.startswith(prefix):
                names.append(name)
        if not names:
            return
        activate(conn, names[0])
        document.execute("catia_close_document", {"save": False})


def main() -> int:
    conn = CATIAConnection()
    say(conn.connect())
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    part_design = PartDesignTools(conn)
    close_prefix(document, conn)

    def fresh(name: str):
        path = os.path.join(OUT, f"{name}.CATPart")
        for index in range(1, conn.documents.Count + 1):
            if str(conn.documents.Item(index).Name) == f"{name}.CATPart":
                activate(conn, f"{name}.CATPart")
                document.execute("catia_close_document", {"save": False})
        if os.path.exists(path):
            os.remove(path)
        document.execute("catia_new_part", {"name": name, "file_path": path})
        activate(conn, f"{name}.CATPart")

    def sections(tag: str):
        """Three circles of growing radius on yz planes at x = 0 / 20 / 40."""
        gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "yz",
                                         "radius": 10.0, "name": f"{tag}_C1"})
        gsd.execute("catia_gsd_circle", {"center": [20, 0, 0], "support_plane": "yz",
                                         "radius": 14.0, "name": f"{tag}_C2"})
        gsd.execute("catia_gsd_circle", {"center": [40, 0, 0], "support_plane": "yz",
                                         "radius": 18.0, "name": f"{tag}_C3"})

    def lofts(tag: str, suffix: str = ""):
        try:
            gsd.execute("catia_gsd_multi_section_surface",
                        {"sections": [f"{tag}_C1", f"{tag}_C2"],
                         "name": f"{tag}_P12{suffix}"})
            gsd.execute("catia_gsd_multi_section_surface",
                        {"sections": [f"{tag}_C2", f"{tag}_C3"],
                         "name": f"{tag}_P23{suffix}"})
            return True
        except Exception as error:  # noqa: BLE001
            say(f"    loft FAILED: {str(error).splitlines()[0][:140]}")
            return False

    def try_join(tag: str, label: str, suffix: str = "") -> str:
        try:
            reply = gsd.execute("catia_gsd_join",
                                {"elements": [f"{tag}_P12{suffix}", f"{tag}_P23{suffix}"],
                                 "name": f"{tag}_J{suffix}"})
            say(f"{label}: OK - {reply[:90]}")
            return "ok"
        except Exception as error:  # noqa: BLE001
            body = str(error).splitlines()[0]
            say(f"{label}: FAILED - {body[:150]}")
            return "failed"

    def tree(label: str) -> None:
        geoset = gsd._get_geoset()
        say(f"{label} tree {geoset.HybridShapes.Count}: " + ", ".join(
            str(geoset.HybridShapes.Item(i).Name)
            for i in range(1, min(geoset.HybridShapes.Count, 12) + 1)))

    # ── a. the base case ──────────────────────────────────────────────────
    say("\n── a. two lofts sharing a section, joined ──")
    fresh("R_A")
    sections("A")
    lofts("A")
    tree("a")
    say(f"\na result: {try_join('A', 'a join(P12, P23)')}")

    # ── b. delete one loft, rebuild it under the same name ───────────────
    say("\n── b. delete P23, rebuild it, join again ──")
    say("b delete: " + part_design.execute("catia_delete_feature",
                                           {"feature_name": "A_P23"}))
    tree("b")
    # Rebuilt under the SAME name, which is what the report describes.
    lofts("A")
    tree("b")
    say(f"\nb result: {try_join('A', 'b join(P12, P23)')}")

    # ── c. delete both lofts, rebuild both ────────────────────────────────
    say("\n── c. delete BOTH lofts, rebuild both, join ──")
    fresh("R_C")
    sections("C")
    lofts("C")
    try_join("C", "c base join")
    for name in ("C_P12", "C_P23", "C_J"):
        try:
            part_design.execute("catia_delete_feature", {"feature_name": name})
        except Exception as error:  # noqa: BLE001
            say(f"c delete {name}: {str(error).splitlines()[0][:80]}")
    tree("c after delete")
    lofts("C")
    tree("c after rebuild")
    say(f"\nc result: {try_join('C', 'c join(P12, P23)')}")

    # ── d. also delete and rebuild the shared section ────────────────────
    say("\n── d. rebuild the SHARED section too ──")
    fresh("R_D")
    sections("D")
    lofts("D")
    try_join("D", "d base join")
    for name in ("D_J", "D_P12", "D_P23", "D_C2"):
        try:
            part_design.execute("catia_delete_feature", {"feature_name": name})
        except Exception as error:  # noqa: BLE001
            say(f"d delete {name}: {str(error).splitlines()[0][:80]}")
    tree("d after delete")
    # C1 and C3 survived; C2 is new, so both lofts reference the new one.
    gsd.execute("catia_gsd_circle", {"center": [20, 0, 0], "support_plane": "yz",
                                     "radius": 14.0, "name": "D_C2"})
    lofts("D")
    tree("d after rebuild")
    say(f"\nd result: {try_join('D', 'd join(P12, P23)')}")

    # ── e. control: never deleted ─────────────────────────────────────────
    say("\n── e. control, no deletes at all ──")
    fresh("R_E")
    sections("E")
    lofts("E")
    say(f"\ne result: {try_join('E', 'e join(P12, P23)')}")

    close_prefix(document, conn)
    say(f"\ndocuments open: {conn.documents.Count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
