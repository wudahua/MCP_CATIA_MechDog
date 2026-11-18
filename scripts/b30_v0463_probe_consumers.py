"""Which features answer ``.Sketch``, and is "different origin" safe to act on?

Cell 1 - the CONSUMER side. ``凸台.1.Sketch.Name`` answered 'SK_A' (probe v0462),
which is the handle a SKETCH_CONSUMED_BY answer has to read. Five features are
built here to see how far that reaches: pad, pocket, hole, and a GSD extrude
(which does NOT live in ``body.Shapes``, so a body-only scan cannot see it).
The GSD sketch is then deleted to see what CATIA does when it is consumed
somewhere the scan cannot reach.

Cell 2 - the DATUM side. 0.3.148 refuses the frame cleanup whenever another
sketch is COPLANAR, which the 2026-11-18 report measured as a false refusal:
two frame sketches on z = 0 with datum points 50 mm apart are two datums. The
claim this must support is that deleting the first one's frame is SAFE - so the
frame is deleted with cleanup_frame_datum=true and the part is updated.

.venv/Scripts/python.exe -u scripts/b30_v0463_probe_consumers.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0463")


def say(line: str = "") -> None:
    print(line, flush=True)


def close_docs(conn) -> None:
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


def new_part(docs, conn, name: str):
    path = os.path.join(ROOT, name + ".CATPart")
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    return conn.get_active_part(), conn.get_active_part_body()


def profile_names(body) -> list[tuple[str, str]]:
    """``(feature, whatever its .Sketch answers)`` for every shape in the body."""
    out = []
    shapes = body.Shapes
    for position in range(1, int(shapes.Count) + 1):
        shape = shapes.Item(position)
        try:
            profile = shape.Sketch
            out.append((str(shape.Name), str(profile.Name)))
        except Exception as error:  # noqa: BLE001
            out.append((str(shape.Name),
                        f"<{str(error).splitlines()[0][:48]}>"))
    return out


def frame_sketch(sketcher, name: str, origin, u, v) -> None:
    sketcher.execute("catia_create_sketch",
                     {"plane": {"type": "frame", "origin": list(origin),
                                "u": list(u), "v": list(v)}, "name": name})


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)
    say(f"probe v0463 on {conn.app.Name}")
    close_docs(conn)

    # ══ cell 1: which features answer .Sketch ═════════════════════════════
    part, body = new_part(docs, conn, "ProbeV0463a")
    frame_sketch(sketcher, "SK_P", (0, 0, 0), (1, 0, 0), (0, 1, 0))
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 40.0, "y2": 20.0})
    sketcher.execute("catia_close_sketch", {})
    say("1 pad: " + " ".join(parts.execute(
        "catia_pad", {"sketch_name": "SK_P", "height": 10.0}).split())[:120])

    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SK_Q"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 5.0, "y1": 5.0, "x2": 15.0, "y2": 15.0})
    sketcher.execute("catia_close_sketch", {})
    say("1 pocket: " + " ".join(parts.execute(
        "catia_pocket", {"sketch_name": "SK_Q", "through_all": True}).split())[:140])

    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SK_H"})
    sketcher.execute("catia_sketch_circle",
                     {"cx": 30.0, "cy": 10.0, "radius": 3.0})
    sketcher.execute("catia_close_sketch", {})
    try:
        say("1 hole: " + " ".join(parts.execute(
            "catia_hole", {"diameter": 6.0, "depth": 5.0}).split())[:160])
    except Exception as error:  # noqa: BLE001
        say(f"1 hole raised: {type(error).__name__}: "
            f"{str(error).splitlines()[0][:140]}")

    # A GSD extrude consumes a sketch too - and lives outside body.Shapes.
    frame_sketch(sketcher, "SK_G", (0, 0, 200), (1, 0, 0), (0, 1, 0))
    sketcher.execute("catia_sketch_circle",
                     {"cx": 0.0, "cy": 0.0, "radius": 5.0})
    sketcher.execute("catia_close_sketch", {})
    try:
        say("1 gsd_extrude: " + " ".join(gsd.execute(
            "catia_gsd_extrude", {"profile": "SK_G", "direction": [0, 0, -1],
                                  "limit1": 20.0}).split())[:140])
    except Exception as error:  # noqa: BLE001
        say(f"1 gsd_extrude raised: {type(error).__name__}: "
            f"{str(error).splitlines()[0][:140]}")

    say("1 body.Shapes .Sketch: " + str(profile_names(body)))
    try:
        set_names = [str(item.Name) for item in body.HybridShapes]
    except Exception:  # noqa: BLE001
        set_names = []
    say(f"1 body.HybridShapes: {set_names}")

    # What does CATIA do with a sketch consumed by a GSD element?
    try:
        reply = parts.execute("catia_delete_feature", {"feature_name": "SK_G"})
        say("1 delete SK_G: " + " ".join(reply.split())[:400])
    except Exception as error:  # noqa: BLE001
        say(f"1 delete SK_G raised: {type(error).__name__}: "
            f"{str(error).splitlines()[0][:220]}")
    try:
        say("1 update: " + " ".join(
            measurement.execute("catia_update_part", {}).split())[:160])
    except Exception as error:  # noqa: BLE001
        say(f"1 update raised: {type(error).__name__}: "
            f"{str(error).splitlines()[0][:160]}")

    # ══ cell 2: deleting a frame nobody else stands on, though someone is coplanar
    part, body = new_part(docs, conn, "ProbeV0463b")
    for sketch_name, origin in (("DAT_A", (0.0, 0.0, 0.0)),
                                ("DAT_B", (50.0, 0.0, 0.0))):
        frame_sketch(sketcher, sketch_name, origin, (1, 0, 0), (0, 1, 0))
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0.0, "y1": 0.0, "x2": 20.0, "y2": 10.0})
        sketcher.execute("catia_close_sketch", {})
    try:
        reply = parts.execute("catia_delete_feature",
                              {"feature_name": "DAT_A",
                               "cleanup_frame_datum": True})
        say("2 delete DAT_A (forced): " + " ".join(reply.split())[:400])
    except Exception as error:  # noqa: BLE001
        say(f"2 delete DAT_A raised: {type(error).__name__}: "
            f"{str(error).splitlines()[0][:220]}")
    try:
        say("2 update: " + " ".join(
            measurement.execute("catia_update_part", {}).split())[:160])
    except Exception as error:  # noqa: BLE001
        say(f"2 update FAILED: {type(error).__name__}: "
            f"{str(error).splitlines()[0][:200]}")
    say("2 tree: " + str([str(body.Sketches.Item(i).Name)
                          for i in range(1, int(body.Sketches.Count) + 1)]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
