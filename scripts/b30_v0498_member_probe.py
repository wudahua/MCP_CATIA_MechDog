"""B30 probe for 0.3.161 - three remaining questions, answered by calling them.

  A  can an instance's visibility be READ BACK after SetShow? (P2-1 needs it:
     a write with no read-back is a claim, not a measurement)
  B  what does a Measurable answer about a box? (P2-2 asks for an exact bounding
     box; GetBox / GetBoundingBox are the candidates)
  C  can a PAD's height be changed after it was built? (P1-4: the report rebuilt
     a 15-feature part because a 5 mm pad direction was wrong)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402


def say(text: str = "") -> None:
    print(text, flush=True)


def payload(reply):
    return reply if isinstance(reply, (dict, list)) else json.loads(str(reply))


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sk = SketcherTools(conn)
    design = PartDesignTools(conn)
    assembly = AssemblyTools(conn)

    root = os.path.join(tempfile.gettempdir(), "p0498q")
    os.makedirs(root, exist_ok=True)

    def fresh(name):
        path = os.path.join(root, name)
        try:
            docs.execute("catia_close_document", {"document": path, "save": False})
        except Exception:  # noqa: BLE001
            pass
        if os.path.isfile(path):
            os.remove(path)
        return path

    # ── A. instance visibility, written and read back ────────────────────
    part_path = fresh("Q0498.CATPart")
    docs.execute("catia_new_part", {"name": "Q0498", "file_path": part_path})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "S"})
    sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 40, "y2": 20})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"sketch_name": "S", "height": 10})
    docs.execute("catia_save_document", {})

    asm_path = fresh("Q0498.CATProduct")
    docs.execute("catia_new_product", {"name": "Q0498A", "file_path": asm_path})
    assembly.execute("catia_add_component", {"file_path": part_path})
    product = conn.app.ActiveDocument.Product
    instance = product.Products.Item(1)
    selection = conn.hso

    say("== A instance visibility: write, then read back")
    for label, value in (("baseline GetShow", None), ("after SetShow(1)", 1),
                         ("after SetShow(0)", 0)):
        if value is not None:
            try:
                selection.Clear()
                selection.Add(instance)
                selection.VisProperties.SetShow(value)
            except Exception as error:  # noqa: BLE001
                say(f"  {label}: SetShow RAISES {str(error).splitlines()[0][:80]}")
                continue
        try:
            selection.Clear()
            selection.Add(instance)
            shown = selection.VisProperties.GetShow()
            say(f"  {label}: GetShow -> {shown}")
        except Exception as error:  # noqa: BLE001
            say(f"  {label}: GetShow RAISES {str(error).splitlines()[0][:80]}")
        finally:
            try:
                selection.Clear()
            except Exception:  # noqa: BLE001
                pass
    docs.execute("catia_close_document", {"document": asm_path, "save": False})
    docs.execute("catia_close_document", {"document": part_path, "save": False})

    # ── B. the Measurable, on a body and on a face ───────────────────────
    say("\n== B what a Measurable answers about a 25x45x80 block")
    box_path = fresh("B0498.CATPart")
    docs.execute("catia_new_part", {"name": "B0498", "file_path": box_path})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "B"})
    sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 25, "y2": 45})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"sketch_name": "B", "height": 80})
    part = conn.get_active_part()
    spa = conn.active_document.GetWorkbench("SPAWorkbench")
    body = part.MainBody
    for label, target in (("body", body), ("Face.1", body.Shapes.Item(1))):
        try:
            reference = part.CreateReferenceFromObject(target)
        except Exception:  # noqa: BLE001
            reference = target
        try:
            measurable = spa.GetMeasurable(reference)
        except Exception as error:  # noqa: BLE001
            say(f"  {label}: GetMeasurable RAISES {str(error).splitlines()[0][:80]}")
            continue
        for member in ("Volume", "Area", "Length", "Perimeter", "CenterOfGravity",
                       "GetBox", "GetBoundingBox", "GetMinimumDistance",
                       "GetPlane", "GetAxisSystem", "MomentsOfInertia"):
            try:
                value = getattr(measurable, member)
                if callable(value):
                    try:
                        say(f"  {label}.{member}() -> {value()}")
                    except Exception as error:  # noqa: BLE001
                        say(f"  {label}.{member}() RAISES "
                            f"{str(error).splitlines()[0][:70]}")
                    continue
                say(f"  {label}.{member} -> {type(value).__name__} {str(value)[:60]}")
            except Exception as error:  # noqa: BLE001
                say(f"  {label}.{member:18s} -> RAISES "
                    f"{str(error).splitlines()[0][:70]}")

    # ── C. can a pad's height be changed afterwards? ─────────────────────
    say("\n== C editing a built pad")
    pad = None
    for index in range(1, int(body.Shapes.Count) + 1):
        shape = body.Shapes.Item(index)
        if str(shape.Name).startswith("凸台"):
            pad = shape
            break
    if pad is None:
        say("  no pad found")
    else:
        say(f"  pad '{pad.Name}'")
        for member in ("FirstLimit", "SecondLimit", "Dimension", "Height"):
            try:
                value = getattr(pad, member)
                say(f"  pad.{member} -> {type(value).__name__} {str(value)[:50]}")
            except Exception as error:  # noqa: BLE001
                say(f"  pad.{member} -> RAISES {str(error).splitlines()[0][:70]}")
        try:
            limit = pad.FirstLimit
            say(f"  pad.FirstLimit.Dimension -> {limit.Dimension}")
            say(f"  value now: {limit.Dimension.Value}")
            limit.Dimension.Value = 20.0
            part.UpdateObject(pad)
            inertia = payload(design.conn and
                              __import__("catia_mcp.tools.measurement",
                                         fromlist=["MeasurementTools"])
                              .MeasurementTools(conn)
                              .execute("catia_get_inertia", {}))
            volume = (inertia.get("data", inertia) or {}).get("volume_mm3")
            say(f"  after Value=20 and UpdateObject: volume {volume} "
                f"(25x45x20 = {25 * 45 * 20})")
        except Exception as error:  # noqa: BLE001
            say(f"  editing RAISES {str(error).splitlines()[0][:120]}")

    docs.execute("catia_close_document", {"document": box_path, "save": False})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
