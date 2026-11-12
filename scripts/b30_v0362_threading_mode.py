"""Is ThreadingMode 0 threaded or is 1? Asked of CATIA, not of a comment.

The fork has written `hole.ThreadingMode = 1  # catThreaded` since 0.3.0 and
never built a threaded hole to check it. Upstream f9751a1 (field-verified on
B30) claims the type libraries say 0 = threaded, 1 = smooth. Both claims are
unverified in this project, so the comment is not evidence for either.

Three readings, in order of hardness:

  A. the CATIA TYPE LIBRARY the Hole interface actually lives in - enumerate
     its TKIND_ENUM entries and print the constants of any enum whose name
     mentions threading. This is CATIA's own declaration.
  B. the FEATURE NAME after each update: a Chinese V5 build names a tapped hole
     differently from a plain one, so `hole.Name` is evidence that survives a
     value CATIA merely accepts.
  C. whether the UPDATE FAILS: threading before the first update is supposed to
     break the rebuild, which is a real behavioural difference.

Throwaway part, closed without saving.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pythoncom

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.sketcher import SketcherTools

say = lambda text: print(text, flush=True)  # noqa: E731


def thread_enums_from(obj, label: str) -> None:
    """Print every threading enum in the type library `obj` belongs to."""
    try:
        type_info = obj._oleobj_.GetTypeInfo()
        type_lib, _index = type_info.GetContainingTypeLib()
    except Exception as error:  # noqa: BLE001
        say(f"  {label}: no type info ({str(error).splitlines()[0][:80]})")
        return
    try:
        count = type_lib.GetTypeInfoCount()
    except Exception as error:  # noqa: BLE001
        say(f"  {label}: GetTypeInfoCount failed ({error})")
        return
    say(f"  {label}: library holds {count} type infos")
    found = 0
    for position in range(count):
        try:
            info = type_lib.GetTypeInfo(position)
            attributes = info.GetTypeAttr()
            name = info.GetDocumentation(-1)[0]
        except Exception:  # noqa: BLE001
            continue
        if attributes.typekind != pythoncom.TKIND_ENUM or not name:
            continue
        if "thread" not in name.lower():
            continue
        found += 1
        say(f"    ENUM {name}")
        for slot in range(attributes.cVars):
            try:
                variable = info.GetVarDesc(slot)
                member = info.GetNames(variable.memid)[0]
                say(f"      {member} = {variable.value}")
            except Exception as error:  # noqa: BLE001
                say(f"      <slot {slot}: {str(error).splitlines()[0][:50]}>")
    if not found:
        say("    no enum with 'thread' in its name in this library")


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    design = PartDesignTools(conn)
    sketch = SketcherTools(conn)

    say(f"documents open: {conn.documents.Count}")
    docs.execute("catia_new_part", {})
    say(f"new part: {conn.active_document.Name}")

    sketch.execute("catia_create_sketch", {"plane": "xy"})
    sketch.execute("catia_sketch_centered_rectangle", {"width": 40, "height": 40})
    sketch.execute("catia_close_sketch", {})
    say(design.execute("catia_pad", {"height": 20}).splitlines()[0])

    # A second sketch on the top face, holding a single point: the Hole takes
    # its axis from a point, and the block is 20 mm thick so depth 12 is blind.
    sketch.execute("catia_create_sketch", {"plane": "xy", "offset": 20})
    sketch.execute("catia_sketch_point", {"x": 5, "y": 5})
    sketch.execute("catia_close_sketch", {})

    part = conn.get_active_part()
    body = conn.get_active_part_body()
    factory = part.ShapeFactory

    active = conn.last_sketch
    hole = factory.AddNewHoleFromSketch(active, 12)
    hole.Diameter.Value = 8.0
    hole.BottomType = 0
    hole.BottomLimit.LimitMode = 0
    hole.BottomLimit.Dimension.Value = 12.0
    say(f"\nhole created: name={hole.Name!r} ThreadingMode={getattr(hole, 'ThreadingMode', '?')}")

    say("\nA. threading enums in the Hole interface's own library")
    thread_enums_from(hole, "hole")

    def update(label: str) -> None:
        try:
            part.UpdateObject(hole)
            say(f"  {label}: update OK")
        except Exception as error:  # noqa: BLE001
            say(f"  {label}: update FAILED - {str(error).splitlines()[0][:110]}")

    for value in (1, 0):
        say(f"\nB. ThreadingMode = {value}")
        try:
            hole.ThreadingMode = value
        except Exception as error:  # noqa: BLE001
            say(f"  set refused: {str(error).splitlines()[0][:110]}")
            continue
        say(f"  read back: {getattr(hole, 'ThreadingMode', '?')}")
        update(f"after ={value}")
        say(f"  feature name now: {hole.Name!r}")

    say("\nC. final tree")
    for index in range(1, body.Shapes.Count + 1):
        shape = body.Shapes.Item(index)
        say(f"  {index}: {shape.Name!r}")

    docs.execute("catia_close_document", {"save": False})
    say("\nclosed without saving")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
