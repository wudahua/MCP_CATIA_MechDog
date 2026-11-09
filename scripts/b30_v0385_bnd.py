"""0.3.85 probe, part 1: does AddNewBoundary take a loop selector?

The report asks for "pick one loop out of a multi-loop border". The only
candidate in either factory is ``AddNewBoundary(iInitialElement, iSupport,
iTypedePropagation)`` - three arguments against ``AddNewBoundaryOfSurface``'s
one. Whether the third one is a propagation along a tangent chain (useless here:
the two loops do not touch) or a loop selector is the question.

A CONE is the discriminator, not a cylinder: its two border circles have
different lengths (2*pi*10 = 62.832 at the bottom, 2*pi*20 = 125.664 at the
top), so the length of whatever comes back says WHICH loop it is. On a cylinder
both loops measure the same and "it worked" would prove nothing.

Every combination runs in its own fresh document. A feature that fails to update
leaves the part in an error state, and every later Update in that document then
fails too - which is how a previous probe turned six independent questions into
six copies of its first failure.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.topo_search import search_topology  # noqa: E402

ROOT = r"F:\catia_probe_v0385\bnd"

BOTTOM = 62.83185307      # 2*pi*10
TOP = 125.6637061         # 2*pi*20
BOTH = BOTTOM + TOP


def say(line: str = "") -> None:
    print(line, flush=True)


def length_of(conn, part, reference) -> float | None:
    try:
        spa = conn.active_document.GetWorkbench("SPAWorkbench")
        return float(spa.GetMeasurable(reference).Length)
    except Exception:  # noqa: BLE001
        return None


def area_mm2(conn, part, reference) -> float | None:
    """Area in mm2. Measurable.Area answers in m2 on this build."""
    try:
        spa = conn.active_document.GetWorkbench("SPAWorkbench")
        return float(spa.GetMeasurable(reference).Area) * 1e6
    except Exception:  # noqa: BLE001
        return None


def identify(length: float | None) -> str:
    if length is None:
        return "no length"
    for label, expected in (("BOTTOM r=10", BOTTOM), ("TOP r=20", TOP),
                            ("BOTH loops", BOTH)):
        if abs(length - expected) < 0.01:
            return label
    return f"unexpected ({length:.4f})"


def fresh(conn, tag: str):
    """A new in-memory part with one cone, and the pieces the probe needs."""
    docs = conn.documents
    doc = docs.Add("Part")
    part = doc.Part
    hsf = part.HybridShapeFactory
    geoset = part.HybridBodies.Add()
    geoset.Name = "GS"

    # A cone: a slanted line revolved about the z axis. Bottom r=10, top r=20,
    # height 30 - so its two border circles have DIFFERENT lengths, which is the
    # whole point: on a cylinder both measure 62.832 and "it worked" proves
    # nothing about which loop came back.
    p_bottom = hsf.AddNewPointCoord(10.0, 0.0, 0.0)
    p_top = hsf.AddNewPointCoord(20.0, 0.0, 30.0)
    a_bottom = hsf.AddNewPointCoord(0.0, 0.0, -10.0)
    a_top = hsf.AddNewPointCoord(0.0, 0.0, 40.0)
    for point in (p_bottom, p_top, a_bottom, a_top):
        geoset.AppendHybridShape(point)
    line = hsf.AddNewLinePtPt(part.CreateReferenceFromObject(p_bottom),
                              part.CreateReferenceFromObject(p_top))
    geoset.AppendHybridShape(line)
    line.Name = f"GEN_{tag}"
    part.UpdateObject(line)
    axis = hsf.AddNewLinePtPt(part.CreateReferenceFromObject(a_bottom),
                              part.CreateReferenceFromObject(a_top))
    geoset.AppendHybridShape(axis)
    axis.Name = f"AXIS_{tag}"
    part.UpdateObject(axis)

    cone = hsf.AddNewRevol(part.CreateReferenceFromObject(line), 360.0, 0.0,
                           part.CreateReferenceFromObject(axis))
    geoset.AppendHybridShape(cone)
    cone.Name = f"CONE_{tag}"
    part.UpdateObject(cone)
    part.Update()
    return doc, part, hsf, geoset, part.FindObjectByName(f"CONE_{tag}")


def edges_of(conn, part, geoset) -> list:
    """Every edge the geometrical set exposes, with its reference kept alive.

    The reference belongs to the selection, so the selection is NOT cleared
    while these are in flight: a cleared reference makes a factory call answer
    "method failed" with no explanation (documented in topo_search).
    """
    sel = conn.hso
    sel.Clear()
    sel.Add(geoset)
    search_topology(sel, "Edge")
    found = []
    for i in range(1, int(sel.Count) + 1):
        try:
            found.append((i, sel.Item(i).Reference))
        except Exception:  # noqa: BLE001
            continue
    return found


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    # Nothing is saved: these documents live in the session only, so a wrong
    # guess cannot leave a file that a later run measures by accident.

    say("=== baseline: boundary of the cone ===")
    doc, part, hsf, geoset, cone = fresh(conn, "base")
    boundary = hsf.AddNewBoundaryOfSurface(
        part.CreateReferenceFromObject(cone))
    geoset.AppendHybridShape(boundary)
    boundary.Name = "CONE_BORDER"
    part.UpdateObject(boundary)
    part.Update()
    base = length_of(conn, part, part.CreateReferenceFromObject(boundary))
    say(f"  AddNewBoundaryOfSurface(CONE) length = {base:.4f} "
        f"-> {identify(base)}")
    doc.Close()

    say("")
    say("=== A. edges of the cone ===")
    doc, part, hsf, geoset, cone = fresh(conn, "edges")
    edges = edges_of(conn, part, geoset)
    say(f"  {len(edges)} edge(s) selected on the geometrical set")
    for index, reference in edges:
        length = length_of(conn, part, reference)
        say(f"  Edge.{index}: {length} mm -> {identify(length)}")
    doc.Close()

    say("")
    say("=== B. AddNewBoundary(edge, cone, iTypedePropagation) ===")
    # One document per combination: the first refusal leaves a part that refuses
    # every later Update, which would hide the answers to the rest.
    for label, wanted in (("bottom", "BOTTOM"), ("top", "TOP")):
        for propagation in (0, 1, 2):
            doc, part, hsf, geoset, cone = fresh(conn, f"{label}{propagation}")
            edges = edges_of(conn, part, geoset)
            # Pick the edge by length rather than by index: the index depends on
            # the order CATIA searches, the length is the geometry.
            target = None
            for index, reference in edges:
                length = length_of(conn, part, reference)
                if length is None:
                    continue
                near = BOTTOM if wanted == "BOTTOM" else TOP
                if abs(length - near) < 0.01:
                    target = reference
                    break
            if target is None:
                say(f"  {label} propagation={propagation}: no {wanted} edge found")
                doc.Close()
                continue
            try:
                feature = hsf.AddNewBoundary(
                    target, part.CreateReferenceFromObject(cone), propagation)
                geoset.AppendHybridShape(feature)
                feature.Name = f"BND_{label}_{propagation}"
                part.UpdateObject(feature)
                part.Update()
                length = length_of(conn, part,
                                   part.CreateReferenceFromObject(feature))
                say(f"  {label} propagation={propagation}: "
                    f"{length} mm -> {identify(length)}")
            except Exception as error:  # noqa: BLE001 - the refusal is the data
                say(f"  {label} propagation={propagation}: REFUSED "
                    f"{str(error).splitlines()[0][:150]}")
            finally:
                try:
                    doc.Close()
                except Exception:  # noqa: BLE001
                    pass


if __name__ == "__main__":
    main()
