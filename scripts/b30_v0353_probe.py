"""B30 probe for the 2026-11-03 (3rd report of the day) claims.

Two questions, both from the reports and both unmeasured so far:

A. "ADJACENT SECTIONS WHOSE ANGULAR FEATURES ARE MISALIGNED MAKE THE
   INTERPOLATED SURFACE TOO TIGHT TO OFFSET BY 0.3 mm." The report builds a
   five-lobe bottle base, shifts the lobes of one ring by 10-12 degrees against
   its neighbours, and the loft builds while thick_surface refuses. Measured
   here on a minimal version of the same arrangement: three lobed rings,
   r(t) = R + 2*cos(5t + phi), phi = 0 on the outer rings and either 0 or 30
   degrees on the middle one. 30 degrees is much larger than the report's
   misalignment, which is the point - if even that thickens, the report's
   mechanism is not reproducible in isolation.

B. the three tools the third report calls P0 and claims are "already in the
   automation API, only not exposed": AddNewFilletBiTangent,
   AddNewSpine, AddNewBoundaryOfSurface. The report reads them out of
   `typeinfo_addnew`, which lists the methods the typelib declares - not the
   ones the installed build will accept. AddNewHealing was declared the same
   way and did nothing (0.3.51), so each is tried here before any tool is
   promised.

Every geometry step asserts the active document first, per the 2026-10-30
accident rule.
"""

from __future__ import annotations

import math
import os
import sys
import tempfile

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.measurement import MeasurementTools
from catia_mcp.tools.part_design import PartDesignTools

OUT = os.path.join(tempfile.gettempdir(), "b30_v0353")
os.makedirs(OUT, exist_ok=True)


def say(text: str = "") -> None:
    print(text, flush=True)


def activate(conn, name: str) -> None:
    """Make ``name`` the active document - and prove it.

    The 2026-10-30 accident was a probe that trusted the active document and
    built three fillets into the user's P12_Fan. Never again: assert here.
    """
    for index in range(1, conn.documents.Count + 1):
        document = conn.documents.Item(index)
        if str(document.Name) == name:
            document.Activate()
            break
    else:
        raise RuntimeError(f"{name} is not open")
    active = str(conn.active_document.Name)
    if active != name:
        raise RuntimeError(f"wanted {name} active, got {active}")


def fresh(conn, document: DocumentTools, name: str) -> None:
    for index in range(conn.documents.Count, 0, -1):
        try:
            existing = str(conn.documents.Item(index).Name)
        except Exception:  # noqa: BLE001 - a dying document, skip it
            continue
        if existing.startswith("V0353_"):
            activate(conn, existing)
            document.execute("catia_close_document", {"save": False})
    path = os.path.join(OUT, f"{name}.CATPart")
    if os.path.exists(path):
        os.remove(path)
    document.execute("catia_new_part", {"name": name, "file_path": path})
    activate(conn, f"{name}.CATPart")


def ring(radius: float, z: float, phase_deg: float, count: int,
         amplitude: float = 2.0, lobes: int = 5) -> list[list[float]]:
    """A lobed circle: r(t) = R + A*cos(lobes*t + phase), at height z."""
    phase = math.radians(phase_deg)
    points = []
    for index in range(count):
        theta = 2 * math.pi * index / count
        r = radius + amplitude * math.cos(lobes * theta + phase)
        points.append([r * math.cos(theta), r * math.sin(theta), z])
    return points


def try_thicken(gsd: GSDTools, surface: str, thickness: float) -> str:
    # thick_surface is a GSD tool, not PartDesign - the 0.3.53 probe found that
    # out the hard way ("Unknown part design tool").
    try:
        gsd.execute("catia_gsd_thick_surface",
                    {"surface": surface, "thickness1": thickness})
        return "OK"
    except Exception as error:  # noqa: BLE001 - the message is the result
        first = str(error).splitlines()[0][:110]
        return f"FAILED ({first})"


