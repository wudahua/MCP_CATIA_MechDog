"""Can a TOPOLOGICAL edge be sampled, and can its vertices be read?

The 2026-11-13 report (13th round, P1-5) asked for `list_edges(coords=true,
samples=N)`. The answer recorded in this project's notes was "no: a topological
edge cannot be sampled", but that conclusion lives only in a note - there is no
measurement in the code and no ceiling entry - so it gets re-measured here before
it is written down as a limit.

Four routes, each in its own fresh document where it builds anything:

    A  Edge.GetCurve()      -> a Curve to put points on at a ratio
    B  Edge.GetVertices()   -> the two endpoints of the edge
    C  Edge.GetFaces()      -> how many faces bound it (P2-2's other half)
    D  AddNewPointOnCurveFromPercent(Edge, t, 0)  + UpdateObject
    E  AddNewPointOnCurveFromPercent(Curve, t, 0) + UpdateObject   (after A)

    .venv/Scripts/python.exe -u scripts/b30_v0431_probe_edge_samples.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402
from catia_mcp.topo_search import topology_cells  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0431e")
ROWS: list[tuple[str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def cell(what: str, ok: bool, detail: str = "") -> None:
    ROWS.append((what, ok, detail))
    say(f"  [{'OK  ' if ok else 'FAIL'}] {what}" + (f"  -> {detail}" if detail else ""))


def close_ours(conn) -> None:
    root = os.path.abspath(ROOT).lower()
    for index in range(int(conn.documents.Count), 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = str(doc.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).lower().startswith(root):
            try:
                doc.Close()
            except Exception:  # noqa: BLE001
                pass


def fresh(conn, docs, sketcher, parts, name: str):
    """A 60 x 40 x 10 plate: 12 edges, all straight, all with two faces."""
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, name + ".CATPart")
    close_ours(conn)
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 60, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 10})
    return conn.get_active_part()


def fresh_circle(conn, docs, sketcher, parts, name: str):
    """A padded circle r=10, h=10: two circular rims, no straight edges."""
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, name + ".CATPart")
    close_ours(conn)
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": 10})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 10})
    return conn.get_active_part()


def edge_of(conn, part, index: int = 1):
    """(the edge OBJECT, its Reference) - Edge.N as the tools mean it.

    `topology_cells` gives a Selection item; the item answers `.Reference` but not
    `GetCurve`, so the object behind it has to be taken out first. That is exactly
    the difference between "the edge cannot tell you" and "the wrapper cannot" -
    the first version of this probe measured the wrapper and would have recorded
    a false limit.
    """
    from catia_mcp.topo_search import topology_cells  # noqa: PLC0415
    item = topology_cells(conn, part.MainBody, "Edge")[index - 1]
    value = getattr(item, "Value", None)
    return value if value is not None else item, item


def main() -> int:
    conn = CATIAConnection()
    conn.ensure_connected()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    measure = MeasurementTools(conn)

    say("A/B/C - what the edge itself answers (read-only, one document)")
    part = fresh(conn, docs, sketcher, parts, "V0431_EdgeRead")
    edge, item = edge_of(conn, part)
    say(f"  edge = {getattr(edge, 'Name', '?')}, type {type(edge).__name__}")

    curve = None
    try:
        curve = edge.GetCurve()
        cell("A Edge.GetCurve()", True, f"{type(curve).__name__}")
    except Exception as error:  # noqa: BLE001
        cell("A Edge.GetCurve()", False, str(error).splitlines()[0][:120])

    try:
        vertices = edge.GetVertices()
        count = int(vertices.Count)
        points = []
        for i in range(1, count + 1):
            vertex = vertices.Item(i)
            try:
                p = conn.app.SystemService.Evaluate(
                    "Public Function catia_mcp_vp(v)\n"
                    "    Dim a(2)\n"
                    "    v.GetPoint a\n"
                    "    catia_mcp_vp = a\n"
                    "End Function\n", 0, "catia_mcp_vp", [vertex])
                points.append([round(float(v), 4) for v in p])
            except Exception as error:  # noqa: BLE001
                points.append(f"GetPoint: {str(error).splitlines()[0][:60]}")
        cell("B Edge.GetVertices() + Vertex.GetPoint",
             count == 2 and all(isinstance(p, list) for p in points),
             f"{count} vertices, {points}")
    except Exception as error:  # noqa: BLE001
        cell("B Edge.GetVertices()", False, str(error).splitlines()[0][:120])

    try:
        faces = edge.GetFaces()
        cell("C Edge.GetFaces()", True, f"{int(faces.Count)} face(s)")
    except Exception as error:  # noqa: BLE001
        cell("C Edge.GetFaces()", False, str(error).splitlines()[0][:120])

    say("")
    say("D - a point on the EDGE reference itself (own document)")
    fresh(conn, docs, sketcher, parts, "V0431_EdgePoint")
    part = conn.get_active_part()
    edge, item = edge_of(conn, part)
    factory = part.HybridShapeFactory
    try:
        point = factory.AddNewPointOnCurveFromPercent(edge, 0.25, 0)
        part.UpdateObject(point)
        cell("D AddNewPointOnCurveFromPercent(Edge object, 0.25)", True, "update ok")
    except Exception as error:  # noqa: BLE001
        cell("D AddNewPointOnCurveFromPercent(Edge object, 0.25)", False,
             str(error).splitlines()[0][:140])
    try:
        ref = item.Reference
        point = factory.AddNewPointOnCurveFromPercent(ref, 0.25, 0)
        part.UpdateObject(point)
        cell("D2 AddNewPointOnCurveFromPercent(Edge Reference, 0.25)", True,
             "update ok")
    except Exception as error:  # noqa: BLE001
        cell("D2 AddNewPointOnCurveFromPercent(Edge Reference, 0.25)", False,
             str(error).splitlines()[0][:140])

    say("")
    say("E - a point on the CURVE the edge answers (own document)")
    fresh(conn, docs, sketcher, parts, "V0431_CurvePoint")
    part = conn.get_active_part()
    edge, item = edge_of(conn, part)
    factory = part.HybridShapeFactory
    try:
        curve = edge.GetCurve()
        point = factory.AddNewPointOnCurveFromPercent(curve, 0.25, 0)
        part.UpdateObject(point)
        # Where did it land? A 60 mm edge sampled at 25 % is 15 mm along it.
        cog = conn.app.SystemService.Evaluate(
            "Public Function catia_mcp_pc(m)\n"
            "    Dim a(2)\n"
            "    m.GetCOG a\n"
            "    catia_mcp_pc = a\n"
            "End Function\n", 0, "catia_mcp_pc",
            [part.GetMeasurable(point)])
        cell("E AddNewPointOnCurveFromPercent(Curve, 0.25)", True,
             f"point at {[round(float(v), 3) for v in cog]}")
    except Exception as error:  # noqa: BLE001
        cell("E AddNewPointOnCurveFromPercent(Curve, 0.25)", False,
             str(error).splitlines()[0][:140])

    say("")
    say("F - five samples along ONE edge: do they sit where the ratio says?")
    fresh(conn, docs, sketcher, parts, "V0431_FiveSamples")
    part = conn.get_active_part()
    spa = part.Parent.GetWorkbench("SPAWorkbench")
    edge, item = edge_of(conn, part)
    factory = part.HybridShapeFactory
    try:
        from catia_mcp.tools.measurement import _BATCH_POINTS  # noqa: PLC0415
        made = []
        for ratio in (0.0, 0.25, 0.5, 0.75, 1.0):
            probe = factory.AddNewPointOnCurveFromPercent(edge, ratio, 0)
            part.UpdateObject(probe)
            made.append(probe)
        refs = [part.CreateReferenceFromObject(probe) for probe in made]
        raw = conn.app.SystemService.Evaluate(_BATCH_POINTS, 0,
                                              "catia_mcp_points", [spa, refs])
        values = [float(v) for v in raw]
        coords = [[round(values[3 * i + k], 4) for k in range(3)]
                  for i in range(len(made))]
        gaps = [round(sum((coords[i + 1][k] - coords[i][k]) ** 2
                          for k in range(3)) ** 0.5, 4)
                for i in range(len(coords) - 1)]
        evenly = all(abs(gap - gaps[0]) < 1e-2 for gap in gaps) if gaps else False
        cell("F five points at 0/25/50/75/100 % of one edge",
             evenly and all(gap > 1e-6 for gap in gaps),
             f"{coords}, gaps {gaps}")
    except Exception as error:  # noqa: BLE001
        cell("F five points at 0/25/50/75/100 % of one edge", False,
             str(error).splitlines()[0][:160])

    say("")
    say("H - a CURVED rim: pad a circle and put a point on its edge")
    fresh_circle(conn, docs, sketcher, parts, "V0431_CircleRim")
    part = conn.get_active_part()
    factory = part.HybridShapeFactory
    try:
        rims = topology_cells(conn, part.MainBody, "Edge")
        done = 0
        for candidate in rims:
            value = getattr(candidate, "Value", None)
            if value is None:
                continue
            try:
                probe = factory.AddNewPointOnCurveFromPercent(value, 0.25, 0)
                part.UpdateObject(probe)
                done += 1
            except Exception:  # noqa: BLE001
                continue
        cell("H a circular rim accepts a point at 25 %", done == len(rims) and done > 0,
             f"{done}/{len(rims)} edge(s)")
    except Exception as error:  # noqa: BLE001
        cell("H a circular rim accepts a point at 25 %", False,
             str(error).splitlines()[0][:160])

    say("")
    say("I - an ARC edge (a filleted corner rim): open, but not straight")
    fresh(conn, docs, sketcher, parts, "V0431_ArcEdge")
    part = conn.get_active_part()
    fillet = parts.execute("catia_fillet", {"radius": 5})
    say("  " + " ".join(str(fillet).split())[:150])
    part = conn.get_active_part()
    factory = part.HybridShapeFactory
    try:
        edges = topology_cells(conn, part.MainBody, "Edge")
        accepted, refused = 0, []
        for candidate in edges:
            value = getattr(candidate, "Value", None)
            if value is None:
                continue
            try:
                probe = factory.AddNewPointOnCurveFromPercent(value, 0.5, 0)
                part.UpdateObject(probe)
                accepted += 1
            except Exception:  # noqa: BLE001
                refused.append(getattr(candidate, "Name", "?"))
        cell("I every edge of a filleted plate accepts a mid-point",
             accepted == len(edges) and accepted > 0,
             f"{accepted}/{len(edges)} edges" + (f", refused {refused[:4]}" if refused else ""))
    except Exception as error:  # noqa: BLE001
        cell("I every edge of a filleted plate accepts a mid-point", False,
             str(error).splitlines()[0][:160])

    say("")
    say("-" * 70)
    say(f"{sum(1 for _, ok, _ in ROWS if ok)}/{len(ROWS)} cells opened")
    close_ours(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
