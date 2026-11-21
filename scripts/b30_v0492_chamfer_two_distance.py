"""Does AddNewChamfer(mode=0) really give a two-distance bevel?

The 2026-11-21 report's P0-1 asks for a 25/20 double-distance chamfer and says
`catia_chamfer` only offers length+angle. The code comment in `_chamfer_one`
already records the opposite fact - mode 0 is catLengthLength and the 6th
argument is the second length - but the tool never exposes it, and the comment's
evidence was a FAILURE (passing 45 as a length-2), not a made bevel.

This builds a 100 mm cube and compares three bevels on a 100 mm edge:

  A  mode 1, length 25, angle 20  -> removes 25^2*tan(20)/2*100 = 11 374 mm3
  B  mode 0, 25 then 20           -> removes 25*20/2*100     = 25 000 mm3
  C  mode 0, 20 then 25           -> same volume, mirrored across the edge

If B lands on 25 000 the two-distance mode works and the only thing missing is
the parameter. If it lands on 11 374 the 6th argument was read as an angle even
in mode 0, and P0-1 is impossible through this API.

Run: ./.venv/Scripts/python.exe -u scripts/b30_v0492_chamfer_two_distance.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, ".")

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.topo_search import search_topology  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = tempfile.mkdtemp(prefix="chamfer_probe_")
CUBE = 100.0


def say(text: str = "") -> None:
    print(text, flush=True)


def volume_of(measurement) -> float | None:
    reply = measurement.execute("catia_get_inertia", {})
    import json

    data = reply if isinstance(reply, dict) else json.loads(reply)
    data = data.get("data", data)
    for key in ("volume", "volume_mm3"):
        if data.get(key) is not None:
            return float(data[key])
    for holder in ("mass_properties", "inertia", "properties"):
        block = data.get(holder)
        if isinstance(block, dict):
            for key in ("volume", "volume_mm3"):
                if block.get(key) is not None:
                    return float(block[key])
    return None


def build_cube(name: str, docs, sketcher, part_design) -> str:
    path = os.path.join(ROOT, f"{name}.CATPart")
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SQ"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": CUBE, "y2": CUBE})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"sketch_name": "SQ", "height": CUBE})
    return path


def bevel(mode: int, first: float, second: float) -> tuple[bool, str, float]:
    conn = CATIAConnection()
    conn.connect()
    part = conn.get_active_part()
    body = part.MainBody
    selection = conn.hso
    selection.Clear()
    selection.Add(body)
    search_topology(selection, "Edge")
    if int(selection.Count) == 0:
        return False, "no edge found", 0.0
    reference = selection.Item(1).Reference
    selection.Clear()
    started = None
    import time

    started = time.perf_counter()
    try:
        chamfer = part.ShapeFactory.AddNewChamfer(
            reference,
            1,      # catTangencyChamferPropagation
            mode,   # 0 = catLengthLength, 1 = catLengthAngle
            0,      # catNoReverseChamfer
            first,
            second,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"AddNewChamfer raised: {' '.join(str(exc).split())[:110]}", 0.0
    try:
        part.UpdateObject(chamfer)
    except Exception as exc:  # noqa: BLE001
        return False, f"UpdateObject raised: {' '.join(str(exc).split())[:110]}", 0.0
    took = time.perf_counter() - started
    # Read back whatever the feature exposes, so the mode is not a guess.
    readback = []
    for attribute in ("Mode", "Length", "Length2", "Angle", "Orientation"):
        try:
            readback.append(f"{attribute}={getattr(chamfer, attribute)!r}")
        except Exception:  # noqa: BLE001
            readback.append(f"{attribute}=<absent>")
    return True, " ".join(readback), took


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    measurement = MeasurementTools(conn)

    say(f"workspace: {ROOT}")
    cases = [
        ("A_mode1_25_20deg", 1, 25.0, 20.0, 25.0 ** 2 * 0.3639702343 / 2 * CUBE),
        ("B_mode0_25_20", 0, 25.0, 20.0, 25.0 * 20.0 / 2 * CUBE),
        ("C_mode0_20_25", 0, 20.0, 25.0, 20.0 * 25.0 / 2 * CUBE),
    ]

    for name, mode, first, second, expected in cases:
        say(f"\n── {name}: mode={mode} {first}/{second} ──")
        build_cube(name, docs, sketcher, part_design)
        before = volume_of(measurement)
        ok, detail, took = bevel(mode, first, second)
        after = volume_of(measurement) if ok else None
        say(f"  call: {'OK' if ok else 'FAILED'}  ({took * 1000:.0f} ms)  {detail}")
        if not ok:
            docs.execute("catia_close_document", {"save": False})
            continue
        say(f"  volume {before:.3f} -> {after:.3f} mm3, removed "
            f"{before - after:.3f}")
        say(f"  a two-distance 25/20 bevel on a 100 mm edge removes "
            f"{expected:.3f} mm3; a 25 mm/20 deg one removes "
            f"{25.0 ** 2 * 0.3639702343 / 2 * CUBE:.3f}")
        docs.execute("catia_close_document", {"save": False})

    say("\n── verdict ──")
    say("  B on 25 000 -> mode 0 is a real two-distance bevel; expose length2.")
    say("  B on 11 374 -> the 6th argument is always an angle; P0-1 impossible.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
