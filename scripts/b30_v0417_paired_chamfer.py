"""A mirrored chamfer: does the SECOND one always fail? (2026-11-13 report, P0)

The report is unusually specific: on the top of a column, two symmetric edges 8 mm
apart, chamfered 70 degrees. The first always succeeds, the second always fails
with `UpdateObject failed`, and it fails whichever one is built first - so the
failure is tied to "the other chamfer already exists". Three call shapes were tried
(two separate calls, one call with both anchors, and on_anchor_miss='skip'): 1 of 2.

That is a claim about a sequence, so a pair of cells cannot settle it - but the
order can: build A then B in one document, and B then A in another. If both orders
fail on the second, the sequence is the trigger. If either order succeeds, the
report's geometry is doing something ours is not.

The test solid is a 40 x 10 x 30 block: its top face has two 10 mm edges (x = 0 and
x = 40) that mirror each other about x = 20, which is the report's situation
without its 70 degree angle.

    .venv/Scripts/python.exe scripts/b30_v0417_paired_chamfer.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0417")
ROOT = os.path.join(_PROBE_ROOT, "chamfer")
results: list[tuple[str, str, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def activate(conn: CATIAConnection, name: str) -> None:
    for index in range(1, conn.documents.Count + 1):
        candidate = conn.documents.Item(index)
        if candidate.Name == name:
            candidate.Activate()
            if conn.active_document.Name != name:
                raise RuntimeError(f"could not activate {name}")
            return
    raise RuntimeError(f"{name} is not open")


def close_ours(conn: CATIAConnection) -> None:
    for index in range(int(conn.documents.Count), 0, -1):
        document = conn.documents.Item(index)
        try:
            full = str(document.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).startswith(os.path.abspath(_PROBE_ROOT)):
            try:
                document.Close()
            except Exception:  # noqa: BLE001
                pass


def volume(conn) -> float | None:
    part = conn.get_active_part()
    return PartDesignTools(conn)._body_volume_mm3(part, part.MainBody)


def top_edges(part_design) -> list[dict]:
    """The edges of the top face, with a point on each."""
    listing = json.loads(part_design.execute("catia_list_edges",
                                             {"coords": True}))
    found = []
    for edge in listing.get("edges", []):
        point = edge.get("point_on_edge")
        if not point or abs(point[2] - 30.0) > 0.01:
            continue
        found.append(edge)
    return found


def build(conn, documents, sketcher, part_design, name: str) -> None:
    path = os.path.join(ROOT, name)
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part", {"name": name.split(".")[0],
                                         "file_path": path, "overwrite": True})
    activate(conn, name)
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 40, "y2": 10})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 30})


def chamfer(part_design, anchor: list[float], size: float = 2.0,
            angle: float = 45.0) -> str:
    try:
        return "ok: " + " ".join(str(part_design.execute(
            "catia_chamfer", {"edge_anchors": [anchor], "length": size,
                              "angle": angle}
        )).split())[:150]
    except Exception as error:  # noqa: BLE001 - the refusal is the datum
        return "REFUSED: " + " ".join(str(error).split())[:220]


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)

    second: dict[str, bool] = {}
    for label, order in (("A then B", ("x0", "x40")), ("B then A", ("x40", "x0"))):
        say("")
        say(f"── {label}: 40x10x30 block, chamfer 2 mm on both top end edges ──")
        build(conn, documents, sketcher, part_design, f"Pair_{label[0]}.CATPart")
        start = volume(conn)
        say(f"  block volume {start}")
        edges = top_edges(part_design)
        say(f"  top edges: "
            + ", ".join(f"{e['name']} L{e.get('length_mm')} "
                        f"at {e.get('point_on_edge')}" for e in edges))
        which = {"x0": min(edges, key=lambda e: e["point_on_edge"][0]),
                 "x40": max(edges, key=lambda e: e["point_on_edge"][0])}
        for step, key in enumerate(order, start=1):
            answer = chamfer(part_design, which[key]["point_on_edge"])
            say(f"  {step}. {key}: {answer}")
            say(f"     volume now {volume(conn)}")
            results.append((label, key, answer))
            if step == 2:
                second[label] = "REFUSED" in answer

    # The report's own chamfer angle was 70 degrees, not the 45 default. A 70 deg
    # bevel leans far enough to reach across the 10 mm end face, so this is the
    # cell that would explain a failure tied to "the other one already exists".
    say("")
    say("── the same pair at 70 degrees (the report's angle) ──")
    for label, order in (("A then B", ("x0", "x40")), ("B then A", ("x40", "x0"))):
        say(f"  -- {label} --")
        build(conn, documents, sketcher, part_design,
              f"Pair70_{label[0]}.CATPart")
        say(f"     block volume {volume(conn)}")
        edges = top_edges(part_design)
        which = {"x0": min(edges, key=lambda e: e["point_on_edge"][0]),
                 "x40": max(edges, key=lambda e: e["point_on_edge"][0])}
        for step, key in enumerate(order, start=1):
            answer = chamfer(part_design, which[key]["point_on_edge"], angle=70.0)
            say(f"     {step}. {key}: {answer}")
            say(f"        volume now {volume(conn)}")
        say(f"     features: "
            + " ".join(str(part_design.execute(
                "catia_list_features", {}))[:150].split()))

    # The third shape from the report: both anchors in ONE call.
    say("")
    say("── both anchors in one call ──")
    build(conn, documents, sketcher, part_design, "Pair_one.CATPart")
    edges = top_edges(part_design)
    low = min(edges, key=lambda e: e["point_on_edge"][0])["point_on_edge"]
    high = max(edges, key=lambda e: e["point_on_edge"][0])["point_on_edge"]
    try:
        reply = " ".join(str(part_design.execute(
            "catia_chamfer", {"edge_anchors": [low, high], "length": 2.0})).split())
        say("  " + reply[:300])
        results.append(("one call", "both", "ok: " + reply[:150]))
    except Exception as error:  # noqa: BLE001
        say("  REFUSED: " + " ".join(str(error).split())[:320])
        results.append(("one call", "both", "REFUSED: " + str(error)[:200]))
    say(f"  volume now {volume(conn)}")

    say("")
    say(f"second-chamfer outcomes: {second}")
    say("VERDICT: " + (
        "the second chamfer FAILS in both orders - the report stands"
        if second and all(second.values())
        else "at least one second chamfer SUCCEEDED - the 'always fails' claim does "
             "not hold on this geometry"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
