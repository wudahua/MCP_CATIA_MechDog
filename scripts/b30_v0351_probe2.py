"""Follow-ups on the 0.3.51 probe, both blocking.

    A  a spline whose points are NOT in the tree: does it survive save + reopen?
       If not, `create_points=false` would ship a part that breaks the moment
       the user closes it, so this decides whether the feature is offered at all.
    B  AddNewHealing's signature: AddNewHealing() said "invalid number of
       arguments" but the method exists, so walk the plausible arities.
    C  zebra: try the attribute names the V5 documentation uses, since the
       viewer refuses both GetTypeInfo and dir().
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
import time

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402

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


def main() -> int:
    conn = CATIAConnection()
    say(conn.connect())
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    close_probes(document, conn)

    # ── A. tree-less points survive save + reopen? ───────────────────────
    say("\n── A. tree-less points across save + reopen ──")
    path = os.path.join(OUT, "PROBE_A1.CATPart")
    for leftover in ("PROBE_A1.CATPart",):
        for index in range(1, conn.documents.Count + 1):
            if str(conn.documents.Item(index).Name) == leftover:
                activate(conn, leftover)
                document.execute("catia_close_document", {"save": False})
    if os.path.exists(path):
        os.remove(path)
    document.execute("catia_new_part", {"name": "PROBE_A1", "file_path": path})
    activate(conn, "PROBE_A1.CATPart")
    part = conn.get_active_part()
    factory = gsd._factory()
    geoset = gsd._get_geoset()

    specs = [[30 * math.cos(2 * math.pi * i / 90),
              30 * math.sin(2 * math.pi * i / 90), 0.0] for i in range(90)]
    refs = []
    for spec in specs:
        point = factory.AddNewPointCoord(*spec)
        refs.append(part.CreateReferenceFromObject(point))   # not appended
    spline = factory.AddNewSpline()
    for ref in refs:
        spline.AddPoint(ref)
    spline.SetClosing(1)
    spline.Name = "A1_SP"
    geoset.AppendHybridShape(spline)      # only the spline goes in the tree
    part.InWorkObject = spline
    started = time.perf_counter()
    part.UpdateObject(spline)
    say(f"A1 spline built+updated: {(time.perf_counter() - started):.2f} s, "
        f"tree {geoset.HybridShapes.Count} element(s)")

    def length_of(name: str) -> str:
        try:
            ref = part.CreateReferenceFromObject(part.FindObjectByName(name))
            measurable = conn.active_document.GetWorkbench("SPAWorkbench").GetMeasurable(ref)
            return f"{float(measurable.Length):.3f} mm"
        except Exception as error:  # noqa: BLE001
            return f"FAILED {str(error).splitlines()[0][:60]}"

    say(f"A2 before save: length {length_of('A1_SP')}")
    say("A3 " + document.execute("catia_save_document", {}))
    activate(conn, "PROBE_A1.CATPart")
    document.execute("catia_close_document", {"save": False})
    say("A4 closed, now reopening from disk")
    try:
        say("A5 " + document.execute("catia_open_document", {"file_path": path}))
    except Exception as error:  # noqa: BLE001
        say(f"A5 reopen FAILED {str(error).splitlines()[0][:120]}")
        close_probes(document, conn)
        return 0
    activate(conn, "PROBE_A1.CATPart")
    part = conn.get_active_part()
    geoset = gsd._get_geoset()
    say(f"A6 after reopen: tree {geoset.HybridShapes.Count} element(s): "
        + ", ".join(str(geoset.HybridShapes.Item(i).Name)
                    for i in range(1, geoset.HybridShapes.Count + 1)))
    say(f"A7 after reopen: length {length_of('A1_SP')}")
    try:
        reopened = part.FindObjectByName("A1_SP")
        started = time.perf_counter()
        part.UpdateObject(reopened)
        say(f"A8 after reopen: UpdateObject ok in {(time.perf_counter() - started):.2f} s")
    except Exception as error:  # noqa: BLE001
        say(f"A8 after reopen: UpdateObject FAILED {str(error).splitlines()[0][:110]}")
    # Reopened parts often need a part-level update first; try that too.
    try:
        started = time.perf_counter()
        part.Update()
        say(f"A9 part.Update() after reopen: {(time.perf_counter() - started):.2f} s")
        say(f"A10 length after part.Update: {length_of('A1_SP')}")
    except Exception as error:  # noqa: BLE001
        say(f"A9 part.Update() FAILED {str(error).splitlines()[0][:110]}")

    # ── B. AddNewHealing signature ───────────────────────────────────────
    say("\n── B. AddNewHealing arities ──")
    document.execute("catia_new_part",
                     {"name": "PROBE_H", "file_path": os.path.join(OUT, "PROBE_H.CATPart")})
    activate(conn, "PROBE_H.CATPart")
    part = conn.get_active_part()
    factory = gsd._factory()
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": 10.0, "name": "H_C0"})
    gsd.execute("catia_gsd_extrude", {"profile": "H_C0", "direction": [0, 0, 1],
                                      "limit1": 20.0, "name": "H_LOW"})
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 20], "support_plane": "xy",
                                     "radius": 10.0, "name": "H_C1"})
    gsd.execute("catia_gsd_extrude", {"profile": "H_C1", "direction": [0, 0, 1],
                                      "limit1": 20.0, "name": "H_HIGH"})
    gsd.execute("catia_gsd_join", {"elements": ["H_LOW", "H_HIGH"], "name": "H_J"})
    part = conn.get_active_part()
    joined = part.CreateReferenceFromObject(part.FindObjectByName("H_J"))
    for label, call in (
        ("B1 (ref)", lambda: factory.AddNewHealing(joined)),
        ("B2 (ref, 1)", lambda: factory.AddNewHealing(joined, 1)),
        ("B3 (ref, 1, 0.001)", lambda: factory.AddNewHealing(joined, 1, 0.001)),
        ("B4 (ref, 0, 0.1)", lambda: factory.AddNewHealing(joined, 0, 0.1)),
        ("B5 (ref, 1, 0.001, False)",
         lambda: factory.AddNewHealing(joined, 1, 0.001, False)),
        ("B6 (ref, ref, 1)", lambda: factory.AddNewHealing(joined, joined, 1)),
    ):
        try:
            healing = call()
            say(f"{label}: CREATED")
            for setter, value in (("Continuity", 1), ("Distance", 0.001),
                                  ("DistanceTolerance", 0.001)):
                try:
                    setattr(healing, setter, value)
                    say(f"   {setter} = {value} accepted")
                except Exception:  # noqa: BLE001
                    pass
            try:
                geoset = gsd._get_geoset()
                geoset.AppendHybridShape(healing)
                healing.Name = "H_HEAL"
                part.InWorkObject = healing
                part.UpdateObject(healing)
                say(f"   appended + UPDATED, tree {geoset.HybridShapes.Count}")
                ref = part.CreateReferenceFromObject(healing)
                measurable = conn.active_document.GetWorkbench("SPAWorkbench").GetMeasurable(ref)
                say(f"   healed area {float(measurable.Area):.3f} mm2")
            except Exception as error:  # noqa: BLE001
                say(f"   append/update FAILED {str(error).splitlines()[0][:110]}")
            break
        except Exception as error:  # noqa: BLE001
            say(f"{label}: {str(error).splitlines()[0][:110]}")

    # ── C. zebra by attribute name ───────────────────────────────────────
    say("\n── C. viewer attributes by name ──")
    viewer = conn.active_window.ActiveViewer
    for candidate in ("EnvironmentMapping", "SetEnvironmentMapping",
                      "Zebra", "SetZebra", "Material", "RenderingMode",
                      "SetRenderingMode", "AnalysisMode", "SetAnalysisMode"):
        try:
            value = getattr(viewer, candidate)
            say(f"C {candidate}: present ({value!r})")
        except Exception as error:  # noqa: BLE001
            say(f"C {candidate}: {str(error).splitlines()[0][:80]}")

    close_probes(document, conn)
    say(f"\ndocuments open: {conn.documents.Count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
