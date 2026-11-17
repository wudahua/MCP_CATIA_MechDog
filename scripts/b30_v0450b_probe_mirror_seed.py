"""Round 18: can a Mirror feature be told WHAT to mirror?

`ShapeFactory.AddNewMirror(planeRef)` has no seed parameter, and the measured
effect of the call as it stands is "mirror the whole current shape and union it
with the body":

    box 60x40x50, pocket 20x40 through   volume 80000
    mirror about xy                       volume 160000   <- a copy below z=0, added

That is a real CATIA operation (Transform > Mirror on a body), and on a part whose
material is a CUT it fills the cut back in: the report's groove removed a -Z
annulus, the mirrored copy of the body has solid exactly there, and the union
returns the pre-groove volume 42188262.4593 to four decimals.

So the question for the tool is whether the same feature can be given a seed - the
part design Mirror of ONE feature - or whether the only honest thing left is to
refuse it for cut features.

This probe dumps the feature's members and tries `Object` as a seed, before and
after the update, on a fresh document each time (an update that fails is a
document-level failure).

.venv/Scripts/python.exe -u scripts/b30_v0450b_probe_mirror_seed.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0450b")


def say(line: str = "") -> None:
    print(line, flush=True)


def fresh(conn, docs, name: str):
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, name + ".CATPart")
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
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    return conn.get_active_part()


def volume(part) -> float:
    spa = part.Parent.GetWorkbench("SPAWorkbench")
    measurable = spa.GetMeasurable(part.CreateReferenceFromObject(part.MainBody))
    return float(measurable.Volume) * 1e9


def build(conn, docs, parts, sketcher, name):
    """box 60x40x50 with a pocket on the +X half: 120000 -> 80000."""
    part = fresh(conn, docs, name)
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0.0, "y1": 0.0,
                                                "x2": 60.0, "y2": 40.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"height": 50.0})
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": 40.0, "y1": 0.0,
                                                "x2": 60.0, "y2": 40.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pocket", {"depth": 50.0})
    return part


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    say(f"probe v0450b on {conn.app.Name}")

    # ── A: what members does the Mirror feature expose? ───────────────────
    part = build(conn, docs, parts, sketcher, "ProbeV0450bMembers")
    before = volume(part)
    say(f"  box+pocket volume {before:.4f}")
    plane = part.OriginElements.PlaneXY
    mirror = part.ShapeFactory.AddNewMirror(
        part.CreateReferenceFromObject(plane))
    say(f"  created '{mirror.Name}' (no update yet)")
    try:
        names = sorted(name for name in dir(mirror) if not name.startswith("_"))
    except Exception as error:  # noqa: BLE001
        names = [f"<dir failed: {str(error).splitlines()[0][:60]}>"]
    say(f"  members: {names}")
    for member in ("Object", "Seed", "Symmetry", "Plane", "Reference", "Invert"):
        try:
            value = getattr(mirror, member)
            say(f"    {member} = {getattr(value, 'Name', value)!r}")
        except Exception as error:  # noqa: BLE001
            say(f"    {member}: <{str(error).splitlines()[0][:70]}>")

    # ── B: set the seed BEFORE the first update ──────────────────────────
    pocket = None
    body = part.MainBody
    for index in range(1, int(body.Shapes.Count) + 1):
        shape = body.Shapes.Item(index)
        if "凹槽" in str(shape.Name) or "Pocket" in str(shape.Name):
            pocket = shape
            break
    say(f"  the pocket feature is '{getattr(pocket, 'Name', None)}'")
    outcome = []
    for label, value in (("the feature itself", pocket),
                         ("a reference to it",
                          part.CreateReferenceFromObject(pocket) if pocket else None)):
        try:
            mirror.Object = value
            outcome.append(f"{label}: accepted")
        except Exception as error:  # noqa: BLE001
            outcome.append(f"{label}: REFUSED {str(error).splitlines()[0][:70]}")
    say("  setting Object: " + " | ".join(outcome))
    try:
        part.UpdateObject(mirror)
        say(f"  after update: volume {volume(part):.4f}  (cut-preserving mirror "
            f"about xy would stay {before:.4f})")
    except Exception as error:  # noqa: BLE001
        say(f"  update FAILED: {str(error).splitlines()[0][:100]}")

    # ── C: the same, but with the seed set only AFTER an update ──────────
    part = build(conn, docs, parts, sketcher, "ProbeV0450bAfter")
    before = volume(part)
    say("")
    say(f"  second document, volume {before:.4f}")
    mirror = part.ShapeFactory.AddNewMirror(
        part.CreateReferenceFromObject(part.OriginElements.PlaneXY))
    try:
        part.UpdateObject(mirror)
        say(f"  after the no-seed update: {volume(part):.4f}")
    except Exception as error:  # noqa: BLE001
        say(f"  no-seed update FAILED: {str(error).splitlines()[0][:80]}")
    pocket = None
    for index in range(1, int(part.MainBody.Shapes.Count) + 1):
        shape = part.MainBody.Shapes.Item(index)
        if "凹槽" in str(shape.Name) or "Pocket" in str(shape.Name):
            pocket = shape
    try:
        mirror.Object = pocket
        say("  set Object after the update: accepted")
        part.UpdateObject(mirror)
        say(f"  volume after re-update: {volume(part):.4f}")
    except Exception as error:  # noqa: BLE001
        say(f"  setting after the update FAILED: "
            f"{str(error).splitlines()[0][:90]}")

    # ── D: does a GROOVE behave the same (the report's feature) ──────────
    part = build(conn, docs, parts, sketcher, "ProbeV0450bGroove")
    say("")
    say(f"  third document, {volume(part):.4f} before a groove")
    sketcher.execute("catia_create_sketch", {"plane": {"type": "offset",
                                                       "base": "zx",
                                                       "distance": -25.0}})
    sketcher.execute("catia_sketch_rectangle", {"x1": 0.0, "y1": 0.0,
                                                "x2": 75.0, "y2": 45.0})
    sketcher.execute("catia_close_sketch", {})
    say(parts.execute("catia_groove", {"angle": 360.0,
                                       "axis": "horizontal"})[:160])
    say(f"  after the groove: {volume(part):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
