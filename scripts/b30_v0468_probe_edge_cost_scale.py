"""Where does `edges resolved` go on a part the size of the report's?

The 2026-11-19 (0.3.150) report measured 10.3 s per single-edge chamfer on a
67-face / 186-edge part, with `features built 289 ms` and `rebuild 20 ms`. The
0.86 s in the changelog was a 12-edge box. So the cost is per EDGE, and the
question this probe answers is: which per-edge call.

Reported per stage, on a star big enough to have ~180 edges:
  topology_cells   the enumeration itself
  NLS per edge     cell.Value.Name (what the fingerprint is made of)
  Length per edge  GetMeasurable + Length (what _edge_table adds)
  GetMeasurable    what the anchor path needs whether or not it wants Length
  one anchor       N x GetMinimumDistance

One new document per cell, per the rule: a failure is document-level.
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402,F401
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402
from catia_mcp.topo_search import nls_of, topology_cells  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "catia_probe_v0468")
os.makedirs(ROOT, exist_ok=True)
CHECKS: list[tuple[str, bool, str]] = []
TEETH = 48


def say(text: str) -> None:
    print(text, flush=True)


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), detail))
    say(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def timed(label: str, fn):
    started = time.perf_counter()
    out = fn()
    say(f"  {label}: {(time.perf_counter() - started) * 1000:.0f} ms")
    return out


def close_all(docs) -> None:
    try:
        for name in list(docs.execute("catia_list_documents", {}).get("documents", [])):
            pass
    except Exception:  # noqa: BLE001
        pass
    try:
        while True:
            docs.execute("catia_close_document", {"save": False})
    except Exception:  # noqa: BLE001
        pass


def star_profile(teeth: int) -> list[list[float]]:
    """A closed zigzag: `teeth` outer points alternating with `teeth` inner ones."""
    points: list[list[float]] = []
    for index in range(teeth * 2):
        angle = 2 * math.pi * index / (teeth * 2)
        radius = 50.0 if index % 2 == 0 else 40.0
        points.append([round(radius * math.cos(angle), 6),
                       round(radius * math.sin(angle), 6)])
    points.append(points[0])
    return points


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    say(f"probe v0468 on {conn.app.Name}")
    close_all(docs)

    path = os.path.join(ROOT, "EdgeScale.CATPart")
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": "EdgeScale", "file_path": path})
    part = conn.get_active_part()
    body = conn.get_active_part_body()
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "STAR"})
    sketcher.execute("catia_sketch_strokes", {"strokes": [star_profile(TEETH)]})
    sketcher.execute("catia_close_sketch", {})
    reply = parts.execute("catia_pad", {"sketch_name": "STAR", "height": 8.0})
    say("  pad: " + " ".join(reply.split())[:120])

    # ── the enumeration ────────────────────────────────────────────────────
    cells = timed("topology_cells", lambda: topology_cells(conn, body, "Edge"))
    say(f"  edges: {len(cells)}")
    spa = parts._topo_measure(part)
    nls = timed("NLS  (cell.Value.Name)",
                lambda: [nls_of(cell) for cell in cells])
    say(f"  NLS non-empty: {sum(1 for text in nls if text)}/{len(nls)}")

    measurables = timed("GetMeasurable x N",
                        lambda: [spa.GetMeasurable(cell.Reference) for cell in cells])
    lengths = timed("Length x N",
                    lambda: [float(m.Length) for m in measurables])
    say(f"  length range {min(lengths):.3f} .. {max(lengths):.3f} mm")

    # The whole edge table, both ways.
    table = timed("_edge_table(lengths=True)", lambda: parts._edge_table(body))
    check("A the table has one row per enumerated edge",
          len(table) == len(cells), f"{len(table)} vs {len(cells)}")
    bare = timed("_edge_table(lengths=False)", lambda: parts._edge_table(body, lengths=False))
    check("A2 a length-free row still knows its faces",
          all(row["bounded_by"] for row in bare[:5])
          and all(row["length"] is None for row in bare),
          f"{bare[0]['bounded_by']}")

    # ── one anchor end to end ─────────────────────────────────────────────
    started = time.perf_counter()
    labels, refs, report, used = parts._anchors_to_edges(
        body, [[50.0, 0.0, 4.0]], 1.0)
    whole = (time.perf_counter() - started) * 1000
    say(f"  _anchors_to_edges (1 anchor): {whole:.0f} ms -> {labels}")
    check("B one anchor still resolves to one edge", len(refs) == 1, report[:80])

    # ── and a real chamfer, for the phases in its own reply ───────────────
    started = time.perf_counter()
    reply = parts.execute("catia_chamfer",
                          {"edge_anchors": [[50.0, 0.0, 4.0]], "length": 0.4,
                           "angle": 45})
    total = (time.perf_counter() - started) * 1000
    text = " ".join(reply.split())
    say(f"  chamfer total {total:.0f} ms")
    say("  reply: " + text[:400])
    check("C the reply reports its phases", "edges resolved" in text, text[:80])

    # ── the same call with names instead of anchors ───────────────────────
    # A FRESH edge: the anchored one now carries a chamfer, and an edge that
    # shares a vertex with it refuses (measured on B30 2026-11-16).
    started = time.perf_counter()
    try:
        reply2 = parts.execute("catia_chamfer",
                               {"edge_names": ["Edge.12"], "length": 0.4,
                                "angle": 45})
        named = (time.perf_counter() - started) * 1000
        say(f"  by name: {named:.0f} ms | " + " ".join(reply2.split())[:220])
    except Exception as error:  # noqa: BLE001 - the timing is what is measured
        named = (time.perf_counter() - started) * 1000
        say(f"  by name: {named:.0f} ms, refused: "
            f"{str(error).splitlines()[0][:140]}")
    check("D a named edge is cheaper than an anchored one",
          named < total, f"{named:.0f} vs {total:.0f} ms")

    say("")
    passed = sum(1 for _label, ok, _detail in CHECKS if ok)
    say(f"{passed}/{len(CHECKS)} checks passed")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
