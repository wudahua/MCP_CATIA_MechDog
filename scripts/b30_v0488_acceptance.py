"""0.3.153 acceptance on B30, 2026-11-20: the re-test of 0.3.152.

The report's three open items, each measured rather than described:

  A  P0 - `catia_fix_constraint` without force refuses at the VALIDATE stage,
     with modified:false and a CodedError code, so the caller never sees a bare
     COM_ERROR and CATIA is not asked at all.
  B  P0 - `force: true` still reaches CATIA and returns what CATIA says, which
     is what to use after a licence change.
  C  P0 - `catia_probe_assembly_constraints` answers with a verdict in one call:
     ENVIRONMENT_LIMITED here, with the sketch control passing, and the
     caller's documents untouched. Its UI-command step now ships the negative
     control that showed why the step cannot be read at all: StartCommand
     accepts a name that cannot exist (0.3.154 withdrew 0.3.152's inference).
  D  P2 - `method='vertices'` on a 582-vertex part now answers from the MESH
     (tighter and ~200x cheaper) instead of returning the box of a 200-vertex
     sample; `allow_truncated=true` still gives that sample, labelled.
  E  P6 - on an assembly the DEFAULT really is the mesh route, which is what the
     description now says (the code has said it since 0.3.114).

One new document per cell.
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
from catia_mcp.tools.diagnostics import DiagnosticsTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "catia_probe_v0488")
os.makedirs(ROOT, exist_ok=True)
CHECKS: list[tuple[str, bool, str]] = []


def say(text: str) -> None:
    print(text, flush=True)


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), detail))
    say(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def payload(reply) -> dict:
    return reply if isinstance(reply, dict) else json.loads(reply)


def outline(n: int = 194) -> list[list[float]]:
    points = []
    for i in range(n):
        angle = 2 * math.pi * i / n
        radius = 40 + 8 * math.cos(5 * angle)
        points.append([round(radius * math.cos(angle), 4),
                       round(radius * math.sin(angle), 4)])
    points.append(points[0])
    return points


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    asm = AssemblyTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    measurement = MeasurementTools(conn)
    diagnostics = DiagnosticsTools(conn)
    say(f"acceptance v0488 on {conn.app.Name}")

    try:
        while True:
            docs.execute("catia_close_document", {"save": False})
    except Exception:  # noqa: BLE001
        pass

    # ── one assembly with two instances, for A/B/E ────────────────────────
    lug = os.path.join(ROOT, "Lug.CATPart")
    if os.path.exists(lug):
        os.remove(lug)
    docs.execute("catia_new_part", {"name": "Lug", "file_path": lug})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "P"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 20,
                                                "y2": 10})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "P", "height": 5})
    asm_path = os.path.join(ROOT, "Asm.CATProduct")
    if os.path.exists(asm_path):
        os.remove(asm_path)
    docs.execute("catia_new_product", {"name": "Asm", "file_path": asm_path})
    asm.execute("catia_add_component", {"file_path": lug, "count": 2})
    product = conn.get_active_product()
    first = product.Products.Item(1).Name

    # ══ A: refused by declaration, before CATIA ═══════════════════════════
    say("\nA: catia_fix_constraint without force")
    started = time.perf_counter()
    try:
        asm.execute("catia_fix_constraint", {"component_name": first})
        check("A the constraint is refused", False, "it was created")
    except Exception as error:  # noqa: BLE001
        took = (time.perf_counter() - started) * 1000
        text = " ".join(str(error).split())
        say(f"  {type(error).__name__} in {took:.0f} ms: " + text[:200])
        check("A the code is UNSUPPORTED_CAPABILITY",
              getattr(error, "code", "") == "UNSUPPORTED_CAPABILITY",
              getattr(error, "code", "?"))
        check("A it is a VALIDATE-stage refusal",
              getattr(error, "stage", "") == "validate", getattr(error, "stage", "?"))
        check("A modified is a known no", getattr(error, "modified", None) is False,
              str(getattr(error, "modified", None)))
        # The stage is the proof: a validate-stage refusal happens before the
        # first COM call, so "CATIA was not asked" is not a claim, it is where
        # the error was raised.
        check("A it says CATIA was never called",
              "before CATIA was called" in text)
        for said in ("Selection.Item(1).Reference", "GetWorkbenchId", "SKETCH",
                     "catia_probe_assembly_constraints", "force: true"):
            check(f"A the refusal names '{said}'", said in text)
    check("A the assembly still has no constraints",
          int(product.Connections("CATIAConstraints").Count) == 0,
          str(product.Connections("CATIAConstraints").Count))

    # ══ B: force reaches CATIA and reports what it says ═══════════════════
    say("\nB: the same call with force: true")
    try:
        asm.execute("catia_fix_constraint", {"component_name": first,
                                            "force": True})
        check("B CATIA accepted the constraint", True, "a constraint was created")
    except Exception as error:  # noqa: BLE001
        text = " ".join(str(error).split())
        say("  " + text[:180])
        check("B force still reaches CATIA (E_FAIL is CATIA's own answer)",
              "AddMonoEltCst" in text)
        check("B and it is no longer a declaration refusal",
              getattr(error, "code", "") != "UNSUPPORTED_CAPABILITY",
              getattr(error, "code", "?"))

    # ══ C: one call, one verdict ══════════════════════════════════════════
    say("\nC: catia_probe_assembly_constraints")
    before_docs = [conn.documents.Item(i).Name
                   for i in range(1, conn.documents.Count + 1)]
    before_active = str(conn.active_document.Name)
    started = time.perf_counter()
    probe = diagnostics.execute("catia_probe_assembly_constraints", {})
    took = (time.perf_counter() - started) * 1000
    say(f"  {took:.0f} ms -> verdict={probe.get('verdict')}")
    for step in probe["data"]["steps"]:
        flag = {True: "PASS", False: "FAIL", None: "INFO"}[step["ok"]]
        say(f"    [{flag}] {step['step']}: {step['detail'][:90]}")
    check("C the verdict is ENVIRONMENT_LIMITED",
          probe.get("verdict") == "ENVIRONMENT_LIMITED", str(probe.get("verdict")))
    check("C the sketch control passes",
          probe["data"]["evidence"].get("sketch_control", {}).get("ok") is True)
    check("C the workbench id was read with the product active",
          probe["data"]["evidence"].get("workbench_id") == "Assembly",
          str(probe["data"]["evidence"].get("workbench_id")))
    # The UI-command step is now a CONTROL, not a reading: StartCommand accepts
    # any string on this build, so the thing to pin is that the probe says so
    # instead of calling a message box "the command is not registered" (which is
    # what 0.3.152 did, and what this round withdraws).
    ui = probe["data"]["evidence"].get("ui_command", {})
    check("C the UI-command step ships a negative control",
          ui.get("negative_control") == "ZzzNoSuchCommand", str(ui)[:120])
    check("C and reports that StartCommand does not validate names",
          ui.get("StartCommand_validates_names") is False, str(ui)[:120])
    check("C so the step is marked not informative",
          ui.get("informative") is False, str(ui)[:120])
    check("C the conclusion no longer blames the licence",
          "not established" in probe["message"].lower()
          and "not registered" not in probe["message"].lower(),
          probe["message"][:160])
    check("C it says the model is untouched",
          probe["data"].get("model_untouched") is True)
    after_docs = [conn.documents.Item(i).Name
                  for i in range(1, conn.documents.Count + 1)]
    check("C it left no document behind", after_docs == before_docs,
          f"{len(after_docs)} vs {len(before_docs)}")
    check("C and the caller's document is still active",
          str(conn.active_document.Name) == before_active,
          str(conn.active_document.Name))
    check("C the conclusion is paste-ready",
          "NOT AN MCP DEFECT" in probe["message"])

    # ══ E: the assembly default really is the mesh ════════════════════════
    say("\nE: get_bounding_box with the default method, on this assembly")
    box = payload(measurement.execute("catia_get_bounding_box", {}))
    sources = [entry.get("source") for entry in box.get("components", [])]
    say(f"  sources={sources} elapsed={box.get('elapsed_ms')} ms")
    say("  mesh_route: " + str(box.get("mesh_route"))[:120])
    check("E every component was bounded from its mesh",
          sources and all(source == "mesh" for source in sources), str(sources))
    check("E the reply says auto is the mesh route",
          "mesh" in str(box.get("mesh_route", "")).lower())

    # ══ F: the same rule on an ASSEMBLY, where the report paid 200 s ══════
    say("\nF: an assembly holding a fresh 582-vertex part, method='vertices'")
    heavy_asm_part = os.path.join(ROOT, "HeavyAsm.CATPart")
    if os.path.exists(heavy_asm_part):
        os.remove(heavy_asm_part)
    docs.execute("catia_new_part", {"name": "HeavyAsm",
                                    "file_path": heavy_asm_part})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "OUT"})
    sketcher.execute("catia_sketch_strokes", {"strokes": [outline()]})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "OUT", "height": 6})
    docs.execute("catia_save_document", {})
    docs.execute("catia_close_document", {"save": False})

    asm3 = os.path.join(ROOT, "HeavyAsm.CATProduct")
    if os.path.exists(asm3):
        os.remove(asm3)
    docs.execute("catia_new_product", {"name": "HeavyAsm", "file_path": asm3})
    asm.execute("catia_add_component", {"file_path": heavy_asm_part, "count": 2})
    asm.execute("catia_add_component", {"file_path": lug, "count": 1})
    started = time.perf_counter()
    cold = payload(measurement.execute("catia_get_bounding_box",
                                       {"method": "vertices"}))
    took = (time.perf_counter() - started) * 1000
    entries = cold.get("components", [])
    say(f"  {took:.0f} ms, sources="
        + str([(e.get("component"), e.get("source"),
                e.get("mesh_reason")) for e in entries])[:300])
    sources = {e.get("component"): e for e in entries}
    heavy_rows = [e for e in entries if str(e.get("component", "")).startswith("HeavyAsm")]
    check("F the heavy part was NOT swept vertex by vertex",
          heavy_rows and all(e.get("source") in ("mesh", "mesh_fallback")
                             for e in heavy_rows),
          str([e.get("source") for e in heavy_rows]))
    check("F the reply names the reason",
          any(str(e.get("mesh_reason")) == "more_vertices_than_the_cap"
              for e in heavy_rows),
          str([e.get("mesh_reason") for e in heavy_rows]))
    check("F the small part still used its vertices",
          any(e.get("source") == "vertices" for e in entries),
          str([e.get("source") for e in entries]))
    check("F the whole call took seconds, not the report's 200 s",
          took < 60_000, f"{took:.0f} ms")
    if heavy_rows:
        say(f"  heavy box: {heavy_rows[0].get('dimensions')}")
    started = time.perf_counter()
    again = payload(measurement.execute("catia_get_bounding_box",
                                        {"method": "vertices"}))
    warm = (time.perf_counter() - started) * 1000
    say(f"  second call: {warm:.0f} ms, sources="
        + str([e.get("source") for e in again.get("components", [])]))
    check("F the second call is cheap once the meshes exist", warm < 10_000,
          f"{warm:.0f} ms")

    # ══ D: the 200-vertex sample is no longer the default answer ══════════
    say("\nD: the 194-point outline part (582 vertices)")
    heavy = os.path.join(ROOT, "Heavy.CATPart")
    if os.path.exists(heavy):
        os.remove(heavy)
    docs.execute("catia_new_part", {"name": "Heavy", "file_path": heavy})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "OUT"})
    sketcher.execute("catia_sketch_strokes", {"strokes": [outline()]})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "OUT", "height": 6})

    started = time.perf_counter()
    default = payload(measurement.execute("catia_get_bounding_box",
                                          {"method": "vertices"}))
    took = (time.perf_counter() - started) * 1000
    say(f"  vertices (default): {default.get('min')} .. {default.get('max')} "
        f"in {took:.0f} ms, code={default.get('bounds_code')}")
    say("  method: " + str(default.get("method"))[:200])
    check("D it answered from the mesh, not from a sample",
          default.get("points_route") == "mesh", str(default.get("points_route")))
    check("D the box reaches the far side (46.1033)",
          abs(float(default["max"]["y"]) - 46.1033) < 0.01,
          str(default["max"]["y"]))
    check("D it took seconds, not 40 000 ms", took < 20_000, f"{took:.0f} ms")
    check("D the reply explains why the route changed",
          "582 vertices" in str(default.get("method"))
          and "allow_truncated=true" in str(default.get("method")))

    sample = payload(measurement.execute("catia_get_bounding_box",
                                         {"method": "vertices",
                                          "allow_truncated": True}))
    say(f"  allow_truncated: {sample.get('min')} .. {sample.get('max')} "
        f"code={sample.get('bounds_code')}")
    check("D allow_truncated still returns the labelled sample",
          sample.get("bounds_code") == "vertex_sweep_truncated",
          str(sample.get("bounds_code")))
    check("D and says how many vertices it missed",
          (sample.get("vertices_missed") or 0) > 0,
          str(sample.get("vertices_missed")))

    say("")
    passed = sum(1 for _label, ok, _detail in CHECKS if ok)
    say(f"{passed}/{len(CHECKS)} checks passed")
    for label, ok, detail in CHECKS:
        if not ok:
            say(f"  FAILED: {label}  {detail}")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
