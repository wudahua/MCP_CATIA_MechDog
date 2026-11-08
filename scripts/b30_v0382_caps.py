"""0.3.82 probes: is the reported gap in CATIA or only in this package?

Three questions, one session:

(a) close_sketch on the report's P1-1 profile - one spline plus four lines whose
    ends coincide to the digit. 0.3.81 recorded the endpoints of LINES only (the
    arc's recorder sat after a `return`, and the spline never called it), so the
    spline's two ends were invisible and the pairing reported a 29 mm "gap"
    between an end and itself. The fix records them; this measures the answer.
    A circle-only sketch is the degenerate case: nothing to pair, which used to
    answer closed:null ("nothing recorded") for a sketch that is trivially closed.

(b) The report's P1-5: "AddNewGSDCircPattern exists in the typeinfo, only not
    exposed". Existence is not usability - AddNewHealing is declared the same way
    and does nothing. So read the real signatures instead of guessing.

(c) P2-1: CaptureToFile answered 762x744 JPG for a request of 1200x900 PNG. Does
    the method accept a size at all?

Nothing here writes to a user file: every document is created under
F:\\catia_probe_v0382_caps and closed before the script ends.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0382_caps"

WANTED_FACTORY = (
    "AddNewGSDCircPattern", "AddNewGSDRectPattern", "AddNewSurfacicCircPattern",
    "AddNewPlaneNormal", "AddNewPlaneTangent", "AddNewPlaneAngle",
    "AddNewPlaneOffset", "AddNewPlane2Lines", "AddNewPlane3Points",
    "AddNewPlaneEquation", "AddNewPointOnCurve", "AddNewAxis2Lines",
)


def say(line: str = "") -> None:
    print(line, flush=True)


def dump(obj, label: str, wanted) -> None:
    say(f"\n=== {label} ===")
    try:
        info = obj._oleobj_.GetTypeInfo()
        attributes = info.GetTypeAttr()
        count = attributes.cFuncs
    except Exception as error:  # noqa: BLE001
        say(f"  no type info: {str(error).splitlines()[0][:90]}")
        return
    found = 0
    for index in range(count):
        try:
            desc = info.GetFuncDesc(index)
            names = info.GetNames(desc.memid)
        except Exception:  # noqa: BLE001
            continue
        if not names or names[0] not in wanted:
            continue
        found += 1
        say(f"  {names[0]}({', '.join(names[1:])})  [{len(names) - 1} params]")
    if not found:
        say("  none of the wanted methods are declared here")


def profile(conn, sketcher, lines: bool) -> None:
    """The report's P1-1 sketch: one 6-point spline closed by four lines."""
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    points = [[0, 0], [10, 2], [20, 8], [25, 20], [20, 32], [10, 38]]
    say("   " + str(sketcher.execute(
        "catia_sketch_spline", {"points": points})).splitlines()[0][:110])
    if lines:
        pairs = [((10, 38), (0, 38)), ((0, 38), (0, 20)),
                 ((0, 20), (0, 10)), ((0, 10), (0, 0))]
        for (x1, y1), (x2, y2) in pairs:
            sketcher.execute("catia_sketch_line",
                             {"x1": x1, "y1": y1, "x2": x2, "y2": y2})


def main() -> None:
    import json

    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    sketcher = SketcherTools(conn)

    part_path = os.path.join(ROOT, "caps.CATPart")
    document.execute("catia_new_part",
                     {"name": "CAPS", "file_path": part_path, "overwrite": True})
    say(f"active: {conn.active_document.Name}")

    # ── (a) the spline + lines profile ────────────────────────────────────
    say("\n=== (a) close_sketch, spline + 4 lines, ends coincide ===")
    profile(conn, sketcher, lines=True)
    reply = sketcher.execute("catia_close_sketch", {"structured": True})
    data = json.loads(reply) if isinstance(reply, str) else reply
    say("   " + json.dumps(data.get("data", {}).get("breaks", {}),
                           ensure_ascii=False)[:400])

    say("\n=== (a2) close_sketch, circle only ===")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_circle", {"cx": 10, "cy": 10, "radius": 5})
    reply = sketcher.execute("catia_close_sketch", {"structured": True})
    data = json.loads(reply) if isinstance(reply, str) else reply
    say("   " + json.dumps(data.get("data", {}).get("breaks", {}),
                           ensure_ascii=False)[:400])

    # ── (b) the factory's real signatures ────────────────────────────────
    part = conn.get_active_part()
    dump(part.HybridShapeFactory, "HybridShapeFactory", WANTED_FACTORY)

    # ── (c) CaptureToFile: does it take a size? ──────────────────────────
    say("\n=== (c) CaptureToFile ===")
    app = conn.app
    shot = os.path.join(ROOT, "shot.jpg")
    for label, call in (
        ("CaptureToFile(path)", lambda: app.CaptureToFile(shot)),
        ("CaptureToFile(path, 1200, 900)",
         lambda: app.CaptureToFile(shot, 1200, 900)),
    ):
        try:
            call()
            size = (os.path.getsize(shot) if os.path.isfile(shot) else "missing")
            say(f"   {label}: OK, {size} bytes")
        except Exception as error:  # noqa: BLE001 - the refusal is the answer
            say(f"   {label}: REFUSED {str(error).splitlines()[0][:110]}")
        try:
            if os.path.isfile(shot):
                os.remove(shot)
        except OSError:
            pass

    # ── cleanup: only the document this script created ───────────────────
    own = os.path.normcase(os.path.abspath(part_path))
    for index in range(conn.documents.Count, 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = os.path.normcase(os.path.abspath(str(doc.FullName)))
        except Exception:  # noqa: BLE001 - never saved
            continue
        if full.startswith(os.path.normcase(os.path.abspath(ROOT))):
            doc.Activate()
            document.execute("catia_close_document", {"save": False})
    say("\ncleanup: closed the documents under " + ROOT)


if __name__ == "__main__":
    main()
