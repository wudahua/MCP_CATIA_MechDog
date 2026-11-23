"""B30 acceptance for 0.3.162 - catia_move_body, through the real tool call.

  A  a 25 x 45 x 80 block moved +5 in x: volume and area unchanged, centre of
     gravity moved by exactly (5, 0, 0), the source hidden, one solid in the view
  B  a body with a THROUGH HOLE moved +10 in z: the tunnel must survive
  C  two disjoint pads in one body moved +10 in x: no lump may be left behind
  D  the refusals: zero vector, unknown body, and a Product document
  E  hide_source=false keeps both bodies visible
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
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

results: list[tuple[str, bool, str]] = []


def say(text: str = "") -> None:
    print(text, flush=True)


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    say(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  [{detail}]" if detail else ""))


def payload(reply):
    return reply if isinstance(reply, (dict, list)) else json.loads(str(reply))


def box_of(measure, body_name: str | None = None) -> dict:
    args = {"body_name": body_name} if body_name else {}
    data = payload(measure.execute("catia_get_bounding_box", args))
    core = data.get("data") if isinstance(data.get("data"), dict) else data
    return {"min": core.get("min"), "max": core.get("max")}


def body_row(design, name: str) -> dict:
    rows = payload(design.execute("catia_list_bodies", {}))
    rows = rows.get("bodies") if isinstance(rows, dict) else rows
    for row in rows:
        if str(row.get("name")) == name:
            return row
    return {}


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sk = SketcherTools(conn)
    design = PartDesignTools(conn)
    measure = MeasurementTools(conn)

    root = os.path.join(tempfile.gettempdir(), "p0499acc")
    os.makedirs(root, exist_ok=True)

    def fresh(tag: str) -> None:
        path = os.path.join(root, f"MOVE_{tag}.CATPart")
        try:
            docs.execute("catia_close_document", {"document": path, "save": False})
        except Exception:  # noqa: BLE001
            pass
        if os.path.isfile(path):
            os.remove(path)
        docs.execute("catia_new_part", {"name": f"MOVE_{tag}", "file_path": path})

    def pad(x1, y1, x2, y2, height, sketch="S"):
        sk.execute("catia_create_sketch", {"plane": "xy", "name": sketch})
        sk.execute("catia_sketch_rectangle",
                   {"x1": x1, "y1": y1, "x2": x2, "y2": y2})
        sk.execute("catia_close_sketch", {})
        design.execute("catia_pad", {"sketch_name": sketch, "height": height})

    say("A. block moved +5 in x")
    fresh("A")
    pad(0, 0, 25, 45, 80)
    reply = design.execute("catia_move_body", {"dx": 5, "dy": 0, "dz": 0})
    say("  " + str(reply).replace("\n", "\n  ")[:900])
    part = conn.get_active_part()
    moved = "零件几何体_MOVED"
    row = body_row(design, moved)
    check("A the moved body exists with volume 90000",
          abs(float(row.get("volume_mm3") or 0) - 90000.0) < 1e-3,
          str(row.get("volume_mm3")))
    check("A the reply says the volume did not change",
          "90000.0000 -> 90000.0000" in str(reply), "")
    check("A the reply says the centre of gravity moved (5, 0, 0)",
          "centre of gravity moved (5.0000, 0.0000, 0.0000)" in str(reply), "")
    envelope = box_of(measure)
    check("A the mesh envelope is the moved block alone (x 5..30)",
          envelope.get("min", {}).get("x") == 5.0
          and envelope.get("max", {}).get("x") == 30.0,
          json.dumps(envelope))
    check("A the source body is still in the tree",
          bool(body_row(design, "零件几何体")), "")
    part.Update()
    check("A a full part.Update() leaves the moved solid measurable",
          abs(float(body_row(design, moved).get("volume_mm3") or 0) - 90000.0) < 1e-3,
          str(body_row(design, moved).get("volume_mm3")))

    say("\nB. through hole, moved +10 in z")
    fresh("B")
    pad(0, 0, 25, 45, 80)
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "H"})
    sk.execute("catia_sketch_circle", {"cx": 12.5, "cy": 22.5, "radius": 5})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pocket", {"sketch_name": "H", "depth": 80})
    before = float(body_row(design, "零件几何体").get("volume_mm3") or 0)
    reply = design.execute("catia_move_body", {"dz": 10})
    after = float(body_row(design, "零件几何体_MOVED").get("volume_mm3") or 0)
    check("B the hole survives the move (volume identical)",
          abs(before - after) < 1e-3, f"{before:.4f} -> {after:.4f}")
    check("B the reply prints it moved (0, 0, 10)",
          "(0.0000, 0.0000, 10.0000)" in str(reply), "")

    say("\nC. two disjoint pads, moved +10 in x")
    fresh("C")
    pad(0, 0, 25, 45, 80, sketch="S1")
    pad(100, 0, 110, 30, 40, sketch="S2")
    before = float(body_row(design, "零件几何体").get("volume_mm3") or 0)
    design.execute("catia_move_body", {"dx": 10})
    after = float(body_row(design, "零件几何体_MOVED").get("volume_mm3") or 0)
    envelope = box_of(measure)
    check("C both lumps came (volume identical)",
          abs(before - after) < 1e-3, f"{before:.4f} -> {after:.4f}")
    check("C the envelope starts at x=10, so nothing was left behind",
          envelope.get("min", {}).get("x") == 10.0
          and envelope.get("max", {}).get("x") == 120.0,
          json.dumps(envelope))

    say("\nD. refusals")
    fresh("D")
    pad(0, 0, 25, 45, 80)
    try:
        design.execute("catia_move_body", {"dx": 0, "dy": 0, "dz": 0})
        check("D a zero vector is refused", False, "it went through")
    except CodedError as error:
        check("D a zero vector is refused", error.code == "BAD_ARGUMENT", error.code)
    try:
        design.execute("catia_move_body", {"dx": 5, "name": "NoSuchBody"})
        check("D an unknown body is refused", False, "it went through")
    except CodedError as error:
        check("D an unknown body is refused", error.code == "BODY_NOT_FOUND",
              error.code)
        check("D the refusal lists the bodies that do exist",
              "零件几何体" in str(error), str(error)[:120])
    asm = os.path.join(root, "MOVE_D.CATProduct")
    try:
        docs.execute("catia_close_document", {"document": asm, "save": False})
    except Exception:  # noqa: BLE001
        pass
    if os.path.isfile(asm):
        os.remove(asm)
    docs.execute("catia_new_product", {"name": "MOVE_D_ASM", "file_path": asm})
    try:
        design.execute("catia_move_body", {"dx": 5})
        check("D an assembly as the active document is refused", False,
              "it went through")
    except CodedError as error:
        # Same refusal the other feature tools give, and it names the remedy.
        check("D an assembly as the active document is refused",
              error.code in ("NOT_A_PART_DOCUMENT", "PRECONDITION_FAILED")
              and "CATProduct" in str(error), f"{error.code}: {str(error)[:90]}")
    try:
        design.execute("catia_move_body", {"dx": 5, "document": asm})
        check("D document=<assembly> is refused", False, "it went through")
    except CodedError as error:
        check("D document=<assembly> is refused",
              error.code == "NOT_A_PART_DOCUMENT", error.code)
    docs.execute("catia_activate_document", {"name": "MOVE_D.CATPart"})

    say("\nE. hide_source=false keeps both")
    fresh("E")
    pad(0, 0, 25, 45, 80)
    reply = design.execute("catia_move_body", {"dx": 5, "hide_source": False})
    check("E the reply says both solids are visible",
          "currently holds both solids" in str(reply), "")
    envelope = box_of(measure)
    check("E the envelope spans both (0..30)",
          envelope.get("min", {}).get("x") == 0.0
          and envelope.get("max", {}).get("x") == 30.0, json.dumps(envelope))

    for name in os.listdir(root):
        if name.startswith("MOVE_"):
            try:
                docs.execute("catia_close_document",
                             {"document": os.path.join(root, name), "save": False})
            except Exception:  # noqa: BLE001
                pass

    say("")
    failed = [row for row in results if not row[1]]
    say(f"{len(results) - len(failed)}/{len(results)} passed")
    for name, _ok, detail in failed:
        say(f"  FAILED: {name}  [{detail}]")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
