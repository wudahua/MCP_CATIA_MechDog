"""What a sketch will say about WHERE it sits, and what the mirror really did.

  G  GetAbsoluteAxisData on a sketch built on a frame plane, and on a second
     sketch built ON THAT frame by name - the only handle the cleanup would have
  H  the mirror on a body whose material genuinely straddles the plane (the
     contrast case the 2026-11-18 report assumed it had)

.venv/Scripts/python.exe -u scripts/b30_v0460_probe_axis_and_mirror.py
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

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0460")
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


def axis_of(sketch, label: str) -> None:
    for name in ("GetAbsoluteAxisData", "AbsoluteAxis"):
        try:
            value = getattr(sketch, name)
            got = value() if callable(value) else value
            say(f"  {label}.{name} -> {got!r}"[:220])
        except Exception as error:  # noqa: BLE001
            say(f"  {label}.{name} FAILS: {str(error).splitlines()[0][:100]}")


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    say(f"probe v0460 on {conn.app.Name}  run {RUN}")

    # ── G: what a sketch says about its own plane ─────────────────────────
    part = fresh(conn, docs, "ProbeV0460g")
    body = conn.get_active_part_body()
    sketcher.execute("catia_create_sketch",
                     {"plane": {"type": "frame", "origin": [0.0, 0.0, 1000.0],
                                "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0]},
                      "name": "SK_A"})
    first = sketcher._active_sketch
    axis_of(first, "open SK_A")
    sketcher.execute("catia_close_sketch", {})
    axis_of(first, "closed SK_A")

    sketcher.execute("catia_create_sketch", {"plane": "SK_A_FRAME",
                                            "name": "SK_B"})
    second = sketcher._active_sketch
    axis_of(second, "open SK_B (built on SK_A_FRAME)")
    sketcher.execute("catia_close_sketch", {})
    axis_of(second, "closed SK_B")

    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SK_C"})
    third = sketcher._active_sketch
    sketcher.execute("catia_close_sketch", {})
    axis_of(third, "closed SK_C (on the xy datum plane)")

    try:
        spa = part.Parent.GetWorkbench("SPAWorkbench")
        plane = None
        for index in range(1, int(body.HybridShapes.Count) + 1):
            node = body.HybridShapes.Item(index)
            if str(getattr(node, "Name", "")).startswith("SK_A_FRAME"):
                plane = node
                break
        if plane is not None:
            ref = part.CreateReferenceFromObject(plane)
            try:
                got = spa.GetMeasurable(ref).GetPlane()
                say(f"  G datum SK_A_FRAME GetPlane -> {got!r}"[:200])
            except Exception as error:  # noqa: BLE001
                say(f"  G datum GetPlane FAILS: {str(error).splitlines()[0][:110]}")
    except Exception as error:  # noqa: BLE001
        say(f"  G datum lookup FAILED: {str(error).splitlines()[0][:110]}")

    # ── H: a body that genuinely straddles the plane ──────────────────────
    part = fresh(conn, docs, "ProbeV0460h")
    body = conn.get_active_part_body()
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "H1"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 40.0, "y2": 40.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 20.0, "symmetric": True})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "H2"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 100.0, "y1": 0.0, "x2": 120.0, "y2": 20.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 10.0})
    spa = part.Parent.GetWorkbench("SPAWorkbench")
    before = float(spa.GetMeasurable(
        part.CreateReferenceFromObject(body)).Volume) * 1e9
    reply = parts.execute("catia_mirror", {"plane": "xy", "allow_body_mirror": True})
    after = float(spa.GetMeasurable(
        part.CreateReferenceFromObject(body)).Volume) * 1e9
    say(f"  H straddling body: {before!r} -> {after!r} mm3")
    say("  H reply: " + " ".join(reply.split())[:260])

    say("")
    say("documents left open in " + ROOT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
