"""0.3.84 probe 6: what call shape does ShapeFactory.AddNewGSDCircPattern take?

Probe 2 created it (14 arguments, matching the typelib) and AppendHybridShape
refused it; trying body.Shapes.Add then left the part unable to update at all.
So the arity is right and the place it goes - or one of the enum arguments - is
not. Each variant gets a fresh part, because a refused pattern can poison the
document:

  * iType: 0, 1, 2 (the trailing enum; the typelib does not name its values)
  * iCompleteCrown: True vs False (True makes the step argument redundant)
  * append target: geometrical set vs nothing (update first, then place)

The measurement that matters is UpdateObject, not the append: an object that
updates is real even before it is filed in the tree.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402

ROOT = r"F:\catia_probe_v0384\pat"


def say(line: str = "") -> None:
    print(line, flush=True)


def build_surface(hsf, gsd, geoset):
    """A vertical planar patch 20..30 mm out on X, spanning z 0..5."""
    gsd.execute("catia_gsd_line",
                {"point1": [0, 0, 0], "point2": [0, 0, 20], "name": "AXZ"})
    gsd.execute("catia_gsd_line",
                {"point1": [20, 0, 0], "point2": [30, 0, 0], "name": "SPOKE"})
    gsd.execute("catia_gsd_extrude",
                {"profile": "SPOKE", "direction": [0, 0, 1], "limit1": 5,
                 "name": "PATCH"})


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)

    own_root = os.path.normcase(os.path.abspath(r"F:\catia_probe_v0384"))
    for index in range(conn.documents.Count, 0, -1):
        try:
            stale = conn.documents.Item(index)
            full = os.path.normcase(os.path.abspath(str(stale.FullName)))
        except Exception:  # noqa: BLE001
            continue
        if full.startswith(own_root):
            stale.Close()

    cases = [
        ("type 0, crown True", 0, True),
        ("type 1, crown True", 1, True),
        ("type 2, crown True", 2, True),
        ("type 0, crown False", 0, False),
        ("type 1, crown False", 1, False),
    ]

    for position, (label, pattern_type, crown) in enumerate(cases, 1):
        name = f"PAT{position}"
        path = os.path.join(ROOT, f"{name}.CATPart")
        if os.path.isfile(path):
            os.remove(path)
        document.execute("catia_new_part", {"name": name, "file_path": path})
        part = conn.get_active_part()
        hsf = part.HybridShapeFactory
        sf = part.ShapeFactory
        build_surface(hsf, gsd, None)

        part.Update()
        geoset = part.HybridBodies.Item(1)
        centre = hsf.AddNewPointCoord(0.0, 0.0, 0.0)
        geoset.AppendHybridShape(centre)
        centre.Name = "CEN"
        part.UpdateObject(centre)

        axis_ref = part.CreateReferenceFromObject(part.FindObjectByName("AXZ"))
        surf_ref = part.CreateReferenceFromObject(part.FindObjectByName("PATCH"))
        centre_ref = part.CreateReferenceFromObject(centre)

        try:
            made = sf.AddNewGSDCircPattern(
                surf_ref, 1, 5, 0.0, 72.0, 0.0, 0.0,
                centre_ref, axis_ref, False, 360.0, False, crown, pattern_type)
        except Exception as error:  # noqa: BLE001 - arity or type refused
            say(f"{label}: AddNewGSDCircPattern REFUSED "
                f"{str(error).splitlines()[0][:120]}")
            continue

        # Update FIRST: an object that updates is a real object, whatever
        # container turns out to accept it.
        try:
            part.UpdateObject(made)
            say(f"{label}: updated WITHOUT being in any container")
            updated = True
        except Exception as error:  # noqa: BLE001 - the question
            say(f"{label}: UpdateObject REFUSED "
                f"{str(error).splitlines()[0][:110]}")
            updated = False
            try:
                part.Update()
            except Exception:  # noqa: BLE001 - expected after a bad node
                pass
            continue

        if updated:
            try:
                made.Name = "GSD_PAT"
                geoset.AppendHybridShape(made)
                part.Update()
                say(f"{label}: and filed in '{geoset.Name}'")
            except Exception as error:  # noqa: BLE001
                say(f"{label}: but AppendHybridShape REFUSED "
                    f"{str(error).splitlines()[0][:100]}")
            # Can the count be edited afterwards? That is the whole advantage.
            for prop, value in (("NbOfCopiesInAngularDir", 8),):
                try:
                    setattr(made, prop, value)
                    part.UpdateObject(made)
                    say(f"{label}: {prop} editable -> {value}")
                except Exception as error:  # noqa: BLE001
                    say(f"{label}: {prop} NOT editable "
                        f"({str(error).splitlines()[0][:70]})")


if __name__ == "__main__":
    main()
