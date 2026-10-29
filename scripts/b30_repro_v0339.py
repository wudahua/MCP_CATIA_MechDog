"""Controlled reproduction of the 2026-10-29 acceptance report, on CATIA B30.

The report (`2026-10-29_CATIA_v0.3.39_核心问题.md`) makes four claims that can be
settled by one measurement each, and one of them (#1, the placement convention)
is a code defect 0.3.40 fixes. This script measures them, not argues about them:

    #2  a fillet fails on every edge of every part  -> try R10 on a plain box
    #3  a ruled loft fails with AppendHybridShape   -> try two circles, ruled on,
                                                       and the same pair smooth
    #4  a GSD element cannot be deleted             -> create one, delete it
    #1  a basis table placed as a batch is applied
        transposed                                 -> place two instances and read
                                                       the axes back

Every document it touches is CREATED here, and closed at the end without saving.
Open user documents are only listed, never activated for editing.

Usage (from the repo root, with the venv that has pywin32):
    .venv/Scripts/python.exe scripts/b30_repro_v0339.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

# Rotate 90 deg about Z, written as BASIS rows: local X -> +Y, local Y -> -X.
BASIS = [[0.0, 1.0, 0.0],
         [-1.0, 0.0, 0.0],
         [0.0, 0.0, 1.0]]
EXPECTED_X = (0.0, 1.0, 0.0)

findings: list[str] = []
made_documents: list[str] = []


def say(line: str) -> None:
    print(line)
    findings.append(line)


def edges_of(reply: str) -> list[str]:
    payload = json.loads(reply)
    items = payload.get("edges") or payload.get("items") or []
    names = []
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            names.append(item["name"])
        elif isinstance(item, str):
            names.append(item)
    return names


def main() -> int:
    conn = CATIAConnection()
    print(conn.connect())
    opened = [conn.documents.Item(i).Name for i in range(1, conn.documents.Count + 1)]
    print(f"documents already open ({len(opened)}): {', '.join(opened)}")

    document = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    gsd = GSDTools(conn)
    assembly = AssemblyTools(conn)

    workdir = tempfile.mkdtemp(prefix="catia_repro_")
    fillet_part = os.path.join(workdir, "REPRO_FILLET.CATPart")

    # ── #2 fillet, R10, on a plain 40 x 20 x 20 box ─────────────────────
    worked = []
    try:
        document.execute("catia_new_part", {"name": "REPRO_FILLET",
                                           "file_path": fillet_part})
        made_documents.append("REPRO_FILLET.CATPart")
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": -20, "y1": -10, "x2": 20, "y2": 10})
        sketcher.execute("catia_close_sketch", {})
        part_design.execute("catia_pad", {"height": 20})
        names = edges_of(part_design.execute("catia_list_edges", {"limit": 30}))
        say(f"#2 box built; {len(names)} edges listed")
        for name in names[:12]:
            try:
                part_design.execute("catia_fillet", {"radius": 10,
                                                    "edge_name": name})
                worked.append(name)
                break
            except Exception as error:  # noqa: BLE001 - the failure IS the datum
                say(f"   R10 on {name}: {str(error).splitlines()[0][:110]}")
        if worked:
            say(f"#2 NOT REPRODUCED: R10 succeeded on {worked[0]} of a fresh box "
                "(so the failure is part- or edge-specific, not systematic)")
        else:
            say("#2 REPRODUCED: R10 failed on every edge of a fresh box")
    except Exception as error:  # noqa: BLE001
        say(f"#2 INCONCLUSIVE: could not build the box ({str(error)[:120]})")

    # ── #3 ruled loft, two circles, and the same pair smooth ────────────
    try:
        document.execute("catia_new_part", {"name": "REPRO_LOFT"})
        made_documents.append("REPRO_LOFT.CATPart")
        gsd.execute("catia_gsd_create_geoset", {"name": "RuledSet"})
        gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                        "radius": 10, "name": "R11"})
        gsd.execute("catia_gsd_circle", {"center": [0, 0, 100], "support_plane": "xy",
                                        "radius": 20, "name": "R12"})
        try:
            gsd.execute("catia_gsd_multi_section_surface",
                        {"sections": ["R11", "R12"], "ruled": True,
                         "name": "RuledLoft"})
            say("#3 ruled=TRUE: NOT REPRODUCED on two circles r=10/20, 100 mm apart")
        except Exception as error:  # noqa: BLE001
            say(f"#3 ruled=TRUE REPRODUCED: {str(error).splitlines()[0][:160]}")

        gsd.execute("catia_gsd_create_geoset", {"name": "SmoothSet"})
        gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                        "radius": 10, "name": "S11"})
        gsd.execute("catia_gsd_circle", {"center": [0, 0, 100], "support_plane": "xy",
                                        "radius": 20, "name": "S12"})
        try:
            gsd.execute("catia_gsd_multi_section_surface",
                        {"sections": ["S11", "S12"], "ruled": False,
                         "name": "SmoothLoft"})
            say("#3 ruled=FALSE: succeeded on the same pair (the pair itself is fine)")
        except Exception as error:  # noqa: BLE001
            say(f"#3 ruled=FALSE failed too: {str(error).splitlines()[0][:120]}")
    except Exception as error:  # noqa: BLE001
        say(f"#3 INCONCLUSIVE: {str(error)[:140]}")

    # ── #4 delete a GSD element ─────────────────────────────────────────
    try:
        gsd.execute("catia_gsd_create_geoset", {"name": "Throwaway"})
        gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                        "radius": 3, "name": "TMP_CIRCLE"})
        message = part_design.execute("catia_delete_feature",
                                      {"feature_name": "TMP_CIRCLE"})
        listing = part_design.execute("catia_list_features", {})
        still_there = "TMP_CIRCLE" in listing
        say(f"#4 delete GSD element: {message.splitlines()[0][:120]} | "
            f"still in tree: {still_there}")
    except Exception as error:  # noqa: BLE001
        say(f"#4 REPRODUCED (delete refused): {str(error).splitlines()[0][:160]}")

    # ── #1 a basis batch is applied transposed ──────────────────────────
    try:
        document.execute("catia_new_product", {"name": "REPRO_ASM"})
        made_documents.append("REPRO_ASM.CATProduct")
        assembly.execute("catia_add_component", {"file_path": fillet_part,
                                                "count": 2})
        listing = json.loads(assembly.execute("catia_list_components",
                                              {"names_only": True}))
        names = listing.get("names") or [entry.get("name")
                                         for entry in listing.get("components", [])]
        if len(names) < 2:
            raise RuntimeError(f"expected 2 instances, found {names!r}")
        reply = json.loads(assembly.execute("catia_set_component_transform", {
            "placements": [
                {"component_name": names[0], "rotation_rows": BASIS,
                 "translation": [100.0, 0.0, 0.0]},
                {"component_name": names[1], "rotation_rows": BASIS,
                 "translation": [200.0, 0.0, 0.0]},
            ],
            "rotation_convention": "basis",
        }))
        say(f"#1 batch convention: {json.dumps(reply.get('rotation_convention'))}")
        after = json.loads(assembly.execute("catia_get_component_transform",
                                           {"component_name": names[0]}))
        mapped = tuple(round(v, 6) for v in
                       after["basis_columns"]["local_x_maps_to"])
        verdict = "MATCH" if mapped == EXPECTED_X else "MISMATCH (transposed)"
        say(f"#1 local X maps to {mapped}, asked for {EXPECTED_X}: {verdict}")
    except Exception as error:  # noqa: BLE001
        say(f"#1 INCONCLUSIVE: {str(error).splitlines()[0][:160]}")

    # ── cleanup: only the documents this script created ─────────────────
    closed = []
    for name in reversed(made_documents):
        try:
            for index in range(1, conn.documents.Count + 1):
                candidate = conn.documents.Item(index)
                if candidate.Name == name:
                    candidate.Close()
                    closed.append(name)
                    break
        except Exception as error:  # noqa: BLE001
            say(f"cleanup: could not close {name} ({str(error)[:80]})")
    say(f"closed {len(closed)}/{len(made_documents)} created document(s): "
        f"{', '.join(closed) or '-'}")
    remaining = [conn.documents.Item(i).Name
                 for i in range(1, conn.documents.Count + 1)]
    say(f"documents open at the end ({len(remaining)}): "
        f"{'same as before' if remaining == opened else ', '.join(remaining)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
