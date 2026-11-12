"""Do the five knowledge tools actually work on B30? Measure before shipping.

The port is upstream's code. Every attribute it relies on is a guess about a COM
interface -- and this project has already shipped one guess like that for 61
releases (`ThreadingMode = 1 # catThreaded`). So each call is made through the
tool AND read back through the raw object, and the numbers are printed:

  A. parameters, all six types: does `ValuateFromString("20mm")` leave `Value`
     in mm (20) or in metres (0.02)?
  B. the same name twice: does it set the value or raise?
  C. a formula: does `CreateFormula(name, comment, target, expression)` take
     those arguments in that order?
  D. `relations.Item(i).Value` -- is that really the expression body, which is
     what `catia_list_relations` publishes as "body"?
  E. delete, and delete of a name that is not there.
  F. a design table from a tab-separated file: does `AddAssociation` take
     (parameter, column-name) in that order?

Throwaway part, closed without saving.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.knowledge import KnowledgeTools
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.sketcher import SketcherTools

say = lambda text: print(text, flush=True)  # noqa: E731


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    knowledge = KnowledgeTools(conn)
    design = PartDesignTools(conn)
    sketch = SketcherTools(conn)

    docs.execute("catia_new_part", {})
    sketch.execute("catia_create_sketch", {"plane": "xy"})
    sketch.execute("catia_sketch_centered_rectangle", {"width": 40, "height": 40})
    sketch.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"height": 20})
    part = conn.get_active_part()
    say(f"part ready, parameters before: {part.Parameters.Count}")

    say("\nA. six types through the tool")
    cases = [("LEN_A", "length", 20), ("ANG_A", "angle", 45),
             ("REAL_A", "real", 3.5), ("INT_A", "integer", 7),
             ("STR_A", "string", "hello"), ("BOOL_A", "boolean", True)]
    for name, ptype, value in cases:
        try:
            say(f"  {name}: " + knowledge.execute(
                "catia_create_parameter",
                {"name": name, "type": ptype, "value": value}).strip())
        except Exception as error:  # noqa: BLE001
            say(f"  {name}: FAILED - {str(error).splitlines()[0][:150]}")

    say("\n   read back through the raw interface (what a caller would see):")
    for name, ptype, _value in cases:
        try:
            param = part.Parameters.Item(name)
            row = [f"name={param.Name}"]
            for attribute in ("Value", "ValueAsString", "Unit"):
                try:
                    row.append(f"{attribute}={getattr(param, attribute)!r}")
                except Exception as error:  # noqa: BLE001
                    row.append(f"{attribute}=<{str(error).splitlines()[0][:40]}>")
            say(f"  {name}: " + ", ".join(row))
        except Exception as error:  # noqa: BLE001
            say(f"  {name}: NOT FOUND - {str(error).splitlines()[0][:100]}")

    say("\nB. the same name again, with a different value")
    try:
        say("  " + knowledge.execute("catia_create_parameter",
                                     {"name": "LEN_A", "type": "length", "value": 35}).strip())
        say(f"  LEN_A now reads {part.Parameters.Item('LEN_A').Value!r}")
    except Exception as error:  # noqa: BLE001
        say(f"  FAILED - {str(error).splitlines()[0][:150]}")

    say("\nC. a formula driving LEN_A")
    try:
        say("  " + knowledge.execute(
            "catia_set_formula",
            {"name": "F_LEN", "target_parameter": "LEN_A",
             "expression": "LEN_A * 2 - LEN_A", "comment": "probe"}).strip())
    except Exception as error:  # noqa: BLE001
        say(f"  FAILED - {str(error).splitlines()[0][:200]}")

    say("\nD. what relations.Item(i) exposes")
    relations = part.Relations
    say(f"  Relations.Count = {relations.Count}")
    for index in range(1, relations.Count + 1):
        rel = relations.Item(index)
        row = [f"name={rel.Name!r}"]
        for attribute in ("Value", "Comment", "Type", "NLSName"):
            try:
                row.append(f"{attribute}={getattr(rel, attribute)!r}")
            except Exception as error:  # noqa: BLE001
                row.append(f"{attribute}=<{str(error).splitlines()[0][:40]}>")
        say("  " + ", ".join(row))

    say("\n  through catia_list_relations:")
    say("  " + knowledge.execute("catia_list_relations", {}).replace("\n", "\n  ")[:600])

    say("\nE. delete")
    try:
        say("  " + knowledge.execute("catia_delete_relation", {"name": "F_LEN"}).strip())
    except Exception as error:  # noqa: BLE001
        say(f"  FAILED - {str(error).splitlines()[0][:150]}")
    try:
        say("  deleting a name that is not there: "
            + knowledge.execute("catia_delete_relation", {"name": "NOPE"}).strip())
    except Exception as error:  # noqa: BLE001
        say(f"  deleting a name that is not there: refused - "
            f"{str(error).splitlines()[0][:120]}")

    say("\nF. design table from a tab-separated file")
    sheet = os.path.join(tempfile.mkdtemp(prefix="kt_"), "family.txt")
    with open(sheet, "w", encoding="ascii", newline="\n") as handle:
        handle.write("LEN_A\tREAL_A\n")
        handle.write("20\t3.5\n")
        handle.write("30\t4.5\n")
    say(f"  sheet: {sheet}")
    try:
        say("  " + knowledge.execute(
            "catia_create_design_table",
            {"name": "DT1", "file_path": sheet,
             "parameters": ["LEN_A", "REAL_A"]}).strip())
    except Exception as error:  # noqa: BLE001
        say(f"  FAILED - {str(error).splitlines()[0][:250]}")

    say("\nG. state after everything")
    say(f"  parameters: {part.Parameters.Count}, relations: {part.Relations.Count}")
    print(docs.execute("catia_close_document", {"save": False}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
