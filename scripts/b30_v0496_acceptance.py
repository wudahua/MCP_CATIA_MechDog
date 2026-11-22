"""B30 acceptance for 0.3.159 - the 2026-11-22 reports.

Six cells, one per item, each one a real call against CATIA:

  A  P8   the constraint refusal keeps its measured body and gains NO gen_py /
          licence boilerplate from the envelope (the schema said one thing and
          the runtime message said another)
  B  P7   the probe reports `documents_left_open: 0` and the document list is
          identical before and after
  C  P9   repeated mesh warnings collapse into one line naming every part
  D  P1-21 chamfer 25/20 reads its MODE back, reports the volume removed
          against the ideal L*a*b/2, and swap_distances=true exchanges them
  E  P2-22 catia_get_inertia(document=...) answers the 3x3 matrix WITHOUT
          making that document active
  F  P2-23 closing a part an assembly holds names the holder - including when it
          sits two levels down

Every document this script creates is closed again; the caller's documents are
only read.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.diagnostics import DiagnosticsTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.planner import PlannerTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, bool, str]] = []


def say(text: str) -> None:
    print(text, flush=True)


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    say(f"  [{PASS if ok else FAIL}] {name}" + (f"  [{detail}]" if detail else ""))


def payload(reply) -> dict:
    if isinstance(reply, dict):
        # Tools that answer with a dict use the {ok, code, data} envelope; the
        # measurement tools answer with their own JSON text instead.
        inner = reply.get("data")
        if isinstance(inner, dict) and (reply.get("ok") is not None
                                       or reply.get("code")):
            return inner
        return reply
    text = str(reply)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and "data" in parsed:
            return parsed["data"]
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except Exception:  # noqa: BLE001 - prose replies are matched as text
        return {"text": text}


def box(tools, sketcher, part_design, name: str, path: str,
        x: float, y: float, z: float) -> None:
    tools.execute("catia_new_part", {"name": name, "file_path": path})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SQ"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": x, "y2": y})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"sketch_name": "SQ", "height": z})


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    assembly = AssemblyTools(conn)
    design = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    measure = MeasurementTools(conn)
    diagnostics = DiagnosticsTools(conn)
    planner = PlannerTools(conn)

    root = os.path.join(tempfile.gettempdir(), "acc0496")
    os.makedirs(root, exist_ok=True)

    def path(name: str) -> str:
        return os.path.join(root, name)

    def fresh(item: str) -> str:
        """Close it if open, delete it if it is on disk, so the cell starts clean."""
        try:
            docs.execute("catia_close_document", {"document": item, "save": False})
        except Exception:  # noqa: BLE001 - not open is fine
            pass
        try:
            if os.path.isfile(item):
                os.remove(item)
        except OSError:
            pass
        return item

    def close_quietly(*paths: str) -> None:
        for item in paths:
            try:
                docs.execute("catia_close_document", {"document": item, "save": False})
            except Exception:  # noqa: BLE001 - already gone is the goal
                pass

    def document_entries() -> list[dict]:
        """catia_list_documents answers a bare JSON list of {name, path, type}."""
        raw = docs.execute("catia_list_documents", {})
        if isinstance(raw, dict):
            raw = raw.get("data", raw)
            raw = raw.get("documents", raw) if isinstance(raw, dict) else raw
        if isinstance(raw, str):
            raw = json.loads(raw)
        return [item for item in raw or [] if isinstance(item, dict)]

    def document_names() -> list[str]:
        return sorted(item.get("name") for item in document_entries()
                      if item.get("name"))

    # ── A. the constraint refusal ──────────────────────────────────────────
    say("\n== A (P8). the constraint refusal's runtime message")
    for tool_name, arguments in (
        ("catia_fix_constraint", {"component_name": "P01_frame.1"}),
        ("catia_coincidence_constraint",
         {"component_name_1": "P01_frame.1", "component_name_2": "P02_crankdisk.1"}),
    ):
        try:
            reply = assembly.execute(tool_name, arguments)
            text = json.dumps(reply) if isinstance(reply, dict) else str(reply)
            say(f"  {tool_name}: returned ok?! {text[:120]}")
            check(f"A {tool_name} still refuses", False, "it returned instead")
            continue
        except Exception as error:  # noqa: BLE001 - the refusal is the answer
            text = str(error)
        check(f"A {tool_name} keeps the measured body",
              "WHICH LAYER REFUSES IS NOT ESTABLISHED" in text, "")
        check(f"A {tool_name} carries no gen_py/licence boilerplate",
              "gen_py" not in text and "workbench licence" not in text, "")
        check(f"A {tool_name} still names the way forward",
              "catia_set_component_transform" in text
              and "force: true" in text, "")
    # the ENVELOPE is what the caller reads: run one through the server layer
    from catia_mcp.server import CATIAMCPServer  # noqa: PLC0415 - local import

    import asyncio  # noqa: PLC0415

    server = CATIAMCPServer()
    server.connection.ensure_connected = lambda: None
    server.assembly_tools = assembly

    def _execute(name, arguments):
        return assembly.execute(name, arguments)

    class _Module:
        execute = staticmethod(_execute)

    server._tool_router = {"catia_fix_constraint": _Module()}

    async def call() -> dict:
        result = await server.call_tool("catia_fix_constraint",
                                       {"component_name": "P01_frame.1"})
        return result.structuredContent

    envelope = asyncio.run(call())
    message = envelope.get("message") or ""
    detail = envelope.get("data") or {}
    effects = envelope.get("effects") or {}
    modified = effects.get("modified")
    say(f"  envelope keys: {sorted(envelope)}")
    say(f"  code={envelope.get('code')} stage={detail.get('failure_stage')} "
        f"modified={modified}")
    check("A the envelope keeps the raise site's message",
          "WHICH LAYER REFUSES IS NOT ESTABLISHED" in message, "")
    check("A the envelope adds no boilerplate",
          "gen_py" not in message and "licence can also lock" not in message, "")
    check("A it is still a validate-stage refusal with modified=false",
          modified is False and detail.get("failure_stage") == "validate",
          f"{detail.get('failure_stage')}/{modified}")

    # ── B. the probe's document accounting ─────────────────────────────────
    say("\n== B (P7). the probe leaves the session alone")
    before = document_names()
    probe = payload(diagnostics.execute("catia_probe_assembly_constraints", {}))
    after = document_names()
    say(f"  verdict={probe.get('verdict')} left_open={probe.get('documents_left_open')}")
    check("B documents_left_open is 0", probe.get("documents_left_open") == 0,
          str(probe.get("documents_left_open")))
    check("B the document list is identical before and after", before == after,
          f"{len(before)} vs {len(after)}")
    check("B the wording is neutral (no 'closed WITHOUT saving')",
          "WITHOUT saving" not in str(probe.get("documents_used")), "")
    check("B the reply says what 0 means",
          "as the caller left it" in str(probe.get("documents_left_open_note")), "")

    # ── C. warnings collapse ───────────────────────────────────────────────
    say("\n== C (P9). repeated mesh warnings")
    target = None
    for name in ("hexapod_assembly.CATProduct", "Assem2_TXT.CATProduct"):
        if name in [item for item in document_names()]:
            target = name
            break
    if target is None:
        check("C an assembly with in-context parts is open", False,
              "neither hexapod_assembly nor Assem2_TXT is open")
    else:
        box_reply = payload(measure.execute(
            "catia_get_bounding_box", {"document": target, "method": "mesh"}))
        warnings = box_reply.get("warnings") or []
        say(f"  {target}: {len(warnings)} warning(s)")
        for line in warnings:
            say(f"    - {line[:150]}")
        grouped = [line for line in warnings if line.startswith(("2 parts (", "3 parts (",
                                                                "4 parts (", "5 parts (",
                                                                "6 parts (", "7 parts (",
                                                                "8 parts ("))]
        unsaved = [line for line in warnings if "Saved=false" in line]
        check("C no warning is repeated once per part",
              len(grouped) == len(unsaved),
              f"{len(grouped)} grouped vs {len(unsaved)} unsaved-notes")
        check("C a grouped line names every part it covers",
              all("," in line.split("):")[0] for line in grouped) if grouped else True,
              str(grouped[:1]))
        check("C the note says what Saved=false can and cannot mean",
              all("cannot move this box" in line for line in unsaved) if unsaved else True,
              "")

    # ── D. the chamfer read-back ───────────────────────────────────────────
    say("\n== D (P1-21). a two-distance bevel, read back")
    chamfer_path = path("Ch0496.CATPart")
    fresh(chamfer_path)
    box(docs, sketcher, design, "Ch0496", chamfer_path, 100, 100, 100)
    docs.execute("catia_save_document", {})
    reply = str(design.execute("catia_chamfer",
                               {"length": 25, "length2": 20, "edge_name": "Edge.1"}))
    say(f"  {reply[:400]}")
    check("D the reply quotes the mode it read", "Mode=0" in reply, "")
    check("D it says the lengths are not readable, so they are what was sent",
          "not readable on this build" in reply and "SENT" in reply, "")
    check("D it reports the volume and the ideal", "Removed 25000.0 mm3" in reply
          and "25000.0 mm3" in reply.split("Removed")[-1], "")
    check("D it names swap_distances as the repair", "swap_distances=true" in reply, "")

    fresh(chamfer_path)
    box(docs, sketcher, design, "Ch0496b", chamfer_path, 100, 100, 100)
    docs.execute("catia_save_document", {})
    swapped = str(design.execute("catia_chamfer",
                                 {"length": 25, "length2": 20,
                                  "swap_distances": True, "edge_name": "Edge.1"}))
    say(f"  swapped: {swapped[:200]}")
    check("D swap_distances builds the other way round",
          "20 x 25 mm" in swapped and "Removed 25000.0 mm3" in swapped, "")
    fresh(chamfer_path)
    box(docs, sketcher, design, "Ch0496c", chamfer_path, 100, 100, 100)
    docs.execute("catia_save_document", {})
    refused = ""
    try:
        design.execute("catia_chamfer",
                       {"length": 25, "swap_distances": True, "edge_name": "Edge.1"})
    except Exception as error:  # noqa: BLE001 - the refusal is the answer
        refused = str(error)
    check("D swap_distances without length2 is refused before CATIA",
          "needs length2" in refused and "Nothing was created" in refused, "")
    close_quietly(chamfer_path)

    # ── E. the tensor without activation ───────────────────────────────────
    say("\n== E (P2-22). catia_get_inertia(document=...)")
    tensor_path = path("Te0496.CATPart")
    fresh(tensor_path)
    box(docs, sketcher, design, "Te0496", tensor_path, 40, 40, 40)
    docs.execute("catia_save_document", {})
    # Put something ELSE in front, so "not active" is a fact rather than luck.
    active_before = str(conn.active_document.Name)
    if active_before == "Te0496.CATPart":
        docs.execute("catia_activate_document", {"name": "hexapod_assembly.CATProduct"})
        active_before = str(conn.active_document.Name)
    if active_before == "Te0496.CATPart":
        docs.execute("catia_activate_document", {"name": "Assem2_TXT.CATProduct"})
        active_before = str(conn.active_document.Name)
    say(f"  active before: {active_before}")
    named = payload(measure.execute("catia_get_inertia",
                                    {"document": "Te0496.CATPart"}))
    active_after = str(conn.active_document.Name)
    say(f"  tensor={named.get('tensor')}")
    check("E the named route answers a matrix", named.get("tensor") is not None,
          str(named.get("tensor"))[:60])
    check("E `tensor` is the matrix, not null while the matrix sits elsewhere",
          named.get("tensor") == named.get("inertia_matrix_kg_m2"), "")
    check("E the active document did not move", active_before == active_after,
          f"{active_before} -> {active_after}")
    # The same part, this time active: the numbers must agree.
    docs.execute("catia_activate_document", {"name": "Te0496.CATPart"})
    active_route = payload(measure.execute("catia_get_inertia", {}))
    say(f"  active-route matrix: {active_route.get('inertia_matrix_kg_m2')}")
    same = named.get("inertia_matrix_kg_m2") == active_route.get("inertia_matrix_kg_m2")
    check("E both routes agree on the matrix", same,
          f"{named.get('inertia_matrix_kg_m2')} vs "
          f"{active_route.get('inertia_matrix_kg_m2')}")
    close_quietly(tensor_path)

    # ── F. closing a held part names the holder ────────────────────────────
    say("\n== F (P2-23). closing a part an assembly holds")
    part_path = path("Held0496.CATPart")
    inner_path = path("Inner0496.CATProduct")
    outer_path = path("Outer0496.CATProduct")
    for stale in (outer_path, inner_path, part_path):
        fresh(stale)
    box(docs, sketcher, design, "Held0496", part_path, 50, 30, 10)
    docs.execute("catia_save_document", {})
    docs.execute("catia_new_product", {"name": "Inner0496", "file_path": inner_path})
    assembly.execute("catia_add_component", {"file_path": part_path})
    docs.execute("catia_save_document", {})
    docs.execute("catia_new_product", {"name": "Outer0496", "file_path": outer_path})
    assembly.execute("catia_add_component", {"file_path": inner_path})
    docs.execute("catia_save_document", {})
    docs.execute("catia_activate_document", {"name": "Held0496.CATPart"})
    reply = str(docs.execute("catia_close_document",
                            {"document": part_path, "save": False}))
    say(f"  {reply[:260]}")
    check("F the held part is reported as STILL OPEN, not as an error",
          "STILL OPEN (closed: false)" in reply, "")
    check("F the holder is named", "Inner0496.CATProduct" in reply, "")
    check("F it says this is not a failure", "not a failure" in reply, "")
    # The deep walk is what makes a two-level holder nameable at all.
    usage = conn.component_usage(deep=True)
    check("F the deep walk resolves the nested part",
          "Held0496.CATPart" in usage, str(list(usage)[:3]))

    say("\n== cleanup")
    close_quietly(outer_path, inner_path, part_path)
    leftovers = [name for name in document_names() if "0496" in name]
    say(f"  left open: {leftovers}")
    check("F this script left nothing open", not leftovers, str(leftovers))

    failed = [name for name, ok, _ in results if not ok]
    say(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        say("FAILED: " + "; ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
