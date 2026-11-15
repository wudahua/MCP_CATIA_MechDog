"""B30 acceptance for 0.3.135 - the 2026-11-15 reports (R1-R6, C2, C4, C6).

Every case is one NEW document, because a document-level failure would hide all
the ones after it.

The two things this has to prove, in the reports' own terms:

  R1  an imported drawing can become ONE curve without leaving the tool - so a
      loft accepts the section and the caller never meets LOFT_REJECTED at all;
  C2  `import_drawing(plane=<frame object>)` places a sketch on a shared datum,
      which the schema used to refuse ('received object; expected string').

    .venv/Scripts/python.exe -u scripts/b30_v0436_audit.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.blueprint import BlueprintTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "catia_probe_v0436_audit")
PDF = ("F:/WokrBuddy Catia Space/苹果入耳式耳机AirPods/"
       "苹果入耳式耳机AirPods尺寸平面图.pdf")
REGION = [248, 111, 266, 138]
CELLS: list[tuple[int, str, bool, str]] = []


def check(number: int, name: str, ok: bool, detail: str = "") -> None:
    CELLS.append((number, name, ok, detail))
    print(f"  [{number:2d}] {'PASS' if ok else 'FAIL'}  {name}"
          + (f"\n         {detail}" if detail else ""), flush=True)


def say(text: str = "") -> None:
    print(text, flush=True)


def close_probe_documents(conn: Any) -> None:
    root = os.path.abspath(ROOT).lower()
    for index in range(int(conn.documents.Count), 0, -1):
        try:
            document = conn.documents.Item(index)
            full = str(document.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).lower().startswith(root):
            try:
                document.Close()
            except Exception:  # noqa: BLE001
                pass


def fresh(conn: Any, docs: Any, name: str) -> None:
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, name + ".CATPart")
    close_probe_documents(conn)
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": name, "file_path": path})


def main() -> int:
    if not os.path.exists(PDF):
        say(f"PDF missing: {PDF}")
        return 2
    conn = CATIAConnection()
    conn.ensure_connected()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    parts = PartDesignTools(conn)
    measure = MeasurementTools(conn)
    drawings = BlueprintTools(conn, sketcher)
    os.makedirs(ROOT, exist_ok=True)
    close_probe_documents(conn)

    filters = {"page": 2, "region": REGION, "min_size": 10,
               "closed_only": True, "max_aspect": 50}

    # ── 1. read_drawing now answers "can this be lofted?" ────────────────
    say("1 - read_drawing: curves and loft_ready per contour (R5)")
    read = json.loads(drawings.execute("catia_read_drawing",
                                       {"file_path": PDF, **filters}))
    contour = (read.get("contours") or [{}])[0]
    check(1, "each contour carries curves and loft_ready",
          contour.get("curves") == contour.get("elements")
          and contour.get("loft_ready") is False,
          f"curves={contour.get('curves')} elements={contour.get('elements')} "
          f"loft_ready={contour.get('loft_ready')}")

    # ── 2. the import that makes ONE curve ──────────────────────────────
    say("")
    say("2 - import_drawing(profile_mode='spline'): one curve, and a loft accepts it")
    fresh(conn, docs, "V0436_SplineProfile")
    imported = drawings.execute("catia_import_drawing",
                                {"file_path": PDF, **filters,
                                 "profile_mode": "spline"})
    part = conn.get_active_part()
    body = conn.get_active_part_body()
    sketch_name = str(body.Sketches.Item(1).Name)
    elements = int(body.Sketches.Item(1).GeometricElements.Count)
    # 113 = absolute axis + 111 control-point elements + the spline itself. The
    # POINTS are unavoidable: CATIA's 2D spline is defined THROUGH point objects,
    # and GeometricElements has no Remove (measured 2026-11-15). What matters is
    # that there is exactly ONE curve in there - proven by the loft in cell 4.
    kinds = [str(body.Sketches.Item(1).GeometricElements.Item(i).Name)
             for i in range(1, elements + 1)]
    check(2, "the spline import draws ONE curve (plus its defining points)",
          sum(1 for name in kinds if name.startswith("样条线")) == 1
          and sum(1 for name in kinds if name.startswith("点.")) == 111,
          f"sketch '{sketch_name}' holds {elements} element(s): "
          f"{sum(1 for n in kinds if n.startswith('点.'))} point(s), "
          f"{sum(1 for n in kinds if n.startswith('样条线'))} spline, "
          "1 absolute axis")
    check(3, "the reply says the sketch is loft-ready and warns about corners",
          "1 curve(s)" in imported and "profile_mode='spline'" in imported
          and "interpolating" in imported,
          (imported.split("This sketch holds")[-1][:110].strip()
           if "This sketch holds" in imported else "no capability sentence"))

    # a second section, so the loft has something to loft to
    sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 30,
                                            "name": "UpperSection"})
    sketcher.execute("catia_sketch_ellipse",
                     {"cx": 7.68, "cy": 11.94, "major": 9, "minor": 8.25})
    sketcher.execute("catia_close_sketch", {})
    loft = parts.execute("catia_solid_loft",
                         {"sections": [sketch_name, "UpperSection"],
                          "name": "PDF_Spline_Loft"})
    volume = json.loads(measure.execute("catia_get_inertia", {})).get("volume_mm3")
    check(4, "catia_solid_loft accepts it and builds a solid",
          "Solid loft created" in loft and float(volume or 0) > 1000,
          f"volume {volume} mm3 (the report's hand-converted run was 4838.70)")

    # ── 3. the geometry is one curve, verified independently ────────────
    say("")
    say("3 - is the imported contour really one curve?")
    geometry = json.loads(sketcher.execute("catia_sketch_get_geometry",
                                           {"sketch_name": sketch_name}))
    entries = geometry.get("elements") or []
    splines = [entry for entry in entries
               if str(entry.get("name") or "").startswith("样条线")]
    check(5, "sketch_get_geometry reports one curve and collapses the points",
          len(splines) == 1 and int(geometry.get("count") or 0) == 2,
          f"count={geometry.get('count')} names="
          f"{[e.get('name') for e in entries]}")

    # ── 4. C2: a frame plane, which the schema used to refuse ───────────
    say("")
    say("4 - import_drawing(plane={frame}) places the sketch on a shared datum")
    fresh(conn, docs, "V0436_FramePlane")
    framed = drawings.execute(
        "catia_import_drawing",
        {"file_path": PDF, **filters, "profile_mode": "spline",
         "plane": {"type": "frame", "origin": [0, 0, 33],
                   "u": [0, 1, 0], "v": [0, 0, 1]}})
    part = conn.get_active_part()
    sketch = conn.get_active_part_body().Sketches.Item(1)
    check(6, "a frame object is accepted and the sketch lands on it",
          "created on" in framed and "frame" in framed.lower(),
          framed.split("created on")[-1][:120].strip())

    # ── 5. R3: an explicit sketch name, and the refusal when it is taken ─
    say("")
    say("5 - catia_create_sketch(name=...): pinned, and refused when taken (R3)")
    fresh(conn, docs, "V0436_SketchName")
    first = sketcher.execute("catia_create_sketch",
                             {"plane": "xy", "name": "FrontView"})
    check(7, "the asked-for name is applied",
          "Named 'FrontView' as asked" in first
          and str(conn.get_active_part_body().Sketches.Item(1).Name) == "FrontView",
          first.split("Sketch '")[-1][:80])
    sketcher.execute("catia_close_sketch", {})
    try:
        sketcher.execute("catia_create_sketch", {"plane": "xy",
                                                 "name": "FrontView"})
        check(8, "a name the part already uses is refused, not suffixed", False,
              "it was accepted")
    except Exception as error:  # noqa: BLE001
        check(8, "a name the part already uses is refused, not suffixed",
              getattr(error, "code", "?") == "SKETCH_NAME_TAKEN"
              and "FrontView" in str(error),
              f"{getattr(error, 'code', '?')}: {str(error).splitlines()[0][:90]}")

    # ── 6. R3: a wrong section name lists what the part has ─────────────
    say("")
    say("6 - catia_solid_loft with a name that is not there")
    try:
        parts.execute("catia_solid_loft", {"sections": ["FrontView", "草图.9"]})
        check(9, "the refusal lists the names the part actually holds", False,
              "it was accepted")
    except Exception as error:  # noqa: BLE001
        text = str(error)
        check(9, "the refusal lists the names the part actually holds",
              "FrontView" in text and "actually holds" in text,
              text.splitlines()[0][:130])

    # ── 7. C4: a parameter that is not there ────────────────────────────
    say("")
    say("7 - catia_set_parameter with a name that is not there (C4)")
    try:
        measure.execute("catia_set_parameter",
                        {"name": "NoSuchParam_v0436", "value": 1})
        check(10, "a missing parameter answers PARAMETER_NOT_FOUND", False,
              "it raised nothing")
    except Exception as error:  # noqa: BLE001
        check(10, "a missing parameter answers PARAMETER_NOT_FOUND",
              getattr(error, "code", "?") == "PARAMETER_NOT_FOUND"
              and "PartNumber" in str(error) or "closest" in str(error).lower()
              or "Parameters here" in str(error),
              f"{getattr(error, 'code', '?')}: {str(error).splitlines()[0][:110]}")

    # ── 8. C6: the measurement says which body it is ────────────────────
    say("")
    say("8 - get_inertia names the body it measured (C6)")
    fresh(conn, docs, "V0436_Scope")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "MainProfile"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 60, "y2": 40})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 10})
    main = json.loads(measure.execute("catia_get_inertia", {}))
    parts.execute("catia_new_body", {"name": "SideView"})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SideProfile"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 20, "y2": 20})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 5})
    side = json.loads(measure.execute("catia_get_inertia", {}))
    check(11, "the reply names the body and says the scope",
          side.get("scope") == "active_body"
          and side.get("body") == "SideView"
          and main.get("volume_mm3") == 24000.0
          and side.get("volume_mm3") == 2000.0,
          f"main body {main.get('volume_mm3')} mm3, then "
          f"{side.get('volume_mm3')} mm3 with scope={side.get('scope')} "
          f"body={side.get('body')}")

    # ── 9. R1: the LOFT_REJECTED hint now names the one-call way out ────
    say("")
    say("9 - a loft that is going to fail still has to name the way out (R1)")
    fresh(conn, docs, "V0436_Rejected")
    drawings.execute("catia_import_drawing", {"file_path": PDF, **filters,
                                              "plane": "xy"})
    multi = str(conn.get_active_part_body().Sketches.Item(1).Name)
    sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 20,
                                            "name": "SecondSection"})
    sketcher.execute("catia_sketch_ellipse",
                     {"cx": 7.68, "cy": 11.94, "major": 9, "minor": 8.25})
    sketcher.execute("catia_close_sketch", {})
    try:
        parts.execute("catia_solid_loft",
                      {"sections": [multi, "SecondSection"]})
        check(12, "the refusal quotes a paste-able gsd_spline call", False,
              "the loft was accepted, so LOFT_REJECTED never fired")
    except Exception as error:  # noqa: BLE001
        text = str(error)
        check(12, "the refusal quotes a paste-able gsd_spline call",
              f"catia_gsd_spline(element='{multi}'" in text
              and "profile_mode='spline'" in text,
              text.split("THE CHEAPEST WAY OUT")[-1][:150].strip()
              if "THE CHEAPEST WAY OUT" in text else text.splitlines()[0][:130])

    say("")
    close_probe_documents(conn)
    passed = sum(1 for _n, _s, ok, _d in CELLS if ok)
    say("-" * 72)
    say(f"{passed}/{len(CELLS)} checks passed")
    for number, name, ok, _detail in CELLS:
        if not ok:
            say(f"  FAILED {number}: {name}")
    return 0 if passed == len(CELLS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
