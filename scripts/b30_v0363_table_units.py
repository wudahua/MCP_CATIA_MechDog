"""Round seven: a design table that multiplies every length by 1000.

Round six applied row 2 of a table whose length column reads "30", and the
parameter came back as 30000.0 where it had been 20.0. Either

  * a bare number in a length column is metres (CATIA's internal unit), so the
    sheet has to say "30mm", or
  * the table is right and something else changed the unit.

That is the difference between a product family and a part 1000x too big, so it
is measured with four sheets: bare numbers, "mm" suffixes, a mixed one, and one
where the column is written in metres.

Throwaway part per case, closed without saving.
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

CASES = {
    "bare numbers": "H\tW\n20\t3.5\n30\t4.5\n",
    "mm suffix": "H\tW\n20mm\t3.5\n30mm\t4.5\n",
    "metres": "H\tW\n0.02m\t3.5\n0.03m\t4.5\n",
}


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    knowledge = KnowledgeTools(conn)
    design = PartDesignTools(conn)
    sketch = SketcherTools(conn)

    for label, body in CASES.items():
        say(f"\n=== {label} ===")
        docs.execute("catia_new_part", {})
        sketch.execute("catia_create_sketch", {"plane": "xy"})
        sketch.execute("catia_sketch_centered_rectangle", {"width": 40, "height": 40})
        sketch.execute("catia_close_sketch", {})
        design.execute("catia_pad", {"height": 20})
        part = conn.get_active_part()
        params = part.Parameters

        for name, ptype, value in (("H", "length", 20), ("W", "real", 3.5)):
            knowledge.execute("catia_create_parameter",
                              {"name": name, "type": ptype, "value": value})
        say(f"   before: H={params.Item('H').Value!r} mm, W={params.Item('W').Value!r}")

        sheet = os.path.join(tempfile.mkdtemp(prefix="kt7_"), "family.txt")
        with open(sheet, "w", encoding="ascii", newline="\n") as handle:
            handle.write(body)
        try:
            knowledge.execute("catia_create_design_table",
                              {"name": "DT1", "file_path": sheet,
                               "parameters": ["H", "W"]})
        except Exception as error:  # noqa: BLE001
            say(f"   table refused: {str(error).splitlines()[0][:150]}")
            docs.execute("catia_close_document", {"save": False})
            continue

        table = part.Relations.Item("DT1")
        say(f"   ConfigurationsNb={table.ConfigurationsNb!r}")
        for row in (1, 2):
            try:
                table.Configuration = row
                part.Update()
                say(f"   row {row}: H={params.Item('H').Value!r}, "
                    f"W={params.Item('W').Value!r}")
            except Exception as error:  # noqa: BLE001
                say(f"   row {row}: no ({str(error).splitlines()[0][:90]})")
        docs.execute("catia_close_document", {"save": False})

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
