"""Three questions the 2026-11-19 report raises that cannot be answered by reading
code, because each one is a claim about what this CATIA will accept.

  A  `Pad.SecondLimit` - it EXISTS and its dimension is writable, but does the
     solid change? Set it, UPDATE the pad, then measure (the first version of
     this probe measured without updating and "proved" nothing).
  B  `Shaft.SecondAngle` - volume alone cannot tell "swept the other way" from
     "swept the same way, twice", so the axial EXTENT is measured as well.
  C  assembly constraints - the report asks for a route that works instead of
     "cannot be done" (P0-1). Work mode, ReferenceProduct, CreateReferenceFromName,
     GenerateConstraints, and a Fix on the root product all get a cell.

.venv/Scripts/python.exe -u scripts/b30_v0465_probe_pad_shaft_asm.py
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
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0465")


def say(line: str = "") -> None:
    print(line, flush=True)


def member(obj, name: str) -> str:
    try:
        return repr(getattr(obj, name))
    except Exception as error:  # noqa: BLE001
        return f"<{str(error).splitlines()[0][:70]}>"


def usable(obj, name: str) -> str:
    try:
        value = getattr(obj, name)
    except Exception as error:  # noqa: BLE001
        return f"absent ({str(error).splitlines()[0][:50]})"
    return f"present: {value!r}"[:110]


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    measurement = MeasurementTools(conn)
    assembly = AssemblyTools(conn)
    say(f"probe v0465 on {conn.app.Name}")

    # Leftovers from a crashed run would otherwise be "already open", and the
    # tool refuses to make a second document with the same name.
    for index in range(int(conn.documents.Count), 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = str(doc.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).lower().startswith(
                os.path.abspath(ROOT).lower()):
            try:
                doc.Close()
            except Exception:  # noqa: BLE001
                pass

    def fresh(name: str):
        path = os.path.join(ROOT, name + ".CATPart")
        if os.path.exists(path):
            os.remove(path)
        docs.execute("catia_new_part", {"name": name, "file_path": path})
        return conn.get_active_part(), conn.get_active_part_body()

    def volume_of(part, body) -> float:
        return parts._body_volume_mm3(part, body)

    def bbox_along(axis: int) -> str:
        reply = measurement.execute("catia_get_bounding_box",
                                    {"method": "vertices"})
        data = reply if isinstance(reply, dict) else json.loads(reply)
        low, high = data.get("min") or {}, data.get("max") or {}
        if not low or not high:
            return f"<bbox keys: {sorted(data)}>"
        key = "xyz"[axis]
        return (f"{key} {float(low[key]):.3f} .. {float(high[key]):.3f} "
                f"(span {float(high[key]) - float(low[key]):.3f})")

    # ══ A: does a Pad have a usable SECOND limit? ══════════════════════════
    say("A: pad limits")
    part, body = fresh("ProbeV0465a")
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "P"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 20.0, "y2": 10.0})
    sketcher.execute("catia_close_sketch", {})
    sketch = body.Sketches.Item("P")
    pad = part.ShapeFactory.AddNewPad(sketch, 5.0)
    part.UpdateObject(pad)
    say(f"A base pad (first only): volume {volume_of(part, body):.4f} "
        f"(expect 1000); {bbox_along(2)}")
    second = getattr(pad, "SecondLimit", None)
    say(f"A SecondLimit present: {second is not None}, "
        f"dimension {member(second, 'Dimension')}")
    for value in (3.0, 4.0):
        try:
            second.Dimension.Value = value
            part.UpdateObject(pad)
            say(f"A SecondLimit={value:g}: volume {volume_of(part, body):.4f}"
                f" (expect 100*(5+{value:g}) = {100 * (5 + value):.0f}); "
                f"{bbox_along(2)}")
        except Exception as error:  # noqa: BLE001
            say(f"A SecondLimit={value:g} FAILED: "
                f"{str(error).splitlines()[0][:100]}")
            break
    try:
        say(f"A after: FirstLimit={pad.FirstLimit.Dimension.Value}, "
            f"SecondLimit={pad.SecondLimit.Dimension.Value}, "
            f"IsSymmetric={pad.IsSymmetric}")
    except Exception as error:  # noqa: BLE001
        say(f"A readback failed: {str(error).splitlines()[0][:80]}")

    # ══ B: does a Shaft have a usable SECOND angle? ════════════════════════
    say("B: shaft angles")
    for first, second in ((60.0, 0.0), (30.0, 30.0)):
        part, body = fresh(f"ProbeV0465b{int(first)}_{int(second)}")
        sketcher.execute("catia_create_sketch", {"plane": "zx", "name": "S"})
        # 5 mm wide in the axial direction, 10..20 radially: revolving this about
        # the sketch's horizontal axis gives a ring 10 mm long and 10..20 across.
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0.0, "y1": 10.0, "x2": 10.0, "y2": 20.0})
        sketcher.execute("catia_close_sketch", {})
        sketch = body.Sketches.Item("S")
        shaft = parts._build_revolution(part, sketch, first, "horizontal",
                                        "AddNewShaftFromRef", "Shaft")
        note = ""
        if second:
            try:
                shaft.SecondAngle.Value = second
                part.UpdateObject(shaft)
            except Exception as error:  # noqa: BLE001
                note = f"  SecondAngle FAILED: {str(error).splitlines()[0][:90]}"
        say(f"B first={first:g} second={second:g}: volume "
            f"{volume_of(part, body):.4f}; {bbox_along(0)}{note}")
        say(f"B   readback FirstAngle={shaft.FirstAngle.Value:g} "
            f"SecondAngle={getattr(shaft, 'SecondAngle', None) and shaft.SecondAngle.Value}")

    # ══ C: assembly constraints ════════════════════════════════════════════
    say("C: assembly constraints")
    for name in ("A1", "A2"):
        path = os.path.join(ROOT, f"ProbeV0465_{name}.CATPart")
        if os.path.exists(path):
            os.remove(path)
        docs.execute("catia_new_part", {"name": name, "file_path": path})
        sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "R"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0.0, "y1": 0.0, "x2": 10.0, "y2": 10.0})
        sketcher.execute("catia_close_sketch", {})
        parts.execute("catia_pad", {"sketch_name": "R", "height": 5.0})
        docs.execute("catia_save_document", {})
    asm_path = os.path.join(ROOT, "ProbeV0465_ASM.CATProduct")
    if os.path.exists(asm_path):
        os.remove(asm_path)
    docs.execute("catia_new_product", {"name": "ProbeV0465_ASM",
                                       "file_path": asm_path})
    for name in ("A1", "A2"):
        assembly.execute("catia_add_component",
                         {"file_path": os.path.join(
                             ROOT, f"ProbeV0465_{name}.CATPart")})
    product = conn.get_active_product()
    say(f"C product {product.Name!r} children "
        f"{[str(product.Products.Item(i).Name) for i in range(1, int(product.Products.Count) + 1)]}")
    for attribute in ("ApplyWorkMode", "GenerateConstraints",
                      "CreateReferenceFromName", "CreateReferenceFromObject",
                      "HasWorkMode", "GetMasterShapeRepresentation",
                      "ReferenceProduct", "Parameters"):
        say(f"C product.{attribute}: {usable(product, attribute)}")
    component = product.Products.Item("A1.1")
    say(f"C component.ReferenceProduct: <{member(component, 'ReferenceProduct')}>")
    say(f"C component.ApplyWorkMode: {usable(component, 'ApplyWorkMode')}")
    constraints = product.Connections("CATIAConstraints")
    say(f"C constraints count before: {constraints.Count}")

    def attempt(label: str, call) -> None:
        try:
            result = call()
            say(f"C  [{label}] OK -> {getattr(result, 'Name', result)!r}"
                f"   (count now {constraints.Count})")
        except Exception as error:  # noqa: BLE001
            say(f"C  [{label}] FAILED: {str(error).splitlines()[0][:110]}"
                f"   (count now {constraints.Count})")

    attempt("fix(raw product)", lambda: constraints.AddMonoEltCst(0, component))
    attempt("fix(ReferenceProduct)",
            lambda: constraints.AddMonoEltCst(0, component.ReferenceProduct))
    attempt("fix(root product)", lambda: constraints.AddMonoEltCst(0, product))
    if hasattr(product, "CreateReferenceFromName"):
        for name in ("A1.1", "ProbeV0465_A1.1",
                     "ProbeV0465_ASM/ProbeV0465_A1.1"):
            attempt(f"fix(ref '{name}')",
                    lambda name=name: constraints.AddMonoEltCst(
                        0, product.CreateReferenceFromName(name)))
    if hasattr(product, "GenerateConstraints"):
        attempt("GenerateConstraints", lambda: product.GenerateConstraints())
    for mode in (0, 1, 2):
        def with_mode(mode=mode):
            component.ApplyWorkMode(mode)
            return constraints.AddMonoEltCst(0, component)
        attempt(f"ApplyWorkMode({mode}) + fix", with_mode)
    say(f"C constraints count after: {constraints.Count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
