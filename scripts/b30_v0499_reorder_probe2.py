"""B30 probe, 0.3.163 part 2: is Copy+Paste a usable reorder?

Part 1 showed no reorder member anywhere (Shapes 25: Item/Count/_NewEnum/
GetBoundary only; Shape 41; Body 29; Part 54; Selection 46 with Copy/Paste but
no move). Copy+Paste is the one route left that could INSERT a feature at a
chosen place, since a paste lands after the in-work object.

So: copy the pocket, set the in-work object to the pad, paste, and read the tree
after each step. Success looks like a THIRD feature between the two, with the
volume unchanged; failure looks like an exception, an empty clipboard, or a
feature appended at the end (which is not a reorder).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
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
    measure = MeasurementTools(conn)

    root = os.path.join(tempfile.gettempdir(), "p0499reorder")
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "PASTE0499.CATPart")
    try:
        docs.execute("catia_close_document", {"document": path, "save": False})
    except Exception:  # noqa: BLE001
        pass
    if os.path.isfile(path):
        os.remove(path)

    docs.execute("catia_new_part", {"name": "PASTE0499", "file_path": path})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "S"})
    sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 25, "y2": 45})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"sketch_name": "S", "height": 80})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "H"})
    sk.execute("catia_sketch_circle", {"cx": 12.5, "cy": 22.5, "radius": 5})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pocket", {"sketch_name": "H", "depth": 80})

    part = conn.get_active_part()
    body = part.MainBody
    sel = conn.hso
    pad = body.Shapes.Item(1)
    pocket = body.Shapes.Item(2)

    def state() -> str:
        names = [str(shape.Name) for shape in body.Shapes]
        data = payload(measure.execute("catia_get_inertia", {}))
        values = data.get("data") if isinstance(data.get("data"), dict) else data
        return (f"shapes={names} V={values.get('volume_mm3')} "
                f"A={values.get('area_mm2')}")

    say(f"start: {state()}")

    say("\n1. copy the pocket, then InWorkObject = the pad, then Paste")
    try:
        sel.Clear()
        sel.Add(pocket)
        sel.Copy()
        say("  Selection.Copy(): returned")
        sel.Clear()
        part.InWorkObject = pad
        say(f"  InWorkObject = '{pad.Name}'")
        sel.Add(pad)
        sel.Paste()
        say("  Selection.Paste(): returned")
        say(f"  after: {state()}")
    except Exception as error:  # noqa: BLE001
        say(f"  FAILED: {type(error).__name__}: {str(error).splitlines()[0][:180]}")

    say("\n2. same with PasteSpecial('CATPrtResultWithLink')")
    try:
        sel.Clear()
        sel.Add(pocket)
        sel.Copy()
        sel.Clear()
        part.InWorkObject = pad
        sel.Add(pad)
        sel.PasteSpecial("CATPrtResultWithLink")
        say("  PasteSpecial returned")
        say(f"  after: {state()}")
    except Exception as error:  # noqa: BLE001
        say(f"  FAILED: {type(error).__name__}: {str(error).splitlines()[0][:180]}")

    say("\n3. paste with NOTHING selected (does it need a target?)")
    try:
        sel.Clear()
        sel.Add(pocket)
        sel.Copy()
        sel.Clear()
        part.InWorkObject = pad
        sel.Paste()
        say("  Paste() with an empty selection returned")
        say(f"  after: {state()}")
    except Exception as error:  # noqa: BLE001
        say(f"  FAILED: {type(error).__name__}: {str(error).splitlines()[0][:180]}")

    say("\n4. is the clipboard really holding the pocket? Paste it at the END")
    try:
        sel.Clear()
        sel.Add(pocket)
        sel.Copy()
        sel.Clear()
        part.InWorkObject = body
        sel.Add(body)
        sel.Paste()
        say("  Paste at the end returned")
        say(f"  after: {state()}")
    except Exception as error:  # noqa: BLE001
        say(f"  FAILED: {type(error).__name__}: {str(error).splitlines()[0][:180]}")

    sel.Clear()
    docs.execute("catia_close_document", {"document": path, "save": False})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
