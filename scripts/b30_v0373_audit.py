"""0.3.73 on B30: the two items the 2026-11-06 reports left open.

  1. `set_component_transform` judged its read-back with `deviation < 1e-6`, so a
     spec table rounded to 4 decimals produced 13 false `matches_request: false`
     in 25 correct placements (P2-4). Now a tolerance, default 1 micron, and the
     knob is checked in both directions here.

  2. `set_visibility(kind=...)` refused a Product outright, so cleaning 11 parts
     took 33 calls (P2-6). Now it walks every part under the product through that
     part's OWN Selection - which also means the active document must not move.

The assembly is built here (two parts, each with a sketch and a pad), placed with
4-decimal unit vectors on purpose, then measured.
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
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def say(line: str) -> None:
    print(line, flush=True)


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    say(f"   {'OK  ' if ok else 'FAIL'} {label}"
        + (f"  [{detail[:170]}]" if detail else ""))


def activate(conn, name: str):
    for index in range(1, conn.documents.Count + 1):
        doc = conn.documents.Item(index)
        if doc.Name == name:
            doc.Activate()
            if conn.active_document.Name != name:
                raise RuntimeError(f"could not activate {name}")
            return doc
    raise RuntimeError(f"{name} is not open")


def close_all(conn) -> None:
    for index in range(conn.documents.Count, 0, -1):
        try:
            conn.documents.Item(index).Close()
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    conn = CATIAConnection()
    say(conn.connect())
    conn.app.DisplayFileAlerts = False
    close_all(conn)

    document = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    assembly = AssemblyTools(conn)

    workdir = tempfile.mkdtemp(prefix="catia_v0373_")
    a_path = os.path.join(workdir, "RA_BASE.CATPart")
    b_path = os.path.join(workdir, "RB_ARM.CATPart")
    asm_path = os.path.join(workdir, "R_ASM.CATProduct")
    say(f"workdir {workdir}")

    document.execute("catia_new_part", {"name": "RA_BASE", "file_path": a_path})
    activate(conn, "RA_BASE.CATPart")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0,
                                               "x2": 100, "y2": 60})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 20})
    # A body that exists in ONE part only, so section 4 has a name that is real
    # but not shared: "RB_ARM" is the DOCUMENT name, not a tree node.
    part_design.execute("catia_new_body", {"name": "RA_ONLY"})
    document.execute("catia_save_document", {})

    document.execute("catia_new_part", {"name": "RB_ARM", "file_path": b_path})
    activate(conn, "RB_ARM.CATPart")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": 15})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})
    document.execute("catia_save_document", {})

    document.execute("catia_new_product", {"name": "R_ASM",
                                          "file_path": asm_path})
    activate(conn, "R_ASM.CATProduct")
    assembly.execute("catia_add_component", {"file_path": a_path})
    assembly.execute("catia_add_component", {"file_path": b_path,
                                             "count": 2})
    listing = json.loads(assembly.execute("catia_list_components",
                                         {"names_only": True}))
    names = listing.get("names") or [e.get("name") for e in
                                     listing.get("components", [])]
    say(f"instances: {names}")

    say("")
    say("=== 1. P2-4: a 4-decimal unit vector is not a failure ─────────────")
    # A rotation about z by 30 degrees, rounded to 4 decimals exactly as a spec
    # table or a spreadsheet would carry it.
    c, s = round(math.cos(math.radians(30)), 4), round(math.sin(math.radians(30)), 4)
    rows = [[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]]
    say(f"   rotation rows (4 dp): {rows}")
    reply = json.loads(assembly.execute("catia_set_component_transform", {
        "component_name": names[1],
        "rotation_rows": rows,
        "translation": [200.0, 0.0, 0.0]}))
    verified = reply.get("verified") or {}
    say(f"   max_deviation={verified.get('max_deviation')} "
        f"tolerance={verified.get('tolerance')} "
        f"matches_request={verified.get('matches_request')} "
        f"warning={reply.get('warning')}")
    check("a 4-decimal placement is reported as matching",
          verified.get("matches_request") is True,
          f"dev={verified.get('max_deviation')}")
    check("the tolerance is published and is the 1 micron default",
          verified.get("tolerance") == 0.001,
          str(verified.get("tolerance")))
    check("no warning for a deviation below it", "warning" not in reply,
          str(reply.get("warning"))[:60])

    tight = json.loads(assembly.execute("catia_set_component_transform", {
        "component_name": names[1],
        "rotation_rows": rows,
        "translation": [200.0, 0.0, 0.0],
        "tolerance": 0.000001}))
    tight_verified = tight.get("verified") or {}
    say(f"   with tolerance=1e-6: matches_request="
        f"{tight_verified.get('matches_request')} warning="
        f"{str(tight.get('warning'))[:80]}")
    check("a tighter tolerance still reports the same deviation honestly",
          tight_verified.get("matches_request") is False
          and tight_verified.get("max_deviation", 0) > 0.000001,
          f"dev={tight_verified.get('max_deviation')}")

    batch = json.loads(assembly.execute("catia_set_component_transform", {
        "placements": [
            {"component_name": names[1], "translation": [200.0, 0.0, 0.0]},
            {"component_name": names[2], "translation": [0.0, 150.0, 0.0],
             "rotation_rows": rows},
        ]}))
    flags = [entry.get("matches_request") for entry in batch.get("placements", [])]
    say(f"   batch matches_request={flags} tolerance={batch.get('tolerance')} "
        f"worst={batch.get('worst_max_deviation')}")
    check("the batch judges every entry against one published tolerance",
          all(flag is True for flag in flags) and batch.get("tolerance") == 0.001,
          f"{flags} / {batch.get('tolerance')}")

    say("")
    say("=== 2. P2-6: set_visibility on a PRODUCT ──────────────────────────")
    active_before = conn.active_document.Name
    started = time.perf_counter()
    reply = part_design.execute("catia_set_visibility",
                                {"kind": "construction", "show": False})
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    say(f"   {elapsed} ms -> {reply}")
    check("the product-level call answers instead of refusing",
          "PRECONDITION_FAILED" not in reply and "no 'Part'" not in reply,
          reply[:120])
    check("the active document did not move", conn.active_document.Name == active_before,
          conn.active_document.Name)
    check("both parts were touched", "in 2 of 2 part(s)" in reply, reply[:120])

    # Read the state back through each part's own selection: 0 shows, 1 hides.
    # `GetShow()` answers a 2-tuple on this build and the flag is [1] -
    # measured here, after `[0]` read 0 in both states and made this check lie.
    states: list[tuple[str, int | None]] = []
    for index in range(1, conn.documents.Count + 1):
        doc = conn.documents.Item(index)
        name = str(doc.Name)
        if not name.upper().startswith(("RA_BASE", "RB_ARM")):
            continue
        try:
            part = conn.part_of(doc)
            sel = doc.Selection
            sel.Clear()
            sel.Add(part.Bodies.Item(1).Sketches.Item(1))
            states.append((name, int(sel.VisProperties.GetShow()[1])))
            sel.Clear()
        except Exception as error:  # noqa: BLE001
            states.append((name, None))
            say(f"   read-back failed for {name}: {str(error).splitlines()[0][:80]}")
    say(f"   GetShow() per part sketch: {states}")
    check("every sketch reads back HIDDEN",
          bool(states) and all(state == 1 for _n, state in states), str(states))

    say("")
    say("=== 3. and the same call puts them back ───────────────────────────")
    back = part_design.execute("catia_set_visibility",
                               {"kind": "construction", "show": True})
    say(f"   {back}")
    states = []
    for index in range(1, conn.documents.Count + 1):
        doc = conn.documents.Item(index)
        name = str(doc.Name)
        if not name.upper().startswith(("RA_BASE", "RB_ARM")):
            continue
        try:
            part = conn.part_of(doc)
            sel = doc.Selection
            sel.Clear()
            sel.Add(part.Bodies.Item(1).Sketches.Item(1))
            states.append((name, int(sel.VisProperties.GetShow()[1])))
            sel.Clear()
        except Exception:  # noqa: BLE001
            states.append((name, None))
    say(f"   GetShow() per part sketch: {states}")
    check("every sketch reads back SHOWN",
          bool(states) and all(state == 0 for _n, state in states), str(states))

    say("")
    say("=== 4. a name that exists in only one part ────────────────────────")
    one = part_design.execute("catia_set_visibility",
                              {"name": "RA_ONLY", "show": False})
    say(f"   {one}")
    check("a per-part name is applied where it exists and says so",
          "in 1 of 2 part(s)" in one, one[:130])
    try:
        part_design.execute("catia_set_visibility",
                            {"name": "NO_SUCH_NODE", "show": False})
        missing = "<returned instead of refusing>"
    except Exception as error:  # noqa: BLE001 - the refusal IS the datum
        missing = f"{type(error).__name__}: {error}"
    say(f"   missing node -> {missing[:200]}")
    check("a missing name is refused, not silently ignored",
          "No tree node named" in missing, "")

    say("")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    say(f"=== {passed}/{len(RESULTS)} checks passed ===")
    for label, ok, detail in RESULTS:
        if not ok:
            say(f"   FAILED: {label} [{detail}]")

    close_all(conn)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
