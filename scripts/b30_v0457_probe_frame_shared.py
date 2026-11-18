"""R3 decision: is it safe to delete a frame's datum by DEFAULT?

The 2026-11-18 report asks for `cleanup_frame_datum` to default to true. The risk
that kept it false is the one case this probe tests: a SECOND sketch built on the
same datum by name. If CATIA refuses to delete a datum something else still uses,
defaulting to true is safe (the delete fails and the reply says so). If it lets it
go, the second sketch is broken and the default must stay false.

  A  a frame sketch, then a second sketch supported by its datum BY NAME
  B  delete the first sketch with cleanup_frame_datum=true - what happens to the
     datum, to the second sketch, and to the update
  C  the tree afterwards, and whether the part is still healthy

.venv/Scripts/python.exe -u scripts/b30_v0457_probe_frame_shared.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0457")


def say(line: str = "") -> None:
    print(line, flush=True)


def inventory(part, body) -> dict:
    out = {}
    for key, attr in (("hybrid", "HybridShapes"), ("sketches", "Sketches"),
                      ("shapes", "Shapes")):
        try:
            items = getattr(body, attr)
            out[key] = [str(items.Item(i).Name)
                        for i in range(1, int(items.Count) + 1)]
        except Exception as error:  # noqa: BLE001
            out[key] = f"<{str(error).splitlines()[0][:60]}>"
    return out


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    say(f"probe v0457 on {conn.app.Name}")

    name = "ProbeV0457"
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
    part = conn.get_active_part()
    body = conn.get_active_part_body()

    # ── A: a frame sketch, then a second sketch ON that datum by name ─────
    sketcher.execute("catia_create_sketch",
                     {"plane": {"type": "frame", "origin": [10.0, 20.0, 30.0],
                                "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0]},
                      "name": "FRAME_A"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 40.0, "y2": 20.0})
    sketcher.execute("catia_close_sketch", {})
    say("  A after FRAME_A: " + str(inventory(part, body)))

    sketcher.execute("catia_create_sketch",
                     {"plane": "FRAME_A_FRAME", "name": "ON_FRAME"})
    sketcher.execute("catia_sketch_circle", {"cx": 5.0, "cy": 5.0, "radius": 3.0})
    sketcher.execute("catia_close_sketch", {})
    say("  A after ON_FRAME: " + str(inventory(part, body)))

    # ── B: delete FRAME_A with the scaffolding — the risky path ───────────
    try:
        reply = parts.execute("catia_delete_feature",
                              {"feature_name": "FRAME_A",
                               "cleanup_frame_datum": True})
        say("  B reply: " + " ".join(reply.split())[:400])
    except Exception as error:  # noqa: BLE001
        say(f"  B delete raised: {type(error).__name__}: "
            f"{str(error).splitlines()[0][:200]}")

    # ── C: what is left, and is the part still healthy ────────────────────
    say("  C tree: " + str(inventory(part, body)))
    try:
        part.Update()
        say("  C part.Update: ok")
    except Exception as error:  # noqa: BLE001
        say(f"  C part.Update FAILED: {str(error).splitlines()[0][:160]}")
    cells = []
    try:
        from catia_mcp.tools.measurement import MeasurementTools
        measurement = MeasurementTools(conn)
        say("  C topology: " + str(measurement.execute("catia_measure_topology", {}))[:200])
        cells.append(True)
    except Exception as error:  # noqa: BLE001
        say(f"  C topology FAILED: {str(error).splitlines()[0][:160]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
