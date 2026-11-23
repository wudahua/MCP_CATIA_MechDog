"""B30 acceptance, 0.3.165: the three findings of the 2026-11-23 (0.3.164) report.

The probe (`b30_v0502_report_probe.py`) recorded the state BEFORE the fixes; this
one checks the state after them, on the same four counts:

A. `catia_move_body` leaves the session on the MOVED body, and the next
   `catia_get_inertia` reads the moved solid (not the pre-move one);
B. `catia_list_bodies` separates visible from hidden volume and says so;
C. a frame element can be DELETED by name (it lives in `body.HybridShapes`, which
   the delete lookup used to miss) - so a rollback's `left` and `reply` agree;
D. `catia_delete_feature(names=['倒圆角.*'])` still works (the report said it did
   not, and 0.3.161 added it).

One new document per cell.
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
    CHECKS.append((label, bool(ok), str(detail)[:240]))
    print(("  PASS " if ok else "  FAIL ") + label
          + ("" if ok else f"   <- {str(detail)[:240]}"), flush=True)


def say(text: str = "") -> None:
    print(text, flush=True)


def payload(reply):
    return reply if isinstance(reply, (dict, list)) else json.loads(str(reply))


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

    def inertia(self, **kwargs) -> dict:
        return payload(self.measure.execute("catia_get_inertia", kwargs))

    def close(self) -> None:
        try:
            self.docs.execute("catia_close_document",
                              {"document": self.path, "save": False})
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    root = os.path.join(tempfile.gettempdir(), "p0503")
    os.makedirs(root, exist_ok=True)

    # ── A: the session follows the moved solid ──────────────────────────
    say("A. after catia_move_body, which body does the next measurement read?")
    cell = Cell(conn, root, "ACC165_A")
    cell.rect("S", 0, 0, 20, 30)
    cell.design.execute("catia_pad", {"sketch_name": "S", "height": 20})
    cell.design.execute("catia_edit_pad", {"feature_name": "凸台.1", "height": 10})
    before = cell.inertia()
    reply = cell.design.execute("catia_move_body", {"dx": 50})
    say("  move: " + reply.split("\n")[1][:200])
    after = cell.inertia()
    say(f"  before: body={before.get('body')} cg={before.get('center_of_gravity_mm')}")
    say(f"  after : body={after.get('body')} cg={after.get('center_of_gravity_mm')} "
        f"visible={after.get('body_visible')}")
    check("A the move switches the active body to the moved one",
          after.get("body") == "零件几何体_MOVED", str(after.get("body")))
    cog = after.get("center_of_gravity_mm") or {}
    check("A and the measurement is the MOVED one (x = 60, not 10)",
          isinstance(cog, dict) and abs(float(cog.get("x", -1)) - 60.0) < 0.01,
          str(cog))
    check("A the reply says which body is active now",
          "The ACTIVE body is now '零件几何体_MOVED'" in reply
          and "PRE-move centre of gravity" in reply, reply[:200])
    check("A the volume did not change", abs(float(after.get("volume_mm3") or 0)
                                            - 6000.0) < 1.0,
          str(after.get("volume_mm3")))

    # ── B: list_bodies separates hidden from visible ────────────────────
    bodies = payload(cell.design.execute("catia_list_bodies", {}))
    say("  list_bodies: " + json.dumps(
        {k: bodies.get(k) for k in ("total_volume_mm3", "visible_volume_mm3",
                                    "hidden_volume_mm3")}, ensure_ascii=False))
    say("  bodies: " + json.dumps(
        [{k: entry.get(k) for k in ("name", "volume_mm3", "visible", "active")}
         for entry in bodies.get("bodies", [])], ensure_ascii=False))
    check("B the total is still every body (12000)",
          abs(float(bodies.get("total_volume_mm3") or 0) - 12000.0) < 1.0,
          str(bodies.get("total_volume_mm3")))
    check("B and the visible/hidden split is published (6000 / 6000)",
          abs(float(bodies.get("visible_volume_mm3") or 0) - 6000.0) < 1.0
          and abs(float(bodies.get("hidden_volume_mm3") or 0) - 6000.0) < 1.0,
          f"{bodies.get('visible_volume_mm3')} / {bodies.get('hidden_volume_mm3')}")
    check("B the note names the trap", "HIDDEN" in str(bodies.get("volume_note")),
          str(bodies.get("volume_note"))[:120])
    check("B each body carries a visibility reading",
          [entry.get("visible") for entry in bodies.get("bodies", [])]
          == [False, True],
          str([entry.get("visible") for entry in bodies.get("bodies", [])]))

    # ── A2: measuring the hidden source says so ─────────────────────────
    cell.design.execute("catia_set_active_body", {"name": "零件几何体"})
    source = cell.inertia()
    say(f"  source body read: body={source.get('body')} "
        f"visible={source.get('body_visible')} cg={source.get('center_of_gravity_mm')}")
    check("A2 the hidden source reads visible=false",
          source.get("body_visible") is False, str(source.get("body_visible")))
    check("A2 and the note says to look at the moved copy",
          "HIDDEN" in str(source.get("body_visible_note")),
          str(source.get("body_visible_note"))[:160])

    # ── A3: the document= route agrees with the active route ────────────
    named = cell.inertia(document="ACC165_A.CATPart")
    say(f"  document= route: body={named.get('body')} "
        f"source={named.get('body_source')} cg={named.get('center_of_gravity_mm')}")
    check("A3 document= measures the same body the active route would",
          named.get("body") == source.get("body")
          and named.get("body_source") == "active_body",
          f"{named.get('body')} / {named.get('body_source')}")
    cell.close()

    # ── C: a frame element can be deleted by name ───────────────────────
    say("C. the frame scaffolding: refused while in use, deletable when orphaned")
    cell = Cell(conn, root, "ACC165_C")
    cell.sk.execute("catia_create_sketch",
                    {"plane": {"type": "frame", "origin": [0, 0, 0],
                               "u": [1, 0, 0], "v": [0, 1, 0]},
                     "name": "T5_Empty"})
    cell.sk.execute("catia_close_sketch", {})
    part = conn.get_active_part()
    # 1. the plane while its sketch stands on it: refused, nothing deleted.
    try:
        cell.design.execute("catia_delete_feature",
                            {"feature_name": "T5_Empty_FRAME"})
        check("C a datum its sketch still stands on is refused", False,
              "the delete went through")
    except CodedError as error:
        check("C a datum its sketch still stands on is refused",
              error.code == "FRAME_DATUM_IN_USE", error.code)
        check("C and the refusal names the sketch to delete instead",
              "'T5_Empty'" in str(error) and "Nothing was deleted" in str(error),
              str(error)[:160])
    check("C nothing was deleted by that refusal",
          part.Bodies.Item(1).HybridShapes.Count == 4,
          str(part.Bodies.Item(1).HybridShapes.Count))
    # 2. delete the sketch, KEEPING the scaffolding - the orphaned state the
    #    report's part was in.
    cell.design.execute("catia_delete_feature",
                        {"feature_name": "T5_Empty",
                         "cleanup_frame_datum": "keep"})
    names = [str(part.Bodies.Item(1).HybridShapes.Item(i).Name)
             for i in range(1, part.Bodies.Item(1).HybridShapes.Count + 1)]
    say(f"  orphaned scaffolding: {names}")
    check("C the sketch went and the scaffolding stayed (as asked)",
          names and "T5_Empty" not in [str(part.Bodies.Item(1).Sketches.Item(i).Name)
                                       for i in range(1, part.Bodies.Item(1)
                                                      .Sketches.Count + 1)],
          str(names))
    # 3. delete it by name - the thing that answered "no such name" before.
    answer = cell.design.execute("catia_delete_feature", {"names": names})
    say("  delete: " + answer[:260].replace("\n", " | "))
    check("C the orphaned scaffolding is deleted, all of it",
          f"Deleted {len(names)} element(s)" in answer, answer[:160])
    check("C and the update at the end SUCCEEDED",
          "the part updates" in answer and "FINAL UPDATE FAILED" not in answer,
          answer[:200])
    left = [str(part.Bodies.Item(1).HybridShapes.Item(i).Name)
            for i in range(1, part.Bodies.Item(1).HybridShapes.Count + 1)]
    check("C and the tree agrees afterwards", left == [], str(left))
    listing = payload(cell.design.execute("catia_list_features", {}))
    check("C list_features no longer counts them",
          not listing.get("frame_scaffolding"), str(listing.get("frame_scaffolding")))
    # catia_update_part answers a PLAIN STRING ("Part updated successfully"), not
    # JSON - parsing it as JSON raised here, which is the same mistake the 0.3.160
    # acceptance made with catia_list_documents.
    update = str(cell.measure.execute("catia_update_part", {}))
    check("C the part still updates on its own", "successfully" in update,
          update[:120])
    cell.close()

    # ── C2: the report's own scenario - a failed plan, then the rollback ─
    say("C2. a failed plan with rollback: left and reply must agree")
    cell = Cell(conn, root, "ACC165_C2")
    cell.planner.bind({
        "catia_create_sketch": cell.sk, "catia_sketch_rectangle": cell.sk,
        "catia_sketch_profile": cell.sk, "catia_close_sketch": cell.sk,
        "catia_delete_feature": cell.design, "catia_pad": cell.design,
        "catia_activate_document": cell.docs, "catia_save_document": cell.docs,
        "catia_list_features": cell.design, "catia_update_part": cell.measure,
    })
    plan = payload(cell.planner.execute("catia_run_plan", {
        "steps": [
            {"tool": "catia_create_sketch",
             "args": {"plane": {"type": "frame", "origin": [0, 0, 0],
                                "u": [1, 0, 0], "v": [0, 1, 0]},
                      "name": "T5_Empty"}},
            {"tool": "catia_close_sketch", "args": {}},
            {"tool": "catia_pad", "args": {"sketch_name": "T5_Empty",
                                           "height": 5}},
        ],
        "rollback": True,
    }))
    data = plan.get("data") or {}
    rollback = data.get("rollback") or {}
    say("  left:  " + json.dumps(rollback.get("left"), ensure_ascii=False))
    say("  reply: " + str(rollback.get("reply"))[:260].replace("\n", " | "))
    part = conn.get_active_part()
    hybrids = [str(part.Bodies.Item(1).HybridShapes.Item(i).Name)
               for i in range(1, part.Bodies.Item(1).HybridShapes.Count + 1)]
    say(f"  body.HybridShapes afterwards: {hybrids}")
    check("C2 nothing is left behind",
          rollback.get("left") == [] and hybrids == [],
          f"left={rollback.get('left')} tree={hybrids}")
    check("C2 and the reply does not claim a name does not exist",
          "No feature, sketch, GSD element or body named" not in str(
              rollback.get("reply")), str(rollback.get("reply"))[:200])
    cell.close()

    # ── D: the wildcard delete the report said does not work ────────────
    say("D. catia_delete_feature(names=['倒圆角.*'])")
    cell = Cell(conn, root, "ACC165_D")
    cell.rect("S", 0, 0, 40, 40)
    cell.design.execute("catia_pad", {"sketch_name": "S", "height": 20})
    edges = payload(cell.design.execute("catia_list_edges", {}))
    rows = edges.get("edges") if isinstance(edges, dict) else edges
    vertical = [row["name"] for row in rows
                if abs(float(row.get("length_mm") or 0) - 20.0) < 0.01][:4]
    try:
        cell.design.execute("catia_fillet", {"radius": 2, "edges": vertical})
    except CodedError as error:
        say(f"  (the 4-edge batch refused with {error.code}; the fillets it did "
            "build are what this cell needs)")
    listing = payload(cell.design.execute("catia_list_features", {}))
    fillets = [entry.get("name") for entry in listing.get("features") or []
               if "倒圆角" in str(entry.get("name"))]
    say(f"  fillets in the tree: {fillets}")
    answer = cell.design.execute("catia_delete_feature", {"names": ["倒圆角.*"]})
    say("  " + answer[:220].replace("\n", " | "))
    check("D the pattern expands and deletes", bool(fillets)
          and f"matched {len(fillets)} element(s)" in answer
          and f"Deleted {len(fillets)} element(s)" in answer, answer[:200])
    cell.close()

    passed = sum(1 for _label, ok, _detail in CHECKS if ok)
    say()
    say(f"{passed}/{len(CHECKS)} checks passed")
    for label, ok, detail in CHECKS:
        if not ok:
            say(f"  FAILED: {label}  <- {detail}")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
