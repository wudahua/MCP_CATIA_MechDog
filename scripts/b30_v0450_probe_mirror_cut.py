"""Round 18 (2026-11-17 report): reproduce `catia_mirror` undoing a cut.

The report's sequence is pad -> groove -> mirror(xy), and the volume after the
mirror equals the volume BEFORE the groove exactly:

    after the groove      26228730.962
    after mirror(xy)      42188262.4593   <- the pre-groove number
    delete the mirror     26228730.962

`_mirror` is `ShapeFactory.AddNewMirror(planeRef)` and nothing else, so the
question is what CATIA's Mirror feature means when the body already holds a cut:
what it wraps, where it is inserted, and whether the body it lands in is the one
being measured.

The probe prints, for every step: the volume, the feature list of every body, the
current in-work object, and the bodies' own volumes. A cut that is 15959531.5 mm3
is used so the presence or absence of the removal is unambiguous.

.venv/Scripts/python.exe -u scripts/b30_v0450_probe_mirror_cut.py
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

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0450")


def say(line: str = "") -> None:
    print(line, flush=True)


def volume(part) -> str:
    try:
        spa = part.Parent.GetWorkbench("SPAWorkbench")
        inertia = spa.GetMeasurable(part.MainBody).MomentOfInertia
        _mass, _cog, _matrix = inertia
        return "?"
    except Exception:  # noqa: BLE001
        pass
    try:
        body = part.MainBody
        inertia = part.Parent.GetWorkbench("SPAWorkbench").GetMeasurable(
            part.CreateReferenceFromObject(body))
        return f"{float(inertia.Volume) * 1e9:.4f}"
    except Exception as error:  # noqa: BLE001
        return f"FAILED {str(error).splitlines()[0][:60]}"


def tree(part) -> None:
    """Every body, its features, and the in-work object."""
    try:
        count = int(part.Bodies.Count)
    except Exception:  # noqa: BLE001
        count = 0
    for position in range(1, count + 1):
        body = part.Bodies.Item(position)
        names = []
        try:
            for index in range(1, int(body.Shapes.Count) + 1):
                shape = body.Shapes.Item(index)
                names.append(f"{shape.Name}({shape.Type if hasattr(shape, 'Type') else '?'})")
        except Exception as error:  # noqa: BLE001
            names = [f"<unreadable: {str(error).splitlines()[0][:40]}>"]
        say(f"    body '{body.Name}': {names}")
    try:
        work = part.InWorkObject
        say(f"    InWorkObject: '{work.Name}'")
    except Exception as error:  # noqa: BLE001
        say(f"    InWorkObject: <unreadable {str(error).splitlines()[0][:40]}>")


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, "ProbeV0450Mirror.CATPart")
    for index in range(int(conn.documents.Count), 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = str(doc.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).lower().startswith(
                os.path.abspath(ROOT).lower()):
            try:
                doc.Close()
            except Exception:  # noqa: BLE001
                pass
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": "ProbeV0450Mirror", "file_path": path})
    part = conn.get_active_part()
    say(f"probe v0450 on {conn.app.Name}")

    # a 60 x 40 box, 50 tall: 120000 mm3
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0.0, "y1": 0.0,
                                                "x2": 60.0, "y2": 40.0})
    sketcher.execute("catia_close_sketch", {})
    say(parts.execute("catia_pad", {"height": 50.0})[:120])
    say(f"  [pad]      volume {volume(part)}")
    tree(part)

    # a pocket 20 x 40 through the whole height, on the +X half
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 40.0, "y1": 0.0,
                                                "x2": 60.0, "y2": 40.0})
    sketcher.execute("catia_close_sketch", {})
    say(parts.execute("catia_pocket", {"depth": 50.0})[:200])
    say(f"  [pocket]   volume {volume(part)}   (expect 80000)")
    tree(part)

    say("")
    say("now the report's call")
    say(parts.execute("catia_mirror", {"plane": "xy"})[:200])
    say(f"  [mirror]   volume {volume(part)}   (a cut-preserving mirror about xy "
        f"should stay 80000: the pocket is symmetric about y already)")
    tree(part)

    say("")
    say("and about a plane the pocket is NOT symmetric to")
    say(parts.execute("catia_mirror", {"plane": "yz"})[:200])
    say(f"  [mirror2]  volume {volume(part)}")
    tree(part)

    say("")
    say("what does the mirror feature say about its own inputs?")
    body = part.MainBody
    for index in range(1, int(body.Shapes.Count) + 1):
        shape = body.Shapes.Item(index)
        if "irror" not in str(shape.Name):
            continue
        describe = {}
        for member in ("Name", "Seed", "Symmetry", "Volume", "NbObjects",
                       "Object", "Mirror"):
            try:
                value = getattr(shape, member)
            except Exception as error:  # noqa: BLE001
                describe[member] = f"<{str(error).splitlines()[0][:40]}>"
                continue
            try:
                describe[member] = getattr(value, "Name", None) or str(value)[:40]
            except Exception:  # noqa: BLE001
                describe[member] = str(value)[:40]
        say(f"    {shape.Name}: {json.dumps(describe, ensure_ascii=False)}")
        try:
            say("      members: " + ", ".join(
                sorted(name for name in dir(shape) if not name.startswith("_"))[:40]))
        except Exception as error:  # noqa: BLE001
            say(f"      members unreadable: {str(error).splitlines()[0][:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
