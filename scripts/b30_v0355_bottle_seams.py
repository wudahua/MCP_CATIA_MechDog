"""The five-petal bottle base, measured - on a COPY, never on the user's file.

The 0.3.53 report says the model still carries 5 C0 seams (at Sec_02/04/06/07/09)
and asks whether AddNewHealing would close them, since the seams are "same
source sections, geometrically continuous, only parametrically broken" - unlike
the 90 degree fold 0.3.51 measured healing against.

That claim is testable and this measures it: where are the seams, and what angle
does the mesh see across them? A parametrisation break reads ~0 degrees (the two
patches meet tangentially and only the edge is doubled); a real crease from two
lofts meeting at a shared section reads whatever the two cross-boundary tangents
differ by, usually several degrees.

Read-only by construction: the file is copied to a temp directory first and the
copy is opened. The user's CATPart is never opened, so nothing can be saved over
it. Every geometry step asserts the active document (2026-10-30 rule).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.measurement import MeasurementTools
from catia_mcp.tools.part_design import PartDesignTools

SOURCE = (r"F:\WorkBuddy Space\2026-10-20-13-31-14\他人反馈的问题"
          r"\CA建模-可乐瓶五爪花瓣瓶底\ColaBottle_PetalBase_v6.CATPart")
WORK = os.path.join(tempfile.gettempdir(), "b30_v0355_bottle")


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
    os.makedirs(WORK, exist_ok=True)
    copy = os.path.join(WORK, "PETAL_v6_COPY.CATPart")
    if os.path.exists(copy):
        os.remove(copy)
    shutil.copy2(SOURCE, copy)
    say(f"copied to {copy} ({os.path.getsize(copy)} bytes)")

    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    measurement = MeasurementTools(conn)
    part_design = PartDesignTools(conn)

    for index in range(conn.documents.Count, 0, -1):
        try:
            name = str(conn.documents.Item(index).Name)
        except Exception:  # noqa: BLE001
            continue
        if name.startswith("PETAL_v6_COPY"):
            activate(conn, name)
            document.execute("catia_close_document", {"save": False})

    document.execute("catia_open_document", {"file_path": copy})
    activate(conn, "PETAL_v6_COPY.CATPart")
    say(f"active: {conn.active_document.Name}")

    # ── 1. what is in the tree ────────────────────────────────────────────
    say("\n── 1. tree ──")
    try:
        features = json.loads(part_design.execute("catia_list_features", {}))
        rows = features.get("features", features if isinstance(features, list) else [])
        say(f"   {len(rows)} features")
        for row in rows:
            if isinstance(row, dict):
                say(f"   {row.get('name', '?'):<28} {row.get('type', '')}")
    except Exception as error:  # noqa: BLE001
        say(f"   list_features failed: {str(error).splitlines()[0][:110]}")

    # ── 2. the whole part ─────────────────────────────────────────────────
    say("\n── 2. check_surface, whole part ──")
    whole = json.loads(measurement.execute("catia_check_surface",
                                           {"crease_deg": 5.0}))
    say(f"   triangles {whole['triangles']}  interior edges {whole['interior_edges']}  "
        f"boundary {whole['boundary_edges']}  non-manifold {whole['non_manifold_edges']}")
    fold = whole["fold_deg"]
    say(f"   median {fold['median']}  p90 {fold['p90']}  p99 {fold['p99']}  max {fold['max']}")
    say(f"   creases >= 5 deg: {whole['creases']}")
    for side, values in whole["fold_deg_by_side"].items():
        if values["edges"]:
            say(f"   by side {side:<6} edges {values['edges']:>6}  "
                f"median {values['median']:>6}  max {values['max']:>7}")

    # ── 3. where the creases are: cluster them by radius ──────────────────
    say("\n── 3. the worst creases, with their place ──")
    for crease in whole["worst_creases"]:
        say(f"   {crease['fold_deg']:7.2f} deg  {crease['side']:<6} "
            f"r {crease['r']:6.2f}  z {crease['z']:8.2f}  "
            f"theta {crease['theta_deg']:7.2f}")

    # The seams are rings, so a histogram over r of the creases above 5 deg is
    # the fastest way to see whether they sit on a handful of radii.
    say("\n── 4. creases per radius band (1 mm), above 5 deg ──")
    buckets: dict[int, int] = {}
    for crease in whole.get("all_creases", whole["worst_creases"]):
        band = int(round(crease["r"]))
        buckets[band] = buckets.get(band, 0) + 1
    for band in sorted(buckets):
        say(f"   r {band:>3} mm: {buckets[band]:>4} crease(s)")

    # A second pass with a low threshold gives the seam rings themselves: the
    # report says they are at 5 radii, so they should show up as narrow spikes.
    say("\n── 5. same sweep at 1 deg, only the counts per radius ──")
    fine = json.loads(measurement.execute("catia_check_surface",
                                          {"crease_deg": 1.0}))
    say(f"   creases >= 1 deg: {fine['creases']}")

    activate(conn, "PETAL_v6_COPY.CATPart")
    document.execute("catia_close_document", {"save": False})
    say(f"\ndocuments open now: {conn.documents.Count}")
    say(f"user file untouched: {os.path.getsize(SOURCE)} bytes")


if __name__ == "__main__":
    main()
