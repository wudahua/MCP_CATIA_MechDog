"""Does AddNewHealing do anything to a SAME-SOURCE seam? Measured, not assumed.

0.3.51 measured healing against a 90 degree fold between two surfaces meeting at
a right angle, and it changed nothing. The 0.3.53 report says that test was the
wrong case: its five seams are "same source sections, geometrically continuous,
only parametrically broken", and asks for that case to be measured before the
tool is refused again.

The arrangement that produces such a seam with these tools: three closed splines
along Z, and TWO lofts sharing the middle one (loft A over sections 1-2, loft B
over 2-3). Each loft arrives at the shared section with its own cross-boundary
tangent, derived from its own other section, so the surfaces meet tangentially at
best - exactly "one geometry, two parametrisations". Then:

  1. join A and B, measure the fold across the shared ring;
  2. AddNewHealing(join) at Continuity 0 / 1 / 2, measure each one.

If healing collapsed a parametrisation break, the seam fold would drop and the
edge count would change. If it does nothing, the numbers stay identical - and
that is worth knowing with the right case measured, not just the wrong one.
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.measurement import MeasurementTools

OUT = os.path.join(tempfile.gettempdir(), "b30_v0355_heal")


def say(text: str = "") -> None:
    print(text, flush=True)


def ring(radius: float, z: float, count: int = 36) -> list[list[float]]:
    return [[round(radius * math.cos(2 * math.pi * i / count), 4),
             round(radius * math.sin(2 * math.pi * i / count), 4), z]
            for i in range(count)]


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)

    for index in range(conn.documents.Count, 0, -1):
        try:
            name = str(conn.documents.Item(index).Name)
        except Exception:  # noqa: BLE001
            continue
        if name.startswith("V0355_"):
            conn.documents.Item(index).Activate()
            document.execute("catia_close_document", {"save": False})

    document.execute("catia_new_part",
                     {"name": "V0355_HEAL",
                      "file_path": os.path.join(OUT, "V0355_HEAL.CATPart")})
    if str(conn.active_document.Name) != "V0355_HEAL.CATPart":
        raise RuntimeError(f"wrong active document: {conn.active_document.Name}")
    part = conn.get_active_part()

    for radius, z, name in ((10.0, 0.0, "H_S1"), (14.0, 20.0, "H_S2"),
                            (18.0, 40.0, "H_S3")):
        gsd.execute("catia_gsd_spline",
                    {"points": ring(radius, z), "closed": True, "name": name})
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["H_S1", "H_S2"], "name": "H_A"})
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["H_S2", "H_S3"], "name": "H_B"})
    gsd.execute("catia_gsd_join", {"elements": ["H_A", "H_B"], "name": "H_J"})
    say("two lofts sharing H_S2, joined as H_J")

    def report(label: str, element: str) -> dict:
        payload = json.loads(measurement.execute(
            "catia_check_surface", {"crease_deg": 10.0}))
        fold = payload["fold_deg"]
        # The seam is the shared ring at z=20, so ask for that band alone.
        seam = json.loads(measurement.execute(
            "catia_check_surface",
            {"crease_deg": 1.0, "region": {"z": [19.0, 21.0]}}))
        seam_fold = seam["fold_deg"]
        # Triangles matter as much as the angles: a heal that quietly DELETED the
        # open ends would also read 0 degrees everywhere, and that is a shape
        # change, not a smooth seam.
        try:
            box = json.loads(measurement.execute("catia_get_bounding_box",
                                                 {"method": "mesh"}))
            dims = box.get("dimensions", {})
            extent = (f"box {dims.get('x', {}).get('size', '?')} x "
                      f"{dims.get('y', {}).get('size', '?')} x "
                      f"{dims.get('z', {}).get('size', '?')}")
        except Exception as error:  # noqa: BLE001
            extent = f"box n/a ({str(error).splitlines()[0][:40]})"
        say(f"   {label:<22} tris {payload['triangles']:>5} "
            f"edges {payload['interior_edges']:>5} boundary {payload['boundary_edges']:>4}  "
            f"all: median {fold['median']} max {fold['max']}")
        say(f"   {'':<22} seam z19-21: edges {seam['interior_edges']:>3} "
            f"median {seam_fold['median']} max {seam_fold['max']}  |  {extent}")
        return payload

    def set_show(name: str, visible: bool) -> None:
        """SetShow(0) shows, (1) hides - measured on B30 2026-10-25."""
        try:
            element = part.FindObjectByName(name)
            if element is None:
                return
            conn.hso.Clear()
            conn.hso.Add(element)
            conn.hso.VisProperties.SetShow(0 if visible else 1)
        except Exception:  # noqa: BLE001 - visibility is best effort here
            pass
        finally:
            try:
                conn.hso.Clear()
            except Exception:  # noqa: BLE001
                pass

    # Measure the join ALONE: three overlapping surfaces in one STL would be a
    # mesh of all three, not of the seam.
    set_show("H_A", False)
    set_show("H_B", False)
    set_show("H_J", True)
    say("\n── before healing (only H_J visible) ──")
    report("joined H_A + H_B", "H_J")

    factory = gsd._factory()
    geoset = gsd._get_geoset()
    for continuity in (0, 1, 2):
        try:
            healed = factory.AddNewHealing(
                part.CreateReferenceFromObject(part.FindObjectByName("H_J")))
            geoset.AppendHybridShape(healed)
            healed.Name = f"H_HEAL_C{continuity}"
            try:
                healed.Continuity = continuity
            except Exception as error:  # noqa: BLE001
                say(f"   Continuity={continuity} not writable: "
                    f"{str(error).splitlines()[0][:70]}")
            part.UpdateObject(healed)
            try:
                length = float((conn.active_document.GetWorkbench("SPAWorkbench")
                                .GetMeasurable(
                                    part.CreateReferenceFromObject(healed))).Length)
                size = f"boundary length {round(length, 3)} mm"
            except Exception:  # noqa: BLE001
                size = "length n/a"
            say(f"\n── heal Continuity={continuity} ({size}) ──")
            for name in ("H_J", "H_A", "H_B"):
                set_show(name, False)
            set_show(f"H_HEAL_C{continuity}", True)
            report(f"heal C{continuity} alone", f"H_HEAL_C{continuity}")
        except Exception as error:  # noqa: BLE001
            say(f"\n   Continuity={continuity}: FAILED at "
                f"{str(error).splitlines()[0][:110]}")

    conn.hso.Clear()
    conn.documents.Item(1).Activate()
    document.execute("catia_close_document", {"save": False})
    say(f"\ndocuments open now: {conn.documents.Count}")


if __name__ == "__main__":
    main()
