"""Two more upstream claims, measured instead of adopted.

f9751a1 also says, field-verified on B30:

  1. SPA Measurable scalars read 0 under late-bound dispatch, and re-wrapping
     the object with win32com.client.Dispatch fixes it. This fork reads Area and
     Volume straight from `spa.GetMeasurable(...)` and has been getting real
     numbers (a r=5 fillet band read 0.000314 m2 = 314.159 mm2), so the claim
     may be about a call path this fork does not use. Measured here on a 20 mm
     cube, whose volume and area are exact: 8000 mm3 and 2400 mm2. If the two
     readings agree, the re-dispatch is not needed here - and it is NOT added,
     because wrapping an object that already works can break it.

  2. A dimensional constraint has to be set to driving (Mode = 0) before its
     value is assigned. This fork writes `cst.Dimension.Value = value` and the
     sketches it builds do come out dimensioned, so the question is what Mode
     already is. Printed here, not assumed.

Throwaway part, closed without saving.
"""

from __future__ import annotations

import sys

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

import win32com.client

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.sketcher import SketcherTools

say = lambda text: print(text, flush=True)  # noqa: E731


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    design = PartDesignTools(conn)
    sketch = SketcherTools(conn)

    docs.execute("catia_new_part", {})

    # 1. measurable scalars, exact values: V = 8000 mm3, A = 2400 mm2
    say("A. Measurable on a 20 mm cube (exact V=8000 mm3, A=2400 mm2)")
    sketch.execute("catia_create_sketch", {"plane": "xy"})
    sketch.execute("catia_sketch_centered_rectangle", {"width": 20, "height": 20})
    sketch.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"height": 20})

    part = conn.get_active_part()
    body = conn.get_active_part_body()
    reference = part.CreateReferenceFromObject(body)
    spa = conn.active_document.GetWorkbench("SPAWorkbench")
    measurable = spa.GetMeasurable(reference)

    for label, target in (("raw (what this fork uses)", measurable),
                          ("win32com.client.Dispatch", None)):
        if target is None:
            try:
                target = win32com.client.Dispatch(measurable)
            except Exception as error:  # noqa: BLE001
                say(f"  {label}: dispatch refused - {str(error).splitlines()[0][:90]}")
                continue
        row = []
        for attribute, scale, unit in (("Volume", 1e9, "mm3"), ("Area", 1e6, "mm2")):
            try:
                value = getattr(target, attribute) * scale
                row.append(f"{attribute} {value:.4f} {unit}")
            except Exception as error:  # noqa: BLE001
                row.append(f"{attribute} FAILED {str(error).splitlines()[0][:40]}")
        say(f"  {label}: " + ", ".join(row))

    # 2. what Mode a dimensional constraint already carries
    say("\nB. constraint Mode after setting Dimension.Value")
    sketch.execute("catia_create_sketch", {"plane": "xy"})
    sketch.execute("catia_sketch_line", {"x1": 0, "y1": 0, "x2": 10, "y2": 0})
    sketch.execute("catia_sketch_line", {"x1": 0, "y1": 8, "x2": 10, "y2": 8})
    # Indices start at 2: item 1 is the sketch's absolute axis (0.3.62).
    for label, payload in (
        ("fix on the first line", {"type": "fix", "geometry_index_1": 2}),
        ("distance line1-line2 = 25", {"type": "distance", "geometry_index_1": 2,
                                       "geometry_index_2": 3, "value": 25}),
    ):
        try:
            say(f"  {label}: " + sketch.execute("catia_sketch_constraint", payload).splitlines()[0])
        except Exception as error:  # noqa: BLE001
            say(f"  {label}: failed - {str(error).splitlines()[0][:110]}")

    active = sketch._active_sketch or conn.last_sketch
    constraints = active.Constraints
    say(f"  {constraints.Count} constraint(s) on the sketch")
    for index in range(1, constraints.Count + 1):
        item = constraints.Item(index)
        fields = []
        for attribute in ("Mode", "Type", "ReferenceType"):
            try:
                fields.append(f"{attribute}={getattr(item, attribute)}")
            except Exception as error:  # noqa: BLE001
                fields.append(f"{attribute}=ERR")
        try:
            dimension = item.Dimension
            fields.append(f"Dimension.Value={dimension.Value}")
        except Exception:  # noqa: BLE001
            fields.append("no Dimension")
        say(f"  {index}: {item.Name!r} " + ", ".join(fields))

    sketch.execute("catia_close_sketch", {})
    docs.execute("catia_close_document", {"save": False})
    say("\nclosed without saving")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
