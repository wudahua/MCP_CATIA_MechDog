"""Is there a bounding box one call away? (v0487)

The 0.3.152 report's P2 is 200 s for 17 instances, and 0.3.152's own numbers say
where it goes: 128 ms per cold measurable, one per vertex. So the question worth
asking before optimising that loop is whether CATIA already answers the box in
ONE call - a member on Measurable, on the body, or on the Part.

No member is assumed: each candidate name is probed with getattr and, when it
resolves, called with the argument shapes a box would need. A name that does not
exist raises AttributeError and is reported as absent, which is the answer too.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "catia_probe_v0487")
os.makedirs(ROOT, exist_ok=True)

CANDIDATES = ("GetBoundingBox", "BoundingBox", "GetBox", "GetExtents",
              "GetEnvelope", "GetBoundingBoxes", "GetDimensions", "Extent",
              "GetMultiBoundingBox", "GetBoundingBoxPoints")


def say(text: str) -> None:
    print(text, flush=True)


def probe(obj, label: str, names=CANDIDATES) -> None:
    for name in names:
        try:
            member = getattr(obj, name)
        except Exception:  # noqa: BLE001 - absence is the answer
            continue
        say(f"  {label}.{name} EXISTS: {member!r}")
        for args in ((), (0,), (0, 0), ([0.0] * 6,), (0, 0, 0)):
            try:
                value = member(*args) if callable(member) else member
            except Exception as exc:  # noqa: BLE001
                say(f"      {name}{args} -> {type(exc).__name__}: "
                    f"{' '.join(str(exc).split())[:90]}")
                continue
            say(f"      {name}{args} -> {value!r}")


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    parts = PartDesignTools(conn)
    say(f"probe v0487 on {conn.app.Name}")

    try:
        while True:
            docs.execute("catia_close_document", {"save": False})
    except Exception:  # noqa: BLE001
        pass

    path = os.path.join(ROOT, "BoxProbe.CATPart")
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": "BoxProbe", "file_path": path})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "P"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 20,
                                                "y2": 10})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "P", "height": 5})

    part = conn.get_active_part()
    body = conn.get_active_part_body()
    doc = conn.active_document
    spa = doc.GetWorkbench("SPAWorkbench")

    say("\nthe part:")
    probe(part, "part", ("GetBoundingBox", "BoundingBox", "GetBox"))
    say("\nthe main body:")
    probe(body, "body", ("GetBoundingBox", "BoundingBox", "GetBox", "Extent"))
    say("\nthe SPA workbench:")
    probe(spa, "spa", ("GetBoundingBox", "BoundingBox", "GetBox",
                       "GetMeasurables", "GetMeasurableBoundingBox"))
    say("\nthe body's measurable:")
    measurable = spa.GetMeasurable(body)
    probe(measurable, "measurable", CANDIDATES)
    say(f"  measurable members we know work: {measurable.GetVolume()} mm3 volume")
    try:
        say(f"  measurable.GetCenterOfGravity -> {measurable.GetCenterOfGravity()}")
    except Exception as exc:  # noqa: BLE001
        say(f"  GetCenterOfGravity failed: {exc}")

    say("\nthe document:")
    probe(doc, "doc", ("GetBoundingBox",))
    say("\ndone - a member listed above is a box in one call; none listed means "
        "the vertex sweep is not avoidable through these interfaces")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
