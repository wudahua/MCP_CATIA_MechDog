"""What actually breaks a join: same-name rebuilds, a different curve object, or
a section sampled too densely?

scripts/b30_v0352_reloft.py rebuilt lofts four different ways and every join
succeeded, which leaves the 0.3.50 report's freeze unexplained. Two candidates
remain, and both are testable:

    A  the two lofts reference DIFFERENT curve objects that happen to be
       geometrically identical (two circles with the same parameters, or a
       section deleted and rebuilt). Join needs a SHARED EDGE; if it compares
       curve identity rather than coincidence, this fails while the geometry is
       perfect.
    B  the sections are sampled so densely that the surface's border degenerates
       - the report's §4 says 130 points at 0.58 deg built a loft that would not
       join, against 90 points at 4 deg.
    C  a same-name rebuild silently produces a SECOND element with that name
       (seen in the first probe: two A_P12), so a later join by name can pick
       either one.
"""

from __future__ import annotations

import math
import os
import sys
import tempfile

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402

OUT = os.path.join(tempfile.gettempdir(), "b30_v0352")
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


def close_prefix(document, conn, prefix="Q_") -> None:
    while True:
        names = []
        for index in range(1, conn.documents.Count + 1):
            try:
                name = str(conn.documents.Item(index).Name)
            except Exception:  # noqa: BLE001
                continue
            if name.startswith(prefix):
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
    part_design = PartDesignTools(conn)
    close_prefix(document, conn)

    def fresh(name: str):
        path = os.path.join(OUT, f"{name}.CATPart")
        for index in range(1, conn.documents.Count + 1):
            if str(conn.documents.Item(index).Name) == f"{name}.CATPart":
                activate(conn, f"{name}.CATPart")
                document.execute("catia_close_document", {"save": False})
        if os.path.exists(path):
            os.remove(path)
        document.execute("catia_new_part", {"name": name, "file_path": path})
        activate(conn, f"{name}.CATPart")

    def circle(name, x, radius):
        gsd.execute("catia_gsd_circle", {"center": [x, 0, 0], "support_plane": "yz",
                                         "radius": radius, "name": name})

    def ring_points(radius, count, z):
        return [[radius * math.cos(2 * math.pi * i / count),
                 radius * math.sin(2 * math.pi * i / count), z]
                for i in range(count)]

    def attempt(label, elements, name="J"):
        try:
            reply = gsd.execute("catia_gsd_join", {"elements": elements, "name": name})
            say(f"{label}: OK - {reply[:70]}")
            return "ok"
        except Exception as error:  # noqa: BLE001
            say(f"{label}: FAILED - {str(error).splitlines()[0][:130]}")
            return "failed"

    # ── A. two circles with identical parameters, distinct objects ────────
    say("\n── A. same geometry, different curve objects ──")
    fresh("Q_A")
    circle("A_C1", 0, 10.0)
    circle("A_C2", 20, 14.0)
    circle("A_C2b", 20, 14.0)        # identical to A_C2, but its own object
    circle("A_C3", 40, 18.0)
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["A_C1", "A_C2"], "name": "A_P12"})
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["A_C2b", "A_C3"], "name": "A_P23"})
    attempt("A1 join(P12 via C2, P23 via C2b)", ["A_P12", "A_P23"])

    fresh("Q_B")
    circle("B_C1", 0, 10.0)
    circle("B_C2", 20, 14.0)
    circle("B_C3", 40, 18.0)
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["B_C1", "B_C2"], "name": "B_P12"})
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["B_C2", "B_C3"], "name": "B_P23"})
    attempt("A2 join both via the same B_C2 object", ["B_P12", "B_P23"])

    # ── B. sampling density vs joinability ───────────────────────────────
    say("\n── B. section sampling density: loft AND join ──")
    for count in (60, 90, 120, 180, 600):
        fresh(f"Q_S{count}")
        tag = f"S{count}"
        for radius, z, suffix in ((10.0, 0.0, "s1"), (14.0, 20.0, "s2"),
                                  (18.0, 40.0, "s3")):
            gsd.execute("catia_gsd_spline",
                        {"points": ring_points(radius, count, z), "closed": True,
                         "name": f"{tag}_{suffix}"})
        status = []
        for pair, piece in ((("s1", "s2"), "P12"), (("s2", "s3"), "P23")):
            try:
                gsd.execute("catia_gsd_multi_section_surface",
                            {"sections": [f"{tag}_{pair[0]}", f"{tag}_{pair[1]}"],
                             "name": f"{tag}_{piece}"})
                status.append(f"{piece} ok")
            except Exception as error:  # noqa: BLE001
                status.append(f"{piece} FAILED {str(error).splitlines()[0][:60]}")
        step = 360.0 / count
        arc = 2 * math.pi * 28.0 / count
        joined = "not attempted"
        if all(" ok" in item for item in status):
            joined = attempt(f"B{count} join", [f"{tag}_P12", f"{tag}_P23"],
                             f"{tag}_J")
        say(f"B {count:>3} points ({step:.2f} deg, {arc:.2f} mm arc at r=28): "
            f"{'; '.join(status)}; join {joined}")

    # ── C. does a same-name rebuild leave two elements? ──────────────────
    say("\n── C. rebuilding under an existing name ──")
    fresh("Q_C")
    circle("C_C1", 0, 10.0)
    circle("C_C2", 20, 14.0)
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["C_C1", "C_C2"], "name": "C_P"})
    geoset = gsd._get_geoset()
    say("C1 tree: " + ", ".join(str(geoset.HybridShapes.Item(i).Name)
                                for i in range(1, geoset.HybridShapes.Count + 1)))
    say("C2 rebuild with the SAME name, without deleting first:")
    say("   " + gsd.execute("catia_gsd_multi_section_surface",
                            {"sections": ["C_C1", "C_C2"], "name": "C_P"})[:120])
    geoset = gsd._get_geoset()
    names = [str(geoset.HybridShapes.Item(i).Name)
             for i in range(1, geoset.HybridShapes.Count + 1)]
    say("C3 tree: " + ", ".join(names))
    say(f"C4 elements named 'C_P': {names.count('C_P')}")
    part = conn.get_active_part()
    try:
        found = part.FindObjectByName("C_P")
        say(f"C5 FindObjectByName('C_P') -> {found.Name!r}")
    except Exception as error:  # noqa: BLE001
        say(f"C5 FindObjectByName('C_P') FAILED {str(error).splitlines()[0][:80]}")
    say("C6 delete by name: " + part_design.execute("catia_delete_feature",
                                                    {"feature_name": "C_P"}))
    geoset = gsd._get_geoset()
    say("C7 tree after one delete: " + ", ".join(
        str(geoset.HybridShapes.Item(i).Name)
        for i in range(1, geoset.HybridShapes.Count + 1)))

    close_prefix(document, conn)
    say(f"\ndocuments open: {conn.documents.Count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
