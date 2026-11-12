"""What does AddNewHealing actually do, and is there a Match instead?

v2 showed it changes nothing on a G0 fold: a 90 deg join stays a 90 deg join at
Continuity 1 and at 2, same triangle count, same dihedral. So either the object
needs settings that were silently refused, or CATIA's Healing is not the tool
for a fold.

    A  list every AddNew* the factory answers, to see what ELSE is there
       (Match, Fillet, Blend, Connect, ...)
    B  healing with each of the settings the object may want, checking whether
       the setter stuck before and after UpdateObject
    C  healing across a GAP: two planes 0.2 mm apart, not joined
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402

OUT = os.path.join(tempfile.gettempdir(), "b30_v0351")


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
    os.makedirs(OUT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    close_probes(document, conn)
    document.execute("catia_new_part",
                     {"name": "PROBE_M", "file_path": os.path.join(OUT, "PROBE_M.CATPart")})
    activate(conn, "PROBE_M.CATPart")
    part = conn.get_active_part()
    factory = gsd._factory()

    # ── A. what AddNew* methods exist ────────────────────────────────────
    say("\n── A. probing AddNew* by name (type info is unavailable on this COM) ──")
    guesses = [
        "Healing", "Match", "Fillet", "Blend", "Connect", "CurveSmooth",
        "SurfacicFillet", "Zebra", "CurvatureAnalysis", "EnvironmentMapping",
        "ParallelCurve", "Fill", "Extrapol", "TritangentFillet",
        "Assemble", "Simplify", "HealingOptimizer",
    ]
    present = {}
    for guess in guesses:
        name = "AddNew" + guess
        try:
            method = getattr(factory, name)
            present[guess] = "present"
            say(f"A {name}: present")
        except Exception as error:  # noqa: BLE001
            present[guess] = str(error).splitlines()[0][:60]
    say("A summary: " + json.dumps({k: v for k, v in present.items() if v == "present"}))

    # ── B. which settings does a Healing accept, and do they stick? ──────
    say("\n── B. Healing settings ──")
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [40, 0, 0],
                                   "name": "M_L"})
    gsd.execute("catia_gsd_extrude", {"profile": "M_L", "direction": [0, 1, 0],
                                      "limit1": 40.0, "name": "M_FLAT"})
    gsd.execute("catia_gsd_extrude", {"profile": "M_L", "direction": [0, 0, 1],
                                      "limit1": 40.0, "name": "M_UP"})
    gsd.execute("catia_gsd_join", {"elements": ["M_FLAT", "M_UP"], "name": "M_J"})
    part = conn.get_active_part()
    joined = part.CreateReferenceFromObject(part.FindObjectByName("M_J"))
    healing = factory.AddNewHealing(joined)
    for field in ("Continuity", "Distance", "DistanceTolerence", "DistanceTolerance",
                  "Connexity", "Manifold", "Tolerance", "MergeDistance",
                  "CorrectionMode", "ContinuityType"):
        try:
            current = getattr(healing, field)
            say(f"B {field}: readable = {current!r}")
        except Exception:  # noqa: BLE001
            say(f"B {field}: not readable")
            continue
        for value in (1, 0.001):
            try:
                setattr(healing, field, value)
                after = getattr(healing, field)
                say(f"B   set {field}={value} -> reads back {after!r}"
                    + ("  STUCK" if str(after) != str(current) else "  UNCHANGED"))
                break
            except Exception as error:  # noqa: BLE001
                say(f"B   set {field}={value} refused: {str(error).splitlines()[0][:60]}")

    # ── C. can a Match do what the report wants? ─────────────────────────
    say("\n── C. AddNewMatch on the same two planes ──")
    try:
        match = factory.AddNewMatch(joined, joined)
        say(f"C1 AddNewMatch(two refs): created")
        say("C2 " + ", ".join(a for a in dir(match) if not a.startswith("_"))[:200])
    except Exception as error:  # noqa: BLE001
        say(f"C1 AddNewMatch(two refs): {str(error).splitlines()[0][:120]}")
    for label, args in (("C3 (ref, ref, 1)", (joined, joined, 1)),
                        ("C4 (ref, ref, 1, 1)", (joined, joined, 1, 1))):
        try:
            match = factory.AddNewMatch(*args)
            say(f"{label}: created")
            break
        except Exception as error:  # noqa: BLE001
            say(f"{label}: {str(error).splitlines()[0][:120]}")

    close_probes(document, conn)
    say(f"\ndocuments open: {conn.documents.Count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
