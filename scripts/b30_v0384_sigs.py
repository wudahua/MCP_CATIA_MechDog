"""0.3.84 probe 1: read the real signatures, before calling anything.

Two reports on 0.3.83 ask for four capabilities, and three of them hinge on a
signature this server has never read:

  * AddNewGSDCircPattern - the report says it lives on ShapeFactory, and the
    0.3.82 reply says it does not exist anywhere. Both can be settled by asking
    the factory objects themselves.
  * AddNewPlaneAngle - a datum plane rotated about an axis, which the tutorial's
    Step 3 needs and no tool provides.
  * AddNewBoundaryOfSurface - whether the one-loop-only limit is the method's or
    this server's: a second parameter selecting the loop would end it.

Nothing is created here except the probe's own part, so a wrong guess costs a
line of output rather than a damaged document. Every document this script
touches is checked to live under ROOT before it is written to.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402

ROOT = r"F:\catia_probe_v0384"

# What to look for, and why each one is on the list.
WANTED = {
    "AddNewGSDCircPattern": "the report's claim: single-feature circular pattern",
    "AddNewGSDRectPattern": "same family, linear",
    "AddNewSurfacicCircPattern": "the surfacic variant",
    "AddNewCircPattern": "the solid-feature variant",
    "AddNewPlaneAngle": "tutorial Step 3: datum plane rotated about an axis",
    "AddNewPlane1Line1Pt": "plane through a line and a point",
    "AddNewPlane2Lines": "plane through two lines",
    "AddNewPlaneEquation": "plane from coefficients",
    "AddNewBoundaryOfSurface": "does it take a loop selector?",
    "AddNewBoundary": "the older spelling, sometimes richer",
    "AddNewFill": "the cap the tutorial's Step 6 needs",
    "AddNewExtract": "for pulling one edge out of a surface",
    "AddNewThickSurface": "the wall the report measured at 0.25/0.25",
}


def say(line: str = "") -> None:
    print(line, flush=True)


def dump(factory, label: str) -> None:
    """Every wanted method this factory declares, with its parameter names."""
    try:
        info = factory._oleobj_.GetTypeInfo()
        count = info.GetTypeAttr().cFuncs
    except Exception as error:  # noqa: BLE001 - no typeinfo, no table
        say(f"{label}: typeinfo unreadable ({str(error).splitlines()[0][:70]})")
        return

    found: dict[str, list[str]] = {}
    all_addnew: list[str] = []
    for index in range(count):
        try:
            desc = info.GetFuncDesc(index)
            names = info.GetNames(desc.memid)
        except Exception:  # noqa: BLE001 - one unreadable slot
            continue
        if not names:
            continue
        head = names[0]
        if head.startswith("AddNew"):
            all_addnew.append(head)
        if head in WANTED:
            found[head] = list(names[1:])

    say(f"{label}: {count} members, {len(all_addnew)} AddNew*")
    say(f"  contain 'Pattern': "
        f"{[name for name in all_addnew if 'Pattern' in name] or 'none'}")
    say(f"  contain 'Plane':   "
        f"{[name for name in all_addnew if 'Plane' in name] or 'none'}")
    say(f"  contain 'Bound':   "
        f"{[name for name in all_addnew if 'Bound' in name] or 'none'}")
    for name, params in found.items():
        say(f"  {name}({', '.join(params)})  <- {WANTED[name]}")
    missing = [name for name in WANTED if name not in found]
    if missing:
        say(f"  not declared here: {missing}")


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)

    # Only ever close this probe's OWN documents: a name is not an identity, and
    # 0.3.80's incident was exactly that mistake.
    own_root = os.path.normcase(os.path.abspath(ROOT))
    for index in range(conn.documents.Count, 0, -1):
        try:
            stale = conn.documents.Item(index)
            full = os.path.normcase(os.path.abspath(str(stale.FullName)))
        except Exception:  # noqa: BLE001 - never saved
            continue
        if full.startswith(own_root):
            say(f"closing a document left by an earlier run: {stale.Name}")
            stale.Close()

    part_path = os.path.join(ROOT, "prb84.CATPart")
    if os.path.isfile(part_path):
        os.remove(part_path)
    say(document.execute("catia_new_part",
                         {"name": "PRB84", "file_path": part_path}))

    part = conn.get_active_part()
    say("")
    say(f"=== ShapeFactory (Part Design) ===")
    dump(part.ShapeFactory, "ShapeFactory")
    say("")
    say("=== HybridShapeFactory (GSD) ===")
    dump(part.HybridShapeFactory, "HybridShapeFactory")


if __name__ == "__main__":
    main()
