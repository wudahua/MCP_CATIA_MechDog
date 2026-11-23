"""B30 probe, 0.3.163 part 3: does InWorkObject actually insert in the MIDDLE?

The report asked for a reorder, and no interface exposes one (part 1) and paste
appends (part 2). The one ordering control that IS declared is `Part.InWorkObject`,
which decides where the NEXT feature goes. That is only useful if it really
inserts between existing features - which is what this measures:

  pad -> pocket A -> set InWorkObject = pad -> pocket B

Success: body.Shapes becomes [凸台.1, 凹槽.2, 凹槽.1] (B between), and the volume
is pad minus BOTH holes. Failure: B appended at the end, or A lost.
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
    path = os.path.join(root, "INWORK0499.CATPart")
    try:
        docs.execute("catia_close_document", {"document": path, "save": False})
    except Exception:  # noqa: BLE001
        pass
    if os.path.isfile(path):
        os.remove(path)

    docs.execute("catia_new_part", {"name": "INWORK0499", "file_path": path})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "S"})
    sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 25, "y2": 45})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"sketch_name": "S", "height": 80})

    sk.execute("catia_create_sketch", {"plane": "xy", "name": "HA"})
    sk.execute("catia_sketch_circle", {"cx": 8, "cy": 8, "radius": 3})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pocket", {"sketch_name": "HA", "depth": 80})

    part = conn.get_active_part()
    body = part.MainBody

    def state(label: str) -> None:
        names = [str(shape.Name) for shape in body.Shapes]
        data = payload(measure.execute("catia_get_inertia", {}))
        values = data.get("data") if isinstance(data.get("data"), dict) else data
        say(f"  {label}: shapes={names} V={values.get('volume_mm3')}")

    state("before")

    pad = body.Shapes.Item(1)
    part.InWorkObject = pad
    say(f"  InWorkObject -> '{part.InWorkObject.Name}'")

    sk.execute("catia_create_sketch", {"plane": "xy", "name": "HB"})
    sk.execute("catia_sketch_circle", {"cx": 18, "cy": 18, "radius": 3})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pocket", {"sketch_name": "HB", "depth": 80})
    state("after pocket B inserted with the in-work object on the pad")

    names = [str(shape.Name) for shape in body.Shapes]
    middle = len(names) == 3 and names[0] == "凸台.1" and names[2] == "凹槽.1"
    say(f"  inserted in the MIDDLE: {'YES' if middle else 'NO'} ({names})")

    say("\n  and the in-work object can be put back to the body:")
    part.InWorkObject = body
    say(f"  InWorkObject -> '{part.InWorkObject.Name}'")

    docs.execute("catia_close_document", {"document": path, "save": False})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
