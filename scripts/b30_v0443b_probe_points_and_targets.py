"""Round 14, second batch: the two P1 claims and the target types P0 needs.

A  catia_gsd_spline with create_points=true - are those Point features left
   behind when the spline is deleted? (The two tool descriptions disagree; the
   first batch measured create_points=FALSE and found nothing left. This batch
   measures the other half, then checks what cascade=true actually removes.)

C  what can be probed at all: a GSD surface (batch 1), a topological Face of a
   solid, and a whole Body. For each: is the closest-point pair readable, and
   what does an INSIDE point read? (Deviation's docstring says 0.0000 for
   "inside a solid", which would make a signed reading impossible for a body.)

    .venv/Scripts/python.exe -u scripts/b30_v0443b_probe_points_and_targets.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.measurement import (read_min_distance_points,  # noqa: E402
                                         MeasurementTools)
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402
from catia_mcp.topo_search import search_topology  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0443b")
CELLS: list[tuple[str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def cell(name: str, ok: bool, detail: str = "") -> None:
    CELLS.append((name, ok, detail))
    say(f"  [{'OK ' if ok else 'NO '}] {name}" + (f"  <- {detail}" if detail else ""))


def fresh(conn, docs, name: str):
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, name + ".CATPart")
    for index in range(int(conn.documents.Count), 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = str(doc.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).lower().startswith(
                os.path.abspath(ROOT).lower()):
            try:
                doc.Close()
            except Exception:  # noqa: BLE001
                pass
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    return conn.get_active_part()


def names_in(part, set_name: str) -> list[str]:
    try:
        shapes = part.HybridBodies.Item(set_name).HybridShapes
    except Exception:  # noqa: BLE001
        return []
    out = []
    for index in range(1, int(shapes.Count) + 1):
        try:
            out.append(str(shapes.Item(index).Name))
        except Exception:  # noqa: BLE001
            continue
    return out


def points_ownership(conn, docs, gsd, parts) -> None:
    say("=" * 78)
    say("A - create_points=TRUE: does the spline own six Point features?")
    part = fresh(conn, docs, "ProbePointsTrue")
    gsd.execute("catia_gsd_create_geoset", {"name": "G_Set"})
    gsd.execute("catia_gsd_spline", {
        "points": [[0, 0, 0], [10, 0, 4], [20, 0, -3], [30, 0, 6],
                   [40, 0, 0], [50, 0, 2]],
        "name": "S_tree", "create_points": True})
    after = names_in(part, "G_Set")
    say(f"  elements after building: {after}")
    cell("A3 create_points=true leaves 1 curve + 6 Point features",
         len(after) == 7, f"{len(after)} elements: {after}")

    reply = parts.execute("catia_delete_feature",
                          {"feature_name": "S_tree", "cascade": False})
    left = names_in(part, "G_Set")
    say(f"  after delete cascade=false: {left}")
    say(f"  reply: {reply[:300]}")
    cell("A4 deleting the curve without cascade leaves the 6 Points",
         len(left) == 6, f"{len(left)} element(s) left: {left}")

    reply = parts.execute("catia_delete_feature", {"names": left, "cascade": True})
    empty = names_in(part, "G_Set")
    say(f"  after cascade delete: {empty}")
    say(f"  reply: {reply[:300]}")
    cell("A5 cascade=true removes them all", not empty, f"left: {empty}")


def build_box(conn, docs, sketcher, parts):
    part = fresh(conn, docs, "ProbeTargets")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0.0, "y1": 0.0,
                                                "x2": 40.0, "y2": 30.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 20.0})
    return part


def targets(conn, docs, sketcher, parts) -> None:
    part = build_box(conn, docs, sketcher, parts)
    app = conn.app
    spa = conn.active_document.GetWorkbench("SPAWorkbench")
    factory = part.HybridShapeFactory
    holder = part.HybridBodies.Add() if int(part.HybridBodies.Count) == 0 else part.HybridBodies.Item(1)
    made = []

    def coord(x, y, z):
        point = factory.AddNewPointCoord(x, y, z)
        holder.AppendHybridShape(point)
        part.UpdateObject(point)
        made.append(point)
        return point

    def pair(target_ref, where):
        point = coord(*where)
        return read_min_distance_points(
            app, spa.GetMeasurable(target_ref),
            part.CreateReferenceFromObject(point))

    say("=" * 78)
    say("C - which targets can be probed? box 40 x 30 x 20 at the origin")
    body = part.MainBody
    sel = conn.hso
    faces = []
    try:
        sel.Clear()
        sel.Add(body)
        search_topology(sel, "Face")
        total = int(sel.Count)
        say(f"  the body exposes {total} faces to a topology search")
        for index in range(1, min(total, 6) + 1):
            faces.append((index, sel.Item(index).Reference))
    finally:
        try:
            sel.Clear()
        except Exception:  # noqa: BLE001
            pass
    for index, ref in faces:
        try:
            on_face, _echo = pair(ref, [20.0, 15.0, 25.0])
            say(f"    Face.{index} closest point to (20,15,25): "
                f"({on_face[0]:.3f}, {on_face[1]:.3f}, {on_face[2]:.3f})")
            if abs(on_face[2] - 20.0) < 0.01:
                cell("C1 a topological Face is probeable (top face at z=20)",
                     True, f"Face.{index} -> z={on_face[2]:.4f}")
        except Exception as error:  # noqa: BLE001
            say(f"    Face.{index}: FAILED {str(error).splitlines()[0][:80]}")
    if not any(name.startswith("C1") for name, _ok, _d in CELLS):
        cell("C1 a topological Face is probeable (top face at z=20)", False,
             "no face answered with z=20")

    say("")
    try:
        body_ref = part.CreateReferenceFromObject(body)
        outside, _ = pair(body_ref, [20.0, 15.0, 25.0])
        cell("C2 body target: point OUTSIDE gives a real closest point",
             abs(outside[2] - 20.0) < 0.01,
             f"({outside[0]:.3f}, {outside[1]:.3f}, {outside[2]:.3f})")
    except Exception as error:  # noqa: BLE001
        cell("C2 body target: point OUTSIDE gives a real closest point", False,
             str(error).splitlines()[0][:110])
    try:
        inside, _ = pair(body_ref, [20.0, 15.0, 10.0])
        probe = coord(20.0, 15.0, 10.0)
        distance = float(spa.GetMeasurable(body_ref).GetMinimumDistance(
            part.CreateReferenceFromObject(probe)))
        cell("C3 body target: point INSIDE - what does it read?", True,
             f"closest point ({inside[0]:.3f}, {inside[1]:.3f}, {inside[2]:.3f}), "
             f"GetMinimumDistance {distance:.4f} mm")
    except Exception as error:  # noqa: BLE001
        cell("C3 body target: point INSIDE - what does it read?", False,
             str(error).splitlines()[0][:110])


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    gsd = GSDTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    say(f"probe v0443b on {conn.app.Name}")
    points_ownership(conn, docs, gsd, parts)
    targets(conn, docs, sketcher, parts)
    say("")
    passed = sum(1 for _n, ok, _d in CELLS if ok)
    say("-" * 78)
    say(f"{passed}/{len(CELLS)} cells reported a capability")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
