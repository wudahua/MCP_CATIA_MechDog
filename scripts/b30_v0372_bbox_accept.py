"""0.3.72 on B30: the assembly mesh box, its cache, and what `auto` costs.

The 2026-11-06 report (P1-3, P3-10) made three claims about a 25-instance
product. Each is checked here against a product whose true envelope is known:

    BB_BOX    100 x 60 x 20 pad          every extreme is a vertex
    BB_ROUND  cylinder R20 + 10x10 tab   the extremes in -x and +-y are on the
              at x=60                    curved face, and it HAS vertices (the
                                         tab's) so `auto` cannot fall back
    instance 1 at the origin, instance 2 at (200,0,0) rotated +90 deg about z

    true envelope   x [-50, 220]  y [-30, 65]  z [0, 20]
    vertex sweep    x [-50, 205]  y [-30, 65]  z [0, 20]   (15 mm short in x)

The scenario is the report's: the parts are on disk and the PRODUCT is reopened,
so every component is loaded in context (measured: `Activate()` on such a part
does not change ActiveDocument, which is how `_edited_in_context` detects it).

  1. is `method='mesh'` refused for a Product? (the report's P1-3.1)
  2. do `exact` / `bounds_code` tell the two routes apart? (P1-3.2)
  3. does a second call reuse the per-part cache instead of re-exporting? (P3-10)
  4. is a part with unsaved edits kept OUT of that cache?
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools, _MESH_CACHE  # noqa: E402
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


def box_of(measurement, label: str, arguments: dict) -> dict:
    started = time.perf_counter()
    raw = measurement.execute("catia_get_bounding_box", arguments)
    wall = round((time.perf_counter() - started) * 1000, 1)
    try:
        reply = json.loads(raw)
    except json.JSONDecodeError:
        say(f"{label:34} not json: {raw[:140]}")
        return {}
    low, high = reply.get("min") or {}, reply.get("max") or {}
    say(f"{label:34} x[{low.get('x')}, {high.get('x')}] "
        f"y[{low.get('y')}, {high.get('y')}] z[{low.get('z')}, {high.get('z')}]")
    say(f"{'':34} wall={wall} ms  elapsed={reply.get('elapsed_ms')} ms  "
        f"complete={reply.get('complete')} exact={reply.get('exact')} "
        f"bounds_code={reply.get('bounds_code')} parts_ms={reply.get('parts_ms')}")
    if "code" in reply:
        say(f"{'':34} code={reply['code']}: {str(reply.get('message'))[:140]}")
    if reply.get("mesh_cache"):
        say(f"{'':34} mesh_cache={reply['mesh_cache']}")
    for part in reply.get("parts") or []:
        say(f"{'':34}   {part.get('part'):16} src={part.get('source'):13} "
            f"cache={part.get('mesh_cache')} {part.get('elapsed_ms')} ms")
    return reply


def main() -> int:
    conn = CATIAConnection()
    say(conn.connect())
    conn.app.DisplayFileAlerts = False
    close_all(conn)

    document = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    assembly = AssemblyTools(conn)
    measurement = MeasurementTools(conn)

    workdir = tempfile.mkdtemp(prefix="catia_bbox72a_")
    box_path = os.path.join(workdir, "QA_BOX.CATPart")
    round_path = os.path.join(workdir, "QA_ROUND.CATPart")
    asm_path = os.path.join(workdir, "QA_ASM.CATProduct")
    say(f"workdir {workdir}")

    document.execute("catia_new_part", {"name": "QA_BOX", "file_path": box_path})
    activate(conn, "QA_BOX.CATPart")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": -50, "y1": -30,
                                               "x2": 50, "y2": 30})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 20})
    document.execute("catia_save_document", {})

    document.execute("catia_new_part", {"name": "QA_ROUND",
                                       "file_path": round_path})
    activate(conn, "QA_ROUND.CATPart")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": 20})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 20})
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 55, "y1": -5,
                                               "x2": 65, "y2": 5})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 20})
    document.execute("catia_save_document", {})

    document.execute("catia_new_product", {"name": "QA_ASM",
                                          "file_path": asm_path})
    activate(conn, "QA_ASM.CATProduct")
    assembly.execute("catia_add_component", {"file_path": box_path})
    assembly.execute("catia_add_component", {"file_path": round_path,
                                             "count": 2})
    listing = json.loads(assembly.execute("catia_list_components",
                                         {"names_only": True}))
    names = listing.get("names") or [entry.get("name")
                                     for entry in listing.get("components", [])]
    say(f"instances: {names}")
    second = next((name for name in names
                   if name.upper().startswith("QA_ROUND") and not name.endswith(".1")),
                  None)
    if second:
        assembly.execute("catia_set_component_transform", {
            "placements": [{"component_name": second,
                            "translation": [200.0, 0.0, 0.0],
                            "rotation_rows": [[0.0, -1.0, 0.0],
                                              [1.0, 0.0, 0.0],
                                              [0.0, 0.0, 1.0]]}],
            "rotation_convention": "matrix"})
        say(f"{second} placed at (200, 0, 0), rotated +90 deg about z")
    document.execute("catia_save_document", {})

    # The report's state: parts on disk, the product reopened, nothing edited.
    close_all(conn)
    _MESH_CACHE.clear()
    document.execute("catia_open_document", {"file_path": asm_path})
    for index in range(1, conn.documents.Count + 1):
        doc = conn.documents.Item(index)
        say(f"open: {doc.Name}  Saved={doc.Saved}")
    say("")

    say("=== 1. is method='mesh' refused for a Product? ===")
    mesh_first = box_of(measurement, "product, mesh (1st)",
                        {"document": "QA_ASM.CATProduct", "method": "mesh"})
    check("method='mesh' on a Product is not refused",
          "code" not in mesh_first, str(mesh_first.get("code") or ""))
    high = (mesh_first.get("max") or {}).get("x")
    check("its envelope reaches the rotated instance (xmax 220)",
          high is not None and abs(high - 220.0) <= 0.6, f"xmax={high}")
    check("and the true -y extreme (ymin -30)",
          abs(((mesh_first.get("min") or {}).get("y") or 0) + 30.0) <= 0.6,
          f"ymin={(mesh_first.get('min') or {}).get('y')}")
    check("mesh route reports exact=true / bounds_code=mesh_chord",
          mesh_first.get("exact") is True
          and mesh_first.get("bounds_code") == "mesh_chord",
          f"{mesh_first.get('exact')} / {mesh_first.get('bounds_code')}")

    say("")
    say("=== 2. what the default (`auto`) does with the same product ===")
    auto_reply = box_of(measurement, "product, auto",
                        {"document": "QA_ASM.CATProduct", "method": "auto"})
    auto_high = (auto_reply.get("max") or {}).get("x")
    check("auto is short in x, and says so with exact=false",
          auto_reply.get("exact") is False
          and auto_reply.get("bounds_code") == "vertex_sweep",
          f"exact={auto_reply.get('exact')} code={auto_reply.get('bounds_code')}")
    check("the gap is the one the report describes",
          auto_high is not None and high is not None and 14.0 <= high - auto_high <= 16.0,
          f"mesh {high} - auto {auto_high}")
    check("complete is true on both routes (coverage, not accuracy)",
          mesh_first.get("complete") is True and auto_reply.get("complete") is True,
          f"{mesh_first.get('complete')} / {auto_reply.get('complete')}")

    say("")
    say("=== 3. the second call: does the per-part cache pay? ===")
    mesh_second = box_of(measurement, "product, mesh (2nd)",
                         {"document": "QA_ASM.CATProduct", "method": "mesh"})
    flags = [part.get("mesh_cache") for part in mesh_second.get("parts") or []]
    check("every part is reused, not re-exported",
          bool(flags) and all(flag in ("reused", "kept") for flag in flags),
          str(flags))
    first_ms = mesh_first.get("elapsed_ms") or 0.0
    second_ms = mesh_second.get("elapsed_ms") or 0.0
    check("and the call is at least twice as fast",
          second_ms and second_ms * 2 < first_ms,
          f"{first_ms} ms -> {second_ms} ms")
    check("the same numbers came back",
          (mesh_second.get("max") or {}) == (mesh_first.get("max") or {})
          and (mesh_second.get("min") or {}) == (mesh_first.get("min") or {}),
          "")

    say("")
    say("=== 4. a part with unsaved edits is not answered from the cache ===")
    # The cache now holds QA_ROUND's box (xmax 65) under that file. Edit the part
    # WITHOUT saving and measure again: a file-keyed cache would answer 65, and
    # that is the one mistake this gate exists to prevent.
    #
    # The product has to be closed first: `catia_open_document` on a part that is
    # loaded inside an open assembly ACTIVATES it instead of giving it a window,
    # so the pad below would land in the wrong document (or fail to activate).
    close_all(conn)
    document.execute("catia_open_document", {"file_path": round_path})
    activate(conn, "QA_ROUND.CATPart")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_circle", {"cx": 85, "cy": 0, "radius": 20})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 20})
    round_doc = None
    for index in range(1, conn.documents.Count + 1):
        doc = conn.documents.Item(index)
        if str(doc.Name).upper().startswith("QA_ROUND"):
            round_doc = doc
    say(f"   QA_ROUND Saved after the edit: {round_doc.Saved}")

    edited = box_of(measurement, "QA_ROUND alone, mesh (edited)",
                    {"document": "QA_ROUND.CATPart", "method": "mesh"})
    flags = edited.get("mesh_cache")
    check("the edited part is labelled unsaved, not reused",
          flags == "unsaved", str(flags))
    check("and the edit is in the numbers (xmax 105)",
          abs(((edited.get("max") or {}).get("x") or 0) - 105.0) <= 0.6,
          f"xmax={(edited.get('max') or {}).get('x')}")

    say("")
    say("=== 5. catia_check_surface still gets its STL (it reads the file, not "
        "the box) ===")
    for attempt in (1, 2):
        raw = measurement.execute("catia_check_surface", {"document": "QA_ROUND.CATPart"})
        reply = json.loads(raw)
        triangles = reply.get("triangles")
        fold = (reply.get("fold_deg") or {}).get("median")
        say(f"   attempt {attempt}: code={reply.get('code')} triangles={triangles} "
            f"fold_deg.median={fold} error={str(reply.get('message'))[:90]}")
        check(f"check_surface reads the mesh on call {attempt}",
              "code" not in reply and bool(triangles), str(reply.get("code") or ""))

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
