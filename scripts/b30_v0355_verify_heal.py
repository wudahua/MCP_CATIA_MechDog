"""End-to-end: catia_gsd_heal and check_surface(top_creases) through the tools.

The experiments so far drove the raw factory. This drives the tools the way a
caller would, so the numbers in the release note describe the shipped code:
two lofts 0.05 mm apart, healed with merging_distance 0.5 and with 0.001, each
state measured through catia_check_surface.
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.measurement import MeasurementTools

OUT = os.path.join(tempfile.gettempdir(), "b30_v0355_verify")
PREFIX = "V0355_"


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
        if name.startswith(PREFIX):
            conn.documents.Item(index).Activate()
            document.execute("catia_close_document", {"save": False})

    document.execute("catia_new_part",
                     {"name": "V0355_VERIFY",
                      "file_path": os.path.join(OUT, "V0355_VERIFY.CATPart")})
    assert str(conn.active_document.Name) == "V0355_VERIFY.CATPart"
    part = conn.get_active_part()

    for radius, z, name in ((10.0, 0.0, "V_S1"), (14.0, 20.0, "V_S2a"),
                            (18.0, 40.0, "V_S3")):
        gsd.execute("catia_gsd_spline",
                    {"points": ring(radius, z), "closed": True, "name": name})
    gsd.execute("catia_gsd_spline",
                {"points": ring(14.0, 20.05), "closed": True, "name": "V_S2b"})
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["V_S1", "V_S2a"], "name": "V_A"})
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["V_S2b", "V_S3"], "name": "V_B"})
    say("built two lofts 0.05 mm apart (V_A, V_B)")

    def only(targets: tuple[str, ...]) -> None:
        for index in range(1, part.HybridBodies.Count + 1):
            body = part.HybridBodies.Item(index)
            try:
                collection = body.HybridShapes
            except Exception:  # noqa: BLE001
                continue
            for item in range(1, collection.Count + 1):
                shape = collection.Item(item)
                try:
                    conn.hso.Clear()
                    conn.hso.Add(shape)
                    conn.hso.VisProperties.SetShow(
                        0 if str(shape.Name) in targets else 1)
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    try:
                        conn.hso.Clear()
                    except Exception:  # noqa: BLE001
                        pass

    def read(label: str, targets: tuple[str, ...]) -> dict:
        only(targets)
        payload = json.loads(measurement.execute(
            "catia_check_surface",
            {"crease_deg": 5.0, "region": {"z": [19.0, 21.0]}, "top_creases": 12}))
        seam_open = payload["boundary_edges"]
        say(f"   {label:<26} tris {payload['triangles']:>5}  "
            f"interior {payload['interior_edges']:>5}  open {seam_open:>4}  "
            f"seam edges {payload['interior_edges']:>4}  "
            f"median {payload['fold_deg']['median']}  "
            f"max {payload['fold_deg']['max']}  "
            f"listed {payload['worst_creases_shown']}")
        return payload

    say("\n── through the tools ──")
    read("two patches, 0.05 mm gap", ("V_A", "V_B"))

    try:
        reply = gsd.execute("catia_gsd_heal",
                            {"elements": ["V_A", "V_B"], "continuity": 1,
                             "merging_distance": 0.5, "name": "V_H1"})
        say("   heal 0.5: " + reply.splitlines()[0][:120])
    except Exception as error:  # noqa: BLE001
        say(f"   heal 0.5 FAILED: {str(error).splitlines()[0][:120]}")
    read("healed, merging 0.5", ("V_H1",))

    try:
        reply = gsd.execute("catia_gsd_heal",
                            {"elements": ["V_A", "V_B"], "continuity": 1,
                             "merging_distance": 0.001, "name": "V_H2"})
        say("   heal 0.001: " + reply.splitlines()[0][:120])
    except Exception as error:  # noqa: BLE001
        say(f"   heal 0.001 FAILED: {str(error).splitlines()[0][:120]}")
    read("healed, merging 0.001", ("V_H2",))

    say("\n── validation the tool does itself ──")
    for args in ({"elements": []}, {"elements": ["V_A"], "continuity": 7},
                 {"elements": ["V_A"], "merging_distance": 0}):
        try:
            gsd.execute("catia_gsd_heal", args)
            say(f"   {args}: accepted (should not be)")
        except Exception as error:  # noqa: BLE001
            say(f"   {args}: {getattr(error, 'code', '?')} / "
                f"{getattr(error, 'stage', '?')}")

    for index in range(conn.documents.Count, 0, -1):
        try:
            name = str(conn.documents.Item(index).Name)
        except Exception:  # noqa: BLE001
            continue
        if name.startswith(PREFIX):
            conn.documents.Item(index).Activate()
            document.execute("catia_close_document", {"save": False})
    say(f"\ndocuments open now: {conn.documents.Count}")


if __name__ == "__main__":
    main()
