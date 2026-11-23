"""B30 probe, 0.3.165: the four things the 2026-11-23 report (0.3.164) claims.

Each cell is its own NEW document. What is being established, in order:

A. after `catia_move_body`, what does `catia_get_inertia` actually read, and what
   does `catia_list_bodies` total? (N1's symptom, reproduced or not)
B. is a body's visibility READABLE? `list_bodies` cannot say "hidden" without
   one, and the report wants hidden bodies out of the total.
C. after a failed plan's rollback, where do the `_FRAME` elements live - in
   `part.HybridBodies` (which `catia_delete_feature` searches) or in
   `body.HybridShapes` (which it does not)? This decides whether `left` and
   `reply` can ever agree.
D. does `catia_delete_feature(names=['倒圆角.*'])` work? The report says it does
   not, and 0.3.161 added exactly that.
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

CHECKS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), str(detail)[:200]))
    print(("  PASS " if ok else "  FAIL ") + label
          + ("" if ok else f"   <- {str(detail)[:200]}"), flush=True)


def say(text: str = "") -> None:
    print(text, flush=True)


def payload(reply):
    return reply if isinstance(reply, (dict, list)) else json.loads(str(reply))


def inertia(measure, **kwargs) -> dict:
    return payload(measure.execute("catia_get_inertia", kwargs))


class Cell:
    def __init__(self, conn, root: str, name: str) -> None:
        self.docs = DocumentTools(conn)
        self.sk = SketcherTools(conn)
        self.design = PartDesignTools(conn)
        self.measure = MeasurementTools(conn)
        self.planner = PlannerTools(conn)
        self.conn = conn
        self.path = os.path.join(root, f"{name}.CATPart")
        try:
            self.docs.execute("catia_close_document",
                              {"document": self.path, "save": False})
        except Exception:  # noqa: BLE001
            pass
        if os.path.isfile(self.path):
            os.remove(self.path)
        self.docs.execute("catia_new_part", {"name": name, "file_path": self.path})

    def rect(self, sketch_name: str, x1, y1, x2, y2) -> None:
        self.sk.execute("catia_create_sketch", {"plane": "xy", "name": sketch_name})
        self.sk.execute("catia_sketch_rectangle",
                        {"x1": x1, "y1": y1, "x2": x2, "y2": y2})
        self.sk.execute("catia_close_sketch", {})

    def close(self) -> None:
        try:
            self.docs.execute("catia_close_document",
                              {"document": self.path, "save": False})
        except Exception:  # noqa: BLE001
            pass


def body_names(part, collection: str) -> dict[str, list[str]]:
    """Every body's ``Shapes`` / ``Sketches`` / ``HybridShapes`` names."""
    out: dict[str, list[str]] = {}
    for index in range(1, part.Bodies.Count + 1):
        body = part.Bodies.Item(index)
        try:
            items = getattr(body, collection)
            out[str(body.Name)] = [str(items.Item(i).Name)
                                   for i in range(1, items.Count + 1)]
        except Exception:  # noqa: BLE001
            out[str(body.Name)] = []
    return out


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    root = os.path.join(tempfile.gettempdir(), "p0502")
    os.makedirs(root, exist_ok=True)

    # ── A: what get_inertia reads after a move ──────────────────────────
    say("A. move_body -> which body does catia_get_inertia read?")
    cell = Cell(conn, root, "T164_A")
    cell.rect("S", 0, 0, 20, 30)
    cell.design.execute("catia_pad", {"sketch_name": "S", "height": 20})
    cell.design.execute("catia_edit_pad", {"feature_name": "凸台.1", "height": 10})
    before = inertia(cell.measure)
    say(f"  before move: body={before.get('body')} scope={before.get('scope')} "
        f"V={before.get('volume_mm3')} cg={before.get('center_of_gravity_mm')}")
    reply = cell.design.execute("catia_move_body", {"dx": 50})
    say("  move: " + reply[:300].replace("\n", " | "))
    after = inertia(cell.measure)
    say(f"  after move: body={after.get('body')} scope={after.get('scope')} "
        f"V={after.get('volume_mm3')} cg={after.get('center_of_gravity_mm')}")
    bodies = payload(cell.design.execute("catia_list_bodies", {}))
    say("  list_bodies: " + json.dumps(
        {"count": bodies.get("count"),
         "total": bodies.get("total_volume_mm3"),
         "bodies": [{k: b.get(k) for k in ("name", "volume_mm3", "active")}
                    for b in bodies.get("bodies", [])]}, ensure_ascii=False))
    check("A the move does NOT change what the next get_inertia reads",
          after.get("body") == before.get("body"), str(after.get("body")))
    cog = after.get("center_of_gravity_mm") or {}
    # the payload answers a dict {x,y,z}; before the move the block is
    # 0..20 in x, so the centroid is 10 and after +50 it must be 60.
    check("A and the centre of gravity is still the pre-move one (10, not 60)",
          isinstance(cog, dict) and abs(float(cog.get("x", -1)) - 10.0) < 0.01,
          str(cog))
    check("A list_bodies totals both solids (the doubling)",
          bodies.get("total_volume_mm3") is not None
          and abs(float(bodies["total_volume_mm3"]) - 12000.0) < 1.0,
          str(bodies.get("total_volume_mm3")))

    # ── B: is a body's visibility readable? ─────────────────────────────
    say("B. can a body's visibility be READ? (list_bodies needs it)")
    part = conn.get_active_part()
    source = part.Bodies.Item(1)
    moved = part.Bodies.Item(part.Bodies.Count)
    for member in ("Visible", "Show", "IsVisible", "Hidden"):
        for body, label in ((source, "source"), (moved, "moved")):
            try:
                say(f"  {label}.{member} -> {getattr(body, member)!r}")
            except Exception as error:  # noqa: BLE001
                say(f"  {label}.{member} RAISES "
                    f"{str(error).splitlines()[0][:70]}")
    sel = conn.hso
    for body, label in ((source, "source"), (moved, "moved")):
        try:
            sel.Clear()
            sel.Add(body)
            state = sel.VisProperties.GetShow()
            say(f"  {label} GetShow() -> {tuple(state)}")
        except Exception as error:  # noqa: BLE001
            say(f"  {label} GetShow() RAISES {str(error).splitlines()[0][:70]}")
        finally:
            try:
                sel.Clear()
            except Exception:  # noqa: BLE001
                pass
    cell.close()

    # ── C: where do the _FRAME elements live? ───────────────────────────
    say("C. a failed plan's rollback: where do the _FRAME elements live?")
    cell = Cell(conn, root, "T164_C")
    cell.planner.bind({
        "catia_create_sketch": cell.sk, "catia_sketch_rectangle": cell.sk,
        "catia_sketch_profile": cell.sk, "catia_close_sketch": cell.sk,
        "catia_delete_feature": cell.design, "catia_pad": cell.design,
        "catia_activate_document": cell.docs, "catia_save_document": cell.docs,
        "catia_list_features": cell.design, "catia_update_part": cell.measure,
    })
    steps = [
        {"tool": "catia_create_sketch",
         "args": {"plane": {"type": "frame", "origin": [0, 0, 0],
                            "u": [1, 0, 0], "v": [0, 1, 0]},
                  "name": "T5_Empty"}},
        {"tool": "catia_close_sketch", "args": {}},
        {"tool": "catia_pad", "args": {"sketch_name": "T5_Empty", "height": 5}},
    ]
    try:
        plan = payload(cell.planner.execute("catia_run_plan",
                                            {"steps": steps, "rollback": True}))
        say("  plan ok=" + str(plan.get("ok")) + " code=" + str(plan.get("code")))
        data = plan.get("data") or {}
        rollback = data.get("rollback") or {}
        say("  created: " + json.dumps(data.get("created_features"), ensure_ascii=False))
        say("  left:    " + json.dumps(rollback.get("left"), ensure_ascii=False))
        say("  reply:   " + str(rollback.get("reply"))[:400].replace("\n", " | "))
    except CodedError as error:
        say(f"  plan raised {error.code}: {str(error)[:200]}")
        rollback = {}
    part = conn.get_active_part()
    hybrids = body_names(part, "HybridShapes")
    geosets = [str(part.HybridBodies.Item(i).Name)
               for i in range(1, part.HybridBodies.Count + 1)]
    say("  body.HybridShapes: " + json.dumps(hybrids, ensure_ascii=False))
    say("  part.HybridBodies: " + json.dumps(geosets, ensure_ascii=False))
    frame_in_body = any("_FRAME" in name for names in hybrids.values()
                        for name in names)
    frame_in_sets = any("_FRAME" in name for name in geosets)
    say(f"  _FRAME in a body's HybridShapes: {frame_in_body}; in HybridBodies: "
        f"{frame_in_sets}")
    leftovers = [name for names in hybrids.values() for name in names
                 if "_FRAME" in name]
    if leftovers:
        try:
            answer = cell.design.execute("catia_delete_feature",
                                         {"names": leftovers})
            say("  delete leftovers -> " + answer[:260].replace("\n", " | "))
        except CodedError as error:
            say(f"  delete leftovers RAISED {error.code}: {str(error)[:300]}")
    listing = payload(cell.design.execute("catia_list_features", {}))
    say("  frame_scaffolding: "
        + json.dumps(listing.get("frame_scaffolding"), ensure_ascii=False))
    cell.close()

    # ── C2: deleting a frame element BY NAME - the report's contradiction ──
    say("C2. can catia_delete_feature delete a frame element by name?")
    cell = Cell(conn, root, "T164_C2")
    cell.sk.execute("catia_create_sketch",
                    {"plane": {"type": "frame", "origin": [0, 0, 0],
                               "u": [1, 0, 0], "v": [0, 1, 0]},
                     "name": "T5_Empty"})
    cell.sk.execute("catia_close_sketch", {})
    part = conn.get_active_part()
    names = [str(part.Bodies.Item(1).HybridShapes.Item(i).Name)
             for i in range(1, part.Bodies.Item(1).HybridShapes.Count + 1)]
    say(f"  body.HybridShapes holds: {names}")
    check("C2 the frame scaffolding is in body.HybridShapes (not a geoset)",
          any("_FRAME" in name for name in names)
          and part.HybridBodies.Count == 0, str(names))
    listing = payload(cell.design.execute("catia_list_features", {}))
    say("  list_features frame_scaffolding: "
        + json.dumps(listing.get("frame_scaffolding"), ensure_ascii=False))
    try:
        answer = cell.design.execute("catia_delete_feature", {"names": names})
        say("  delete by name -> " + answer[:300].replace("\n", " | "))
        check("C2 deleting a frame element by name works", True, answer[:120])
    except CodedError as error:
        say(f"  delete by name RAISED {error.code}: {str(error)[:320]}")
        check("C2 deleting a frame element by name works", False,
              f"{error.code}: {str(error)[:160]}")
    still = [str(part.Bodies.Item(1).HybridShapes.Item(i).Name)
             for i in range(1, part.Bodies.Item(1).HybridShapes.Count + 1)]
    say(f"  after: body.HybridShapes holds {still}")
    cell.close()

    # ── C3: is a part document's own Selection usable? ──────────────────
    say("C3. part.Parent.Selection - for reading a body's visibility without "
        "moving the active document")
    cell = Cell(conn, root, "T164_C3")
    cell.rect("S", 0, 0, 10, 10)
    cell.design.execute("catia_pad", {"sketch_name": "S", "height": 10})
    part = conn.get_active_part()
    body = part.MainBody
    try:
        selection = part.Parent.Selection
        selection.Clear()
        selection.Add(body)
        state = selection.VisProperties.GetShow()
        say(f"  part.Parent.Selection -> GetShow() = {tuple(state)}")
        selection.Clear()
        check("C3 part.Parent.Selection can read visibility", True, str(state))
    except Exception as error:  # noqa: BLE001
        say(f"  part.Parent.Selection RAISES {str(error).splitlines()[0][:90]}")
        check("C3 part.Parent.Selection can read visibility", False, str(error)[:120])
    cell.close()

    # ── D: does names=['倒圆角.*'] work? ────────────────────────────────
    say("D. catia_delete_feature(names=['倒圆角.*']) - the report says it does not")
    cell = Cell(conn, root, "T164_D")
    cell.rect("S", 0, 0, 40, 40)
    cell.design.execute("catia_pad", {"sketch_name": "S", "height": 20})
    edges = payload(cell.design.execute("catia_list_edges", {}))
    rows = edges.get("edges") if isinstance(edges, dict) else edges
    vertical = [row["name"] for row in rows
                if abs(float(row.get("length_mm") or 0) - 20.0) < 0.01][:4]
    say(f"  4 vertical edges: {vertical}")
    try:
        cell.design.execute("catia_fillet", {"radius": 2, "edges": vertical})
    except CodedError as error:
        say(f"  fillet raised {error.code}: {str(error)[:200]}")
    listing = payload(cell.design.execute("catia_list_features", {}))
    names = [entry.get("name") for entry in listing.get("features") or []]
    say(f"  features: {names}")
    try:
        answer = cell.design.execute("catia_delete_feature",
                                     {"names": ["倒圆角.*"]})
        say("  wildcard delete -> " + answer[:300].replace("\n", " | "))
        check("D names=['倒圆角.*'] works", "matched" in answer, answer[:150])
    except CodedError as error:
        say(f"  wildcard delete RAISED {error.code}: {str(error)[:300]}")
        check("D names=['倒圆角.*'] works", False, f"{error.code}: {str(error)[:150]}")
    array = payload(cell.measure.execute("catia_get_inertia", {}))
    say(f"  after delete: V={array.get('volume_mm3')}")
    cell.close()

    passed = sum(1 for _label, ok, _detail in CHECKS if ok)
    say()
    say(f"{passed}/{len(CHECKS)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
