"""B30 probe for 0.3.161 - the fillet that did 4 of 12 and said ok:true.

The report's exact part: a 25 x 45 x 80 block, one call of
``catia_fillet(radius=2)`` with no edge named.

  A  the call is now a FAILURE carrying done=4/12 and self_enumerated
  B  the reply still says how many took and which edges refused
  C  catia_delete_feature(names=['倒圆角.*']) removes exactly those features
  D  the recipe the refusal recommends - 4 long edges, then one rim edge per end
     with propagation='tangency' - reaches the full 26-face box
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.results import CodedError  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.planner import PlannerTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

results: list[tuple[str, bool, str]] = []


def say(text: str) -> None:
    print(text, flush=True)


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    say(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  [{detail}]" if detail else ""))


def payload(reply):
    return reply if isinstance(reply, (dict, list)) else json.loads(str(reply))


def topo(planner) -> dict:
    return payload(planner.execute("catia_measure_topology", {}))


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sk = SketcherTools(conn)
    design = PartDesignTools(conn)
    planner = PlannerTools(conn)
    measure = MeasurementTools(conn)
    planner.bind({"catia_measure_topology": planner})

    root = os.path.join(tempfile.gettempdir(), "p0498")
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "BOX0498.CATPart")
    try:
        docs.execute("catia_close_document", {"document": path, "save": False})
    except Exception:  # noqa: BLE001
        pass
    if os.path.isfile(path):
        os.remove(path)

    docs.execute("catia_new_part", {"name": "BOX0498", "file_path": path})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "BOX"})
    sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 25, "y2": 45})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"sketch_name": "BOX", "height": 80})
    topology = topo(planner)
    say(f"  block: {topology['faces']} faces, {topology['edges']} edges, "
        f"{topology['vertices']} vertices")
    check("A the block starts as 6 faces / 12 edges", topology["faces"] == 6
          and topology["edges"] == 12, f"{topology['faces']}/{topology['edges']}")

    # ── A. catia_fillet(radius=2) with no edge named ─────────────────────
    say("\n== A catia_fillet(radius=2) with no edge named (the report's call)")
    refusal = None
    try:
        reply = str(design.execute("catia_fillet", {"radius": 2}))
        say("  ok:true -> " + reply[:400])
        check("A the call is a FAILURE, not ok:true", False, reply[:120])
    except CodedError as error:
        refusal = error
        say(f"  code={error.code} modified={getattr(error, 'modified', None)}")
        say("  " + str(error)[:900])
        check("A the call is a FAILURE, not ok:true", True, error.code)

    if refusal is not None:
        data = getattr(refusal, "data", {}) or {}
        say(f"  data: requested={data.get('requested')} done={data.get('done')} "
            f"self_enumerated={data.get('self_enumerated')}")
        check("B done < requested is in the DATA", data.get("requested") == 12
              and 0 < int(data.get("done") or 0) < 12,
              f"{data.get('done')}/{data.get('requested')}")
        check("B it says the tool chose the set", data.get("self_enumerated") is True)
        check("B the message says the edges were OFFERED", "were OFFERED" in str(refusal))
        check("B the message names the count that took",
              f"{data.get('done')}/12 edge(s) done" in str(refusal),
              f"{data.get('done')}/12")
        check("B the recipe is offered as a next action",
              any("tangency" in action for action in getattr(refusal, "next_actions", [])))

    features = payload(design.execute("catia_list_features", {})).get("features") or []
    fillets = [entry["name"] for entry in features
               if str(entry.get("name", "")).startswith("倒圆角")]
    say(f"  fillets in the tree: {fillets}")
    check("B the four that built are really there", len(fillets) == 4, str(fillets))

    # ── C. the wildcard delete ───────────────────────────────────────────
    say("\n== C catia_delete_feature(names=['倒圆角.*'])")
    try:
        deleted = str(design.execute("catia_delete_feature",
                                     {"names": ["倒圆角.*"]}))
        say("  " + deleted[:400])
        check("C the wildcard deleted them all", "Failed (0)" not in deleted
              and "Deleted 4 element(s)" in deleted, deleted[:80])
        check("C and the reply prints what it matched",
              "Expanded '倒圆角.*' matched 4 element(s)" in deleted, "")
    except CodedError as error:
        say(f"  refused: {error}")
        check("C the wildcard deleted them all", False, str(error)[:120])
    topology = topo(planner)
    check("C the block is back to 12 edges", topology["edges"] == 12,
          str(topology["edges"]))

    # ── D. the recipe: 4 long edges, then one rim edge per end -----------
    say("\n== D the recipe: 4 edges parallel to the extrusion + 2 tangency rims")
    edges = payload(design.execute("catia_list_edges", {}))
    rows = edges.get("edges") if isinstance(edges, dict) else edges
    long_edges = [row["name"] for row in rows if abs(float(row["length_mm"]) - 80.0) < 0.01]
    say(f"  edges 80 mm long: {long_edges}")
    check("D the block has four 80 mm edges", len(long_edges) == 4, str(long_edges))
    try:
        step1 = str(design.execute("catia_fillet",
                                   {"radius": 2, "edge_names": long_edges}))
        say("  " + step1[:300])
        check("D the four long edges took", "4/4 edge(s) done" in step1, step1[:80])
    except CodedError as error:
        say(f"  refused: {error}")
        check("D the four long edges took", False, str(error)[:200])

    # One rim edge per end face, with tangency propagation. The rim edges are
    # the ones still exactly 25 or 45 mm long: a fillet shortens the neighbours it
    # runs into, so an exact length is the discriminator, and after the first rim
    # is closed its four are gone.
    for round_number, end_face in ((1, "Pad.1;1"), (2, "Pad.1;2")):
        edges = payload(design.execute("catia_list_edges", {}))
        rows = edges.get("edges") if isinstance(edges, dict) else edges
        rim = None
        for row in rows:
            # An edge ON the end face bounds it: the four long ones bound only
            # the sketch's side faces, so they are excluded without a length guess.
            if any(str(face).startswith(end_face)
                   for face in (row.get("bounded_by") or [])):
                rim = row["name"]
                break
        if rim is None:
            check(f"D rim {round_number}: an edge to propagate from", False, "none found")
            break
        say(f"  rim {round_number}: starting from {rim}")
        try:
            reply = str(design.execute("catia_fillet",
                                       {"radius": 2, "edge_name": rim,
                                        "propagation": "tangency"}))
            say("  " + reply[:300])
            check(f"D rim {round_number} propagated", "error" not in reply.lower(),
                  reply[:80])
        except CodedError as error:
            say(f"  refused: {error}")
            check(f"D rim {round_number} propagated", False, str(error)[:200])

    topology = topo(planner)
    inertia = payload(measure.execute("catia_get_inertia", {}))
    volume = (inertia.get("data", inertia) or {}).get("volume_mm3")
    say(f"  final: {topology['faces']} faces / {topology['edges']} edges / "
        f"{topology['vertices']} vertices, V={volume}")
    check("D the box is fully rounded: 26 faces / 48 edges / 24 vertices",
          topology["faces"] == 26 and topology["edges"] == 48
          and topology["vertices"] == 24, str(topology["faces"]))
    check("D and the volume is the corpus value 89495.669",
          volume is not None and abs(float(volume) - 89495.669) < 0.01, str(volume))

    say("")
    passed = sum(1 for _n, ok, _d in results if ok)
    say(f"{passed}/{len(results)} checks passed")
    for name, ok, detail in results:
        if not ok:
            say(f"  FAILED: {name}  [{detail}]")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
