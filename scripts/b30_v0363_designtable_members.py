"""Round five: what IS a DesignTable, per its own type library?

Round four could not read NumberOfConfigurations, Configurations or
ConfigurationName, and could not write ConfigurationName either. Those errors
(`Item.NumberOfConfigurations`) look like a failed binding rather than a missing
member, and the difference decides how much this tool is worth:

  * a design table with a switchable configuration is a product-family machine;
  * a design table you can only create is a file association a human has to
    activate by hand in CATIA.

So the type library answers it: list every member whose name mentions config,
activity, or apply, plus the interface's own name. Same technique that settled
ThreadingMode in 0.3.62.

Throwaway part, closed without saving.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.knowledge import KnowledgeTools
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.sketcher import SketcherTools

say = lambda text: print(text, flush=True)  # noqa: E731

WANTED = ("config", "apply", "activ", "synchro", "associat", "column", "sheet")


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

    for name, ptype, value in (("H", "length", 20), ("W", "real", 3.5)):
        knowledge.execute("catia_create_parameter",
                          {"name": name, "type": ptype, "value": value})
    sheet = os.path.join(tempfile.mkdtemp(prefix="kt5_"), "family.txt")
    with open(sheet, "w", encoding="ascii", newline="\n") as handle:
        handle.write("H\tW\n20\t3.5\n30\t4.5\n")
    knowledge.execute("catia_create_design_table",
                      {"name": "DT1", "file_path": sheet, "parameters": ["H", "W"]})
    table = part.Relations.Item("DT1")

    say("A. interface name and member count")
    try:
        info = table._oleobj_.GetTypeInfo()
        say(f"   interface: {info.GetDocumentation(-1)[0]!r}")
        count = info.GetTypeAttr().cFuncs
        say(f"   cFuncs = {count}")
    except Exception as error:  # noqa: BLE001
        say(f"   no type info: {str(error).splitlines()[0][:90]}")
        print(docs.execute("catia_close_document", {"save": False}), flush=True)
        return 1

    say("\nB. members about config / apply / activation / association")
    rows = []
    for slot in range(count):
        try:
            function = info.GetFuncDesc(slot)
            names = info.GetNames(function.memid)
        except Exception:  # noqa: BLE001
            continue
        member = names[0] if names else ""
        if any(token in member.lower() for token in WANTED):
            rows.append((member, getattr(function, "invkind", "?"),
                         getattr(function, "args", "?")))
    for member, kind, arguments in sorted(set(rows)):
        say(f"   {member}  [{kind}, args={arguments}]")
    if not rows:
        say("   none")

    say("\nC. every member, for the record")
    everything = []
    for slot in range(count):
        try:
            function = info.GetFuncDesc(slot)
            names = info.GetNames(function.memid)
        except Exception:  # noqa: BLE001
            continue
        if names:
            everything.append(names[0])
    say("   " + ", ".join(sorted(set(everything))))

    print(docs.execute("catia_close_document", {"save": False}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
