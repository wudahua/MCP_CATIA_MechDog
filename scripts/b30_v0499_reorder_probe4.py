"""B30 probe, 0.3.163 part 4: does InWorkObject place a NEW feature in the middle?

Part 3 said "no", but the pocket there went through catia_pocket, whose
_prepare_body sets InWorkObject back to the body before every Part Design
factory (that is deliberate: a GSD call leaves the in-work object on the
geometrical set and the factory then fails with a bare "method failed"). So part
3 measured this server's decision, not CATIA's.

Here the factory is called RAW on the same session, with InWorkObject left on the
pad, and the tree is read afterwards:

  pad -> pocket A (through catia_pocket) -> InWorkObject = pad
      -> ShapeFactory.AddNewPocket(sketch HB) -> UpdateObject

If 凹槽.2 lands at index 2 and both holes are cut, then CATIA CAN insert at a
chosen position, and the only thing in the way is _prepare_body.
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
    path = os.path.join(root, "RAW0499.CATPart")
    try:
        docs.execute("catia_close_document", {"document": path, "save": False})
    except Exception:  # noqa: BLE001
        pass
    if os.path.isfile(path):
        os.remove(path)

    docs.execute("catia_new_part", {"name": "RAW0499", "file_path": path})
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

    # The second sketch exists in the tree; do NOT create it through a tool now,
    # because the sketch tools also touch the in-work object.
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "HB"})
    sk.execute("catia_sketch_circle", {"cx": 18, "cy": 18, "radius": 3})
    sk.execute("catia_close_sketch", {})

    pad = body.Shapes.Item(1)
    part.InWorkObject = pad
    say(f"  InWorkObject -> '{part.InWorkObject.Name}'")

    try:
        hb = part.FindObjectByName("HB")
        # AddNewPocket takes the SKETCH OBJECT, not a Reference - the tool does
        # the same (sketch = self._get_last_sketch(...)), and a Reference answers
        # "类型不匹配" (measured here first).
        pocket = part.ShapeFactory.AddNewPocket(hb, 80)
        # The tool sets this explicitly; without it the cut goes the other way and
        # removes nothing. Set it so "inserted but cut nothing" cannot be blamed
        # on the direction.
        pocket.DirectionOrientation = 0
        pocket.FirstLimit.LimitMode = 2
        part.UpdateObject(pocket)
        say(f"  raw AddNewPocket OK: '{pocket.Name}'")
        state("after UpdateObject only")
        part.Update()
        say("  part.Update() returned")
        state("after a full part.Update()")
    except Exception as error:  # noqa: BLE001
        say(f"  raw AddNewPocket FAILED: {type(error).__name__}: "
            f"{str(error).splitlines()[0][:160]}")

    state("after")
    names = [str(shape.Name) for shape in body.Shapes]
    say(f"  inserted in the MIDDLE: "
        f"{'YES' if len(names) == 3 and names[2] == '凹槽.1' else 'NO'} ({names})")

    docs.execute("catia_close_document", {"document": path, "save": False})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
