"""Three questions the 0.3.50 report raises, answered on B30 before any code moves.

    A  can a coordinate point feed a spline WITHOUT being appended to a
       geometrical set? 636 Point features for ten sections is the real cost the
       report describes; `cascade` only cleans up after them.
    B  does HybridShapeFactory really expose AddNewHealing, and does it work?
    C  can the viewer be put into zebra / environment-mapping mode over COM?
    D  what does `delete_feature names[] + cascade` actually cost on 636 points
       (the report budgets 2 s each, i.e. 21 minutes).

Every document this script opens is named PROBE_*, activated by name before it
is touched, and closed with save=false.
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402

OUT = os.path.join(tempfile.gettempdir(), "b30_v0351")
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


def close_probes(document, conn) -> None:
    while True:
        names = []
        for index in range(1, conn.documents.Count + 1):
            try:
                name = str(conn.documents.Item(index).Name)
            except Exception:  # noqa: BLE001
                continue
            if name.startswith("PROBE_"):
                names.append(name)
        if not names:
            return
        activate(conn, names[0])
        document.execute("catia_close_document", {"save": False})


def fresh(name, document, conn):
    for index in range(1, conn.documents.Count + 1):
        if str(conn.documents.Item(index).Name) == f"{name}.CATPart":
            activate(conn, f"{name}.CATPart")
            document.execute("catia_close_document", {"save": False})
    document.execute("catia_new_part",
                     {"name": name, "file_path": os.path.join(OUT, f"{name}.CATPart")})
    activate(conn, f"{name}.CATPart")


def methods_of(obj):
    """Every method name the object's type information knows."""
    try:
        info = obj._oleobj_.GetTypeInfo()
        count = info.GetTypeAttr().cFuncs
        names = []
        for index in range(count):
            try:
                names.append(info.GetDocumentation(index)[0].split("(")[0].strip())
            except Exception:  # noqa: BLE001
                continue
        return names
    except Exception:  # noqa: BLE001
        return []


