"""0.3.81 probe 2: the three pocket data points in the 2026-11-08 report (M7).

The reporter gives three runs of one call on one shape of body and says the third
removed ~5x what the arithmetic allows. Two of the three are claims about
GEOMETRY, so they can be settled here rather than argued about:

  A  one block (40x40x5, z 0..5). frame sketch at y=300, profile x 10..20 /
     z 1..4, pocket depth 400 'normal'. Arithmetic: 10 x 3 x 40 = 1200 mm3.
  B  two blocks that do not touch (z 0..5 and z 100..105). Same sketch. The
     report says this answers NO_MATERIAL twice with "UpdateObject failed".
  C  nine blocks (y 0/-100/-200 x z 0/100/200). frame sketch at y=300, profile
     x 0..30 / z 0..30, pocket depth 400 'normal'. The report measured
     29 747.4 mm3 and could not explain it.

Every volume is read from the body's own inertia, before and after, so the
answer is the arithmetic and not the reply.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0381_m7"


def say(line: str = "") -> None:
    print(line, flush=True)


def block(conn, docs, sketcher, design, x0, y0, z0, size=40):
    """One 40x40x5 block as its own pad, on a frame sketch at (x0, y0, z0)."""
    sketcher.execute("catia_create_sketch", {
        "plane": {"type": "frame", "origin": [x0, y0, z0],
                  "u": [1, 0, 0], "v": [0, 1, 0]}})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": size, "y2": size})
    sketcher.execute("catia_close_sketch", {})
    name = str(conn.last_sketch.Name)
    design.execute("catia_pad", {"sketch_name": name, "height": 5,
                                 "direction": "normal"})
    return name


def cut(conn, sketcher, design, args, label):
    """The pocket in the report, with the volume measured either side of it."""
    part = conn.get_active_part()
    body = conn.get_active_part_body()
    before = design._body_volume_mm3(part, body)
    sketcher.execute("catia_create_sketch", {
        "plane": {"type": "frame", "origin": [0, 300, 0],
                  "u": [1, 0, 0], "v": [0, 0, 1]}})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": args["x1"], "y1": args["y1"],
                      "x2": args["x2"], "y2": args["y2"]})
    sketcher.execute("catia_close_sketch", {})
    name = str(conn.last_sketch.Name)
    say(f"   {label}: body has {body.Shapes.Count} shape(s), volume "
        f"{before:.1f} mm3; sketch '{name}'")
    try:
        reply = design.execute("catia_pocket", {
            "sketch_name": name, "depth": 400, "direction": "normal"})
    except Exception as error:  # noqa: BLE001 - FeatureRejected is a RuntimeError
        after = design._body_volume_mm3(part, body)
        say(f"   {label}: REFUSED "
            f"{getattr(error, 'code', type(error).__name__)}")
        say("     " + str(error).replace("\n", "\n     ")[:700])
        say(f"     volume after: {after:.1f} mm3")
        return None
    after = design._body_volume_mm3(part, body)
    removed = (before - after) if (before and after) else None
    if removed is None:
        say("   volume after the cut could not be read")
    else:
        say(f"   {label}: OK, removed {removed:.1f} mm3 "
            f"({before:.1f} -> {after:.1f})")
    say("     " + reply.replace("\n", "\n     ")[:600])
    return removed


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    design = PartDesignTools(conn)
    say(f"session before: {conn.documents.Count} document(s)")

    try:
        say("\nA — one block, profile x 10..20 / z 1..4 (arithmetic: 1200 mm3)")
        docs.execute("catia_new_part", {
            "name": "PZ81M7A", "file_path": os.path.join(ROOT, "A.CATPart"),
            "overwrite": True})
        block(conn, docs, sketcher, design, 0, 0, 0)
        cut(conn, sketcher, design,
            {"x1": 10, "y1": 1, "x2": 20, "y2": 4}, "A")

        say("\nB — two separated blocks, same profile (report: NO_MATERIAL twice)")
        docs.execute("catia_new_part", {
            "name": "PZ81M7B", "file_path": os.path.join(ROOT, "B.CATPart"),
            "overwrite": True})
        block(conn, docs, sketcher, design, 0, 0, 0)
        block(conn, docs, sketcher, design, 0, 0, 100)
        # ONE cut, not two: after the first one the profile's footprint has no
        # material left, so a second identical call failing would prove nothing.
        cut(conn, sketcher, design,
            {"x1": 10, "y1": 1, "x2": 20, "y2": 4}, "B")

        say("\nC — nine blocks, profile x 0..30 / z 0..30 (report: 29747.4 mm3)")
        docs.execute("catia_new_part", {
            "name": "PZ81M7C", "file_path": os.path.join(ROOT, "C.CATPart"),
            "overwrite": True})
        for y0 in (0, -100, -200):
            for z0 in (0, 100, 200):
                block(conn, docs, sketcher, design, 0, y0, z0)
        say("   arithmetic, if the cut runs -Y from y=300 with depth 400:")
        say("     z 0..5 layer: y 0..40 -> 30 x 5 x 40 = 6000; "
            "y -60..-100 -> another 6000 (the section is truncated at y=-100)")
        say("     z 100..105 and z 200..205: profile z 0..30 reaches neither -> 0")
        cut(conn, sketcher, design,
            {"x1": 0, "y1": 0, "x2": 30, "y2": 30}, "C")
    finally:
        say("\ncleanup: closing only documents under " + ROOT)
        mine = os.path.normcase(os.path.abspath(ROOT))
        for index in range(int(conn.documents.Count), 0, -1):
            try:
                doc = conn.documents.Item(index)
                full = os.path.normcase(os.path.abspath(str(doc.FullName)))
            except Exception:  # noqa: BLE001
                continue
            if not full.startswith(mine):
                continue
            try:
                doc.Close()
                say(f"   closed {os.path.basename(full)}")
            except Exception as error:  # noqa: BLE001
                say(f"   could not close {os.path.basename(full)}: "
                    f"{str(error).splitlines()[0][:60]}")
        say(f"session after: {conn.documents.Count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
