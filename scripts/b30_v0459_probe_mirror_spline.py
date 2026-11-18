"""Probe for the 0.3.147 report: the bogus drawn error, and the mirror that adds 2x.

  A  what the API offers at all: ShapeFactory members, the Mirror object, a datum
     plane, a sketch - looking for a union primitive and for a reference query
  B  a body with two solids, one of them symmetric: mirror about xy - volume,
     face count, and whether any union primitive can rescue it
  C  the SAME body when all its material is on one side: what the volume and the
     face count look like there (that is the legitimate delta == before)
  D  a spline on a frame whose u is 1000 mm long: what the drawn error says, and
     what the distance is when the probe point is placed with a UNIT u

.venv/Scripts/python.exe -u scripts/b30_v0459_probe_mirror_spline.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402
from catia_mcp.topo_search import topology_cells  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0459")
RUN = str(int(time.time()))[-5:]


def say(line: str = "") -> None:
    print(line, flush=True)


def fresh(conn, docs, name):
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


def members(obj, label: str) -> list[str]:
    """Every name the object's type library publishes, or the reason it fails."""
    try:
        info = obj._oleobj_.GetTypeInfo()
        attributes = info.GetTypeAttr()
        names: list[str] = []
        for index in range(attributes.cFuncs):
            try:
                names.append(info.GetNames(info.GetFuncDesc(index).memid)[0])
            except Exception:  # noqa: BLE001
                continue
        say(f"  {label}: {len(names)} member(s)")
        return names
    except Exception as error:  # noqa: BLE001
        say(f"  {label}: typeinfo FAILED: {str(error).splitlines()[0][:90]}")
        return []


def volume(conn, part, body) -> float | None:
    try:
        spa = part.Parent.GetWorkbench("SPAWorkbench")
        return float(spa.GetMeasurable(
            part.CreateReferenceFromObject(body)).Volume) * 1e9
    except Exception as error:  # noqa: BLE001
        say(f"  volume FAILED: {str(error).splitlines()[0][:100]}")
        return None


def bbox(conn, part, body) -> list[float] | None:
    try:
        spa = part.Parent.GetWorkbench("SPAWorkbench")
        box = spa.GetMeasurable(part.CreateReferenceFromObject(body)).GetBoundingBox(
            part.CreateReferenceFromObject(conn.get_origin_elements()["xy"]))
        _ = box
        return None
    except Exception:  # noqa: BLE001
        return None


