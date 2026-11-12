"""What do Mode and ReferenceType mean on a CATIA constraint, per CATIA?

Upstream f9751a1 says a dimensional constraint must be forced to driving
(Mode = 0) and a `fix` must get ReferenceType = 1 (fix in space). Measured on
B30 2026-11-05, a freshly added fix already reads Mode = 0 and
ReferenceType = 0 - so the first is the default and the second is not.

Whether ReferenceType = 0 is "fix in space" or "not yet stated" cannot be
settled from the value alone. The enum in MecModInterfaces can: print its
constants. Same for CatConstraintMode and CatConstraintType, so the three
numbers in this file's own constants block stop being folklore.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pythoncom

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.sketcher import SketcherTools

say = lambda text: print(text, flush=True)  # noqa: E731

WANTED = ("constraintmode", "constraintreferencetype", "constrainttype",
          "cstmode", "referencetype")


def dump_enums(obj, label: str) -> None:
    try:
        type_lib, _index = obj._oleobj_.GetTypeInfo().GetContainingTypeLib()
        count = type_lib.GetTypeInfoCount()
    except Exception as error:  # noqa: BLE001
        say(f"  {label}: no type library ({str(error).splitlines()[0][:80]})")
        return
    say(f"  {label}: {count} type infos")
    for position in range(count):
        try:
            info = type_lib.GetTypeInfo(position)
            attributes = info.GetTypeAttr()
            name = info.GetDocumentation(-1)[0]
        except Exception:  # noqa: BLE001
            continue
        if attributes.typekind != pythoncom.TKIND_ENUM or not name:
            continue
        if not any(token in name.lower() for token in WANTED):
            continue
        say(f"    ENUM {name}")
        for slot in range(attributes.cVars):
            try:
                variable = info.GetVarDesc(slot)
                member = info.GetNames(variable.memid)[0]
                say(f"      {member} = {variable.value}")
            except Exception as error:  # noqa: BLE001
                say(f"      <slot {slot}: {str(error).splitlines()[0][:50]}>")


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketch = SketcherTools(conn)

    docs.execute("catia_new_part", {})
    sketch.execute("catia_create_sketch", {"plane": "xy"})
    sketch.execute("catia_sketch_line", {"x1": 0, "y1": 0, "x2": 10, "y2": 0})
    sketch.execute("catia_sketch_constraint", {"type": "fix", "geometry_index_1": 2})

    active = sketch._active_sketch
    constraint = active.Constraints.Item(1)
    say(f"constraint {constraint.Name!r}: Mode={constraint.Mode} "
        f"Type={constraint.Type} ReferenceType={constraint.ReferenceType}")

    say("\nA. enums on the constraint interface")
    dump_enums(constraint, "constraint")

    say("\nB. can ReferenceType be set to 1, and does it read back?")
    for value in (1, 0):
        try:
            constraint.ReferenceType = value
            say(f"  set {value}: read back {constraint.ReferenceType}")
        except Exception as error:  # noqa: BLE001
            say(f"  set {value}: refused - {str(error).splitlines()[0][:90]}")

    docs.execute("catia_close_document", {"save": False})
    say("\nclosed without saving")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