def main() -> None:
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    part_design = PartDesignTools(conn)
    measurement = MeasurementTools(conn)

    say("=" * 74)
    say("A. angular misalignment between adjacent sections vs 0.3 mm offset")
    say("=" * 74)
    say(f"{'case':<34}{'loft':<10}{'thicken 0.3':<14}check_surface")

    for tag, phases, count in (
            ("A1 phases 0/0/0", (0.0, 0.0, 0.0), 40),
            ("A2 phases 0/30/0 (lobe shift)", (0.0, 30.0, 0.0), 40),
            ("A3 phases 0/30/0, 90 points", (0.0, 30.0, 0.0), 90),
            ("A4 phases 0/15/0", (0.0, 15.0, 0.0), 40),
    ):
        fresh(conn, document, "V0353_A")
        activate(conn, "V0353_A.CATPart")
        names = []
        for index, (radius, z) in enumerate(((25.0, 0.0), (29.0, 20.0),
                                             (33.0, 40.0))):
            name = f"A_S{index + 1}"
            gsd.execute("catia_gsd_spline",
                        {"points": ring(radius, z, phases[index], count),
                         "closed": True, "name": name})
            names.append(name)
        try:
            gsd.execute("catia_gsd_multi_section_surface",
                        {"sections": names, "name": "A_LOFT"})
            loft = "built"
            verdict = try_thicken(gsd, "A_LOFT", 0.3)
        except Exception as error:  # noqa: BLE001
            loft = "FAILED"
            verdict = f"not tried ({str(error).splitlines()[0][:70]})"
        stats = ""
        if loft == "built":
            try:
                payload = __import__("json").loads(
                    measurement.execute("catia_check_surface",
                                        {"crease_deg": 25.0}))
                fold = payload["fold_deg"]
                stats = f"median {fold['median']} max {fold['max']}"
            except Exception as error:  # noqa: BLE001
                stats = f"n/a ({str(error).splitlines()[0][:50]})"
        else:
            stats = "-"
        say(f"{tag:<34}{loft:<10}{verdict:<14}{stats}")

    # Region filter sanity: the same check restricted to the middle band.
    say("\nA5 region filter on the A2 mesh (report asked for this):")
    try:
        payload = __import__("json").loads(
            measurement.execute("catia_check_surface",
                                {"crease_deg": 25.0, "region": {"z": [10, 30]}}))
        say(f"   z 10..30: median {payload['fold_deg']['median']} "
            f"max {payload['fold_deg']['max']} "
            f"edges {payload['interior_edges']} of "
            f"{payload.get('interior_edges_total')}")
    except Exception as error:  # noqa: BLE001
        say(f"   region filter FAILED ({str(error).splitlines()[0][:90]})")

    say()
    say("=" * 74)
    say("B. are the three P0 factory methods usable, or only declared?")
    say("=" * 74)
    fresh(conn, document, "V0353_B")
    activate(conn, "V0353_B.CATPart")
    part = conn.get_active_part()
    factory = gsd._factory()
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": 10.0, "name": "B_C1"})
    gsd.execute("catia_gsd_extrude", {"profile": "B_C1", "direction": [0, 0, 1],
                                      "limit1": 30.0, "name": "B_S1"})
    gsd.execute("catia_gsd_circle", {"center": [30, 0, 0], "support_plane": "xy",
                                     "radius": 10.0, "name": "B_C2"})
    gsd.execute("catia_gsd_extrude", {"profile": "B_C2", "direction": [0, 0, 1],
                                      "limit1": 30.0, "name": "B_S2"})
    ref1 = part.CreateReferenceFromObject(part.FindObjectByName("B_S1"))
    ref2 = part.CreateReferenceFromObject(part.FindObjectByName("B_S2"))

    for label, call in (
            ("AddNewFilletBiTangent", lambda: factory.AddNewFilletBiTangent(
                ref1, ref2, 5.0, 5.0, 0, 0)),
            ("AddNewFilletTriTangent", lambda: factory.AddNewFilletTriTangent(
                ref1, ref2, ref2, ref2, 5.0, 5.0, 5.0)),
            ("AddNewSpine", lambda: factory.AddNewSpine()),
            ("AddNewBoundaryOfSurface", lambda: factory.AddNewBoundaryOfSurface(
                ref1)),
            ("AddNewBoundaryOfSurface(ref,1)", lambda:
                factory.AddNewBoundaryOfSurface(ref1, 1)),
            ("AddNewExtract", lambda: factory.AddNewExtract(ref1)),
    ):
        try:
            built = call()
            say(f"B {label:<26}: created  "
                f"{type(built).__name__}  "
                f"{getattr(built, 'Name', '?')}")
            try:
                gsd._get_geoset().AppendHybridShape(built)
                part.UpdateObject(built)
                say(f"  {label:<26}  appended + updated OK")
            except Exception as error:  # noqa: BLE001
                say(f"  {label:<26}  append/update FAILED: "
                    f"{str(error).splitlines()[0][:80]}")
        except Exception as error:  # noqa: BLE001
            say(f"B {label:<26}: {str(error).splitlines()[0][:100]}")

    say("\nlast: does extract exist as a TOOL already?")
    tool_names = [tool["name"] for tool in gsd.get_tool_definitions()]
    for name in ("catia_gsd_extract", "catia_gsd_boundary", "catia_gsd_heal"):
        say(f"   {name:<24} {'declared' if name in tool_names else 'NOT a tool'}")

    for index in range(conn.documents.Count, 0, -1):
        try:
            existing = str(conn.documents.Item(index).Name)
        except Exception:  # noqa: BLE001
            continue
        if existing.startswith("V0353_"):
            activate(conn, existing)
            document.execute("catia_close_document", {"save": False})
    say(f"\ndocuments open now: {conn.documents.Count}")


if __name__ == "__main__":
    main()
