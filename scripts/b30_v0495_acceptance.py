"""0.3.158 acceptance on B30, 2026-11-21: the last four items of the report.

  A  P2-8  catia_diagnose is 80 222 characters and was truncated by the client.
           brief=true and sections=[...] are measured here: the whole report, the
           brief one, and a two-section one, plus the refusal for a bad name.
  B  P2-20 33/65/30 on the reporter's own P01 is reproduced, and the report's
           hypothesis (a consumed sketch wire coinciding with a solid edge) is
           tested: is any point in both lists?  Then the same check is run on a
           part built here, where the pocket profile lands exactly on an edge.
  C  P1-13 a mid-plane cut against a one-sided cut of the same window: the face
           count difference is the split the report blamed, and the reply has to
           say it before the caller counts faces.
  D  P2-15 a frame sketch whose datum another sketch still stands on: the BATCH
           delete used to leave the scaffold and say nothing.

One new document per cell.
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, ".")

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.diagnostics import DiagnosticsTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.planner import PlannerTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = tempfile.mkdtemp(prefix="acc0495_")
CHECKS: list[tuple[str, bool, str]] = []
P01 = ("F:\\WokrBuddy Catia Space\\catia文建-发动机曲柄活塞机构\\CATIA_out\\"
       "P01_Piston.CATPart")


def say(text: str = "") -> None:
    print(text, flush=True)


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
          + (f"  ({detail})" if detail else ""), flush=True)


def payload(reply):
    return reply if isinstance(reply, dict) else json.loads(reply)


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    planner = PlannerTools(conn)
    diagnostics = DiagnosticsTools(conn)
    diagnostics.tool_count = 155

    def box(name: str, x: float, y: float, z: float) -> str:
        path = os.path.join(ROOT, name + ".CATPart")
        docs.execute("catia_new_part", {"name": name, "file_path": path})
        sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "BOX"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": x, "y2": y})
        sketcher.execute("catia_close_sketch", {})
        part_design.execute("catia_pad", {"sketch_name": "BOX", "height": z})
        return path

    # ══ A: the size of the diagnosis ═════════════════════════════════════
    say("A: catia_diagnose - full, brief, and two sections")
    full = diagnostics.execute("catia_diagnose", {})
    brief = diagnostics.execute("catia_diagnose", {"brief": True})
    sectioned = diagnostics.execute("catia_diagnose",
                                    {"sections": ["mcp", "session"]})
    say(f"  full {len(full)} chars, brief {len(brief)}, sections {len(sectioned)}")
    full_data, brief_data = payload(full), payload(brief)
    check("A the full report is the big one", len(full) > 60_000, str(len(full)))
    check("A brief is less than a third of it", len(brief) * 3 < len(full),
          f"{len(brief)} vs {len(full)}")
    check("A brief keeps every section",
          set(brief_data) == set(full_data), str(sorted(set(full_data))))
    check("A brief keeps every ceiling ENTRY and its status",
          brief_data["capability_ceilings"]["count"] == len(
              full_data["capability_ceilings"])
          and all("status" in entry
                  for entry in brief_data["capability_ceilings"]["entries"]),
          str(brief_data["capability_ceilings"]["count"]))
    check("A brief replaces the prose with its length",
          "detail" not in brief_data["capability_ceilings"]["entries"][0]
          and brief_data["capability_ceilings"]["detail_withheld_chars"] > 10_000,
          str(brief_data["capability_ceilings"]["detail_withheld_chars"]))
    check("A sections returns exactly the keys asked for, in order",
          list(payload(sectioned)) == ["mcp", "session"], str(list(payload(sectioned))))
    check("A a two-section report is a few kilobytes",
          len(sectioned) < 5_000, str(len(sectioned)))
    try:
        diagnostics.execute("catia_diagnose", {"sections": ["nope"]})
        check("A an unknown section is refused", False, "it was accepted")
    except Exception as error:  # noqa: BLE001
        text = " ".join(str(error).split())
        check("A an unknown section is refused", "capability_ceilings" in text,
              text[:80])

    # ══ B: the vertex points, and the report's hypothesis ════════════════
    say("\nB: vertex points on the reporter's P01 and on a part built here")
    if os.path.isfile(P01):
        docs.execute("catia_open_document", {"file_path": P01})
        docs.execute("catia_activate_document",
                     {"name": os.path.basename(P01)})
        data = payload(planner.execute("catia_measure_topology",
                                       {"include_vertex_points": True}))
        say(f"  P01: {data['faces']} faces / {data['edges']} edges / "
            f"{data['vertices']} vertices, {len(data['vertex_points'])} points")
        check("B P01 reproduces the report's 33/65/30",
              (data["faces"], data["edges"], data["vertices"]) == (33, 65, 30),
              f"{data['faces']}/{data['edges']}/{data['vertices']}")
        check("B the point list has one entry per counted vertex",
              len(data["vertex_points"]) == data["vertices"],
              f"{len(data['vertex_points'])} vs {data['vertices']}")
        wire = data["construction_vertex_points"].get("sketch_wire_vertex", [])
        say(f"  {len(wire)} dropped sketch-wire corners")
        # THE HYPOTHESIS, tested: a dropped corner that sits ON a counted vertex
        # would be one the rule merged. Measured distance, not a count.
        nearest = min((min(math.dist(point, kept)
                           for kept in data["vertex_points"])
                       for point in wire), default=None)
        check("B no dropped corner coincides with a counted vertex",
              nearest is not None and nearest > 1.0,
              f"nearest {nearest:.3f} mm" if nearest is not None else "no points")
        docs.execute("catia_close_document", {"document": P01, "save": False})
    else:
        check("B P01 is not on this machine", False, P01)

    box("B1", 100, 60, 20)
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "NOTCH"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 40, "y1": 0, "x2": 60, "y2": 30})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pocket",
                        {"sketch_name": "NOTCH", "depth": 20, "direction": "up"})
    local = payload(planner.execute("catia_measure_topology",
                                    {"include_vertex_points": True}))
    say(f"  B1: {local['faces']} faces / {local['edges']} edges / "
        f"{local['vertices']} vertices")
    check("B a profile corner on the solid's edge is COUNTED, not merged",
          (local["faces"], local["edges"], local["vertices"]) == (10, 24, 16),
          f"{local['faces']}/{local['edges']}/{local['vertices']}")
    check("B and the point list agrees with the count",
          len(local["vertex_points"]) == 16, str(len(local["vertex_points"])))
    check("B the dropped cells are listed with a reason",
          bool(local.get("construction_vertex_points")), "")
    docs.execute("catia_close_document", {"save": False})

    # ══ C: mid-plane cut against one-sided cut of the same window ════════
    say("\nC: the same 20x20 window, cut through from the middle and from the top")
    box("C1", 100, 60, 20)
    sketcher.execute("catia_create_sketch",
                     {"plane": {"type": "offset", "base": "xy", "distance": 10},
                      "name": "MID"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 40, "y1": 15, "x2": 60, "y2": 35})
    sketcher.execute("catia_close_sketch", {})
    mid_reply = part_design.execute("catia_pocket",
                                    {"sketch_name": "MID", "depth": 40,
                                     "direction": "both"})
    mid = payload(planner.execute("catia_measure_topology", {}))
    say(f"  mid-plane: {mid['faces']} faces / {mid['edges']} edges / "
        f"{mid['vertices']} vertices")
    check("C the reply says two one-sided cuts", "two one-sided cuts" in mid_reply,
          " ".join(str(mid_reply).split())[:90])
    check("C and names the topology cost before the caller counts faces",
          "CAN cost faces" in mid_reply and "did NOT split anything" in mid_reply,
          "")
    docs.execute("catia_close_document", {"save": False})

    box("C2", 100, 60, 20)
    sketcher.execute("catia_create_sketch",
                     {"plane": {"type": "offset", "base": "xy", "distance": 20},
                      "name": "TOP"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 40, "y1": 15, "x2": 60, "y2": 35})
    sketcher.execute("catia_close_sketch", {})
    one_reply = part_design.execute("catia_pocket",
                                    {"sketch_name": "TOP", "depth": 20,
                                     "direction": "reverse"})
    one = payload(planner.execute("catia_measure_topology", {}))
    say(f"  one-sided: {one['faces']} faces / {one['edges']} edges / "
        f"{one['vertices']} vertices")
    check("C the one-sided cut does NOT carry the topology note",
          "CAN cost faces" not in one_reply, "")
    # THE CONTROL THE NOTE PROMISES, run: this window does NOT split when it goes
    # through from the middle of a plate. So the report's split is a property of
    # their geometry, not of direction='both' - which is what the reply now says.
    check("C a through-cut from the mid-plane splits nothing here",
          mid["faces"] == one["faces"] == 10,
          f"mid {mid['faces']} vs one-sided {one['faces']}")
    check("C and the edges agree too", mid["edges"] == one["edges"],
          f"{mid['edges']} vs {one['edges']}")
    docs.execute("catia_close_document", {"save": False})

    # ══ D: the frame scaffolding a batch delete leaves ════════════════════
    say("\nD: delete a frame sketch whose datum a second sketch still stands on")
    docs.execute("catia_new_part", {"name": "D1",
                                    "file_path": os.path.join(ROOT, "D1.CATPart")})
    frame = {"type": "frame", "origin": [0, 0, 0],
             "u": [1, 0, 0], "v": [0, 0, 1]}
    sketcher.execute("catia_create_sketch", {"plane": frame, "name": "FR_A"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 40, "y2": 30})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"sketch_name": "FR_A", "height": 10})
    sketcher.execute("catia_create_sketch", {"plane": frame, "name": "FR_B"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 50, "y1": 0, "x2": 70, "y2": 20})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"sketch_name": "FR_B", "height": 10})
    features = payload(part_design.execute("catia_list_features", {}))
    say(f"  frame scaffolding before: {features.get('frame_scaffolding')}")
    check("D two frame sketches left eight scaffold elements",
          (features.get("frame_scaffolding") or {}).get("element_count") == 8,
          str((features.get("frame_scaffolding") or {}).get("element_count")))

    # The pair, because a consumed sketch cannot go on its own - and that is the
    # call whose cascade used to leave the scaffold behind without a word.
    reply = part_design.execute("catia_delete_feature",
                                {"names": ["凸台.1", "FR_A"]})
    text = " ".join(str(reply).split())
    say(f"  batch delete reply: {text[:400]}")
    check("D the batch delete reports the scaffold it left",
          "Frame scaffolding stayed in the tree" in text, text[:90])
    check("D and names the sketch and the element count",
          "'FR_A'" in text and "(4 element(s))" in text, "")
    after = payload(part_design.execute("catia_list_features", {}))
    by_owner = {entry["sketch"]: entry["elements"]
                for entry in (after.get("frame_scaffolding") or {}).get("sketches") or []}
    check("D the four elements of the deleted sketch are really still there",
          by_owner.get("FR_A") == 4, str(by_owner))
    check("D and the surviving sketch keeps its own four", by_owner.get("FR_B") == 4,
          str(by_owner))
    check("D the reply names the by-name route out", "_FRAME.V" in text, "")
    docs.execute("catia_close_document", {"save": False})

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    say(f"\n{passed}/{len(CHECKS)} checks passed")
    say(f"workspace: {ROOT}")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
