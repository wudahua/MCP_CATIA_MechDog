"""Does the assembly world box use every local axis of a ROTATED instance?

The 2026-11-13 report (problem 5) says the assembly box under-reports by 7-11 mm
on all three maxima, on an instance whose placement was verified to 6e-10 mm, and
reads it as "a local axis is paired with the wrong row/column of R". That is a
claim about arithmetic, so it can be settled with a solid whose box is known by
hand: pad a 0..100 x 0..20 rectangle by 10.

    instance 1   identity                  world x[0,100] y[0,20]  z[0,10]
    instance 2   90 deg about Z, T=(200,0,0)
                  world x[180,200] y[0,100] z[0,10]
    whole                                            x[0,200] y[0,100] z[0,10]

A single axis mapped to the wrong row shows up as one of those six numbers being
wrong by exactly one local extent - and the unrotated instance is the control
that catches a broken harness rather than a broken box.

Every document is created here; nothing already open is touched.

Usage, from the repo root:
    .venv/Scripts/python.exe scripts/b30_v0415_rotation_box.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0415")

# 90 deg about Z: (x, y) -> (-y, x)
SPIN = [[0.0, -1.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0]]
SHIFT = [200.0, 0.0, 0.0]

EXPECTED = {
    "BOX_R.1": ((0.0, 0.0, 0.0), (100.0, 20.0, 10.0)),
    "BOX_R.2": ((180.0, 0.0, 0.0), (200.0, 100.0, 10.0)),
    "whole": ((0.0, 0.0, 0.0), (200.0, 100.0, 10.0)),
}

results: list[tuple[str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(label: str, reply: dict, key: str, tolerance: float = 0.01) -> None:
    low = reply.get("min") or {}
    high = reply.get("max") or {}
    want_low, want_high = EXPECTED[key]
    got_low = tuple(float(low.get(axis, float("nan"))) for axis in "xyz")
    got_high = tuple(float(high.get(axis, float("nan"))) for axis in "xyz")
    gaps = [abs(got_low[i] - want_low[i]) for i in range(3)]
    gaps += [abs(got_high[i] - want_high[i]) for i in range(3)]
    worst = max(gaps)
    okay = worst <= tolerance
    results.append((label, okay, f"worst axis error {worst:.4f} mm"))
    say(f"[{'PASS' if okay else 'FAIL'}] {label}")
    say(f"        want x[{want_low[0]}, {want_high[0]}] y[{want_low[1]}, "
        f"{want_high[1]}] z[{want_low[2]}, {want_high[2]}]")
    say(f"        got  x[{got_low[0]:.4f}, {got_high[0]:.4f}] "
        f"y[{got_low[1]:.4f}, {got_high[1]:.4f}] "
        f"z[{got_low[2]:.4f}, {got_high[2]:.4f}]  worst {worst:.4f}")


def close_ours(conn: CATIAConnection) -> None:
    """Close what an interrupted earlier run left open, and nothing else."""
    for index in range(int(conn.documents.Count), 0, -1):
        document = conn.documents.Item(index)
        try:
            full = str(document.FullName or "")
        except Exception:  # noqa: BLE001 - an unreadable path is not ours
            continue
        if full and os.path.abspath(full).startswith(os.path.abspath(PROBE_ROOT)):
            try:
                document.Close()
            except Exception:  # noqa: BLE001
                pass


def activate(conn: CATIAConnection, name: str) -> None:
    for index in range(1, conn.documents.Count + 1):
        candidate = conn.documents.Item(index)
        if candidate.Name == name:
            candidate.Activate()
            if conn.active_document.Name != name:
                raise RuntimeError(f"could not activate {name}")
            return
    raise RuntimeError(f"{name} is not open")


def curved(conn, documents, sketcher, part_design, assembly, measurement) -> None:
    """The other half of the report: a CURVED part, where the extreme is not a vertex.

    An STL box can only chord a circle, and a vertex sweep cannot see the bulge at
    all. The report's own numbers (X max 67.954 against 75.344) are 7-11 mm short
    on three curved parts, so the size of that gap is the question: if a R35 disc
    loses ~7 mm here, the mechanism is chording, not the rotation matrix.
    """
    say("")
    say("── a curved part: R35 disc, pad 10 (true box x,y = -35..35) ──")
    disc_path = os.path.join(PROBE_ROOT, "DISC_R.CATPart")
    disc_asm = os.path.join(PROBE_ROOT, "DISC_ASM.CATProduct")
    for stale in (disc_path, disc_asm):
        if os.path.exists(stale):
            os.remove(stale)

    documents.execute("catia_new_part", {"name": "DISC_R",
                                         "file_path": disc_path,
                                         "overwrite": True})
    activate(conn, "DISC_R.CATPart")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": 35})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})

    for method in ("mesh", "vertices", "auto"):
        try:
            reply = json.loads(measurement.execute("catia_get_bounding_box", {
                "document": "DISC_R.CATPart", "method": method}))
        except Exception as error:  # noqa: BLE001
            say(f"  standalone {method:9} FAILED {str(error).splitlines()[0][:110]}")
            continue
        low, high = reply.get("min") or {}, reply.get("max") or {}
        say(f"  standalone {method:9} x[{low.get('x')}, {high.get('x')}] "
            f"y[{low.get('y')}, {high.get('y')}]  "
            f"short={35 - float(high.get('x', 0)):.4f} mm  "
            f"source={reply.get('source')}")

    documents.execute("catia_new_product", {"name": "DISC_ASM",
                                            "file_path": disc_asm,
                                            "overwrite": True})
    activate(conn, "DISC_ASM.CATProduct")
    add_reply = assembly.execute("catia_add_component", {"file_path": disc_path,
                                                         "count": 2})
    say("  add reply: " + " ".join(str(add_reply).split())[:260])
    say(f"  active now: {conn.active_document.Name}")
    listing = json.loads(assembly.execute("catia_list_components",
                                          {"names_only": True}))
    names = listing.get("names") or [
        entry.get("name") for entry in listing.get("components", [])]
    say(f"  list_components: {json.dumps(listing)[:260]}")
    say(f"  instances: {names}")
    if len(names) >= 2:
        assembly.execute("catia_set_component_transform", {"placements": [
            {"component_name": names[0],
             "rotation_rows": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
             "translation": [0.0, 0.0, 0.0]},
            {"component_name": names[1], "rotation_rows": SPIN,
             "translation": [200.0, 0.0, 0.0]}]})
    try:
        reply = json.loads(measurement.execute("catia_get_bounding_box", {
            "document": "DISC_ASM.CATProduct", "method": "mesh"}))
        low, high = reply.get("min") or {}, reply.get("max") or {}
        say(f"  assembly   mesh      x[{low.get('x')}, {high.get('x')}] "
            f"y[{low.get('y')}, {high.get('y')}]  (want x[0, 235] y[-35, 35])")
    except Exception as error:  # noqa: BLE001
        say(f"  assembly mesh FAILED {str(error).splitlines()[0][:110]}")


def main() -> int:
    os.makedirs(PROBE_ROOT, exist_ok=True)
    part_path = os.path.join(PROBE_ROOT, "BOX_R.CATPart")
    asm_path = os.path.join(PROBE_ROOT, "ROT_ASM.CATProduct")
    for stale in (part_path, asm_path):
        if os.path.exists(stale):
            os.remove(stale)

    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    assembly = AssemblyTools(conn)
    measurement = MeasurementTools(conn)

    documents.execute("catia_new_part", {"name": "BOX_R", "file_path": part_path,
                                         "overwrite": True})
    activate(conn, "BOX_R.CATPart")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 100, "y2": 20})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})
    say("BOX_R: x 0..100, y 0..20, z 0..10")

    documents.execute("catia_new_product", {"name": "ROT_ASM",
                                            "file_path": asm_path,
                                            "overwrite": True})
    activate(conn, "ROT_ASM.CATProduct")
    added = assembly.execute("catia_add_component", {"file_path": part_path,
                                                     "count": 2})
    say("add_component: " + " ".join(str(added).split())[:300])
    listing = json.loads(assembly.execute("catia_list_components",
                                          {"names_only": True}))
    say("list_components: " + json.dumps(listing)[:300])
    names = listing.get("names") or [
        entry.get("name") for entry in listing.get("components", [])]
    say(f"instances: {names}")

    first = next(name for name in names if name.startswith("BOX_R"))
    second = next(name for name in names if name.startswith("BOX_R")
                  and name != first)
    placed = json.loads(assembly.execute("catia_set_component_transform", {
        "placements": [
            {"component_name": first,
             "rotation_rows": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
             "translation": [0.0, 0.0, 0.0]},
            {"component_name": second,
             "rotation_rows": SPIN,
             "translation": SHIFT},
        ]}))
    say("placement reply: " + json.dumps(placed)[:400])

    say("")
    say("── the three boxes, from each route ──")
    for method in ("mesh", "vertices", "auto"):
        say(f"\n method={method}")
        for label, arguments, key in (
            (f"{first} (no rotation)", {"component": first, "method": method},
             "BOX_R.1"),
            (f"{second} (90 deg about Z)", {"component": second,
                                            "method": method}, "BOX_R.2"),
            ("whole product", {"document": "ROT_ASM.CATProduct",
                               "method": method}, "whole"),
        ):
            try:
                reply = json.loads(measurement.execute("catia_get_bounding_box",
                                                       arguments))
            except Exception as error:  # noqa: BLE001 - the failure is the datum
                say(f"[FAIL] {label}: {str(error).splitlines()[0][:140]}")
                results.append((label, False, "call failed"))
                continue
            check(f"{label} [{method}]", reply, key)

    failed = [label for label, okay, _ in results if not okay]
    say("")
    say(f"{len(results) - len(failed)}/{len(results)} boxes match the hand "
        f"calculation")
    if failed:
        say("MISMATCHED: " + "; ".join(failed))
        say("VERDICT: the report stands - a rotated instance's local axis is "
            "paired with the wrong term of R")
    else:
        say("VERDICT: not reproduced - every rotated extent matches to "
            "0.01 mm, so the world box does use all three local axes")

    curved(conn, documents, sketcher, part_design, assembly, measurement)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