def cells(conn, body, kind: str) -> int:
    try:
        return len(topology_cells(conn, body, kind))
    except Exception as error:  # noqa: BLE001
        say(f"  {kind} count FAILED: {str(error).splitlines()[0][:100]}")
        return -1


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    say(f"probe v0459 on {conn.app.Name}  run {RUN}")

    # ── A: what the API offers ────────────────────────────────────────────
    part = fresh(conn, docs, "ProbeV0459a")
    body = conn.get_active_part_body()
    sf = part.ShapeFactory
    sf_names = members(sf, "ShapeFactory")
    adds = sorted(name for name in sf_names if name.startswith("AddNew"))
    say("  AddNew* : " + ", ".join(adds))
    say("  has Assemble: " + str(any("Assemble" in n for n in adds))
        + " | Boolean: " + str(any("Boolean" in n for n in adds))
        + " | Assemble-ish: " + str([n for n in adds if "ssembl" in n.lower()]))
    say("  datum plane members: " + ", ".join(
        members(part.OriginElements.PlaneXY, "datum plane (OriginElements.PlaneXY)")))
    say("  Body members: " + ", ".join(members(body, "Body")))

    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "A1"})
    say("  sketch members: " + ", ".join(
        members(sketcher._active_sketch, "the open sketch")))
    sketcher.execute("catia_close_sketch", {})

    # ── B: two solids, one symmetric - the mirror that doubles ───────────
    part = fresh(conn, docs, "ProbeV0459b")
    sf = part.ShapeFactory          # the FIRST cell's factory belongs to another part
    body = conn.get_active_part_body()
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "S1"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 20.0, "y2": 20.0})
    sketcher.execute("catia_close_sketch", {})
    say("  B pad.1: " + parts.execute("catia_pad", {"height": 10.0})[:90])
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "S2"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 40.0, "y1": 0.0, "x2": 80.0, "y2": 40.0})
    sketcher.execute("catia_close_sketch", {})
    say("  B pad.2: " + parts.execute("catia_pad", {"height": 20.0,
                                                   "symmetric": True})[:120])
    before = volume(conn, part, body)
    b_faces, b_edges = cells(conn, body, "Face"), cells(conn, body, "Edge")
    say(f"  B before: volume {before!r} mm3, faces {b_faces}, edges {b_edges}")
    ref = part.CreateReferenceFromObject(conn.get_origin_elements()["xy"])
    part.InWorkObject = body
    mirror = sf.AddNewMirror(ref)
    part.Update()
    after = volume(conn, part, body)
    a_faces, a_edges = cells(conn, body, "Face"), cells(conn, body, "Edge")
    say(f"  B after : volume {after!r} mm3, faces {a_faces}, edges {a_edges}, "
        f"delta {None if None in (before, after) else after - before!r}")
    say("  Mirror members: " + ", ".join(members(mirror, "the Mirror feature")))
    try:
        say(f"  B mirror.Plane: {mirror.Plane!r}"[:120])
    except Exception as error:  # noqa: BLE001
        say(f"  B mirror.Plane FAILS: {str(error).splitlines()[0][:90]}")
    for name in ("Mirroring", "MirrorMode", "Mode", "KeepOriginal", "Volume"):
        try:
            say(f"  B mirror.{name} -> {getattr(mirror, name)!r}"[:120])
        except Exception as error:  # noqa: BLE001
            say(f"  B mirror.{name} FAILS")
    # Can a union primitive rescue it?  AddNewAdd / AddNewAssemble exist.
    last = body.Shapes.Item(int(body.Shapes.Count))
    say(f"  B shapes now: {[str(body.Shapes.Item(i).Name) for i in range(1, int(body.Shapes.Count) + 1)]}")
    for candidate in ("AddNewAdd", "AddNewAssemble"):
        if candidate not in sf_names:
            say(f"  B {candidate}: not in the type library")
            continue
        try:
            made = getattr(sf, candidate)(
                part.CreateReferenceFromObject(mirror.rstrip if False else last),
                part.CreateReferenceFromObject(mirror))
            part.Update()
            say(f"  B {candidate}(pad_last, mirror) -> {volume(conn, part, body)!r} "
                f"mm3, faces {cells(conn, body, 'Face')}, "
                f"shapes {[str(body.Shapes.Item(i).Name) for i in range(1, int(body.Shapes.Count) + 1)]}")
            _ = made
        except Exception as error:  # noqa: BLE001
            say(f"  B {candidate} FAILED: {str(error).splitlines()[0][:150]}")

    # ── C: all material on one side - the legitimate doubling ────────────
    part = fresh(conn, docs, "ProbeV0459c")
    sf = part.ShapeFactory
    body = conn.get_active_part_body()
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "T1"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 20.0, "y2": 20.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 10.0})
    before = volume(conn, part, body)
    c_faces = cells(conn, body, "Face")
    ref = part.CreateReferenceFromObject(conn.get_origin_elements()["xy"])
    part.InWorkObject = body
    sf.AddNewMirror(ref)
    part.Update()
    after = volume(conn, part, body)
    say(f"  C one-sided: {before!r} -> {after!r} mm3, faces {c_faces} -> "
        f"{cells(conn, body, 'Face')}")

    # ── D: the spline error on a frame whose u is 1000 mm long ───────────
    part = fresh(conn, docs, "ProbeV0459d")
    sketcher.execute("catia_create_sketch",
                     {"plane": {"type": "frame", "origin": [0.0, 0.0, 0.0],
                                "u": [1000.0, 0.0, 0.0], "v": [0.0, 1000.0, 0.0]},
                      "name": "LONG"})
    reply = sketcher.execute("catia_sketch_spline",
                             {"control_points": [[100.0, 0.0], [100.0, 100.0],
                                                 [0.0, 100.0]],
                              "degree": 2, "weights": [1.0, 0.70710678, 1.0]})
    say("  D long-u frame: " + " ".join(reply.split())[:300])
    placement = getattr(sketcher, "_last_placement", None)
    say(f"  D placement: {placement!r}"[:200])
    sketcher.execute("catia_close_sketch", {})

    # The same curve on a frame with UNIT vectors - the control case.
    part = fresh(conn, docs, "ProbeV0459e")
    sketcher.execute("catia_create_sketch",
                     {"plane": {"type": "frame", "origin": [0.0, 0.0, 0.0],
                                "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0]},
                      "name": "UNIT"})
    reply = sketcher.execute("catia_sketch_spline",
                             {"control_points": [[100.0, 0.0], [100.0, 100.0],
                                                 [0.0, 100.0]],
                              "degree": 2, "weights": [1.0, 0.70710678, 1.0]})
    say("  E unit-u frame: " + " ".join(reply.split())[:300])

    # ── F: the report's own numbers - a body sitting at z ~ 1000 ─────────
    part = fresh(conn, docs, "ProbeV0459f")
    body = conn.get_active_part_body()
    sketcher.execute("catia_create_sketch",
                     {"plane": {"type": "frame", "origin": [0.0, 0.0, 1000.0],
                                "u": [1000.0, 0.0, 0.0], "v": [0.0, 1000.0, 0.0]},
                      "name": "SK_BOX2"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 40.0, "y2": 40.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 20.0, "symmetric": True})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SK_Q"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 100.0, "y2": 100.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 10.0})
    before = volume(conn, part, body)
    say(f"  F before: {before!r} mm3, bbox {bbox(conn, part, body)}")
    reply = parts.execute("catia_mirror", {"plane": "xy", "allow_body_mirror": True})
    after = volume(conn, part, body)
    say("  F mirror reply: " + " ".join(reply.split())[:300])
    say(f"  F after : {after!r} mm3, bbox {bbox(conn, part, body)}, "
        f"faces {cells(conn, body, 'Face')}")

    say("")
    say("documents left open in " + ROOT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
