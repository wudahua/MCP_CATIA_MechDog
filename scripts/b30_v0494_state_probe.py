"""B30 2026-11-21: when can a part file an open assembly references be rebuilt?

The report's P1-11 suggestion #2 asks catia_new_part(overwrite=true) to warn when
the file is referenced by an open assembly. Whether that call is even REACHABLE
in that state decides where the warning belongs, so this probe walks the states
and prints what CATIA answers:

  1  part P saved, assembly A with two instances of P, A saved
  2  what Application.Documents holds now (is P loaded "in context"?)
  3  catia_close_document(P) while A is open
  4  catia_new_part(overwrite=true) over P while A is open
  5  close A, then close P, then rebuild P, then reopen A
  6  what the reopened A says about its instances

Read-only apart from its own temp documents.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, ".")

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = tempfile.mkdtemp(prefix="probe0494_")


def say(text: str = "") -> None:
    print(text, flush=True)


def flat(reply) -> str:
    if isinstance(reply, dict):
        return json.dumps(reply, ensure_ascii=False)
    return " ".join(str(reply).split())


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    assembly = AssemblyTools(conn)
    part = os.path.join(ROOT, "P.catpart".upper().replace(".CATPART", ".CATPart"))
    asm = os.path.join(ROOT, "A.CATProduct")

    docs.execute("catia_new_part", {"name": "P", "file_path": part})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "S"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 40, "y2": 20})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"sketch_name": "S", "height": 10})
    docs.execute("catia_new_product", {"name": "A", "file_path": asm})
    for _ in range(2):
        assembly.execute("catia_add_component", {"file_path": part})
    listing = json.loads(assembly.execute("catia_list_components", {}))
    names = [c["name"] for c in listing["components"]]
    assembly.execute("catia_set_component_transform",
                     {"component_name": names[0], "translation": [100, 0, 0]})
    docs.execute("catia_save_document", {})
    say(f"instances: {names}")

    def documents(label: str) -> list[dict]:
        payload = json.loads(docs.execute("catia_list_documents", {}))
        items = payload.get("documents") if isinstance(payload, dict) else payload
        mine = [i for i in items or [] if "probe0494" in (i.get("path") or "")]
        for item in mine:
            say(f"  [{label}] {item.get('name')} | {item.get('type')} | "
                f"component_of={item.get('component_of')} | {item.get('path')}")
        if not mine:
            say(f"  [{label}] none of this probe's documents are open")
        return mine

    say("\n2  documents with the assembly open")
    documents("A open")

    say("\n3  close the PART while the assembly is open")
    say("  " + flat(docs.execute("catia_close_document",
                                 {"document": part, "save": False}))[:400])
    documents("after close(P)")

    say("\n4  catia_new_part(overwrite=true) over P while A is open")
    try:
        say("  " + flat(docs.execute("catia_new_part",
                                     {"name": "P2", "file_path": part,
                                      "overwrite": True}))[:500])
    except Exception as error:  # noqa: BLE001 - the answer IS the measurement
        say(f"  refused: {type(error).__name__}: {' '.join(str(error).split())[:400]}")
    documents("after new_part")

    say("\n5  close A, then close P, then rebuild")
    say("  " + flat(docs.execute("catia_close_document",
                                 {"document": asm, "save": True}))[:200])
    documents("after close(A)")
    try:
        say("  " + flat(docs.execute("catia_close_document",
                                     {"document": part, "save": False}))[:300])
    except Exception as error:  # noqa: BLE001
        say(f"  close(P) failed: {' '.join(str(error).split())[:300]}")
    documents("after close(P) again")
    try:
        say("  " + flat(docs.execute("catia_new_part",
                                     {"name": "P2", "file_path": part,
                                      "overwrite": True}))[:500])
    except Exception as error:  # noqa: BLE001
        say(f"  rebuild refused: {' '.join(str(error).split())[:400]}")

    say("\n6  reopen the assembly and look at its instances")
    docs.execute("catia_open_document", {"file_path": asm})
    documents("A reopened")
    try:
        listing = json.loads(assembly.execute("catia_list_components", {}))
        say(f"  broken={listing.get('broken')} "
            f"part_numbers={[c.get('part_number') for c in listing['components']]}")
        say(f"  poses={[c.get('position') for c in listing['components']]}")
        say(f"  note={str(listing.get('note'))[:200]}")
    except Exception as error:  # noqa: BLE001
        say(f"  list_components failed: {' '.join(str(error).split())[:300]}")

    say(f"\nworkspace: {ROOT}")
    for path in (asm, part):
        try:
            docs.execute("catia_close_document", {"document": path, "save": False})
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
