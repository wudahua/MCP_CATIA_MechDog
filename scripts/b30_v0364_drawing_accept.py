"""Re-run the eleven drafting tools after the fixes, plus the three behaviours
that were added because of the first run:

  * add_view with NO part_name picks the part this session last built
  * add_view with part_name picks that one instead
  * face-driven projection works (topology_cells, localized search)
  * element index 1 is refused (the view's absolute axis)
  * a distance on elements that cannot carry it is explained, not forwarded
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.drawing import DraftingTools
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.sketcher import SketcherTools

RESULTS: list[tuple[str, bool, str]] = []


def say(message: str) -> None:
    print(message, flush=True)


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    say(f"   {'OK  ' if ok else 'FAIL'} {label}" + (f"  [{detail[:170]}]" if detail else ""))


def build_plate(docs, sketch, design, width=60.0, height=40.0, thickness=10.0):
    sketch.execute("catia_create_sketch", {"plane": "xy"})
    sketch.execute("catia_sketch_centered_rectangle",
                   {"width": width, "height": height})
    sketch.execute("catia_close_sketch", {})
    say("  " + design.execute("catia_pad", {"height": thickness}).splitlines()[0])


def main() -> None:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketch = SketcherTools(conn)
    design = PartDesignTools(conn)
    draw = DraftingTools(conn)

    for index in range(conn.documents.Count, 0, -1):
        try:
            conn.documents.Item(index).Close()
        except Exception:  # noqa: BLE001
            pass
    say(f"open at start: {conn.documents.Count}")

    say("\n=== build part A: 60x40x10 plate with a phi10 hole ===")
    docs.execute("catia_new_part", {})
    build_plate(docs, sketch, design)
    sketch.execute("catia_create_sketch", {"plane": "xy"})
    sketch.execute("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": 5})
    try:
        sketch.execute("catia_sketch_constraint",
                       {"type": "radius", "geometry_index_1": 2, "value": 5})
    except Exception as error:  # noqa: BLE001
        say(f"  radius constraint: {str(error).splitlines()[0][:90]}")
    sketch.execute("catia_close_sketch", {})
    design.execute("catia_hole", {"diameter": 10, "through_all": True})
    part_a = str(conn.active_document.Name)
    say(f"  part A = {part_a}   remembered = {conn.last_part_name}")

    say("\n=== build part B: a cylinder, so TWO parts are open ===")
    docs.execute("catia_new_part", {})
    sketch.execute("catia_create_sketch", {"plane": "xy"})
    sketch.execute("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": 12})
    sketch.execute("catia_sketch_constraint",
                   {"type": "radius", "geometry_index_1": 2, "value": 12})
    sketch.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"height": 25})
    part_b = str(conn.active_document.Name)
    say(f"  part B = {part_b}   remembered = {conn.last_part_name}")
    check("two parts open", True, f"{part_a}, {part_b}")

    say("\n=== D1 catia_new_drawing ===")
    text = draw.execute("catia_new_drawing", {})
    say("   " + text.splitlines()[0][:150])
    check("D1 new_drawing", "created" in text.lower())

    say("\n=== D11a add_view with NO part_name (must pick the remembered part B) ===")
    text = draw.execute("catia_drawing_add_view",
                        {"plane": "xy", "x": 120, "y": 200, "name": "Cyl View"})
    say("   " + text.splitlines()[0][:200])
    check("D11a remembered part", part_b in text and "added" in text, text[:140])

    say("\n=== D11b add_view with part_name = A (must override) ===")
    text = draw.execute("catia_drawing_add_view",
                        {"part_name": part_a, "plane": "xy",
                         "x": 120, "y": 60, "name": "Plate View"})
    say("   " + text.splitlines()[0][:200])
    check("D11b explicit part", part_a in text and "added" in text, text[:140])

    # Face.N ordering is NOT stable across builds: the same nominal plate gave
    # the hole's cylinder at Face.1 in one session and at Face.7 in another
    # (measured on B30 2026-11-05). So find a planar face HERE rather than
    # hard-coding an index that means something else tomorrow.
    part_doc_a = None
    for index in range(1, conn.documents.Count + 1):
        candidate = conn.documents.Item(index)
        if str(candidate.Name) == part_a:
            part_doc_a = candidate
            break
    planar_index = nonplanar_index = None
    for index in range(1, 12):
        try:
            draw._face_plane(part_doc_a, None, f"Face.{index}")
        except Exception as error:  # noqa: BLE001
            if "out of range" in str(error):
                break
            if nonplanar_index is None and "has no plane" in str(error):
                nonplanar_index = index
            continue
        if planar_index is None:
            planar_index = index
    say(f"   planar face found: Face.{planar_index}, non-planar: Face.{nonplanar_index}")

    say(f"\n=== D11c add_view face-driven (Face.{planar_index}) ===")
    started = time.perf_counter()
    try:
        text = draw.execute("catia_drawing_add_view",
                            {"part_name": part_a, "face": f"Face.{planar_index}",
                             "x": 330, "y": 200, "name": "Face View"})
        say(f"   {text.splitlines()[0][:200]}  ({time.perf_counter() - started:.1f}s)")
        check("D11c face-driven", "added" in text and f"plane of Face.{planar_index}" in text)
    except Exception as error:  # noqa: BLE001
        check("D11c face-driven", False, f"{type(error).__name__}: {str(error).splitlines()[0][:160]}")

    say(f"\n=== D11c2 face-driven on a non-planar face (Face.{nonplanar_index}) ===")
    try:
        draw.execute("catia_drawing_add_view",
                     {"part_name": part_a, "face": f"Face.{nonplanar_index}",
                      "name": "Bad"})
        check("non-planar face refused", False, "it was accepted")
    except Exception as error:  # noqa: BLE001
        message = str(error).splitlines()[0]
        check("non-planar face refused", "has no plane" in message and "cylindrical" in message,
              message[:160])

    say("\n=== D11d add_view with a bad face index ===")
    try:
        draw.execute("catia_drawing_add_view",
                     {"part_name": part_a, "face": "Face.99", "name": "Bad"})
        check("face out of range refused", False, "it was accepted")
    except Exception as error:  # noqa: BLE001
        message = str(error).splitlines()[0]
        check("face out of range refused", "out of range" in message and "catia_list_faces" in message,
              message[:150])

    say("\n=== D6 projection, D7 section, D8 detail on the plate view ===")
    text = draw.execute("catia_drawing_projection_view",
                        {"direction": "right", "parent_view": "Plate View",
                         "name": "Right View"})
    say("   " + text.splitlines()[0][:180])
    check("D6 projection", "added" in text)

    text = draw.execute("catia_drawing_section_view",
                        {"profile": [-30.0, 0.0, 30.0, 0.0],
                         "parent_view": "Plate View", "x": 330, "y": 60})
    say("   " + text.splitlines()[0][:190])
    check("D7 section", "added" in text)

    text = draw.execute("catia_drawing_detail_view",
                        {"center_x": 0, "center_y": 0, "radius": 14,
                         "parent_view": "Plate View", "scale": 2,
                         "x": 430, "y": 60})
    say("   " + text.splitlines()[0][:190])
    check("D8 detail", "added" in text)

    say("\n=== D9 text / D10 table on the Background View ===")
    text = draw.execute("catia_drawing_add_text",
                        {"text": "PLATE 60x40x10", "x": 20, "y": 190,
                         "view_name": "Background View"})
    check("D9 text", "added" in text, text[:120])
    text = draw.execute("catia_drawing_add_table",
                        {"x": 250, "y": 20, "rows": 2, "columns": 2,
                         "cells": [["PART", "PLATE"], ["QTY", "1"]],
                         "view_name": "Background View"})
    check("D10 table", "2 cell(s)" in text or "cell(s) filled" in text, text[:140])

    say("\n=== D5 generate_dimensions: cylinder view (1 constraint) vs plate view (0) ===")
    text = draw.execute("catia_drawing_generate_dimensions", {"view_name": "Cyl View"})
    say("   cyl:   " + text.replace("\n", " ")[:200])
    flat = text.replace(" ", "").replace("\n", "")
    check("D5 generated on cylinder", '"generated":1' in flat, flat[:120])

    text = draw.execute("catia_drawing_generate_dimensions", {"view_name": "Plate View"})
    say("   plate: " + text.replace("\n", " ")[:160])
    flat = text.replace(" ", "").replace("\n", "")
    check("D5 zero on unconstrained plate", '"generated":0' in flat, flat[:120])

    text = draw.execute("catia_drawing_list_dimensions", {"view_name": "Cyl View"})
    say("   list:  " + text.replace("\n", " ")[:220])
    check("D6b list_dimensions", "3d_driven" in text)

    say("\n=== D2/D3/D4 manual geometry + the axis guard ===")
    drawing = draw._find_active_drawing()
    view = drawing.Sheets.ActiveSheet.Views.Add("Manual View")
    view.x = 480
    view.y = 200
    try:
        view.Activate()
    except Exception:  # noqa: BLE001
        pass
    factory = view.Factory2D
    factory.CreateLine(0.0, 0.0, 40.0, 0.0)
    factory.CreateLine(0.0, 15.0, 40.0, 15.0)
    factory.CreateLine(0.0, 0.0, 0.0, 30.0)
    listing = draw.execute("catia_drawing_list_view_geometry", {"view_name": "Manual View"})
    say("   listing: " + listing.replace("\n", " ")[:200])
    check("D2 lists manual geometry", '"index":4' in listing.replace(" ", "").replace("\n", ""),
          listing[-60:])

    try:
        draw.execute("catia_drawing_add_dimension",
                     {"type": "length", "element_index_1": 1,
                      "view_name": "Manual View"})
        check("absolute axis refused", False, "index 1 was accepted")
    except Exception as error:  # noqa: BLE001
        message = str(error).splitlines()[0]
        check("absolute axis refused", "ABSOLUTE AXIS" in message and "index 2" in message,
              message[:150])

    text = draw.execute("catia_drawing_add_dimension",
                        {"type": "distance", "element_index_1": 2,
                         "element_index_2": 3, "view_name": "Manual View"})
    say("   parallel: " + text.splitlines()[0][:160])
    check("D3 distance on parallel lines", "added" in text)

    try:
        draw.execute("catia_drawing_add_dimension",
                     {"type": "distance", "element_index_1": 2,
                      "element_index_2": 4, "view_name": "Manual View"})
        check("perpendicular pair refused by CATIA, explained", False, "accepted")
    except Exception as error:  # noqa: BLE001
        message = str(error).splitlines()[0]
        check("perpendicular pair explained", "cannot" in message.lower() or "needs two elements" in message,
              message[:170])

    say("\n=== inventory ===")
    sheet = draw._find_active_drawing().Sheets.ActiveSheet
    for i in range(1, sheet.Views.Count + 1):
        v = sheet.Views.Item(i)
        try:
            kind = "generative" if v.IsGenerative() else "manual"
        except Exception:  # noqa: BLE001
            kind = "?"
        say(f"   {i}: {str(v.Name)!r:18s} {kind:11s} elements={v.GeometricElements.Count} "
            f"dims={v.Dimensions.Count}")

    say("\n=== screenshot ===")
    try:
        from catia_mcp.tools.export import ExportTools
        say("   " + ExportTools(conn).execute(
            "catia_screenshot",
            {"file_path": r"C:\Temp\v0364_drawing2.jpg"}).splitlines()[0][:150])
    except Exception as error:  # noqa: BLE001
        say(f"   failed: {str(error).splitlines()[0][:140]}")

    say("\n=== summary ===")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    say(f"   {passed}/{len(RESULTS)} passed")
    for label, ok, detail in RESULTS:
        if not ok:
            say(f"   FAILED: {label} - {detail[:170]}")

    say("\n=== close ===")
    for index in range(conn.documents.Count, 0, -1):
        try:
            conn.documents.Item(index).Close()
        except Exception as error:  # noqa: BLE001
            say(f"   close failed: {str(error).splitlines()[0][:90]}")
    say(f"open at end: {conn.documents.Count}")


if __name__ == "__main__":
    main()