def main() -> int:
    conn = CATIAConnection()
    say(conn.connect())
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    part_design = PartDesignTools(conn)
    measurement = MeasurementTools(conn)
    close_probes(document, conn)

    def circle_points(radius: float, count: int, z: float = 0.0):
        return [[radius * math.cos(2 * math.pi * i / count),
                 radius * math.sin(2 * math.pi * i / count), z]
                for i in range(count)]

    # ── A. a point that is NOT in the tree ───────────────────────────────
    say("\n── A. coordinate point without AppendHybridShape ──")
    fresh("PROBE_A", document, conn)
    part = conn.get_active_part()
    factory = gsd._factory()
    geoset = gsd._get_geoset()
    say(f"A0 tree elements before: {geoset.HybridShapes.Count}")

    specs = circle_points(30.0, 90)
    refs = []
    started = time.perf_counter()
    for spec in specs:
        point = factory.AddNewPointCoord(spec[0], spec[1], spec[2])
        # deliberately NOT appended
        refs.append(part.CreateReferenceFromObject(point))
    say(f"A1 90 points built, none appended: {(time.perf_counter() - started):.2f} s, "
        f"tree now {geoset.HybridShapes.Count}")

    try:
        spline = factory.AddNewSpline()
        for ref in refs:
            spline.AddPoint(ref)
        spline.SetClosing(1)
        spline.Name = "A_SP"
        part.InWorkObject = spline
        started = time.perf_counter()
        part.UpdateObject(spline)
        say(f"A2 spline through tree-less points: UPDATED in "
            f"{(time.perf_counter() - started):.2f} s")
        try:
            ref = part.CreateReferenceFromObject(spline)
            measurable = conn.active_document.GetWorkbench("SPAWorkbench").GetMeasurable(ref)
            say(f"A3 length {float(measurable.Length):.3f} mm (analytic 188.496)")
        except Exception as error:  # noqa: BLE001
            say(f"A3 length FAILED {str(error).splitlines()[0][:90]}")
        say(f"A4 tree elements after: {geoset.HybridShapes.Count} "
            f"(a tree-less spline is not in the tree either)")
        # Can it be appended afterwards, i.e. is the object still alive?
        try:
            geoset.AppendHybridShape(spline)
            part.UpdateObject(spline)
            say(f"A5 late AppendHybridShape works: tree now "
                f"{geoset.HybridShapes.Count}, name {spline.Name}")
        except Exception as error:  # noqa: BLE001
            say(f"A5 late Append FAILED {str(error).splitlines()[0][:90]}")
    except Exception as error:  # noqa: BLE001
        say(f"A2 spline through tree-less points FAILED: {str(error).splitlines()[0][:130]}")

    # ── B. AddNewHealing ─────────────────────────────────────────────────
    say("\n── B. AddNewHealing ──")
    names = methods_of(factory)
    say(f"B0 HybridShapeFactory methods: {len(names)}")
    say("B0 healing-ish: " + (", ".join(n for n in names
                                        if "Heal" in n or "Curv" in n or "Zebra" in n) or "none"))
    fresh("PROBE_B", document, conn)
    part = conn.get_active_part()
    factory = gsd._factory()
    geoset = gsd._get_geoset()
    for label, kwargs in (("B1 AddNewHealing()", {}),
                          ("B2 AddNewHealing(continuity)", {"continuity": 0})):
        try:
            healing = factory.AddNewHealing(**kwargs) if kwargs else factory.AddNewHealing()
            say(f"{label}: object created ({type(healing).__name__})")
            say("B3   its methods: " + (", ".join(methods_of(healing)) or "none readable"))
            break
        except Exception as error:  # noqa: BLE001
            say(f"{label}: {str(error).splitlines()[0][:130]}")

    # two coaxial cylinders joined, then healing over the C0 seam
    say("\nB4 a real C0 seam: two extruded circles joined, then healed")
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                    "radius": 10.0, "name": "B_C0"})
    gsd.execute("catia_gsd_extrude", {"profile": "B_C0", "direction": [0, 0, 1],
                                      "limit1": 20.0, "name": "B_LOW"})
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 20], "support_plane": "xy",
                                    "radius": 10.0, "name": "B_C1"})
    gsd.execute("catia_gsd_extrude", {"profile": "B_C1", "direction": [0, 0, 1],
                                      "limit1": 20.0, "name": "B_HIGH"})
    try:
        say("B5 " + gsd.execute("catia_gsd_join",
                                {"elements": ["B_LOW", "B_HIGH"], "name": "B_J"}))
    except Exception as error:  # noqa: BLE001
        say(f"B5 join FAILED: {str(error).splitlines()[0][:120]}")
    say(f"B6 tree elements: {geoset.HybridShapes.Count}")

    # ── C. zebra / environment mapping on the viewer ─────────────────────
    say("\n── C. zebra on the viewer ──")
    viewer = conn.active_window.ActiveViewer
    viewer_names = methods_of(viewer)
    say(f"C0 ActiveViewer methods: {len(viewer_names)}")
    say("C0 zebra-ish: " + (", ".join(n for n in viewer_names
                                      if any(k in n.lower() for k in
                                             ("zebra", "environment", "mapping", "analysis",
                                              "material", "render"))) or "none"))
    try:
        props = [n for n in dir(viewer) if not n.startswith("_")]
        say("C1 python-visible attributes: " + ", ".join(props[:30]))
    except Exception as error:  # noqa: BLE001
        say(f"C1 FAILED {str(error).splitlines()[0][:80]}")

    # ── D. what cascade + names[] costs ──────────────────────────────────
    say("\n── D. delete 90 orphan points in one call ──")
    fresh("PROBE_D", document, conn)
    geoset = gsd._get_geoset()
    gsd.execute("catia_gsd_spline", {"points": circle_points(20.0, 90), "name": "D_SP"})
    part = conn.get_active_part()
    total = geoset.HybridShapes.Count
    say(f"D0 after one 90-point spline: {total} elements")
    point_names = [str(geoset.HybridShapes.Item(i).Name)
                   for i in range(1, total + 1)]
    started = time.perf_counter()
    reply = part_design.execute("catia_delete_feature",
                                {"names": point_names, "cascade": True})
    elapsed = time.perf_counter() - started
    say(f"D1 one names[] call over {len(point_names)} elements: {elapsed:.1f} s")
    say("D2 reply: " + reply.replace("\n", " ")[:260])
    say(f"D3 tree now: {gsd._get_geoset().HybridShapes.Count}")

    close_probes(document, conn)
    say(f"\ndocuments open: {conn.documents.Count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
