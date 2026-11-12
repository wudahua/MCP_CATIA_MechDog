"""0.3.63 acceptance: the six knowledge tools, after the corrections.

Each correction is checked against the behaviour that motivated it:

  A. boolean "false" stays false (bool("false") is True - that was the bug);
  B. a parameter associated with a design table refuses a direct write WITH AN
     EXPLANATION naming catia_apply_design_table;
  C. catia_set_formula drives a pad's own height, using the name this build
     reports, and the solid follows;
  D. the English path form fails with near matches in the message;
  E. a design table applies a row, and the reply carries the metres warning;
  F. delete still works.

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
PASS, FAIL = "PASS", "FAIL"


def check(label: str, ok: bool, detail: str = "") -> None:
    say(f"  [{PASS if ok else FAIL}] {label}" + (f" - {detail}" if detail else ""))


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
    params = part.Parameters

    say("\nA. boolean from the string 'false'")
    knowledge.execute("catia_create_parameter",
                      {"name": "B1", "type": "boolean", "value": "false"})
    check("'false' stored as False", params.Item("B1").Value is False,
          f"reads {params.Item('B1').Value!r}")
    knowledge.execute("catia_create_parameter",
                      {"name": "B1", "type": "boolean", "value": "true"})
    check("'true' stored as True", params.Item("B1").Value is True)
    try:
        knowledge.execute("catia_create_parameter",
                          {"name": "B2", "type": "boolean", "value": "nope"})
        check("unknown text refused", False, "it was accepted")
    except Exception as error:  # noqa: BLE001
        check("unknown text refused", "not a boolean" in str(error))

    say("\nC. a formula driving the pad's own height")
    pad = part.MainBody.Shapes.Item(1)
    target = str(pad.FirstLimit.Dimension.Name)
    say(f"  target name reported by CATIA: {target!r}")
    knowledge.execute("catia_create_parameter", {"name": "H", "type": "length", "value": 12})
    try:
        say("  " + knowledge.execute(
            "catia_set_formula",
            {"name": "F_PAD", "target_parameter": target, "expression": "H * 2"}).strip())
        part.Update()
        got = pad.FirstLimit.Dimension.Value
        check("pad height followed the formula (12 x 2)", abs(float(got) - 24.0) < 1e-6,
              f"FirstLimit.Length = {got!r}")
    except Exception as error:  # noqa: BLE001
        check("pad height followed the formula", False, str(error).splitlines()[0][:140])

    say("\nD. the English path form, which must fail usefully")
    try:
        knowledge.execute("catia_set_formula",
                          {"name": "F_BAD", "target_parameter":
                           "PartBody\\Pad.1\\FirstLimit\\Length", "expression": "20mm"})
        check("English path refused", False, "it was accepted")
    except Exception as error:  # noqa: BLE001
        message = str(error)
        check("English path refused", "No parameter" in message)
        check("message names the localized form", "凸台" in message or "catia_get_parameters" in message)
        check("message points at the listing tool", "catia_get_parameters" in message)

    say("\nD2. the same target written from the feature name, without the prefix")
    short = "凸台.1\\第一限制\\长度"
    try:
        knowledge.execute("catia_set_formula",
                          {"name": "F_SHORT", "target_parameter": short,
                           "expression": "20mm"})
        check("prefix-less path refused", False, "it was accepted")
    except Exception as error:  # noqa: BLE001
        message = str(error)
        check("prefix-less path refused", "No parameter" in message)
        check("near matches offered", "Names that end the same way" in message,
              message.split("way:")[-1][:120])

    say("\nB and E. design table: association blocks a direct write, apply works")
    knowledge.execute("catia_create_parameter", {"name": "LEN", "type": "length", "value": 20})
    knowledge.execute("catia_create_parameter", {"name": "REAL", "type": "real", "value": 3.5})
    sheet = os.path.join(tempfile.mkdtemp(prefix="kt8_"), "family.txt")
    with open(sheet, "w", encoding="ascii", newline="\n") as handle:
        handle.write("LEN\tREAL\n20mm\t3.5\n30mm\t4.5\n")
    created = knowledge.execute("catia_create_design_table",
                                {"name": "DT1", "file_path": sheet,
                                 "parameters": ["LEN", "REAL"]})
    check("create says the unit rule", "METRES" in created, created[:110])
    check("create says how to apply", "catia_apply_design_table" in created)

    try:
        knowledge.execute("catia_create_parameter", {"name": "LEN", "type": "length", "value": 25})
        check("direct write to an associated parameter refused", False, "it succeeded")
    except Exception as error:  # noqa: BLE001
        message = str(error)
        check("direct write refused", True)
        check("refusal names the design table path",
              "catia_apply_design_table" in message, message.splitlines()[0][:150])

    applied = knowledge.execute("catia_apply_design_table",
                                {"name": "DT1", "configuration": 2})
    check("apply row 2", params.Item("LEN").Value == 30.0,
          f"LEN = {params.Item('LEN').Value!r}")
    check("the other column followed too", params.Item("REAL").Value == 4.5,
          f"REAL = {params.Item('REAL').Value!r}")
    check("apply reply repeats the unit rule", "1000x" in applied or "metres" in applied)

    try:
        knowledge.execute("catia_apply_design_table", {"name": "DT1", "configuration": 9})
        check("out-of-range row refused", False, "it was accepted")
    except Exception as error:  # noqa: BLE001
        check("out-of-range row refused", "out of range" in str(error))

    say("\nF. relations listing and delete")
    listing = knowledge.execute("catia_list_relations", {"filter": "DT1"})
    check("listing finds the table", "DT1" in listing, listing.replace("\n", " ")[:90])
    knowledge.execute("catia_delete_relation", {"name": "F_PAD"})
    check("delete removed the formula", "F_PAD" not in knowledge.execute("catia_list_relations", {}))
    try:
        knowledge.execute("catia_delete_relation", {"name": "NOPE"})
        check("deleting an unknown name refused", False)
    except Exception as error:  # noqa: BLE001
        check("deleting an unknown name refused", "No relation" in str(error))

    say(f"\nparameters now: {params.Count}, relations: {part.Relations.Count}")
    print(docs.execute("catia_close_document", {"save": False}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
